"""Lean runtime switches. Environment variables win over settings.json keys."""

from __future__ import annotations

import os
from typing import Any

from config import config


def _setting(name: str, default: Any) -> Any:
    env = os.getenv(name.upper())
    if env is not None and env.strip() != "":
        return env.strip()
    value = getattr(config, name.lower(), None)
    return default if value is None else value


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off", ""}


def lean_runtime_enabled() -> bool:
    """The lean loop is the only runtime since 10.0 (the legacy pipeline was removed)."""
    return True


def max_iterations() -> int:
    try:
        return max(1, min(int(_setting("lean_max_iterations", 60)), 500))
    except (TypeError, ValueError):
        return 60


def context_tokens() -> int:
    """Token budget for one request. Defaults to the 64k local context."""
    try:
        configured = int(_setting("lean_context_tokens", 0) or 0)
    except (TypeError, ValueError):
        configured = 0
    if configured > 0:
        return configured
    local = int(getattr(getattr(config, "local", None), "context_length", 0) or 0)
    return max(local, 64000)


def max_output_tokens() -> int:
    try:
        return max(256, int(_setting("lean_max_output_tokens", 8192)))
    except (TypeError, ValueError):
        return 8192


def approval_mode() -> str:
    """smart (default): approve destructive/external only; always; never."""
    mode = str(_setting("lean_approval_mode", "smart") or "smart").strip().lower()
    return mode if mode in {"smart", "always", "never"} else "smart"


def approval_timeout_seconds() -> float:
    try:
        return max(15.0, float(_setting("lean_approval_timeout_seconds", 600)))
    except (TypeError, ValueError):
        return 600.0


def request_timeout_seconds() -> float:
    try:
        return max(30.0, float(_setting("lean_request_timeout_seconds", 600)))
    except (TypeError, ValueError):
        return 600.0


def group_fan_out() -> bool:
    """@all or several @mentions: answer at the same time, independently."""
    return _as_bool(_setting("lean_group_fan_out", True))


def group_merge() -> bool:
    """After a fan-out, the room's lead writes a short merged reply."""
    return _as_bool(_setting("lean_group_merge", True))


def group_max_rounds() -> int:
    """Backstop: rounds of work per message in a group chat or handed-off job."""
    try:
        return max(1, min(int(_setting("lean_group_max_rounds", 8)), 30))
    except (TypeError, ValueError):
        return 8


def group_token_budget() -> int:
    """Backstop: model tokens one group-chat message may use (0 = no limit)."""
    try:
        return max(0, int(_setting("lean_group_token_budget", 200_000)))
    except (TypeError, ValueError):
        return 200_000


def learning_mode() -> str:
    """on: record experience and use lessons; off: neither.

    control (evaluation only): record nothing, and put same-size unrelated notes
    where lessons would go, so a measured gain can't come from a longer prompt.
    """
    if not _as_bool(_setting("learning_enabled", True)):
        return "off"
    mode = str(_setting("learning_mode", "on") or "on").strip().lower()
    return mode if mode in {"on", "off", "control"} else "on"


def learning_enabled() -> bool:
    return learning_mode() == "on"


def reflection_daily_cap() -> int:
    """Background reflections per day across all agents; each is one model call."""
    try:
        return max(0, min(int(_setting("learning_reflection_daily_cap", 30)), 500))
    except (TypeError, ValueError):
        return 30


def playbook_size() -> int:
    """Lessons an agent reads before a task."""
    try:
        return max(0, min(int(_setting("learning_playbook_size", 3)), 8))
    except (TypeError, ValueError):
        return 3
