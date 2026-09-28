#!/usr/bin/env python3
"""Launch PS4 MultiPut through GoldHEN's PayLoader."""
from __future__ import annotations

import argparse
import os
import socket
import struct
import sys
import threading
import time

VERSION = "1.2.0"

DEFAULT_PAYLOADER_PORT = 9090
DEFAULT_LISTENER_PORT = 9022
DEFAULT_ELF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "multiput.elf")
MAX_RESPONSE = 64 * 1024


def load_payload(path: str) -> bytes:
    with open(path, "rb") as source:
        payload = source.read()
    if len(payload) < 64 or payload[:4] != b"\x7fELF":
        raise RuntimeError(f"{path} is not an ELF file")
    if payload[4:6] != b"\x02\x01":
        raise RuntimeError(f"{path} is not a 64-bit little-endian ELF")
    if struct.unpack_from("<H", payload, 18)[0] != 62:
        raise RuntimeError(f"{path} is not an x86-64 ELF")
    return payload


def launch_goldhen(host: str, port: int, payload: bytes, timeout: float) -> None:
    request = (
        f"POST / HTTP/1.1\r\nHost: {host}:{port}\r\n"
        "Content-Type: application/octet-stream\r\n"
        f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n"
    ).encode() + payload
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(request)
        reply = bytearray()
        while len(reply) < MAX_RESPONSE:
            chunk = sock.recv(min(4096, MAX_RESPONSE - len(reply)))
            if not chunk:
                break
            reply += chunk
    status = bytes(reply).split(b"\r\n", 1)[0].split()
    if len(status) < 2 or not status[0].startswith(b"HTTP/1.") or status[1] != b"200":
        line = bytes(reply).split(b"\r\n", 1)[0].decode("latin-1", "replace")
        raise RuntimeError(f"GoldHEN PayLoader rejected payload: {line or 'no response'}")
    print(f"GoldHEN PayLoader accepted {len(payload):,} bytes")


def wait_for_listener(host: str, port: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    path = b"/data/pkg/.multiput-probe"
    probe = struct.pack("<B7sQH", 0xFF, b"\0" * 7, 0, len(path)) + path
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5) as sock:
                sock.sendall(probe)
                if sock.recv(1) == b"E":
                    return
                last_error = RuntimeError("listener did not speak the MultiPut protocol")
        except OSError as exc:
            last_error = exc
        time.sleep(0.1)
    raise RuntimeError(f"MultiPut listener {host}:{port} did not open: {last_error}")


def inject(host: str, elf_path: str,
           listener_port: int = DEFAULT_LISTENER_PORT,
           payloader_port: int = DEFAULT_PAYLOADER_PORT,
           timeout: float = 20.0) -> None:
    payload = load_payload(elf_path)
    launch_goldhen(host, payloader_port, payload, timeout)
    wait_for_listener(host, listener_port, min(timeout, 10.0))
    print(f"MultiPut listening on {host}:{listener_port}")


def _recv_exact(sock: socket.socket, length: int) -> bytes:
    result = bytearray()
    while len(result) < length:
        chunk = sock.recv(length - len(result))
        if not chunk:
            raise ConnectionError("test peer closed early")
        result += chunk
    return bytes(result)


def selftest() -> int:
    payload = bytearray(64)
    payload[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<H", payload, 18, 62)
    payload = bytes(payload) + b"multiput-test"

    http_received: list[bytes] = []
    http_server = socket.socket()
    http_server.bind(("127.0.0.1", 0))
    http_server.listen(1)
    http_port = http_server.getsockname()[1]

    def serve_http() -> None:
        with http_server.accept()[0] as conn:
            request = bytearray()
            while b"\r\n\r\n" not in request:
                request += conn.recv(4096)
            head, body = bytes(request).split(b"\r\n\r\n", 1)
            content_length = 0
            for line in head.split(b"\r\n"):
                if line.lower().startswith(b"content-length:"):
                    content_length = int(line.split(b":", 1)[1])
            body += _recv_exact(conn, content_length - len(body))
            http_received.append(body)
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK")

    listener_received: list[bytes] = []
    listener_server = socket.socket()
    listener_server.bind(("127.0.0.1", 0))
    listener_server.listen(1)
    listener_port = listener_server.getsockname()[1]

    def serve_listener() -> None:
        with listener_server.accept()[0] as conn:
            listener_received.append(conn.recv(4096))
            conn.sendall(b"E")

    http_thread = threading.Thread(target=serve_http, daemon=True)
    listener_thread = threading.Thread(target=serve_listener, daemon=True)
    http_thread.start()
    listener_thread.start()
    launch_goldhen("127.0.0.1", http_port, payload, 5)
    wait_for_listener("127.0.0.1", listener_port, 5)
    http_server.close()
    listener_server.close()
    http_thread.join(timeout=2)
    listener_thread.join(timeout=2)

    invalid_rejected = False
    try:
        bad = bytearray(payload)
        struct.pack_into("<H", bad, 18, 183)
        path = os.path.join(os.path.dirname(__file__), ".multiput-invalid-test")
        with open(path, "wb") as output:
            output.write(bad)
        try:
            load_payload(path)
        finally:
            os.unlink(path)
    except RuntimeError:
        invalid_rejected = True

    checks = [
        ("GoldHEN POST body byte-exact", http_received == [payload]),
        ("MultiPut protocol listener recognized", bool(listener_received)),
        ("non-x86-64 ELF rejected", invalid_rejected),
    ]
    for name, ok in checks:
        print(f"{'ok  ' if ok else 'FAIL'} {name}")
    return 0 if all(ok for _name, ok in checks) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", nargs="?", help="PS4 address")
    parser.add_argument("--elf", default=DEFAULT_ELF,
                        help=f"payload path (default: {DEFAULT_ELF})")
    parser.add_argument("--listener-port", type=int, default=DEFAULT_LISTENER_PORT)
    parser.add_argument("--payloader-port", type=int, default=DEFAULT_PAYLOADER_PORT)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    if not args.host:
        parser.error("HOST is required unless --selftest is used")
    try:
        inject(args.host, args.elf, args.listener_port,
               args.payloader_port, args.timeout)
    except (OSError, RuntimeError) as exc:
        print(f"multiput injection failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
