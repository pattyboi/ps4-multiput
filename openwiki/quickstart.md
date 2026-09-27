---
type: "Guide"
title: "Quickstart"
description: "The shortest path from a clean checkout or the prebuilt release to a running parallel-stream transfer onto a jailbroken PS4, plus the two constraints that matter before you start and where to read next."
tags: ["quickstart", "getting-started", "setup"]
verified:
  - by: openwiki/0.6.0
    at: 2026-09-27T19:28:00.481Z
sources:
  - id: openwiki-source-012f2c78e3b1446dfc35803f
    resource: repo://Makefile
  - id: openwiki-source-23775c3de52f3ab95a13cb8b
    resource: repo://README.md
generated: { by: "omp", at: "2026-09-27T19:28:00.481Z" }
---

# Quickstart

## Read this first

- Destinations must be **normalized paths under `/data/pkg/`** — anything
  else is rejected on both the sending host and the console. See
  [wire-protocol.md](/openwiki/wire-protocol.md).
- The protocol is **unauthenticated and unencrypted, LAN-only**. Anything
  that can reach the receiver's port can write files under `/data/pkg/`. Do
  not expose these ports beyond a trusted LAN. See
  [overview.md](/openwiki/overview.md).

## Path A: using the prebuilt release

Requirements: Python 3.10+ (standard library only), a GoldHEN-jailbroken PS4
with PayLoader enabled (default TCP 9090), and network reach from the host
to the console.

```sh
# 1. Launch the receiver on the console via GoldHEN
python3 multiput_inject.py 192.168.1.50

# 2. Push a file over 4 parallel streams
python3 multiput_push.py 192.168.1.50 ./game.pkg /data/pkg/game.pkg --streams 4
```

`multiput_inject.py` uploads `multiput.elf` (bundled in the release) to
GoldHEN PayLoader and confirms the receiver's listener actually came up
before returning. `multiput_push.py` splits the file into `--streams`
disjoint ranges (1–64, default 4) and sends them concurrently. Try
`--streams 1`, `2`, `4`, and `8` against a representative large file to find
the best value for your network.

## Path B: rebuilding from source

Additionally requires the
[ps4-payload-dev/sdk](https://github.com/ps4-payload-dev/sdk) toolchain via
`PS4_PAYLOAD_SDK`, and POSIX `make`.

```sh
make check
```

builds `multiput.elf` and then runs both Python modules' `--selftest`
entry points, entirely offline (no console contact) — this is the standard
gate before touching a real console. See
[build-and-release.md](/openwiki/build-and-release.md) for the full
toolchain and packaging details, including how to build a receiver on a
non-default port with `EXTRA_CFLAGS=-DDEFAULT_PORT=<port>` (which must then
be passed to both `multiput_inject.py --listener-port` and
`multiput_push.py --port`).

## Where to go next

| Question | Page |
|---|---|
| What is MultiPut and how do its pieces fit together? | [overview.md](/openwiki/overview.md) |
| What exact bytes go over the wire? | [wire-protocol.md](/openwiki/wire-protocol.md) |
| How does the console-side receiver work without ps4debug? | [receiver.md](/openwiki/receiver.md) |
| How do the launcher and sender split/send a file? | [python-tooling.md](/openwiki/python-tooling.md) |
| How is `multiput.elf` built and how is a release cut? | [build-and-release.md](/openwiki/build-and-release.md) |
