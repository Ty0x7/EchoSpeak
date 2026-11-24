"""A removal stays within what the user asked to remove (agent/lean/removal_scope.py)."""
from __future__ import annotations

import json
import os
import shlex
import threading
import time

import pytest

from agent.lean import removal_scope as rs
from agent.lean.approvals import get_approval_broker
from agent.lean.provider import ModelTurn, ToolCall
from agent.lean.toolbox import NativeTool, Toolbox
from tests.test_lean_runtime import ScriptedClient, _Entry, _turn


@pytest.fixture(autouse=True)
def _permissions_on(monkeypatch):
    from config import config

    monkeypatch.setattr(config, "permissions_enabled", True, raising=False)


def test_collective_wording():
    for text in ("Delete all the notes", "remove both drafts", "delete these files", "rm the 3 logs",
                 "delete notes-*.md", "remove every backup", "delete two of them"):
        assert rs.collective(text), text
    for text in ("Delete the notes file in the project.", "remove the old report", "delete it", "yes"):
        assert not rs.collective(text), text


def test_named_matches_whole_names_and_stems(tmp_path):
    assert rs.named(tmp_path / "notes-draft.md", "delete notes-draft.md")
    assert rs.named(tmp_path / "notes-draft.md", "delete notes-draft please")
    assert not rs.named(tmp_path / "notes-draft.md", "delete the notes file")
    assert not rs.named(tmp_path / "notes.md", "delete my-notes-old.txt")
    assert not rs.named(tmp_path / "a.md", "delete a file")  # stems under three characters never match words


def test_files_changed_during_the_request_are_the_agents_own(tmp_path):
    old, new = tmp_path / "old.txt", tmp_path / "new.txt"
    old.write_text("x")
    new.write_text("y")
    started = time.time()
    os.utime(old, (started - 60, started - 60))
    os.utime(new, (started + 1, started + 1))
    assert rs.unnamed_targets([str(old), str(new)], "clean up", started) == [str(old)]
    assert rs.unnamed_targets([str(old)], "clean up", started, own=[str(old)]) == []


def test_problem_allows_one_unnamed_target_and_collective_requests():
    assert rs.problem(["/p/a"], set(), "delete the log") == ""
    assert rs.problem(["/p/a", "/p/b"], set(), "delete all logs") == ""
    assert "Ask the user" in rs.problem(["/p/a", "/p/b"], set(), "delete the log")
    assert "Ask the user" in rs.problem(["/p/b"], {"/p/a"}, "delete the log")


def test_is_removal():
    assert rs.is_removal("terminal", {"command": "rm a b"})
    assert rs.is_removal("terminal_run", {"command": "cd x && unlink y"})
    assert rs.is_removal("file_delete", {"path": "a"})
    assert not rs.is_removal("terminal", {"command": "git rm-cache-helper --list"})
    assert not rs.is_removal("terminal", {"command": "python farm.py"})


AMBIGUOUS = "Delete the notes file in the project."
BOTH = "rm notes-draft.md notes-final.md"


