"""Structured file changes (agent/lean/file_changes.py): bounded, numbered, redacted diffs on tool_end
and on approval_request, computed from the disk (applied) or as a dry run (proposed)."""

from __future__ import annotations

import json
import threading
from typing import Any

import pytest

from agent.lean import file_changes as fc
from agent.lean.approvals import get_approval_broker
from agent.lean.coding import file_edit
from agent.lean.provider import ModelTurn, ToolCall
from agent.lean.toolbox import NativeTool, Toolbox
from agent.tools import _tool_execution_context
from tests.test_lean_runtime import ScriptedClient, _Entry, _turn

STATS = "".join(f"line {i}\n" for i in range(1, 41))


@pytest.fixture(autouse=True)
def _permissions_on(monkeypatch):
    from config import config

    for name in ("enable_system_actions", "allow_file_write"):
        monkeypatch.setattr(config, name, True)


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "stats.py").write_text(STATS, encoding="utf-8")
    token = _tool_execution_context.set({"strict_scope": True, "project_root": str(root), "lean_runtime": True,
                                         "enforce_tools": True, "allowed_tool_names": ["file_write", "file_delete"]})
    yield root
    _tool_execution_context.reset(token)


def _rows(change: dict) -> list[list[str]]:
    return [row for hunk in change["hunks"] for row in hunk["lines"]]


# ── the diff itself ─────────────────────────────────────────────────────
def test_hunks_are_numbered_with_three_lines_of_context():
    before = STATS
    after = STATS.replace("line 10\n", "line ten\nline 10b\n").replace("line 30\n", "")
    hunks, added, removed, truncated = fc.hunks_of(before, after)
    assert (added, removed, truncated) == (2, 2, False)
    assert len(hunks) == 2
    first = hunks[0]
    assert (first["old_start"], first["old_lines"], first["new_start"], first["new_lines"]) == (7, 7, 7, 8)
    assert first["lines"][:3] == [[" ", "line 7"], [" ", "line 8"], [" ", "line 9"]]
    assert first["lines"][3:6] == [["-", "line 10"], ["+", "line ten"], ["+", "line 10b"]]
    assert first["lines"][-1] == [" ", "line 13"]
    second = hunks[1]
    assert second["old_start"] == 27 and second["new_start"] == 28
    assert ["-", "line 30"] in second["lines"]


def test_caps_bound_rows_line_length_and_bytes():
    before = "".join(f"{i}\n" for i in range(1000))
    after = "".join(f"{i} changed {'x' * 250}\n" for i in range(1000))
    hunks, added, removed, truncated = fc.hunks_of(before, after)
    assert added == removed == 1000          # counts cover the whole change
    assert truncated
    rows = [r for h in hunks for r in h["lines"]]
    assert len(rows) <= fc.MAX_DIFF_LINES
    assert all(len(text) <= fc.MAX_LINE_CHARS + 1 for _tag, text in rows)
    record = fc.build(path=fc.Path("/tmp/x.py"), op="write", before=fc.Side(True, before), after=fc.Side(True, after))
    assert len(json.dumps(record).encode()) <= fc.MAX_RECORD_BYTES
    assert record["language"] == "python" and record["truncated"]


