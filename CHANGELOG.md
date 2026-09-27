# Changelog

## 0.1.0 - 2026-09-27

- Added a freestanding, import-free PS4 receiver using a single `poll(2)` state
  machine and disjoint `pwrite(2)` ranges.
- Added in-payload `libkernel_sys.sprx` syscall-stub discovery, removing both
  hardcoded ASLR addresses and the ps4debug runtime dependency.
- Added GoldHEN PayLoader launch and protocol-level listener verification.
- Added a standard-library-only parallel sender with 1–64 stream support.
- Restricted destinations to normalized files below `/data/pkg/` in both the
  sender and receiver.
- Added offline protocol, ELF-validation, HTTP-delivery, listener, and
  byte-integrity self-tests.
- Verified live transfers on PS4 firmware 13.52 and measured four streams at
  19.7 MB/s on the test network.
