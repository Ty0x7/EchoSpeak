"""Settings consolidation: retired keys are migrated away, the Advanced page reads
real values, and webhooks only run when switched on and signed."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
import api.routes.channels as channels_routes
import api.deps as deps
import api.routes.settings as settings_routes
from tests.route_paths import patch_api


def test_retired_keys_are_dropped_from_the_stored_settings(tmp_path, monkeypatch):
    import config as config_module

    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"cron_enabled": True, "discord_bot_auto_confirm": True, "web_search_timeout": 9}), encoding="utf-8")
    monkeypatch.setattr(config_module, "SETTINGS_PATH", path)

    payload = config_module._drop_retired_settings(json.loads(path.read_text(encoding="utf-8")))
    assert payload == {"web_search_timeout": 9}
    assert json.loads(path.read_text(encoding="utf-8")) == {"web_search_timeout": 9}  # written back once
    assert not set(config_module.RETIRED_SETTING_KEYS) & set(config_module.config.to_public_dict())


def test_settings_response_reports_lm_studio_only(monkeypatch):
    from api import server

    for stored in (True, False):
        patch_api(monkeypatch, "_read_runtime_settings", lambda stored=stored: {"lm_studio_only": stored})
        assert settings_routes._settings_response().settings["lm_studio_only"] is stored


class _Request:
    def __init__(self, body: bytes, signature: str = ""):
        self._body = body
        self.headers = {"x-echospeak-signature": signature} if signature else {}

    async def body(self) -> bytes:
        return self._body


@pytest.fixture
def webhook(monkeypatch):
    from agent import routines
    from api import server

    runs: list[str] = []
    routine = SimpleNamespace(id="r1", name="Build report", action_config={})
    manager = SimpleNamespace(get_routine_by_webhook=lambda path: routine if path == "/build" else None, run_routine=runs.append)
    monkeypatch.setattr(routines, "get_routine_manager", lambda: manager)
    monkeypatch.setattr(server.config, "webhook_enabled", True, raising=False)
    monkeypatch.setattr(channels_routes, "_load_webhook_secret", lambda: "s3cret")
    return server, runs


def _call(server, request):
    return asyncio.run(channels_routes.webhook_trigger("build", request))


def test_webhooks_are_refused_while_switched_off(webhook, monkeypatch):
    server, runs = webhook
    monkeypatch.setattr(server.config, "webhook_enabled", False, raising=False)
    with pytest.raises(HTTPException) as err:
        _call(server, _Request(b"{}"))
    assert err.value.status_code == 403 and runs == []


def test_unsigned_webhooks_are_refused(webhook, monkeypatch):
    server, runs = webhook
    monkeypatch.setattr(channels_routes, "_load_webhook_secret", lambda: "")
    with pytest.raises(HTTPException) as err:
        _call(server, _Request(b"{}"))
    assert err.value.status_code == 403 and runs == []

    monkeypatch.setattr(channels_routes, "_load_webhook_secret", lambda: "s3cret")
    with pytest.raises(HTTPException) as err:
        _call(server, _Request(b"{}", signature="sha256=wrong"))
    assert err.value.status_code == 401 and runs == []


def test_signed_webhooks_run_the_routine(webhook):
    server, runs = webhook
    body = b'{"x": 1}'
    digest = hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    signature = next(
        sig for sig in (f"sha256={digest}", digest) if channels_routes._verify_webhook_signature("s3cret", body, sig)
    )
    assert _call(server, _Request(body, signature=signature)) == {"ok": True, "triggered": "Build report"}
    assert runs == ["r1"]
