"""HTTP routes for learning (agent/learning): feedback on replies, and the owner's review page.

Everything here reads or changes learning's own records: lessons, episodes,
statistics and per-agent pauses. Nothing reaches permissions, approvals,
toolsets or settings; those keep their own routes and checks.
"""

from __future__ import annotations

import threading
from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from agent import learning
from agent.lean.personas import get_persona_store

router = APIRouter(prefix="/lean", tags=["learning"])


class FeedbackPayload(BaseModel):
    execution_id: str = Field(min_length=1, max_length=200)
    # 1 = worked, -1 = didn't work, 0 = take it back
    value: int = Field(ge=-1, le=1)
    note: str = Field(default="", max_length=1000)
    agent_id: str = Field(default="", max_length=120)


class LessonActionPayload(BaseModel):
    action: Literal["approve", "reject", "promote", "retire", "restore"]


class LessonEditPayload(BaseModel):
    title: Optional[str] = Field(default=None, max_length=200)
    text: Optional[str] = Field(default=None, max_length=1000)


class RollbackPayload(BaseModel):
    event_id: int


class PausePayload(BaseModel):
    paused: bool


def _names() -> dict[str, str]:
    return {persona.id: persona.name for persona in get_persona_store().list()}


@router.post("/feedback")
def post_feedback(payload: FeedbackPayload) -> dict[str, Any]:
    """Worked / Didn't work under a reply. The owner's word is the strongest evidence (V4)."""
    episodes = learning.record_feedback(payload.execution_id, payload.value, payload.note, payload.agent_id)
    return {"ok": True, "value": payload.value, "episodes": len(episodes)}


@router.get("/learning/status")
def learning_status() -> dict[str, Any]:
    return learning.status()


@router.get("/learning/profiles")
def learning_profiles() -> dict[str, Any]:
    from agent.learning.profiles import profile

    store = learning.get_experience_store()
    paused = store.paused_agents()
    rows = []
    for persona in get_persona_store().list():
        data = profile(persona.id)
        active = store.lessons(agent_id=persona.id, statuses=learning.ACTIVE_STATUSES)
        rows.append({
            **data,
            "name": persona.name,
            "title": persona.title,
            "paused": persona.id in paused,
            "track_record": learning.track_record(persona.id),
            "lessons_proven": sum(1 for lesson in active if lesson.status == "established"),
            "lessons_unproven": sum(1 for lesson in active if lesson.status == "probation"),
        })
    return {"profiles": rows}


@router.get("/learning/lessons")
def learning_lessons(agent_id: str = "", status: str = "") -> dict[str, Any]:
    statuses = [s for s in status.split(",") if s in learning.LESSON_STATUSES]
    names = _names()
    lessons = learning.get_experience_store().lessons(agent_id=agent_id, statuses=statuses)
    return {"lessons": [{**lesson.to_dict(), "agent_name": names.get(lesson.agent_id, lesson.agent_id)}
                        for lesson in lessons]}


@router.get("/learning/lessons/{lesson_id}")
def learning_lesson(lesson_id: str) -> dict[str, Any]:
    store = learning.get_experience_store()
    lesson = store.get_lesson(lesson_id)
    events = store.lesson_events(lesson_id)
    if lesson is None and not events:
        raise HTTPException(status_code=404, detail="Lesson not found")
    sources = [ep.to_dict() for ep in (store.get_episode(i) for i in (lesson.source_episodes if lesson else [])) if ep]
    return {"lesson": lesson.to_dict() if lesson else None, "events": events, "episodes": sources}


@router.post("/learning/lessons/{lesson_id}/action")
def learning_lesson_action(lesson_id: str, payload: LessonActionPayload) -> dict[str, Any]:
    try:
        return {"lesson": learning.lesson_action(lesson_id, payload.action).to_dict()}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Lesson not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/learning/lessons/{lesson_id}")
def learning_lesson_edit(lesson_id: str, payload: LessonEditPayload) -> dict[str, Any]:
    try:
        return {"lesson": learning.edit_lesson(lesson_id, title=payload.title, text=payload.text).to_dict()}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Lesson not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/learning/lessons/{lesson_id}/rollback")
def learning_lesson_rollback(lesson_id: str, payload: RollbackPayload) -> dict[str, Any]:
    try:
        lesson = learning.rollback_lesson(lesson_id, payload.event_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="That change isn't in this lesson's history") from exc
    return {"lesson": lesson.to_dict() if lesson else None}


@router.delete("/learning/lessons/{lesson_id}")
def learning_lesson_delete(lesson_id: str) -> dict[str, Any]:
    if not learning.delete_lesson(lesson_id):
        raise HTTPException(status_code=404, detail="Lesson not found")
    return {"ok": True}


@router.get("/learning/episodes")
def learning_episodes(agent_id: str = "", limit: int = Query(30, ge=1, le=200)) -> dict[str, Any]:
    episodes = learning.get_experience_store().episodes(agent_id=agent_id, limit=limit)
    return {"episodes": [ep.to_dict() for ep in episodes]}


@router.get("/learning/reliability")
def learning_reliability() -> dict[str, Any]:
    from agent.learning.reliability import summary

    return summary()


@router.post("/learning/agents/{agent_id}/pause")
def learning_pause(agent_id: str, payload: PausePayload) -> dict[str, Any]:
    if get_persona_store().get(agent_id) is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    learning.set_paused(agent_id, payload.paused)
    return {"agent_id": agent_id, "paused": payload.paused}


@router.post("/learning/reflect")
def learning_reflect_now() -> dict[str, Any]:
    """Reflect on queued episodes now (in the background, within the daily cap)."""
    threading.Thread(target=learning.worker_tick, name="learning-reflect", daemon=True).start()
    return {"ok": True, "pending": learning.status()["reflections_pending"]}
