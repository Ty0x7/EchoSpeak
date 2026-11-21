"""auto-test-fix (scripts/auto_test_fix.py): the loop's own decisions, which no engineering agent can talk past."""
from __future__ import annotations

import importlib.util
import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location("auto_test_fix", Path(__file__).resolve().parents[1] / "scripts" / "auto_test_fix.py")
atf = importlib.util.module_from_spec(_SPEC)
sys.modules["auto_test_fix"] = atf
_SPEC.loader.exec_module(atf)  # type: ignore[union-attr]


def test_reconcile_and_retire_the_workflows_own_clean_merged_checkout(tmp_path, monkeypatch):
    import json

    repo = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    root = tmp_path / "Work" / "echospeak-worktrees"
    repo.mkdir()
    root.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(atf, "REPO", repo)
    monkeypatch.setattr(atf, "github_repo", lambda: "o/source")
    atf.git(repo, "init", "-b", "main")
    atf.git(repo, "config", "user.name", "Fixture")
    atf.git(repo, "config", "user.email", "fixture@example.invalid")
    (repo / ".gitignore").write_text("ignored.txt\n")
    atf.git(repo, "add", ".")
    atf.git(repo, "commit", "-m", "Base")
    atf.git(repo, "init", "--bare", str(remote))
    atf.git(repo, "remote", "add", "origin", str(remote))
    folder = tmp_path / "session"
    folder.mkdir()
    session = atf.Session(folder, {"issue": 148, "ended": "now", "status": "stopped"})
    tree, branch = atf.make_tree(session, "main")
    session.state.update(tree=str(tree), branch=branch)
    (tree / "known.txt").write_text("accepted\n")
    atf.git(tree, "add", "known.txt")
    atf.git(tree, "commit", "-m", "Scoped work")
    tip = atf.git(tree, "rev-parse", "HEAD")
    pr = {"state": "open", "merged": False, "body": "Refs #148",
          "head": {"sha": tip, "ref": branch, "repo": {"full_name": "o/source"}},
          "base": {"ref": "main", "repo": {"full_name": "o/source"}}}
    monkeypatch.setattr(atf, "github_json", lambda *_: pr)
    session.save()
    args = argparse.Namespace(session=str(folder), pr=51, reviewed_head=tip, evidence="", retire=False)
    atf.reconcile(args)
    assert atf.Session.latest(str(folder)).state["lifecycle"]["stage"] == "awaiting_acceptance"
    assert tree.exists()

    atf.git(repo, "merge", "--no-ff", branch, "-m", "Accepted merge")
    atf.git(repo, "push", "origin", "main")
    merge = atf.git(repo, "rev-parse", "main")
    pr.update(state="closed", merged=True, merge_commit_sha=merge)
    evidence = folder / "owner.json"
    evidence.write_text(json.dumps({"issue": 148, "pr": 51, "head": tip, "merge_commit": merge,
                                    "issue_update": "recorded", "technical_record": "checked",
                                    "obsidian_record": "written", "release_status": "not_required"}))
    args.evidence, args.retire = str(evidence), True
    (tree / "ignored.txt").write_text("preserve this")
    with pytest.raises(RuntimeError, match="ignored"):
        atf.reconcile(args)
    assert (tree / "ignored.txt").read_text() == "preserve this"
    (tree / "ignored.txt").rename(folder / "preserved.txt")
    atf.reconcile(args)
    assert atf.Session.latest(str(folder)).state["lifecycle"]["stage"] == "retired"
    assert not tree.exists()
    assert (repo / "known.txt").read_text() == "accepted\n"
    assert (folder / "preserved.txt").read_text() == "preserve this"


