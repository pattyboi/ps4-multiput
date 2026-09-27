---
type: "Reference"
title: "Wire Protocol"
description: "The exact binary framing MultiPut's sender and receiver agree on: header layout, SETUP vs DATA semantics, the /data/pkg/ destination rule enforced on both ends, and the protocol's lack of authentication."
tags: ["protocol", "wire-format", "binary", "security"]
verified:
  - by: openwiki/0.6.0
    at: 2026-09-27T19:28:00.481Z
sources:
  - id: openwiki-source-2f592f166d417cc8e885471b
    resource: repo://multiput_protocol.py
  - id: openwiki-source-23775c3de52f3ab95a13cb8b
    resource: repo://README.md
  - id: openwiki-source-0956a3cbe4959e21e620e904
    resource: repo://src/multiput.c
generated: { by: "omp", at: "2026-09-27T19:28:00.481Z" }
---

# Wire Protocol

This is the exact byte-level contract implemented independently by
`multiput_protocol.py` (Python, client side) and `multiput.c` (C, receiver
side — see [receiver.md](/openwiki/receiver.md)). A reader implementing or
debugging a third client needs only this page, not both source files.

## Frame layout

All integer fields are little-endian. Every connection sends exactly one of
two frame shapes:

```text
# Common header (16 bytes), every connection:
uint8  kind        # 0 = SETUP, 1 = DATA
uint8  pad[7]       # zero bytes
uint64 arg          # meaning depends on kind, see below

# Then, every connection:
uint16 path_len
uint8  path[path_len]

# DATA connections only, after the path:
uint64 body_len
uint8  body[body_len]

# Every connection, last:
uint8  reply         # 'K' (success) or 'E' (error)
```

The receiver reads and validates each field in order and can reject at any
point (closing with `E`) — an oversized `path_len`, a disallowed path, or a
short/failed body write all terminate the connection with `E` rather than
attempting to recover the frame.

## SETUP vs DATA: what `arg` means

The header's `arg` field is overloaded by `kind`:

- **SETUP** (`kind = 0`): `arg` is the **total file size**. The receiver
  responds by creating (or truncating) the destination file and calling
  `ftruncate()` to that size — the file is fully pre-sized before any data
  is written to it. A SETUP connection has no body; after the path is
  validated it goes straight to the reply.
- **DATA** (`kind = 1`): `arg` is the **byte offset this connection's range
  starts at** within the file, and the following `body_len`/`body` pair is
  that range's length and bytes. The receiver writes the body at
  `arg + <bytes already written by this connection>` via `pwrite()` — an
  absolute file offset, independent of any other connection.

## One transfer = one SETUP + N DATA connections

A single logical file transfer is: one SETUP connection (which must succeed
with `K` before anything else happens), followed by `N` DATA connections
(`N` = the sender's `--streams`, 1–64), each carrying one disjoint,
non-overlapping byte range of the same file, opened and written
concurrently.

Because SETUP has already pre-sized the file and every DATA connection
carries its own absolute starting offset, no DATA connection needs to
coordinate with any other — each one independently `pwrite()`s its own
range into the shared, already-correctly-sized file. This is what makes the
parallel-stream design possible: `N` concurrent TCP connections, `N`
independent write cursors, zero shared mutable state on the wire between
them. See [python-tooling.md](/openwiki/python-tooling.md) for how the
sender computes and dispatches those `N` ranges, and
[receiver.md](/openwiki/receiver.md) for exactly how the receiver applies
them.

## Destination path rule (enforced twice, independently)

Every path (SETUP and DATA alike) must satisfy the same rule, checked
separately on both ends:

| Requirement | Client (`multiput_protocol.py`) | Receiver (`multiput.c`) |
|---|---|---|
| Must start with `/data/pkg/` | literal `str.startswith()` | literal 10-byte prefix compare |
| No `.`/`..` traversal | path must equal its own `posixpath.normpath()` | no path segment may equal `.` or `..` |
| No trailing slash | rejected | rejected |
| No embedded NUL | rejected | rejected inline during the segment scan |
| Length cap | 4096 UTF-8 bytes | `MAX_PATH_LEN` = 4096 |

Both checks independently guarantee the same outcome — a path can never
resolve outside `/data/pkg/` — using different mechanisms (whole-path
normalization vs per-segment scanning). Neither side trusts the other to
have already validated it: the client check exists so a bad destination
fails locally before any bytes are sent, and the receiver check exists
because the receiver cannot assume its peer is the trusted sender.

## No authentication, no encryption

The protocol carries no session token, no nonce, no signature, and no
encryption — it is plaintext TCP. The `/data/pkg/` restriction bounds
*where* a connected peer can write, not *whether* a peer is allowed to
connect at all: anything that can reach the receiver's listening port can
create or overwrite any normalized file path under `/data/pkg/`. MultiPut is
built for a trusted LAN where the operator controls both the sending host
and the console; see [overview.md](/openwiki/overview.md) for the trust
model this implies.
