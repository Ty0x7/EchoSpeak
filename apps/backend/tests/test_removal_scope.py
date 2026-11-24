"""A removal stays within what the user asked to remove (agent/lean/removal_scope.py, #170).

Every case runs through the real loop and approval broker. The terminal tool runs the actual command with bash in a
disposable project under tmp_path, so globbing, `find -delete`, inline scripts and recursive removal behave as they
would for real. Each user file is created before the request starts.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from agent.lean import ask
from agent.lean import removal_scope as rs
from agent.lean.approvals import get_approval_broker
from agent.lean.provider import ModelTurn, ToolCall
from agent.lean.toolbox import NativeTool, Toolbox
from tests.test_lean_runtime import ScriptedClient, _Entry, _turn

NOTES = ("notes-draft.md", "notes-final.md")


@pytest.fixture(autouse=True)
def _permissions_on(monkeypatch):
    from config import config

    monkeypatch.setattr(config, "permissions_enabled", True, raising=False)


def _project(tmp_path: Path, files=NOTES, folders: dict[str, tuple[str, ...]] | None = None) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    past = time.time() - 3600
    for name in files:
        (root / name).write_text(name)
        os.utime(root / name, (past, past))
    for folder, inner in (folders or {}).items():
        (root / folder).mkdir()
        for name in inner:
            (root / folder / name).write_text(name)
    return root


def _call(i: int, tool: str, args: dict) -> ModelTurn:
    return ModelTurn(tool_calls=[ToolCall(f"c{i}", tool, json.dumps(args))])


def _run(monkeypatch, root: Path, request: str, steps: list[tuple[str, dict]], *, history=(), answers=(),
         mode: str = "always", interactive: bool = True):
    """Play ``steps`` as the model's tool calls; answer cards as an approving owner, questions with ``answers``."""
    from agent.lean import settings

    monkeypatch.setattr(settings, "approval_mode", lambda: mode)
    box = Toolbox(toolsets=["none"], session_id="t", project_root=str(root))
    ran: list[str] = []

    def terminal(args):
        ran.append(args["command"])
        done = subprocess.run(["bash", "-c", args["command"]], cwd=root, capture_output=True, text=True, timeout=20)
        return f"[exit {done.returncode}] {done.stdout}{done.stderr}"

    def write(args):
        (root / args["path"]).parent.mkdir(parents=True, exist_ok=True)
        (root / args["path"]).write_text(args.get("content", ""))
        return f"Wrote {args['path']}"

    box.native = {"terminal_run": NativeTool("terminal_run", "", {"type": "object"}, terminal),
                  "file_write": NativeTool("file_write", "", {"type": "object"}, write),
                  ask.ASK_USER: ask.ask_user_tool()}
    monkeypatch.setattr(box, "entry", lambda name: _Entry("destructive", True, "file_ops"))
    client = ScriptedClient([*(_call(i, tool, args) for i, (tool, args) in enumerate(steps)),
                             ModelTurn(content="Done.")])
    events: list[dict] = []
    turn = _turn(client, box, events)
    turn.history = list(history)
    turn.interactive = interactive
    pending = list(answers)

    def emit(event):
        events.append(event)
        if event["type"] == "approval_request":
            reply = pending.pop(0) if event.get("kind") == "question" else "allow"
            threading.Timer(0.01, lambda: get_approval_broker().resolve(event["id"], reply)).start()

    turn._emit = emit
    turn.run(request)
    blocked = [str(e.get("output", "")) for e in events
               if e["type"] == "tool_end" and str(e.get("output", "")).startswith("Not run:")]
    return {"left": sorted(p.name for p in root.iterdir()), "ran": ran, "blocked": blocked, "events": events}


def _rm(command: str) -> tuple[str, dict]:
    return ("terminal_run", {"command": command, "where": "sandbox"})


# ── weaknesses from source review: each failed on the first #171 head (614239e) ──

