#!/usr/bin/env python3
"""PS4 MultiPut wire framing shared by the sender and its loopback tests."""
from __future__ import annotations

import struct

KIND_SETUP = 0
KIND_DATA = 1

HEADER_FMT = "<B7sQ"
HEADER_SIZE = struct.calcsize(HEADER_FMT)
PATH_LEN_FMT = "<H"
LENGTH_FMT = "<Q"

DEFAULT_PORT = 9022
MAX_PATH_LEN = 4096
DESTINATION_ROOT = "/data/pkg/"

assert HEADER_SIZE == 16


def validate_remote_path(path: str) -> bytes:
    """Return UTF-8 bytes for a safe regular-file destination under /data/pkg."""
    raw = path.encode("utf-8")
    if len(raw) > MAX_PATH_LEN:
        raise ValueError(f"path longer than {MAX_PATH_LEN} UTF-8 bytes")
    if b"\0" in raw:
        raise ValueError("path contains a NUL byte")
    if not path.startswith(DESTINATION_ROOT):
        raise ValueError(f"destination must start with {DESTINATION_ROOT}")
    import posixpath
    if path != posixpath.normpath(path):
        raise ValueError("destination must be its own normalized path")
    if path.endswith("/"):
        raise ValueError("destination must be a file, not a directory")
    return raw


def pack_header(kind: int, arg: int) -> bytes:
    if kind not in (KIND_SETUP, KIND_DATA):
        raise ValueError(f"unsupported frame kind: {kind}")
    if not 0 <= arg <= 0xFFFF_FFFF_FFFF_FFFF:
        raise ValueError("arg does not fit uint64")
    return struct.pack(HEADER_FMT, kind, b"\0" * 7, arg)


def pack_path(path: str) -> bytes:
    raw = validate_remote_path(path)
    return struct.pack(PATH_LEN_FMT, len(raw)) + raw


def pack_length(length: int) -> bytes:
    if not 0 <= length <= 0xFFFF_FFFF_FFFF_FFFF:
        raise ValueError("length does not fit uint64")
    return struct.pack(LENGTH_FMT, length)


def recv_full(sock, length: int) -> bytes:
    """Read exactly length bytes or raise ConnectionError."""
    chunks = []
    remaining = length
    while remaining > 0:
        chunk = sock.recv(min(remaining, 1 << 20))
        if not chunk:
            raise ConnectionError("connection closed mid-frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def recv_ack(sock) -> bool:
    return recv_full(sock, 1) == b"K"
