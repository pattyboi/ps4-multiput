---
type: "Reference"
title: "Python Launcher and Sender"
description: "How multiput_inject.py launches the receiver through GoldHEN PayLoader, how multiput_push.py splits and sends a file over parallel streams, and multiput_protocol.py's role as the single source of truth for wire framing."
tags: ["python", "sender", "launcher", "protocol"]
verified:
  - by: openwiki/0.6.0
    at: 2026-09-27T19:28:00.481Z
sources:
  - id: openwiki-source-c2897f38a8327aaa32d61ef1
    resource: repo://multiput_inject.py
  - id: openwiki-source-2f592f166d417cc8e885471b
    resource: repo://multiput_protocol.py
  - id: openwiki-source-757a4b2176a406164ee81c7a
    resource: repo://multiput_push.py
  - id: openwiki-source-0956a3cbe4959e21e620e904
    resource: repo://src/multiput.c
generated: { by: "omp", at: "2026-09-27T19:28:00.481Z" }
---

# Python Launcher and Sender

Three modules, one responsibility each:

- `multiput_protocol.py` — wire framing and destination-path validation, no
  network I/O of its own. Imported by everything else.
- `multiput_inject.py` — gets the receiver running on the console via GoldHEN
  PayLoader.
- `multiput_push.py` — sends one local file to a running receiver over
  parallel streams.

## `multiput_protocol.py`: the single source of truth for framing

Every byte `multiput_push.py` puts on the wire is built through this module's
`pack_header`, `pack_path`, and `pack_length` — there is no second,
divergent encoding path anywhere in the sender. The same module is reused by
both offline self-tests (`multiput_push.py`'s `_TestServer` and
`multiput_inject.py`'s `selftest()`), so a change to the framing here is
immediately exercised by both `--selftest` entry points without any separate
test-only encoding logic to keep in sync.

`validate_remote_path()` is the client-side half of the `/data/pkg/`
destination boundary described in
[wire-protocol.md](/openwiki/wire-protocol.md): it rejects anything that
doesn't start with `/data/pkg/`, anything that isn't already its own
`posixpath.normpath()` (i.e. no `..`, no `.`, no doubled slashes), anything
ending in `/`, any path containing a NUL byte, and anything over 4096 UTF-8
bytes. This check runs and can raise **before** a single byte reaches the
network — a bad destination fails locally, not after round-tripping to the
console.

## `multiput_inject.py`: get the receiver running

`load_payload()` validates the ELF file before any network call:
- at least 64 bytes and the `\x7fELF` magic,
- ELF class byte pair `02 01` (64-bit, little-endian),
- `e_machine == 62` (x86-64) read from the fixed offset 18.

`launch_goldhen()` then POSTs the raw ELF bytes to GoldHEN PayLoader's HTTP
endpoint (default port 9090) as a manually-built `POST / HTTP/1.1` request
with `Content-Length` and `Connection: close`, and requires a `200` status
line in the response before treating the upload as accepted.

A GoldHEN `200` only means the console accepted and started running the
payload — it does **not** prove the payload's own listener socket actually
came up. `wait_for_listener()` closes that gap: it repeatedly opens a short
connection to the target port and sends a deliberately-invalid probe frame —
header `kind = 0xFF` (neither `KIND_SETUP` nor `KIND_DATA`) with a real,
already-allowed path (`/data/pkg/.multiput-probe`) — and treats a reply of
`E` as proof that something speaking the MultiPut protocol is alive on that
port. Kind `0xFF` guarantees the receiver's `conn_step()` falls through to
its `else { conn_fail(c); }` branch after the path check passes, so the probe
never creates or touches a file — it only proves reachability.

This distinction matters operationally: GoldHEN never stops a payload it
previously launched. If an older MultiPut build is still resident and
listening on the same port, a fresh `multiput_inject.py` run's new payload
will fail its own `bind()` silently and exit, while the **old** payload keeps
answering — `wait_for_listener()` will report success (because *a* MultiPut
listener answered), but it may be the stale one, not the one just uploaded.
See [receiver.md](/openwiki/receiver.md) and `README.md`'s Safety and
limitations section for the full gotcha and how to rule it out (rebuild with
a fresh `DEFAULT_PORT` and restart the console when in doubt).

## `multiput_push.py`: split and send

`push_multistream(host, port, local_path, remote_path, streams=4, ...)`:

1. Sends one `SETUP` connection (`send_setup()`) carrying the total file size
   as the header's `arg`, and requires its `K` acknowledgement before opening
   any `DATA` connection. A failed or rejected `SETUP` aborts immediately —
   no `DATA` connection is ever attempted against an unconfirmed destination.
2. Splits the file into `streams` (1–64, validated) contiguous byte ranges:
   `base = total // streams` for every range except the last, whose length is
   `total - offset` — the remainder is absorbed by the final range rather
   than distributed by a separate modulo step, so ranges are always exactly
   contiguous and exactly cover `[0, total)` with no gap or overlap.
3. Spawns one daemon `threading.Thread` per range running `_send_range()`,
   which opens its own fresh `DATA` connection, sends a header whose `arg` is
   that range's starting offset and a length field for that range, then
   streams the local file's bytes for that range in `CHUNK_SIZE = 1 MiB`
   reads via `sendall()`, and finally requires a `K` acknowledgement for that
   connection specifically.
4. All threads are `.join()`-ed before `push_multistream()` returns.
   Per-thread exceptions are appended to a shared `errors` list under a
   `threading.Lock` rather than propagated directly — the function returns
   `True` only when that list is empty, i.e. every single range was
   acknowledged.

Progress reporting shares the same lock: a `progress_cb` closure accumulates
bytes sent across all threads and only fires a `progress` event roughly every
64 MiB of aggregate progress (or on the very last byte), keeping status
output readable at high stream counts instead of firing on every 1 MiB
chunk from every thread.

## Offline self-tests

Both `--selftest` entry points run entirely against `127.0.0.1` loopback
sockets — no console, no GoldHEN, no external network:

- `multiput_inject.py --selftest`: spins up a loopback HTTP-shaped server and
  a loopback MultiPut-protocol listener, drives `launch_goldhen()` and
  `wait_for_listener()` against them, and separately proves a non-x86-64 ELF
  is rejected by `load_payload()`.
- `multiput_push.py --selftest` (via `_TestServer`, a loopback implementation
  of the real wire protocol backed by real files): proves multi-stream
  splitting, uneven-remainder handling, real byte-exact file writes, and
  rejection of unsafe destination paths, exercising the exact same
  `multiput_protocol.py` framing used against a real console.

`make check` runs both; see
[build-and-release.md](/openwiki/build-and-release.md).
