"""Real Lean/API/scheduler paths with scripted responses; no model/network calls."""
from __future__ import annotations
import json
import threading
from types import SimpleNamespace
from concurrent.futures import Future

import pytest

from agent import state, routines, notifications, delivery
from agent.coordination import Coordinator, work_scope
from agent.coordination_service import review, responsibility_state, resume
from agent.lean import runtime, personas, settings, loop, automations
from agent.lean.provider import ModelTurn, ToolCall, Endpoint
from agent.lean.toolbox import NativeTool, Toolbox
from api.routes import lean
from tests.test_lean_runtime import ScriptedClient


class Client(ScriptedClient):
    def close(self):
        pass


@pytest.fixture
def env(tmp_path, monkeypatch):
    store = state.StateStore(tmp_path / "state")
    monkeypatch.setattr(state, "_state_store", store)
    roster = personas.PersonaStore(tmp_path / "agents.json")
    monkeypatch.setattr(personas, "_STORE", roster)
    manager = routines.RoutineManager(tmp_path / "routines")
    monkeypatch.setattr(routines, "_routine_manager", manager)
    monkeypatch.setattr(settings, "learning_mode", lambda: "off")
    monkeypatch.setattr(runtime.LeanSession, "_endpoint_for", lambda *a, **k: Endpoint(provider="fixture", model="4b", base_url="http://127.0.0.1:1", api_key="", local=True))
    monkeypatch.setattr(runtime.LeanSession, "_recall", lambda *a, **k: [])
    notifications.reset()
    return store, roster, manager


def install_clients(monkeypatch, scripts):
    clients = {k: Client(v) for k, v in scripts.items()}
    monkeypatch.setattr(runtime.LeanSession, "_client_for", lambda self, p, routing=False: clients[p.id])
    return clients


def run(session="native", **kwargs):
    return runtime.run_lean_query(SimpleNamespace(memory=None), message="Inspect the task and report the result.",
        session_id=session, request_id="request-" + session, emit=lambda _: None, cancel=threading.Event(), **kwargs)


def test_direct_work_records_attempt_tools_and_provenance(env, monkeypatch):
    store, _, _ = env
    native = runtime.LeanSession._native_tools
    monkeypatch.setattr(runtime.LeanSession, "_native_tools", lambda self, *a, **k: native(self, *a, **k) + [
        NativeTool("fixture_read", "read", {"type": "object", "properties": {}}, lambda _: "Observed value: 42", parallel_safe=True, always=True)])
    install_clients(monkeypatch, {"echo": [ModelTurn(tool_calls=[ToolCall("r1", "fixture_read", "{}")]), ModelTurn(content="Observed value is 42.")]})
    work = store.coordination.create(objective="Inspect value", agent_id="echo", session_id="native")
    out = run(work_id=work["id"])
    assert out["work"]["status"] == "completed" and out["work"]["evidence"]
    assert store.coordination.view("actions")[0]["status"] == "completed"
    assert store.coordination.view("attempts")[0]["model"]["agent_id"] == "echo"
    items = store.list_items(out["execution_id"])
    assert any(i.payload.get("work_id") == work["id"] for i in items)


def test_echo_requests_specialist_help_with_child_grant(env, monkeypatch):
    store, _, _ = env
    install_clients(monkeypatch, {"echo": [ModelTurn(tool_calls=[ToolCall("d1", "delegate_to_agent", json.dumps({"agent": "Jarvis", "task": "Compare the supplied numbers"}))]), ModelTurn(content="Jarvis returned the comparison.")],
        "scout": [ModelTurn(content="The supplied numbers are equal.")]})
    root = store.coordination.create(objective="Compare supplied numbers", agent_id="echo", session_id="native")
    out = run(work_id=root["id"])
    children = [w for w in store.coordination.view("works") if w["parent_id"] == root["id"]]
    assert children and children[0]["agent_id"] == "scout" and children[0]["status"] == "completed"
    assert set(children[0]["tools"]) <= set(out["work"]["tools"])


def test_review_is_explicit_idempotent_and_decline_leaves_no_entity(env):
    store, roster, manager = env
    spec = {"name": "Archivist", "purpose": "Check project notes", "toolsets": ["web", "memory"], "context_scope": "session", "budget": {"tokens": 2000}}
    proposal = store.coordination.proposal("specialist", spec)
    assert roster.find_by_name("Archivist") is None
    accepted = lean.review_native_proposal(proposal["id"], lean.WorkDecision(action="accept", revision=1))
    assert review(proposal["id"], accept=True, revision=1)["entity_id"] == accepted["entity_id"]
    agent = roster.get(accepted["entity_id"])
    assert agent.context_scope == "session" and agent.budget["tokens"] == 2000
    declined = store.coordination.proposal("specialist", {**spec, "name": "Not created"})
    review(declined["id"], accept=False, revision=1)
    assert roster.find_by_name("Not created") is None and manager.list_routines() == []


