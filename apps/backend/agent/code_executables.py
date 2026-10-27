"""Find the official coding-agent executables the owner installed: Codex and Claude Code.

Each is run unmodified, as its vendor published it; EchoSpeak never ships, patches or
wraps either one. Lookup is passive (paths only): nothing here runs the program.

Order: the path set in Settings › Models › Coding agents, then PATH, then where each
vendor's own Windows installer puts it. The Codex desktop app (Microsoft Store package)
carries the same `codex` CLI that serves `app-server`, but doesn't put it on PATH, so a
PATH-only lookup reported "not installed" on a PC where Codex was installed and signed in.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys
from typing import Optional

_PACKAGES = r"Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion\AppModel\Repository\Packages"


def _usable(path) -> Optional[Path]:
    """A real native executable. npm's .cmd/.ps1 shims need a shell, which isn't used."""
    try:
        candidate = Path(path)
    except TypeError:
        return None
    if not candidate.is_absolute() or not candidate.is_file():
        return None
    if os.name == "nt" and candidate.suffix.lower() != ".exe":
        return None
    return candidate


def _store_package_root(prefix: str) -> Optional[Path]:
    """Install folder of the newest x64 Microsoft Store package whose name starts with `prefix`."""
    if sys.platform != "win32":
        return None
    try:
        import winreg

        roots = []
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _PACKAGES) as packages:
            index = 0
            while True:
                try:
                    name = winreg.EnumKey(packages, index)
                except OSError:
                    break
                index += 1
                if name.startswith(prefix) and "_x64_" in name:
                    with winreg.OpenKey(packages, name) as package:
                        roots.append((name, winreg.QueryValueEx(package, "PackageRootFolder")[0]))
        return Path(max(roots)[1]) if roots else None
    except OSError:
        return None


def _newest(paths) -> Optional[Path]:
    found = [p for p in (_usable(path) for path in paths) if p]
    return max(found, key=lambda p: p.stat().st_mtime) if found else None


def _codex_candidates():
    yield shutil.which("codex")
    root = _store_package_root("OpenAI.Codex_")
    if root:
        yield root / "app" / "resources" / "codex.exe"
    local = os.environ.get("LOCALAPPDATA")
    if local:
        # The desktop app also copies its CLI here (one folder per build).
        yield _newest((Path(local) / "OpenAI" / "Codex" / "bin").glob("*/codex.exe"))


def _claude_candidates():
    yield shutil.which("claude")
    home = Path.home()
    # Anthropic's native installer.
    yield home / ".local" / "bin" / ("claude.exe" if os.name == "nt" else "claude")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        yield Path(local) / "Programs" / "claude" / "claude.exe"


_RUNTIMES = {
    "codex": ("codex_executable", "_codex_candidates", "Codex"),
    "claude_cli": ("claude_code_executable", "_claude_candidates", "Claude Code"),
}


def locate(runtime: str) -> dict:
    """{"path", "source", "configured", "problem"} for one runtime, without running it."""
    from config import config

    setting, finder, name = _RUNTIMES[runtime]
    candidates = globals()[finder]
    configured = str(getattr(config, setting, "") or "").strip()
    if configured:
        path = _usable(configured)
        if path:
            return {"path": str(path), "source": "settings", "configured": configured, "problem": ""}
        return {"path": "", "source": "", "configured": configured,
                "problem": f"The {name} path in Settings isn't an existing .exe file: {configured}"}
    for index, candidate in enumerate(candidates()):
        path = _usable(candidate) if candidate else None
        if path:
            source = "path" if index == 0 else "installed app"
            return {"path": str(path), "source": source, "configured": "", "problem": ""}
    return {"path": "", "source": "", "configured": "",
            "problem": f"{name} wasn't found. Install it, or set its path in Settings › Models › Coding agents."}


def executable(runtime: str) -> str:
    """The executable to launch, or a ValueError the owner can act on."""
    found = locate(runtime)
    if not found["path"]:
        raise ValueError(found["problem"])
    return found["path"]
