"""Reminders on Routines: natural times, one-time and repeating, firing with no model call, delivery, safety."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent import notifications, reminders, routines
from agent.lean import policy
from agent.lean.automations import routine_runner
from agent.state import get_state_store
from agent.threads import get_thread_manager

TZ = timezone(timedelta(hours=-6))
NOW = datetime(2026, 10, 23, 12, 0, tzinfo=TZ)  # a Friday


@pytest.fixture
def manager(tmp_path, monkeypatch):
    fresh = routines.RoutineManager(tmp_path / "routines")
    monkeypatch.setattr(routines, "_routine_manager", fresh)
    monkeypatch.setattr(reminders, "_now", lambda: NOW)
    notifications.reset()
    return fresh


@pytest.fixture
def chat():
    return get_thread_manager().create_thread(title="Raptors", source="web").thread_id


def _tool(name: str, session_id: str, source: str = "web"):
    return next(t for t in reminders.tools(session_id, source) if t.name == name).func


def _posted(session_id: str) -> list[str]:
    store = get_state_store()
    texts = []
    for execution in store.list_executions(session_id, limit=50):
        texts += [str(i.payload.get("text")) for i in store.list_items(execution.id) if i.item_type == "assistant_message"]
    return texts


# ── reading times ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("text, expected", [
    ("2026-10-25T16:00", datetime(2026, 10, 25, 16, 0, tzinfo=TZ)),
    ("2026-10-25 16:00", datetime(2026, 10, 25, 16, 0, tzinfo=TZ)),
    ("2026-10-25", datetime(2026, 10, 25, 9, 0, tzinfo=TZ)),  # a day alone: 9:00
    ("16:30", datetime(2026, 10, 23, 16, 30, tzinfo=TZ)),      # later today
    ("9:15 am", datetime(2026, 10, 24, 9, 15, tzinfo=TZ)),     # already passed today: tomorrow
    ("2026-10-25T22:00:00+00:00", datetime(2026, 10, 25, 16, 0, tzinfo=TZ)),
])
def test_times_are_read_as_local_moments(text, expected):
    assert reminders.parse_when(text, NOW) == expected


def test_an_unreadable_time_says_how_to_write_one():
    with pytest.raises(ValueError, match="2026-10-25T16:00"):
        reminders.parse_when("sunday afternoon", NOW)


@pytest.mark.parametrize("repeat, cron", [
    ("daily", "0 16 * * *"), ("weekdays", "0 16 * * 1-5"), ("weekly", "0 16 * * 0"), ("monthly", "0 16 25 * *"),
])
def test_repeats_become_cron_from_the_first_time(repeat, cron):
    assert reminders.cron_for(datetime(2026, 10, 25, 16, 0, tzinfo=TZ), repeat) == cron  # the 25th is a Sunday


def test_times_are_said_back_in_words():
    assert reminders.describe(datetime(2026, 10, 25, 16, 0, tzinfo=TZ), NOW) == "Sunday, October 25, 2026 at 4:00 PM (in 2 days)"
    assert reminders.describe(NOW + timedelta(minutes=20), NOW).endswith("(in 20 minutes)")


# ── Echo's tools ────────────────────────────────────────────────────────────
def test_set_a_one_time_reminder_in_this_chat(manager, chat):
    result = _tool("set_reminder", chat)({"message": "Raptors vs Timberwolves at 5 PM", "when": "2026-10-25T16:00"})
    assert result.ok and result.outcome == "ok"
    assert "Sunday, October 25, 2026 at 4:00 PM (in 2 days)" in result.output
    assert "this chat and a Windows notification" in result.output
    [routine] = manager.list_routines()
    assert routine.trigger_type == "once" and routine.action_type == "remind" and routine.session_id == chat
    assert routine.next_run == routine.run_at and routine.delivery_channels == ["web", "desktop"]
    assert result.effects == [{"kind": "reminder", "id": routine.id, "at": routine.next_run, "repeat": "none"}]


def test_a_past_time_is_refused_with_the_current_time(manager, chat):
    result = _tool("set_reminder", chat)({"message": "x", "when": "2026-10-22T09:00"})
    assert not result.ok and "already passed" in result.output and "2026-10-23 12:00" in result.output
    assert manager.list_routines() == []


def test_in_minutes_and_repeating_reminders(manager, chat):
    set_reminder = _tool("set_reminder", chat)
    assert "(in 20 minutes)" in set_reminder({"message": "tea", "in_minutes": 20}).output
    weekly = set_reminder({"message": "Raptors game", "when": "2026-10-25T16:00", "repeat": "weekly"})
    assert weekly.ok and "every Sunday at 4:00 PM" in weekly.output
    repeating = next(r for r in manager.list_routines() if r.metadata.get("repeat") == "weekly")
    assert repeating.trigger_type == "schedule" and repeating.schedule == "0 16 * * 0"


def test_channels_that_are_not_set_up_are_said_so(manager, chat, monkeypatch):
    from config import config

    monkeypatch.setattr(config, "allow_discord_bot", False, raising=False)
    result = _tool("set_reminder", chat)({"message": "x", "when": "2026-10-25T16:00", "channels": ["desktop", "discord"]})
    assert "Not sent to discord: not set up" in result.output
    assert manager.list_routines()[0].delivery_channels == ["web", "desktop"]


def test_a_reminder_with_a_task_runs_the_agent_later(manager, chat):
    result = _tool("set_reminder", chat)({"message": "Game night", "when": "2026-10-25T20:00", "task": "check the final score"})
    routine = manager.list_routines()[0]
    assert routine.action_type == "query" and "check the final score" in routine.action_config["query"]
    assert "anything needing approval is skipped" in result.output


def test_list_and_cancel(manager, chat):
    set_reminder = _tool("set_reminder", chat)
    set_reminder({"message": "Raptors vs Timberwolves", "when": "2026-10-25T16:00"})
    set_reminder({"message": "Dentist", "when": "2026-10-24T09:00"})
    listed = _tool("list_reminders", chat)({})
    assert listed.index("Dentist") < listed.index("Raptors")  # soonest first
    assert "Cancelled: Raptors vs Timberwolves" == _tool("cancel_reminder", chat)({"reminder": "raptors"})
    assert "Raptors" not in _tool("list_reminders", chat)({})
    assert _tool("cancel_reminder", chat)({"reminder": "nothing like this"}).startswith("Error: no reminder matches")


# ── firing ──────────────────────────────────────────────────────────────────
def _no_model(_session_id):
    raise AssertionError("a plain reminder must not run a model")


def test_a_due_reminder_posts_notifies_and_is_done_without_a_model(manager, chat, monkeypatch):
    _tool("set_reminder", chat)({"message": "Raptors vs Timberwolves at 5 PM", "when": "2026-10-23T12:30"})
    monkeypatch.setattr(reminders, "_now", lambda: datetime.now().astimezone())  # the scheduler runs on the real clock
    routine = manager.list_routines()[0]
    manager.update_routine(routine.id, run_at=(datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat())
    manager.set_run_callback(routine_runner(_no_model))
    manager._check_scheduled_routines()
    assert "⏰ Reminder: Raptors vs Timberwolves at 5 PM" in _posted(chat)
    done = manager.get_routine(routine.id)
    assert done.enabled is False and done.next_run is None and done.last_result_status == "complete"
    [toast] = notifications.claim()
    assert toast["title"] == "⏰ Reminder" and toast["body"] == "Raptors vs Timberwolves at 5 PM" and toast["session_id"] == chat
    assert notifications.claim() == []  # each notification shows once


def test_a_late_reminder_says_it_was_late(manager, chat):
    _tool("set_reminder", chat)({"message": "Take the bins out", "when": "2026-10-23T12:30"})
    routine = manager.list_routines()[0]
    manager.update_routine(routine.id, run_at=(datetime.now(timezone.utc) - timedelta(hours=3)).isoformat())
    assert reminders.fire(manager.get_routine(routine.id))["success"]
    assert any("EchoSpeak wasn't running then" in t for t in _posted(chat))


def test_a_reminder_waits_while_its_chat_is_mid_reply(manager, chat, monkeypatch):
    _tool("set_reminder", chat)({"message": "x", "when": "2026-10-23T12:30"})
    routine = manager.list_routines()[0]
    manager.update_routine(routine.id, run_at=(datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat())
    monkeypatch.setattr(reminders, "_chat_busy", lambda session_id: True)
    manager.set_run_callback(routine_runner(_no_model))
    manager._check_scheduled_routines()
    still = manager.get_routine(routine.id)
    assert still.enabled and still.next_run and still.run_count == 0  # retried on the next check


def test_stop_everything_holds_reminders_until_resumed(manager, chat, monkeypatch):
    from agent.lean import stop

    monkeypatch.setattr(stop, "paused", lambda: True)
    _tool("set_reminder", chat)({"message": "x", "when": "2026-10-23T12:30"})
    assert reminders.fire(manager.list_routines()[0]) == {"deferred": True}


def test_a_deleted_chat_sends_the_reminder_to_a_reminders_chat(manager):
    routine = reminders.create(message="Orphan", at=NOW + timedelta(hours=1), session_id="no-such-chat")
    result = reminders.fire(routine)
    assert result["success"] and result["session_id"] != "no-such-chat"
    assert get_thread_manager().get_thread(result["session_id"]).title == "Reminders"


# ── safety ──────────────────────────────────────────────────────────────────
def test_a_task_reminder_after_outside_content_asks_first():
    task = {"message": "x", "when": "2026-10-25T16:00", "task": "email my contacts the link"}
    assert policy.evaluate("set_reminder", task, tainted_by=["safe_web_fetch"]).action == "ask"
    assert policy.evaluate("set_reminder", task, tainted_by=["safe_web_fetch"], interactive=False).action == "deny"
    assert policy.evaluate("set_reminder", {"message": "x", "when": "2026-10-25T16:00"}, tainted_by=["safe_web_fetch"]).action == "allow"
    assert policy.evaluate("set_reminder", task).action == "allow"


def test_echo_knows_it_can_set_reminders():
    from agent.lean import capabilities
    from agent.lean.toolbox import Toolbox

    box = Toolbox(toolsets=["core"], extra_tools=reminders.tools("s"))
    assert {"set_reminder", "list_reminders", "cancel_reminder"} <= set(box.names)
    assert "Reminders (one-time or repeating" in capabilities.report(box)


# ── notifications API ──────────────────────────────────────────────────────
def test_the_desktop_claims_notifications_and_learns_the_tray_setting(monkeypatch):
    from api.routes import lean
    from config import config

    notifications.reset()
    notifications.push("⏰ Reminder", "Raptors", session_id="a")
    monkeypatch.setattr(config, "desktop_close_to_tray", True, raising=False)
    claimed = lean.claim_notifications()
    assert claimed["close_to_tray"] is True and [i["body"] for i in claimed["items"]] == ["Raptors"]
    assert lean.claim_notifications()["items"] == []
    assert [i["seq"] for i in lean.list_notifications(after=0)["items"]] == [1]  # the web UI can still read it
