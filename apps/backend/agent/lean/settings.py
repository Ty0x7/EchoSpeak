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


def context_tokens(endpoint: Any = None) -> int:
    """Honor exact model metadata/local configuration before a fallback budget.

    A global trim budget may lower a known window, never enlarge it. Unknown
    cloud windows retain the existing fallback, not an invented model limit.
    """
    try:
        configured = int(_setting("lean_context_tokens", 0) or 0)
    except (TypeError, ValueError):
        configured = 0
    if endpoint is not None:
        registry = dict(getattr(config, "model_capability_profiles", {}) or {})
        meta = registry.get(f"{endpoint.provider}:{endpoint.model}") or registry.get(endpoint.model) or {}
        known = int(meta.get("context_limit") or 0)
        if not known and endpoint.local:
            known = int(getattr(config.local, "context_length", 0) or 0)
        if known > 0:
            return max(512, min(known, configured) if configured > 0 else known)
    if configured > 0:
        return configured
    local = int(getattr(getattr(config, "local", None), "context_length", 0) or 0)
    return max(local, 64000)


def max_output_tokens(endpoint: Any = None) -> int:
    try:
        budget = max(256, int(_setting("lean_max_output_tokens", 8192)))
    except (TypeError, ValueError):
        budget = 8192
    return min(budget, max(256, context_tokens(endpoint) // 2)) if endpoint is not None else budget


def approval_mode() -> str:
    """smart (default): approve destructive/external only; always; never.

    autonomous: like smart, but for walking away. Classes marked autonomous in
    approvals.scope_for (desktop control, non-dangerous terminal asks) are approved
    automatically; every other ask is held for the owner without waiting, so the run
    keeps going and reports what still needs them. Disabled permissions, Blocked apps,
    privacy modes, project scope, the Rule of Two and Stop are unchanged.
    """
    mode = str(_setting("lean_approval_mode", "smart") or "smart").strip().lower()
    return mode if mode in {"smart", "always", "never", "autonomous"} else "smart"


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


def research_swarm() -> bool:
    """Offer spawn_workers (agent/lean/swarm.py). Off until the bench shows a gain for your models."""
    return _as_bool(_setting("lean_research_swarm", False))


def _int(name: str, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(_setting(name, default))))
    except (TypeError, ValueError):
        return default


def swarm_token_budget() -> int:
    """New tokens all of one request's workers may use together (their prompts plus replies)."""
    return _int("lean_swarm_token_budget", 120_000, 10_000, 2_000_000)


def swarm_worker_steps() -> int:
    return _int("lean_swarm_worker_steps", 12, 3, 40)


def swarm_one_at_a_time() -> bool:
    """Settings › Agent: run research workers one after another, for PCs short on memory or GPU."""
    return _as_bool(_setting("lean_swarm_one_at_a_time", False))


def swarm_parallel(local: bool) -> int:
    """Workers at once: all of them (up to 6) unless the owner chose one at a time.

    A local server that takes one request at a time (LM Studio with one parallel slot,
    or an older build) simply queues them; it is never slower than one at a time.
    """
    if swarm_one_at_a_time():
        return 1
    return _int("lean_swarm_local_parallel", 6, 1, 6) if local else _int("lean_swarm_cloud_parallel", 6, 1, 6)


def group_max_rounds() -> int:
    """Backstop: rounds of work per message in a group chat or handed-off job."""
    try:
        return max(1, min(int(_setting("lean_group_max_rounds", 8)), 30))
    except (TypeError, ValueError):
        return 8


def group_token_budget() -> int:
    """Optional cap on new model tokens one group-chat message may use. 0 (the default) means
    none: a team works until the job is done, stuck (no progress, repeating) or at the round
    ceiling. Set lean_group_token_budget to cap spend on paid cloud models."""
    try:
        return max(0, int(_setting("lean_group_token_budget", 0)))
    except (TypeError, ValueError):
        return 0


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
