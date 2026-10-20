"""Always allow and autonomous mode: owner grants by class of action, walk-away runs that don't
stall on approvals, and the boundaries neither may cross."""

from __future__ import annotations

import threading
from typing import Any

import pytest

from agent.lean import approvals, settings
from agent.lean.approvals import GrantStore, get_approval_broker, scope_for
from agent.lean.loop import LeanTurn
from agent.lean.personas import AgentPersona
from agent.lean.provider import ModelTurn, ToolCall
from agent.lean.toolbox import NativeTool, Toolbox
from config import config


@pytest.fixture(autouse=True)
def grants(tmp_path):
    store = GrantStore(tmp_path / "approval-grants.json")
    approvals.set_grant_store(store)
    yield store
    approvals.set_grant_store(None)


class _Scripted:
    def __init__(self, turns: list[ModelTurn]) -> None:
        self.turns = list(turns)
        self.calls: list[list[dict[str, Any]]] = []

    def stream_turn(self, messages, **_kw):
        self.calls.append([dict(m) for m in messages])
        turn = self.turns.pop(0)
        if turn.content and _kw.get("on_content"):
            _kw["on_content"](turn.content)
        return turn


class _Entry:
    def __init__(self, risk: str = "moderate", is_action: bool = True, origin: str = "native") -> None:
        self.risk_level, self.is_action, self.origin, self.category = risk, is_action, origin, "general"


def _turn(tools: dict[str, Any], turns: list[ModelTurn], events: list[dict[str, Any]], *, interactive: bool = True,
          entry: Any = None, taint: list[str] | None = None) -> tuple[LeanTurn, _Scripted]:
    box = Toolbox(toolsets=["none"], session_id="auto")
    box.native = {n: NativeTool(n, n, {"type": "object", "properties": {}}, f) for n, f in tools.items()}
    if entry is not None:
        box.entry = lambda name: entry  # type: ignore[method-assign]
    client = _Scripted(turns)
    turn = LeanTurn(client=client, persona=AgentPersona(id="echo", name="Echo"), system_prompt="s", history=[],  # type: ignore[arg-type]
                    toolbox=box, session_id="auto", request_id="r", execution_id="e", emit=events.append,
                    cancel=threading.Event(), persist_tool_runs=False, interactive=interactive, taint=taint)
    return turn, client


def _mode(monkeypatch, mode: str) -> None:
    monkeypatch.setattr(settings, "approval_mode", lambda: mode)


# ── which classes can be granted ────────────────────────────────────────

def test_scopes_cover_routine_classes_and_never_the_sensitive_ones():
    assert scope_for("computer_action", {}).key == "desktop_control"
    assert scope_for("terminal", {"command": "npm test", "where": "host"}, "this runs directly on your PC, outside the sandbox").key == "terminal:host"
    assert scope_for("terminal", {"command": "pip install x", "network": True}, "this command needs internet access").key == "terminal:network"
    assert scope_for("mcp__obs__start_recording", {}, "this runs an external MCP action").key == "mcp:mcp__obs__start_recording"
    # Never grantable: dangerous or destructive commands, sends, identity, A2A, Rule of Two.
    assert scope_for("terminal", {"command": "rm -rf build", "where": "host"}, "this runs directly on your PC, outside the sandbox") is None
    assert scope_for("terminal", {"command": "x"}, "this can delete or overwrite files in your project") is None
    assert scope_for("file_delete", {"path": "a"}, "this can delete or overwrite work") is None
    assert scope_for("email_send", {}, "this sends something outside your machine") is None
    assert scope_for("soul_update", {}, "this changes how the agent behaves") is None
    assert scope_for("ask_remote_agent", {}, "this sends a task to an independent agent") is None
    assert scope_for("computer_action", {}, "outside content", "rule_of_two") is None
    # On-screen control after only screen reading can be granted, as "For this chat" already allows.
    assert scope_for("computer_action", {}, "screen", "screen_control").key == "desktop_control"


def test_persist_decision_saves_a_revocable_grant(grants):
    broker = get_approval_broker()
    approval = broker.open(session_id="s", request_id="r", tool="computer_action", summary="click", args={},
                           reason="this controls your desktop", scope=approvals.DESKTOP_SCOPE)
    assert approval.public()["persistable"] and approval.public()["scope_label"] == "Control the mouse and keyboard"
    broker.resolve(approval.id, "persist")
    assert approval.decision == "allow" and grants.has("desktop_control")
    assert [g["key"] for g in grants.list()] == ["desktop_control"]
    assert grants.remove("desktop_control") and not grants.has("desktop_control")


def test_persist_is_refused_for_an_ungrantable_approval():
    broker = get_approval_broker()
    approval = broker.open(session_id="s", request_id="r", tool="file_delete", summary="delete", args={}, reason="x")
    with pytest.raises(ValueError, match="can't be always allowed"):
        broker.resolve(approval.id, "persist")
    broker.resolve(approval.id, "deny")


# ── the loop ────────────────────────────────────────────────────────────

