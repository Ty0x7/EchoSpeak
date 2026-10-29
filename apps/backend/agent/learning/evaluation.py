"""Is the agent getting better? Measured from graded episodes and the lessons' control groups.

Learning is only worth keeping if it shows up in outcomes, so these numbers come from what ran
(agent/learning/episodes.py and the verification ledger), never from what an agent says:

- **Verified completion:** measured engineering work that ended verified or with passing checks.
- **False success:** replies that said the work was done when the evidence, or the owner, said otherwise.
- **Recovery:** work that started from a failing check and ended passing.
- **Stalls:** work that stopped improving on its best result.
- **Blockers:** environment, authorization and the like, which aren't the agent's failures and
  aren't counted against it.
- **Lessons against their holdouts:** for each lesson, its checked win rate when used against the
  rate on matching tasks where it was held back at random. A lesson that does worse than its
  holdout is negative transfer. Comparing with the holdout also separates a lesson that helped
  from success that would have happened anyway.

Read-only: nothing here changes a lesson or an episode.
"""

from __future__ import annotations

import time
from typing import Any, Iterable

from agent.learning.playbook import HARM_MARGIN, MIN_HELD
from agent.learning.store import NOT_ATTRIBUTABLE, Episode, Lesson, get_experience_store


def _rate(part: int, whole: int) -> float | None:
    return round(part / whole, 3) if whole else None


def _mean(values: Iterable[float]) -> float | None:
    rows = [float(v) for v in values if v]
    return round(sum(rows) / len(rows), 1) if rows else None


def summarize(episodes: Iterable[Episode]) -> dict[str, Any]:
    """Outcome rates for a set of episodes (research workers' subtasks left out)."""
    own = [ep for ep in episodes if ep.source != "swarm"]
    measured = [ep for ep in own if ep.verification]
    statuses = [str(ep.verification.get("status") or "") for ep in measured]
    verified = sum(1 for s in statuses if s in {"verified", "checks_pass"})
    started_failing = [ep for ep in measured if ep.verification.get("started_failing") or ep.verification.get("recovered")]
    recovered = sum(1 for ep in measured if ep.verification.get("recovered"))
    claimed = [ep for ep in own if ep.outcome == "success" or ep.effective_cause == "false_success"]
    false_success = sum(1 for ep in claimed if ep.effective_cause in {"false_success", "verifier_gap"}
                        or (ep.feedback < 0 and ep.outcome == "success" and ep.effective_cause not in NOT_ATTRIBUTABLE))
    stalled = sum(1 for ep in measured if ep.verification.get("bottleneck") == "implementation"
                  or ep.effective_cause == "stalled")
    causes: dict[str, int] = {}
    for ep in own:
        if ep.effective_cause:
            causes[ep.effective_cause] = causes.get(ep.effective_cause, 0) + 1
    blockers: dict[str, int] = {}
    for ep in measured:
        kind = str(ep.verification.get("bottleneck") or "")
        if kind:
            blockers[kind] = blockers.get(kind, 0) + 1
    attempts = [int(ep.verification.get("attempts") or 0) for ep in measured
                if ep.verification.get("status") in {"verified", "checks_pass"}]
    return {
        "episodes": len(own),
        "measured": len(measured),
        "verified_completion": _rate(verified, len(measured)),
        "false_success": _rate(false_success, len(claimed)),
        "recovery": _rate(recovered, len(started_failing)),
        "stall": _rate(stalled, len(measured)),
        "attempts_to_verified": round(sum(attempts) / len(attempts), 2) if attempts else None,
        # What it costs: new tokens and wall time per measured task, and per verified one.
        "tokens_per_task": _mean(ep.tokens for ep in measured),
        "tokens_per_verified": _mean(ep.tokens for ep in measured if ep.verification.get("status") in {"verified", "checks_pass"}),
        "seconds_per_task": _mean(ep.duration_s for ep in measured),
        "statuses": {s: statuses.count(s) for s in dict.fromkeys(statuses) if s},
        "causes": causes,
        "blockers": blockers,
        "not_attributable": sum(1 for ep in own if ep.effective_cause in NOT_ATTRIBUTABLE),
    }


def lesson_effect(lesson: Lesson) -> dict[str, Any]:
    """A lesson's checked win rate with it and without it (its random holdout), and the verdict."""
    used = lesson.wins + lesson.losses
    held = lesson.held_wins + lesson.held_losses
    rate, held_rate = _rate(lesson.wins, used), _rate(lesson.held_wins, held)
    if held < MIN_HELD or rate is None or held_rate is None:
        verdict = "not enough holdout data"
    elif rate + HARM_MARGIN < held_rate:
        verdict = "negative transfer"
    elif rate > held_rate:
        verdict = "helps"
    else:
        verdict = "no measurable effect"
    return {"id": lesson.id, "agent_id": lesson.agent_id, "title": lesson.title, "status": lesson.status,
            "with": {"wins": lesson.wins, "decided": used, "rate": rate},
            "without": {"wins": lesson.held_wins, "decided": held, "rate": held_rate},
            "effect": round(rate - held_rate, 3) if rate is not None and held_rate is not None else None,
            "verdict": verdict}


def transfer_effect(lesson: Lesson) -> dict[str, Any]:
    """How a proven lesson does in other agents' work, beside its home record."""
    home, away = lesson.wins + lesson.losses, lesson.shared_wins + lesson.shared_losses
    return {"id": lesson.id, "agent_id": lesson.agent_id, "title": lesson.title, "transfer": lesson.transfer,
            "home": {"wins": lesson.wins, "decided": home, "rate": _rate(lesson.wins, home)},
            "elsewhere": {"uses": lesson.shared_uses, "wins": lesson.shared_wins, "decided": away,
                          "rate": _rate(lesson.shared_wins, away)}}


def with_and_without_lessons(episodes: Iterable[Episode]) -> dict[str, Any]:
    """Checked success on tasks that read lessons against matching tasks whose lessons were all held back."""
    rows = {"with": [0, 0], "held_back": [0, 0]}
    for ep in episodes:
        if ep.source == "swarm" or ep.effective_cause in NOT_ATTRIBUTABLE:
            continue
        group = "with" if ep.lessons_used else "held_back" if ep.lessons_withheld else ""
        if not group or not (ep.verified_success or ep.failed):
            continue
        rows[group][0] += int(ep.verified_success)
        rows[group][1] += 1
    return {group: {"wins": wins, "decided": decided, "rate": _rate(wins, decided)} for group, (wins, decided) in rows.items()}


def report(agent_id: str = "", days: int = 30) -> dict[str, Any]:
    store = get_experience_store()
    since = time.time() - max(1, int(days)) * 86400
    episodes = store.episodes(agent_id=agent_id, since=since, limit=2000)
    lessons = store.lessons(agent_id=agent_id)
    effects = [lesson_effect(lesson) for lesson in lessons if lesson.uses or lesson.held_wins + lesson.held_losses]
    environments = sorted({ep.environment for ep in episodes if ep.environment})
    return {
        "agent_id": agent_id,
        "days": days,
        **summarize(episodes),
        # One learning system, measured where the work happened: Chat, Agents and Code.
        "by_environment": {env: summarize([ep for ep in episodes if ep.environment == env]) for env in environments},
        "transfer": [transfer_effect(lesson) for lesson in lessons if lesson.shared_uses],
        "lessons": with_and_without_lessons(episodes),
        "lesson_effects": sorted(effects, key=lambda row: (row["verdict"] != "negative transfer", row["title"])),
    }