def test_modifying_a_user_file_does_not_make_it_the_agents(tmp_path, monkeypatch):
    root = _project(tmp_path)
    out = _run(monkeypatch, root, "Fix the typos in the notes and clean up any temporary files you create.",
               [("file_write", {"path": "notes-draft.md", "content": "fixed"}),
                ("file_write", {"path": "notes-final.md", "content": "fixed"}),
                _rm("rm notes-draft.md notes-final.md")])
    assert out["left"] == list(NOTES) and len(out["blocked"]) == 1


def test_an_atomic_rewrite_of_a_user_file_is_still_the_users(tmp_path, monkeypatch):
    root = _project(tmp_path)
    out = _run(monkeypatch, root, "Normalise the notes and remove your scratch files.",
               [_rm("cp notes-draft.md n.tmp && mv n.tmp notes-draft.md"),
                _rm("cp notes-final.md n.tmp && mv n.tmp notes-final.md"),
                _rm("rm notes-draft.md notes-final.md")])
    assert out["left"] == list(NOTES) and len(out["blocked"]) == 1


def test_a_user_file_moved_into_an_agent_folder_is_still_the_users(tmp_path, monkeypatch):
    root = _project(tmp_path)
    out = _run(monkeypatch, root, "Stage the notes for review, then tidy up after yourself.",
               [_rm("mkdir staging && mv notes-final.md staging/"), _rm("rm -r staging")])
    assert "staging" in out["left"] and (root / "staging" / "notes-final.md").exists()
    assert len(out["blocked"]) == 1


@pytest.mark.parametrize("request_text", [
    "Fix all the tests and delete the notes file.",
    "Delete the draft but keep all the others.",
    "Read both files, then delete the old notes file.",
])
def test_quantifiers_outside_the_removal_object_do_not_widen_it(tmp_path, monkeypatch, request_text):
    root = _project(tmp_path, files=(*NOTES, "todo.md"))
    out = _run(monkeypatch, root, request_text, [_rm("rm notes-draft.md notes-final.md todo.md")])
    assert out["left"] == sorted((*NOTES, "todo.md")) and out["blocked"]


def test_a_file_the_user_said_to_keep_is_never_removed(tmp_path, monkeypatch):
    root = _project(tmp_path)
    out = _run(monkeypatch, root, "Delete everything except notes-final.md.", [_rm("rm notes-draft.md notes-final.md")])
    assert out["left"] == list(NOTES) and "asked to keep notes-final.md" in out["blocked"][0]


def test_an_assistant_suggestion_does_not_authorize_a_removal(tmp_path, monkeypatch):
    root = _project(tmp_path)
    out = _run(monkeypatch, root, "yes", [_rm("rm notes-draft.md notes-final.md")],
               history=[{"role": "user", "content": "Delete the notes file in the project."},
                        {"role": "assistant", "content": "Should I delete notes-draft.md and notes-final.md?"}])
    assert out["left"] == list(NOTES) and out["blocked"]


def test_an_earlier_turn_does_not_authorize_this_one(tmp_path, monkeypatch):
    root = _project(tmp_path)
    out = _run(monkeypatch, root, "go ahead", [_rm("rm notes-draft.md notes-final.md")],
               history=[{"role": "user", "content": "Delete notes-draft.md and notes-final.md."},
                        {"role": "assistant", "content": "Do you want me to proceed?"}])
    assert out["left"] == list(NOTES) and out["blocked"]


def test_an_ambiguous_single_removal_is_not_a_guess(tmp_path, monkeypatch):
    root = _project(tmp_path)
    out = _run(monkeypatch, root, "Delete the notes file in the project.", [_rm("rm notes-final.md")])
    assert out["left"] == list(NOTES) and "more than one file" in out["blocked"][0]


def test_a_single_unnamed_removal_must_match_the_description(tmp_path, monkeypatch):
    root = _project(tmp_path, files=("config.yaml", "app.log"))
    out = _run(monkeypatch, root, "Delete the log.", [_rm("rm config.yaml")])
    assert out["left"] == ["app.log", "config.yaml"] and "doesn't match" in out["blocked"][0]


