#!/usr/bin/env python3
"""Web UI for MultiPut: browse .pkg libraries and push over parallel TCP.

Browses one or more configured root directories, lets the operator switch
roots or add a new one (any directory under MULTIPUT_WEB_DOWNLOAD_BASE),
multi-selects files, and pushes them through ../multiput_push.py's parallel
TCP sender. The receiver is auto-injected through GoldHEN PayLoader when
its control listener is not already running.

This stays a thin UI over MultiPut rather than reimplementing the protocol.
There is no FTP backend, resume path, or directory-create step.

Deliberately plain, matching pi-tuning/grow-tent's dashboard: server-
rendered Jinja2 + vanilla JS polling, no build step, no framework. Console
host is a UI text field, not a fixed config value -- it moves around the
LAN session to session (192.168.1.185 one night, .105 the next).

    python3 web/app.py
    MULTIPUT_WEB_ROOTS="gamarr=/mnt/x/complete/gamarr;decypharr=/mnt/x/decypharr" python3 web/app.py
"""
from __future__ import annotations

import asyncio
import os
import socket
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

MULTIPUT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MULTIPUT_DIR))
import multiput_push  # noqa: E402
import multiput_inject  # noqa: E402

REMOTE_ROOT = multiput_push.proto.DESTINATION_ROOT.rstrip("/")

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))

DOWNLOAD_BASE = Path(os.environ.get(
    "MULTIPUT_WEB_DOWNLOAD_BASE", "/mnt/dietpi_userdata2/downloads"
)).resolve()


def _parse_roots(spec: str) -> dict[str, Path]:
    """`label=path;label=path` -> {label: Path}. Each path is resolved and
    must exist under DOWNLOAD_BASE at parse time (startup config is
    trusted the same as everything else in this repo; runtime additions
    via /api/roots get the real validation, see add_root())."""
    out: dict[str, Path] = {}
    for part in spec.split(";"):
        part = part.strip()
        if not part:
            continue
        label, _, path = part.partition("=")
        out[label.strip()] = Path(path.strip()).resolve()
    return out


ROOTS: dict[str, Path] = _parse_roots(os.environ.get(
    "MULTIPUT_WEB_ROOTS",
    "gamarr=/mnt/dietpi_userdata2/downloads/complete/gamarr;"
    "decypharr=/mnt/dietpi_userdata2/downloads/decypharr",
))
DEFAULT_HOST = os.environ.get("MULTIPUT_WEB_CONSOLE_HOST", "")
DEFAULT_STREAMS = int(os.environ.get("MULTIPUT_WEB_STREAMS", "8"))
HTTP_HOST = os.environ.get("MULTIPUT_WEB_HTTP_HOST", "0.0.0.0")
HTTP_PORT = int(os.environ.get("MULTIPUT_WEB_HTTP_PORT", "8101"))


def scan(root: Path) -> list[dict]:
    """[{path, size, group}] for every *.pkg under root, sorted by path.
    `group` is the top-level subfolder name (the release/title folder), or
    "" for a .pkg sitting directly in root."""
    if not root.is_dir():
        return []
    out = []
    for p in sorted(root.rglob("*.pkg")):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        group = "" if rel.parent == Path(".") else str(rel.parent)
        out.append({"path": rel.as_posix(), "size": p.stat().st_size, "group": group})
    return out


def add_root(label: str, path: str) -> Path:
    """Validates and registers a new browsable root. Raises ValueError
    with an operator-facing reason on any rejection -- this is the one
    place user-supplied text turns into a filesystem path, so every
    failure mode is explicit rather than surfacing as a bare 500 or,
    worse, silently scanning somewhere unintended."""
    label = label.strip()
    if not label:
        raise ValueError("label required")
    try:
        resolved = Path(path).expanduser().resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"path does not exist: {exc}") from exc
    if not resolved.is_dir():
        raise ValueError(f"not a directory: {resolved}")
    if resolved != DOWNLOAD_BASE and DOWNLOAD_BASE not in resolved.parents:
        raise ValueError(f"must be under {DOWNLOAD_BASE} (got {resolved})")
    ROOTS[label] = resolved
    return resolved


