"""Resolve Windows Start-menu names and desktop shortcuts after the caller checks launch permissions."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess

from agent.child_env import child_env


def installed_app_id(name: str) -> str | None:
    """Exact names only: never interpolate model input into PowerShell code."""
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    script = (
        "$ErrorActionPreference = 'Stop'; "
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
        "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        [str(powershell), "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, encoding="utf-8", errors="replace", timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), env=child_env(),
    )
    if result.returncode:
        raise ValueError("Windows could not read installed apps. Use the exact executable path instead.")
    rows = json.loads(result.stdout.strip() or "[]")
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list):
        raise ValueError("Windows returned an invalid installed-app list.")
    normalize = lambda value: " ".join(str(value or "").strip().casefold().split())
    matches = {
        str(row.get("AppID") or "") for row in rows
        if isinstance(row, dict) and normalize(row.get("Name")) == normalize(name)
        and re.fullmatch(r"[A-Za-z0-9._!{}-]{1,256}", str(row.get("AppID") or ""))
    }
    if len(matches) > 1:
        raise ValueError("Multiple installed apps have that exact name. Use the exact executable path instead.")
    return next(iter(matches), None)


SHORTCUT_SUFFIXES = (".lnk", ".url")
# Programs that run whatever their arguments say. Commands belong in the terminal
# tool, where terminal permissions and approvals apply.
COMMAND_HOSTS = {
    "cmd", "powershell", "pwsh", "wt", "bash", "wsl", "wscript", "cscript", "mshta", "rundll32",
    "regsvr32", "msiexec", "python", "pythonw", "py", "node", "reg", "schtasks", "conhost",
}


def desktop_dirs() -> list[Path]:
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    dirs = [home / "Desktop"]
    if os.environ.get("OneDrive"):
        dirs.append(Path(os.environ["OneDrive"]) / "Desktop")
    if os.environ.get("PUBLIC"):
        dirs.append(Path(os.environ["PUBLIC"]) / "Desktop")
    return dirs


def shortcut_path(app: str) -> Path | None:
    """A desktop shortcut by its exact name ("26.2"), or a full .lnk/.url path."""
    text = str(app or "").strip().strip('"')
    if not text:
        return None
    if "\\" in text or "/" in text:
        path = Path(os.path.expandvars(text))
        return path if path.suffix.lower() in SHORTCUT_SUFFIXES and path.is_file() else None
    if text in {".", ".."}:
        return None
    for folder in desktop_dirs():
        for name in ([text] if text.lower().endswith(SHORTCUT_SUFFIXES) else [text + s for s in SHORTCUT_SUFFIXES]):
            candidate = folder / name
            if candidate.is_file():
                return candidate
    return None


def launch_shortcut(path: Path) -> None:
    """Explorer opens it, so the app never inherits EchoSpeak's environment (keys, tokens)."""
    windows = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    subprocess.Popen([str(windows / "explorer.exe"), str(path)], env=child_env(),
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def runs_commands(command: list[str]) -> bool:
    name = Path(str(command[0] if command else "")).name.lower()
    return name.removesuffix(".exe") in COMMAND_HOSTS
