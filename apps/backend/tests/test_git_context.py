"""Git awareness: the repo summary, when the git rules load, failure hints and approvals."""

import shutil
import subprocess

import pytest

from agent.lean import git_context
from agent.lean.approvals import dangerous_command
from agent.lean.personas import AgentPersona
from agent.lean.prompt import build_system_prompt
from agent.lean.terminal import approval_for


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_summary_names_the_branch_and_changes(tmp_path):
    assert git_context.summary(str(tmp_path / "missing")) == ""  # no such folder
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "--allow-empty", "-m", "start")
    assert git_context.summary(str(tmp_path)) == "git: branch main · clean"
    (tmp_path / "a.txt").write_text("hi", encoding="utf-8")
    assert git_context.summary(str(tmp_path)) == "git: branch main · 1 changed file"


def test_rules_load_only_for_repos_and_git_requests():
    assert git_context.wants_rules("anything", "git: branch main · clean")
    for text in ("open a PR for this", "commit and push it", "fix the merge conflicts", "clone this repo"):
        assert git_context.wants_rules(text, ""), text
    for text in ("pull up the weather", "release notes for my book", "what's 2+2"):
        assert not git_context.wants_rules(text, ""), text
    with_rules = build_system_prompt(persona=AgentPersona(id="echo", name="Echo"), soul_text="", git_rules=True)
    without = build_system_prompt(persona=AgentPersona(id="echo", name="Echo"), soul_text="")
    assert "## Git and GitHub" in with_rules and "## Git and GitHub" not in without


def test_failure_hints_for_common_git_stalls():
    assert "pull --ff-only" in git_context.failure_hint("! [rejected] main -> main (non-fast-forward)")
    assert "gh auth login" in git_context.failure_hint("fatal: could not read Username for 'https://github.com'")
    assert "conflict" in git_context.failure_hint("CONFLICT (content): Automatic merge failed; fix conflicts")
    assert git_context.failure_hint("all good") == ""


def test_history_rewriting_and_github_writes_ask_first():
    for command in ("git rebase main", "git restore app.py", "git stash drop", "gh pr merge 4 --merge",
                    "gh release create v1", "gh repo delete me/x", "git push origin main"):
        assert dangerous_command(command), command
    for command in ("git status -sb", "git log --oneline -5", "gh pr view 4", "gh pr checks"):
        assert not dangerous_command(command), command


def test_sandbox_asks_before_rewriting_history(monkeypatch):
    from agent.lean import terminal

    monkeypatch.setattr(terminal, "resolved_mode", lambda: "docker")
    assert approval_for({"command": "git rebase -i main"})[0]
    assert approval_for({"command": "gh pr merge 3"})[0]
    assert not approval_for({"command": "git diff --staged"})[0]
