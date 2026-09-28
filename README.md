# PS4 MultiPut

PS4 MultiPut is a standalone file sender and freestanding receiver for
jailbroken PlayStation 4 consoles. One SETUP connection pre-sizes the
destination, then disjoint byte ranges stream through parallel TCP
connections, each acknowledged independently. Writes stay below
`/data/pkg/`.

It does **not** modify or extend GoldHEN's closed-source FTP server. The receiver
is an independent ELF launched through GoldHEN PayLoader.

## Status

Release: **1.2.0**

Tested on:

- PS4 firmware 13.52
- GoldHEN 2.4b18.11
- Linux/aarch64 host with Python 3.13
- [ps4-payload-dev/sdk](https://github.com/ps4-payload-dev/sdk)

Other firmware versions may work because the receiver discovers its required
syscall stubs in the live `libkernel_sys.sprx` mapping instead of hardcoding
ASLR addresses. They have not been verified with MultiPut.

## Measured result

A 503,310,848-byte firmware image was transferred repeatedly over the Wi-Fi 6
bridge (host wired to one router, console wired to the other, 5 GHz HE80
backhaul between them, RSSI -70 dBm):

| Streams | 0.1.0 receiver | 0.1.1 receiver |
|---:|---:|---:|
| 1 | 16.8 MB/s | 15.6 MB/s |
| 4 | 23.4 MB/s | 25.1-25.9 MB/s |
| 8 | 26.7 MB/s | 26.1-27.4 MB/s |
| 16 | - | 27.1 MB/s |

An 8-stream `iperf3` run across the same path measures 193-198 Mbit/s
(~24 MB/s): at 8+ streams MultiPut now transfers at the raw capacity of the
backhaul itself, so more streams or further receiver changes cannot raise
throughput on that network -- the remaining lever is the radio link (signal
strength and airtime), not this software. The 0.1.1 socket-buffer
reservation is what lets 4 streams reach that same ceiling; before it,
in-flight data was capped at ~64 KiB by Orbis's 8 KiB default socket
buffers. Benchmark your own path rather than assuming more streams are
always faster.

Revision 3 was also measured live with the same 75,563,008-byte package on
firmware 13.52. The pre-coalescing receiver remained on port 9022; the rebuilt
coalescing receiver was compiled for and injected on port 9023, so the results
cannot come from GoldHEN's still-running old listener:

| Transport | Receiver | Average | Retransmits |
|---|---|---:|---:|
| TCP, 8 streams | 9022 | 25.7 MB/s | kernel-managed |
| Reliable UDP | pre-coalescing, 9022 | 17.0-17.1 MB/s | 244-284 |
| Reliable UDP | coalescing, 9023 | 17.4-17.8 MB/s | 187-216 |

Coalescing reduced write-call pressure and retransmissions but recovered only
about 3% average throughput, and reliable UDP never reached TCP on any live
measurement, so the UDP transport was removed entirely in 1.2.0.

Post-regdomain-fix and instrumented results (2026-09-28), same Wi-Fi 6
bridge path: after correcting the backhaul router's regulatory domain,
the same 8-stream `iperf3` measured 249-263 Mbit/s the same hour. An
instrumented receiver (1.2.0) verified the per-connection buffer grants
live, through GoldHEN's klog server (TCP 3232): every connection at 8,
16, and 32 streams was granted the full 512 KiB receive buffer, with
`setsockopt` succeeding on every one -- Orbis does not clamp or reject
large `SO_RCVBUF` requests at these stream counts, so no socket-buffer,
process-budget, or kernel-patch lever exists on this path. On a
524,288,000-byte file, throughput was flat across stream counts (30.1 /
29.7 / 28.8 MB/s at 8/16/32), within ~5% of the same-hour `iperf3`
ceiling: one file's transfer saturates the link at 8 streams, and more
streams only shrink each stream's share. The 32-stream dip seen on a
209,715,200-byte file (26.6 MB/s) was per-stream share amortization
(6.25 MB per stream), not a socket limit.


## How it works

GoldHEN maps the receiver ELF without resolving normal libc or libkernel
imports. Retail PS4 syscall-origin checks also reject a `syscall` instruction
placed directly in an arbitrary injected mapping. MultiPut therefore uses this
launch sequence:

1. `multiput_inject.py` sends the unmodified receiver ELF to GoldHEN PayLoader
   on port 9090.
2. GoldHEN starts the payload from a native libkernel thread trampoline.
3. The receiver scans backward from its return address and locates the twelve
   required native `libkernel_sys.sprx` syscall stubs plus the optional
   `getsockopt` and `klog` stubs when present, and klogs its own startup,
   bind failures, and every connection's requested vs granted receive
   buffer (visible on GoldHEN's klog server, TCP 3232).
4. It listens on port 9022. One `poll(2)` state machine multiplexes the
   listener and up to 64 TCP connections. Every accepted connection reserves
   a 512 KiB receive buffer and verifies the grant with `getsockopt`.
   No `fork()` or pthread runtime is required.

The built ELF has no imported libraries, relocations, or embedded `syscall`
instructions.

## Requirements

To run the prebuilt release on the host:

- Python 3.10 or newer; standard library only

To rebuild `multiput.elf`:

- POSIX `make`
- the PS4 payload SDK binary distribution

Console:

- a jailbroken PS4 running GoldHEN
- GoldHEN PayLoader enabled, normally TCP 9090
- network access from the host to the console

The release tarball includes a prebuilt `multiput.elf`; the SDK is not needed
unless you rebuild it or select a different listener port.

Install the SDK using its published binary distribution:

```sh
wget https://github.com/ps4-payload-dev/sdk/releases/latest/download/ps4-payload-sdk.zip
sudo unzip -d /opt ps4-payload-sdk.zip
export PS4_PAYLOAD_SDK=/opt/ps4-payload-sdk
```

See the SDK's own README for the required Clang/LLD packages on your platform.

## Build

```sh
make
```

This creates `multiput.elf`. To build a receiver on another port:

```sh
make clean
make EXTRA_CFLAGS=-DDEFAULT_PORT=9023
```

Use the same value with `multiput_inject.py --listener-port` and
`multiput_push.py --port`.

## Launch

Enable GoldHEN PayLoader, then run:

```sh
python3 multiput_inject.py 192.168.1.50
```

The launcher sends `multiput.elf` to GoldHEN and verifies that port 9022 speaks
the MultiPut protocol. The receiver resolves the native syscall stubs itself;
ps4debug is not required.

Non-default service ports:

```sh
python3 multiput_inject.py 192.168.1.50 \
  --payloader-port 9090 \
  --listener-port 9022
```

## Transfer

```sh
python3 multiput_push.py \
  192.168.1.50 \
  ./game.pkg \
  /data/pkg/game.pkg \
  --streams 4
```

Try `--streams 1`, `2`, `4`, and `8` with a representative large file to find
the best value for your network. The accepted range is 1–64.

Split streams across several interfaces of one console (the receiver binds
every interface, so each one needs no extra setup). Streams are distributed
round-robin across the addresses:

```sh
python3 multiput_push.py \
  192.168.1.105,192.168.1.199 \
  ./game.pkg \
  /data/pkg/game.pkg \
  --streams 8
```

This adds real speed only when the interfaces use **separate radios** --
for example console Wi-Fi on 2.4 GHz while the wired path uses the 5 GHz
backhaul. If every path shares one radio's airtime, splitting across
interfaces cannot exceed that radio.


## Web dashboard

```sh
cd web
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python3 app.py                 # binds 0.0.0.0:8101
```

Browses one or more configured `.pkg` library directories, multi-selects
files, and pushes through the parallel TCP sender. It calls
`push_multistream()` directly and auto-injects the receiver through GoldHEN
PayLoader first if its control listener is not already running. There is
no FTP fallback or read-back verification.
See `web/README.md` for configuration and the systemd unit.

## Safety and limitations

- **Trusted LAN only.** MultiPut has no authentication or encryption. GoldHEN
  PayLoader is also an unauthenticated service. Do not expose these ports to
  the internet or an untrusted network.
- The receiver accepts only normalized file paths below `/data/pkg/`. Both the
  sender and receiver enforce this boundary.
- SETUP creates or resizes the destination before file data begins. Existing
  files with the same path are overwritten.
- There is no resume protocol. A failed transfer can leave a partial file;
  rerun the whole transfer.
- No remote content hash is performed. A successful completion means every
  announced range was written without a reported I/O error.
- The receiver lives inside `ScePartyDaemon`; it stops when that process or the
  console restarts. Run the injector again after a restart.
- GoldHEN does not stop a previously injected payload before starting a new
  one. Re-running the injector without a console restart starts a second
  listener thread that fails to bind the already-open port and exits
  silently — the **old** listener keeps answering. If you changed
  `multiput.c` and re-injected but behavior didn't change, restart the
  console (or pick a fresh `DEFAULT_PORT` for the rebuild) before concluding
  the new build is broken.

## Offline verification

No console is contacted by these commands:

```sh
python3 multiput_inject.py --selftest
python3 multiput_push.py --selftest
# or build and run both
make check
```

The launcher self-test covers ELF validation, byte-exact GoldHEN HTTP delivery,
and protocol-level listener verification. The sender test uses a loopback
receiver and real files to cover TCP range splitting, empty files, path
restrictions, and byte-exact output.

## Wire protocol

All integer fields are little-endian.

Every connection begins with:

```text
uint8  kind       # 0 SETUP, 1 DATA
uint8  pad[7]
uint64 arg        # total file size (SETUP) or range start offset (DATA)
uint16 path_len
uint8  path[path_len]
```

DATA appends a `uint64` range length followed by that many file bytes. The
receiver replies `K` or `E` and closes the connection.

## Project layout

```text
Makefile                 build and offline check targets
src/multiput.c           freestanding PS4 receiver
multiput_inject.py       ELF validator and GoldHEN launcher
multiput_push.py         parallel sender and loopback test server
multiput_protocol.py     shared wire framing and path policy
web/                     browser dashboard (vintage lowrider theme) over push + inject()
```

## License and attribution

PS4 MultiPut is released under the MIT License. It is not affiliated with or
endorsed by Sony Interactive Entertainment.

The project interoperates with, but does not contain code or binaries from:

- [GoldHEN](https://github.com/GoldHEN/GoldHEN)
- [ps4-payload-dev/sdk](https://github.com/ps4-payload-dev/sdk)
