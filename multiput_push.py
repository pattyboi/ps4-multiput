#!/usr/bin/env python3
"""Send one file to a running PS4 MultiPut receiver over parallel TCP ranges.

The receiver must already be running; launch it with multiput_inject.py.
Destinations are restricted to normalized files below /data/pkg/.

Examples:
    python3 multiput_push.py 192.168.1.50 game.pkg /data/pkg/game.pkg
    python3 multiput_push.py 192.168.1.50 game.pkg /data/pkg/game.pkg --streams 8
    python3 multiput_push.py --selftest
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time

import multiput_protocol as proto
VERSION = "0.1.0"


DEFAULT_STREAMS = 4
CHUNK_SIZE = 1 * 1024 * 1024


def _print_event(kind: str, **kw) -> None:
    if kind == "setup":
        print(f"setup {kw['remote']} ({kw['total']:,} bytes, {kw['streams']} streams)")
    elif kind == "progress":
        pct = kw["sent"] / kw["total"] * 100 if kw["total"] else 0
        print(f"  {kw['sent']:,}/{kw['total']:,} ({pct:.1f}%) at {kw['rate_mb_s']:.1f} MB/s")
    elif kind == "stream_done":
        print(f"  stream {kw['index']} done ({kw['length']:,} bytes @ +{kw['offset']:,})")
    elif kind == "stream_failed":
        print(f"  stream {kw['index']} FAILED: {kw['error']}")
    elif kind == "done":
        print(f"done  {kw['remote']} ({kw['sent']:,} bytes, {kw['elapsed_s']:.1f}s, "
              f"{kw['avg_mb_s']:.1f} MB/s avg)")


def send_setup(host: str, port: int, remote_path: str, file_size: int,
               timeout: float = 30.0) -> bool:
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(proto.pack_header(proto.KIND_SETUP, file_size))
        sock.sendall(proto.pack_path(remote_path))
        return proto.recv_ack(sock)


def _send_range(host: str, port: int, remote_path: str, local_path: str,
                 offset: int, length: int, progress_cb, timeout: float) -> None:
    """Runs in its own thread: streams local_path[offset:offset+length]
    over one fresh connection. progress_cb(n) is called after each chunk
    actually sent (for aggregate rate reporting), not before -- a stalled
    connection must not be counted as progress."""
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(proto.pack_header(proto.KIND_DATA, offset))
        sock.sendall(proto.pack_path(remote_path))
        sock.sendall(proto.pack_length(length))

        with open(local_path, "rb") as f:
            f.seek(offset)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(CHUNK_SIZE, remaining))
                if not chunk:
                    raise IOError(f"local file ended {remaining} bytes early")
                sock.sendall(chunk)
                remaining -= len(chunk)
                progress_cb(len(chunk))

        if not proto.recv_ack(sock):
            raise IOError("receiver replied error (E) for this range")


def push_multistream(
    host: str, port: int, local_path: str, remote_path: str,
    streams: int = DEFAULT_STREAMS, on_event=_print_event, timeout: float = 60.0,
) -> bool:
    """Return True only when every disjoint range is acknowledged."""
    proto.validate_remote_path(remote_path)
    if not 1 <= streams <= 64:
        raise ValueError("streams must be between 1 and 64")
    total = os.path.getsize(local_path)
    if total == 0:
        streams = 1

    on_event("setup", remote=remote_path, total=total, streams=streams)
    try:
        setup_ok = send_setup(host, port, remote_path, total, timeout=timeout)
        setup_error = "SETUP rejected by receiver"
    except OSError as exc:
        setup_ok = False
        setup_error = f"SETUP connection failed: {exc}"
    if not setup_ok:
        on_event("stream_failed", index=-1, error=setup_error)
        return False

    base = total // streams
    ranges = []
    offset = 0
    for i in range(streams):
        length = base if i < streams - 1 else total - offset
        ranges.append((offset, length))
        offset += length

    sent = [0]
    lock = threading.Lock()
    last_report = [0, time.monotonic()]
    errors: list[tuple[int, str]] = []

    def progress_cb(n: int) -> None:
        with lock:
            sent[0] += n
            if sent[0] - last_report[0] >= 64 * 1024 * 1024 or sent[0] == total:
                now = time.monotonic()
                dt = max(now - last_report[1], 1e-6)
                rate = (sent[0] - last_report[0]) / dt / 1e6
                on_event("progress", sent=sent[0], total=total, rate_mb_s=rate)
                last_report[0], last_report[1] = sent[0], now

    def worker(index: int, offset: int, length: int) -> None:
        try:
            _send_range(host, port, remote_path, local_path, offset, length,
                        progress_cb, timeout)
            on_event("stream_done", index=index, offset=offset, length=length)
        except Exception as exc:  # noqa: BLE001 -- surfaced via errors list
            with lock:
                errors.append((index, str(exc)))
            on_event("stream_failed", index=index, error=str(exc))

    start = time.monotonic()
    threads = [
        threading.Thread(target=worker, args=(i, off, length), daemon=True)
        for i, (off, length) in enumerate(ranges)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = max(time.monotonic() - start, 1e-6)

    ok = not errors
    if ok:
        on_event("done", remote=remote_path, sent=sent[0], elapsed_s=elapsed,
                  avg_mb_s=sent[0] / elapsed / 1e6)
    return ok


class _TestServer:
    """Loopback implementation of the exact wire protocol using real files."""

    def __init__(self, root: str):
        self.root = root
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(64)
        self.port = self._sock.getsockname()[1]
        self._stop = False
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def _accept_loop(self) -> None:
        self._sock.settimeout(0.2)
        while not self._stop:
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _resolve(self, remote_path: str) -> str:
        proto.validate_remote_path(remote_path)
        return os.path.join(self.root, remote_path.lstrip("/"))

    def _handle(self, conn: socket.socket) -> None:
        with conn:
            try:
                hdr = proto.recv_full(conn, proto.HEADER_SIZE)
                kind, _pad, arg = __import__("struct").unpack(proto.HEADER_FMT, hdr)
                path_len_raw = proto.recv_full(conn, 2)
                path_len = __import__("struct").unpack(proto.PATH_LEN_FMT, path_len_raw)[0]
                path = proto.recv_full(conn, path_len).decode("utf-8")
                local = self._resolve(path)

                if kind == proto.KIND_SETUP:
                    os.makedirs(os.path.dirname(local) or ".", exist_ok=True)
                    fd = os.open(local, os.O_CREAT | os.O_WRONLY, 0o666)
                    os.ftruncate(fd, arg)
                    os.close(fd)
                    conn.sendall(b"K")
                elif kind == proto.KIND_DATA:
                    length_raw = proto.recv_full(conn, 8)
                    length = __import__("struct").unpack(proto.LENGTH_FMT, length_raw)[0]
                    fd = os.open(local, os.O_WRONLY)
                    remaining = length
                    written_offset = arg
                    while remaining > 0:
                        chunk = proto.recv_full(conn, min(remaining, 1024 * 1024))
                        os.pwrite(fd, chunk, written_offset)
                        written_offset += len(chunk)
                        remaining -= len(chunk)
                    os.close(fd)
                    conn.sendall(b"K")
                else:
                    conn.sendall(b"E")
            except Exception:
                try:
                    conn.sendall(b"E")
                except OSError:
                    pass

    def close(self) -> None:
        self._stop = True
        self._sock.close()
        self._thread.join(timeout=2)


def selftest() -> int:
    """Exercise framing, uneven splitting, path policy, and byte-exact writes."""
    import hashlib
    import tempfile

    def digest(path: str) -> bytes:
        result = hashlib.sha256()
        with open(path, "rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                result.update(chunk)
        return result.digest()

    checks: list[tuple[str, bool]] = []

    with tempfile.TemporaryDirectory() as tmp:
        srv = _TestServer(os.path.join(tmp, "console_root"))
        try:
            local = os.path.join(tmp, "src.bin")
            size = 21 * 1024 * 1024 + 12345  # deliberately not a multiple of 4
            with open(local, "wb") as f:
                f.write(os.urandom(size))

            events: list[tuple[str, dict]] = []
            ok = push_multistream(
                "127.0.0.1", srv.port, local, "/data/pkg/test.pkg",
                streams=4, on_event=lambda kind, **kw: events.append((kind, kw)),
            )
            checks.append(("push_multistream reports success", ok))

            received = os.path.join(srv.root, "data/pkg/test.pkg")
            checks.append(("received file exists", os.path.isfile(received)))
            checks.append(("received size matches", os.path.getsize(received) == size))
            checks.append(("received bytes byte-exact",
                           ok and digest(received) == digest(local)))
            checks.append(("done event fired with full byte count",
                           any(k == "done" and kw.get("sent") == size for k, kw in events)))

            invalid_paths = ("/etc/passwd", "/data/pkg/../escape.pkg",
                             "/data/pkg/", "/data/pkg//double.pkg")
            rejected = True
            for candidate in invalid_paths:
                try:
                    proto.validate_remote_path(candidate)
                    rejected = False
                except ValueError:
                    pass
            checks.append(("unsafe destination paths rejected", rejected))
        finally:
            srv.close()

    bad = [n for n, ok in checks if not ok]
    for n, ok in checks:
        print(f"{'ok  ' if ok else 'FAIL'} {n}")
    if bad:
        print(f"SELFTEST FAIL  {len(bad)} of {len(checks)} failed: {', '.join(bad)}")
        return 1
    print(f"SELFTEST PASS  {len(checks)} checks: multi-stream split, byte-exact "
          "receive, uneven remainder, destination policy")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("host", nargs="?")
    ap.add_argument("local_path", nargs="?")
    ap.add_argument("remote_path", nargs="?")
    ap.add_argument("-p", "--port", type=int, default=proto.DEFAULT_PORT)
    ap.add_argument("--streams", type=int, default=DEFAULT_STREAMS)
    ap.add_argument("--timeout", type=float, default=60.0,
                    help="per-socket timeout in seconds (default: 60)")
    ap.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    if not (args.host and args.local_path and args.remote_path):
        ap.error("host, local_path, remote_path are required unless --selftest")

    try:
        ok = push_multistream(args.host, args.port, args.local_path, args.remote_path,
                              streams=args.streams, timeout=args.timeout)
    except (OSError, ValueError) as exc:
        print(f"multiput push failed: {exc}", file=sys.stderr)
        return 1
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
