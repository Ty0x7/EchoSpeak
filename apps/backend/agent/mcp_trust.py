"""Review and pin MCP servers before they run (OWASP ASI04, supply chain).

An MCP server is a program (or URL) that adds tools the agent can call. Two
risks: a server added or edited in settings.json starts without anyone looking,
and an approved server quietly changes its tools later (a "rug pull": the same
name, new instructions hidden in a tool description).

So EchoSpeak pins two fingerprints per server:
  - config: how it starts (command, arguments, folder, URL, environment, headers)
  - tools:  the tools it offered (names, descriptions, input schemas), recorded
            the first time it runs after approval
A new or edited server doesn't start until the owner approves it in Settings;
if its tools change, none of them are registered until the owner approves again.

Servers that were already configured before this check existed are trusted
once, the first time it runs, so upgrading doesn't switch off working setups.
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any

_lock = threading.Lock()


def _path() -> Path:
    from config import DATA_DIR

    return Path(DATA_DIR) / "mcp-trust.json"


def _load() -> dict[str, Any]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict[str, Any]) -> None:
    path = _path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def config_fingerprint(raw: dict[str, Any]) -> str:
    """How the server starts. Secrets are included only as hashes."""
    raw = dict(raw or {})
    return _sha({
        "command": str(raw.get("command") or raw.get("cmd") or "").strip(),
        "args": [str(a) for a in list(raw.get("args") or [])],
        "cwd": str(raw.get("cwd") or "").strip(),
        "transport": str(raw.get("transport") or "stdio").strip().casefold(),
        "url": str(raw.get("url") or raw.get("endpoint") or "").strip(),
        "env": {str(k): _sha(str(v)) for k, v in dict(raw.get("env") or {}).items()},
        "headers": {str(k): _sha(str(v)) for k, v in dict(raw.get("headers") or {}).items()},
    })


def tools_fingerprint(tools: list[dict[str, Any]]) -> str:
    rows = sorted(
        (str(t.get("name") or ""), str(t.get("description") or ""),
         json.dumps(t.get("inputSchema") or t.get("input_schema") or {}, sort_keys=True))
        for t in tools or []
    )
    return _sha(rows)


def adopt_existing(servers: dict[str, Any]) -> None:
    """First run with this check: trust the servers the owner had already set up."""
    with _lock:
        if _path().exists():
            return
        data = {"servers": {str(name): {"config": config_fingerprint(raw), "tools": ""}
                            for name, raw in (servers or {}).items() if isinstance(raw, dict)}}
        _save(data)


def check(name: str, raw: dict[str, Any]) -> str:
    """'trusted', 'new' (never approved) or 'changed' (how it starts changed since approval)."""
    pinned = (_load().get("servers") or {}).get(str(name)) or {}
    if not pinned:
        return "new"
    return "trusted" if pinned.get("config") == config_fingerprint(raw) else "changed"


def tools_ok(name: str, tools: list[dict[str, Any]]) -> bool:
    """Record the tool list on the first run after approval; afterwards it must match."""
    with _lock:
        data = _load()
        entry = (data.setdefault("servers", {})).get(str(name))
        if not entry:
            return False
        fingerprint = tools_fingerprint(tools)
        if not entry.get("tools"):
            entry["tools"] = fingerprint
            _save(data)
            return True
        return entry["tools"] == fingerprint


def approve(name: str, raw: dict[str, Any]) -> None:
    """Pin how the server starts now; its tools are recorded the next time it runs."""
    with _lock:
        data = _load()
        data.setdefault("servers", {})[str(name)] = {"config": config_fingerprint(raw), "tools": ""}
        _save(data)


def describe(raw: dict[str, Any]) -> str:
    """What the owner is approving, in one line (secrets left out)."""
    raw = dict(raw or {})
    url = str(raw.get("url") or raw.get("endpoint") or "").strip()
    if url:
        return f"Connects to {url}"
    command = " ".join([str(raw.get("command") or raw.get("cmd") or "")] + [str(a) for a in list(raw.get("args") or [])])
    env = sorted(dict(raw.get("env") or {}))
    return f"Runs: {command.strip()}" + (f" (with settings for {', '.join(env)})" if env else "")
