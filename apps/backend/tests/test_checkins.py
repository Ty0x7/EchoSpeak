"""Echo checks in: model-free notices about what needs the owner, and the friendly heartbeat.

Real StateStore, coordination ledger, routines and threads in a temp folder; no model calls.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from agent import attention, checkins, code_accounts, routines, state, threads
from agent.lean import stop
from config import config


@pytest.fixture
def env(tmp_path, monkeypatch):
    store = state.StateStore(tmp_path / "state")
    monkeypatch.setattr(state, "_state_store", store)
    manager = routines.RoutineManager(tmp_path / "routines")
    monkeypatch.setattr(routines, "_routine_manager", manager)
    chats = threads.ThreadManager(tmp_path / "threads.json")
    monkeypatch.setattr(threads, "get_thread_manager", lambda: chats)
    monkeypatch.setattr(checkins, "_path", lambda: tmp_path / "checkins.json")
    monkeypatch.setattr(code_accounts, "_cache_path", lambda: tmp_path / "agent-status.json")
    monkeypatch.setattr(config, "checkin_mode", "important")
    monkeypatch.setattr(config, "checkin_quiet_start", 0)
    monkeypatch.setattr(config, "checkin_quiet_end", 0)  # no quiet hours unless a test sets them
    monkeypatch.setattr(stop, "paused", lambda: False)
    monkeypatch.setattr(attention, "_connections", lambda: [])  # no MCP manager in these tests
    sent = []
    from agent.lean import automations
    monkeypatch.setattr(automations, "deliver", lambda text, channels, label, **k: sent.append((text, channels)) or channels)
    return store, manager, chats, sent


def _texts(store, chats):
    session = next(t for t in chats.list_threads(include_archived=True) if t.title == checkins.CHAT_TITLE)
    return [m["text"] for turn in store.session_timeline(session.thread_id)["turns"] for m in turn["messages"]]


def test_echo_tells_you_what_needs_you_once_in_a_chat_you_can_reply_to(env):
    store, manager, chats, sent = env
    coord = store.coordination
    stuck = coord.claim(coord.create(objective="Publish the newsletter", agent_id="echo", tools=["file_write"])["id"])
    coord.finish(stuck, status="failed", result="SMTP refused the login")
    code_accounts._remember("claude_cli", {"installed": True, "signed_in": False, "models": []})

    text = checkins.tick()
    assert text and "A task failed" in text and "Publish the newsletter" in text and "Claude Code is signed out" in text
    assert text.endswith("Reply here and I'll help with any of them.")
    assert _texts(store, chats) == [text]  # in the check-ins chat, as Echo, no "user" prompt shown
    assert sent and sent[0][1][0] == "desktop"

    # The same things aren't announced again, and notices keep a minimum gap.
    assert checkins.tick(now=time.time() + 60) is None
    assert checkins.tick(now=time.time() + 3600) is None
    code_accounts._remember("codex", {"installed": True, "signed_in": False, "models": []})
    assert checkins.tick(now=time.time() + 60) is None  # too soon after the last notice
    later = checkins.tick(now=time.time() + checkins.MIN_GAP_SECONDS + 5)
    assert later and later.startswith("Heads up: Codex is signed out") and "Claude Code" not in later


def test_quiet_hours_off_and_stop_hold_notices(env, monkeypatch):
    store, *_ = env
    code_accounts._remember("codex", {"installed": True, "signed_in": False, "models": []})
    monkeypatch.setattr(config, "checkin_mode", "off")
    assert checkins.tick() is None
    monkeypatch.setattr(config, "checkin_mode", "important")
    monkeypatch.setattr(stop, "paused", lambda: True)
    assert checkins.tick() is None
    monkeypatch.setattr(stop, "paused", lambda: False)
    hour = datetime.now().hour
    monkeypatch.setattr(config, "checkin_quiet_start", hour)
    monkeypatch.setattr(config, "checkin_quiet_end", (hour + 1) % 24)
    assert checkins.quiet() and checkins.tick() is None
    monkeypatch.setattr(config, "checkin_quiet_end", hour)  # start == end: no quiet hours
    assert checkins.tick() is not None  # held items go out once quiet hours end


def test_attention_sees_approvals_reminders_and_model_errors(env, monkeypatch):
    store, manager, *_ = env
    coord = store.coordination
    work = coord.claim(coord.create(objective="Rename the files", agent_id="echo", tools=["file_write"])["id"])
    coord.approval(work, approval_id="apr_1", tool="file_write", args={"path": "a.txt"}, summary="Write a.txt", reason="")
    soon = (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()
    manager.create_routine("Call mom", trigger_type="once", run_at=soon, action_type="remind",
                           action_config={"message": "Call mom back"})
    failed = store.create_execution(thread_id="t1", query="hi", status="failed")
    store.update_execution(failed.id, status="failed", error="401 Unauthorized: invalid API key")

    items = {item.kind: item for item in attention.collect()}
    assert items["approval"].urgency == "now" and "Write a.txt" in items["approval"].detail
    assert items["reminder"].urgency == "fyi" and "Call mom" in items["reminder"].title
    assert items["model"].urgency == "soon" and "invalid API key" in items["model"].detail
    # Upcoming reminders are context for a friendly check-in, not a notice of their own.
    notice = checkins.tick()
    assert "Write a.txt" in notice and "Call mom" not in notice


def test_a_broken_source_never_hides_the_others(env, monkeypatch):
    def broken():
        raise RuntimeError("source down")
    monkeypatch.setattr(attention, "SOURCES", (broken, attention._coding_agents))
    code_accounts._remember("codex", {"installed": True, "signed_in": False, "models": []})
    assert [item.kind for item in attention.collect()] == ["coding_agent"]


def test_the_heartbeat_starts_without_settings_nothing_sets(monkeypatch):
    """It used to refuse to start without HEARTBEAT_PROJECT_ID and HEARTBEAT_SESSION_ID."""
    import asyncio

    from agent import heartbeat
    from api import deps

    started = []
    monkeypatch.setattr(config, "heartbeat_enabled", True)
    monkeypatch.setattr(config, "heartbeat_project_id", "")
    monkeypatch.setattr(config, "heartbeat_session_id", "")
    monkeypatch.setattr(deps, "get_agent", lambda *a, **k: SimpleNamespace())
    monkeypatch.setattr(heartbeat.HeartbeatManager, "start", lambda self: started.append(self))
    monkeypatch.setattr(heartbeat, "_manager", None, raising=False)
    previous = heartbeat.get_heartbeat_manager()
    heartbeat.set_heartbeat_manager(None)
    try:
        asyncio.run(deps._reconcile_heartbeat_runtime())
    finally:
        heartbeat.set_heartbeat_manager(previous)
    assert started


def test_a_silent_check_in_leaves_no_trace(env):
    store, *_ = env
    turn = store.create_execution(thread_id="checkins", query="SYSTEM PULSE", status="completed", record_user_message=False)
    store.add_item(turn_id=turn.id, item_type="assistant_message", status="complete", session_id="checkins", payload={"text": "NO_HEARTBEAT"})
    store.update_execution(turn.id, status="completed", success=True)
    assert store.session_timeline("checkins")["turns"]
    assert store.discard_turn(turn.id)
    assert store.session_timeline("checkins")["turns"] == []
    assert not store.search_messages("NO_HEARTBEAT", limit=5)
    running = store.create_execution(thread_id="checkins", query="x", status="running")
    assert store.discard_turn(running.id) is False  # never a turn that's still going
