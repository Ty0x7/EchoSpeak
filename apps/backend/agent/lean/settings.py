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
    """The lean loop is the default. Set ECHOSPEAK_LEAN_RUNTIME=0 to fall back.

    The legacy regression suite (ECHOSPEAK_TESTING=1) keeps exercising the
    legacy runtime unless a test opts in explicitly.
    """
    if os.getenv("ECHOSPEAK_LEAN_RUNTIME") is None and _as_bool(os.getenv("ECHOSPEAK_TESTING", "0")):
        return False
    return _as_bool(_setting("echospeak_lean_runtime", True))


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