def test_schedule_occurrence_resume_pause_and_delivery_without_rerun(env, monkeypatch):
    store, _, manager = env
    install_clients(monkeypatch, {"scout": [ModelTurn(content="The scheduled check is complete.")]})
    proposal = store.coordination.proposal("responsibility", {"agent_id": "scout", "objective": "Inspect the briefing", "trigger": "schedule", "schedule": "0 9 * * *", "channels": ["desktop"]})
    accepted = review(proposal["id"], accept=True, revision=1)
    responsibility = accepted["entity_id"]
    routine = manager.get_routine(responsibility)
    callback = automations.routine_runner(lambda _: SimpleNamespace(memory=None))
    first = callback(routine)
    second = callback(routine)
    assert first["success"] and second["success"] and first["work_id"] == second["work_id"]
    assert len(store.coordination.view("attempts")) == 1
    assert store.coordination.view("responsibilities")[0]["status"] == "active"
    assert store.coordination.view("deliveries")[0]["status"] == "accepted"
    responsibility_state(responsibility, False)
    assert not manager.get_routine(responsibility).enabled
    responsibility_state(responsibility, True)
    assert manager.get_routine(responsibility).enabled


def test_unattended_approval_survives_restart_and_resumes_in_another_chat(env, monkeypatch):
    store, _, _ = env
    native = runtime.LeanSession._native_tools
    effects = []
    monkeypatch.setattr(runtime.LeanSession, "_native_tools", lambda self, *a, **k: native(self, *a, **k) + [NativeTool("fixture_action", "act", {"type": "object", "properties": {}}, lambda _: effects.append("done") or "done", always=True)])
    monkeypatch.setattr(loop, "tool_needs_approval", lambda entry, name, args: (name == "fixture_action", "Review this exact action"))
    install_clients(monkeypatch, {"echo": [ModelTurn(tool_calls=[ToolCall("a1", "fixture_action", "{}")]), ModelTurn(content="Waiting for owner review.")]})
    out = run(source="routine")
    assert out["work"]["status"] == "waiting_approval" and effects == []
    store.coordination = Coordinator(store)
    pending = lean.list_approvals("")["items"][0]
    lean.decide_approval(pending["id"], lean.ApprovalDecisionRequest(decision="allow"))
    install_clients(monkeypatch, {"echo": [ModelTurn(tool_calls=[ToolCall("a2", "fixture_action", "{}")]), ModelTurn(content="The approved action completed.")]})
    continued = resume(out["work"]["id"], "another-chat", agent_factory=lambda _: SimpleNamespace(memory=None))
    assert effects == ["done"] and continued["work"]["status"] == "completed"
    assert set(continued["work"]["sessions"]) == {"native", "another-chat"}


def test_question_recovery_and_single_use_answer(env):
    store, _, _ = env
    coord = store.coordination
    work = coord.create(objective="Choose output", agent_id="echo", tools=["ask_user"])
    ctx = coord.claim(work["id"])
    args = {"question": "Which format?", "options": ["HTML", "Markdown"]}
    coord.question(ctx, "q1", args)
    coord.finish(ctx, status="completed")
    store.coordination = Coordinator(store)
    lean.decide_approval("q1", lean.ApprovalDecisionRequest(decision="HTML"))
    ctx = store.coordination.claim(work["id"])
    assert store.coordination.consume_answer(ctx, args) == "HTML"
    assert store.coordination.consume_answer(ctx, args) is None


def test_resource_conflict_and_unknown_write_prevent_second_writer(env):
    store, _, _ = env
    coord = store.coordination
    contexts = [coord.claim(coord.create(objective="Edit", agent_id=a, project_id="project", tools=["file_write"])["id"]) for a in ("echo", "forge")]
    intent = coord.action_intent(contexts[0], "w1", "file_write", {"path": "a.txt"}, read_only=False)
    with pytest.raises(PermissionError, match="Resource"):
        coord.action_intent(contexts[1], "w2", "file_write", {"path": "b.txt"}, read_only=False)
    coord.action_result(intent, ok=False, outcome="lost receipt")
    with pytest.raises(PermissionError, match="Resource"):
        coord.action_intent(contexts[1], "w3", "file_write", {}, read_only=False)


