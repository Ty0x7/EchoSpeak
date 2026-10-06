"""Competence profiles: what each agent is good and bad at, computed from graded episodes.

An agent never describes its own strengths; these numbers come from what its
work actually did (agent/learning/episodes.py). Routing and delegation read a
one-line track record so work goes to whoever has done that kind of task well.
The soul (voice, values, rules) stays the owner's: nothing here changes it.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from agent.learning.store import Episode, get_experience_store

WINDOW_DAYS = 90
# A kind needs this many decided tasks before it says anything about the agent.
MIN_DECIDED = 3
STRONG = 0.7
WEAK = 0.4
_CACHE_SECONDS = 60.0
_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_cache_lock = threading.Lock()


def _verdict(episode: Episode) -> str:
    """win | loss | '' (answered, or nothing to judge)."""
    if episode.feedback < 0 or episode.outcome in {"failure", "stopped"}:
        return "loss"
    if episode.outcome == "success":
        return "win"
    return "win" if episode.feedback > 0 else ""


def profile(agent_id: str) -> dict[str, Any]:
    now = time.time()
    with _cache_lock:
        hit = _cache.get(agent_id)
        if hit and now - hit[0] < _CACHE_SECONDS:
            return hit[1]
    episodes = get_experience_store().episodes(agent_id=agent_id, since=now - WINDOW_DAYS * 86400, limit=2000)
    kinds: dict[str, dict[str, int]] = {}
    false_success = 0
    for ep in episodes:
        row = kinds.setdefault(ep.task_kind, {"wins": 0, "losses": 0, "checked": 0, "answered": 0, "confirmed": 0})
        verdict = _verdict(ep)
        if verdict == "win":
            row["wins"] += 1
        elif verdict == "loss":
            row["losses"] += 1
        else:
            row["answered"] += 1
        if ep.verified_success:
            row["checked"] += 1
        if ep.feedback > 0:
            row["confirmed"] += 1
        # Said it was done when it wasn't: a false claim caught in the turn, or the owner said it didn't work.
        if "unverified_claim" in ep.stop_reason or "promise_unfulfilled" in ep.stop_reason \
                or (ep.feedback < 0 and ep.outcome == "success"):
            false_success += 1
    strengths, weaknesses = [], []
    for kind, row in kinds.items():
        decided = row["wins"] + row["losses"]
        row["decided"] = decided
        row["rate"] = round(row["wins"] / decided, 3) if decided else 0.0
        if decided >= MIN_DECIDED:
            (strengths if row["rate"] >= STRONG else weaknesses if row["rate"] <= WEAK else []).append(kind)
    data = {
        "agent_id": agent_id,
        "episodes": len(episodes),
        "kinds": kinds,
        "strengths": sorted(strengths, key=lambda k: -kinds[k]["decided"]),
        "weaknesses": sorted(weaknesses, key=lambda k: -kinds[k]["decided"]),
        "false_success": false_success,
        "window_days": WINDOW_DAYS,
    }
    with _cache_lock:
        _cache[agent_id] = (now, data)
    return data


def track_record(agent_id: str) -> str:
    """'track record: research 8/9 done, coding 1/4 done', or '' until there is enough history."""
    data = profile(agent_id)
    shown = [(kind, row) for kind, row in data["kinds"].items() if row.get("decided", 0) >= MIN_DECIDED]
    if not shown:
        return ""
    shown.sort(key=lambda item: -item[1]["decided"])
    parts = [f"{kind.replace('_', ' ')} {row['wins']}/{row['decided']} done" for kind, row in shown[:3]]
    return "track record: " + ", ".join(parts)


def forget_cache() -> None:
    with _cache_lock:
        _cache.clear()
