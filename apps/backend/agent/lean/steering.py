"""Steering: instructions the user adds while an agent is working on their request.

A queued follow-up (StateStore.queued_turns) waits for the current turn to end
and then runs as its own turn. A steer joins the turn that is running: the loop
reads this inbox at its next safe checkpoint (before the next model call, or
when the agent was about to give its final answer) and adds the text to the
conversation as the user's words, so the work done so far, tool results and
approvals are kept. Nothing is injected in the middle of a tool call.

The inbox lives in memory, per chat. Whatever is left when the turn ends (the
agent finished first, or the user pressed Stop) moves to the durable queue, so a
steer is never silently lost. Like a queued message it is the owner's input;
it is never treated as outside content.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any

_LOCK = threading.Lock()
_INBOX: dict[str, list[dict[str, Any]]] = {}
MAX_PENDING = 20


def add(session_id: str, text: str, client_id: str = "") -> dict[str, Any]:
    body = str(text or "").strip()
    if not body:
        raise ValueError("Steering needs a message.")
    item = {"id": str(client_id or f"steer_{uuid.uuid4().hex[:10]}")[:128], "text": body[:8000], "at": time.time()}
    with _LOCK:
        pending = _INBOX.setdefault(str(session_id or "default"), [])
        if any(row["id"] == item["id"] for row in pending):
            return next(row for row in pending if row["id"] == item["id"])
        if len(pending) >= MAX_PENDING:
            raise ValueError("Too many pending instructions; wait for the agent to read them.")
        pending.append(item)
    return item


def take(session_id: str) -> list[dict[str, Any]]:
    """Everything waiting for this chat, oldest first, removed from the inbox."""
    with _LOCK:
        return _INBOX.pop(str(session_id or "default"), [])


def pending(session_id: str) -> list[dict[str, Any]]:
    with _LOCK:
        return [dict(row) for row in _INBOX.get(str(session_id or "default"), [])]


def to_queue(session_id: str) -> list[dict[str, Any]]:
    """Move unread steers to the durable follow-up queue when the turn ends."""
    left = take(session_id)
    if not left:
        return []
    from agent.state import get_state_store

    store = get_state_store()
    for row in left:
        store.enqueue_turn(session_id, message=row["text"], client_request_id=row["id"])
    return left


def as_user_text(rows: list[dict[str, Any]]) -> str:
    lines = "\n".join(f"- {row['text']}" for row in rows) if len(rows) > 1 else rows[0]["text"]
    return ("[The user added this while you were working. Take it into account from now on, keep the work you "
            "have already done, and say briefly how you're adjusting]:\n" + lines)
