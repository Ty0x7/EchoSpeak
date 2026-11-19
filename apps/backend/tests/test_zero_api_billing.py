"""Zero API billing (owner policy, 18 November 2026): no autonomous evaluation or engineering path can make a paid API
call. Claude Code and Codex run only on the owner's subscriptions, inference is local, and when a subscription can't
carry a call the workflow stops instead of falling back. "A $0.01 API charge is still unacceptable."
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


ai = _load("harness_ai_judge")
atf = _load("auto_test_fix")
heval = _load("harness_eval")

PAID_ENV = {"ANTHROPIC_API_KEY": "sk-ant-x", "ANTHROPIC_AUTH_TOKEN": "x", "ANTHROPIC_BASE_URL": "https://proxy",
            "OPENAI_API_KEY": "sk-x", "CODEX_API_KEY": "x", "CLAUDE_CODE_USE_BEDROCK": "1", "CLAUDE_CODE_USE_VERTEX": "1",
            "GEMINI_API_KEY": "x", "XAI_API_KEY": "x", "BRAVE_SEARCH_API_KEY": "x", "TAVILY_API_KEY": "x",
            "ELEVENLABS_API_KEY": "x", "RUNWAY_API_KEY": "x", "MINIMAX_API_KEY": "x", "GITHUB_TOKEN": "x"}


def test_no_paid_credentials_reach_the_engineering_agent_or_the_judge(monkeypatch):
    for k, v in PAID_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("ATF_ALLOW_API_BILLING", "1")  # a former opt-in: it must change nothing now
    for env in (ai._clean_env(), atf._clean_env()):
        assert not set(ai.BILLING_VARS) & set(env), sorted(set(ai.BILLING_VARS) & set(env))
    assert not hasattr(ai, "api_billing_allowed")


def test_the_system_under_test_gets_no_paid_provider_key_from_the_shell_or_from_env_flags():
    env = heval.local_only_env({**PAID_ENV, "PATH": "/usr/bin", "HOME": "/home/x", "API_AUTH_KEY": "local",
                                "LOCAL_MODEL_URL": "http://127.0.0.1:1", "LEARNING_ENABLED": "false"})
    leaked = [k for k in PAID_ENV if k in env]
    assert not leaked, leaked
    assert env["API_AUTH_KEY"] == "local" and env["PATH"] == "/usr/bin" and env["LOCAL_MODEL_URL"]


def test_the_system_under_test_never_uses_the_owners_keyring():
    """After a restart the locked desktop keyring failed MCP approval (HTTP 500): the eval backend was using the
    owner's real keyring. Its credential key belongs in the run's disposable data folder."""
    source = (SCRIPTS / "harness_eval.py").read_text(encoding="utf-8")
    assert '"ECHOSPEAK_CREDENTIAL_KEYFILE": "1"' in source


def test_the_model_server_must_be_on_this_machine():
    for ok in ("http://127.0.0.1:11434", "http://localhost:11434"):
        heval.assert_local(ok)
    for bad in ("https://api.openai.com/v1", "https://api.anthropic.com", "http://10.0.0.5:11434", "https://ollama.com"):
        with pytest.raises(SystemExit):
            heval.assert_local(bad)


def test_a_non_subscription_login_stops_the_workflow(monkeypatch):
    def fake_run(argv, **_):
        if argv[:3] == ["claude", "auth", "status"]:
            return SimpleNamespace(stdout=json.dumps({"loggedIn": True, "authMethod": "api_key"}), stderr="", returncode=0)
        return SimpleNamespace(stdout="Logged in using an API key", stderr="", returncode=0)
    monkeypatch.setattr(ai.subprocess, "run", fake_run)
    assert "subscription" in ai.subscription_problem("claude")
    assert "ChatGPT" in ai.subscription_problem("codex")
    assert atf.billing_problems("claude", "claude:haiku")


def test_an_exhausted_subscription_or_paid_credit_stops_instead_of_falling_back(monkeypatch):
    replies = [
        {"is_error": True, "result": "", "api_error_status": 429},
        {"result": "", "usage": {}},  # nothing came back
        {"result": "{}", "usage": {"fallback_credit": 0.02}},  # extra paid credit: never acceptable
    ]
    for reply in replies:
        monkeypatch.setattr(ai.subprocess, "run", lambda *a, _r=reply, **k: SimpleNamespace(
            stdout=json.dumps(_r), stderr="You've hit your usage limit", returncode=1))
        with pytest.raises(ai.SubscriptionUnavailable):
            ai.ask("claude:haiku", "prompt")
    monkeypatch.setattr(ai.subprocess, "run", lambda *a, **k: SimpleNamespace(
        stdout="ERROR: You've hit your usage limit. Purchase more credits", stderr="", returncode=1))
    with pytest.raises(ai.SubscriptionUnavailable):
        ai.ask("codex", "prompt")


def test_unverifiable_agents_and_judges_are_disabled():
    with pytest.raises(ValueError, match="zero API billing"):
        ai.ask("command:curl https://api.openai.com/v1/chat/completions", "prompt")
    args = SimpleNamespace(driver="command", ai_judge="", resume=False)
    assert atf.run(args) == 2


def test_an_engineering_agent_on_paid_credit_is_a_failed_cycle(tmp_path):
    log = tmp_path / "driver.log"
    log.write_text(json.dumps({"result": "done", "usage": {"fallback_credit": 1.5}}) + "\n--- stderr ---\n")
    outcome, why = atf.driver_outcome({"status": "fixed"}, 0, log)
    assert outcome == "driver_failed" and "fallback credit" in why


def test_the_claude_driver_and_judge_use_only_the_subscription_cli(tmp_path):
    brief = tmp_path / "brief.md"
    brief.write_text("x")
    argv, _ = atf.driver_command("claude", tmp_path, brief, tmp_path / ".atf-report.json", 3.0, "")
    joined = " ".join(argv)
    assert argv[0] == "claude" and "--settings" not in argv and "api" not in joined.lower().replace("apps", "")
    assert "--max-budget-usd" in argv  # an estimate cap on subscription usage, never a billing switch


def test_evaluation_scripts_contain_no_direct_paid_api_client():
    """No SDK client or HTTPS endpoint of a paid provider in the autonomous tooling: inference is local, and the
    engineering agent and the judge go through the subscription CLIs."""
    pattern = re.compile(r"(?i)import anthropic|from anthropic|import openai|from openai|api\.anthropic\.com|"
                         r"api\.openai\.com|generativelanguage\.googleapis|api\.x\.ai|openrouter\.ai")
    names = ["auto_test_fix.py", "harness_ai_judge.py", "harness_eval.py", "harness_judge.py", "harness_campaign.py",
             "model_recorder.py", "eval_fixtures/accounts_mcp.py"]
    hits = {n: pattern.findall((SCRIPTS / n).read_text(encoding="utf-8")) for n in names}
    assert not any(hits.values()), hits
