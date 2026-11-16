"""The shared backend: attach to the running one, or start one detached that outlives the terminal.

Standard library only. The detached backend is started exactly like ``echospeak serve`` (loopback,
auth on, a fresh random key in the child's environment only, ``ECHOSPEAK_RUNTIME_KIND=desktop``),
but in its own session so closing the terminal does not stop it. It publishes the usual endpoint
file; we also record ``terminal-backend.json`` next to it so ``echospeak backend stop`` only ever
stops a backend this client started (never the desktop app's).
"""
from __future__ import annotations

import json
import os
import secrets
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional

from . import discovery
from .client import ApiError, Client, ConnectError

RECORD = "terminal-backend.json"
LOG_NAME = "terminal-backend.log"


class BackendError(Exception):
    pass


@dataclass
class Status:
    running: bool
    url: str = ""
    pid: int = 0
    version: str = ""
    data_dir: str = ""
    started_by_terminal: bool = False
    log: str = ""
    lock_pid: int = 0      # pid holding the data folder's writer lock, if readable


def runtime_dir(env: Mapping[str, str] = os.environ) -> Path:
    """Where *this* data folder's backend publishes its endpoint (first of the backend's order)."""
    return discovery.runtime_dirs(env)[0]


def _record_path(env: Mapping[str, str]) -> Path:
    return runtime_dir(env) / RECORD


def read_record(env: Mapping[str, str] = os.environ) -> Optional[dict]:
    try:
        data = json.loads(_record_path(env).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not discovery._pid_alive(int(data.get("pid") or 0)):
        return None
    return data


def _write_record(env: Mapping[str, str], payload: dict) -> None:
    path = _record_path(env)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


def lock_holder(data: Path) -> int:
    """The pid written into the backend's single-writer lock file, if any is alive."""
    for path in (data / "phase3" / ".echospeak_state.lock", data / ".echospeak_state.lock"):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            if line.startswith("pid="):
                try:
                    pid = int(line[4:].strip())
                except ValueError:
                    continue
                if discovery._pid_alive(pid):
                    return pid
    return 0


def status(env: Mapping[str, str] = os.environ) -> Status:
    data = discovery.data_dir(env)
    found = None
    for directory in discovery.runtime_dirs(env):
        found = discovery.read_endpoint_file(directory / "endpoint.json")
        if found:
            break
    record = read_record(env)
    log = str(Path(env.get("ECHOSPEAK_LOGS_DIR") or data / "logs") / LOG_NAME)
    if not found:
        return Status(False, data_dir=str(data), log=log, lock_pid=lock_holder(data))
    pid = int(found.get("pid") or 0)
    return Status(True, url=discovery._file_url(found), pid=pid, version=str(found.get("version") or ""),
                  data_dir=str(found.get("data_dir") or ""), log=log,
                  started_by_terminal=bool(record and int(record.get("pid") or 0) == pid),
                  lock_pid=lock_holder(data))


def _ready(client: Client) -> bool:
    try:
        client.get("/threads", limit=1)  # proves auth works, not only /health
        return True
    except (ConnectError, ApiError):
        return False


def start_detached(env: Mapping[str, str] = os.environ, wait: float = 180.0,
                   progress: Callable[[float], None] = lambda _t: None) -> discovery.Endpoint:
    """Start the backend in its own session (it survives this terminal) and wait until it serves."""
    root, python = discovery.backend_paths(env)
    port = discovery.free_port()
    key = secrets.token_urlsafe(32)
    child_env = discovery.serve_env(port, key, env)
    log_path = Path(child_env["ECHOSPEAK_LOGS_DIR"]) / LOG_NAME
    with open(log_path, "ab") as log:
        os.chmod(log_path, 0o600)
        log.write(f"\n=== echospeak terminal: starting backend on 127.0.0.1:{port} at {time.ctime()} ===\n".encode())
        log.flush()
        # The key travels in the child's environment only, never argv. start_new_session: a SIGHUP
        # from the closing terminal does not reach it.
        child = subprocess.Popen([python, "app.py", "--mode", "api", "--host", "127.0.0.1", "--port", str(port)],
                                 cwd=str(root), env=child_env, stdout=log, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, start_new_session=True, close_fds=True)
    _write_record(env, {"pid": child.pid, "port": port, "started_at": time.time(), "log": str(log_path),
                        "data_dir": child_env["ECHOSPEAK_DATA_DIR"]})
    client = Client(f"http://127.0.0.1:{port}", key, timeout=3)
    started = time.monotonic()
    while True:
        if child.poll() is not None:
            tail = _tail(log_path)
            holder = lock_holder(Path(child_env["ECHOSPEAK_DATA_DIR"]))
            hint = f" Another backend (pid {holder}) holds this data folder." if holder else ""
            raise BackendError(f"the backend exited with code {child.returncode}.{hint} Log: {log_path}"
                               + (f"\n{tail}" if tail else ""))
        if _ready(client):
            break
        elapsed = time.monotonic() - started
        if elapsed > wait:
            raise BackendError(f"the backend did not become ready in {wait:.0f}s (still starting, pid {child.pid}). "
                               f"Log: {log_path}")
        progress(elapsed)
        time.sleep(0.4)
    version = ""
    try:
        version = str(client.health().get("version") or "")
    except (ConnectError, ApiError):
        pass
    return discovery.Endpoint(f"http://127.0.0.1:{port}", key, "started", version=version, pid=child.pid)


def _tail(path: Path, lines: int = 6) -> str:
    try:
        rows = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    from .render import sanitize

    return "\n".join("  " + sanitize(r)[:200] for r in rows[-lines:])


def stop(env: Mapping[str, str] = os.environ, any_backend: bool = False, wait: float = 25.0) -> str:
    st = status(env)
    if not st.running:
        return "no backend is running for this data folder"
    if not st.started_by_terminal and not any_backend:
        raise BackendError(f"the backend (pid {st.pid}) was not started by the terminal; it is probably the desktop "
                           "app's. Quit the app to stop it, or pass --any to stop it anyway.")
    os.kill(st.pid, signal.SIGTERM)
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if not discovery._pid_alive(st.pid):
            break
        time.sleep(0.25)
    else:
        os.kill(st.pid, signal.SIGKILL)
        return f"backend pid {st.pid} did not stop in {wait:.0f}s; killed"
    try:
        _record_path(env).unlink()
    except OSError:
        pass
    return f"backend pid {st.pid} stopped"
