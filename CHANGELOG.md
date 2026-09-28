# Changelog

## 1.2.0 - 2026-09-28
- Removed the experimental reliable-UDP transport entirely, from the
  receiver, sender, protocol module, and dashboard. It never reached TCP on
  any live measurement (17.0-17.8 vs 25.7+ MB/s on the same file and link),
  and the same-hour instrumented runs showed TCP riding the radio's own
  ceiling, so the data plane had nothing left to win. The wire protocol now
  has exactly two frame kinds: SETUP (0) and DATA (1).
- Version 1.2.0. Dashboard restyled: a tasteful vintage-lowrider theme
  (aged cream, oxide red, black ink, chrome trim, one gold pinstripe) over
  the same no-build vanilla-JS UI.

- Receiver: verify every accepted connection's SO_RCVBUF reservation with
  getsockopt, and log conn/fd/set-result/granted bytes through Orbis klog
  (syscall 601, visible on GoldHEN's klog server on TCP 3232). A failed
  bind (a stale receiver already on the port) and incomplete syscall
  resolution now log themselves instead of exiting silently.
- Resolves getsockopt (118) and klog (601) as optional stubs alongside the
  UDP pair; the required set is unchanged, so firmware without those
  stubs still runs the receiver.
- Live 13.52 measurement (instrumented receiver on port 9024,
  524,288,000-byte file): every connection at 8, 16, and 32 streams was
  granted the full 524,288-byte receive buffer (set=0, granted=524288) --
  Orbis neither clamps nor rejects large SO_RCVBUF requests under this
  concurrency, so the socket-buffer, per-process-budget, and kernel-patch
  escalation path is retired. Throughput was flat (30.1 / 29.7 / 28.8 MB/s
  across 8/16/32 streams) against a same-hour 8-stream iperf3 ceiling of
  249-263 Mbit/s: stream counts beyond 8 are not a throughput lever on a
  link-bound path.
- The 32-stream dip (26.6 MB/s) measured earlier on a 209,715,200-byte
  file was per-stream share amortization (6.25 MB per stream), not a
  socket limit: at 15.6 MB per stream the dip disappears.

## 0.3.0 - 2026-09-27

- Added an opt-in reliable UDP data plane while retaining TCP setup,
  completion, and the existing parallel TCP transport.
- Added 1,400-byte DATA packets, a 512-packet receive window, cumulative plus
  selective acknowledgements, RTT-derived retransmission timeout, AIMD
  congestion control, bounded sender pacing, duplicate suppression, and
  dynamic scheduling/retransmission across multiple console addresses.
- Kept the PS4 receiver single-threaded and allocation-free. UDP uses the same
  `poll(2)` loop and a static 512-packet receive ring. It buffers validated
  datagrams, coalesces contiguous runs into `pwrite(2)` operations of up to
  128 packets, selectively acknowledges buffered out-of-order packets, and
  advances the cumulative ACK only after a coalesced write succeeds.
- Added CLI `--transport udp` and a dashboard transport selector. TCP remains
  the default.
- Live 13.52 testing used separate listeners without reboot: pre-coalescing on
  port 9022 and the rebuilt receiver on port 9023. On the same 75,563,008-byte
  file, coalescing moved UDP from 17.0-17.1 to 17.4-17.8 MB/s and reduced
  retransmits from 244-284 to 187-216; eight-stream TCP measured 25.7 MB/s.
- Extended the byte-exact loopback self-test with deterministic packet loss,
  retransmission recovery, and empty-file completion.

## 0.2.0 - 2026-09-27

- Bundled the browser dashboard (formerly a separate `pkg_web` tool with an
  FTP transfer option) into this repo as `web/`, calling
  `push_multistream()`/`inject()` directly. Dropped the FTP backend and its
  verify-after-push option entirely -- MultiPut is a single lightweight
  "install, load a payload, go" path, not a choice of transport.
- Measured through the dashboard against a real console: 27.7 MB/s average
  (221.6 Mbit/s) on an 8-stream, 503 MB push, matching the CLI's own
  measured ceiling on the same link.

## 0.1.1 - 2026-09-27

- Receiver: reserve a 512 KiB receive buffer on every accepted connection.
  Orbis defaults its socket buffers to 8 KiB, capping in-flight data at
  ~64 KiB and throttling every stream to window/RTT.
- Sender: reserve a 512 KiB send buffer on every connection.
- Sender: accept multiple comma-separated console addresses; SETUP goes to
  the first and DATA streams round-robin across all of them, driving every
  interface the receiver listens on at once. The receiver needs no change
  because it already binds every interface.
- Measured over the Wi-Fi 6 bridge with a 503 MB file: 4 streams
  23.4 -> 25.1-25.9 MB/s; 8+ streams match the backhaul's own 8-stream
  iperf3 capacity (193-198 Mbit/s), so the remaining ceiling on that
  network is the radio link, not this software.

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
