"""Learning from verified experience (EchoSpeak 11.0).

Agents get better at their work without retraining the model and without
rewriting their own rules. It runs on its own as a consequence of normal work in
Chat, Agents and Code (one system, in the shared Lean harness), with whichever model
the user picked: nothing has to be approved for routine learning to happen.
The loop, end to end:

    finished request -> episodes.py   grade what each agent actually did (V0..V4), spot tampering
                     -> reliability.py count how each tool and search provider is doing
                     -> profiles.py    per-agent track record by task kind, for routing
                     -> reflector.py   (background, agent's own model) propose lessons
                     -> curator.py     code decides what is kept, and what waits for the owner
                     -> playbook.py    lessons read before similar tasks; promoted or retired by results
    owner            -> feedback here  Worked / Didn't work (V4), corrects the counts;
                                       "that worked" / "that's wrong" in the next message counts too (implicit)

What learning can write: advisory lesson text and statistics, in its own
database (store.py). What it can never write: permissions, approvals,
toolsets, policy, verifiers, tests, settings or code. A lesson can suggest an
approach; it can't grant anything, and the safety checks in agent/lean/policy.py
never read it.

Only the owner's own requests teach: guests, public channels and inbound A2A
calls are never recorded. Requests that read outside content are recorded as
untrusted, and anything learned from them waits for the owner's review.

Every entry point here catches its own errors: a learning failure must never
break or slow a chat.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from loguru import logger

from agent.learning.store import (
    ACTIVE_STATUSES,
    LESSON_STATUSES,
    Episode,
    ExperienceStore,
    Lesson,
    get_experience_store,
    set_experience_store,
)

__all__ = [
    "ACTIVE_STATUSES", "LESSON_STATUSES", "Episode", "ExperienceStore", "Lesson", "TurnLearning",
    "get_experience_store", "set_experience_store", "record_request", "prepare_turn", "track_record",
    "record_feedback", "implicit_feedback", "worker_tick", "lesson_action", "edit_lesson", "rollback_lesson",
    "delete_lesson", "set_paused", "status", "reset",
]

_MAINTENANCE_EVERY = 3600.0
_last_maintenance = {"at": 0.0}
_maintenance_lock = threading.Lock()


@dataclass
class TurnLearning:
    """What learning adds to one agent's turn."""
    section: str = ""  # the "Lessons from your past work" prompt section
    lesson_ids: list[str] = field(default_factory=list)
    withheld_ids: list[str] = field(default_factory=list)  # matched but held back: the control group
    shared_ids: list[str] = field(default_factory=list)  # proven lessons from another agent (transfer)
    shared_withheld_ids: list[str] = field(default_factory=list)  # transferable but held back: transfer's control
    chars: int = 0  # prompt characters the lessons cost
    notes: list[str] = field(default_factory=list)  # tool reliability notes


def _mode() -> str:
    from agent.lean import settings

    return settings.learning_mode()


# ── during a request ────────────────────────────────────────────────────

def prepare_turn(agent_id: str, goal: str, tool_names: Iterable[str], *, limit: Optional[int] = None,
                 project_id: str = "") -> TurnLearning:
    """Lessons and tool notes for an agent about to work on ``goal``.

    ``limit`` lowers the number of lessons (Chat on a small model); ``project_id`` scopes project lessons.
    """
    try:
        from agent.lean import settings
        from agent.learning import playbook, reliability

        mode = _mode()
        if mode == "off" or agent_id in get_experience_store().paused_agents():
            return TurnLearning()
        if not playbook.worth_lessons(goal):
            # Small talk and one-line requests: lessons would only cost context (ACE: not every task needs one).
            return TurnLearning(notes=reliability.tool_notes(tool_names) if mode == "on" else [])
        size = settings.playbook_size() if limit is None else max(0, min(limit, settings.playbook_size()))
        budget = playbook.COMPACT_CHARS if limit is not None and limit <= 2 else playbook.FULL_CHARS
        lessons = playbook.fit(playbook.select(agent_id, goal, size, project_id=project_id), budget)
        room = budget - sum(playbook.cost(l) for l in lessons)
        shared = (playbook.fit(playbook.transferable(agent_id, goal, project_id, taken=lessons), room)
                  if size and len(lessons) < size else [])
        if mode == "control":
            section = playbook.control_section(lessons + shared)
            return TurnLearning(section=section, chars=len(section))
        used, withheld = playbook.holdout(lessons)
        shared_used, shared_withheld = playbook.holdout(shared, rate=playbook.HOLDOUT_SHARED)
        section = playbook.section(used, shared_used)
        return TurnLearning(section=section, lesson_ids=[l.id for l in used],
                            withheld_ids=[l.id for l in withheld], shared_ids=[l.id for l in shared_used],
                            shared_withheld_ids=[l.id for l in shared_withheld], chars=len(section),
                            notes=reliability.tool_notes(tool_names))
    except Exception:
        logger.warning("Learning: could not prepare lessons", exc_info=True)
        return TurnLearning()


