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
