---
type: "Reference"
title: "PS4 Receiver (multiput.c)"
description: "How the freestanding receiver runs without libc, CRT, or ps4debug on retail PS4 firmware: its runtime syscall self-resolution, its single-threaded poll(2) connection multiplexer, and its destination-path safety check."
tags: ["ps4", "freestanding", "syscalls", "receiver", "goldhen"]
verified:
  - by: openwiki/0.6.0
    at: 2026-09-27T19:28:00.481Z
sources:
  - id: openwiki-source-23775c3de52f3ab95a13cb8b
    resource: repo://README.md
  - id: openwiki-source-0956a3cbe4959e21e620e904
    resource: repo://src/multiput.c
generated: { by: "omp", at: "2026-09-27T19:28:00.481Z" }
---

# PS4 Receiver (`multiput.c`)

## Deployment model dictates every design choice

GoldHEN PayLoader maps `multiput.elf`'s `PT_LOAD` segments directly into the
console process and jumps to `_start` from a native libkernel thread
trampoline. There is no dynamic linker, no libc, no CRT startup, and no
`pthread`/`fork` support — this single fact explains nearly every unusual
choice in `multiput.c`:

- compiled `-ffreestanding -fno-builtin` (see
  [build-and-release.md](/openwiki/build-and-release.md)) because there is no
  libc to link against or fall back to;
- one process, one thread, a `poll(2)`-driven event loop instead of a
  thread-per-connection model, because there is no threading runtime
  available to spawn into;
- every PS4 syscall the receiver needs is obtained by resolving raw
  function-pointer addresses at runtime (below), because there is no libc
  wrapper layer (`open()`, `read()`, …) to call.

## Syscall self-resolution: no ps4debug required

Retail PS4 firmware enforces syscall-origin checks: a `syscall` instruction
only succeeds when it executes from code mapped as part of libkernel, not
from arbitrary unsigned payload memory. An unsigned payload therefore cannot
just execute its own `syscall` instructions — it has to *call through*
already-mapped libkernel code that does.

An earlier revision solved this by having the **host-side injector** attach
to the console over ps4debug, read live process memory to find libkernel's
ASLR-randomized syscall stub addresses, and patch those addresses directly
into the ELF's bytes before uploading it — a running ps4debug service on the
console was therefore a hard prerequisite for every launch.

The current receiver instead resolves its own syscall stub addresses
entirely at runtime, using only its own entry-point context:

1. `_start` captures `__builtin_return_address(0)` — the address, inside
   libkernel's thread trampoline, that called this payload.
2. `resolve_syscalls(caller)` masks that address down to its containing
   `0x4000`-byte page and scans backward, page by page, up to 32 pages.
3. For every byte offset in a page, it tests for the exact 12-byte pattern
   `48 c7 c0 <4-byte syscall number, little-endian> 49 89 ca 0f 05` — the
   machine code for `mov rax, imm32; mov r10, rcx; syscall`, which is the
   shape of a native libkernel syscall stub.
4. A match's embedded syscall number is looked up in `record_syscall()`
   against the twelve numbers the receiver needs — `read`=3, `write`=4,
   `open`=5, `close`=6, `accept`=30, `socket`=97, `bind`=104,
   `setsockopt`=105, `listen`=106, `poll`=209, `pwrite`=476, `ftruncate`=480
   — and if it's one of them, that stub's address is stored in the
   `syscall_api` table and a bit is set in a found-mask.
5. Scanning stops as soon as the mask equals `0x0fff` (all twelve found) or
   32 pages have been exhausted, in which case `_start` returns immediately
   without ever calling `serve()`.

Once resolved, every `sys_*` wrapper in the file (`sys_read`, `sys_open`,
`sys_pwrite`, …) calls through the corresponding function pointer in
`syscall_api` via the `CALLn` macros — these are real calls into mapped
libkernel code, satisfying the syscall-origin check, with zero dependency on
any external debug service. This is why the receiver no longer requires
ps4debug: address resolution that used to happen on the host, over the
network, against a live debug session, now happens once, locally, inside the
payload itself, at every launch.

## Connection state machine and event loop

