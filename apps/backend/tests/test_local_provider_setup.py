"""Local model setup on a fresh PC: the right port follows the chosen app, chats follow
the default, and a running LM Studio / Ollama is found without the user doing anything."""

from __future__ import annotations

import pytest

from config import ModelProvider


def test_stock_addresses_follow_the_selected_app():
    from agent.model_runtime import resolve_local_provider_base_url as resolve

    # Another app's stock port, in any spelling, never leaks into LM Studio.
    for stale in ("http://localhost:11434", "http://127.0.0.1:11434/", "http://localhost:11434/v1", "localhost:11434"):
        assert resolve(ModelProvider.LM_STUDIO, stale) == "http://localhost:1234"
    assert resolve(ModelProvider.OLLAMA, "http://127.0.0.1:1234/v1") == "http://localhost:11434"
    assert resolve(ModelProvider.LM_STUDIO, "") == "http://localhost:1234"
    # A custom address is the user's choice and is kept.
    assert resolve(ModelProvider.LM_STUDIO, "http://192.168.1.20:1234") == "http://192.168.1.20:1234"
    assert resolve(ModelProvider.LM_STUDIO, "http://localhost:5555") == "http://localhost:5555"


def test_fresh_install_defaults_to_lm_studio_without_a_personal_model():
    from config import LocalModelConfig

    fresh = LocalModelConfig()
    assert fresh.provider == ModelProvider.LM_STUDIO
    assert fresh.base_url == "http://localhost:1234"
    assert fresh.model_name == ""


def test_detect_reports_running_apps_lm_studio_first(monkeypatch):
    from agent import model_runtime

    loaded = {"lmstudio": ["google/gemma-4-e4b", "text-embedding-nomic"], "ollama": ["llama3.2"]}
    monkeypatch.setattr(
        model_runtime,
        "list_local_models",
        lambda provider, base_url="", timeout=2.5: [m for m in loaded.get(ModelProvider(provider).value, []) if "embed" not in m],
    )
    rows = model_runtime.detect_local_providers()
    assert [r["provider"] for r in rows][:2] == ["lmstudio", "ollama"]
    assert rows[0] == {"provider": "lmstudio", "base_url": "http://localhost:1234", "running": True, "models": ["google/gemma-4-e4b"]}
    assert not next(r for r in rows if r["provider"] == "vllm")["running"]


def test_empty_model_uses_what_the_app_has_loaded(monkeypatch):
    from agent import model_runtime
    from agent.lean import provider as lean_provider

    monkeypatch.setattr(lean_provider.config.local, "model_name", "", raising=False)
    monkeypatch.setattr(lean_provider.config.local, "base_url", "http://localhost:11434", raising=False)
    monkeypatch.setattr(model_runtime, "first_local_model", lambda provider, base_url="": "qwen/qwen3.5-9b")
    endpoint = lean_provider.resolve_endpoint("lmstudio", "default")
    assert endpoint.base_url == "http://localhost:1234/v1"
    assert endpoint.model == "qwen/qwen3.5-9b"


@pytest.fixture
def store(tmp_path, monkeypatch):
    from agent import state as state_module

    monkeypatch.setattr(state_module, "DATA_DIR", tmp_path, raising=False)
    return state_module.StateStore(tmp_path) if hasattr(state_module, "StateStore") else state_module.get_state_store()


def test_chats_on_the_old_default_follow_the_new_one(store):
    store.ensure_session_model_binding("fresh", provider_id="ollama", model_id="default")
    store.ensure_session_model_binding("picked", provider_id="ollama", model_id="llama3.2")
    store.ensure_session_model_binding("other", provider_id="openai", model_id="gpt-4o-mini")

    changed = store.retarget_default_bindings(
        old_provider_id="ollama", old_model_id="llama3.2", new_provider_id="lmstudio", new_model_id="google/gemma-4-e4b"
    )
    assert sorted(changed) == ["fresh", "picked"]
    moved = store.ensure_session_model_binding("fresh", provider_id="x", model_id="x")
    assert (moved.provider_id, moved.model_id, moved.binding_revision) == ("lmstudio", "google/gemma-4-e4b", 2)
    kept = store.ensure_session_model_binding("other", provider_id="x", model_id="x")
    assert (kept.provider_id, kept.model_id) == ("openai", "gpt-4o-mini")


def test_settings_app_change_moves_the_port_with_it(monkeypatch):
    from api import server

    stored: dict = {"use_local_models": True, "local": {"provider": "ollama", "base_url": "http://localhost:11434", "model_name": "llama3.2"}}
    monkeypatch.setattr(server, "_read_runtime_settings", lambda: stored)
    monkeypatch.setattr(server, "write_runtime_override_payload", lambda payload: stored.update(payload))
    monkeypatch.setattr(server.config, "reload", lambda: None)

    server._apply_settings_patch({"local": {"provider": "lmstudio"}})
    assert stored["local"]["provider"] == "lmstudio"
    assert stored["local"]["base_url"] == "http://localhost:1234"

    # A custom address stays put when the app changes.
    stored["local"]["base_url"] = "http://192.168.1.20:9000"
    server._apply_settings_patch({"local": {"provider": "ollama"}})
    assert stored["local"]["base_url"] == "http://192.168.1.20:9000"


def test_switch_ignores_a_stale_stock_address(monkeypatch):
    from agent.model_runtime import is_known_local_default_url

    assert is_known_local_default_url("http://127.0.0.1:11434/v1")
    assert not is_known_local_default_url("http://192.168.1.20:1234")


def test_first_run_setup_picks_the_running_app_once(monkeypatch):
    from agent import model_runtime
    from api import server

    stored: dict = {}
    monkeypatch.setattr(server, "_read_runtime_settings", lambda: stored)
    monkeypatch.setattr(server, "write_runtime_override_payload", lambda payload: (stored.clear(), stored.update(payload)))
    monkeypatch.setattr(server.config, "reload", lambda: None)
    monkeypatch.setattr(server.config.openai, "api_key", "", raising=False)
    monkeypatch.setattr(server.config.gemini, "api_key", "", raising=False)
    monkeypatch.delenv("USE_LOCAL_MODELS", raising=False)
    monkeypatch.delenv("LOCAL_MODEL_NAME", raising=False)
    monkeypatch.setattr(server, "_AUTOCONFIG_LAST", 0.0)
    monkeypatch.setattr(
        model_runtime,
        "detect_local_providers",
        lambda timeout=1.2: [
            {"provider": "lmstudio", "base_url": "http://localhost:1234", "running": True, "models": ["google/gemma-4-e4b"]},
            {"provider": "ollama", "base_url": "http://localhost:11434", "running": False, "models": []},
        ],
    )
    found = server._autoconfigure_local_provider()
    assert found and found["provider"] == "lmstudio"
    assert stored["use_local_models"] is True
    assert stored["local"] == {"provider": "lmstudio", "base_url": "http://localhost:1234", "model_name": "google/gemma-4-e4b"}
    # The choice is now made, so it never runs again on its own.
    assert server._local_setup_chosen()
    assert server._autoconfigure_local_provider() is None