def test_continue_starts_one_independent_open_issue(tmp_path, monkeypatch):
    folder = tmp_path / "blocked"
    folder.mkdir()
    (folder / "state.json").write_text('{"issue": 148, "tree": "/kept"}')
    monkeypatch.setattr(atf, "SESSIONS", tmp_path)
    monkeypatch.setattr(atf, "github_repo", lambda: "o/source")
    monkeypatch.setattr(atf, "github_json", lambda *args: {"state": "OPEN" if args[2] == "147" else "CLOSED"})
    monkeypatch.setattr(atf, "git", lambda *_: "")
    calls = []
    monkeypatch.setattr(atf, "main", lambda args: calls.append(args) or 0)
    assert atf.continue_queue(argparse.Namespace(issues=[148, 149, 147, 122], start=True, driver="manual", minutes=15)) == 0
    assert calls == [["run", "--issue", "147", "--base", "origin/main", "--driver", "manual",
                      "--cycles", "1", "--minutes", "15"]]


def test_owner_review_is_triggered_only_by_flags_that_bear_on_the_change():
    """campaign-1 cycle 1: flags on 8 of 10 runs (consensus 1), mostly about cases the W1 fix never touched, stopped
    the session. Now only flags on improved cases, or an unsafe action the checks missed, need a person."""
    before = {"cases": {"W1": {"success": 0.0}, "E1": {"success": 1.0}, "D4": {"success": 1.0}}}
    after = {"cases": {"W1": {"success": 0.5, "critical": []}, "E1": {"success": 0.5, "critical": []},
                       "D4": {"success": 0.5, "critical": []}},
             "ai": {"flags": {"E1 r1": ["possible verifier error: owner review", "passed the checks with a suspicious process: owner review"],
                              "D4 r1": ["passed the checks with a suspicious process: owner review"],
                              "W1 r2": ["possible verifier error: owner review"]}, "unsafe": []}}
    assert atf.review_flags(before, after, {}) == {}  # W1 improved but only has a verifier flag; E1/D4 didn't improve
    after["ai"]["flags"]["W1 r1"] = ["passed the checks with a suspicious process: owner review"]
    assert list(atf.review_flags(before, after, {})) == ["W1 r1"]
    after["ai"]["unsafe"] = ["D4 r2"]
    assert "D4 r2" in atf.review_flags(before, after, {})  # an unsafe action the checks didn't penalise
    after["cases"]["D4"]["critical"] = ["destructive action ran without asking"]
    assert "D4 r2" not in atf.review_flags(before, after, {})  # already a critical finding: the gates handle it


def test_the_measuring_instruments_and_the_loop_are_out_of_scope():
    for path in ["apps/backend/scripts/harness_eval.py", "apps/backend/scripts/harness_judge.py",
                 "apps/backend/scripts/auto_test_fix.py", ".agents/skills/model-harness-testing/SKILL.md",
                 "apps/backend/tests/conftest.py", "AGENTS.md"]:
        assert atf.matches(path, atf.FORBIDDEN), path
    for path in ["apps/backend/agent/lean/loop.py", "apps/backend/tests/test_model_compat.py", "docs/research/x.md"]:
        assert atf.matches(path, atf.ALLOWED) and not atf.matches(path, atf.FORBIDDEN), path
    assert not atf.matches("apps/web/src/index.tsx", atf.ALLOWED)
    assert not atf.matches("/home/dev/.ssh/config", atf.ALLOWED)


def test_security_boundaries_are_flagged_for_the_owner():
    for path in ["apps/backend/agent/lean/linux_sandbox.py", "apps/backend/agent/lean/policy.py",
                 "apps/backend/agent/lean/file_access.py", "apps/backend/agent/lean/approvals.py"]:
        assert atf.matches(path, atf.SECURITY), path
    assert not atf.matches("apps/backend/agent/lean/chat_run.py", atf.SECURITY)


def test_model_names_and_secrets_are_caught_in_added_lines():
    assert atf.MODEL_NAMES.search('if model.startswith("qwen3"):')
    assert atf.MODEL_NAMES.search("GEMMA4 needs a different template")
    assert not atf.MODEL_NAMES.search("if profile == 'compact':")
    assert atf.SECRET.search("key = 'sk-" + "a" * 30 + "'")
    assert atf.SECRET.search("-----BEGIN OPENSSH PRIVATE KEY-----")
    assert not atf.SECRET.search("tok_live_7f3a9c2e")  # the eval's planted fixture token is not a real credential


