"""Lean runtime: the loop runs until the model stops calling tools, failures go
back to the model instead of ending the turn, and approvals pause only the one
tool that needs them."""

from __future__ import annotations

import json
import threading
from typing import Any

import pytest

from agent.lean.approvals import get_approval_broker, tool_needs_approval
from agent.lean.loop import LeanTurn
from agent.lean.personas import AgentPersona
from agent.lean.provider import ModelTurn, ThinkTagScrubber, ToolCall, extract_text_tool_calls
from agent.lean.toolbox import NativeTool, Toolbox, _compact_schema


# ── provider helpers ────────────────────────────────────────────────────

def test_think_scrubber_handles_tags_split_across_chunks():
    scrubber = ThinkTagScrubber()
    visible, hidden = "", ""
    for chunk in ["Hi <thi", "nk>plan the ", "answer</th", "ink> there"]:
        v, h = scrubber.feed(chunk)
        visible += v
        hidden += h
    v, h = scrubber.flush()
    visible += v
    hidden += h
    assert visible == "Hi  there"
    assert hidden == "plan the answer"


def test_text_tool_calls_are_recovered():
    calls, cleaned = extract_text_tool_calls(
        'Sure.\n<tool_call>{"name": "web_search", "arguments": {"query": "python"}}</tool_call>',
        {"web_search"},
    )
    assert [c.name for c in calls] == ["web_search"]
    assert json.loads(calls[0].arguments) == {"query": "python"}
    assert cleaned == "Sure."


def test_loose_json_arguments_are_repaired():
    args, error = ToolCall(id="1", name="x", arguments="{'path': 'a.txt',}").parsed_arguments()
    assert error == ""
    assert args == {"path": "a.txt"}


def test_schema_compaction_keeps_required_fields():
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "x" * 400},
            "limit": {"anyOf": [{"type": "integer"}, {"type": "null"}], "default": 5},
        },
        "required": ["query"],
    }
    compact = _compact_schema(schema)
    assert compact["required"] == ["query"]
    assert compact["properties"]["limit"]["type"] == "integer"
    assert len(compact["properties"]["query"]["description"]) <= 141


# ── approvals ───────────────────────────────────────────────────────────

class _Entry:
    def __init__(self, risk: str = "safe", is_action: bool = False, category: str = "general") -> None:
        self.risk_level = risk
        self.is_action = is_action
        self.category = category
        self.origin = "native"


def test_smart_approval_only_gates_destructive_and_external():
    assert tool_needs_approval(_Entry("moderate", True, "file_ops"), "file_write", {"path": "a"})[0] is False
    assert tool_needs_approval(_Entry("destructive", True, "file_ops"), "file_delete", {"path": "a"})[0] is True
    assert tool_needs_approval(_Entry("moderate", True), "email_send", {})[0] is True
    assert tool_needs_approval(_Entry("destructive", True, "system"), "terminal_run", {"command": "git status"})[0] is False
    assert tool_needs_approval(_Entry("destructive", True, "system"), "terminal_run", {"command": "git push origin main"})[0] is True


# ── loop ────────────────────────────────────────────────────────────────

class ScriptedClient:
    """Plays back model turns; records the messages each call received."""

    def __init__(self, turns: list[ModelTurn]) -> None:
        self.turns = list(turns)
        self.calls: list[list[dict[str, Any]]] = []

    def stream_turn(self, messages, *, tools=None, on_reasoning=None, on_content=None, cancel=None, temperature=None, max_tokens=None):
        self.calls.append([dict(m) for m in messages])
        turn = self.turns.pop(0)
        if turn.reasoning and on_reasoning:
            on_reasoning(turn.reasoning)
        if turn.content and on_content:
            on_content(turn.content)
        return turn


def _toolbox(tools: dict[str, Any]) -> Toolbox:
    box = Toolbox(toolsets=["none"], session_id="t")
    box.native = {
        name: NativeTool(name=name, description=name, parameters={"type": "object", "properties": {}}, func=func)
        for name, func in tools.items()
    }
    return box


def _turn(client: ScriptedClient, toolbox: Toolbox, events: list[dict[str, Any]], cancel: threading.Event | None = None) -> LeanTurn:
    return LeanTurn(
        client=client,  # type: ignore[arg-type]
        persona=AgentPersona(id="echo", name="Echo"),
        system_prompt="system",
        history=[],
        toolbox=toolbox,
        session_id="t",
        request_id="r",
        execution_id="e",
        emit=events.append,
        cancel=cancel or threading.Event(),
        persist_tool_runs=False,
    )


