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
    root.mkdir(parents=True)
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
    assert rs.intent("notes-draft.md", previous_request="Delete the notes file").selected_text == "notes-draft.md"
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


# ── second source review (04dc9f4): selections bound to what was presented; indirect removals resolved ──

def test_both_selects_only_what_the_question_presented(tmp_path, monkeypatch):
    root = _project(tmp_path, files=(*NOTES, "todo.md"))
    out = _run(monkeypatch, root, "Delete the notes file in the project.",
               [QUESTION, _rm("rm notes-draft.md notes-final.md todo.md"), _rm("rm notes-draft.md notes-final.md")],
               answers=["Both"])
    assert out["left"] == ["todo.md"] and len(out["blocked"]) == 1 and "todo.md" in out["blocked"][0]


def test_all_does_not_stretch_to_a_wildcard_over_more_files(tmp_path, monkeypatch):
    root = _project(tmp_path, files=(*NOTES, "todo.md"))
    out = _run(monkeypatch, root, "Delete the notes file in the project.", [QUESTION, _rm("rm *.md")], answers=["All"])
    assert out["left"] == sorted((*NOTES, "todo.md")) and out["blocked"]


def test_both_does_not_authorize_an_indirect_command(tmp_path, monkeypatch):
    root = _project(tmp_path, files=(*NOTES, "todo.md"))
    out = _run(monkeypatch, root, "Delete the notes file in the project.",
               [QUESTION, _rm("ls *.md | xargs rm")], answers=["Both"])
    assert out["left"] == sorted((*NOTES, "todo.md")) and out["blocked"]


def test_an_answer_to_an_unrelated_question_selects_nothing_to_remove(tmp_path, monkeypatch):
    root = _project(tmp_path)
    question = ("ask_user", {"question": "Which notes file should I summarise?", "options": list(NOTES)})
    out = _run(monkeypatch, root, "Summarise one of the notes for me.", [question, _rm("rm notes-final.md")],
               answers=["notes-final.md"])
    assert out["left"] == list(NOTES) and out["blocked"]


@pytest.mark.parametrize("command", [
    "find . -type f -delete",
    "find . -name '*.md' -delete",
    "find . -type f -exec rm -f {} +",
])
def test_a_resolved_find_is_checked_against_the_collective_request(tmp_path, monkeypatch, command):
    root = _project(tmp_path, files=(*NOTES, "todo.md"))
    out = _run(monkeypatch, root, "Delete all the notes files.", [_rm(command)])
    assert "todo.md" in out["left"] and "doesn't match" in out["blocked"][0]


@pytest.mark.parametrize("command", [
    "find . -name 'notes*' -delete",
    "find . -maxdepth 1 -name 'notes-*.md' -exec rm {} \\;",
])
def test_a_resolved_find_within_the_request_is_allowed(tmp_path, monkeypatch, command):
    root = _project(tmp_path, files=(*NOTES, "todo.md"))
    out = _run(monkeypatch, root, "Delete all the notes files.", [_rm(command)])
    assert out["left"] == ["todo.md"] and not out["blocked"]


@pytest.mark.parametrize("command", [
    "ls notes* | xargs rm",
    "python3 -c \"import os; [os.remove(f) for f in os.listdir('.') if f.startswith('notes')]\"",
    "find . -name 'notes*' -exec sh -c 'rm \"$0\"' {} \\;",
    "find . -name 'notes*' -fprint /dev/null -delete",
    "rsync -a --delete empty/ ./",
])
def test_collective_intent_alone_does_not_authorize_an_unresolved_command(tmp_path, monkeypatch, command):
    root = _project(tmp_path, files=(*NOTES, "todo.md"))
    (root / "empty").mkdir()
    out = _run(monkeypatch, root, "Delete all the notes files.", [_rm(command)])
    assert set(NOTES) <= set(out["left"]) and "couldn't be listed" in out["blocked"][0]