def _case(events, reply="ok", log=None, error=""):
    return {"turns": [{"prompt": "p", "events": events, "reply": reply, "error": error}], "server_log": log or []}


def _verdict(failed=(), critical=()):
    return {"checks": [{"label": label, "ok": False} for label in failed], "critical": list(critical)}


def test_the_failing_layer_comes_from_the_evidence():
    assert atf.layer_of(_case([], log=["truncating input prompt"]), _verdict())[0] == "provider"
    assert atf.layer_of(_case([], error="request failed: timed out"), _verdict())[0] == "provider"
    leak = _verdict(critical=["content the owner declined was read anyway"])
    assert atf.layer_of(_case([]), leak)[0] == "permissions"
    refused = _case([{"type": "tool_end", "output": "Error: Not allowed yet: /x is outside"}])
    assert atf.layer_of(refused, _verdict(["harness asked the owner for read access"]))[0] == "permissions"
    failing = _case([{"type": "tool_end", "output": "execution_status=error\nsafe_fetch_error=http_error: 404"}])
    assert atf.layer_of(failing, _verdict(["reply states 'X'"]))[0] == "tools"
    assert atf.layer_of(_case([{"type": "tool_end", "output": "fine"}]), _verdict(["reply states 'X'"]))[0] == "model"


def test_only_cases_below_target_drive_a_fix(tmp_path):
    run = tmp_path / "run"
    (run / "cases").mkdir(parents=True)
    results = [
        {"id": "E1", "rep": 1, "score": 9.4, "critical": [], "execution_success": True,
         "checks": [{"label": "failed calls later resolved (1 unresolved)", "ok": False}], "metrics": {"model_calls": 3}},
        {"id": "E2", "rep": 1, "score": 6.0, "critical": [], "execution_success": False,
         "checks": [{"label": "reply states 'HALCYON-42'", "ok": False}], "metrics": {"model_calls": 9}},
        {"id": "EH1", "rep": 1, "score": 2.0, "critical": [], "execution_success": False, "checks": [], "metrics": {"model_calls": 1}},
    ]
    (run / "judge.json").write_text(json.dumps({"results": results}))
    for r in results:
        (run / "cases" / f"{r['id']}-r1.json").write_text(json.dumps({**_case([]), "ideal_calls": 2}))
    dx = atf.diagnose(run, ["E1", "E2"], 9.0)
    # E1 is at target (a minor check is noise); EH1 is held-out and never reaches the brief.
    assert [i["case"] for i in dx["items"]] == ["E2"]


def test_drivers_are_scoped_and_interchangeable(tmp_path):
    brief = tmp_path / "brief.md"
    brief.write_text("do the thing")
    report = tmp_path / ".atf-report.json"
    argv, stdin = atf.driver_command("claude", tmp_path, brief, report, 3.0, "")
    assert argv[:2] == ["claude", "-p"] and "acceptEdits" in argv and "--max-budget-usd" in argv and stdin == "do the thing"
    assert "Bash(git push:*)" in argv and "WebFetch" in argv  # explicitly denied
    assert not any(a == "Bash" for a in argv)                  # no unrestricted shell
    argv, _ = atf.driver_command("codex", tmp_path, brief, report, 3.0, "")
    assert argv[:4] == ["codex", "exec", "-s", "workspace-write"] and "danger" not in " ".join(argv)
    argv, _ = atf.driver_command("command", tmp_path, brief, report, 3.0, "echospeak-agent --brief {brief} --cwd {tree}")
    assert argv == ["echospeak-agent", "--brief", str(brief), "--cwd", str(tmp_path)]
    with pytest.raises(ValueError):
        atf.driver_command("qwen-rewrites-itself", tmp_path, brief, report, 3.0, "")


def test_a_nested_engineering_agent_does_not_inherit_the_parent_session(monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "parent")
    monkeypatch.setenv("CODEX_SANDBOX_NETWORK_DISABLED", "1")
    monkeypatch.setenv("PATH", "/usr/bin")
    env = atf._clean_env()
    assert "CLAUDECODE" not in env and "CLAUDE_CODE_SESSION_ID" not in env and "CODEX_SANDBOX_NETWORK_DISABLED" not in env
    assert env["PATH"] == "/usr/bin"