def track_record(agent_id: str) -> str:
    """One line for routing and delegation, or '' (learning off, paused, or too little history)."""
    try:
        if _mode() != "on" or agent_id in get_experience_store().paused_agents():
            return ""
        from agent.learning.profiles import track_record as record

        return record(agent_id)
    except Exception:
        logger.debug("Learning: track record failed", exc_info=True)
        return ""


# ── after a request ─────────────────────────────────────────────────────

def record_request(
    *,
    goal: str,
    results: list[Any],
    job: Any,
    taint: list[str],
    session_id: str,
    execution_id: str,
    source: str,
    caller_role: str,
    names: dict[str, str],
    endpoints: dict[str, tuple[str, str]],
    lessons_used: dict[str, list[str]],
    team: bool,
    cancelled: bool,
    lead: str = "",
    routed: Optional[dict[str, str]] = None,
    lessons_withheld: Optional[dict[str, list[str]]] = None,
    lessons_shared: Optional[dict[str, list[str]]] = None,
    environment: str = "",
    project_id: str = "",
    lessons_shared_withheld: Optional[dict[str, list[str]]] = None,
    lesson_chars: Optional[dict[str, int]] = None,
) -> list[Episode]:
    """Grade and keep a finished request. Only the owner's, never a cancelled one.

    In "control" mode (evaluation) episodes are still recorded, tagged, so learning-on and learning-off
    periods can be compared; nothing is credited to lessons and nothing is reflected on.
    """
    try:
        mode = _mode()
        if mode == "off" or caller_role != "owner" or cancelled or not results:
            return []
        from agent.learning import playbook, profiles, reflector, reliability
        from agent.learning.episodes import build_episodes

        store = get_experience_store()
        paused = store.paused_agents()
        kept: list[Episode] = []
        for episode in build_episodes(goal=goal, results=results, job=job, taint=taint, session_id=session_id,
                                      execution_id=execution_id, source=source, names=names, endpoints=endpoints,
                                      lessons_used=lessons_used, team=team,
                                      lead=lead, routed=routed, lessons_withheld=lessons_withheld):
            if episode.agent_id in paused:
                continue
            episode.environment, episode.project_id, episode.learning_mode = environment, project_id, mode
            episode.lessons_shared = list(dict.fromkeys((lessons_shared or {}).get(episode.agent_id, [])))
            episode.lessons_shared_withheld = [i for i in dict.fromkeys((lessons_shared_withheld or {}).get(episode.agent_id, []))
                                               if i not in episode.lessons_shared]
            episode.lesson_chars = int((lesson_chars or {}).get(episode.agent_id, 0))
            if mode == "control":
                episode.lessons_used, episode.lessons_withheld, episode.lessons_shared = [], [], []
                episode.lessons_shared_withheld = []
                store.add_episode(episode)
                kept.append(episode)
                continue
            playbook.attribute(episode)
            store.add_episode(episode)
            reliability.record_episode(episode)
            if not reflector.skip_reason(episode):
                store.queue_reflection(episode.id, episode.agent_id, reflector.priority(episode))
            kept.append(episode)
        profiles.forget_cache()
        from agent.learning import routing
        routing.forget_profiles()
        return kept
    except Exception:
        logger.warning("Learning: could not record the request", exc_info=True)
        return []


