"""How tools and search providers have been doing lately. Counted in code, no model.

Two uses:
- A short note in the prompt when a tool the agent has keeps failing, so it
  tries another way instead of repeating the same call.
- Web search in "auto" mode tries a provider that has failed its last three
  searches within the hour after the ones that work. Nothing is removed: it is
  still tried as a fallback, and moves back up as soon as it works again.

Only counts and rates leave this module. Error text never reaches a prompt,
since a failing tool's output can be anything (a web page, an injected string).
"""

from __future__ import annotations

import time
from typing import Iterable

from loguru import logger

from agent.learning.store import Episode, get_experience_store

TOOL = "tool"
SEARCH_PROVIDER = "search_provider"
# A tool earns a prompt note after this many recent calls with this failure share.
NOTE_MIN_CALLS = 5
NOTE_FAILURE_SHARE = 0.6
# A search provider is tried last after this many failures in a row, for this long.
DEMOTE_AFTER = 3
DEMOTE_FOR_SECONDS = 3600


def record_episode(episode: Episode) -> None:
    store = get_experience_store()
    for tool in episode.tools:
        if not tool.get("not_run") and tool.get("name"):
            store.record_outcome(TOOL, str(tool["name"]), bool(tool.get("ok")), at=episode.created_at)


def record_search(provider: str, ok: bool) -> None:
    """One search attempt by one provider. Never raises: search must not depend on learning."""
    try:
        from agent.lean import settings

        if settings.learning_enabled():
            get_experience_store().record_outcome(SEARCH_PROVIDER, provider, ok)
    except Exception:
        logger.debug("Search reliability record failed", exc_info=True)


def provider_order(order: list[str]) -> list[str]:
    """``order`` with providers that are failing right now moved to the end."""
    try:
        from agent.lean import settings

        if not settings.learning_enabled():
            return list(order)
        rows = {r["name"]: r for r in get_experience_store().reliability(SEARCH_PROVIDER)}
    except Exception:
        logger.debug("Search reliability read failed", exc_info=True)
        return list(order)
    now = time.time()

    def failing(name: str) -> bool:
        row = rows.get(name)
        if not row:
            return False
        recent = str(row["recent"])[-DEMOTE_AFTER:]
        return (len(recent) == DEMOTE_AFTER and "1" not in recent
                and now - float(row["last_failure_at"] or 0) < DEMOTE_FOR_SECONDS)

    return [p for p in order if not failing(p)] + [p for p in order if failing(p)]


def tool_notes(names: Iterable[str], limit: int = 3) -> list[str]:
    """Prompt notes for tools this agent has that keep failing lately."""
    wanted = set(names)
    notes: list[tuple[float, str]] = []
    for row in get_experience_store().reliability(TOOL):
        if row["name"] not in wanted:
            continue
        recent = str(row["recent"])[-10:]
        failures = recent.count("0")
        if len(recent) >= NOTE_MIN_CALLS and failures / len(recent) >= NOTE_FAILURE_SHARE:
            notes.append((failures / len(recent), (
                f"{row['name']} failed {failures} of its last {len(recent)} calls. If it fails again, "
                "read the error and try a different approach or tool instead of repeating the same call."
            )))
    notes.sort(key=lambda item: -item[0])
    return [text for _, text in notes[:limit]]


def summary() -> dict[str, list[dict[str, object]]]:
    """For the Learning page: every tool and provider with lifetime and recent counts."""
    out: dict[str, list[dict[str, object]]] = {TOOL: [], SEARCH_PROVIDER: []}
    for row in get_experience_store().reliability():
        recent = str(row["recent"])
        out.setdefault(row["kind"], []).append({
            "name": row["name"],
            "ok": int(row["ok"]),
            "failed": int(row["failed"]),
            "recent_ok": recent.count("1"),
            "recent_failed": recent.count("0"),
            "last_failure_at": float(row["last_failure_at"] or 0),
            "updated_at": float(row["updated_at"] or 0),
        })
    return out