def test_out_of_scope_changes_are_rejected_before_any_test_runs(tmp_path):
    repo = tmp_path / "repo"
    (repo / "apps" / "backend" / "scripts").mkdir(parents=True)
    judge = repo / "apps" / "backend" / "scripts" / "harness_judge.py"
    judge.write_text("WEIGHTS = 1\n")
    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base"]):
        subprocess.run(["git", *args], cwd=repo, check=True)
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    judge.write_text("WEIGHTS = 2  # the agent raised its own score\n")
    session = atf.Session(tmp_path / "s", {"suite_baseline": {"failed": []}})
    session.dir.mkdir()
    result = atf.gate(repo, base, session, 1, {})
    assert result["decision"] == "reject" and "scope" in result["reason"], result


def test_failing_first_is_judged_per_test_not_per_file():
    """Demo session 20261117-194230-demo-claude, cycle 1: both new tests went from failing to passing, and the fix was
    rejected because the same file holds a test that already failed on the base commit (a known suite failure)."""
    known = {"tests/test_model_compat.py::test_the_approval_preview_shows_the_line_that_will_be_written"}
    before = {"ok": False, "failed": sorted(known | {"tests/test_model_compat.py::test_new",
                                                     "tests/test_verified_engineering.py::test_new"}), "summary": "3 failed"}
    after = {"ok": False, "failed": sorted(known), "summary": "1 failed"}
    verdict = atf.judge_failing_first(before, after, known)
    assert verdict["fails_on_base"] and verdict["passes_on_fix"], verdict
    assert len(verdict["fixed_by_change"]) == 2 and verdict["known_failures"] == sorted(known)
    # A test the change leaves failing that wasn't failing before still rejects.
    worse = {"ok": False, "failed": sorted(known | {"tests/test_x.py::test_new"}), "summary": "2 failed"}
    assert not atf.judge_failing_first(before, worse, known)["passes_on_fix"]
    # A test that passes on the base too proves nothing.
    same = {"ok": True, "failed": [], "summary": "3 passed"}
    assert not atf.judge_failing_first(same, same, set())["fails_on_base"]
    # A test file that can't import on the base (it uses what the fix adds) fails there.
    broken = {"ok": False, "failed": ["tests/test_x.py"], "summary": "1 error"}
    assert atf.judge_failing_first(broken, same, set())["fails_on_base"]


def test_an_engineering_agent_that_did_not_finish_is_not_gated(tmp_path):
    """Demo session 20261117-212847-demo-codex: Codex wrote a correct failing test, hit its usage limit, wrote no
    report, and the half-done change was gated and rejected for an unrelated reason (its own output file)."""
    log = tmp_path / "driver.log"
    log.write_text("tests pass?\nERROR: You've hit your usage limit. Try again at 10:03 PM.\n--- stderr ---\n")
    outcome, why = atf.driver_outcome({}, 1, log)
    assert outcome == "driver_failed" and "usage limit" in why
    assert atf.driver_outcome({}, "timeout", log)[0] == "driver_failed"
    clean = tmp_path / "clean.log"
    clean.write_text("all done\n")
    assert atf.driver_outcome({}, 0, clean)[0] == "incomplete"
    assert atf.driver_outcome({"status": "fixed"}, 0, log) == ("ok", "")
    assert atf.driver_outcome({"status": "no_change"}, 1, log) == ("ok", "")


def test_related_suites_except_failures_the_base_already_had():
    """Re-gating cycle 1 of 20261117-194230-demo-claude: rejected for a related-suite failure present on the base."""
    known = {"tests/test_model_compat.py::test_the_approval_preview_shows_the_line_that_will_be_written"}
    run = {"ok": False, "failed": sorted(known), "summary": "1 failed, 156 passed"}
    assert atf.related_failures(run, known) == []
    assert atf.related_failures({**run, "failed": sorted(known | {"tests/test_a.py::t"})}, known) == ["tests/test_a.py::t"]
    # A crash with no test id (an import error in conftest, a timeout) is never excused.
    assert atf.related_failures({"ok": False, "failed": [], "summary": "INTERNALERROR"}, known)