`serve()` opens one listening socket on `DEFAULT_PORT` (compile-time
constant, default 9022 — see
[build-and-release.md](/openwiki/build-and-release.md)) and runs a single
`poll(2)` loop forever, multiplexing the listener plus up to `MAX_CONNS = 64`
active connections in one process with no threads. Each connection is a
`conn_t` slot progressing through explicit states in `conn_step()`:

```
ST_HEADER -> ST_PATHLEN -> ST_PATH -> [SETUP: straight to ST_REPLY]
                                    -> [DATA:  ST_LENGTH -> ST_BODY -> ST_REPLY]
```

- `ST_HEADER` reads the fixed 16-byte header (`kind`, 7 pad bytes, `arg`).
- `ST_PATHLEN` reads a `uint16` path length, rejecting `0` or anything over
  `MAX_PATH_LEN` (4096).
- `ST_PATH` reads that many path bytes, then calls `path_is_allowed()`
  (below); a `KIND_SETUP` connection proceeds straight to
  `conn_finish_setup()` and `ST_REPLY`, a `KIND_DATA` connection proceeds to
  `ST_LENGTH`, anything else is rejected.
- `ST_LENGTH` (DATA only) reads an 8-byte body length, then opens the
  destination file for writing (`conn_open_data()`) and moves to `ST_BODY`.
- `ST_BODY` reads and writes the file in `RECV_CHUNK` (256 KiB) increments
  until the full announced body length has been written, then replies `K`.
- `ST_REPLY` writes the single reply byte (`K` or `E`) and resets the slot.

Every state transition is driven by a single `poll()` return: the loop asks
for `POLLIN` on every connection except those in `ST_REPLY` (which ask for
`POLLOUT`), and dispatches at most one `conn_step()` per ready file
descriptor per iteration — a slow or stalled peer only occupies its own slot,
never blocks the loop, and a malformed frame is rejected via `conn_fail()`
(sets `E`, closes any open file handle) without tearing down any other
connection.

## SETUP pre-sizes the file; DATA writes are offset-independent

`conn_finish_setup()` (for `KIND_SETUP`) opens the destination with
`O_CREAT | O_WRONLY` and calls `ftruncate(fd, c->arg)`, where `arg` is the
total file size sent in the SETUP header — this pre-sizes (and, for an
existing file, truncates/resizes) the destination once, before any DATA
connection writes a single byte.

Each `KIND_DATA` connection carries its own starting byte offset as the
header's `arg` field. `write_range()` calls `pwrite(filefd, buf, len,
c->arg + c->body_written + done)` — every write's absolute file offset is
derived purely from that connection's own `arg` plus how much *it* has
written so far. No DATA connection needs to know anything about any other
connection's progress or existence: this is what lets `multiput_push.py`
run many DATA connections concurrently against disjoint ranges with zero
coordination between them (see
[python-tooling.md](/openwiki/python-tooling.md) and
[wire-protocol.md](/openwiki/wire-protocol.md)).

## `path_is_allowed()`: the receiver-side destination boundary

`path_is_allowed()` is the console-side half of the `/data/pkg/` boundary
described in [wire-protocol.md](/openwiki/wire-protocol.md) (the other half
is `multiput_protocol.py`'s `validate_remote_path()` on the Python client).
It performs a literal byte comparison of the path's first 10 bytes against
`"/data/pkg/"`, rejects a trailing `/`, then walks the remainder
segment-by-segment (split on `/`) rejecting any segment that is empty (a
doubled slash), exactly `"."`, or exactly `".."` — with an inline check that
rejects an embedded NUL byte anywhere in the path. Because every `..`
segment anywhere in the path is unconditionally rejected, the path can never
resolve to anything outside the literal `/data/pkg/` prefix, regardless of
how many segments it contains.

**Note on testing this check**: on 2026-09-27, a live retest against port
9022 appeared to show this check failing (`/data/pkg/../x` was accepted).
The cause was not a defect in `path_is_allowed()` — it was a **stale
receiver process from an earlier session still resident and listening on
that same port**, since GoldHEN never stops a previously launched payload
and a new payload's `bind()` on an already-open port fails silently. Testing
against a freshly compiled, never-before-used `DEFAULT_PORT` build (see
[build-and-release.md](/openwiki/build-and-release.md)) confirmed the check
rejects every traversal and absolute-path attempt correctly. This is
documented operationally in `README.md`'s Safety and limitations section.
