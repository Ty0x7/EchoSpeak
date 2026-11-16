"""Echo checks in: it tells you about things that need you, without being asked.

Two layers, set in Settings › Automations › Echo checks in:

- "Things that need me" (the default): on the scheduler tick, agent/attention.py looks at
  EchoSpeak's own state with no model call. Anything new and important becomes one short
  message from Echo in Echo's conversation in Agents, where you can reply and Echo picks
  it up like any conversation. It also appears as a desktop notification and on any channel
  you chose. The same thing isn't announced again for 12 hours, notices are at least 10
  minutes apart, and nothing is sent during quiet hours or while Stop everything is on.
- "Friendly check-ins" adds Echo's own periodic note (agent/heartbeat.py), written by the
  model from the same attention list and what it remembers. It may simply ask what's up.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger

from agent import attention

REPEAT_SECONDS = 12 * 3600  # don't announce the same thing again sooner
MIN_GAP_SECONDS = 10 * 60   # at least this long between two notices
FORGET_SECONDS = 7 * 24 * 3600
MODES = ("off", "important", "friendly")
_lock = threading.Lock()


def mode() -> str:
    from config import config
    value = str(getattr(config, "checkin_mode", "important") or "important").strip().lower()
    return value if value in MODES else "important"


def quiet(now: Optional[datetime] = None) -> bool:
    """Whether it's quiet hours (local time). Start == end means no quiet hours."""
    from config import config
    start = int(getattr(config, "checkin_quiet_start", 22)) % 24
    end = int(getattr(config, "checkin_quiet_end", 8)) % 24
    hour = (now or datetime.now()).hour
    if start == end:
        return False
    return start <= hour < end if start < end else hour >= start or hour < end


def session() -> str:
    """Where check-ins go: Echo's own conversation in Agents, like a message from a contact.

    Older versions used a "Check-ins with Echo" chat; it stays as history.
    """
    from agent.lean.personas import get_persona_store
    from agent.lean.rooms import get_room_store

    echo = get_persona_store().default()
    return get_room_store().direct(echo.id, echo.name).thread_id


def _path() -> Path:
    from config import DATA_DIR
    return Path(DATA_DIR) / "checkins.json"


def _load() -> dict:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    from agent.atomic_replace import replace as atomic_replace
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data), encoding="utf-8")
    atomic_replace(temporary, path)


def compose(items: list[attention.Item]) -> str:
    """Echo's message for new items: short, specific, and an invitation to reply."""
    if len(items) == 1:
        item = items[0]
        where = f" You'll find it in {item.where}." if item.where else ""
        return f"Heads up: {item.title}" + (f" ({item.detail})" if item.detail else "") + f".{where}\n\nReply here if you want me to help with it."
    return ("A few things need you:\n" + attention.digest(items) + "\n\nReply here and I'll help with any of them.")


def channels() -> list[str]:
    from config import config
    extra = [c for c in (getattr(config, "heartbeat_channels", None) or []) if c in {"discord", "telegram"}]
    return ["desktop", *extra]


def tick(now: Optional[float] = None) -> Optional[str]:
    """Tell the owner about anything new that needs them. Returns the notice text, or None."""
    from agent.lean import stop

    now = time.time() if now is None else now
    if mode() == "off" or stop.paused() or quiet(datetime.fromtimestamp(now)):
        return None
    with _lock:
        state = _load()
        notified = {k: float(v) for k, v in (state.get("notified") or {}).items() if now - float(v) < FORGET_SECONDS}
        if now - float(state.get("last_notice") or 0) < MIN_GAP_SECONDS:
            return None
        fresh = [item for item in attention.collect()
                 if item.urgency in {"now", "soon"} and now - notified.get(item.key, 0) > REPEAT_SECONDS]
        if not fresh:
            if len(notified) != len(state.get("notified") or {}):
                _save({**state, "notified": notified})
            return None
        fresh = fresh[:6]
        text = compose(fresh)
        from agent.coordination import digest
        ref = "checkin:" + digest(sorted(item.key for item in fresh))[:24]
        from agent.reminders import post_to_chat
        chat = session()
        if post_to_chat(chat, text, ref=ref) is None:
            return None  # that chat is mid-reply: the next tick tries again
        try:
            from agent.lean.automations import deliver
            deliver(text, channels(), "Echo", session_id=chat, ref=ref)
        except Exception:
            logger.exception("Check-in notice couldn't be delivered beyond the chat")
        for item in fresh:
            notified[item.key] = now
        _save({**state, "notified": notified, "last_notice": now})
        return text