def test_loop_runs_tools_until_the_model_answers():
    client = ScriptedClient([
        ModelTurn(reasoning="need data", tool_calls=[ToolCall("c1", "lookup", "{}")]),
        ModelTurn(tool_calls=[ToolCall("c2", "lookup", '{"again": true}')]),
        ModelTurn(content="All done."),
    ])
    events: list[dict[str, Any]] = []
    result = _turn(client, _toolbox({"lookup": lambda args: "42"}), events).run("go")
    assert result.success and result.text == "All done."
    kinds = [e["type"] for e in events]
    assert kinds.count("tool_start") == 2 and kinds.count("tool_end") == 2
    assert "reasoning_delta" in kinds and kinds[-1] == "agent_done"
    # Tool results went back to the model as tool messages.
    assert any(m.get("role") == "tool" and m.get("content") == "42" for m in client.calls[2])


def test_tool_failure_is_returned_to_the_model_not_fatal():
    def broken(_args):
        raise RuntimeError("disk on fire")

    client = ScriptedClient([
        ModelTurn(tool_calls=[ToolCall("c1", "broken", "{}")]),
        ModelTurn(content="That tool failed, here is what I can tell you."),
    ])
    events: list[dict[str, Any]] = []
    result = _turn(client, _toolbox({"broken": broken}), events).run("go")
    assert result.success
    failed = [e for e in events if e["type"] == "tool_end"]
    assert failed and failed[0]["ok"] is False
    assert "disk on fire" in client.calls[1][-1]["content"]


def test_unknown_tool_and_bad_json_are_feedback():
    client = ScriptedClient([
        ModelTurn(tool_calls=[ToolCall("c1", "nope", "{}"), ToolCall("c2", "lookup", "not json at all")]),
        ModelTurn(content="ok"),
    ])
    result = _turn(client, _toolbox({"lookup": lambda a: "x"}), []).run("go")
    tool_msgs = [m for m in client.calls[1] if m.get("role") == "tool"]
    assert "unknown tool" in tool_msgs[0]["content"]
    assert "not valid JSON" in tool_msgs[1]["content"]
    assert result.text == "ok"


def test_empty_reply_is_nudged_instead_of_ending():
    client = ScriptedClient([
        ModelTurn(reasoning="hmm"),
        ModelTurn(content="Here you go."),
    ])
    result = _turn(client, _toolbox({}), []).run("go")
    assert result.text == "Here you go."
    assert "stopped without replying" in client.calls[1][-1]["content"]


def test_repeated_identical_call_is_short_circuited():
    calls = []
    client = ScriptedClient([
        ModelTurn(tool_calls=[ToolCall("a", "lookup", "{}")]),
        ModelTurn(tool_calls=[ToolCall("b", "lookup", "{}")]),
        ModelTurn(tool_calls=[ToolCall("c", "lookup", "{}")]),
        ModelTurn(content="done"),
    ])
    result = _turn(client, _toolbox({"lookup": lambda a: calls.append(1) or "same"}), []).run("go")
    assert len(calls) == 2
    assert "already called lookup" in client.calls[3][-1]["content"]
    assert result.text == "done"


def test_text_encoded_tool_call_runs_and_is_hidden():
    client = ScriptedClient([
        ModelTurn(content='<tool_call>{"name": "lookup", "arguments": {}}</tool_call>'),
        ModelTurn(content="Answer."),
    ])
    events: list[dict[str, Any]] = []
    result = _turn(client, _toolbox({"lookup": lambda a: "found"}), events).run("go")
    assert result.text == "Answer."
    assert any(e["type"] == "text_replace" for e in events)


def test_denied_approval_is_reported_to_the_model(monkeypatch):
    toolbox = _toolbox({"wipe": lambda a: "wiped"})
    monkeypatch.setattr(toolbox, "entry", lambda name: _Entry("destructive", True))
    client = ScriptedClient([
        ModelTurn(tool_calls=[ToolCall("c1", "wipe", "{}")]),
        ModelTurn(content="Okay, I left it alone."),
    ])
    events: list[dict[str, Any]] = []
    turn = _turn(client, toolbox, events)

    def deny_when_asked(event):
        events.append(event)
        if event["type"] == "approval_request":
            threading.Timer(0.05, lambda: get_approval_broker().resolve(event["id"], "deny")).start()

    turn._emit = deny_when_asked
    result = turn.run("go")
    assert result.text == "Okay, I left it alone."
    assert "denied permission" in client.calls[1][-1]["content"]
    assert any(e["type"] == "approval_resolved" and e["decision"] == "deny" for e in events)


