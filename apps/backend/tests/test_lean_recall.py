"""Agents get the context that exists instead of acting lost.

- Recall is keyed on the user's request and the agent's task, not on "[System]" briefs.
- Stopwords don't decide which memories are relevant.
- Teammates get the chat summary; a request that points back pulls in past chats.
- Long turns get an "are you on track?" reminder.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any

from agent.lean import runtime as lean_runtime, summaries
from agent.lean.loop import LeanTurn
from agent.lean.personas import AgentPersona, get_persona_store
from agent.lean.provider import ModelTurn, ToolCall
from agent.lean.recall import points_back
from agent.lean.toolbox import NativeTool, Toolbox
from agent.memory import AgentMemory
from agent.state import get_state_store
from tests.test_lean_runtime import ScriptedClient


def _session(monkeypatch, scripts: dict[str, ScriptedClient], queries: list[str], session_id: str = ""):
    monkeypatch.setattr(lean_runtime.LeanSession, "_client_for", lambda self, persona, routing=False: scripts[persona.id])
    monkeypatch.setattr(lean_runtime.LeanSession, "_recall", lambda self, query, limit=8: queries.append(query) or [])
    events: list[dict[str, Any]] = []
    session = lean_runtime.LeanSession(
        agent=type("Agent", (), {"memory": None})(), session_id=session_id or f"recall-{uuid.uuid4().hex[:8]}",
        request_id=f"req-{uuid.uuid4().hex[:6]}", emit=events.append, cancel=threading.Event(), source="web",
    )
    return session


def test_a_teammate_recalls_by_its_task_and_the_request_and_gets_the_chat_summary(monkeypatch):
    # These agents share a project (System A, agent/lean/agent_reach.py): delegation needs a shared environment.
    monkeypatch.setattr(lean_runtime.LeanSession, "_project_members", lambda self: [p.id for p in self.personas.list()])
    monkeypatch.setattr(summaries, "summary_text", lambda session_id: "Earlier: the user is building a bakery site.")
    scripts = {
        "echo": ScriptedClient([
            ModelTurn(tool_calls=[ToolCall("d1", "delegate_to_agent", '{"agent": "Glados", "task": "add a menu page to the bakery site"}')]),
            ModelTurn(content="Glados added it."),
        ]),
        "forge": ScriptedClient([ModelTurn(tool_calls=[ToolCall("c1", "complete_task", '{"summary": "Added menu.html."}')])]),
    }
    queries: list[str] = []
    session = _session(monkeypatch, scripts, queries)
    session.run("Can you get the menu page done?", persona_id="echo")

    teammate_query = queries[1]
    assert teammate_query.startswith("add a menu page to the bakery site")
    assert "Can you get the menu page done?" in teammate_query
    assert "handed you this task" not in teammate_query and "[System]" not in teammate_query
    teammate_prompt = scripts["forge"].calls[0][0]["content"]
    assert "Earlier: the user is building a bakery site." in teammate_prompt


def test_memory_recall_ignores_stopwords():
    class Store:
        _records = {
            "m1": {"id": "m1", "text": "The user's dog is called Biscuit.", "active": True, "owner_id": "local-owner",
                   "scope": "account", "metadata": {}},
            "m2": {"id": "m2", "text": "My favourite colour is what the user calls teal.", "active": True,
                   "owner_id": "local-owner", "scope": "account", "metadata": {}},
            "m3": {"id": "m3", "text": "The user works night shifts.", "active": True, "owner_id": "local-owner",
                   "scope": "account", "metadata": {}},
        }
        _owner_id = AgentMemory._owner_id

    rows = AgentMemory.runtime_memory_projection.__wrapped__(Store(), "what is my dog's name?", session_id="s")
    assert [row["memory_id"] for row in rows] == ["m1"]  # "what", "is", "my" no longer match m2 and m3


def test_pointing_back_pulls_in_matching_past_chats(monkeypatch):
    store = get_state_store()
    other = f"old-{uuid.uuid4().hex[:6]}"
    turn = store.create_execution(thread_id=other, query="Pick a colour for the Atlas logo", record_user_message=True)
    store.add_item(turn_id=turn.id, item_type="assistant_message", status="complete", session_id=other,
                   payload={"text": "Let's go with teal for the Atlas logo.", "agent_name": "Echo"})
    scripts = {"echo": ScriptedClient([ModelTurn(content="Teal.")])}
    session = _session(monkeypatch, scripts, [])
    session.run("What colour did we pick last time for the Atlas logo?")

    messages = scripts["echo"].calls[0]
    request = messages[-1]["content"]
    # On the current request, not in the system prompt: recall changes per request and would break the cached prefix.
    assert "## From past conversations" in request and "teal" in request.lower()
    assert "## From past conversations" not in messages[0]["content"]

    # No back-reference, no lookup.
    plain = {"echo": ScriptedClient([ModelTurn(content="Blue is calm.")])}
    _session(monkeypatch, plain, []).run("What colour suits a logo for Atlas?")
    assert not any("## From past conversations" in str(m["content"]) for m in plain["echo"].calls[0])


def test_points_back():
    for text in ("like last time", "what we discussed yesterday", "you told me to use teal", "remember when I asked",
                 # Evaluation MC1/MCH1 (19 November): these reached no past chat at all.
                 "Which venue did I mention earlier for the offsite?",
                 "What's the name of the vendor's account manager I mentioned before?",
                 "did I tell you my sister's name?", "the budget I gave you earlier"):
        assert points_back(text), text
    for text in ("what time is it?", "write a poem", "try again", "mention the deadline in the email",
                 "I mentioned it to the team, can you draft the follow-up?"):
        assert not points_back(text), text


def test_chat_search_finds_a_past_message_from_a_descriptive_query(monkeypatch):
    """Evaluation MC1 (19 November): "venue offsite meeting location" found nothing, because every word had to
    appear in one message. A search with no exact match now ranks messages by the words they do share."""
    store = get_state_store()
    other = f"old-{uuid.uuid4().hex[:6]}"
    marker = uuid.uuid4().hex[:6]
    turn = store.create_execution(thread_id=other, query=f"For context: the team offsite is at Ashcombe{marker} Hall.",
                                  record_user_message=True)
    store.add_item(turn_id=turn.id, item_type="assistant_message", status="complete", session_id=other,
                   payload={"text": "Two agenda topics for the offsite.", "agent_name": "Echo"})
    session = _session(monkeypatch, {"echo": ScriptedClient([])}, [])
    built = session._build_turn(get_persona_store().get("echo"), "where is the offsite?", history=[], depth=0)
    search = built.toolbox.native["chat_search"].func
    found = search({"query": "venue offsite meeting location"})
    assert f"Ashcombe{marker}" in found
    # An exact match still comes first and nothing is invented when no word matches.
    assert "No past messages match" in search({"query": f"zzq{marker} qqz{marker}"})


def test_long_turns_get_an_on_track_reminder_with_the_failure_count():
    calls = [ModelTurn(tool_calls=[ToolCall(f"c{i}", "flaky", '{"n": %d}' % i)]) for i in range(8)]
    client = ScriptedClient(calls + [ModelTurn(content="Stopped: the service keeps failing.")])
    box = Toolbox(toolsets=["none"], session_id="t")
    box.native = {"flaky": NativeTool(name="flaky", description="x", parameters={"type": "object", "properties": {}},
                                      func=lambda args: "Error: service unavailable")}
    events: list[dict[str, Any]] = []
    turn = LeanTurn(client=client, persona=AgentPersona(id="echo", name="Echo"), system_prompt="s", history=[], toolbox=box,
                    session_id="t", request_id="r", execution_id="e", emit=events.append, cancel=threading.Event(),
                    persist_tool_runs=False, goal="Fetch today's sales report")
    turn.run("get the sales report")

    eighth = [m for m in client.calls[8] if m.get("role") == "tool"][-1]["content"]
    assert "[Reminder from EchoSpeak: 8 steps into this task. The goal: Fetch today's sales report" in eighth
    assert "8 of the last 8 tool calls failed: change the approach" in eighth
    earlier = [m for m in client.calls[7] if m.get("role") == "tool"][-1]["content"]
    assert "Reminder" not in earlier
    assert [e["step"] for e in events if e["type"] == "goal_reminder"] == [8]
