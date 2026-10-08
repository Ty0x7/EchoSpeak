"""The check step: when code changed in a reply and nothing ran since, the agent is
asked once to run it or its tests before calling it done."""
from __future__ import annotations

import threading
from typing import Any

from agent.lean.job import changed_code_file
from agent.lean.loop import LeanTurn
from agent.lean.personas import AgentPersona
from agent.lean.provider import ModelTurn, ToolCall
from agent.lean.toolbox import NativeTool, Toolbox


class Client:
    def __init__(self, turns: list[ModelTurn]) -> None:
        self.turns = list(turns)
        self.calls: list[list[dict[str, Any]]] = []

    def stream_turn(self, messages, **_kwargs):
        self.calls.append([dict(m) for m in messages])
        turn = self.turns.pop(0)
        on_content = _kwargs.get("on_content")
        if turn.content and on_content:
            on_content(turn.content)
        return turn


def _turn(client: Client, *, terminal: bool = True) -> tuple[LeanTurn, list[str], list[dict[str, Any]]]:
    ran: list[str] = []
    events: list[dict[str, Any]] = []
    box = Toolbox(toolsets=["none"], session_id="t")
    tools = {"file_write": NativeTool(name="file_write", description="write", parameters={"type": "object", "properties": {}},
                                      func=lambda a: f"Wrote {a.get('path')}")}
    if terminal:
        tools["terminal"] = NativeTool(name="terminal", description="run", parameters={"type": "object", "properties": {}},
                                       func=lambda a: ran.append(a.get("command", "")) or "3 passed\n[exit code 0]")
    box.native = tools
    turn = LeanTurn(client=client, persona=AgentPersona(id="glados", name="Glados"), system_prompt="s", history=[], toolbox=box,
                    session_id="t", request_id="r", execution_id="e", emit=events.append, cancel=threading.Event(),
                    persist_tool_runs=False)
    return turn, ran, events


WRITE = ModelTurn(tool_calls=[ToolCall("w", "file_write", '{"path": "calc.py", "content": "def add(a, b): return a + b"}')])


def test_unrun_code_gets_one_check_before_done():
    client = Client([
        WRITE,
        ModelTurn(content="Done! calc.py is ready."),
        ModelTurn(tool_calls=[ToolCall("t", "terminal", '{"command": "pytest -q"}')]),
        ModelTurn(content="Done, and the tests pass (3 passed)."),
    ])
    turn, ran, events = _turn(client)
    result = turn.run("add an add() function")
    assert ran == ["pytest -q"]
    assert result.text.endswith("Done, and the tests pass (3 passed).") and "calc.py is ready" not in result.text
    nudge = client.calls[2][-1]["content"]
    assert nudge.startswith("You changed calc.py but haven't run anything to check it since.")
    assert any(e["type"] == "check_nudge" and e["files"] == ["calc.py"] for e in events)


def test_running_after_the_change_needs_no_check():
    client = Client([
        WRITE,
        ModelTurn(tool_calls=[ToolCall("t", "terminal", '{"command": "python calc.py"}')]),
        ModelTurn(content="Done; it runs."),
    ])
    turn, _ran, events = _turn(client)
    assert turn.run("go").text == "Done; it runs."
    assert not any(e["type"] == "check_nudge" for e in events)


def test_notes_and_agents_without_a_terminal_are_not_asked():
    client = Client([ModelTurn(tool_calls=[ToolCall("w", "file_write", '{"path": "shopping-list.md", "content": "- eggs"}')]),
                     ModelTurn(content="Saved your list.")])
    turn, _ran, events = _turn(client)
    assert turn.run("save my list").text == "Saved your list."
    client = Client([WRITE, ModelTurn(content="Wrote calc.py.")])
    turn, _ran, events = _turn(client, terminal=False)
    assert turn.run("go").text == "Wrote calc.py." and not any(e["type"] == "check_nudge" for e in events)


def test_the_check_is_asked_only_once():
    client = Client([WRITE, ModelTurn(content="Done."), ModelTurn(content="It can't be run here, so it isn't checked.")])
    turn, ran, _events = _turn(client)
    assert turn.run("go").text.endswith("It can't be run here, so it isn't checked.") and ran == []


def test_what_counts_as_a_code_change():
    assert changed_code_file("file_write", {"path": "src/App.tsx"}) == "src/App.tsx"
    assert changed_code_file("file_edit", {"file_path": "build.ps1"}) == "build.ps1"
    assert changed_code_file("file_move", {"path": "a.txt", "destination": "b.py"}) == "b.py"
    assert changed_code_file("file_write", {"path": "notes.md"}) == ""
    assert changed_code_file("file_read", {"path": "calc.py"}) == ""
