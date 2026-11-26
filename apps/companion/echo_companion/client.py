"""Talk to the EchoSpeak backend: find it, call it, and listen to its companion stream.

Discovery follows the terminal client (apps/terminal/echospeak_cli/discovery.py). It
checks ECHOSPEAK_URL and ECHOSPEAK_KEY first, then the private endpoint file
``$ECHOSPEAK_RUNTIME_DIR`` or ``$XDG_RUNTIME_DIR/echospeak/endpoint.json``. That file is
0600, owned by this user and names a live pid. The key is never put on argv or logged.
"""

from __future__ import annotations

import json
import os
import stat
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional


def runtime_dirs() -> list[Path]:
    explicit = os.environ.get("ECHOSPEAK_RUNTIME_DIR", "").strip()
    if explicit:
        return [Path(explicit).expanduser()]
    xdg = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    return [Path(xdg) / "echospeak"] if xdg else []


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def discover() -> tuple[str, str]:
    """(base url, key) or ("", "") when no backend is running."""

    url = os.environ.get("ECHOSPEAK_URL", "").strip()
    if url:
        return url.rstrip("/"), os.environ.get("ECHOSPEAK_KEY", "").strip()
    for directory in runtime_dirs():
        path = directory / "endpoint.json"
        try:
            info = os.lstat(path)
            if stat.S_ISLNK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            if not _alive(int(data.get("pid") or 0)):
                continue
            host = str(data.get("host") or "127.0.0.1")
            if host not in {"127.0.0.1", "localhost", "::1"}:
                continue
            return f"http://{host}:{int(data['port'])}", str(data.get("auth_key") or "")
        except (OSError, ValueError, KeyError):
            continue
    return "", ""


class Backend:
    def __init__(self) -> None:
        self.url, self.key = discover()

    def refresh(self) -> bool:
        self.url, self.key = discover()
        return bool(self.url)

    def _request(self, method: str, path: str, body: Optional[dict] = None, timeout: float = 10) -> Any:
        if not self.url and not self.refresh():
            raise ConnectionError("EchoSpeak isn't running")
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.url + path, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        if self.key:
            request.add_header("x-echospeak-key", self.key)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read() or b"{}")

    def get(self, path: str, timeout: float = 10) -> Any:
        return self._request("GET", path, timeout=timeout)

    def post(self, path: str, body: dict, timeout: float = 10) -> Any:
        return self._request("POST", path, body, timeout=timeout)

    def stream(self, path: str, on_event: Callable[[str, dict], None], on_state: Callable[[bool], None],
               stop: threading.Event) -> None:
        """Read server-sent events until `stop`; reconnect with backoff (1 s → 30 s)."""

        delay = 1.0
        while not stop.is_set():
            try:
                if not self.url:
                    self.refresh()
                if not self.url:
                    raise ConnectionError("no backend")
                request = urllib.request.Request(self.url + path)
                if self.key:
                    request.add_header("x-echospeak-key", self.key)
                with urllib.request.urlopen(request, timeout=40) as response:
                    on_state(True)
                    delay = 1.0
                    name, data = "message", []
                    for raw in response:
                        if stop.is_set():
                            return
                        line = raw.decode("utf-8", "replace").rstrip("\n")
                        if line.startswith("event:"):
                            name = line[6:].strip()
                        elif line.startswith("data:"):
                            data.append(line[5:].strip())
                        elif not line:
                            if data:
                                try:
                                    on_event(name, json.loads("\n".join(data)))
                                except ValueError:
                                    pass
                            name, data = "message", []
            except (OSError, urllib.error.URLError, ConnectionError):
                pass
            on_state(False)
            self.url = ""  # the backend may have restarted on a new port
            stop.wait(delay)
            delay = min(30.0, delay * 2)