def record_feedback(execution_id: str, value: int, note: str = "", agent_id: str = "",
                    source: str = "owner") -> list[Episode]:
    """The owner's Worked (+1) / Didn't work (-1) / cleared (0) on a reply.

    ``source`` "implicit" is the same signal read from the owner's next message; it never overrides
    what the owner said with the buttons.
    """
    from agent.lean.policy import redact_secrets
    from agent.learning import playbook, profiles, reflector
    from agent.learning.episodes import feedback_cause

    value = max(-1, min(1, int(value)))
    store = get_experience_store()
    changed: list[Episode] = []
    for episode in store.episodes(execution_id=execution_id, limit=20):
        if agent_id and episode.agent_id != agent_id:
            continue
        if source == "implicit" and (episode.feedback_source == "owner" or episode.outcome == "error"):
            continue
        previous = episode.attribution or "none"
        episode.feedback = value
        episode.feedback_note = redact_secrets(" ".join(str(note or "").split()))[:500] if value else ""
        # Reclassifies the episode (a misunderstanding, an environment problem, checks that missed something);
        # the note itself only reaches a reflection, whose lessons still have to prove themselves.
        episode.feedback_source = source if value else ""
        # A casual "thanks, that worked" is recorded but proves nothing: the evidence keeps deciding.
        weak = source == "implicit" and value > 0
        episode.feedback_cause = "" if weak else feedback_cause(episode, value, episode.feedback_note)
        playbook.attribute(episode, previous=previous)
        store.update_episode(episode)
        if value and not reflector.skip_reason(episode):
            store.queue_reflection(episode.id, episode.agent_id, reflector.priority(episode))
        changed.append(episode)
    profiles.forget_cache()
    from agent.learning import routing
    routing.forget_profiles()
    return changed


IMPLICIT_WINDOW_SECONDS = 6 * 3600


def implicit_feedback(session_id: str, message: str) -> list[Episode]:
    """Read the owner's next message as feedback on the reply before it ("that worked", "that's wrong").

    No model call: a short list of unambiguous phrases (episodes.implicit_signal). Applied only to the most
    recent request in this conversation, within a few hours, and never over the owner's own button.
    """
    try:
        if _mode() != "on" or not session_id:
            return []
        from agent.learning.episodes import implicit_signal

        value = implicit_signal(message)
        if not value:
            return []
        recent = get_experience_store().episodes(session_id=session_id, since=time.time() - IMPLICIT_WINDOW_SECONDS,
                                                 limit=10, role="own")
        if not recent or not recent[0].execution_id:
            return []
        latest = [ep for ep in recent if ep.execution_id == recent[0].execution_id]
        # The reply the user is answering: the lead's (or the only agent's), never every agent in a group.
        lead = next((ep for ep in latest if ep.role == "lead"), None) or (latest[0] if len({ep.agent_id for ep in latest}) == 1 else None)
        if lead is None:
            return []
        return record_feedback(lead.execution_id, value, " ".join(str(message).split())[:300],
                               agent_id=lead.agent_id, source="implicit")
    except Exception:
        logger.debug("Learning: implicit feedback failed", exc_info=True)
        return []


def reset() -> None:
    """Forget everything learned (the owner's Reset in diagnostics). Memory is a separate store, untouched."""
    get_experience_store().reset()
    from agent.learning import routing
    from agent.learning.profiles import forget_cache

    forget_cache()
    routing.forget_profiles()


def worker_tick(*, client_factory: Any = None) -> list[dict[str, Any]]:
    """One pass of the background worker: reflect when no chat is running; hourly upkeep."""
    try:
        from agent.lean.runtime import is_busy
        from agent.learning import playbook, reflector

        if _mode() != "on":
            return []
        with _maintenance_lock:
            due = time.time() - _last_maintenance["at"] > _MAINTENANCE_EVERY
            if due:
                _last_maintenance["at"] = time.time()
        if due:
            playbook.retire_stale()
            playbook.consolidate()  # twins admitted apart are merged (deterministic, no model call)
            get_experience_store().prune()
        if is_busy():
            return []  # the model is serving a chat; reflection waits
        return reflector.run_pending(limit=3, client_factory=client_factory)
    except Exception:
        logger.warning("Learning worker tick failed", exc_info=True)
        return []


