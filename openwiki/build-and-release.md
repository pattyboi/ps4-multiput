---
type: "Reference"
title: "Build, Packaging, and Release"
description: "How multiput.elf is cross-compiled with the ps4-payload-dev/sdk toolchain, how make check gates a release offline, and how the deterministic release tarball is packaged and verified."
tags: ["build", "release", "toolchain", "packaging"]
verified:
  - by: openwiki/0.6.0
    at: 2026-09-27T19:28:00.481Z
sources:
  - id: openwiki-source-ca6cb4b1a14fd7969dfae3ec
    resource: repo://CHANGELOG.md
  - id: openwiki-source-012f2c78e3b1446dfc35803f
    resource: repo://Makefile
  - id: openwiki-source-0956a3cbe4959e21e620e904
    resource: repo://src/multiput.c
generated: { by: "omp", at: "2026-09-27T19:28:00.481Z" }
---

# Build, Packaging, and Release

## Toolchain

`multiput.elf` (the console-side receiver) is cross-compiled with the
[ps4-payload-dev/sdk](https://github.com/ps4-payload-dev/sdk) toolchain,
located via the `PS4_PAYLOAD_SDK` environment variable and pulled in through
`$(PS4_PAYLOAD_SDK)/toolchain/orbis.mk`. The `Makefile` fails fast with
`PS4_PAYLOAD_SDK is undefined` if that variable isn't set — there is no
fallback or auto-discovery.

Compilation flags:

```
-O2 -fPIC -ffreestanding -fno-builtin -fno-stack-protector
-fno-asynchronous-unwind-tables -fno-unwind-tables -mstackrealign
-Wall -Wextra -Werror
```

`-ffreestanding` and `-fno-builtin` are load-bearing, not defensive: the
receiver has no libc, so the compiler must never substitute a builtin
(`memcpy`, etc.) that assumes one exists. `-Wall -Wextra -Werror` means any
new warning is a build failure, matching this repo's zero-warning convention.

Linking uses `orbis-ld -e _start -o multiput.elf build/multiput.o` — a bare
entry-point override, not a normal libc-style link. There is no CRT object,
no libc, and nothing else on the link line; the single translation unit
`src/multiput.c` is the entire payload. See [receiver.md](/openwiki/receiver.md) for
why the binary has to be built this way (GoldHEN maps it directly with no
dynamic linker).

## `DEFAULT_PORT` is compile-time, not a runtime flag

`multiput.c` guards its listener port with:

```c
#ifndef DEFAULT_PORT
#define DEFAULT_PORT 9022
#endif
```

The port the receiver actually binds on the console is baked into the ELF at
compile time. To build a variant listening on a different port:

```sh
make EXTRA_CFLAGS=-DDEFAULT_PORT=9027
```

`EXTRA_CFLAGS` is appended to `CFLAGS` by the Makefile for exactly this
purpose. This matters because `multiput_inject.py`'s `--listener-port` flag
does **not** configure the receiver — it only tells the Python launcher which
port to poll while waiting for the listener to come up (see
[python-tooling.md](/openwiki/python-tooling.md)). If `--listener-port` doesn't match
the port the specific `multiput.elf` being injected was actually compiled
real (different) port.

This distinction is also the key to diagnosing GoldHEN's stale-payload
gotcha documented in `README.md` (Safety and limitations) and in
[receiver.md](/openwiki/receiver.md): GoldHEN never stops a previously injected
payload, so a rebuild that behaves unexpectedly on the console's default
port should be re-tested with a fresh `DEFAULT_PORT` to rule out an old
resident listener answering instead of the new build.

## `make check`: the offline pre-release gate

```sh
make check
```

expands to:

```sh
$(ELF):                             # build the receiver ELF first
python3 multiput_inject.py --selftest
python3 multiput_push.py --selftest
```

Both self-tests run entirely offline against loopback sockets — no console,
no GoldHEN, no network beyond `127.0.0.1`. This is the standard gate run
before any live console test, and before cutting or refreshing a release.
It is explicitly *not* a substitute for a live console test: it proves the
ELF is well-formed and that the Python framing/splitting/validation logic is
correct, but it cannot prove the receiver actually resolves syscalls or
writes files correctly on real PS4 hardware. See
[python-tooling.md](/openwiki/python-tooling.md) for exactly what each `--selftest`
entry point exercises.

`make clean` removes `build/` and the built `multiput.elf`.

## The SDK is a build-time-only dependency

End users running the **prebuilt release** need only:

- Python 3.10+ (standard library only — no pip dependencies)
- a GoldHEN-jailbroken PS4 with PayLoader enabled

The published release tarball bundles a prebuilt `multiput.elf`, so the
ps4-payload-dev/sdk toolchain is only required to:

- rebuild `multiput.elf` from `src/multiput.c`, or
- build a non-default-port variant via `EXTRA_CFLAGS=-DDEFAULT_PORT=<port>`

## Release artifact and verification convention

The release tarball (`dist/ps4-multiput-<version>.tar.gz`, gitignored, not
tracked in the repo) is built with a deterministic `tar` invocation:

```sh
tar --sort=name --mtime=<fixed-date> --owner=0 --group=0 --numeric-owner \
    --transform='s,^,ps4-multiput-<version>/,' \
    -czf dist/ps4-multiput-<version>.tar.gz \
    .gitignore CHANGELOG.md LICENSE Makefile README.md VERSION \
    multiput.elf multiput_inject.py multiput_protocol.py multiput_push.py \
    src/multiput.c
```

`--sort=name`, a fixed `--mtime`, and `--owner=0 --group=0 --numeric-owner`
make the archive byte-for-byte reproducible from the same tracked inputs —
two separate builds from the same commit produce an identical tarball and
therefore an identical checksum. A `SHA256SUMS` file is generated alongside
it and verified with `sha256sum -c` both immediately after packaging and
again after the archive round-trips through a GitHub Release upload/download,
so the published bytes are proven identical to what was built locally.

The tracked source is intentionally split from build output: `wireless`-style
secrets aren't a concern in this repo, but the same "don't publish something
you can't reproduce or that's gone stale" discipline applies to `dist/` — it
is rebuilt, re-hashed, and re-uploaded (with the release's git tag
force-updated to the new commit) whenever tracked source changes after a
release tag was already cut. This means the published GitHub Release
tarball for a given version tag always matches the current tip of `main` for
that version, not necessarily the exact commit the tag was first created on.