def test_delivery_receipt_and_failure_do_not_repeat_inference(env, monkeypatch):
    store, _, _ = env
    from config import config
    import discord_bot
    monkeypatch.setattr(config, "discord_bot_owner_id", "123")
    monkeypatch.setattr(config, "allow_discord_bot", True)
    monkeypatch.setattr(delivery, "coordinator", lambda: store.coordination)
    future = Future()
    def send(*args, on_receipt):
        future.add_done_callback(on_receipt)
        return True
    monkeypatch.setattr(discord_bot, "queue_discord_dm", send)
    row = store.coordination.enqueue(key="result", channel="discord", destination="123", text="Result", agent_id="scout")
    assert delivery.dispatch(row["id"])["status"] == "sending"
    future.set_result(SimpleNamespace(id="receipt-1"))
    assert delivery.dispatch(row["id"])["status"] == "confirmed"
    monkeypatch.setattr(config, "discord_bot_owner_id", "456")
    stale = store.coordination.enqueue(key="stale", channel="discord", destination="123", text="Private", agent_id="scout")
    assert delivery.dispatch(stale["id"])["status"] == "failed"


def test_specialist_workers_inherit_parent_work_and_bound_reads(env, monkeypatch):
    from agent.lean import swarm, provider
    from tests.test_swarm import Scripted, _research, _lead_toolbox, _session
    store, _, _ = env
    coord = store.coordination
    root = coord.create(objective="Research", agent_id="scout", tools=["safe_web_fetch", "web_search"], budget={"children": 2})
    ctx = coord.claim(root["id"])
    Scripted.scripts = {"first": _research("first", "https://example.test/a", "A"), "second": _research("second", "https://example.test/b", "B")}
    Scripted.fail, Scripted.calls = set(), []
    monkeypatch.setattr(provider, "ChatClient", Scripted)
    with work_scope(ctx):
        group = swarm.Swarm(session=_session(), persona=personas.AgentPersona(id="scout", name="Jarvis"), lead_toolbox=_lead_toolbox(), cancel=threading.Event())
        group.run([{"goal": "first"}, {"goal": "second"}])
    children = [w for w in coord.view("works") if w["parent_id"] == root["id"]]
    assert len(children) == 2 and all(w["status"] == "completed" and w["budget"]["children"] == 0 for w in children)
    assert all(set(w["tools"]) <= set(root["tools"]) for w in children)


def test_custom_specialist_answers_through_the_shared_harness(env, monkeypatch):
    store, roster, _ = env
    proposal = store.coordination.proposal("specialist", {"name": "Archivist", "purpose": "Read supplied notes", "context_scope": "session", "toolsets": ["memory"]})
    approved = review(proposal["id"], accept=True, revision=1)
    clients = install_clients(monkeypatch, {approved["entity_id"]: [ModelTurn(content="I can organize the supplied notes.")]})
    result = run(persona_id=approved["entity_id"])
    assert result["success"] and result["messages"][0]["agent_id"] == approved["entity_id"]
    assert len(clients[approved["entity_id"]].calls) == 1


def test_scoped_agent_notes_and_pending_proposal_cannot_grant_authority(env):
    store, _, _ = env
    coord = store.coordination
    coord.note("scout", "project-a", "Research notes")
    assert coord.note("forge", "project-a") == "" and coord.note("scout", "project-b") == ""
    from agent.coordination_service import validate_proposal
    with pytest.raises(ValueError):
        validate_proposal("specialist", {"name": "All powerful", "purpose": "Do everything", "toolsets": ["all"]})
    work = coord.create(objective="Read", agent_id="echo", tools=["file_read"])
    ctx = coord.claim(work["id"])
    coord.proposal("specialist", {"name": "Writer", "purpose": "Write", "toolsets": ["core"]})
    with pytest.raises(PermissionError, match="original work grant"):
        coord.action_intent(ctx, "a", "file_write", {}, read_only=False)


def test_relative_resource_approval_is_rechecked_against_original_project(env, tmp_path):
    store, _, _ = env
    coord = store.coordination
    target = tmp_path / "relative.txt"
    target.write_text("old", encoding="utf-8")
    work = coord.create(objective="Replace", agent_id="echo", tools=["file_write"])
    ctx = coord.claim(work["id"])
    coord.bind(ctx, ["file_write"], project_root=str(tmp_path))
    coord.approval(ctx, approval_id="relative", tool="file_write", args={"path": "relative.txt"}, summary="Replace", reason="Review")
    target.write_text("changed externally", encoding="utf-8")
    with pytest.raises(PermissionError, match="stale"):
        coord.decide("relative", "allow")
    assert coord.decide("relative", "deny")["status"] == "denied"