def test_an_unresolved_command_the_user_gave_verbatim_may_run(tmp_path, monkeypatch):
    root = _project(tmp_path, files=(*NOTES, "todo.md"))
    out = _run(monkeypatch, root, "Please run `ls notes* | xargs rm` in the project.", [_rm("ls notes* | xargs rm")])
    assert out["left"] == ["todo.md"] and not out["blocked"]


def _git_project(tmp_path):
    root = _project(tmp_path, files=(*NOTES, "todo.md"))
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.test",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.test"}
    for argv in (["git", "init", "-q"], ["git", "add", "todo.md"], ["git", "commit", "-qm", "base"]):
        subprocess.run(argv, cwd=root, check=True, env=env)
    return root


def test_git_clean_is_resolved_by_its_dry_run(tmp_path, monkeypatch):
    root = _git_project(tmp_path)
    out = _run(monkeypatch, root, "Delete all the untracked files.", [_rm("git clean -f")])
    assert out["left"] == [".git", "todo.md"] and not out["blocked"]


def test_git_clean_is_still_checked_against_the_description(tmp_path, monkeypatch):
    root = _git_project(tmp_path)
    out = _run(monkeypatch, root, "Delete all the log files.", [_rm("git clean -f")])
    assert set(NOTES) <= set(out["left"]) and "doesn't match" in out["blocked"][0]


@pytest.mark.parametrize("command", [
    "grep -n \"rm -rf\" notes-draft.md",
    "echo 'never rm notes-final.md' > reminder.txt",
    "git log --oneline -- notes-draft.md",
])
def test_text_that_only_mentions_a_removal_is_not_one(tmp_path, monkeypatch, command):
    root = _project(tmp_path)
    out = _run(monkeypatch, root, "Summarise the notes.", [_rm(command)])
    assert set(NOTES) <= set(out["left"]) and not out["blocked"] and out["ran"] == [command]


@pytest.mark.parametrize("command", ["bash -c 'rm notes-draft.md'", "eval 'rm notes-final.md'"])
def test_a_removal_run_through_a_shell_string_is_not_listable(tmp_path, monkeypatch, command):
    root = _project(tmp_path)
    out = _run(monkeypatch, root, "Delete all the notes files.", [_rm(command)])
    assert set(NOTES) <= set(out["left"]) and "couldn't be listed" in out["blocked"][0]


def test_resolving_git_clean_never_runs_a_repository_configured_program(tmp_path, monkeypatch):
    root = _git_project(tmp_path)
    marker = tmp_path / "fsmonitor-ran"
    hook = tmp_path / "monitor.sh"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(0o755)
    subprocess.run(["git", "config", "core.fsmonitor", str(hook)], cwd=root, check=True)
    call = rs.removal("terminal_run", {"command": "git clean -f"}, root)
    assert call is not None and call.resolved_by == "git clean -n" and {p.name for p in call.targets} == set(NOTES)
    assert not marker.exists()


# ── final check: a read-only preview must cover everything the real command removes ──

def _archive_project(tmp_path):
    root = _project(tmp_path, files=(*NOTES, "todo.md"), folders={"notes-archive": ("budget.xlsx",)})
    return root


def test_a_find_action_placed_before_its_tests_is_previewed_where_it_acts(tmp_path, monkeypatch):
    root = _archive_project(tmp_path)
    out = _run(monkeypatch, root, "Delete all the notes files.", [_rm("find . -type f -delete -name 'notes*'")])
    assert "todo.md" in out["left"] and (root / "notes-archive" / "budget.xlsx").exists() and out["blocked"]


@pytest.mark.parametrize("command", [
    "find . -name 'notes*' -exec rm -rf {} +",
    "rm -rf notes-archive notes-draft.md notes-final.md",
    "rm -r notes-*",
])
def test_a_recursive_removal_is_checked_file_by_file(tmp_path, monkeypatch, command):
    root = _archive_project(tmp_path)
    out = _run(monkeypatch, root, "Delete all the notes files.", [_rm(command)])
    assert (root / "notes-archive" / "budget.xlsx").exists() and "budget.xlsx" in out["blocked"][0]


