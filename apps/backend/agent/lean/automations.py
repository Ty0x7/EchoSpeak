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


def deliver(text: str, channels: list[str], label: str) -> list[str]:
    """Send a finished automation result to external channels. Returns where it went."""
    from agent.lean import outbound, stop

    sent: list[str] = []
    if stop.paused():
        return sent
    body = f"{label}\n\n{text}".strip()
    for channel in {str(c).strip().lower() for c in channels or []}:
        if outbound.take(channel):
            logger.warning("Routine result not sent to {}: outbound limit reached", channel)
            continue
        try:
            if channel == "discord":
                owner = str(getattr(config, "discord_bot_owner_id", "") or "").strip()
                if owner:
                    from discord_bot import queue_discord_dm

                    if queue_discord_dm(owner, body[:1990]):
                        sent.append("discord")
            elif channel == "telegram":
                from telegram_bot import get_telegram_bot

                bot = get_telegram_bot()
                if bot is not None:
                    bot.send_heartbeat(body[:3900])
                    sent.append("telegram")
        except Exception as exc:
            logger.warning("Automation delivery to {} failed: {}", channel, exc)
    return sent


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
) -> dict[str, Any]:
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
    )
    text = str(result.get("response") or "").strip()
    delivered = deliver(text, list(channels or []), f"{name}") if text else []
    return {
        "success": bool(result.get("success")),
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
