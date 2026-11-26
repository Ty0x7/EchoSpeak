"""Where a running backend can be found: ``endpoint.json`` in a private per-user runtime directory.

The terminal client (``apps/terminal``) reads this file to attach to the backend the desktop app
(or ``echospeak serve``) started, without the per-launch API key ever being typed or put on argv.

Why writing the key here adds no new exposure (POSIX only):

- On Linux a process running as the same user can already read the backend's environment, which
  holds ``API_AUTH_KEY``, through ``/proc/<pid>/environ``. The file is ``0600`` inside a ``0700``
  directory owned by the user (normally ``$XDG_RUNTIME_DIR``, a per-user tmpfs cleared at logout),
  so only that same user can read it.
- The directory is refused if it is a symlink, owned by someone else, or still group/world
  accessible after ``chmod 0700``; the reader refuses files with a foreign owner or loose mode.
- On Windows nothing is written, so the desktop app's "the key is never persisted" property holds.

The file is removed on clean shutdown only while it still names this process. A crash leaves a
stale file; ``read_endpoint`` ignores it because its pid is no longer alive.
"""
from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path
from typing import Any, Optional

from loguru import logger

SCHEMA = 1
FILENAME = "endpoint.json"
# Module-level so tests can exercise the non-POSIX path without touching os.name (pathlib needs it).
IS_POSIX = os.name == "posix"


DESKTOP_APP_ID = "ai.echospeak.desktop"


def default_data_dir() -> Path:
    """The owner's data folder: the desktop app's, which the terminal attaches to by default."""
    base = os.environ.get("XDG_DATA_HOME", "").strip() or str(Path.home() / ".local" / "share")
    return Path(base) / DESKTOP_APP_ID / "runtime"


def _same_dir(a: Any, b: Any) -> bool:
    try:
        return os.path.realpath(os.path.expanduser(str(a))) == os.path.realpath(os.path.expanduser(str(b)))
    except (TypeError, ValueError):
        return False


def runtime_dir(data_dir: Optional[str | os.PathLike] = None) -> Path:
    """Where this data folder's endpoint lives.

    The shared ``$XDG_RUNTIME_DIR/echospeak`` belongs to the owner's default data folder
    only. A backend for any other folder (a test, a dev checkout) publishes inside that
    folder, ``<data dir>/runtime``, so it can never replace the owner's endpoint (#213).
    """
    explicit = os.environ.get("ECHOSPEAK_RUNTIME_DIR", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    if data_dir is None:
        from config import DATA_DIR

        data_dir = DATA_DIR
    xdg = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    if xdg and _same_dir(data_dir, default_data_dir()):
        return Path(xdg) / "echospeak"
    return Path(data_dir) / "runtime"


def _private_dir(path: Path) -> Optional[str]:
    """Create/tighten the directory; return a reason it is unsafe, or None."""
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            return "it is a symlink or not a directory"
        if info.st_uid != os.getuid():
            return "it is owned by another user"
        if info.st_mode & 0o077:
            os.chmod(path, 0o700)
            info = os.lstat(path)
        if info.st_mode & 0o077:
            return "it is accessible to other users"
    except OSError as exc:
        return f"it could not be prepared ({type(exc).__name__})"
    return None


def write_endpoint(host: str, port: int, *, auth_key: str = "", auth_required: bool = False,
                   version: str = "", data_dir: str = "") -> Optional[Path]:
    """Publish this process's endpoint. Never raises; returns the file path or None."""
    if not IS_POSIX:
        return None
    directory = runtime_dir(data_dir or None)
    problem = _private_dir(directory)
    if problem:
        logger.warning("Not writing the endpoint file to {}: {}", directory, problem)
        return None
    payload = {
        "schema": SCHEMA,
        "pid": os.getpid(),
        "host": str(host),
        "port": int(port),
        "auth_key": str(auth_key or ""),
        "auth_required": bool(auth_required),
        "version": str(version),
        "data_dir": str(data_dir),
        "started_at": time.time(),
    }
    target = directory / FILENAME
    current = _load(target)
    if current and int(current.get("pid") or 0) not in {0, os.getpid()} and _pid_alive(int(current.get("pid") or 0)):
        # Another live backend owns this endpoint; never take it over.
        logger.warning("Not replacing {}: it belongs to live backend pid {} ({})", target, current.get("pid"),
                       current.get("data_dir") or "unknown data folder")
        return None
    temp = directory / f".{FILENAME}.{os.getpid()}.tmp"
    try:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
    except OSError as exc:
        logger.warning("Could not write the endpoint file in {}: {}", directory, type(exc).__name__)
        try:
            os.unlink(temp)
        except OSError:
            pass
        return None
    logger.info("Endpoint file written to {} (host {}, port {})", target, host, port)
    return target


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _load(path: Path) -> Optional[dict[str, Any]]:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    with os.fdopen(fd, "r", encoding="utf-8") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            logger.warning("Ignoring endpoint file {}: wrong owner or permissions", path)
            return None
        try:
            data = json.loads(handle.read(65536))
        except ValueError:
            return None
    return data if isinstance(data, dict) and data.get("schema") == SCHEMA else None


def read_endpoint(data_dir: Optional[str | os.PathLike] = None) -> Optional[dict[str, Any]]:
    """The live endpoint, or None when missing, unsafe or stale (its process is gone)."""
    if not IS_POSIX:
        return None
    data = _load(runtime_dir(data_dir) / FILENAME)
    if not data:
        return None
    try:
        pid = int(data.get("pid") or 0)
        int(data.get("port") or 0)
    except (TypeError, ValueError):
        return None
    return data if _pid_alive(pid) else None


def remove_endpoint(data_dir: Optional[str | os.PathLike] = None) -> bool:
    """Remove the file only if it still describes this process."""
    if not IS_POSIX:
        return False
    path = runtime_dir(data_dir) / FILENAME
    data = _load(path)
    if not data or int(data.get("pid") or 0) != os.getpid():
        return False
    try:
        os.unlink(path)
    except OSError:
        return False
    return True


def ensure_endpoint(host: str, port: int, **kwargs: Any) -> Optional[Path]:
    """Re-publish this backend's endpoint if the file went missing or names a dead process."""
    if not IS_POSIX:
        return None
    data_dir = kwargs.get("data_dir") or None
    current = _load(runtime_dir(data_dir) / FILENAME)
    if current and int(current.get("pid") or 0) == os.getpid():
        return runtime_dir(data_dir) / FILENAME
    if current and _pid_alive(int(current.get("pid") or 0)):
        return None
    return write_endpoint(host, port, **kwargs)
