"""Execution evidence (agent/lean/evidence.py): outcomes set where they're known, effects observed, one boundary for host commands."""

from __future__ import annotations

import threading
from typing import Any

import pytest

from agent.lean import evidence, terminal
from agent.lean.approvals import tool_needs_approval
from agent.lean.job import unbacked_screen_claim
from agent.lean.provider import ModelTurn, ToolCall
from agent.lean.toolbox import NativeTool, Toolbox, ToolResult
from agent.learning.episodes import tool_rows
from tests.test_capabilities import _Scripted, _turn


@pytest.fixture
def folder(monkeypatch, tmp_path):
    monkeypatch.setattr(evidence, "_resolve", lambda raw: (tmp_path / str(raw)) if str(raw or "").strip() else None)
    return tmp_path


# ── outcomes are set where they're known ────────────────────────────────────
def test_a_result_defaults_its_outcome_from_ok():
    assert ToolResult(True, "x", 0).outcome == "ok" and ToolResult(False, "x", 0).outcome == "failed"
    assert ToolResult(False, "x", 0, outcome="refused").outcome == "refused"


def test_every_way_a_call_does_not_run_has_its_own_outcome(monkeypatch):
    from agent.lean import approvals

    calls =[ToolCall("c1", "wipe", '{"path": "a"}'),        # denied by the owner
             ToolCall("c2", "wipe", '{"path": "a"}'),        # the same call again: refused (already denied)
             ToolCall("c3", "nope", "{}"),                   # a tool that doesn't exist: skipped
             ToolCall("c4", "look", '{"n": 1}')]             # runs
    client = _Scripted([ModelTurn(tool_calls=[c]) for c in calls] + [ModelTurn(content="done")])
    events: list[dict[str, Any]] = []
    turn = _turn(client, {"wipe": lambda a: "wiped", "look": lambda a: "seen"}, events)
    monkeypatch.setattr(approvals, "tool_needs_approval", lambda entry, name, args: (name == "wipe", "it deletes"))
    monkeypatch.setattr("agent.lean.loop.tool_needs_approval", lambda entry, name, args: (name == "wipe", "it deletes"))
    broker = approvals.get_approval_broker()

    def deny(event):
        events.append(event)
        if event["type"] == "approval_request":
            threading.Timer(0.05, lambda: broker.resolve(event["id"], "deny")).start()

    turn._emit = deny
    turn.run("clean up")
    outcomes = {item["id"]: item["outcome"] for item in turn.timeline if item.get("kind") == "tool"}
    assert outcomes == {"c1": "denied", "c2": "refused", "c3": "skipped", "c4": "ok"}
    ends = {e["id"]: e["outcome"] for e in events if e["type"] == "tool_end"}
    assert ends == outcomes


def test_a_privacy_block_and_a_switched_off_tool_are_refusals(monkeypatch):
    from agent import privacy

    box = Toolbox(toolsets=["none"])
    box.native = {"look": NativeTool("look", "l", {"type": "object", "properties": {}}, lambda a: "seen")}
    monkeypatch.setattr(privacy, "tool_block_reason", lambda name, args: "Offline mode is on." if name == "look" else "")
    assert box.run("look", {}).outcome == "refused"
    monkeypatch.setattr(privacy, "tool_block_reason", lambda name, args: "")
    assert box.run("made_up_tool", {}).outcome == "skipped"


def test_the_refusal_breaker_counts_outcomes_not_wording():
    from agent.lean.loop import REFUSAL_STREAK_NOTE

    # Worded nothing like the old refusal phrases, but the owner denied each one.
    turns = [ModelTurn(tool_calls=[ToolCall(f"c{i}", "send", f'{{"to": "p{i}"}}')]) for i in range(REFUSAL_STREAK_NOTE)]
    client = _Scripted(turns + [ModelTurn(content="stopped")])
    turn = _turn(client, {"send": lambda a: "sent"}, [])
    turn._approve = lambda call, name, args, step: (False, "Nope.", "denied")  # type: ignore[method-assign]
    turn.run("message everyone")
    assert "all refused or blocked" in client.calls[REFUSAL_STREAK_NOTE][-1]["content"]


# ── effects are observed, not taken from the tool's word ─────────────────────
def test_a_write_that_landed_is_observed_on_disk(folder):
    (folder / "mics.md").write_text("# Mics", encoding="utf-8")
    effects, problem = evidence.observe("file_write", {"path": "mics.md"}, "Wrote mics.md")
    assert problem == "" and effects[0]["exists"] and effects[0]["bytes"] == 6
    assert evidence.written_files(effects) == {"mics.md"}


def test_a_write_that_is_not_there_is_a_failure(folder):
    effects, problem = evidence.observe("file_write", {"path": "missing.md"}, "Wrote missing.md")
    assert "isn't there" in problem and not effects[0]["exists"]
    assert evidence.written_files(effects) == set()


def test_a_delete_that_left_the_file_is_a_failure(folder):
    (folder / "old.txt").write_text("x", encoding="utf-8")
    assert "still there" in evidence.observe("file_delete", {"path": "old.txt"}, "Deleted old.txt")[1]


