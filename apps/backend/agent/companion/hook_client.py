#!/usr/bin/env python3
"""Echo Companion's hook for Claude Code and Codex. Standard library only.

Installed as a copy at ~/.local/share/echospeak/companion/echo-companion-hook.py and registered through
each agent's official hook mechanism:

    Claude Code  ~/.claude/settings.json  hooks  (event JSON on stdin)
    Codex        ~/.codex/hooks.json      hooks  (event JSON on stdin)
    Codex        ~/.codex/config.toml     notify (event JSON as the last argument)

It keeps only allowlisted fields, drops events outside the owner's chosen project folders,
masks obvious secrets, and writes one small JSON file into a private spool directory that
EchoSpeak reads later. It never prints, never blocks and always exits 0, so a hook can never
change what an agent does or slow it down, and nothing breaks while EchoSpeak is closed.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time

HOME = os.environ.get("ECHOSPEAK_COMPANION_HOME") or os.path.expanduser("~/.local/share/echospeak/companion")
SPOOL = os.path.join(HOME, "spool")
SCOPE = os.path.join(HOME, "scope.json")
MAX_INPUT = 2 * 1024 * 1024
MAX_MESSAGE = 12000
MAX_PROMPT = 600
MAX_SPOOL_FILES = 5000

AGENTS = {"claude-code", "codex"}
# Fields copied from the agent's event; everything else (tool inputs and outputs included) is dropped.
KEEP = {
    "session_id", "thread-id", "turn_id", "turn-id", "prompt_id", "cwd", "hook_event_name", "type",
    "source", "reason", "agent_id", "agent_type", "transcript_path", "agent_transcript_path",
    "last_assistant_message", "last-assistant-message", "prompt", "message", "notification_type",
    "tool_name", "permission_mode", "stop_hook_active", "model", "title",
}
SECRET = re.compile(
    r"(sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"
    r"|(?i:(?:api[_-]?key|token|secret|password)\s*[:=]\s*)\S{8,})"
)


def mask(text: str, limit: int) -> str:
    return SECRET.sub("[redacted]", text[:limit])


def scope() -> dict:
    try:
        with open(SCOPE, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def allowed(agent: str, cwd: str, settings: dict) -> bool:
    if not settings.get("enabled", True) or not settings.get("agents", {}).get(agent, False):
        return False
    roots = [os.path.realpath(os.path.expanduser(str(root))) for root in settings.get("roots", []) if str(root).strip()]
    here = os.path.realpath(cwd or "/nonexistent")
    return any(here == root or here.startswith(root.rstrip(os.sep) + os.sep) for root in roots)


def clean(event: dict, settings: dict) -> dict:
    kept = {}
    for key, value in event.items():
        if key not in KEEP or value is None:
            continue
        if isinstance(value, str):
            if key in {"last_assistant_message", "last-assistant-message"}:
                value = mask(value, MAX_MESSAGE)
            elif key == "prompt":
                if not settings.get("capture_prompts", True):
                    continue
                value = mask(value, MAX_PROMPT)
            else:
                value = mask(value, 2000)
        elif not isinstance(value, (bool, int, float)):
            continue
        kept[key] = value
    return kept


def main(argv: list[str]) -> int:
    try:
        agent = argv[1] if len(argv) > 1 else ""
        if agent not in AGENTS:
            return 0
        if len(argv) > 2 and argv[2].lstrip().startswith("{"):
            event = json.loads(argv[2][:MAX_INPUT])  # Codex notify passes JSON as an argument
            event.setdefault("hook_event_name", "notify")
        else:
            raw = sys.stdin.buffer.read(MAX_INPUT + 1)
            if len(raw) > MAX_INPUT:
                return 0
            event = json.loads(raw or b"{}")
        if not isinstance(event, dict):
            return 0
        settings = scope()
        if not allowed(agent, str(event.get("cwd") or ""), settings):
            return 0
        os.makedirs(SPOOL, mode=0o700, exist_ok=True)
        if len(os.listdir(SPOOL)) >= MAX_SPOOL_FILES:
            return 0  # EchoSpeak has been away a long time; never fill the disk
        record = {"v": 1, "agent": agent, "received_at": time.time(), "event": clean(event, settings)}
        name = f"{time.time_ns()}-{agent}-{os.getpid()}"
        temporary = os.path.join(SPOOL, f".{name}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(record, handle)
        os.replace(temporary, os.path.join(SPOOL, f"{name}.json"))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