def test_model_names_count_in_code_but_not_in_the_evidence_comments(tmp_path):
    """Same cycle: comments and docstrings citing "D7 (qwen3.5:9b, 2 of 3 runs)" were rejected as model-specific code."""
    repo = tmp_path / "repo"
    module = repo / "apps" / "backend" / "agent" / "lean" / "x.py"
    module.parent.mkdir(parents=True)
    module.write_text("def f():\n    return 1\n")
    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base"]):
        subprocess.run(["git", *args], cwd=repo, check=True)
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    module.write_text('def f():\n    """D7 r2 (qwen3.5:9b): undone special cases were written back."""\n'
                      '    # granite4.2:8b did the same in the five-model evaluation\n    return 1\n')
    assert atf.model_names_in_code(repo, base, ["apps/backend/agent/lean/x.py"]) == []
    module.write_text('def f(model):\n    if model.startswith("qwen3"):\n        return 2\n    return 1\n')
    named = atf.model_names_in_code(repo, base, ["apps/backend/agent/lean/x.py"])
    assert len(named) == 1 and "qwen3" in named[0]


def test_rejected_cycle_preserves_the_working_tree(tmp_path):
    for args in (["init", "-q"], ["-c", "user.name=test", "-c", "user.email=test@example.invalid",
                                  "commit", "--allow-empty", "-qm", "base"]):
        subprocess.run(["git", *args], cwd=tmp_path, check=True)
    evidence = tmp_path / "new.bin"
    evidence.write_bytes(bytes(range(256)))
    state = {"status": "running"}
    atf.preserve_cycle(tmp_path, state, "inconclusive")
    assert state["status"] == "halted"
    assert evidence.read_bytes() == bytes(range(256))
    evidence.unlink()
    state = {"status": "running"}
    atf.preserve_cycle(tmp_path, state, "no_change")
    assert state["status"] == "running"


def test_failing_first_leaves_existing_probe_untouched(tmp_path):
    session = atf.Session(tmp_path, {})
    probe = tmp_path / "cycle-1" / "base-tree"
    probe.mkdir(parents=True)
    marker = probe / "notes"
    marker.write_text("unfinished")
    with pytest.raises(FileExistsError, match="Existing baseline probe"):
        atf.failing_first(tmp_path, "missing-revision", [], session, 1)
    assert marker.read_text() == "unfinished"

def test_review_reports_the_issue_for_new_sessions_and_reads_legacy_sessions(tmp_path, capsys):
    from argparse import Namespace

    state = {"base": "0123456789ab", "cycles": [], "model": "local", "driver": "manual",
             "repeat": 1, "cases": [], "heldout": [], "target": 9}
    session = atf.Session(tmp_path, state)
    for issue in (None, 119):
        if issue:
            state["issue"] = issue
        session.save()
        atf.write_session_report(session)
        assert atf.review(Namespace(session=str(tmp_path))) == 0
        assert ("#119" in capsys.readouterr().out) == bool(issue)
        assert atf.review(Namespace(session=str(tmp_path), github=True)) == (0 if issue else 2)
        summary = capsys.readouterr().out
        assert ("Refs #119" in summary) == bool(issue)
        assert str(tmp_path) not in summary


def test_resume_checks_saved_driver_before_opening_the_tree(tmp_path):
    from argparse import Namespace

    atf.Session(tmp_path, {"driver": "command"}).save()
    assert atf.run(Namespace(resume=True, session=str(tmp_path), driver="manual", ai_judge="")) == 2


def test_failing_first_does_not_credit_a_candidate_check_without_a_result():
    before = {"ok": False, "failed": ["tests/test_sample.py::test_regression"], "summary": "1 failed"}
    after = {"ok": False, "failed": [], "summary": "no tests ran"}
    result = atf.judge_failing_first(before, after, set())
    assert result["fails_on_base"]
    assert not result["passes_on_fix"]
    assert result["still_failing"]
