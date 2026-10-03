"""Tool surface: the trimmed toolsets, and names / arguments from other harnesses."""

from __future__ import annotations

import os
import threading
from typing import Any

import pytest

from agent.lean.loop import LeanTurn
from agent.lean.personas import AgentPersona
from agent.lean.provider import ModelTurn, ToolCall
from agent.lean.toolbox import DEFAULT_TOOLSETS, TOOLSETS, NativeTool, Toolbox
from tests.test_lean_runtime import ScriptedClient


def _box(*names: str) -> Toolbox:
    box = Toolbox(toolsets=["none"], session_id="t")
    box.native = {
        name: NativeTool(name=name, description=name, parameters={"type": "object", "properties": {}},
                         func=lambda args, name=name: f"{name} got {sorted(args.items())}")
        for name in names
    }
    return box


def test_default_toolsets_drop_redundant_tools():
    default = {tool for name in DEFAULT_TOOLSETS for tool in TOOLSETS[name]}
    # The prompt has the time; file_write makes folders; create_artifact / file_write cover artifact_write;
    # the EchoSpeak changelog is for guests and the opt-in self set; terminal starts background work.
    for gone in ("get_system_time", "file_mkdir", "artifact_write", "project_update_context", "process_start"):
        assert gone not in default, gone
    assert "project_update_context" in TOOLSETS["self"]
    assert TOOLSETS["research"] == TOOLSETS["web"] + TOOLSETS["live"]  # old name still works


def test_other_harness_names_and_arguments_reach_the_right_tool():
    box = _box("terminal", "file_read", "file_edit", "file_search", "file_find", "safe_web_fetch")
    assert box.normalize_call("bash", {"cmd": "ls", "workdir": "src"}) == ("terminal", {"command": "ls", "cwd": "src"})
    assert box.normalize_call("process_start", {"command": "npm run dev"}) == (
        "terminal", {"command": "npm run dev", "background": True})
    assert box.normalize_call("read_file", {"file_path": "a.py"}) == ("file_read", {"path": "a.py"})
    assert box.normalize_call("edit_file", {"file_path": "a.py", "old_string": "x", "new_string": "y"}) == (
        "file_edit", {"path": "a.py", "old_text": "x", "new_text": "y"})
    assert box.normalize_call("grep", {"query": "TODO"}) == ("file_search", {"pattern": "TODO"})
    assert box.normalize_call("Web-Fetch", {"link": "https://x.y"}) == ("safe_web_fetch", {"url": "https://x.y"})
    # Canonical arguments are never overwritten, and unknown names stay unknown.
    assert box.normalize_call("file_read", {"path": "a", "file_path": "b"}) == ("file_read", {"path": "a", "file_path": "b"})
    assert box.normalize_call("teleport", {}) == ("teleport", {})
    # An alias only applies when its target is in this agent's toolbox.
    assert _box("file_read").normalize_call("bash", {"cmd": "ls"}) == ("bash", {"cmd": "ls"})


def test_the_loop_runs_an_aliased_call_instead_of_failing():
    client = ScriptedClient([
        ModelTurn(tool_calls=[ToolCall("c1", "edit_file", '{"file_path": "a.py", "old_string": "x", "new_string": "y"}')]),
        ModelTurn(content="Edited."),
    ])
    events: list[dict[str, Any]] = []
    turn = LeanTurn(
        client=client, persona=AgentPersona(id="echo", name="Echo"), system_prompt="s", history=[],
        toolbox=_box("file_edit"), session_id="t", request_id="r", execution_id="e", emit=events.append,
        cancel=threading.Event(), persist_tool_runs=False,
    )
    result = turn.run("fix it")
    tool_result = next(m for m in client.calls[1] if m.get("role") == "tool")
    assert tool_result["content"] == "file_edit got [('new_text', 'y'), ('old_text', 'x'), ('path', 'a.py')]"
    assert next(e for e in events if e["type"] == "tool_start")["name"] == "file_edit"
    assert result.text == "Edited."


@pytest.mark.skipif(os.name == "nt", reason="uses bash")
def test_terminal_background_flag_starts_it_in_the_background(tmp_path, monkeypatch):
    from agent.lean import terminal as term
    from config import config

    monkeypatch.setattr(config, "terminal_execution_mode", "host", raising=False)
    monkeypatch.setattr(config, "file_tool_root", str(tmp_path), raising=False)
    monkeypatch.setattr(term, "_PROC_DIR", tmp_path / "procs")
    try:
        out = term.Terminal(str(tmp_path)).run({"command": "echo up; sleep 5", "background": True, "wait_seconds": 1})
        assert "started in the background" in out and "up" in out
        assert {t.name for t in term.Terminal(str(tmp_path)).tools()} == {"terminal", "process_output", "process_stop"}
    finally:
        term.stop_all_processes()
        term._PROCESSES.clear()
