"""Summaries instead of trimming: long turns compact their older steps, long
chats keep a rolling summary of older turns."""

from __future__ import annotations

import threading
from typing import Any

from agent.lean import summaries
from agent.lean.loop import LeanTurn
from agent.lean.personas import AgentPersona
from agent.lean.provider import ModelTurn
from tests.test_lean_runtime import ScriptedClient, _toolbox


def _turn(client, events):
    return LeanTurn(
        client=client, persona=AgentPersona(id="echo", name="Echo"), system_prompt="system", history=[],
        toolbox=_toolbox({}), session_id="t", request_id="r", execution_id="e",
        emit=events.append, cancel=threading.Event(), persist_tool_runs=False,
    )


def _step(n: int) -> list[dict[str, Any]]:
    call = {"id": f"c{n}", "type": "function", "function": {"name": "web_search", "arguments": f'{{"q": "q{n}"}}'}}
    return [
        {"role": "assistant", "content": f"step {n}", "tool_calls": [call]},
        {"role": "tool", "tool_call_id": f"c{n}", "name": "web_search", "content": f"result {n} " + "x" * 2000},
    ]


def test_turn_compaction_keeps_recent_steps_and_folds_the_rest_into_the_user_message():
    events: list[dict[str, Any]] = []
    client = ScriptedClient([ModelTurn(content="Goal: find X. Tried q1-q3. Found result 2 matters.")])
    turn = _turn(client, events)
    turn._history_in_messages = 0
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "find X"}]
    for n in range(1, 6):
        messages += _step(n)

    assert turn._compact_turn(messages, keep_steps=2)

    roles = [m["role"] for m in messages]
    assert roles == ["system", "user", "assistant", "tool", "assistant", "tool"]  # alternation intact, no orphans
    assert "summarized to fit the context window" in messages[1]["content"] and "Found result 2" in messages[1]["content"]
    assert [m["content"] for m in messages if m["role"] == "assistant"] == ["step 4", "step 5"]
    assert "result 1" in client.calls[0][0]["content"]  # the summary saw the dropped steps
    assert events[-1]["type"] == "context_compacted"
    assert turn.timeline[-1]["kind"] == "note"


def test_turn_compaction_needs_enough_steps():
    turn = _turn(ScriptedClient([]), [])
    turn._history_in_messages = 0
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}] + _step(1) + _step(2)
    assert not turn._compact_turn(messages, keep_steps=2)


def _fake_turns(count: int) -> list[dict[str, Any]]:
    return [
        {"execution_id": f"e{i}", "messages": [
            {"role": "user", "text": f"question {i}"},
            {"role": "assistant", "text": f"answer {i}", "agent_name": "Echo", "agent_id": "echo"},
        ]}
        for i in range(count)
    ]


def test_chat_summary_covers_older_turns_and_history_skips_them(monkeypatch, tmp_path):
    from agent.lean import runtime as lean_runtime
    from agent.state import get_state_store

    monkeypatch.setattr(summaries, "DATA_DIR", tmp_path)
    turns = _fake_turns(summaries.RECENT_TURNS + 5)
    store = get_state_store()
    monkeypatch.setattr(store, "session_timeline", lambda session_id, limit=80: {"turns": turns[-limit:]})

    client = ScriptedClient([ModelTurn(content="The user asked questions 0-4; we decided on plan B.")])
    assert summaries.update("chat-1", client)
    assert "question 0" in client.calls[0][0]["content"] and f"question {summaries.RECENT_TURNS + 4}" not in client.calls[0][0]["content"]
    assert summaries.summary_text("chat-1").startswith("The user asked")
    # Nothing new to fold in: no second model call.
    assert not summaries.update("chat-1", ScriptedClient([]))

    session = lean_runtime.LeanSession.__new__(lean_runtime.LeanSession)
    session.session_id, session.room = "chat-1", None
    session.personas = lean_runtime.get_persona_store()
    history = session._history()
    text = " ".join(row["content"] for row in history)
    assert "question 0" not in text and "question 4" not in text  # covered by the summary
    assert f"question {summaries.RECENT_TURNS + 4}" in text  # recent turns stay verbatim


def test_summary_appears_in_the_system_prompt():
    from agent.lean.prompt import build_system_prompt

    prompt = build_system_prompt(persona=AgentPersona(id="echo", name="Echo"), soul_text="", chat_summary="We chose plan B.")
    assert "## Earlier in this chat (summary)\nWe chose plan B." in prompt