def test_binary_and_large_files_are_withheld(tmp_path):
    blob = tmp_path / "a.bin"
    blob.write_bytes(b"\x00\x01\x02" * 10)
    side = fc.read_side(blob)
    record = fc.build(path=blob, op="write", before=side, after=side)
    assert record["binary"] and record["hunks"] == [] and record["withheld_reason"] == "a binary file"
    big = fc.Side(True, "x\n" * (fc.MAX_DIFF_BYTES // 2 + 10))
    record = fc.build(path=tmp_path / "big.txt", op="write", before=fc.Side(True, ""), after=big)
    assert record["too_large"] and record["hunks"] == []


def test_secrets_are_redacted_from_the_diff():
    secret = "sk-test-0123456789abcdef"
    record = fc.build(path=fc.Path("/tmp/.env"), op="write", before=fc.Side(True, "A=1\n"),
                      after=fc.Side(True, f"A=1\nKEY={secret}\n"))
    from agent.lean import policy

    redacted = policy.redact_payload(record, [secret])
    assert secret not in json.dumps(redacted)
    # build() runs the configured-secret redaction itself
    import agent.lean.policy as p

    old = p._SECRET_CACHE.copy()
    p._SECRET_CACHE.update(at=10**12, values=[secret])
    try:
        record = fc.build(path=fc.Path("/tmp/.env"), op="write", before=fc.Side(True, "A=1\n"),
                          after=fc.Side(True, f"A=1\nKEY={secret}\n"))
    finally:
        p._SECRET_CACHE.update(old)
    assert secret not in json.dumps(record) and "[redacted secret]" in json.dumps(record)


# ── applied changes ─────────────────────────────────────────────────────
def test_snapshot_then_applied_for_an_edit(project):
    args = {"path": "src/stats.py", "old_text": "line 5\n", "new_text": "line five\n"}
    snap = fc.snapshot("file_edit", args)
    assert file_edit(args).startswith("Edited")
    change = fc.applied(snap, str(project))
    assert change["op"] == "edit" and change["status"] == "applied"
    assert change["display_path"] == "src/stats.py"
    assert change["path"] == str(project / "src" / "stats.py")
    assert (change["added"], change["removed"]) == (1, 1)
    assert ["-", "line 5"] in _rows(change) and ["+", "line five"] in _rows(change)


def test_file_write_marks_create_and_write_and_diffs_the_old_content(project):
    from agent.tools import file_write

    snap = fc.snapshot("file_write", {"path": "src/new.py", "content": "print('hi')\n"})
    file_write.func("src/new.py", "print('hi')\n")
    created = fc.applied(snap, str(project))
    assert created["op"] == "create" and created["added"] == 1 and created["removed"] == 0
    assert created["total_lines"] == 1

    body = STATS.replace("line 20\n", "line twenty\n")
    snap = fc.snapshot("file_write", {"path": "src/stats.py", "content": body})
    file_write.func("src/stats.py", body)
    wrote = fc.applied(snap, str(project))
    assert wrote["op"] == "write" and (wrote["added"], wrote["removed"]) == (1, 1)
    assert len(wrote["hunks"]) == 1 and wrote["hunks"][0]["old_start"] == 17


def test_delete_and_unchanged(project):
    snap = fc.snapshot("file_edit", {"path": "src/stats.py"})
    assert fc.applied(snap, str(project)) is None        # nothing changed: no record
    snap = fc.snapshot("file_delete", {"path": "src/stats.py"})
    (project / "src" / "stats.py").unlink()
    gone = fc.applied(snap, str(project))
    assert gone["op"] == "delete" and gone["removed"] == 40 and gone["added"] == 0


def test_toolbox_attaches_the_change_to_the_result_meta(tmp_path, monkeypatch):
    root = tmp_path / "p"
    root.mkdir()
    (root / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
    box = Toolbox(toolsets=["none"], session_id="t", project_root=str(root))
    box.native = {"file_edit": NativeTool(name="file_edit", description="", parameters={"type": "object", "properties": {}},
                                          func=file_edit)}
    result = box.run("file_edit", {"path": "a.txt", "old_text": "two", "new_text": "2"})
    assert result.ok
    change = result.meta["file_change"]
    assert change["display_path"] == "a.txt" and change["added"] == 1 and change["removed"] == 1
    # A failed call carries no change
    result = box.run("file_edit", {"path": "a.txt", "old_text": "missing", "new_text": "x"})
    assert not result.ok and "file_change" not in result.meta


# ── proposed changes (approval cards) ───────────────────────────────────
def test_proposed_edit_is_a_dry_run(project):
    target = project / "src" / "stats.py"
    change = fc.proposed("file_edit", {"path": "src/stats.py", "old_text": "line 3\n", "new_text": "line III\n"}, str(project))
    assert change["status"] == "proposed" and change["base_sha256"]
    assert ["+", "line III"] in _rows(change)
    assert target.read_text() == STATS                     # nothing applied
    missing = fc.proposed("file_edit", {"path": "src/stats.py", "old_text": "nope", "new_text": "x"}, str(project))
    assert missing["hunks"] == [] and "wasn't found" in missing["withheld_reason"]
    write = fc.proposed("file_write", {"path": "src/stats.py", "content": "short\n"}, str(project))
    assert write["op"] == "write" and write["removed"] == 40 and write["added"] == 1
    assert target.read_text() == STATS
    create = fc.proposed("file_write", {"path": "src/fresh.py", "content": "x = 1\n"}, str(project))
    assert create["op"] == "create" and not (project / "src" / "fresh.py").exists()
    delete = fc.proposed("file_delete", {"path": "src/stats.py"}, str(project))
    assert delete["op"] == "delete" and delete["removed"] == 40 and target.exists()
    assert fc.proposed("file_read", {"path": "src/stats.py"}) is None


# ── through the loop: tool_end and approval_request events ──────────────
def test_events_carry_file_change(tmp_path, monkeypatch):
    root = tmp_path / "p"
    root.mkdir()
    (root / "a.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    box = Toolbox(toolsets=["none"], session_id="t", project_root=str(root))
    box.native = {"file_edit": NativeTool(name="file_edit", description="", parameters={"type": "object", "properties": {}},
                                          func=file_edit)}
    monkeypatch.setattr(box, "entry", lambda name: _Entry("destructive", True, "file_ops"))
    args = json.dumps({"path": "a.py", "old_text": "y = 2", "new_text": "y = 3"})
    client = ScriptedClient([ModelTurn(tool_calls=[ToolCall("c1", "file_edit", args)]), ModelTurn(content="Done.")])
    events: list[dict[str, Any]] = []
    turn = _turn(client, box, events)

    def allow_when_asked(event):
        events.append(event)
        if event["type"] == "approval_request":
            threading.Timer(0.05, lambda: get_approval_broker().resolve(event["id"], "allow")).start()

    turn._emit = allow_when_asked
    turn.run("go")
    request = next(e for e in events if e["type"] == "approval_request")
    proposal = request["file_change"]
    assert proposal["status"] == "proposed" and proposal["tool_call_id"] == "c1"
    assert ["+", "y = 3"] in _rows(proposal)
    end = next(e for e in events if e["type"] == "tool_end")
    change = end["meta"]["file_change"]
    assert change["status"] == "applied" and change["tool_call_id"] == "c1" and change["display_path"] == "a.py"
    assert (root / "a.py").read_text() == "x = 1\ny = 3\n"
    # The saved timeline keeps only the small facts
    tool_item = next(i for i in turn.timeline if i.get("kind") == "tool")
    assert "file_change" not in tool_item.get("meta", {})


def test_always_mode_asks_for_native_file_edit_and_terminal(monkeypatch):
    from agent.lean import approvals, settings

    monkeypatch.setattr(settings, "approval_mode", lambda: "always")
    assert approvals.tool_needs_approval(None, "file_edit", {"path": "a"})[0] is True
    assert approvals.tool_needs_approval(None, "terminal", {"command": "ls"})[0] is True
    assert approvals.tool_needs_approval(None, "file_read", {"path": "a"})[0] is False
    monkeypatch.setattr(settings, "approval_mode", lambda: "smart")
    assert approvals.tool_needs_approval(None, "file_edit", {"path": "a"})[0] is False


@pytest.mark.parametrize(
    "actions, answer, decisions, contents, contradicted, summary_tool",
    [
        pytest.param(
            ("write", "terminal", "write"), "Done! I've deleted `notes-draft.md` from the project folder.",
            ("allow", "allow", "allow"), "", True, False, id="recorded-c1-recreation",
        ),
        pytest.param(
            ("delete", "write"), "`notes-draft.md` is no longer in the project.",
            ("allow", "allow"), "", True, False, id="stale-native-delete",
        ),
        pytest.param(
            ("delete", "write"), "`notes-draft.md` was deleted.",
            ("allow", "allow"), "", True, True, id="stale-completion-summary",
        ),
        pytest.param(
            ("delete",), "`notes-draft.md` was deleted.",
            ("allow",), None, False, True, id="genuine-completion-summary",
        ),
        pytest.param(
            ("write", "terminal"), "I deleted `notes-draft.md`.",
            ("allow", "allow"), None, False, False, id="genuine-terminal-delete",
        ),
        pytest.param(
            ("delete", "write"), "I deleted `notes-draft.md` and recreated it.",
            ("allow", "allow"), "", False, False, id="authorized-recreation",
        ),
        pytest.param(
            ("delete", "write"), "I deleted `notes-draft.md`.\nI then recreated it.",
            ("allow", "allow"), "", False, False, id="authorized-recreation-across-sentences",
        ),
        pytest.param(
            ("write", "delete"), "I kept `notes-draft.md` after you declined deletion.",
            ("allow", "deny"), "", False, False, id="denied-delete",
        ),
        pytest.param(
            ("write",), "`archive/notes-draft.md` was deleted.",
            ("allow",), "", False, False, id="distinct-path-with-same-basename",
        ),
        pytest.param(
            ("write",), "I removed `notes-draft.md` from the index.",
            ("allow",), "", False, False, id="removed-reference",
        ),
        pytest.param(
            ("write",), "> I deleted `notes-draft.md`.",
            ("allow",), "", False, False, id="quoted-example",
        ),
    ],
)
def test_streamed_file_removal_matches_final_disk_state(tmp_path, monkeypatch, actions, answer, decisions,
                                                       contents, contradicted, summary_tool):
    from agent.lean import settings

    root = tmp_path / "project"
    root.mkdir()
    draft = root / "notes-draft.md"
    draft.write_text("draft")
    final = root / "notes-final.md"
    final.write_text("final")
    (root / "archive").mkdir()
    monkeypatch.setattr(settings, "approval_mode", lambda: "always")
    box = Toolbox(toolsets=["none"], session_id="t", project_root=str(root))
    writes = []
    deletes = []

    def write(args):
        draft.write_text(args["content"])
        writes.append(args["content"])
        return "Wrote the draft"

    def delete(_args):
        draft.unlink()
        deletes.append(draft)
        return "Deleted the draft"

    def terminal(args):
        delete(args)
        return "[ok · 0.1s · sandbox · project]"

    names = {"write": "file_write", "delete": "file_delete", "terminal": "terminal_run"}
    box.native = {
        names[action]: NativeTool(name=names[action], description="", parameters={"type": "object"}, func=fn)
        for action, fn in {"write": write, "delete": delete, "terminal": terminal}.items()
    }
    box.native["complete_task"] = NativeTool("complete_task", "", {"type": "object"},
                                            lambda _args: "Completion summary submitted.", ends_turn=True)
    monkeypatch.setattr(box, "entry", lambda name: None if name == "complete_task"
                        else _Entry("destructive", True, "file_ops"))
    turns = []
    for i, action in enumerate(actions):
        arguments = ({"command": "rm notes-draft.md", "where": "sandbox"} if action == "terminal"
                     else {"path": draft.name, **({"content": ""} if action == "write" else {})})
        turns.append(ModelTurn(tool_calls=[ToolCall(f"c{i}", names[action], json.dumps(arguments))]))
    conclusion = (ModelTurn(tool_calls=[ToolCall("done", "complete_task", json.dumps({"summary": answer}))])
                  if summary_tool else ModelTurn(content=answer))
    client = ScriptedClient([*turns, conclusion])
    events = []
    turn = _turn(client, box, events)
    approvals = iter(decisions)

    def emit(event):
        events.append(event)
        if event["type"] == "approval_request":
            decision = next(approvals)
            threading.Timer(0.01, lambda: get_approval_broker().resolve(event["id"], decision)).start()

    turn._emit = emit
    result = turn.run("Remove only the draft.")
    assert len(client.calls) == len(actions) + 1
    assert len(writes) == actions.count("write")
    assert len(deletes) == sum(action in {"delete", "terminal"} and decision == "allow"
                               for action, decision in zip(actions, decisions, strict=True))
    assert [event["decision"] for event in events if event["type"] == "approval_resolved"] == list(decisions)
    assert (draft.read_text() if draft.exists() else None) == contents
    assert final.read_text() == "final"
    done = next(event for event in events if event["type"] == "agent_done")
    assert done["text"] == result.text
    assert done["completed"] == result.completed == (answer if summary_tool and not contradicted else "")
    if contradicted:
        assert result.stop_reason == "unverified_claim"
        assert any(event.get("check") == "final_file_state" for event in events)
        assert "notes-draft.md" in result.text and "still present" in result.text
        assert any(event["type"] == "text_replace" and event["text"] == result.text for event in events)
        assert answer not in result.text
    else:
        assert result.text == answer


def test_final_state_observations_stay_in_scope(tmp_path):
    first = tmp_path / "first"
    first.mkdir()
    second = tmp_path / "second"
    second.mkdir()
    private = second / "notes-draft.md"
    private.write_text("second project")
    target = first / private.name
    target.symlink_to(private)
    box = Toolbox(toolsets=["none"], session_id="t", project_root=str(first))
    receipt = {"kind": "tool", "outcome": "ok", "name": "file_write",
               "effects": [{"kind": "file", "path": str(target), "exists": True}]}
    assert box.contradicted_file_removals("I deleted `notes-draft.md`.", [receipt]) == []
    assert box.contradicted_file_removals(f"I deleted `{private}`.", [receipt]) == []
    assert private.read_text() == "second project"