def _multiput_listener_up(host: str, port: int, timeout: float = 1.0) -> bool:
    """A bare connect-and-close is fine against MultiPut's own listener --
    the fragile one-shot port is GoldHEN's PayLoader (9090), probed inside
    multiput_inject.inject() itself via the real protocol, never here."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@dataclass
class PushState:
    running: bool = False
    host: str = ""
    files_total: int = 0
    files_done: int = 0
    current_file: str = ""
    current_sent: int = 0
    current_total: int = 0
    current_rate_mb_s: float = 0.0
    log: list[str] = field(default_factory=list)
    error: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "running": self.running,
            "host": self.host,
            "files_total": self.files_total,
            "files_done": self.files_done,
            "current_file": self.current_file,
            "current_sent": self.current_sent,
            "current_total": self.current_total,
            "current_rate_mb_s": round(self.current_rate_mb_s, 1),
            "log": self.log[-40:],
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


# Single-operator tool, one push at a time: plain module-level state, no
# store/locking machinery. Written from a background thread (see
# _blocking_push) and read from the event loop; individual attribute
# writes are GIL-atomic, which is all a progress *display* needs.
state = PushState()


def _blocking_push(files: list[tuple[str, str, int]], streams: int) -> None:
    """Run the parallel TCP push off the asyncio event loop.

    Every file is a fresh full transfer; the receiver's SETUP creates and
    truncates the destination.
    """
    def log(msg: str) -> None:
        state.log.append(msg)

    hosts = [
        item for item in (part.strip() for part in state.host.split(","))
        if item
    ]
    primary_host = hosts[0]
    if not _multiput_listener_up(primary_host, multiput_push.proto.DEFAULT_PORT):
        log("MultiPut listener not up -- injecting via GoldHEN PayLoader")
        try:
            multiput_inject.inject(primary_host, multiput_inject.DEFAULT_ELF)
        except (OSError, RuntimeError) as exc:
            state.error = f"inject failed: {exc}"
            state.running = False
            state.finished_at = time.time()
            return
        log("MultiPut listener up")

    def on_event(kind: str, **kw) -> None:
        if kind == "setup":
            state.current_file, state.current_total = kw["remote"], kw["total"]
            state.current_sent = 0
            log(f"push {kw['remote']} ({kw['total']:,} bytes, "
                f"{kw['streams']} streams)")
        elif kind == "progress":
            state.current_sent, state.current_rate_mb_s = kw["sent"], kw["rate_mb_s"]
        elif kind == "stream_failed":
            label = f"stream {kw['index']}" if kw["index"] >= 0 else "transfer"
            log(f"{label} failed: {kw['error']}")
        elif kind == "done":
            state.current_sent = kw["sent"]
            log(f"done {kw['remote']} ({kw['avg_mb_s']:.1f} MB/s avg)")

    try:
        for local, remote, _size in files:
            ok = multiput_push.push_multistream(
                hosts, multiput_push.proto.DEFAULT_PORT, local, remote,
                streams=streams, on_event=on_event,
            )
            if not ok:
                raise RuntimeError(f"{remote}: transfer failed, see log")
            state.files_done += 1
    except Exception as exc:
        state.error = str(exc)
        log(f"ERROR: {exc}")
    finally:
        state.running = False
        state.finished_at = time.time()


async def run_push(root_label: str, rel_paths: list[str], streams: int) -> None:
    root = ROOTS[root_label]
    files: list[tuple[str, str, int]] = []
    for rel in rel_paths:
        local = root / rel
        if not local.is_file():
            continue
        remote = f"{REMOTE_ROOT}/{local.name}"
        files.append((str(local), remote, local.stat().st_size))

    state.files_total = len(files)
    state.files_done = 0
    state.current_file = ""
    state.current_sent = 0
    state.current_total = 0
    state.log = []
    state.error = ""
    state.started_at = time.time()
    state.finished_at = 0.0

    await asyncio.to_thread(_blocking_push, files, streams)


class PushBody(BaseModel):
    host: str
    root: str
    paths: list[str]
    streams: int = DEFAULT_STREAMS


class AddRootBody(BaseModel):
    label: str
    path: str


def create_app() -> FastAPI:
    app = FastAPI(title="MultiPut Push")
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return templates.TemplateResponse(request, "index.html", {
            "default_host": DEFAULT_HOST,
            "default_streams": DEFAULT_STREAMS,
            "download_base": str(DOWNLOAD_BASE),
        })

    @app.get("/api/roots")
    async def api_roots():
        return {"base": str(DOWNLOAD_BASE),
                "roots": [{"label": label, "path": str(p)} for label, p in ROOTS.items()]}

    @app.post("/api/roots")
    async def api_add_root(body: AddRootBody):
        try:
            resolved = add_root(body.label, body.path)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return {"label": body.label.strip(), "path": str(resolved)}

    @app.get("/api/files")
    async def api_files(root: str | None = None):
        label = root or next(iter(ROOTS))
        if label not in ROOTS:
            raise HTTPException(404, f"unknown root: {label}")
        return {"root": label, "path": str(ROOTS[label]), "files": scan(ROOTS[label])}

    @app.get("/api/status")
    async def api_status():
        return state.to_dict()

    @app.post("/api/push")
    async def api_push(body: PushBody):
        if state.running:
            raise HTTPException(409, "a push is already running")
        if body.root not in ROOTS:
            raise HTTPException(400, f"unknown root: {body.root}")
        if not body.paths:
            raise HTTPException(400, "no files selected")
        hosts = [
            item for item in (part.strip() for part in body.host.split(","))
            if item
        ]
        if not hosts:
            raise HTTPException(400, "at least one console host required")
        if not 1 <= body.streams <= 64:
            raise HTTPException(400, "streams must be between 1 and 64")
        state.running = True  # set synchronously -- closes the race against a double-click
        state.host = ",".join(hosts)
        asyncio.create_task(
            run_push(body.root, body.paths, body.streams)
        )
        return {"ok": True}

    return app


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(create_app(), host=HTTP_HOST, port=HTTP_PORT, log_level="info")