def test_the_toolbox_turns_an_unobserved_write_into_a_failure(folder):
    box = Toolbox(toolsets=["none"])
    box.native = {"file_write": NativeTool("file_write", "w", {"type": "object", "properties": {}}, lambda a: f"Wrote {a['path']}")}
    result = box.run("file_write", {"path": "ghost.md", "content": "x"})
    assert not result.ok and result.outcome == "failed" and "isn't there" in result.output


@pytest.mark.parametrize("output, effect", [
    ("[ok · 5.6s · sandbox · C:\\Users\\me\\Documents]\nfile list", {"where": "sandbox", "exit_code": 0}),
    ("[exit code 2 · 11.2s · this PC · C:\\x]\n[stderr]\nnope", {"where": "host", "exit_code": 2}),
    ("[running · process ab12cd34 · this PC · C:\\x]\nOutput so far:", {"where": "host", "running": "ab12cd34"}),
])
def test_terminal_runs_record_where_and_how_they_ended(output, effect):
    [observed] = evidence.observe("terminal", {"command": "dir"}, output)[0]
    assert observed["kind"] == "process" and {k: observed[k] for k in effect} == effect


def test_a_screen_action_that_changed_nothing_has_no_effect():
    effects, _ = evidence.observe("computer_action", {}, "Executed 1 actions.", {"screen_change": 0.001})
    assert evidence.outcome_for(True, "ok", effects) == "no_effect"
    effects, _ = evidence.observe("computer_action", {}, "Executed 1 actions.", {"screen_change": 0.35})
    assert evidence.outcome_for(True, "ok", effects) == "ok"


def test_a_claimed_change_on_screen_needs_one():
    claim = "The Start Menu has successfully opened on your desktop."
    assert unbacked_screen_claim(claim, 0.001) == claim
    assert unbacked_screen_claim(claim, 0.30) == ""
    assert unbacked_screen_claim(claim, None) == ""  # nothing acted on the screen


def test_the_start_menu_claim_is_challenged_when_the_click_missed():
    client = _Scripted([
        ModelTurn(tool_calls=[ToolCall("c1", "computer_action", '{"observation_id": "o", "actions": []}')]),
        ModelTurn(content="The Start Menu has successfully opened on your desktop."),
        ModelTurn(content="The click didn't open the Start menu; the screen didn't change."),
    ])
    missed = lambda a: ToolResult(True, "Executed 1 actions. The screen looks unchanged.", 0,  # noqa: E731
                                  meta={"screen_change": 0.001})
    turn = _turn(client, {"computer_action": missed}, [])
    turn._approve = lambda call, name, args, step: (True, "", "")  # type: ignore[method-assign]
    result = turn.run("click Start")
    assert "isn't true yet" in client.calls[2][-1]["content"]
    assert result.text.startswith("The click didn't open")
    row = next(i for i in turn.timeline if i.get("kind") == "tool")
    assert row["outcome"] == "no_effect" and row["summary"] == "No visible change on screen"


# ── learning reads outcomes ──────────────────────────────────────────────────
def test_learning_grades_from_outcomes_and_falls_back_to_text_for_old_records():
    rows = tool_rows([
        {"kind": "tool", "name": "computer_action", "status": "done", "output": "Executed", "outcome": "no_effect"},
        {"kind": "tool", "name": "terminal", "status": "failed", "output": "Nope.", "outcome": "denied"},
        {"kind": "tool", "name": "file_write", "status": "done", "output": "Wrote a", "outcome": "ok"},
        {"kind": "tool", "name": "terminal", "status": "failed", "output": "Blocked by EchoSpeak's safety policy: x"},
    ])
    assert [(r["ok"], r["not_run"]) for r in rows] == [(False, False), (False, True), (True, False), (False, True)]


# ── one boundary for commands on this PC ─────────────────────────────────────
class _Entry:
    is_action = True
    risk_level = "moderate"
    category = "terminal"
    origin = "native"


def test_auto_falling_back_to_this_pc_asks_like_where_host(monkeypatch):
    monkeypatch.setattr(terminal, "resolved_mode", lambda: "host")
    monkeypatch.setattr(terminal, "execution_mode", lambda: "auto")
    needs, reason = tool_needs_approval(_Entry(), "terminal", {"command": "python cleanup.py"})
    assert needs and "outside the sandbox" in reason
    from agent.lean.approvals import scope_for

    scope = scope_for("terminal", {"command": "python cleanup.py"}, reason)
    assert scope is not None and scope.key == "terminal:host" and scope.autonomous  # Always allow and Autonomous cover it


def test_choosing_this_pc_keeps_ordinary_commands_unasked(monkeypatch):
    monkeypatch.setattr(terminal, "resolved_mode", lambda: "host")
    monkeypatch.setattr(terminal, "execution_mode", lambda: "host")
    assert not tool_needs_approval(_Entry(), "terminal", {"command": "python cleanup.py"})[0]
    assert tool_needs_approval(_Entry(), "terminal", {"command": "rd /s /q C:\\x"})[0]
