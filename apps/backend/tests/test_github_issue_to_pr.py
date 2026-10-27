"""GitHub issue → fix → pull request: approval boundaries, the PR tool, and the git flow
against a real local bare remote. A fake GitHub client stands in for the network."""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.lean import agent_skills, approvals, settings
from agent.lean.approvals import dangerous_command, tool_needs_approval
from agent.lean.toolbox import looks_failed
from skills.github_comms import tools as gh

BACKEND = Path(__file__).resolve().parents[1]


class FakeRepo:
    full_name = "owner/fixture"
    default_branch = "main"

    def __init__(self):
        self.pulls: list[dict] = []
        self.merged = False

    def create_pull(self, **kwargs):
        self.pulls.append(kwargs)
        return SimpleNamespace(number=7, title=kwargs["title"], html_url="https://github.com/owner/fixture/pull/7")

    def merge(self, *a, **k):  # must never be reached
        self.merged = True


@pytest.fixture
def repo(monkeypatch):
    fake = FakeRepo()
    monkeypatch.setattr(gh, "_resolve_repo", lambda name=None: fake)
    return fake


def _create(**kwargs):
    return gh.github_create_pull_request.invoke(kwargs)


@pytest.mark.parametrize("mode", ["smart", "always", "never", "autonomous"])
def test_opening_a_pull_request_asks_in_every_approval_mode_and_cannot_be_granted(monkeypatch, mode):
    monkeypatch.setattr(settings, "approval_mode", lambda: mode)
    needs, reason = tool_needs_approval(None, "github_create_pull_request", {"title": "x", "head": "fix"})
    assert needs and "pull request" in reason
    assert approvals.scope_for("github_create_pull_request", {}, reason) is None  # no saved "always allow"


def test_registered_github_calendar_and_notion_writes_ask_in_the_default_mode(monkeypatch):
    """The approval list named github_comment / calendar_create, which match no registered tool."""
    monkeypatch.setattr(settings, "approval_mode", lambda: "smart")
    for name in ("github_comment_issue", "github_create_issue", "calendar_create_event",
                 "calendar_delete_event", "notion_append_block", "notion_create_page"):
        assert tool_needs_approval(SimpleNamespace(is_action=True, category="x"), name, {})[0], name


@pytest.mark.parametrize("command", ["git push -u origin fix/issue-3", "gh pr create --fill", "gh pr merge 7",
                                     "gh issue comment 3 -b done", "gh api -X POST repos/o/r/pulls"])
def test_publishing_through_the_terminal_asks_too(command):
    assert dangerous_command(command)


@pytest.mark.parametrize("command", ["gh pr view 7", "gh pr list", "gh pr checks 7", "git status --porcelain"])
def test_reading_through_the_terminal_does_not_ask(command):
    assert not dangerous_command(command)


def test_the_tool_opens_a_draft_against_the_default_branch_and_never_merges(repo):
    out = _create(title="Fix totals", head="fix/issue-3", body="Fixes #3")
    assert repo.pulls == [{"title": "Fix totals", "body": "Fixes #3", "head": "fix/issue-3", "base": "main", "draft": True}]
    assert "pull/7" in out and "Not merged" in out and not looks_failed(out) and not repo.merged


def test_the_tool_refuses_a_pull_request_from_the_default_branch(repo):
    out = _create(title="Fix", head="main")
    assert looks_failed(out) and repo.pulls == []


def test_a_github_error_is_recorded_as_a_failure(monkeypatch):
    def broken(name=None):
        raise RuntimeError("GITHUB_TOKEN not set in .env")
    monkeypatch.setattr(gh, "_resolve_repo", broken)
    assert looks_failed(_create(title="Fix", head="fix/x"))


def test_the_issue_to_pr_skill_ships_valid_and_names_the_boundaries():
    text = (BACKEND / "resources" / "agent-skills" / "github-issue-to-pr" / "SKILL.md").read_text(encoding="utf-8")
    meta, body, problem = agent_skills.parse_skill_md(text)
    assert not problem and meta["name"] == "github-issue-to-pr"
    for rule in ("github_create_pull_request", "## Never", "Merge, close, approve or auto-merge", "force-push", "git add -A"):
        assert rule.lower() in body.lower(), rule


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def test_issue_to_pr_flow_against_a_local_bare_remote(tmp_path, repo):
    """The skill's steps end to end: branch, fix, test, commit, push, PR; the default branch is untouched."""
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", "-b", "main", str(remote))
    work = tmp_path / "work"
    _git(tmp_path, "clone", str(remote), str(work))
    _git(work, "config", "user.name", "Fixture")
    _git(work, "config", "user.email", "fixture@example.invalid")
    (work / "calc.py").write_text("def total(items):\n    return sum(items) + 1  # issue 3: off by one\n", encoding="utf-8")
    (work / "test_calc.py").write_text("from calc import total\n\ndef test_total():\n    assert total([1, 2]) == 3\n", encoding="utf-8")
    _git(work, "add", "calc.py", "test_calc.py")
    _git(work, "commit", "-m", "Initial")
    _git(work, "push", "-u", "origin", "main")
    main_before = _git(work, "rev-parse", "main")

    assert _git(work, "status", "--porcelain") == ""  # step 2: clean checkout
    _git(work, "switch", "-c", "fix/issue-3-total")  # step 3
    (work / "calc.py").write_text("def total(items):\n    return sum(items)\n", encoding="utf-8")  # step 4
    import sys
    tests = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_calc.py"],
                           cwd=work, capture_output=True, text=True)  # step 5
    assert tests.returncode == 0, tests.stdout
    _git(work, "add", "calc.py")  # step 6
    _git(work, "commit", "-m", "Fix off-by-one in total (fixes #3)")
    _git(work, "push", "-u", "origin", "fix/issue-3-total")  # step 7 (approved by the owner in the app)
    out = _create(title="Fix off-by-one in total", head="fix/issue-3-total",
                  body="Fixes the total.\n\nTested: pytest test_calc.py (1 passed).\n\nFixes #3")  # step 8

    assert _git(remote, "rev-parse", "main") == main_before  # the default branch never moved
    assert _git(remote, "log", "-1", "--format=%s", "fix/issue-3-total") == "Fix off-by-one in total (fixes #3)"
    assert repo.pulls[0]["head"] == "fix/issue-3-total" and repo.pulls[0]["base"] == "main" and repo.pulls[0]["draft"]
    assert "Fixes #3" in repo.pulls[0]["body"] and not repo.merged and "pull/7" in out
