"""auto-test-fix (scripts/auto_test_fix.py): the loop's own decisions, which no engineering agent can talk past."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location("auto_test_fix", Path(__file__).resolve().parents[1] / "scripts" / "auto_test_fix.py")
atf = importlib.util.module_from_spec(_SPEC)
sys.modules["auto_test_fix"] = atf
_SPEC.loader.exec_module(atf)  # type: ignore[union-attr]


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