# ── the owner's controls ────────────────────────────────────────────────

_ACTIONS = {
    # action: (allowed from, new status, note)
    "approve": ({"pending_review"}, "probation", "approved by you: unproven until it helps in checked tasks"),
    "reject": ({"pending_review", "probation", "established"}, "quarantined", "rejected by you: never used"),
    "promote": ({"probation", "pending_review"}, "established", "marked proven by you"),
    "retire": ({"probation", "established", "pending_review"}, "retired", "retired by you"),
    "restore": ({"retired", "quarantined"}, "probation", "restored by you: unproven again"),
}


def lesson_action(lesson_id: str, action: str) -> Lesson:
    store = get_experience_store()
    lesson = store.get_lesson(lesson_id)
    if lesson is None:
        raise KeyError(lesson_id)
    allowed, status, note = _ACTIONS[action]
    if lesson.status not in allowed:
        raise ValueError(f"can't {action} a lesson that is {lesson.status.replace('_', ' ')}")
    before = Lesson.from_dict(lesson.to_dict())
    lesson.status, lesson.note = status, note
    if action in {"approve", "promote"}:
        lesson.trusted = True  # the owner vouched for it
    return store.update_lesson(lesson, actor="owner", action=action, reason=note, before=before)


def edit_lesson(lesson_id: str, *, title: Optional[str] = None, text: Optional[str] = None) -> Lesson:
    """The owner's wording. Held to the same content rules as learned lessons."""
    from agent.learning.curator import vet

    store = get_experience_store()
    lesson = store.get_lesson(lesson_id)
    if lesson is None:
        raise KeyError(lesson_id)
    new_title = " ".join(str(title if title is not None else lesson.title).split())
    new_text = " ".join(str(text if text is not None else lesson.text).split())
    problem = vet(new_title, new_text)
    if problem:
        raise ValueError(f"not saved: {problem}")
    before = Lesson.from_dict(lesson.to_dict())
    lesson.title, lesson.text, lesson.edited = new_title, new_text, True
    return store.update_lesson(lesson, actor="owner", action="edit", reason="edited by you", before=before)


def rollback_lesson(lesson_id: str, event_id: int) -> Optional[Lesson]:
    """Put a lesson back the way it was just before ``event_id``. A creation rolls back to retired."""
    store = get_experience_store()
    event = store.lesson_event(event_id)
    if event is None or event["lesson_id"] != lesson_id:
        raise KeyError(f"{lesson_id}:{event_id}")
    current = store.get_lesson(lesson_id)
    snapshot = event["before"]
    if snapshot is None:
        if current is None:
            return None
        before = Lesson.from_dict(current.to_dict())
        current.status, current.note = "retired", "rolled back to before it was learned"
        return store.update_lesson(current, actor="owner", action="rollback", reason=f"to before event {event_id}",
                                   before=before)
    restored = Lesson.from_dict(snapshot)
    restored.note = f"rolled back to before: {event['action']}"
    if current is None:
        return store.add_lesson(restored, actor="owner", reason=f"restored from event {event_id}")
    return store.update_lesson(restored, actor="owner", action="rollback", reason=f"to before event {event_id}",
                               before=current)


def delete_lesson(lesson_id: str) -> bool:
    return get_experience_store().delete_lesson(lesson_id, actor="owner", reason="deleted by you")


def set_paused(agent_id: str, paused: bool) -> None:
    get_experience_store().set_paused(agent_id, paused)
    from agent.learning.profiles import forget_cache

    forget_cache()


def status() -> dict[str, Any]:
    from agent.lean import settings
    from agent.learning.reflector import _start_of_today

    store = get_experience_store()
    return {
        "mode": settings.learning_mode(),
        "enabled": settings.learning_enabled(),
        "reflection_daily_cap": settings.reflection_daily_cap(),
        "reflections_today": store.reflections_since(_start_of_today()),
        "playbook_size": settings.playbook_size(),
        "paused_agents": sorted(store.paused_agents()),
        **store.counts(),
    }
