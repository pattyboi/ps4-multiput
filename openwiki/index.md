---
okf_version: "0.2"
---

# Files

- [Build, Packaging, and Release](build-and-release.md) - How multiput.elf is cross-compiled with the ps4-payload-dev/sdk toolchain, how make check gates a release offline, and how the deterministic release tarball is packaged and verified.
- [PS4 MultiPut Overview](overview.md) - What MultiPut is, why it exists, its three-actor architecture, and the trust boundary a jailbroken PS4 owner accepts by running it.
- [Python Launcher and Sender](python-tooling.md) - How multiput_inject.py launches the receiver through GoldHEN PayLoader, how multiput_push.py splits and sends a file over parallel streams, and multiput_protocol.py's role as the single source of truth for wire framing.
- [Quickstart](quickstart.md) - The shortest path from a clean checkout or the prebuilt release to a running parallel-stream transfer onto a jailbroken PS4, plus the two constraints that matter before you start and where to read next.
- [PS4 Receiver (multiput.c)](receiver.md) - How the freestanding receiver runs without libc, CRT, or ps4debug on retail PS4 firmware: its runtime syscall self-resolution, its single-threaded poll(2) connection multiplexer, and its destination-path safety check.
- [Wire Protocol](wire-protocol.md) - The exact binary framing MultiPut's sender and receiver agree on: header layout, SETUP vs DATA semantics, the /data/pkg/ destination rule enforced on both ends, and the protocol's lack of authentication.
