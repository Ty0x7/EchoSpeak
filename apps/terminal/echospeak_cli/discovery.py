"""Find the backend: --url/--key, then ECHOSPEAK_URL/ECHOSPEAK_KEY, then the endpoint file.

The endpoint-file reader duplicates the small checks in apps/backend/agent/endpoint_file.py on
purpose: this client never imports backend modules.
"""
from __future__ import annotations

import ipaddress
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional
from urllib.parse import urlsplit

DEFAULT_URL = "http://127.0.0.1:8000"


class DiscoveryError(Exception):
    pass


@dataclass
class Endpoint:
    url: str
    key: str
    source: str  # "flag" | "env" | "endpoint-file" | "default"
    version: str = ""
    pid: int = 0


DESKTOP_APP_ID = "ai.echospeak.desktop"


def data_dir(env: Mapping[str, str] = os.environ) -> Path:
    """The desktop app's data folder (Tauri ``app_local_data_dir()/runtime``), so the app and the
    terminal share one backend's agents, chats, memory and settings. ECHOSPEAK_DATA_DIR overrides it."""
    if env.get("ECHOSPEAK_DATA_DIR", "").strip():
        return Path(env["ECHOSPEAK_DATA_DIR"]).expanduser()
    base = env.get("XDG_DATA_HOME", "").strip() or str(Path(env.get("HOME") or Path.home()) / ".local" / "share")
    return Path(base) / DESKTOP_APP_ID / "runtime"


def runtime_dirs(env: Mapping[str, str] = os.environ) -> list[Path]:
    """Same order as the backend: explicit, XDG runtime, then <data dir>/runtime."""
    if env.get("ECHOSPEAK_RUNTIME_DIR", "").strip():
        return [Path(env["ECHOSPEAK_RUNTIME_DIR"]).expanduser()]
    dirs = []
    if env.get("XDG_RUNTIME_DIR", "").strip():
        dirs.append(Path(env["XDG_RUNTIME_DIR"]) / "echospeak")
    dirs.append(data_dir(env) / "runtime")
    return dirs


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


def read_endpoint_file(path: Path) -> Optional[dict]:
    """The file's contents if it is ours, private and names a live process; else None."""
    if os.name != "posix":
        return None
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    with os.fdopen(fd, "r", encoding="utf-8") as fh:
        info = os.fstat(fh.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            return None
        try:
            data = json.loads(fh.read(65536))
        except ValueError:
            return None
    if not isinstance(data, dict) or data.get("schema") != 1:
        return None
    try:
        if not _pid_alive(int(data.get("pid") or 0)) or not int(data.get("port") or 0):
            return None
    except (TypeError, ValueError):
        return None
    return data


def is_loopback(host: str) -> bool:
    host = (host or "").strip("[]").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_url(url: str, insecure_remote: bool = False) -> str:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise DiscoveryError(f"not an http(s) URL: {url}")
    if parts.scheme == "http" and not is_loopback(parts.hostname) and not insecure_remote:
        raise DiscoveryError(
            f"refusing plain http to non-loopback host {parts.hostname}: the API key would cross the network "
            "unencrypted. Use https, an SSH tunnel, or pass --insecure-remote.")
    return url.rstrip("/")


def _file_url(data: dict) -> str:
    host = str(data.get("host") or "127.0.0.1")
    if host in {"0.0.0.0", "::", ""}:
        host = "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{int(data['port'])}"


def discover(url: str = "", key: str = "", env: Mapping[str, str] = os.environ,
             insecure_remote: bool = False) -> Endpoint:
    """Precedence: flags > environment > endpoint file > default URL (no key)."""
    if url:
        return Endpoint(check_url(url, insecure_remote), key or env.get("ECHOSPEAK_KEY", ""), "flag")
    if env.get("ECHOSPEAK_URL", "").strip():
        return Endpoint(check_url(env["ECHOSPEAK_URL"].strip(), insecure_remote),
                        key or env.get("ECHOSPEAK_KEY", ""), "env")
    for directory in runtime_dirs(env):
        data = read_endpoint_file(directory / "endpoint.json")
        if data:
            return Endpoint(_file_url(data), key or str(data.get("auth_key") or ""), "endpoint-file",
                            version=str(data.get("version") or ""), pid=int(data.get("pid") or 0))
    return Endpoint(DEFAULT_URL, key or env.get("ECHOSPEAK_KEY", ""), "default")


# ── serve: start a private backend as a child process ─────────────────
def backend_paths(env: Mapping[str, str] = os.environ) -> tuple[Path, str]:
    import sys

    root = Path(env.get("ECHOSPEAK_BACKEND_DIR") or Path(__file__).resolve().parents[2] / "backend")
    if not (root / "app.py").is_file():
        raise DiscoveryError(f"backend not found at {root}; set ECHOSPEAK_BACKEND_DIR")
    explicit = env.get("ECHOSPEAK_BACKEND_PYTHON", "").strip()
    if explicit:
        return root, explicit
    venv = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    candidates = [str(venv)] if venv.exists() else []
    candidates += [sys.executable]
    for candidate in candidates:
        if _can_run_backend(candidate, root):
            return root, candidate
    raise DiscoveryError(
        "no Python with the backend's packages was found. From the EchoSpeak checkout run "
        "`apps/terminal/install.sh --with-backend`, or set ECHOSPEAK_BACKEND_PYTHON to a Python that has "
        "apps/backend/requirements installed")


def _can_run_backend(python: str, root: Path) -> bool:
    """Whether this interpreter has the backend's core packages (a quick import probe, no app code run)."""
    import subprocess

    try:
        probe = subprocess.run([python, "-I", "-c", "import fastapi, loguru, uvicorn"], cwd=str(root),
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return probe.returncode == 0


def free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def serve_env(port: int, key: str, env: Mapping[str, str] = os.environ) -> dict[str, str]:
    """Mirror the desktop launcher (apps/desktop/src-tauri/src/backend.rs): loopback, auth on, no bypass.

    ECHOSPEAK_RUNTIME_KIND=desktop makes config enforce these auth settings over saved settings
    (config.py _enforce_host_auth) and keeps routines/SOUL under the data dir.
    """
    data = data_dir(env)
    data.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(data, 0o700)
    logs = Path(env.get("ECHOSPEAK_LOGS_DIR") or data / "logs")
    logs.mkdir(mode=0o700, parents=True, exist_ok=True)
    child = dict(env)
    child.update({
        "ECHOSPEAK_RUNTIME_KIND": "desktop",
        "ECHOSPEAK_DATA_DIR": str(data),
        "ECHOSPEAK_LOGS_DIR": str(logs),
        "SOUL_PATH": env.get("SOUL_PATH") or str(data / "SOUL.md"),
        "API_HOST": "127.0.0.1",
        "API_PORT": str(port),
        "API_AUTH_ENABLED": "true",
        "API_AUTH_LOCALHOST_BYPASS": "false",
        "API_AUTH_KEY": key,
        "ADMIN_API_KEY": key,
        "API_TRUST_PROXY_HEADERS": "false",
        "PYTHONUNBUFFERED": "1",
    })
    child.pop("ECHOSPEAK_URL", None)
    child.pop("ECHOSPEAK_KEY", None)
    return child
