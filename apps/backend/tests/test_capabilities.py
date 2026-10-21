"""Runtime capability awareness: permission switches hold for lean-native tools, the agent is
told what is on and off (and why), and the loop stops re-trying what was refused."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from agent.lean import capabilities
from agent.lean.approvals import get_approval_broker
from agent.lean.coding import coding_tools
from agent.lean.loop import LeanTurn
from agent.lean.personas import AgentPersona
from agent.lean.prompt import build_system_prompt
from agent.lean.provider import ModelTurn, ToolCall
from agent.lean.terminal import Terminal
from agent.lean.toolbox import NativeTool, Toolbox
from config import config


@pytest.fixture
def switches(monkeypatch):
    def set_(*, master: bool = False, files: bool = False, terminal: bool = False) -> None:
        monkeypatch.setattr(config, "enable_system_actions", master)
        monkeypatch.setattr(config, "allow_file_write", files)
        monkeypatch.setattr(config, "allow_terminal_commands", terminal)
    set_()
    return set_


def _box(project: Path) -> Toolbox:
    return Toolbox(extra_tools=coding_tools() + Terminal(str(project)).tools(), session_id="cap", project_root=str(project))


# ── permission switches hold for lean-native tools ───────────────────────

def test_native_edit_and_terminal_are_withheld_while_permissions_are_off(tmp_path, switches):
    box = _box(tmp_path)
    assert "file_read" in box.names
    assert not {"file_edit", "terminal", "process_output", "process_stop"} & set(box.names)


def test_native_edit_and_terminal_are_offered_once_permissions_are_on(tmp_path, switches):
    switches(master=True, files=True, terminal=True)
    assert {"file_edit", "terminal"} <= set(_box(tmp_path).names)


def test_the_master_switch_alone_is_not_enough(tmp_path, switches):
    switches(master=True)
    assert not {"file_edit", "terminal"} & set(_box(tmp_path).names)


def test_switching_file_edits_off_mid_turn_stops_the_next_edit(tmp_path, switches):
    switches(master=True, files=True)
    target = tmp_path / "note.txt"
    target.write_text("hello world\n", encoding="utf-8")
    box = _box(tmp_path)
    switches(master=True, files=False)
    result = box.run("file_edit", {"path": str(target), "old_text": "hello", "new_text": "changed"})
    assert not result.ok
    assert "Settings › Permissions (Create and edit files)" in result.output
    assert target.read_text(encoding="utf-8") == "hello world\n"


# ── why a tool is off ────────────────────────────────────────────────────

def test_a_call_to_a_switched_off_tool_says_what_turns_it_on(tmp_path, switches):
    result = _box(tmp_path).run("terminal", {"command": "echo hi"})
    assert not result.ok
    assert "not available right now" in result.output
    assert "Allow actions on this computer" in result.output
    assert "work around" in result.output


def test_a_tool_outside_the_agents_toolsets_names_the_toolset(tmp_path, switches):
    switches(master=True, files=True, terminal=True)
    box = Toolbox(toolsets=["core"], extra_tools=coding_tools() + Terminal(str(tmp_path)).tools(), session_id="cap",
                  project_root=str(tmp_path))
    assert "terminal" not in box.names
    assert "\"Run commands\" under Agents, Edit" in box.run("terminal", {"command": "dir"}).output


def test_a_made_up_tool_is_still_reported_as_unknown(tmp_path, switches):
    output = _box(tmp_path).run("teleport", {}).output
    assert output.startswith("Error: unknown tool 'teleport'")


def test_sandbox_paths_given_to_file_tools_map_back_to_this_pc(tmp_path, switches, monkeypatch):
    # A 4B model ran `mkdir -p /work/Desktop/Game` in the sandbox terminal, then called
    # file_write("/work/Desktop/Game/index.html"), which the Windows file tools refused.
    from agent.lean import terminal as term

    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    monkeypatch.setattr(term, "workspace_roots", lambda project_root="": [desktop])
    assert term.to_host_path("/work/Desktop/Game/index.html") == str(desktop / "Game" / "index.html")
    assert term.to_host_path("/work/Desktop") == str(desktop)
    assert term.to_host_path("/work/Elsewhere/x") is None          # not a mounted folder: unchanged
    assert term.to_host_path("C:/Users/me/x.txt") is None
    switches(master=True, files=True)
    box = _box(tmp_path)
    name, args = box.normalize_call("file_write", {"path": "/work/Desktop/Game/index.html", "content": "<h1>DAWN</h1>"})
    assert args["path"] == str(desktop / "Game" / "index.html")
    assert "give file_read, file_write and the other file tools the Windows paths" in Terminal(str(tmp_path)).describe() \
        or Terminal(str(tmp_path)).mode == "host"


# ── the report the agent reads ──────────────────────────────────────────

def test_report_lists_ready_and_off_abilities_with_reasons(tmp_path, switches):
    report = capabilities.report(_box(tmp_path))
    assert report.startswith("## What you can use right now")
    ready = next(line for line in report.splitlines() if line.startswith("- Ready:"))
    assert "Reading files" in ready and "Terminal commands" not in ready
    assert "  - Creating and editing files: turned off in Settings › Permissions" in report
    assert "  - Terminal commands: turned off in Settings › Permissions" in report
    assert "- Approvals:" in report


def test_report_follows_offline_mode(tmp_path, switches, monkeypatch):
    monkeypatch.setattr(config, "privacy_mode", "offline", raising=False)
    monkeypatch.setattr(config, "searxng_base_url", "", raising=False)
    box = Toolbox(toolsets=["web"], session_id="cap", project_root=str(tmp_path))
    box.native["web_search"] = NativeTool("web_search", "search", {"type": "object", "properties": {}}, lambda a: "")
    report = capabilities.report(box)
    assert "Web search and reading pages: Offline mode is on" in report


# ── the prompt only teaches tools the agent has ──────────────────────────

def _prompt(**kwargs: Any) -> str:
    return build_system_prompt(persona=AgentPersona(id="echo", name="Echo"), soul_text="You are Echo.", **kwargs)


def test_prompt_drops_rules_for_tools_this_turn_does_not_offer():
    lean = _prompt(tool_names={"file_read", "calculate"})
    assert "take_screenshot and read_image" not in lean
    assert "research_notebook" not in lean
    assert "create_artifact" not in lean
    assert "Cards appear by themselves" not in lean
    full = _prompt(tool_names={"take_screenshot", "read_image", "research_notebook", "web_search", "create_artifact",
                               "weather_live"})
    assert "take_screenshot and read_image" in full and "research_notebook" in full
    assert "weather_live (weather card), web_search (source chips)." in full
    assert "stock_history" not in full


def test_prompt_without_tool_names_keeps_every_rule():
    assert "take_screenshot and read_image" in _prompt()


def test_prompt_says_files_are_read_only_when_edits_are_off(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "file_tool_root", str(tmp_path), raising=False)
    text = _prompt(project_root=str(tmp_path), can_write=False)
    assert "You may read files under" in text and "creating and editing files is off" in text
    assert "You may read and write files under" not in text


def test_prompt_carries_the_capability_report():
    assert "## What you can use right now" in _prompt(capabilities="## What you can use right now\n- Ready: x.")


# ── the loop stops re-trying what was refused ────────────────────────────

class _Scripted:
    def __init__(self, turns: list[ModelTurn]) -> None:
        self.turns = list(turns)
        self.calls: list[list[dict[str, Any]]] = []

    def stream_turn(self, messages, *, tools=None, on_reasoning=None, on_content=None, cancel=None, temperature=None,
                    max_tokens=None):
        self.calls.append([dict(m) for m in messages])
        turn = self.turns.pop(0)
        if turn.content and on_content:
            on_content(turn.content)
        return turn


class _Destructive:
    risk_level = "destructive"
    is_action = True
    category = "file_ops"
    origin = "native"


def _turn(client: _Scripted, tools: dict[str, Any], events: list[dict[str, Any]]) -> LeanTurn:
    box = Toolbox(toolsets=["none"], session_id="cap")
    box.native = {name: NativeTool(name, name, {"type": "object", "properties": {}}, func) for name, func in tools.items()}
    return LeanTurn(client=client, persona=AgentPersona(id="echo", name="Echo"), system_prompt="system", history=[],  # type: ignore[arg-type]
                    toolbox=box, session_id="cap", request_id="r", execution_id="e", emit=events.append,
                    cancel=threading.Event(), persist_tool_runs=False)


def test_a_denied_call_is_not_asked_for_again_in_the_same_request(monkeypatch):
    ran: list[str] = []
    client = _Scripted([
        ModelTurn(tool_calls=[ToolCall("c1", "wipe", '{"path": "a"}')]),
        ModelTurn(tool_calls=[ToolCall("c2", "wipe", '{"path": "a"}')]),
        ModelTurn(content="I left it alone."),
    ])
    events: list[dict[str, Any]] = []
    turn = _turn(client, {"wipe": lambda a: ran.append("wipe") or "wiped"}, events)
    monkeypatch.setattr(turn.toolbox, "entry", lambda name: _Destructive())

    def deny(event):
        events.append(event)
        if event["type"] == "approval_request":
            threading.Timer(0.05, lambda: get_approval_broker().resolve(event["id"], "deny")).start()

    turn._emit = deny
    assert turn.run("clean up").text == "I left it alone."
    assert ran == []
    assert sum(1 for e in events if e["type"] == "approval_request") == 1
    assert "already denied by the user" in client.calls[2][-1]["content"]


def test_three_failures_in_a_row_tell_the_agent_to_change_approach():
    client = _Scripted([
        ModelTurn(tool_calls=[ToolCall("c1", "fetch", '{"n": 1}')]),
        ModelTurn(tool_calls=[ToolCall("c2", "fetch", '{"n": 2}')]),
        ModelTurn(tool_calls=[ToolCall("c3", "fetch", '{"n": 3}')]),
        ModelTurn(content="It keeps failing."),
    ])
    events: list[dict[str, Any]] = []
    _turn(client, {"fetch": lambda a: "Error: connection refused"}, events).run("get it")
    assert "has failed" not in client.calls[2][-1]["content"]
    assert "fetch has failed 3 times in a row" in client.calls[3][-1]["content"]
    assert any(e["type"] == "fail_streak" and e["count"] == 3 for e in events)


def test_refusals_in_a_row_get_one_firm_note_and_too_many_end_the_turn():
    from agent.lean.loop import REFUSAL_STOP

    # Each call is a different blocked action, so exact-repeat detection never fires.
    blocked = lambda a: "Error: email_send is not available right now: turned off in Settings › Permissions (Email)."  # noqa: E731
    turns = [ModelTurn(tool_calls=[ToolCall(f"c{i}", "send", f'{{"to": "p{i}@x.test"}}')]) for i in range(REFUSAL_STOP + 2)]
    client = _Scripted(turns + [ModelTurn(content="unreachable")])
    events: list[dict[str, Any]] = []
    result = _turn(client, {"send": blocked}, events).run("email everyone")
    assert "all refused or blocked" in client.calls[3][-1]["content"]  # after the third refusal
    assert "all refused or blocked" not in client.calls[2][-1]["content"]
    assert sum(1 for e in events if e["type"] == "refusal_streak") == 1
    assert result.stop_reason == "refusals" and "refused or blocked" in result.text
    assert len(client.calls) == REFUSAL_STOP  # it stopped instead of probing on


def test_a_worker_out_of_steps_gets_one_tool_less_turn_to_report():
    client = _Scripted([ModelTurn(tool_calls=[ToolCall(f"c{i}", "look", f'{{"n": {i}}}')]) for i in range(2)]
                       + [ModelTurn(content='{"answer": "found 2 of 3", "sources": [], "confidence": "low"}')])
    turn = _turn(client, {"look": lambda a: "partial data"}, [])
    turn.max_steps_override = 2
    turn.final_grace = True
    result = turn.run("research it")
    assert result.stop_reason == "max_steps" and "found 2 of 3" in result.text
    assert "step budget is used up" in client.calls[-1][-1]["content"] and client.calls[-1][-1]["role"] == "tool"
    # The lead's own turns keep the honest "out of steps" note instead (no final_grace).
    plain = _turn(_Scripted([ModelTurn(tool_calls=[ToolCall(f"d{i}", "look", f'{{"n": {i}}}')]) for i in range(2)]),
                  {"look": lambda a: "partial data"}, [])
    plain.max_steps_override = 2
    assert "found" not in plain.run("research it").text


def test_a_success_resets_the_failure_streak():
    outputs = iter(["Error: busy", "Error: busy", "ok", "Error: busy"])
    client = _Scripted([ModelTurn(tool_calls=[ToolCall(f"c{i}", "fetch", f'{{"n": {i}}}')]) for i in range(4)]
                       + [ModelTurn(content="Done.")])
    events: list[dict[str, Any]] = []
    _turn(client, {"fetch": lambda a: next(outputs)}, events).run("go")
    assert not any(e["type"] == "fail_streak" for e in events)