def test_naming_a_folder_covers_what_is_inside_it(tmp_path, monkeypatch):
    root = _archive_project(tmp_path)
    out = _run(monkeypatch, root, "Delete the notes-archive folder.", [_rm("rm -r notes-archive")])
    assert "notes-archive" not in out["left"] and not out["blocked"]


@pytest.mark.parametrize("command", [
    "find . -name \"${X:-*}\" -delete",
    "find ~/../.. -maxdepth 0 -name nothing -delete",
    "find . -name 'notes*' -o -name {todo,x}.md -delete",
    "find -L . -name 'notes*' -delete",
    "find . -name 'notes*' -delete -delete",
    "find . -name 'notes*' -prune -delete",
])
def test_expressions_the_preview_cannot_reproduce_are_not_resolved(tmp_path, monkeypatch, command):
    root = _archive_project(tmp_path)
    call = rs.removal("terminal_run", {"command": command}, root)
    assert call is not None and call.indirect and not call.resolved_by
    out = _run(monkeypatch, root, "Delete all the notes files.", [_rm(command)])
    assert "todo.md" in out["left"] and "couldn't be listed" in out["blocked"][0]


@pytest.mark.parametrize("command", ["git clean -ffd", "git clean -f -f -d", "git clean -fX"])
def test_git_clean_forms_the_dry_run_does_not_list_are_not_resolved(tmp_path, monkeypatch, command):
    root = _git_project(tmp_path)
    call = rs.removal("terminal_run", {"command": command}, root)
    assert call is not None and call.indirect and not call.resolved_by


PREVIEW_CASES = [
    "find . -name 'notes*' -delete",
    "find . -type f -name '*.md' -delete",
    "find . -name 'todo*' -o -name 'notes*' -delete",
    "find . \\( -name 'notes*' -o -name 'todo*' \\) -type f -delete",
    "find . -maxdepth 1 -name 'notes*' -exec rm {} \;",
    "find . -name 'notes*' -exec rm -rf {} +",
    "find . -empty -delete",
    "find . -type f -delete -name 'notes*'",
    "rm -rf notes-archive notes-draft.md",
    "rm -r notes-*",
]


@pytest.mark.parametrize("command", PREVIEW_CASES)
def test_the_preview_covers_everything_the_command_removes(tmp_path, command):
    import shutil

    preview_root = _archive_project(tmp_path / "a")
    (preview_root / "empty-dir").mkdir()
    real_root = tmp_path / "b" / "project"
    shutil.copytree(preview_root, real_root, symlinks=True)

    def tree(root):
        return {str(p.relative_to(root)) for p in root.rglob("*")}

    before = tree(preview_root)
    call = rs.removal("terminal_run", {"command": command}, preview_root)
    assert tree(preview_root) == before  # previewing changed nothing
    assert call is not None and not call.indirect
    covered = {str(p.relative_to(preview_root)) for p in rs.covered(call)}
    subprocess.run(["bash", "-c", command], cwd=real_root, capture_output=True, timeout=20)
    removed = before - tree(real_root)
    assert removed <= covered, f"removed but not previewed: {sorted(removed - covered)}"


def test_git_clean_preview_covers_what_git_clean_removes(tmp_path):
    import shutil

    for flags in ("-f", "-fd", "-fx", "-fdx"):
        base = tmp_path / flags
        root = _git_project(base)
        (root / ".gitignore").write_text("*.log\n")
        (root / "app.log").write_text("x")
        (root / "build").mkdir()
        (root / "build" / "out.bin").write_text("y")
        copy = base / "copy"
        shutil.copytree(root, copy, symlinks=True)
        tree = lambda r: {str(p.relative_to(r)) for p in r.rglob("*") if ".git" not in p.relative_to(r).parts}
        before = tree(root)
        call = rs.removal("terminal_run", {"command": f"git clean {flags}"}, root)
        assert tree(root) == before and call is not None and not call.indirect
        covered = {str(p.relative_to(root)) for p in rs.covered(call)}
        subprocess.run(["git", "clean", flags], cwd=copy, capture_output=True, check=True)
        removed = before - tree(copy)
        assert removed <= covered, (flags, sorted(removed - covered))