def test_interrupted_approved_configuration_recovers_without_hidden_schedule(env, monkeypatch):
    store, _, manager = env
    proposal = store.coordination.proposal("responsibility", {"agent_id": "scout", "objective": "Review project", "trigger": "event"})
    original = manager.update_routine
    def fail(*a, **k):
        raise OSError("injected activation failure")
    monkeypatch.setattr(manager, "update_routine", fail)
    with pytest.raises(OSError):
        review(proposal["id"], accept=True, revision=1)
    assert len(manager.list_routines()) == 1 and not manager.list_routines()[0].enabled
    monkeypatch.setattr(manager, "update_routine", original)
    from agent.coordination_service import recover_reviews
    recover_reviews()
    assert len(manager.list_routines()) == 1 and manager.list_routines()[0].enabled


def test_uncertain_delivery_is_never_blindly_requeued(env, monkeypatch):
    store, _, _ = env
    from config import config
    import discord_bot
    monkeypatch.setattr(config, "discord_bot_owner_id", "123")
    monkeypatch.setattr(config, "allow_discord_bot", True)
    submitted = []
    def send(*args, on_receipt):
        submitted.append(args)
        future = Future()
        future.set_exception(TimeoutError("Receipt lost"))
        on_receipt(future)
        return True
    monkeypatch.setattr(discord_bot, "queue_discord_dm", send)
    row = store.coordination.enqueue(key="uncertain", channel="discord", destination="123", text="Result", agent_id="scout")
    assert delivery.dispatch(row["id"])["status"] == "unknown"
    assert delivery.dispatch(row["id"])["status"] == "unknown" and len(submitted) == 1


def test_completed_effect_is_not_repeated_after_resuming(env):
    store, _, _ = env
    coord = store.coordination
    row = coord.create(objective="Write result", agent_id="echo", tools=["file_write"])
    first = coord.claim(row["id"])
    action = coord.action_intent(first, "w1", "file_write", {"path": "out.txt", "content": "done"}, read_only=False)
    coord.action_result(action, ok=True, outcome="ok")
    row = coord.finish(first, status="interrupted")
    coord.transition(row["id"], "ready", revision=row["revision"])
    second = coord.claim(row["id"])
    with pytest.raises(PermissionError, match="earlier attempt"):
        coord.action_intent(second, "w2", "file_write", {"path": "out.txt", "content": "done"}, read_only=False)


def test_event_responsibility_uses_authenticated_stable_occurrences(env, monkeypatch):
    store, _, _ = env
    from api import deps
    monkeypatch.setattr(deps, "get_agent", lambda _: SimpleNamespace(memory=None))
    install_clients(monkeypatch, {"scout": [ModelTurn(content="The approved event check is complete.")]})
    proposal = store.coordination.proposal("responsibility", {"agent_id": "scout", "objective": "Review event", "trigger": "event"})
    from agent import coordination_service
    assert coordination_service.coordinator() is store.coordination, (coordination_service.coordinator().path, store.coordination.path)
    reviewed = review(proposal["id"], accept=True, revision=1)
    request = lean.WorkDecision(action="event", reconciliation="source-event-123")
    first = lean.native_responsibility(reviewed["entity_id"], request)
    second = lean.native_responsibility(reviewed["entity_id"], request)
    assert first["success"] and first["work_id"] == second["work_id"]
    assert len(store.coordination.view("attempts")) == 1


def test_background_process_retains_resource_against_other_work_until_exit_is_observed(env):
    """A running process holds its resource against *other* work; its own work carries on
    (review R3: "start the dev server, then fix App.tsx" was impossible in one request)."""
    store, _, _ = env
    coord = store.coordination
    row = coord.create(objective="Run tests", agent_id="echo", project_id="project", tools=["terminal", "process_output"])
    ctx = coord.claim(row["id"])
    action = coord.action_intent(ctx, "start", "terminal", {"command": "tests"}, read_only=False)
    coord.action_result(action, ok=True, outcome="ok", effects=[{"kind": "process", "running": "p1"}])
    again = coord.action_intent(ctx, "again", "terminal", {"command": "other"}, read_only=False)  # its own work: allowed
    coord.action_result(again, ok=True, outcome="ok", uncertain=False)
    other = coord.claim(coord.create(objective="Edit", agent_id="forge", project_id="project", tools=["terminal"])["id"])
    with pytest.raises(PermissionError, match="still running"):
        coord.action_intent(other, "elsewhere", "terminal", {"command": "build"}, read_only=False)
    coord.process_observed(ctx, "p1", success=True)
    coord.action_intent(other, "after-exit", "terminal", {"command": "build"}, read_only=False)
    # The work itself finishes; a long-running process doesn't keep it waiting.
    assert coord.finish(ctx, status="completed")["status"] == "completed"


