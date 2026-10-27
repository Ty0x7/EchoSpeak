"""Automations on the lean loop: routines (schedule / webhook / manual) and the
heartbeat check-in.

One path for both:
  1. pick the chat the result goes to (the routine's own chat, created on first run)
  2. run the chosen agent on the lean loop with source="routine"
     (no live UI, so approval-gated actions are skipped and reported, not hung)
  3. the answer lands in that chat like any other message
  4. optionally deliver it to Discord (owner DM) and/or Telegram (allowed users)

This replaces the TaskRun / AutomationRun / lease coordinator, which required a
Project and Session for every run and blocked all external delivery.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any, Callable, Optional

from loguru import logger

from config import config
from agent.lean.runtime import run_lean_query

AgentFactory = Callable[[str], Any]

AUTOMATION_PREFIX = (
    "This is an automated run of \"{name}\". The user is not watching live, so do the work "
    "now and finish with a short report of what you did or found. If something needs the "
    "user's approval, say exactly what and stop there.\n\n"
)


def deliver(text: str, channels: list[str], label: str, *, session_id: str = "", ref: str = "") -> list[str]:
    """Send a finished automation result to its channels. Returns where it went.

    "desktop" queues a Windows notification (agent/notifications.py; the desktop shell shows it, even
    with the window in the tray). Discord and Telegram go to the owner.
    """
    from agent.delivery import queue_result
    return queue_result(text, channels, label=label, agent_id="echo", session_id=session_id,
                        key=ref or f"notice:{session_id}:{label}:{text}")


def _ensure_chat(title: str) -> str:
    from agent.threads import get_thread_manager

    return get_thread_manager().create_thread(title=title[:60], source="web").thread_id


def run_automation(
    *,
    agent_factory: AgentFactory,
    name: str,
    prompt: str,
    session_id: str,
    agent_id: str = "",
    channels: Optional[list[str]] = None,
    source: str = "routine",
    occurrence: str = "",
    project_id: str = "",
    budget: Optional[dict] = None,
    responsibility_id: str = "",
) -> dict[str, Any]:
    from agent.state import get_state_store
    from agent.lean.personas import get_persona_store
    from agent.delivery import queue_result
    store = get_state_store()
    if project_id:
        store.update_thread_state(session_id, active_project_id=project_id)
    owner = get_persona_store().get(agent_id) if agent_id else get_persona_store().default()
    if owner is None:
        return {"success": False, "error": "Routine owner no longer exists"}
    work = store.coordination.create(objective=AUTOMATION_PREFIX.format(name=name) + prompt,
        agent_id=owner.id, session_id=session_id, project_id=project_id,
        key=occurrence or f"automation:{uuid.uuid4().hex}", budget=budget,
        provenance={"source": source, "occurrence": occurrence, "responsibility_id": responsibility_id})
    if work["status"] not in {"ready", "completed", "failed", "cancelled"}:
        return {"deferred": True, "work_id": work["id"], "status": work["status"]}
    if work["status"] in {"completed", "failed", "cancelled"}:
        delivered = queue_result(work["result"], channels or [], label=name, agent_id=owner.id,
            session_id=session_id, key=work["id"], work_id=work["id"])
        return {"success": work["status"] == "completed", "response": work["result"], "work_id": work["id"], "delivered": delivered}
    agent = agent_factory(session_id)
    result = run_lean_query(
        agent,
        message=AUTOMATION_PREFIX.format(name=name) + prompt,
        session_id=session_id,
        request_id=f"auto-{uuid.uuid4().hex[:12]}",
        emit=lambda _event: None,
        cancel=threading.Event(),
        source=source,
        persona_id=agent_id,
        work_id=work["id"],
    )
    text = str(result.get("response") or "").strip()
    state = result.get("work", {}).get("status", "failed")
    if state in {"waiting_approval", "waiting_input", "blocked", "unknown", "interrupted", "paused"}:
        return {"deferred": True, "work_id": work["id"], "status": state, "response": text}
    delivered = queue_result(text, channels or [], label=name, agent_id=owner.id, session_id=session_id,
                             key=work["id"], work_id=work["id"]) if text else []
    return {
        "success": bool(result.get("success")),
        "work_id": work["id"],
        "task_id": str(result.get("execution_id") or ""),
        "execution_id": str(result.get("execution_id") or ""),
        "session_id": session_id,
        "delivered": delivered,
        "response": text,
        "error": str(result.get("error") or ""),
    }


def routine_runner(agent_factory: AgentFactory) -> Callable[[Any], dict[str, Any]]:
    """RoutineManager callback: runs one routine occurrence on the lean loop."""

    def run(routine: Any) -> dict[str, Any]:
        from agent.routines import get_routine_manager

        if routine.action_type == "remind":
            # A plain reminder: its message goes out as it is, with no model call (agent/reminders.py).
            from agent import reminders

            try:
                return reminders.fire(routine)
            except Exception as exc:
                logger.exception("Reminder {} failed", routine.name)
                return {"success": False, "error": str(exc)}
        from agent.lean import stop

        if stop.paused():
            return {"deferred": True}
        query = str(routine.action_config.get("query") or routine.action_config.get("message") or routine.description or routine.name).strip()
        session_id = str(getattr(routine, "session_id", "") or "").strip()
        if not session_id or not _thread_exists(session_id):
            session_id = _ensure_chat(f"Routine · {routine.name}")
            get_routine_manager().update_routine(routine.id, session_id=session_id)
        try:
            return run_automation(
                agent_factory=agent_factory,
                name=routine.name,
                prompt=query,
                session_id=session_id,
                agent_id=str((routine.metadata or {}).get("agent_id") or ""),
                channels=[c for c in (routine.delivery_channels or []) if c != "web"],
                occurrence=f"routine:{routine.id}:{(routine.metadata or {}).get('event_id') or routine.next_run or routine.last_run or 'manual'}",
                project_id=str(routine.project_id or ""), budget=(routine.metadata or {}).get("budget"),
                responsibility_id=str((routine.metadata or {}).get("responsibility_id", "")),
            )
        except Exception as exc:
            logger.exception("Routine {} failed", routine.name)
            return {"success": False, "error": str(exc), "session_id": session_id}

    return run


def _thread_exists(thread_id: str) -> bool:
    try:
        from agent.threads import get_thread_manager

        return get_thread_manager().get_thread(thread_id) is not None
    except Exception:
        return False


def heartbeat_session() -> str:
    """The heartbeat posts into one dedicated chat."""
    from agent.threads import get_thread_manager

    manager = get_thread_manager()
    for thread in manager.list_threads(include_archived=True, limit=500):
        if str(getattr(thread, "title", "")) == "Heartbeat":
            return thread.thread_id
    return _ensure_chat("Heartbeat")