def test_a_recursive_removal_of_an_unnamed_user_folder(tmp_path, monkeypatch):
    root = _project(tmp_path, files=(), folders={"docs": ("guide.md", "faq.md")})
    out = _run(monkeypatch, root, "Remove the old stuff.", [_rm("rm -rf docs")])
    assert out["left"] == ["docs"] and out["blocked"]


@pytest.mark.parametrize("command", ["rm notes-*.md", "rm -f ./notes*", "rm -- *"])
def test_a_wildcard_is_checked_by_what_it_matches(tmp_path, monkeypatch, command):
    root = _project(tmp_path)
    out = _run(monkeypatch, root, "Delete the notes file in the project.", [_rm(command)])
    assert out["left"] == list(NOTES) and out["blocked"]


@pytest.mark.parametrize("command", ["rm -rf .", "rm -rf ./", "rm -rf ../project", "rm -rf ~/../.."])
def test_the_project_folder_itself_is_never_removed(tmp_path, monkeypatch, command):
    root = _project(tmp_path)
    out = _run(monkeypatch, root, "Delete everything in the project.", [_rm(command)])
    assert root.exists() and out["left"] == list(NOTES) and "project folder" in out["blocked"][0]


@pytest.mark.parametrize("command", [
    "find . -name 'notes*' -delete",
    "find . -name 'notes*' -exec rm {} +",
    "ls notes* | xargs rm",
    "python3 -c \"import os; os.remove('notes-final.md')\"",
    "python3 -c \"import pathlib; pathlib.Path('notes-final.md').unlink()\"",
    "mkdir -p sub && cd sub && rm ../notes-final.md",
    "f=notes-final.md; rm $f",
])
def test_indirect_removals_need_collective_intent(tmp_path, monkeypatch, command):
    root = _project(tmp_path)
    out = _run(monkeypatch, root, "Delete the notes file in the project.", [_rm(command)])
    assert set(NOTES) <= set(out["left"]) and out["blocked"]


@pytest.mark.parametrize("mode, interactive", [("autonomous", True), ("never", True), ("always", False)])
def test_the_check_holds_in_every_approval_mode(tmp_path, monkeypatch, mode, interactive):
    root = _project(tmp_path)
    out = _run(monkeypatch, root, "Delete the notes file in the project.", [_rm("rm notes-draft.md notes-final.md")],
               mode=mode, interactive=interactive)
    assert out["left"] == list(NOTES) and out["blocked"]
    assert not any(e["type"] == "approval_request" for e in out["events"])


def test_nothing_is_removed_without_a_request_to_remove(tmp_path, monkeypatch):
    root = _project(tmp_path)
    out = _run(monkeypatch, root, "Summarise the notes.", [_rm("rm notes-draft.md")])
    assert out["left"] == list(NOTES) and "didn't ask to remove anything" in out["blocked"][0]


# ── what stays allowed ─────────────────────────────────────────────────

def test_the_agents_own_temporary_files_and_folders_can_be_cleaned_up(tmp_path, monkeypatch):
    root = _project(tmp_path)
    out = _run(monkeypatch, root, "Count the words in the notes and tidy up anything you make.",
               [_rm("printf x > scratch_a.py && printf y > scratch_b.py && mkdir -p work/deep && printf z > work/deep/t"),
                _rm("rm -r scratch_a.py scratch_b.py work")])
    assert out["left"] == list(NOTES) and not out["blocked"]


