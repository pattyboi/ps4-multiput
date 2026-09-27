# PS4 MultiPut

PS4 MultiPut is a standalone parallel file sender and freestanding receiver for
jailbroken PlayStation 4 consoles. It transfers one file through several TCP
connections and writes the disjoint ranges directly to `/data/pkg/`.

It does **not** modify or extend GoldHEN's closed-source FTP server. The receiver
is an independent ELF launched through GoldHEN PayLoader.

## Status

Release: **0.1.0**

Tested on:

- PS4 firmware 13.52
- GoldHEN 2.4b18.11
- Linux/aarch64 host with Python 3.13
- [ps4-payload-dev/sdk](https://github.com/ps4-payload-dev/sdk)

Other firmware versions may work because the receiver discovers its required
syscall stubs in the live `libkernel_sys.sprx` mapping instead of hardcoding
ASLR addresses. They have not been verified with MultiPut.

## Measured result

A 75,563,008-byte package was transferred repeatedly over a Wi-Fi 6 bridge:

| Streams | Average throughput |
|---:|---:|
| 1 | 15.6 MB/s |
| 2 | 16.5 MB/s |
| 4 | **19.7 MB/s** |
| 8 | 19.4 MB/s |

Four streams were about 15% faster than the existing 17.1 MB/s long-run FTP
baseline on that network. The eight-stream result was downloaded through FTP
and compared byte-for-byte with the source. These numbers describe one network;
benchmark your own path rather than assuming more streams are always faster.

## How it works

GoldHEN maps the receiver ELF without resolving normal libc or libkernel
imports. Retail PS4 syscall-origin checks also reject a `syscall` instruction
placed directly in an arbitrary injected mapping. MultiPut therefore uses this
launch sequence:

1. `multiput_inject.py` sends the unmodified receiver ELF to GoldHEN PayLoader
   on port 9090.
2. GoldHEN starts the payload from a native libkernel thread trampoline.
3. The receiver scans backward from its return address and locates the twelve
   native `libkernel_sys.sprx` syscall stubs it requires.
4. It listens on port 9022 and multiplexes up to 64 connections with one
   `poll(2)` state machine. No `fork()` or pthread runtime is required.

The built ELF has no imported libraries, relocations, or embedded `syscall`
instructions.

## Requirements

Host:

- Python 3.10 or newer; standard library only
- POSIX `make`
- the PS4 payload SDK binary distribution

Console:

- a jailbroken PS4 running GoldHEN
- GoldHEN PayLoader enabled, normally TCP 9090
- network access from the host to the console

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

## Safety and limitations

- **Trusted LAN only.** MultiPut has no authentication or encryption. GoldHEN
  PayLoader is also an unauthenticated service. Do not expose these ports to
  the internet or an untrusted network.
- The receiver accepts only normalized file paths below `/data/pkg/`. Both the
  sender and receiver enforce this boundary.
- SETUP creates or resizes the destination before DATA connections begin.
  Existing files with the same path are overwritten.
- There is no resume protocol. A failed transfer can leave a partial file;
  remove it or rerun the whole transfer.
- Version 0.1.0 does not perform a remote content hash. A successful `K` reply
  means every announced byte range was written without a reported I/O error.
- The receiver lives inside `ScePartyDaemon`; it stops when that process or the
  console restarts. Run the injector again after a restart.

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
receiver and real files to cover uneven multi-stream splitting, path
restrictions, acknowledgements, and byte-exact output.

## Wire protocol

All integer fields are little-endian.

Every connection begins with:

```text
uint8  kind       # 0 = SETUP, 1 = DATA
uint8  pad[7]
uint64 arg        # SETUP: final size; DATA: destination offset
uint16 path_len
uint8  path[path_len]
```

A DATA connection then sends:

```text
uint64 data_len
uint8  data[data_len]
```

The receiver replies with one byte and closes the connection:

- `K`: operation completed
- `E`: invalid request or I/O failure

The sender waits for SETUP acknowledgement before opening DATA connections.
Each DATA range is disjoint.

## Project layout

```text
Makefile                 build and offline check targets
src/multiput.c           freestanding PS4 receiver
multiput_inject.py       ELF validator and GoldHEN launcher
multiput_push.py         parallel sender and loopback test server
multiput_protocol.py     shared wire framing and path policy
```

## License and attribution

PS4 MultiPut is released under the MIT License. It is not affiliated with or
endorsed by Sony Interactive Entertainment.

The project interoperates with, but does not contain code or binaries from:

- [GoldHEN](https://github.com/GoldHEN/GoldHEN)
- [ps4-payload-dev/sdk](https://github.com/ps4-payload-dev/sdk)
