"""Stop everything (OWASP ASI10, rogue agents): one switch that halts every agent.

`stop_everything()` cancels every running turn, whatever started it, cancels the
approvals and questions still waiting for an answer, and pauses background work
(routines, heartbeat, Discord, Telegram and other channels, inbound A2A) until the
owner presses Resume. The pause is saved to disk, so restarting the app doesn't
quietly bring back a routine that was misbehaving. The owner's own chats in the
app keep working while paused.
"""

from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

PAUSED_REPLY = "All agents are paused right now: the owner pressed Stop everything. Nothing was run."

_lock = threading.Lock()
_running: set[threading.Event] = set()
_state: dict[str, Any] | None = None
_last_activity = time.time()


def _path() -> Path:
    from config import DATA_DIR

    return Path(DATA_DIR) / "paused.json"


def _load() -> dict[str, Any]:
    global _state
    if _state is None:
        try:
            data = json.loads(_path().read_text(encoding="utf-8"))
            _state = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            _state = {}
    return _state


def _save(state: dict[str, Any]) -> None:
    global _state
    _state = state
    path = _path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    tmp.replace(path)


def paused() -> bool:
    with _lock:
        return bool(_load().get("paused"))


def status() -> dict[str, Any]:
    with _lock:
        state = dict(_load())
        running = len(_running)
    return {"paused": bool(state.get("paused")), "since": state.get("since", 0), "reason": state.get("reason", ""),
            "running": running}


@contextmanager
def tracking(cancel: threading.Event) -> Iterator[None]:
    """Register a running turn so Stop everything can cancel it."""
    global _last_activity
    with _lock:
        _running.add(cancel)
        _last_activity = time.time()
    try:
        yield
    finally:
        with _lock:
            _running.discard(cancel)
            _last_activity = time.time()


def idle_seconds() -> float:
    """How long no agent has been running (0 while one is). Background upkeep waits for quiet."""
    with _lock:
        return 0.0 if _running else max(0.0, time.time() - _last_activity)


def stop_everything(reason: str = "") -> dict[str, Any]:
    from agent.lean.approvals import get_approval_broker

    with _lock:
        _save({"paused": True, "since": time.time(), "reason": str(reason or "")[:200]})
        events = list(_running)
    for event in events:
        event.set()
    cancelled_requests = 0
    try:
        from api.deps import _ACTIVE_QUERY_CANCEL_LOCK, _ACTIVE_QUERY_CANCELLATIONS

        with _ACTIVE_QUERY_CANCEL_LOCK:
            for _owner, event, _execution in list(_ACTIVE_QUERY_CANCELLATIONS.values()):
                if not event.is_set():
                    event.set()
                    cancelled_requests += 1
    except Exception:
        pass
    waiting = get_approval_broker().cancel_all()
    return {"paused": True, "stopped": len(events) + cancelled_requests, "approvals_cancelled": waiting}


def resume() -> dict[str, Any]:
    with _lock:
        _save({"paused": False, "since": time.time(), "reason": ""})
    return status()
