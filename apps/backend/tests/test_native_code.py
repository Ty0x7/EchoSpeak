"""Native Echo Code as its own environment: model-first like Chat, built for software engineering.

Chat, Code and Agents share one harness (runtime, loop, toolbox, ledger, approvals, learning); these tests
pin what differs: who answers, what it may use, what its prompt says, and that the others are unchanged.
Real ThreadManager, RoomStore and StateStore in a temp folder; scripted model and terminal.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from agent import environments
from agent.lean import runtime as lean_runtime
from agent.lean.personas import CHAT_AGENT_ID, CODE_AGENT_ID
from agent.lean.provider import ModelTurn, ToolCall
from tests.test_environments import _session, env  # noqa: F401  (fixture)
from tests.test_lean_runtime import ScriptedClient
from tests.test_learning import store  # noqa: F401  (fixture)
from tests.test_verified_engineering import PYTEST_FAIL, PYTEST_PASS, bench  # noqa: F401  (fixture)

AGENT_ONLY = {"delegate_to_agent", "assign_tasks", "soul_update", "spawn_workers", "work_status", "agent_notes",
              "propose_agent_change"}
NOT_ENGINEERING = {"set_reminder", "create_media", "weather_live", "sports_live", "product_search", "computer_action"}


@pytest.fixture()
def code_thread(env, bench):  # noqa: F811
    chats, _, _ = env
    return chats.create_thread(title="Code · app", source="web", surface="code").thread_id


def _call(name: str, **args: Any) -> ToolCall:
    return ToolCall(f"c{uuid.uuid4().hex[:6]}", name, json.dumps(args))


def test_each_conversation_runs_in_its_environment(env, monkeypatch):  # noqa: F811
    chats, room_store, _ = env
    code = chats.create_thread(title="Code · app", source="web", surface="code").thread_id
    chat = chats.create_thread(title="Trip", source="web").thread_id
    contact = room_store.direct("echo", "Echo").thread_id
    assert environments.turn_environment(code, source="web") == "code"
    assert environments.turn_environment(chat, source="web") == "chat"
    assert environments.turn_environment(contact, source="web") == "agents"
    # Routines, guests and resumed agent work stay with their agent wherever they show.
    assert environments.turn_environment(code, source="routine") == "agents"
    assert environments.turn_environment(code, source="web", caller_role="trusted") == "agents"
    assert environments.turn_environment(code, source="web", work_id="work_1") == "agents"
    session = _session(monkeypatch, code)
    assert session.code_first and not session.model_first and session.environment == "code"


def test_code_is_the_selected_model_as_a_coding_assistant_with_engineering_tools(code_thread, monkeypatch):
    session = _session(monkeypatch, code_thread)
    persona = session._code_persona()
    assert persona.id == CODE_AGENT_ID and persona.name == "qwen3-8b"
    turn = session._build_turn(persona, "fix the failing date tests", history=[], depth=0)
    names = set(turn.toolbox.names)
    assert {"file_write", "terminal", "set_objective"} <= names  # the fixture's file tools; file_edit follows Settings › Permissions
    assert not names & AGENT_ONLY and not names & NOT_ENGINEERING
    prompt = turn.system_prompt
    assert "coding assistant in Echo Code" in prompt and "How you work in Echo Code" in prompt
    assert "## Your team" not in prompt and "Cards appear by themselves" not in prompt
    assert turn.verifier is not None  # 11.11.5's ledger measures checks and states completion


def test_a_native_code_session_edits_runs_verifies_and_reports_as_the_model(code_thread, store, monkeypatch, bench):  # noqa: F811
    bench.extend([PYTEST_FAIL, PYTEST_PASS])
    client = ScriptedClient([
        ModelTurn(tool_calls=[_call("terminal", command="pytest -q tests/test_dates.py")]),
        ModelTurn(tool_calls=[_call("file_write", path="dates.py", content="def leap(y): return y % 4 == 0")]),
        ModelTurn(tool_calls=[_call("terminal", command="pytest -q tests/test_dates.py")]),
        ModelTurn(content="Fixed leap years in dates.py; the date tests pass (5 passed)."),
    ])
    monkeypatch.setattr(lean_runtime.LeanSession, "_client_for", lambda self, persona, routing=False: client)
    events: list[dict[str, Any]] = []
    session = _session(monkeypatch, code_thread)
    session._emit = events.append
    session.emit = lambda event: events.append(event)
    out = session.run("fix the leap year bug in dates.py")
    assert not client.turns and len(client.calls) == 4  # no extra model calls: no job review, no delegation
    starts = [e for e in events if e.get("type") == "agent_start"]
    assert starts and starts[0]["agent"]["id"] == CODE_AGENT_ID and starts[0]["agent"]["name"] == "qwen3-8b"
    labels = [e.get("done_label") for e in events if e.get("type") == "tool_end"]
    assert labels == ["Ran `pytest -q tests/test_dates.py`", "Wrote dates.py", "Ran `pytest -q tests/test_dates.py`"]
    notes = [e["note"] for e in events if e.get("type") == "answer_note"]
    assert notes and notes[-1].startswith("Checks pass — test `pytest -q tests/test_dates.py`: 0 failed, 5 passed")
    # Code learns as its own learner, recorded as Code.
    [episode] = store.episodes()
    assert episode.agent_id == CODE_AGENT_ID and episode.environment == "code" and episode.verified_success
    assert out["success"]


def test_chat_and_agents_are_unchanged(env, monkeypatch):  # noqa: F811
    chats, room_store, _ = env
    chat = _session(monkeypatch, chats.create_thread(title="Trip", source="web").thread_id)
    assert chat.model_first and not chat.code_first and chat._chat_persona().id == CHAT_AGENT_ID
    agents = _session(monkeypatch, room_store.direct("echo", "Echo").thread_id)
    assert agents.environment == "agents" and not agents.code_first
    turn = agents._build_turn(agents.personas.get("echo"), "plan my week", history=[], depth=0)
    assert "soul_update" in turn.toolbox.names and "How you work in Echo Code" not in turn.system_prompt


def test_native_code_keeps_echo_codes_provider_rule(code_thread, monkeypatch):
    """Plans for code, APIs for chat (28 Oct): native Code never calls an API-key provider."""
    from types import SimpleNamespace

    session = _session(monkeypatch, code_thread)
    session.agent = SimpleNamespace(memory=None, llm_provider=SimpleNamespace(value="openai"),
                                    model_runtime=SimpleNamespace(model_id="gpt-5"))
    with pytest.raises(PermissionError):
        session._endpoint_for(session._code_persona())


def test_code_can_use_proven_coding_lessons_from_echo_without_becoming_echo(store, monkeypatch):  # noqa: F811
    from agent import learning
    from agent.learning import playbook
    from agent.learning.store import Lesson

    monkeypatch.setattr(playbook, "HOLDOUT_SHARED", 0.0)
    lesson = store.add_lesson(Lesson(agent_id="echo", title="Reproduce first", status="established", task_kind="coding",
                                     text="Before fixing a bug, run the failing test once and read the actual error."),
                              actor="test")
    picked = learning.prepare_turn(CODE_AGENT_ID, "fix the failing python test in the parser module", [])
    assert picked.shared_ids == [lesson.id] and "proven in another agent's work" in picked.section
