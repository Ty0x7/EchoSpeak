"""Regression tests for the 10.2 security audit fixes."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient


def test_calculate_never_evaluates_python():
    from agent.tools import calculate

    run = getattr(calculate, "func", calculate)
    assert run("2+3*4") == "14"
    assert run("sqrt(16) + pi") .startswith("7.14")
    for attack in (
        "().__class__.__mro__[1].__subclasses__()",
        "__import__('os').system('echo hi')",
        "[c for c in ().__class__.__base__.__subclasses__()]",
        "(lambda: 1)()",
        "'a' * 10",
        "9 ** 9 ** 9",
    ):
        assert run(attack).startswith("Calculation error"), attack


def test_unknown_and_a2a_sources_are_guests():
    from agent.adapters import BaseAdapter
    from config import DiscordUserRole

    adapter = BaseAdapter()
    assert adapter.resolve_role("a2a") == DiscordUserRole.PUBLIC
    assert adapter.resolve_role("some_new_channel") == DiscordUserRole.PUBLIC
    assert adapter.resolve_role("web") == DiscordUserRole.OWNER
    assert adapter.resolve_role("voice") == DiscordUserRole.OWNER


def test_a2a_requires_its_key(monkeypatch):
    from api.routes import channels
    from config import config

    class Req:
        def __init__(self, auth):
            self.headers = {"authorization": auth}

    monkeypatch.setattr(config, "a2a_auth_key", "", raising=False)
    with pytest.raises(HTTPException) as no_key:
        channels._a2a_auth_check(Req("Bearer anything"))
    assert no_key.value.status_code == 503
    monkeypatch.setattr(config, "a2a_auth_key", "s3cret-key-value", raising=False)
    with pytest.raises(HTTPException):
        channels._a2a_auth_check(Req("Bearer wrong"))
    channels._a2a_auth_check(Req("Bearer s3cret-key-value"))


def test_gateway_websocket_refuses_other_sites():
    from api.server import app

    client = TestClient(app, client=("127.0.0.1", 50000))
    with pytest.raises(Exception):
        with client.websocket_connect("/gateway/ws", headers={"host": "127.0.0.1:8000", "origin": "https://evil.example"}):
            pass
    with pytest.raises(Exception):
        with client.websocket_connect("/gateway/ws", headers={"host": "rebind.evil.example"}):
            pass
    with client.websocket_connect("/gateway/ws", headers={"host": "127.0.0.1:8000", "origin": "http://localhost:5174"}) as ws:
        assert ws.receive_json()["type"] == "gateway_ready"


def test_child_processes_do_not_get_echospeak_keys(monkeypatch):
    from agent.child_env import child_env

    monkeypatch.setenv("API_AUTH_KEY", "launch-key")
    monkeypatch.setenv("ADMIN_API_KEY", "launch-key")
    monkeypatch.setenv("PATH_FOR_TEST", "kept")
    env = child_env({"EXTRA": "1"})
    assert "API_AUTH_KEY" not in env and "ADMIN_API_KEY" not in env
    assert env["PATH_FOR_TEST"] == "kept" and env["EXTRA"] == "1"


def test_tool_output_never_carries_stored_secrets():
    from agent.lean.policy import redact_secrets

    secret = "sk-test-0123456789abcdef"
    out = redact_secrets(f'{{"openai": {{"api_key": "{secret}"}}}}', secrets=[secret])
    assert secret not in out and "[redacted secret]" in out


def test_desktop_auth_cannot_be_loosened_by_saved_settings(monkeypatch):
    from config import config

    monkeypatch.setenv("ECHOSPEAK_RUNTIME_KIND", "desktop")
    monkeypatch.setenv("API_AUTH_KEY", "host-launch-key")
    monkeypatch.setattr(config, "api_auth_enabled", False, raising=False)
    monkeypatch.setattr(config, "api_auth_localhost_bypass", True, raising=False)
    config._enforce_host_auth()
    assert config.api_auth_enabled is True
    assert config.api_auth_localhost_bypass is False
    assert config.api_auth_key == "host-launch-key"


def test_provider_detect_get_changes_nothing(monkeypatch):
    from api.routes import settings as settings_routes
    from agent import model_runtime

    calls = []
    monkeypatch.setattr(model_runtime, "detect_local_providers", lambda: [])
    monkeypatch.setattr(settings_routes, "_autoconfigure_local_provider", lambda force=False: calls.append(force))
    import asyncio

    assert asyncio.run(settings_routes.detect_providers())["applied"] is None
    assert calls == []
    asyncio.run(settings_routes.detect_and_apply_provider())
    assert calls == [True]


def test_discord_channel_history_taints_the_turn():
    import threading

    from agent.lean import runtime as lean_runtime

    session = lean_runtime.LeanSession(
        agent=type("Agent", (), {"memory": None})(), session_id="taint-test", request_id="r",
        emit=lambda e: None, cancel=threading.Event(), source="discord_bot", untrusted_sources=["discord_channel"],
    )
    assert session._taint == ["discord_channel"]