def test_non_interactive_sources_never_wait_for_approval(monkeypatch):
    toolbox = _toolbox({"wipe": lambda a: "wiped"})
    monkeypatch.setattr(toolbox, "entry", lambda name: _Entry("destructive", True))
    client = ScriptedClient([
        ModelTurn(tool_calls=[ToolCall("c1", "wipe", "{}")]),
        ModelTurn(content="Needs the app."),
    ])
    turn = _turn(client, toolbox, [])
    turn.interactive = False
    result = turn.run("go")
    assert result.text == "Needs the app."
    assert "only be given in the EchoSpeak app" in client.calls[1][-1]["content"]


def test_cancel_stops_the_loop():
    cancel = threading.Event()
    cancel.set()
    client = ScriptedClient([ModelTurn(content="never")])
    result = _turn(client, _toolbox({}), [], cancel=cancel).run("go")
    assert not result.success and result.error == "cancelled"
    assert client.calls == []


# ── group chat: every agent turn is its own message ─────────────────────

def test_handoff_seals_the_message_so_the_reply_lands_after_the_teammate():
    """A -> B -> A must produce three messages in that order, not A (with A's
    continuation streamed into it) above B."""
    events: list[dict[str, Any]] = []
    teammate_client = ScriptedClient([ModelTurn(reasoning="checking", content="Scout: it's sunny.")])

    def run_teammate(args: dict[str, Any]) -> str:
        teammate = LeanTurn(
            client=teammate_client,  # type: ignore[arg-type]
            persona=AgentPersona(id="scout", name="Scout"),
            system_prompt="system", history=[], toolbox=_toolbox({}),
            session_id="t", request_id="r", execution_id="e",
            emit=events.append, cancel=threading.Event(), persist_tool_runs=False,
        )
        return "Scout replied:\n" + teammate.run(args["task"]).text

    box = _toolbox({})
    box.native["delegate_to_agent"] = NativeTool(
        name="delegate_to_agent", description="", parameters={"type": "object", "properties": {}},
        func=run_teammate, handoff=lambda args: "Handed to Scout. Their reply is below.",
    )
    sealed: list[Any] = []
    client = ScriptedClient([
        ModelTurn(content="Let me ask Scout.", tool_calls=[ToolCall("d1", "delegate_to_agent", '{"agent": "Scout", "task": "weather"}')]),
        ModelTurn(reasoning="Scout answered", content="So: bring sunglasses."),
    ])
    turn = LeanTurn(
        client=client,  # type: ignore[arg-type]
        persona=AgentPersona(id="echo", name="Echo"),
        system_prompt="system", history=[], toolbox=box,
        session_id="t", request_id="r", execution_id="e",
        emit=events.append, cancel=threading.Event(), persist_tool_runs=False,
        on_seal=sealed.append,
    )
    result = turn.run("what's the weather?")

    starts = [e for e in events if e["type"] == "agent_start"]
    assert [e["agent_id"] for e in starts] == ["echo", "scout", "echo"]
    ids = [e["message_id"] for e in starts]
    assert len(set(ids)) == 3

    # Every event belongs to the message that was open at the time.
    def text_of(message_id: str) -> str:
        return "".join(e.get("data", "") for e in events if e["type"] == "agent_token" and e["message_id"] == message_id)

    assert text_of(ids[0]) == "Let me ask Scout."
    assert text_of(ids[1]) == "Scout: it's sunny."
    assert text_of(ids[2]) == "So: bring sunglasses."
    tool_events = [e for e in events if e["type"] in {"tool_start", "tool_end"}]
    assert {e["message_id"] for e in tool_events} == {ids[0]}
    assert [e["message_id"] for e in events if e["type"] == "reasoning_delta"] == [ids[1], ids[2]]

    # Each message is closed before the next one opens.
    done_order = [e["message_id"] for e in events if e["type"] == "agent_done"]
    assert done_order == [ids[0], ids[1], ids[2]]
    assert sealed and sealed[0].message_id == ids[0] and sealed[0].text == "Let me ask Scout."
    assert result.message_id == ids[2] and result.text == "So: bring sunglasses."
    # The model still got the teammate's answer as the tool result.
    assert any(m.get("role") == "tool" and "it's sunny" in m.get("content", "") for m in client.calls[1])


def test_handoff_with_nothing_more_to_say_leaves_no_empty_message():
    events: list[dict[str, Any]] = []
    box = _toolbox({})
    box.native["delegate_to_agent"] = NativeTool(
        name="delegate_to_agent", description="", parameters={"type": "object", "properties": {}},
        func=lambda args: "Scout replied:\nDone.", handoff=lambda args: "Handed to Scout.",
    )
    client = ScriptedClient([
        ModelTurn(tool_calls=[ToolCall("d1", "delegate_to_agent", '{"agent": "Scout", "task": "x"}')]),
        ModelTurn(content=""),
    ])
    result = _turn(client, box, events).run("go")
    assert result.empty and result.text == ""
    assert len(client.calls) == 2  # no "you stopped without replying" nudge after a handoff
    assert events[-1]["type"] == "agent_done" and events[-1]["empty"] is True