@pytest.mark.parametrize("request_text, command, left", [
    ("Delete notes-draft.md and notes-final.md.", "rm notes-draft.md notes-final.md", []),
    ("Delete both notes files.", "rm notes-draft.md notes-final.md", []),
    ("Remove all the markdown files.", "rm notes-draft.md notes-final.md", []),
    ("Delete notes-draft.md, keep notes-final.md.", "rm notes-draft.md", ["notes-final.md"]),
    ("Delete all the notes files.", "find . -name 'notes*' -delete", []),
])
def test_named_and_collective_requests(tmp_path, monkeypatch, request_text, command, left):
    root = _project(tmp_path)
    out = _run(monkeypatch, root, request_text, [_rm(command)])
    assert out["left"] == left and not out["blocked"]


def test_a_collective_request_only_covers_what_it_describes(tmp_path, monkeypatch):
    root = _project(tmp_path, files=(*NOTES, "app.log", "db.log"))
    out = _run(monkeypatch, root, "Delete all the log files.", [_rm("rm app.log db.log notes-draft.md")])
    assert "notes-draft.md" in out["left"] and "doesn't match" in out["blocked"][0]
    out2 = _run(monkeypatch, root, "Delete all the log files.", [_rm("rm app.log db.log")])
    assert out2["left"] == list(NOTES) and not out2["blocked"]


def test_one_unambiguous_unnamed_target(tmp_path, monkeypatch):
    root = _project(tmp_path, files=("app.log", "config.yaml"))
    out = _run(monkeypatch, root, "Delete the log.", [_rm("rm app.log")])
    assert out["left"] == ["config.yaml"] and not out["blocked"]


QUESTION = ("ask_user", {"question": "Which notes file should I delete?", "options": [*NOTES, "Both"]})


def test_a_card_selection_binds_the_removal(tmp_path, monkeypatch):
    root = _project(tmp_path)
    out = _run(monkeypatch, root, "Delete the notes file in the project.",
               [QUESTION, _rm("rm notes-draft.md notes-final.md"), _rm("rm notes-draft.md")],
               answers=["notes-draft.md"])
    assert out["left"] == ["notes-final.md"] and len(out["blocked"]) == 1


def test_a_card_selection_of_both(tmp_path, monkeypatch):
    root = _project(tmp_path)
    out = _run(monkeypatch, root, "Delete the notes file in the project.",
               [QUESTION, _rm("rm notes-draft.md notes-final.md")], answers=["Both"])
    assert out["left"] == [] and not out["blocked"]


def test_a_typed_selection_names_exactly_what_it_selects(tmp_path, monkeypatch):
    root = _project(tmp_path)
    history = [{"role": "user", "content": "Delete the notes file in the project."},
               {"role": "assistant", "content": "Which one: notes-draft.md or notes-final.md?"}]
    out = _run(monkeypatch, root, "notes-draft.md", [_rm("rm notes-draft.md notes-final.md"), _rm("rm notes-draft.md")],
               history=history)
    assert out["left"] == ["notes-final.md"] and len(out["blocked"]) == 1


# ── the parts on their own ─────────────────────────────────────────────

def test_intent_clauses():
    assert not rs.intent("Please don't delete any files, just list them").removal
    assert rs.intent("Delete both notes files.").collective
    assert not rs.intent("Fix all tests and delete the notes file").collective
    keep = rs.intent("Delete everything except notes-final.md")
    assert keep.collective and "notes-final.md" in keep.protected_text
    assert rs.intent("notes-draft.md", previous_request="Delete the notes file").exact_only
    assert not rs.intent("notes-draft.md", previous_request="Summarise the notes").removal


def test_provenance_uses_paths_and_inodes(tmp_path):
    (tmp_path / "user.txt").write_text("u")
    record = rs.Provenance.snapshot(tmp_path)
    (tmp_path / "user.txt").write_text("edited")
    (tmp_path / "mine.txt").write_text("m")
    (tmp_path / "user.txt").rename(tmp_path / "moved.txt")
    assert record.created(tmp_path / "mine.txt")
    assert not record.created(tmp_path / "moved.txt")
    assert not rs.Provenance(root=tmp_path).created(tmp_path / "mine.txt")  # no complete record: the user's
