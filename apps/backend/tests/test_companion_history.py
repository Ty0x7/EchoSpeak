"""Scoped, read-only recovery and commentary regression tests."""

import json
import time
from datetime import datetime, timezone
from types import SimpleNamespace

from agent.companion import commentary, history, native, recap
from agent.companion.observe import CompanionStore, activity, normalise

REPORT = ("## Summary\nAdded a regression for the harness window.\n\n## Verification\n- Ran eight checks.\n\n"
          "## Remaining\n- The timing audit still needs review.\n")


def row(at, kind, **fields):
    return {"timestamp": datetime.fromtimestamp(at, timezone.utc).isoformat(), "type": kind, **fields}


def codex(at, kind, **fields):
    return row(at, "event_msg", payload={"type": kind, **fields})


def write(path, *rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_codex_recovery_preserves_scope_and_recovers_active_work(tmp_path, monkeypatch):
    now = time.time()
    root = str(tmp_path / "project")
    other = str(tmp_path / "other")
    path = tmp_path / "rollout-test.jsonl"
    write(path, row(now - 3600, "session_meta", payload={"id": "c1", "cwd": root}),
          codex(now - 2400, "task_started", turn_id="one"),
          codex(now - 2399, "item_completed", item={"type": "UserMessage", "content": [
              {"type": "text", "text": "Verify the harness window"}]}),
          codex(now - 120, "task_complete", turn_id="one", last_agent_message=REPORT),
          codex(now - 70, "task_started", turn_id="two"),
          row(now - 69, "turn_context", payload={"cwd": other, "turn_id": "two"}),
          codex(now - 65, "task_complete", turn_id="two", last_agent_message="unrelated project report"),
          row(now - 40, "turn_context", payload={"cwd": root + "/backend", "turn_id": "two"}),
          codex(now - 30, "item_started", item={"type": "CommandExecution", "id": "cmd",
                "command": "python -m pytest /private/path", "aggregated_output": "private output"}))
    scope = {"capture_prompts": True}
    ids = lambda cwd: "p1" if cwd.startswith(root) else "p2"
    events = list(history.read_events(path, "codex", scope, now, lambda agent, cwd: ids(cwd)))
    store = CompanionStore(tmp_path / "db", tmp_path / "spool")
    for event in events:
        assert store.add(event)
    assert len(events) == 5
    assert all("private output" not in json.dumps(e) and "/private/path" not in json.dumps(e) and e["cwd"] == root for e in events)
    assert store.sessions(now)[0]["state"] == "operation"
    answer = recap.answer("agent:codex", store, activity(store, include_echo=False, now=now))
    assert "running a test command" in answer and "timing audit" in answer
    assert not store.add(events[2])
    monkeypatch.setattr(history.knowledge, "_project_for", lambda cwd: (ids(cwd), cwd))
    assert "timing audit" not in recap.latest_report_answer(store, "codex", project_id="p2")


def test_claude_stop_deduplicates_against_history_in_both_orders(tmp_path):
    path = tmp_path / "cc.jsonl"
    cwd = str(tmp_path)
    now = time.time()
    write(path, row(now - 80, "user", uuid="u", sessionId="cc", cwd=cwd, message={"content": "Verify harness"}),
          row(now - 60, "assistant", uuid="a", sessionId="cc", cwd=cwd,
              message={"stop_reason": "end_turn", "content": [{"type": "text", "text": REPORT}]}),
          row(now - 40, "assistant", uuid="b", sessionId="cc", cwd=cwd, isSidechain=True,
              message={"stop_reason": "end_turn", "content": [{"type": "text", "text": "Hidden helper"}]}))
    scope = {"capture_prompts": False}
    assert list(history.read_events(path, "claude-code", scope, now, lambda *a: "")) == []
    events = list(history.read_events(path, "claude-code", scope, now, lambda *a: "p"))
    assert len(events) == 2 and events[0]["text"] == ""
    report = events[-1]
    mirrored = normalise({"agent": "claude-code", "received_at": now - 58, "event": {"hook_event_name": "Stop",
                          "session_id": "cc", "prompt_id": "different", "cwd": cwd, "last_assistant_message": REPORT}})
    for idx, pair in enumerate(((report, mirrored), (mirrored, report))):
        store = CompanionStore(tmp_path / f"db-{idx}", tmp_path / "spool")
        assert store.add(pair[0]) and not store.add(pair[1])
        assert store.sessions(now)[0]["state"] == "completed"


def test_new_long_task_progress_announces_once(monkeypatch):
    for name, value in {"companion_enabled": True, "companion_speak": True, "companion_soul_style": "templates",
                        "companion_progress_minutes": 30, "companion_quiet_hours": ""}.items():
        monkeypatch.setattr(commentary.config, name, value)
    spoken = []
    c = commentary.Commentary(speak=lambda text, **kw: spoken.append(text) or {"status": "queued"})
    now = time.time()
    evidence = {"id": "op", "agent": "codex", "session_id": "s", "kind": "operation_done", "at": now,
                "task": "verify the harness", "meta": {"operation": "a test command"}, "turn_started_at": now - 1900}
    assert not c.handle({**evidence, "late": True})
    assert c.handle(evidence)["spoken"]
    assert not c.handle({**evidence, "id": "op2"})
    assert len(spoken) == 1 and "completed a test command" in spoken[0]


def test_quiet_research_workers_stay_visible_and_distinct(tmp_path, monkeypatch):
    store = CompanionStore(tmp_path / "db", tmp_path / "spool")
    now = time.time()
    worker = SimpleNamespace(session_id="room", execution_id="run", _active_project=lambda: "")
    monkeypatch.setattr(native, "_publish", store.add)
    for name in ("Glados", "Jarvis"):
        native.worker_status(worker, name, 1, "Check the current test results", "running")
    snapshot = activity(store, include_echo=False, now=now + 50 * 60)
    answer = recap.answer("other_agents", store, snapshot)
    assert len(snapshot["sessions"]) == 2
    assert "Glados" in answer and "Jarvis" in answer and "unsure" in answer