def test_bad_handoff_arguments_do_not_seal_the_message():
    events: list[dict[str, Any]] = []
    box = _toolbox({})
    box.native["delegate_to_agent"] = NativeTool(
        name="delegate_to_agent", description="", parameters={"type": "object", "properties": {}},
        func=lambda args: "unused", handoff=lambda args: "Error: unknown agent. Choose one of: Scout",
    )
    client = ScriptedClient([
        ModelTurn(tool_calls=[ToolCall("d1", "delegate_to_agent", '{"agent": "Nobody"}')]),
        ModelTurn(content="Okay, I'll answer myself."),
    ])
    result = _turn(client, box, events).run("go")
    assert [e["type"] for e in events].count("agent_start") == 1
    assert result.text == "Okay, I'll answer myself."
    assert any(m.get("role") == "tool" and m.get("content", "").startswith("Error: unknown agent") for m in client.calls[1])


def test_session_persists_a_b_a_as_three_ordered_messages(monkeypatch):
    """Full LeanSession: Echo delegates to Scout and then wraps up. The saved
    Session timeline must reload as Echo, Scout, Echo with distinct ids."""
    from agent.lean import runtime as lean_runtime
    from agent.state import get_state_store

    scripts = {
        "echo": ScriptedClient([
            ModelTurn(content="Asking Scout.", tool_calls=[ToolCall("d1", "delegate_to_agent", '{"agent": "Scout", "task": "find it"}')]),
            ModelTurn(content="Scout found it, so we're done."),
        ]),
        "scout": ScriptedClient([ModelTurn(content="Found it.")]),
    }
    monkeypatch.setattr(lean_runtime.LeanSession, "_client_for", lambda self, persona, routing=False: scripts[persona.id])
    monkeypatch.setattr(lean_runtime.LeanSession, "_recall", lambda self, query, limit=8: [])
    events: list[dict[str, Any]] = []
    session_id = "handoff-order-test"
    agent = type("Agent", (), {"memory": None})()
    out = lean_runtime.LeanSession(
        agent=agent, session_id=session_id, request_id="req-handoff", emit=events.append,
        cancel=threading.Event(), source="web",
    ).run("find the thing", persona_id="echo")

    assert out["success"]
    assert [m["agent_id"] for m in out["messages"]] == ["echo", "scout", "echo"]
    assert len({m["message_id"] for m in out["messages"]}) == 3
    assert out["response"] == "Scout found it, so we're done."

    turns = get_state_store().session_timeline(session_id)["turns"]
    saved = [m for t in turns for m in t["messages"] if m["role"] == "assistant"]
    assert [m["agent_id"] for m in saved] == ["echo", "scout", "echo"]
    assert [m["message_id"] for m in saved] == [m["message_id"] for m in out["messages"]]
    assert [m["text"] for m in saved] == ["Asking Scout.", "Found it.", "Scout found it, so we're done."]
    assert any(row.get("kind") == "tool" for row in saved[0]["timeline"])


def test_agent_copying_its_own_transcript_prefix_is_cleaned():
    events: list[dict[str, Any]] = []
    client = ScriptedClient([ModelTurn(content="[Echo]: Spaces, per PEP 8.")])
    result = _turn(client, _toolbox({}), events).run("tabs or spaces?")
    assert result.text == "Spaces, per PEP 8."
    assert any(e["type"] == "text_replace" and e["text"] == "Spaces, per PEP 8." for e in events)


def test_step_limit_stops_honestly_without_an_extra_model_call(monkeypatch):
    from agent.lean import settings as lean_settings

    monkeypatch.setattr(lean_settings, "max_iterations", lambda: 2)
    events: list[dict[str, Any]] = []
    client = ScriptedClient([
        ModelTurn(tool_calls=[ToolCall("c1", "lookup", '{"q": 1}')]),
        ModelTurn(tool_calls=[ToolCall("c2", "lookup", '{"q": 2}')]),
    ])
    result = _turn(client, _toolbox({"lookup": lambda args: "ok"}), events).run("big job")
    assert len(client.calls) == 2  # no forced third call
    assert result.stop_reason == "max_steps"
    assert result.text.startswith("I ran out of steps (2) before finishing.")
    assert "Press Continue" in result.text
    assert events[-1]["type"] == "agent_done" and events[-1]["stop_reason"] == "max_steps"
