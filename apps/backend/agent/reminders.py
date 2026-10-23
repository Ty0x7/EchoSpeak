"""Reminders: "remind me on Sunday about the Raptors game", built on Routines.

A reminder is a Routine (agent/routines.py) with ``metadata.kind == "reminder"``:
- one-time: ``trigger_type="once"`` with ``run_at``; it fires once, then shows as done;
- repeating: ``trigger_type="schedule"`` with a cron made from the first time (daily, weekdays,
  weekly, monthly).

It fires on the existing scheduler. A plain reminder (``action_type="remind"``) posts its message
into the chat it was set in, with no model call, so it works even when no model is running. A
reminder with a ``task`` (``action_type="query"``) runs the agent like any routine: unattended,
so anything needing approval is skipped and reported. Delivery goes through
``automations.deliver``: the desktop notification feed (agent/notifications.py), Discord and
Telegram, each only when it's set up.

Echo gets three tools (set_reminder, list_reminders, cancel_reminder), owners only. The current
date and time are in its prompt, so it turns "Sunday at 4" into an exact time; set_reminder
repeats the time back in words so the reply can't claim a reminder that wasn't saved.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Optional

from loguru import logger

REPEATS = ("none", "daily", "weekdays", "weekly", "monthly")
CHANNELS = ("desktop", "discord", "telegram")
# A reminder set for a day without a time.
DEFAULT_HOUR = 9
# Reminders more than this late (EchoSpeak was closed) say so.
LATE_AFTER = timedelta(minutes=5)
MAX_MESSAGE = 500


# ── time ────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now().astimezone()


def parse_when(when: str, now: Optional[datetime] = None) -> datetime:
    """An exact local time from ISO-style text: 2026-10-25T16:00, 2026-10-25 16:00, 2026-10-25, or 16:00.

    A date alone means 9:00 that day; a time alone means the next time it comes round.
    A time without an offset is local. Raises ValueError with a reason a model can act on.
    """
    now = now or _now()
    text = str(when or "").strip().replace("/", "-")
    if not text:
        raise ValueError("give a time, e.g. 2026-10-25T16:00, or use in_minutes")
    clock = re.fullmatch(r"(\d{1,2}):(\d{2})(?:\s*([ap]m))?", text, re.I)
    if clock:
        hour, minute = int(clock.group(1)), int(clock.group(2))
        if clock.group(3):
            hour = hour % 12 + (12 if clock.group(3).lower() == "pm" else 0)
        at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return at if at > now else at + timedelta(days=1)
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            day = date.fromisoformat(text)
            at = datetime(day.year, day.month, day.day, DEFAULT_HOUR)
        else:
            at = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"couldn't read {when!r} as a time; use the form 2026-10-25T16:00 (now is "
                         f"{now.strftime('%Y-%m-%dT%H:%M')})") from exc
    if at.tzinfo is None:
        at = at.replace(tzinfo=now.tzinfo)
    return at.astimezone(now.tzinfo).replace(second=0, microsecond=0)


def cron_for(at: datetime, repeat: str) -> str:
    """The cron schedule for a repeating reminder first due at ``at``."""
    minute, hour = at.minute, at.hour
    if repeat == "daily":
        return f"{minute} {hour} * * *"
    if repeat == "weekdays":
        return f"{minute} {hour} * * 1-5"
    if repeat == "weekly":
        return f"{minute} {hour} * * {(at.weekday() + 1) % 7}"  # cron: 0 = Sunday
    if repeat == "monthly":
        return f"{minute} {hour} {at.day} * *"
    raise ValueError(f"repeat must be one of {', '.join(REPEATS)}")


def describe(at: datetime, now: Optional[datetime] = None) -> str:
    """'Sunday, October 25, 2026 at 4:00 PM (in 2 days)'."""
    now = now or _now()
    clock = at.strftime("%I:%M %p").lstrip("0")
    label = f"{at.strftime('%A, %B')} {at.day}, {at.year} at {clock}"
    delta = at - now
    if delta.total_seconds() < 0:
        return label
    minutes = int(delta.total_seconds() // 60)
    if minutes < 60:
        rel = f"in {max(minutes, 1)} minute{'s' if minutes != 1 else ''}"
    elif minutes < 24 * 60:
        hours = round(minutes / 60)
        rel = f"in {hours} hour{'s' if hours != 1 else ''}"
    else:
        days = (at.date() - now.date()).days
        rel = "tomorrow" if days == 1 else f"in {days} days"
    return f"{label} ({rel})"


def _repeat_words(repeat: str, at: datetime) -> str:
    clock = at.strftime("%I:%M %p").lstrip("0")
    return {"daily": f"every day at {clock}", "weekdays": f"every weekday at {clock}",
            "weekly": f"every {at.strftime('%A')} at {clock}", "monthly": f"on day {at.day} of every month at {clock}"}.get(repeat, "")


# ── create, list, cancel ────────────────────────────────────────────────

def is_reminder(routine: Any) -> bool:
    return str((getattr(routine, "metadata", None) or {}).get("kind") or "") == "reminder"


def create(*, message: str, at: datetime, repeat: str = "none", task: str = "", session_id: str = "",
           channels: Optional[list[str]] = None, agent_id: str = "echo") -> Any:
    from agent.routines import get_routine_manager

    message = " ".join(str(message or "").split())[:MAX_MESSAGE]
    if not message:
        raise ValueError("say what to remind about")
    repeat = str(repeat or "none").lower()
    if repeat not in REPEATS:
        raise ValueError(f"repeat must be one of {', '.join(REPEATS)}")
    wanted = ["web", *[c for c in dict.fromkeys(channels or ["desktop"]) if c in CHANNELS]]
    task = str(task or "").strip()
    return get_routine_manager().create_routine(
        name=f"Reminder: {message[:60]}",
        description=message,
        trigger_type="once" if repeat == "none" else "schedule",
        run_at=at.isoformat() if repeat == "none" else None,
        schedule=cron_for(at, repeat) if repeat != "none" else None,
        action_type="query" if task else "remind",
        action_config={"message": message, **({"query": f"Reminder for the user: {message}\n\n{task}"} if task else {})},
        metadata={"kind": "reminder", "agent_id": agent_id or "echo", "repeat": repeat,
                  "first_at": at.isoformat(), "task": task},
        delivery_channels=wanted,
        session_id=session_id,
    )


def upcoming() -> list[Any]:
    from agent.routines import get_routine_manager

    items = [r for r in get_routine_manager().list_routines(enabled_only=True) if is_reminder(r) and r.next_run]
    return sorted(items, key=lambda r: str(r.next_run))


def find(ref: str) -> list[Any]:
    """Reminders matching an id (or its first characters) or words from the message."""
    from agent.routines import get_routine_manager

    ref = str(ref or "").strip().lower()
    if not ref:
        return []
    mine = [r for r in get_routine_manager().list_routines() if is_reminder(r) and r.enabled]
    by_id = [r for r in mine if r.id.lower().startswith(ref.removeprefix("r-"))]
    if by_id:
        return by_id
    return [r for r in mine if ref in str((r.action_config or {}).get("message") or r.description or "").lower()]


def _when_of(routine: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(routine.next_run).replace("Z", "+00:00")).astimezone()
    except (TypeError, ValueError):
        return None


def line(routine: Any, now: Optional[datetime] = None) -> str:
    at = _when_of(routine)
    repeat = str((routine.metadata or {}).get("repeat") or "none")
    first = (routine.metadata or {}).get("first_at")
    when = describe(at, now) if at else "not scheduled"
    if repeat != "none" and first:
        try:
            when = f"{_repeat_words(repeat, datetime.fromisoformat(first))}, next {when}"
        except ValueError:
            pass
    task = " · then does: " + str((routine.metadata or {}).get("task"))[:80] if (routine.metadata or {}).get("task") else ""
    return f"r-{routine.id[:8]} · {(routine.action_config or {}).get('message') or routine.description} · {when}{task}"


# ── firing ──────────────────────────────────────────────────────────────

def _chat_busy(session_id: str) -> bool:
    from agent.state import get_state_store

    store = get_state_store()
    state = store.get_thread_state(session_id)
    active = store.get_execution(state.active_turn_id or state.current_execution_id or "")
    return bool(active and active.status not in {"completed", "failed", "canceled", "superseded"})


def post_to_chat(session_id: str, text: str, *, ref: str = "") -> Optional[str]:
    """Add one finished Echo message to a chat, with no model call. None while that chat is mid-reply."""
    from agent.state import get_state_store
    from agent.threads import get_thread_manager

    if _chat_busy(session_id):
        return None  # creating a turn now would supersede the one running; the scheduler retries
    store = get_state_store()
    state = store.get_thread_state(session_id)
    execution = store.create_execution(
        thread_id=session_id, kind="reminder", source="reminder", status="completed", query="",
        record_user_message=False, workspace_id=str(state.workspace_id or ""),
        active_project_id=str(state.active_project_id or ""), metadata={"reminder_id": ref},
    )
    store.add_item(turn_id=execution.id, item_type="assistant_message", status="complete", session_id=session_id,
                   project_id=str(state.active_project_id or ""),
                   payload={"text": text, "agent_id": "echo", "agent_name": "Echo",
                            "message_id": f"msg_{uuid.uuid4().hex[:12]}", "timeline": [], "backend_success": True,
                            "role": "reminder"})
    store.update_execution(execution.id, status="completed", success=True, response_preview=text[:200])
    store.update_thread_state(session_id, execution_status="ready", current_execution_id="", active_turn_id="")
    try:
        get_thread_manager().touch_thread(session_id)
    except Exception:
        logger.debug("Couldn't touch the reminder's chat", exc_info=True)
    return execution.id


def _session_for(routine: Any) -> str:
    from agent.lean.automations import _ensure_chat, _thread_exists
    from agent.routines import get_routine_manager

    session_id = str(getattr(routine, "session_id", "") or "").strip()
    if session_id and _thread_exists(session_id):
        return session_id
    session_id = _ensure_chat("Reminders")  # the chat it was set in is gone
    get_routine_manager().update_routine(routine.id, session_id=session_id)
    return session_id


def fire(routine: Any, now: Optional[datetime] = None) -> dict[str, Any]:
    """Deliver a plain reminder: its chat, then the desktop, Discord and Telegram as set. No model call."""
    from agent.lean import stop
    from agent.lean.automations import deliver

    if stop.paused():
        return {"deferred": True}  # Stop everything: held until the owner resumes
    now = now or _now()
    message = str((routine.action_config or {}).get("message") or routine.description or routine.name)
    due = _when_of(routine)
    late = f"\n\n(This was due {describe(due, now)}; EchoSpeak wasn't running then.)" if due and now - due > LATE_AFTER else ""
    session_id = _session_for(routine)
    posted = post_to_chat(session_id, f"⏰ Reminder: {message}{late}", ref=routine.id)
    if posted is None:
        return {"deferred": True}
    channels = [c for c in (routine.delivery_channels or []) if c != "web"]
    sent = deliver(message + late, channels, "⏰ Reminder", session_id=session_id, ref=routine.id) if channels else []
    return {"success": True, "task_id": posted, "session_id": session_id, "delivered": sent}


# ── Echo's tools ────────────────────────────────────────────────────────

def _available_channels() -> list[str]:
    from config import config

    out = ["desktop"]
    if str(getattr(config, "discord_bot_owner_id", "") or "").strip() and getattr(config, "allow_discord_bot", False):
        out.append("discord")
    if getattr(config, "allow_telegram_bot", False) and str(getattr(config, "telegram_bot_token", "") or "").strip():
        out.append("telegram")
    return out


def tools(session_id: str, source: str = "web") -> list:
    from agent.lean.toolbox import NativeTool, ToolResult

    default_channels = {"discord_bot_dm": ["desktop", "discord"], "telegram": ["desktop", "telegram"]}.get(source, ["desktop"])

    def set_reminder(args: dict[str, Any]) -> ToolResult:
        now = _now()
        try:
            if args.get("in_minutes") not in (None, ""):
                minutes = int(args["in_minutes"])
                if not 1 <= minutes <= 60 * 24 * 366:
                    raise ValueError("in_minutes must be between 1 and 527040")
                at = (now + timedelta(minutes=minutes)).replace(second=0, microsecond=0)
            else:
                at = parse_when(str(args.get("when") or ""), now)
            repeat = str(args.get("repeat") or "none").lower()
            if repeat == "none" and at <= now:
                raise ValueError(f"{describe(at, now)} has already passed (now is {now.strftime('%A %Y-%m-%d %H:%M')}); "
                                 "pick a later time")
            if repeat not in REPEATS:
                raise ValueError(f"repeat must be one of {', '.join(REPEATS)}")
            available = _available_channels()
            asked = [str(c).lower() for c in (args.get("channels") or default_channels)]
            channels = [c for c in asked if c in available] or ["desktop"]
            routine = create(message=str(args.get("message") or ""), at=at, repeat=repeat, task=str(args.get("task") or ""),
                             session_id=session_id, channels=channels)
        except ValueError as exc:
            return ToolResult(False, f"Error: reminder not set: {exc}.", 0)
        where = ["this chat", *[{"desktop": "a Windows notification", "discord": "Discord", "telegram": "Telegram"}[c]
                                for c in channels]]
        skipped = [c for c in asked if c not in available]
        when = describe(at, now) if repeat == "none" else f"{_repeat_words(repeat, at)}, first {describe(routine_at(routine) or at, now)}"
        text = (f"Reminder set (r-{routine.id[:8]}): \"{routine.action_config.get('message')}\" · {when}. "
                f"It will appear in {', '.join(where[:-1]) + ' and ' + where[-1] if len(where) > 1 else where[0]}. "
                "If EchoSpeak is closed then, it arrives as soon as EchoSpeak starts again.")
        if skipped:
            text += f" Not sent to {', '.join(skipped)}: not set up in Settings."
        if routine.action_type == "query":
            text += " At that time Echo will also do the task; anything needing approval is skipped and reported."
        return ToolResult(True, text, 0, effects=[{"kind": "reminder", "id": routine.id, "at": routine.next_run,
                                                   "repeat": repeat}])

    def list_reminders(args: dict[str, Any]) -> str:
        items = upcoming()
        if not items:
            return "No reminders are set."
        now = _now()
        return "Upcoming reminders:\n" + "\n".join(f"- {line(r, now)}" for r in items[:30])

    def cancel_reminder(args: dict[str, Any]) -> str:
        from agent.routines import get_routine_manager

        matches = find(str(args.get("reminder") or args.get("id") or ""))
        if not matches:
            return "Error: no reminder matches that. Call list_reminders to see their ids."
        if len(matches) > 1 and not args.get("all_matches"):
            listed = "\n".join(f"- {line(r)}" for r in matches[:10])
            return f"Error: {len(matches)} reminders match. Use the id of the one to cancel:\n{listed}"
        for routine in matches:
            get_routine_manager().delete_routine(routine.id)
        return "Cancelled: " + "; ".join(str((r.action_config or {}).get("message") or r.description) for r in matches)

    channel_list = {"type": "array", "items": {"type": "string", "enum": list(CHANNELS)},
                    "description": "Where else to notify: desktop (Windows notification, default), discord, telegram."}
    return [
        NativeTool(
            name="set_reminder",
            description=("Remind the user at a time: 'remind me Sunday about the game', 'in 20 minutes', 'every weekday "
                         "at 8'. Give the exact local time as when (2026-10-25T16:00), computed from the current date "
                         "and time in your instructions, or in_minutes. With no time of day, use 9:00; for an event "
                         "with a known start, an hour before. Say the time back to the user from the result."),
            parameters={"type": "object", "properties": {
                "message": {"type": "string", "description": "What to remind about, e.g. 'Raptors vs Timberwolves at 5 PM'."},
                "when": {"type": "string", "description": "Local date and time, e.g. 2026-10-25T16:00 (a date alone means 9:00)."},
                "in_minutes": {"type": "integer", "description": "Instead of when: minutes from now."},
                "repeat": {"type": "string", "enum": list(REPEATS), "description": "none (default) or how often."},
                "task": {"type": "string", "description": "Optional: something to do at that time, e.g. 'check the "
                                                           "final score'. Leave out for a plain reminder."},
                "channels": channel_list,
            }, "required": ["message"]},
            func=set_reminder,
        ),
        NativeTool(name="list_reminders", description="List the user's upcoming reminders with their ids.",
                   parameters={"type": "object", "properties": {}}, func=list_reminders, parallel_safe=True),
        NativeTool(name="cancel_reminder", description="Cancel a reminder by its id (r-…) or words from its message.",
                   parameters={"type": "object", "properties": {
                       "reminder": {"type": "string"},
                       "all_matches": {"type": "boolean", "description": "Cancel every match, not just one."}},
                       "required": ["reminder"]},
                   func=cancel_reminder),
    ]


def routine_at(routine: Any) -> Optional[datetime]:
    return _when_of(routine)
