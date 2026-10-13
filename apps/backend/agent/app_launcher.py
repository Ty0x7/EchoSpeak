"""Resolve Windows Start-menu names after the caller checks launch permissions."""

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
