"""Claude Code with the owner's own plan, and finding the owner's installed coding agents.

No real Claude Code or Codex runs here: a fixture script plays `claude -p` and calls the real
PermissionRequest hook program, so the approve/deny round trip is tested end to end.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import subprocess
import sys

import pytest

from agent import code_accounts, code_executables
from agent.code_runtimes import ClaudeCli
from config import config

FIXTURE = Path(__file__).parent / "fixtures" / "fake_claude_cli.py"


class Stub:
    """The parts of an Echo Code session an adapter uses."""
    def __init__(self, root, decide=True):
        self.root, self.row, self.events, self.asked = Path(root), {}, [], []
        self.account, self.models, self.task, self.result = {}, [], True, "failed"
        self.turn_finished, self.decide = asyncio.Event(), decide

    def save(self):
        pass

    def record_model(self, model):
        self.row["model"] = model

    def emit(self, kind, data):
        self.events.append((kind, data))

    async def approval(self, kind, details):
        self.asked.append((kind, details))
        return self.decide

    def text(self):
        return "".join(d["text"] for k, d in self.events if k == "claude/text")


@pytest.fixture
def signed_in(monkeypatch):
    monkeypatch.setattr(code_accounts, "claude_status", lambda: {"installed": True, "signed_in": True, "method": "claude.ai", "plan": "pro"})
    monkeypatch.setattr(code_executables, "executable", lambda runtime: sys.executable)
    real_argv = ClaudeCli.argv
    monkeypatch.setattr(ClaudeCli, "argv", lambda self, model: [sys.executable, str(FIXTURE), *real_argv(self, model)[1:]])


def run(coroutine):
    return asyncio.run(coroutine)


def test_a_turn_uses_the_owners_sign_in_and_resumes_claude_codes_own_session(tmp_path, signed_in, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-never-reach-claude-code")
    session = Stub(tmp_path)

    async def scenario():
        adapter = ClaudeCli(session)
        await adapter.connect()
        assert session.account == {"connected": True, "type": "Your Claude Pro plan"}
        argv = adapter.argv("sonnet")
        assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"
        assert argv[argv.index("--setting-sources") + 1] == "user" and "--strict-mcp-config" in argv
        hook = json.loads(argv[argv.index("--settings") + 1])["hooks"]["PermissionRequest"][0]["hooks"][0]
        assert hook["command"] == sys.executable and "args" in hook  # exec form: no shell quoting
        await adapter.prompt("hello", "sonnet")
        first = session.row["native_id"]
        assert "--resume" in adapter.argv("") and adapter.argv("")[-1] == first
        await adapter.prompt("again", "")
        await adapter.rpc.close()
        return first

    first = run(scenario())
    assert session.result == "completed" and session.row["model"] == "claude-fake"
    assert session.row["native_id"] == first == "fake-session-1"
    assert "hello acceptEdits|user own-sign-in" in session.text()  # no API key: Claude Code uses its own login


@pytest.mark.parametrize("decide, expected", [(True, "ran it"), (False, "not allowed")])
def test_claude_codes_permission_prompt_becomes_an_echo_code_approval(tmp_path, signed_in, decide, expected):
    session = Stub(tmp_path, decide=decide)

    async def scenario():
        adapter = ClaudeCli(session)
        await adapter.connect()
        await adapter.prompt("this needs-approval", "")
        await adapter.rpc.close()

    run(scenario())
    assert session.asked == [("claude/Bash", {"tool": "Bash", "input": {"command": "npm install left-pad"}})]
    assert expected in session.text()
    assert any(k == "permission_denied" for k, _ in session.events) is (not decide)


def test_the_hook_denies_when_echo_cant_be_reached_or_the_token_is_wrong(tmp_path, monkeypatch):
    event = json.dumps({"hook_event_name": "PermissionRequest", "tool_name": "Bash", "tool_input": {"command": "rm -rf x"}})
    hook = [sys.executable, "-P", "-m", "agent.code_claude_hook"]
    backend = str(Path(__file__).resolve().parents[1])

    def decide(env_extra):
        env = {**__import__("os").environ, "PYTHONPATH": backend, **env_extra}
        done = subprocess.run(hook, input=event, capture_output=True, text=True, env=env, timeout=30)
        assert done.returncode == 0
        return json.loads(done.stdout)["hookSpecificOutput"]["decision"]["behavior"]

    assert decide({}) == "deny"  # not started by Echo: no port or token

    async def scenario():
        adapter = ClaudeCli(Stub(tmp_path, decide=True))
        adapter.token = "the-right-token"
        adapter.server = await asyncio.start_server(adapter._hook, "127.0.0.1", 0)
        port = str(adapter.server.sockets[0].getsockname()[1])
        wrong = await asyncio.to_thread(decide, {"ECHO_CODE_HOOK_PORT": port, "ECHO_CODE_HOOK_TOKEN": "guessed"})
        right = await asyncio.to_thread(decide, {"ECHO_CODE_HOOK_PORT": port, "ECHO_CODE_HOOK_TOKEN": "the-right-token"})
        adapter.server.close()
        return wrong, right

    assert run(scenario()) == ("deny", "allow")


def test_connect_requires_claude_codes_own_sign_in(tmp_path, monkeypatch):
    monkeypatch.setattr(code_accounts, "claude_status", lambda: {"installed": True, "signed_in": False})
    with pytest.raises(ValueError, match="Sign in to Claude Code first"):
        run(ClaudeCli(Stub(tmp_path)).connect())


def test_claude_status_keeps_only_sign_in_method_and_plan(monkeypatch):
    monkeypatch.setattr(code_accounts, "locate", lambda runtime: {"path": "C:/x/claude.exe", "source": "installed app", "problem": ""})
    payload = {"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "max", "email": "owner@example.com",
               "orgId": "org-1", "orgName": "Owner's Organization"}
    monkeypatch.setattr(code_accounts, "_claude", lambda *a, **k: subprocess.CompletedProcess([], 0, json.dumps(payload), ""))
    status = code_accounts.claude_status()
    assert {k: status[k] for k in ("installed", "signed_in", "method", "plan", "uses_plan")} == {
        "installed": True, "signed_in": True, "method": "claude.ai", "plan": "max", "uses_plan": True}
    assert [m["model"] for m in status["models"]][:4] == ["default", "opus", "sonnet", "haiku"]
    saved = json.dumps(code_accounts.remembered("claude_cli"))
    for private in ("owner@example.com", "org-1", "Organization"):
        assert private not in json.dumps(status) and private not in saved
    assert code_accounts.remembered("claude_cli")["signed_in"] is True  # survives a restart: read from disk


def test_codex_lists_the_plans_models_and_leaves_out_hidden_ones():
    rows = [{"model": "gpt-6.1-sol", "displayName": "GPT-6.1-Sol", "isDefault": True, "description": "Best",
             "supportedReasoningEfforts": [{"reasoningEffort": "high"}]},
            {"model": "gpt-reserve", "hidden": True}, {"model": "codex-auto-review", "hidden": True},
            {"model": "gpt-6-luna", "displayName": "GPT-6-Luna"}, "junk", {"model": ""}]
    models = code_accounts.codex_models(rows)
    assert [m["model"] for m in models] == ["gpt-6.1-sol", "gpt-6-luna"]
    assert models[0]["isDefault"] and models[0]["efforts"] == ["high"] and models[0]["description"] == "Best"


def test_echo_code_status_is_remembered_and_marked_stale_without_running_anything(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.routes.echo_code import router

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("reading the status must not run a program"))
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    code_accounts._remember("codex", {"installed": True, "signed_in": True, "plan": "plus", "models": [{"model": "gpt-6.1-sol"}]})
    fresh = client.get("/echo-code/runtimes/codex/account").json()
    assert fresh["signed_in"] and fresh["plan"] == "plus" and fresh["stale"] is False
    monkeypatch.setattr(code_accounts.time, "time", lambda: 9e12)
    assert client.get("/echo-code/runtimes/codex/account").json()["stale"] is True


def test_plans_for_code_apis_for_chat(monkeypatch):
    """Echo Code never runs an API-key provider; Chat does (switch API and the runtime both refuse)."""
    from types import SimpleNamespace

    from agent import echo_code
    from agent.lean import runtime
    from agent.lean.personas import AgentPersona
    from agent.lean.provider import Endpoint

    surfaces = {"code-1": "code", "chat-1": "chat"}
    monkeypatch.setattr(echo_code, "is_code_thread", lambda session_id: surfaces.get(session_id) == "code")
    for provider in ("openai", "anthropic", "gemini", "xai"):
        assert echo_code.code_provider_problem("code-1", provider) == echo_code.CODE_MODEL_RULE
        assert echo_code.code_provider_problem("chat-1", provider) == ""
    for provider in ("lmstudio", "ollama", "llama_cpp"):
        assert echo_code.code_provider_problem("code-1", provider) == ""

    monkeypatch.setattr(runtime, "resolve_endpoint", lambda provider, model: Endpoint(
        base_url="https://api.example", api_key="k", model=model or "m", provider=provider, local=provider == "lmstudio"))
    session = SimpleNamespace(session_id="code-1", _route_overrides={"echo": ("openai", "gpt")},
                              agent=SimpleNamespace(llm_provider=SimpleNamespace(value="lmstudio"), model_runtime=SimpleNamespace(model_id="qwen")))
    persona = AgentPersona(id="echo", name="Echo")
    with pytest.raises(PermissionError, match="Codex"):
        runtime.LeanSession._endpoint_for(session, persona)  # even smart model choice can't pick an API model here
    session._route_overrides = {}
    assert runtime.LeanSession._endpoint_for(session, persona).provider == "lmstudio"
    session.session_id = "chat-1"
    session._route_overrides = {"echo": ("openai", "gpt")}
    assert runtime.LeanSession._endpoint_for(session, persona).provider == "openai"  # Chat may use API keys


def test_codex_from_the_desktop_app_is_found_without_being_on_path(tmp_path, monkeypatch):
    bundled = tmp_path / "OpenAI.Codex_1_x64__x" / "app" / "resources" / "codex.exe"
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"")
    monkeypatch.setattr(config, "codex_executable", "")
    monkeypatch.setattr(code_executables.shutil, "which", lambda name: None)
    monkeypatch.setattr(code_executables, "_store_package_root", lambda prefix: bundled.parents[2])
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "empty"))
    found = code_executables.locate("codex")
    assert found["path"] == str(bundled) and found["source"] == "installed app"


def test_a_path_set_in_settings_wins_and_a_bad_one_says_so(tmp_path, monkeypatch):
    mine = tmp_path / "codex.exe"
    mine.write_bytes(b"")
    monkeypatch.setattr(config, "codex_executable", str(mine))
    assert code_executables.locate("codex")["source"] == "settings"
    monkeypatch.setattr(config, "codex_executable", str(tmp_path / "codex.cmd"))
    found = code_executables.locate("codex")
    assert not found["path"] and "Settings" in found["problem"]
    with pytest.raises(ValueError, match="isn't an existing .exe"):
        code_executables.executable("codex")


def test_settings_checks_run_only_when_asked_and_stay_closed_in_private_mode(monkeypatch):
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from agent import code_sessions
    from api.routes.echo_code import router

    calls = []
    blocked = {"value": False}

    def admit():
        if blocked["value"]:
            raise PermissionError("External runtimes are blocked in Private/Offline mode")

    stub = SimpleNamespace(admit=admit, call=lambda coroutine: asyncio.run(coroutine))
    monkeypatch.setattr(code_sessions, "manager", lambda: stub)

    async def codex_status():
        calls.append("codex")
        return {"installed": True, "signed_in": True, "plan": "plus"}

    monkeypatch.setattr(code_accounts, "codex_status", codex_status)
    monkeypatch.setattr(code_accounts, "claude_login", lambda: calls.append("claude-login") or {"started": True})
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    assert calls == []  # nothing runs until the owner asks
    assert client.post("/echo-code/runtimes/codex/check").json()["plan"] == "plus"
    assert client.post("/echo-code/runtimes/claude_cli/login").json() == {"started": True}
    assert client.post("/echo-code/runtimes/acp/check").status_code == 422
    blocked["value"] = True
    assert client.post("/echo-code/runtimes/codex/check").status_code == 403
    assert calls == ["codex", "claude-login"]