def test_an_always_allowed_class_runs_without_asking(grants, monkeypatch):
    _mode(monkeypatch, "smart")
    grants.add(approvals.DESKTOP_SCOPE)
    ran: list[str] = []
    events: list[dict[str, Any]] = []
    turn, _ = _turn({"computer_action": lambda a: ran.append("x") or "done"},
                    [ModelTurn(tool_calls=[ToolCall("c1", "computer_action", "{}")]), ModelTurn(content="Clicked.")], events)
    turn.run("click it")
    assert ran == ["x"]
    assert not any(e["type"] == "approval_request" for e in events)
    assert any(e["type"] == "auto_approved" and "Always allow" in e["note"] for e in events)


def test_autonomous_mode_approves_routine_classes_and_holds_the_rest(monkeypatch):
    _mode(monkeypatch, "autonomous")
    ran: list[str] = []
    events: list[dict[str, Any]] = []
    turn, client = _turn(
        {"computer_action": lambda a: ran.append("click") or "done",
         "file_delete": lambda a: ran.append("delete") or "deleted"},
        [ModelTurn(tool_calls=[ToolCall("c1", "computer_action", "{}"), ToolCall("c2", "file_delete", '{"path": "a"}')]),
         ModelTurn(content="Clicked; the delete needs you.")], events,
        entry=_Entry(risk="destructive"))
    result = turn.run("tidy up")
    assert ran == ["click"]                       # the delete never ran
    assert not any(e["type"] == "approval_request" for e in events)  # and nobody was asked or waited on
    assert any(e["type"] == "approval_held" and e["tool"] == "file_delete" for e in events)
    done = next(e for e in events if e["type"] == "agent_done")
    assert [h["tool"] for h in done["held"]] == ["file_delete"]
    assert "waiting for their approval" in client.calls[1][-1]["content"]
    assert result.success


def test_autonomous_mode_never_approves_a2a_or_rule_of_two(monkeypatch):
    _mode(monkeypatch, "autonomous")
    events: list[dict[str, Any]] = []
    ran: list[str] = []
    turn, _ = _turn({"ask_remote_agent": lambda a: ran.append("a2a") or "ok"},
                    [ModelTurn(tool_calls=[ToolCall("c1", "ask_remote_agent", '{"task": "x"}')]), ModelTurn(content="Held.")],
                    events)
    turn.run("ask the other agent")
    assert ran == [] and any(e["type"] == "approval_held" for e in events)


def test_autonomous_mode_doesnt_approve_for_sources_nobody_watches(monkeypatch):
    _mode(monkeypatch, "autonomous")
    events: list[dict[str, Any]] = []
    ran: list[str] = []
    turn, client = _turn({"computer_action": lambda a: ran.append("x") or "done"},
                         [ModelTurn(tool_calls=[ToolCall("c1", "computer_action", "{}")]), ModelTurn(content="Couldn't.")],
                         events, interactive=False)
    turn.run("click it")
    assert ran == [] and "can only be given in the EchoSpeak app" in client.calls[1][-1]["content"]


def test_autonomy_is_not_a_bypass_of_disabled_permissions(monkeypatch, tmp_path):
    # Desktop control switched off: the tool isn't in the toolbox, so nothing can approve it into existence.
    _mode(monkeypatch, "autonomous")
    monkeypatch.setattr(config, "enable_system_actions", False)
    from agent.lean.coding import coding_tools
    from agent.lean.terminal import Terminal

    box = Toolbox(extra_tools=coding_tools() + Terminal(str(tmp_path)).tools(), session_id="auto", project_root=str(tmp_path))
    assert "terminal" not in box.names and "file_edit" not in box.names
    assert "not available right now" in box.run("terminal", {"command": "dir"}).output


def test_always_allow_doesnt_unblock_a_blocked_app(grants, monkeypatch):
    from agent import tools as registry_tools

    monkeypatch.setattr(config, "enable_system_actions", True)
    monkeypatch.setattr(config, "allow_open_application", True)
    monkeypatch.setattr(config, "open_application_blocklist", ["regedit"])
    monkeypatch.setattr(registry_tools.subprocess, "Popen", lambda *a, **kw: pytest.fail("Unexpected launch"))
    for key in ("desktop_control", "terminal:host", "generation"):
        grants.add(approvals.Scope(key, key))
    _mode(monkeypatch, "autonomous")
    assert "Blocked apps" in registry_tools.open_application.invoke({"app": "regedit"})


def test_stop_still_ends_an_autonomous_run(monkeypatch):
    _mode(monkeypatch, "autonomous")
    events: list[dict[str, Any]] = []
    cancel = threading.Event()

    def slow(args):
        cancel.set()  # the user pressed Stop while this tool ran
        return "done"

    turn, _ = _turn({"computer_action": slow},
                    [ModelTurn(tool_calls=[ToolCall("c1", "computer_action", "{}")]), ModelTurn(content="more")], events)
    turn.cancel = cancel
    result = turn.run("keep clicking")
    assert not result.success and result.text == "Stopped."
