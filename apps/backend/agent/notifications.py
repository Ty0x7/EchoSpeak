"""Notifications for the owner's desktop: reminders that fired, routine results sent to "desktop".

A small in-process feed with increasing sequence numbers:
- the desktop shell (apps/desktop/src-tauri) *claims* new items and shows each as a Windows
  notification, so they arrive while the window is hidden in the tray;
- the web UI *reads* items after a sequence number to refresh the chat a reminder posted into,
  and, in a plain browser (no desktop shell), shows them with the Web Notifications API.

Items live in memory: the backend is running whenever a reminder can fire, and anything that
fired is also saved in its chat, so nothing is lost if a notification is.
"""

from __future__ import annotations

import threading
import time
from typing import Any

MAX_ITEMS = 100

_lock = threading.Lock()
_items: list[dict[str, Any]] = []
_seq = 0


def push(title: str, body: str, *, session_id: str = "", kind: str = "reminder", ref: str = "") -> dict[str, Any]:
    """Queue one notification. Returns it, with its sequence number."""
    global _seq
    with _lock:
        _seq += 1
        item = {"seq": _seq, "title": str(title or "EchoSpeak")[:120], "body": str(body or "")[:500],
                "session_id": str(session_id or ""), "kind": kind, "ref": str(ref or ""), "at": time.time(),
                "shown": False}
        _items.append(item)
        del _items[:-MAX_ITEMS]
        return dict(item)


def claim() -> list[dict[str, Any]]:
    """Items the desktop hasn't shown yet, marked shown so each appears once."""
    with _lock:
        fresh = [item for item in _items if not item["shown"]]
        for item in fresh:
            item["shown"] = True
        return [dict(item) for item in fresh]


def since(seq: int) -> dict[str, Any]:
    """Items after ``seq`` (read-only, for the web UI), and the latest sequence number."""
    with _lock:
        return {"items": [dict(item) for item in _items if item["seq"] > int(seq or 0)], "latest": _seq}


def reset() -> None:
    """Tests."""
    global _seq
    with _lock:
        _items.clear()
        _seq = 0