@pytest.mark.parametrize(
    "request_text, commands, history, expect_left, expect_blocked",
    [
        pytest.param(AMBIGUOUS, [BOTH], [], {"notes-draft.md", "notes-final.md"}, 1, id="recorded-c1-both"),
        pytest.param(AMBIGUOUS, ["rm notes-draft.md", "rm notes-final.md"], [], {"notes-final.md"}, 1,
                     id="one-at-a-time"),
        pytest.param(AMBIGUOUS, ["rm notes-draft.md"], [], {"notes-final.md"}, 0, id="single-unnamed-allowed"),
        pytest.param("Delete both notes files.", [BOTH], [], set(), 0, id="collective-request"),
        pytest.param("Delete notes-draft.md and notes-final.md", [BOTH], [], set(), 0, id="both-named"),
        pytest.param("yes", [BOTH],
                     [{"role": "user", "content": AMBIGUOUS},
                      {"role": "assistant", "content": "Delete notes-draft.md and notes-final.md?"}],
                     set(), 0, id="confirmed-answer-to-question"),
        pytest.param("notes-draft.md", ["rm notes-draft.md"],
                     [{"role": "user", "content": AMBIGUOUS},
                      {"role": "assistant", "content": "Which notes file: notes-draft.md or notes-final.md?"}],
                     {"notes-final.md"}, 0, id="clarified-choice"),
    ],
)
def test_ambiguous_removal_is_not_widened(tmp_path, monkeypatch, request_text, commands, history,
                                          expect_left, expect_blocked):
    from agent.lean import settings

    root = tmp_path / "project"
    root.mkdir()
    past = time.time() - 3600
    for name in ("notes-draft.md", "notes-final.md"):
        (root / name).write_text(name)
        os.utime(root / name, (past, past))
    monkeypatch.setattr(settings, "approval_mode", lambda: "always")
    box = Toolbox(toolsets=["none"], session_id="t", project_root=str(root))
    ran = []

    def terminal(args):
        ran.append(args["command"])
        for token in shlex.split(args["command"])[1:]:
            (root / token).unlink()
        return "[ok · 0.1s · sandbox · project]"

    box.native = {"terminal_run": NativeTool("terminal_run", "", {"type": "object"}, terminal)}
    monkeypatch.setattr(box, "entry", lambda name: _Entry("destructive", True, "file_ops"))
    turns = [ModelTurn(tool_calls=[ToolCall(f"c{i}", "terminal_run", json.dumps({"command": c, "where": "sandbox"}))])
             for i, c in enumerate(commands)]
    client = ScriptedClient([*turns, ModelTurn(content="Which file did you mean?")])
    events = []
    turn = _turn(client, box, events)
    turn.history = history

    def emit(event):
        events.append(event)
        if event["type"] == "approval_request":
            threading.Timer(0.01, lambda: get_approval_broker().resolve(event["id"], "allow")).start()

    turn._emit = emit
    turn.run(request_text)
    assert {p.name for p in root.iterdir()} == expect_left
    blocked = [e for e in events if e["type"] == "tool_end" and "Ask the user which one" in str(e.get("output", ""))]
    assert len(blocked) == expect_blocked
    # A blocked removal never reaches the approval card or the tool.
    assert len(ran) == len(commands) - expect_blocked
    assert sum(e["type"] == "approval_request" for e in events) == len(commands) - expect_blocked
    if expect_blocked:
        tool_messages = [m for m in client.calls[-1] if m.get("role") == "tool"]
        assert "notes-draft.md, notes-final.md" in tool_messages[-1]["content"]


def test_agent_created_files_can_be_cleaned_up_together(tmp_path, monkeypatch):
    from agent.lean import settings

    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setattr(settings, "approval_mode", lambda: "always")
    box = Toolbox(toolsets=["none"], session_id="t", project_root=str(root))

    def make(_args):
        for name in ("scratch_a.py", "scratch_b.py"):
            (root / name).write_text("x")
        return "made"

    def terminal(args):
        for token in shlex.split(args["command"])[1:]:
            (root / token).unlink()
        return "[ok]"

    box.native = {"make": NativeTool("make", "", {"type": "object"}, make),
                  "terminal_run": NativeTool("terminal_run", "", {"type": "object"}, terminal)}
    monkeypatch.setattr(box, "entry", lambda name: _Entry("destructive", True, "file_ops"))
    client = ScriptedClient([
        ModelTurn(tool_calls=[ToolCall("m", "make", "{}")]),
        ModelTurn(tool_calls=[ToolCall("r", "terminal_run", json.dumps({"command": "rm scratch_a.py scratch_b.py"}))]),
        ModelTurn(content="Cleaned up."),
    ])
    events = []
    turn = _turn(client, box, events)

    def emit(event):
        events.append(event)
        if event["type"] == "approval_request":
            threading.Timer(0.01, lambda: get_approval_broker().resolve(event["id"], "allow")).start()

    turn._emit = emit
    turn.run("Check the inventory and tidy up anything you make.")
    assert list(root.iterdir()) == []
