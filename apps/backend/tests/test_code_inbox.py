"""Code review state follows execution evidence, never an assistant's success claim."""
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from agent import echo_code, state, threads
from agent.lean.approvals import get_approval_broker
from api.routes.echo_code import router


@pytest.fixture
def inbox(tmp_path, monkeypatch):
    manager = threads.ThreadManager(tmp_path / "threads.json")
    store = state.StateStore(tmp_path / "state")
    monkeypatch.setattr(threads, "get_thread_manager", lambda: manager)
    monkeypatch.setattr(state, "_state_store", store)
    thread = manager.create_thread(surface="code", title="Code · fixture")
    return store, manager, thread.thread_id


def finish(store, tid, check="checks_pass", **kwargs):
    turn = store.create_execution(thread_id=tid, query="Fix the fixture", **kwargs)
    store.update_execution(turn.id, status="completed", success=True,
                           metadata={"verification": {"code": {"status": check}}})
    store.update_thread_state(tid, active_turn_id="", current_execution_id="")
    return turn


@pytest.mark.parametrize("check,expected", [("checks_pass", "ready_to_review"), ("verified", "ready_to_review"),
    ("failing", "needs_attention"), ("blocked", "needs_attention"), ("partial", "needs_attention"),
    ("unverified", "needs_attention")])
def test_finished_turn_uses_the_ledger_not_success(inbox, check, expected):
    store, _, tid = inbox
    finish(store, tid, check)
    projected = store.code_inbox_snapshot(tid)
    assert projected["state"] == expected
    assert projected["verification"] != "Not checked"


def test_choices_persist_but_cannot_hide_new_work_or_rewrite_checks(inbox, tmp_path):
    store, _, tid = inbox
    turn = finish(store, tid, "failing")
    initial = store.code_inbox_snapshot(tid)
    assert echo_code.triage_session(tid, "snoozed", initial["activity"])["state"] == "snoozed"
    restored = state.StateStore(tmp_path / "state")
    assert restored.code_inbox_snapshot(tid)["state"] == "snoozed"
    assert restored.code_inbox_snapshot(tid)["verification"] == "Checks failed"
    assert store.get_execution(turn.id).metadata["verification"]["code"]["status"] == "failing"
    newer = store.create_execution(thread_id=tid, query="Try another fix")
    assert store.code_inbox_snapshot(tid)["state"] == "working"
    with pytest.raises(echo_code.RuntimeUnavailable, match="changed or is working"):
        echo_code.triage_session(tid, "settled", initial["activity"])
    store.update_execution(newer.id, status="canceled")
    assert store.code_inbox_snapshot(tid)["state"] == "needs_attention"


def test_approval_and_question_take_priority_over_running(inbox):
    store, _, tid = inbox
    store.create_execution(thread_id=tid)
    broker = get_approval_broker()
    question = broker.open_question(session_id=tid, request_id="fixture", question="Which package?", options=["A", "B"], allow_other=True)
    try:
        value = echo_code.triage_session
        projected = store.code_inbox_snapshot(tid, waiting=True)
        assert projected["state"] == "needs_attention" and not projected["can_triage"]
        with pytest.raises(echo_code.RuntimeUnavailable):
            value(tid, "snoozed", projected["activity"])
    finally:
        broker.resolve(question.id, "A")
        import threading
        broker.wait(question, threading.Event())


def test_queue_needs_attention_and_read_does_not_create_state(inbox):
    store, _, tid = inbox
    before = len(store.list_thread_states())
    assert store.code_inbox_snapshot("unknown")["state"] == "settled"
    assert len(store.list_thread_states()) == before
    finish(store, tid)
    store.update_thread_state(tid, queued_turns=[{"id": "q1", "message": "Next task"}], queue_paused=True)
    value = store.code_inbox_snapshot(tid)
    assert value["state"] == "needs_attention" and value["reason"] == "Queued follow-up waiting"
    assert not value["can_triage"]


def test_recovered_question_cannot_be_hidden_by_review_choices(inbox):
    store, _, tid = inbox
    finish(store, tid)
    coordinator = store.coordination
    work = coordinator.create(objective="Clarify package", agent_id="code", session_id=tid, tools=["ask_user"])
    context = coordinator.claim(work["id"], session_id=tid, agent_id="code")
    coordinator.question(context, "recovered-question", {"question": "Which package?", "options": ["A", "B"]})
    projected = store.code_inbox_snapshot(tid)
    assert projected["state"] == "needs_attention" and not projected["can_triage"]
    assert tid in echo_code._waiting_sessions()
    with pytest.raises(echo_code.RuntimeUnavailable):
        echo_code.triage_session(tid, "snoozed", projected["activity"])


def test_inbox_route_validates_activity_and_refuses_chat(inbox):
    store, manager, tid = inbox
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    chat = manager.create_thread(title="Ordinary Chat")
    choice = {"action": "snoozed", "activity": "0" * 32}
    assert client.post(f"/echo-code/sessions/{chat.thread_id}/inbox", json=choice).status_code == 404
    assert client.post(f"/echo-code/sessions/{tid}/inbox", json={**choice, "action": "running"}).status_code == 422
    assert client.post(f"/echo-code/sessions/{tid}/inbox", json=choice).status_code == 409
    current = store.code_inbox_snapshot(tid)
    result = client.post(f"/echo-code/sessions/{tid}/inbox", json={**choice, "activity": current["activity"]})
    assert result.status_code == 200 and result.json()["state"] == "snoozed"


def test_overview_adds_evidence_without_provider_or_transcript_reads(inbox, monkeypatch, tmp_path):
    store, _, tid = inbox
    item = SimpleNamespace(id="p", name="Fixture", workspace_root=str(tmp_path), trust_state="trusted", archived=False)
    import agent.projects
    monkeypatch.setattr(agent.projects, "get_project_manager", lambda: SimpleNamespace(list_projects=lambda: [item]))
    finish(store, tid, "failing", active_project_id="p")
    row = echo_code.overview()["projects"][0]["sessions"][0]
    assert row["thread_id"] == tid and row["inbox"]["state"] == "needs_attention"
    assert "Fix the fixture" not in str(row["inbox"])