def test_loop_returns_previous_effect_as_refusal_without_running_it(env):
    store, _, _ = env
    coord = store.coordination
    row = coord.create(objective="Effect", agent_id="echo", tools=["fixture_effect"])
    first = coord.claim(row["id"])
    action = coord.action_intent(first, "first", "fixture_effect", {}, read_only=False)
    coord.action_result(action, ok=True, outcome="ok")
    row = coord.finish(first, status="interrupted")
    coord.transition(row["id"], "ready", revision=row["revision"])
    ctx = coord.claim(row["id"])
    executed = []
    box = Toolbox(toolsets=["none"])
    box.native["fixture_effect"] = NativeTool("fixture_effect", "effect", {"type": "object", "properties": {}}, lambda _: executed.append(True))
    client = Client([ModelTurn(tool_calls=[ToolCall("second", "fixture_effect", "{}")]), ModelTurn(content="The earlier effect was recorded; I did not repeat it.")])
    turn = loop.LeanTurn(client=client, persona=personas.AgentPersona(id="echo", name="Echo"), system_prompt="system", history=[], toolbox=box,
        session_id="fixture", request_id="retry", execution_id="fixture", emit=lambda _: None, cancel=threading.Event(), persist_tool_runs=False)
    turn.work_context = ctx
    result = turn.run("Inspect the recorded result")
    assert executed == [] and result.success
    assert any("already completed" in str(m.get("content", "")) for m in client.calls[-1] if m["role"] == "tool")


def test_specialist_memory_save_cannot_overwrite_account_memory(env):
    _, roster, _ = env
    captured = []
    class Memory:
        _records = {}
        def add_memory_item(self, *args, **kwargs):
            captured.append(kwargs)
            return ""
    session = runtime.LeanSession(agent=SimpleNamespace(memory=Memory()), session_id="scoped", request_id="scoped",
        emit=lambda _: None, cancel=threading.Event(), source="web")
    tool = next(t for t in session._native_tools(roster.get("scout"), 0) if t.name == "memory_save")
    tool.func({"fact": "The project uses Python.", "key": "language"})
    assert captured[0]["scope"] == "session" and captured[0]["semantic_key"] == "agent:scout:language"


def test_chat_question_creates_no_work_but_a_real_write_does(env, monkeypatch):
    """Review R4: a work record comes from a state-changing tool call, not from words in the message."""
    store, _, _ = env
    native = runtime.LeanSession._native_tools
    writes = []
    monkeypatch.setattr(runtime.LeanSession, "_native_tools", lambda self, *a, **k: native(self, *a, **k) + [
        NativeTool("fixture_read", "read", {"type": "object", "properties": {}}, lambda _: "Lima", parallel_safe=True, always=True),
        NativeTool("fixture_write", "write", {"type": "object", "properties": {}}, lambda _: writes.append(1) or "saved", always=True)])
    monkeypatch.setattr(loop, "tool_needs_approval", lambda entry, name, args: (False, ""))
    install_clients(monkeypatch, {"echo": [ModelTurn(tool_calls=[ToolCall("r1", "fixture_read", "{}")]), ModelTurn(content="Lima.")]})
    asked = runtime.run_lean_query(SimpleNamespace(memory=None), message="Can you add up how many letters are in Lima?",
        session_id="ask", request_id="request-ask", emit=lambda _: None, cancel=threading.Event())
    assert "work" not in asked and store.coordination.view("works") == []
    install_clients(monkeypatch, {"echo": [ModelTurn(tool_calls=[ToolCall("w1", "fixture_write", "{}")]), ModelTurn(content="Saved.")]})
    wrote = runtime.run_lean_query(SimpleNamespace(memory=None), message="What do you think? Keep it.",
        session_id="write", request_id="request-write", emit=lambda _: None, cancel=threading.Event())
    assert writes == [1] and wrote["work"]["status"] == "completed"
    assert store.coordination.view("deliveries") == []  # the reply streamed into the chat; no outbox row
