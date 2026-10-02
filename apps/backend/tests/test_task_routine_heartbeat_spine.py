from pathlib import Path
from types import SimpleNamespace

import pytest


def test_product_task_identity_is_stable_and_state_is_atomic(tmp_path: Path) -> None:
    from agent.task_store import TaskStore

    path = tmp_path / "todos.json"
    store = TaskStore(path)
    first = store.create(title="Daily brief", idempotency_key="routine:r1:bucket")
    second = store.create(title="Duplicate retry", idempotency_key="routine:r1:bucket")
    assert second.id == first.id

    updated = store.update(first.id, status="complete", verification={"verified": True})
    assert updated is not None and updated.status == "complete"
    reloaded = TaskStore(path).get(first.id)
    assert reloaded is not None and reloaded.verification == {"verified": True}
    assert not list(tmp_path.glob("*.tmp.*"))


def test_corrupt_product_tasks_fail_closed_with_recovery_copy(tmp_path: Path) -> None:
    from agent.task_store import TaskStore

    path = tmp_path / "todos.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(RuntimeError, match="authoritative file was not overwritten"):
        TaskStore(path)
    assert path.read_text(encoding="utf-8") == "{broken"
    assert list((tmp_path / "corrupt-state").rglob("RECOVERY.txt"))


def test_routine_requires_coordinator_and_records_callback_result(tmp_path: Path) -> None:
    from agent.routines import RoutineManager

    manager = RoutineManager(tmp_path / "routines")
    routine = manager.create_routine(name="Governed", trigger_type="manual")
    assert manager.run_routine(routine.id) is False
    blocked = manager.get_routine(routine.id)
    assert blocked is not None
    assert blocked.last_result_status == "failed"
    assert "coordinator" in blocked.last_error.lower()

    manager.set_run_callback(lambda _routine: {"success": True, "task_id": "task-1"})
    assert manager.run_routine(routine.id) is True
    completed = manager.get_routine(routine.id)
    assert completed is not None
    assert completed.last_task_id == "task-1"
    assert completed.last_result_status == "complete"


def test_heartbeat_tick_runs_one_lean_turn_with_the_pulse_and_routes_the_reply(monkeypatch) -> None:
    """Since 8.1 the heartbeat is one lean turn in its own chat; a reply is routed, silence is not."""
    from agent.heartbeat import HeartbeatManager
    import agent.heartbeat as heartbeat_module
    import agent.lean.runtime as lean_runtime

    turns = []
    routed = []

    def fake_run_lean_query(agent, **kwargs):
        turns.append(kwargs)
        return {"response": replies.pop(0), "success": True, "execution_id": f"exec-{len(turns)}"}

    replies = ["A useful update", "NO_HEARTBEAT"]
    monkeypatch.setattr(lean_runtime, "run_lean_query", fake_run_lean_query)
    monkeypatch.setattr(heartbeat_module, "route_message", lambda text, channels, label="": routed.append((text, channels)))
    monkeypatch.setattr(heartbeat_module, "_NO_HEARTBEAT_SENTINEL", "NO_HEARTBEAT", raising=False)

    manager = HeartbeatManager(
        agent=object(),
        interval_minutes=30,
        prompt="Check now",
        channels=["web", "email"],
        project_id="project-a",
        session_id="session-a",
    )
    monkeypatch.setattr(manager, "_gather_system_pulse", lambda: "all systems nominal")
    manager._tick()

    assert len(turns) == 1
    assert turns[0]["session_id"] == "session-a"
    assert turns[0]["source"] == "heartbeat"
    assert "all systems nominal" in turns[0]["message"] and "Check now" in turns[0]["message"]
    assert routed == [("A useful update", ["web", "email"])]

    manager._tick()  # the agent said there is nothing to report
    assert len(turns) == 2
    assert len(routed) == 1


def test_background_channel_router_never_calls_legacy_external_senders(monkeypatch) -> None:
    import agent.heartbeat as heartbeat

    assert not hasattr(heartbeat, "_route_discord")
    assert not hasattr(heartbeat, "_route_telegram")
    assert not hasattr(heartbeat, "_route_email")
    assert not hasattr(heartbeat, "_route_whatsapp")
    heartbeat.route_message("hello", ["web", "discord", "telegram", "email", "whatsapp"])
