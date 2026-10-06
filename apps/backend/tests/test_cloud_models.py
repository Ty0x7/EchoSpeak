"""Offline regression coverage for cloud catalogs, credentials and native streams."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from config import config, ModelProvider
from agent import cloud_providers as cloud
from agent.lean.provider import ChatClient, Endpoint, resolve_endpoint, ProviderError


@pytest.mark.parametrize("provider", cloud.CLOUD_PROVIDERS)
def test_catalog_uses_provider_auth_and_lists_all_models(monkeypatch, provider):
    key = "test-key"
    seen = []
    def handle(request):
        seen.append(request)
        assert key not in str(request.url)
        if provider == "gemini":
            assert request.headers["x-goog-api-key"] == key
            return httpx.Response(200, json={"models": [{"name": "models/gemini-3.8-live", "supportedGenerationMethods": ["bidiGenerateContent"]}, {"name": "models/embedding-test", "supportedGenerationMethods": ["embedContent"]}]})
        if provider == "anthropic":
            assert request.headers["x-api-key"] == key
            assert request.headers["anthropic-version"] == "2023-06-01"
            return httpx.Response(200, json={"data": [{"id": "claude-test"}], "has_more": False})
        assert request.headers["authorization"] == f"Bearer {key}"
        return httpx.Response(200, json={"data": [{"id": "gpt-test" if provider == "openai" else "grok-test"}, {"id": "image-test"}]})
    real_client = httpx.Client
    monkeypatch.setattr(cloud.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw))
    result = cloud.list_cloud_models(provider, key)
    assert result["reachable"] and result["models"]
    assert len(result["catalog"]) == (1 if provider == "anthropic" else 2)
    assert "embedding-test" not in result["models"]
    assert "image-test" not in result["models"]


@pytest.mark.parametrize("provider", ["gemini", "anthropic"])
def test_paginated_catalog(monkeypatch, provider):
    def handle(request):
        second = "pageToken" in request.url.params or "after_id" in request.url.params
        if provider == "gemini":
            return httpx.Response(200, json={"models": [{"name": f"models/gemini-{2 if second else 1}", "supportedGenerationMethods": ["generateContent"]}], **({} if second else {"nextPageToken": "p2"})})
        return httpx.Response(200, json={"data": [{"id": f"claude-{2 if second else 1}"}], "has_more": not second, "last_id": "claude-1"})
    real_client = httpx.Client
    monkeypatch.setattr(cloud.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw))
    assert len(cloud.list_cloud_models(provider, "test-key")["models"]) == 2


def test_keys_round_trip_through_secure_store_and_masking(tmp_path, monkeypatch):
    import config as module
    from api.routes.settings import _sanitize_incoming_settings
    monkeypatch.setattr(module, "SETTINGS_PATH", tmp_path / "settings.json")
    # Test the existing secret broker path, with an isolated durable root from conftest.
    patch = _sanitize_incoming_settings({"anthropic": {"api_key": " test-claude ", "model": " claude-custom "}, "xai": {"api_key": "test-grok", "model": "grok-custom"}, "default_cloud_provider": "xai"})
    module.write_runtime_override_payload(patch)
    stored = json.loads(module.SETTINGS_PATH.read_text())
    assert "test-claude" not in json.dumps(stored) and "test-grok" not in json.dumps(stored)
    restored = module.read_runtime_override_payload(include_secrets=True)
    assert restored["anthropic"]["api_key"] == "test-claude"
    assert restored["xai"]["api_key"] == "test-grok"
    assert _sanitize_incoming_settings({"xai": {"api_key": "***"}})["xai"] == {}
    instance = module.Config()
    assert instance.xai.model == "grok-custom" and instance.default_cloud_provider == "xai"
    assert instance.to_public_dict()["xai"]["api_key"] == "***"


def _http_client(provider, packets, handle=None):
    client = ChatClient(Endpoint(cloud.BASE_URLS[provider], "test-key", "model-test", provider, False))
    client._http.close()
    def respond(request):
        if handle:
            handle(request)
        return httpx.Response(200, text="\n".join("data: " + json.dumps(p) + "\n" for p in packets))
    client._http = httpx.Client(base_url=client.endpoint.base_url, transport=httpx.MockTransport(respond))
    return client


def test_claude_native_stream_and_tool_history():
    from agent.lean.cloud_streams import anthropic_messages
    def handle(request):
        assert request.url.path == "/v1/messages"
        assert request.headers["x-api-key"] == "test-key"
        body = json.loads(request.content)
        assert body["system"] == "Be helpful"
        assert body["messages"][0]["role"] == "user"
    packets = [{"type": "message_start", "message": {"usage": {"input_tokens": 10}}}, {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}, {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hello"}}, {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "c1", "name": "web_search", "input": {}}}, {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": '{"query":"Echo"}'}}, {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 4}}, {"type": "message_stop"}]
    client = _http_client("anthropic", packets, handle)
    messages = [{"role": "system", "content": "Be helpful"}, {"role": "user", "content": "Search"}]
    try:
        result = client.stream_turn(messages)
        assert result.content == "Hello" and result.tool_calls[0].name == "web_search"
        assert result.usage["total"] == 14
        _, history = anthropic_messages(messages + [{"role": "assistant", "provider_blocks": result.provider_blocks}, {"role": "tool", "tool_call_id": "c1", "content": "found"}])
        assert history[-1]["content"][0]["tool_use_id"] == "c1"
    finally:
        client.close()


def test_openai_reasoning_tokens():
    def handle(request):
        body = json.loads(request.content)
        assert "max_tokens" not in body and body["max_completion_tokens"] > 0
        assert "temperature" not in body
    client = _http_client("openai", [{"choices": [{"delta": {"content": "Hi"}, "finish_reason": "stop"}]}], handle)
    client.endpoint.model = "gpt-6-test"
    try:
        assert client.stream_turn([{"role": "user", "content": "hi"}], temperature=.1).content == "Hi"
    finally:
        client.close()


def test_gemini_tool_signature():
    client = _http_client("gemini", [{"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "search", "arguments": "{}"}, "extra_content": {"google": {"thought_signature": "sig"}}}]}}]}])
    try:
        assert client.stream_turn([]).tool_calls[0].extra_content["google"]["thought_signature"] == "sig"
    finally:
        client.close()


@pytest.mark.parametrize("provider", ["gemini", "openai", "xai"])
def test_unindexed_parallel_calls_keep_ids_names_arguments_and_signatures(provider):
    packets = [
        {"choices": [{"delta": {"tool_calls": [
            {"id": "call_alpha", "function": {"name": "memory_save", "arguments": '{"fact":"OK"}'}, "extra_content": {"google": {"thought_signature": "sig"}}},
            {"id": "call_beta", "function": {"name": "sports_live", "arguments": '{"query":"OK"}'}},
        ]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"id": "call_gamma", "function": {"name": "weather_live", "arguments": '{"location":"OK"}'}},
        ]}, "finish_reason": "tool_calls"}]},
    ]
    client = _http_client(provider, packets)
    try:
        calls = client.stream_turn([{"role": "user", "content": "Check"}]).tool_calls
        assert [(c.id, c.name, c.parsed_arguments()) for c in calls] == [
            ("call_alpha", "memory_save", ({"fact": "OK"}, "")),
            ("call_beta", "sports_live", ({"query": "OK"}, "")),
            ("call_gamma", "weather_live", ({"location": "OK"}, "")),
        ]
        assert calls[0].extra_content["google"]["thought_signature"] == "sig"
        assert calls[1].extra_content == {}
    finally:
        client.close()


def test_indexed_interleaved_fragments_and_unindexed_id_continuation():
    packets = [{"choices": [{"delta": {"tool_calls": calls}}]} for calls in [
        [{"index": 0, "id": "a", "function": {"name": "web_", "arguments": '{"query":"'}},
         {"index": 1, "id": "b", "function": {"name": "calculate", "arguments": '{"expression":"'}}],
        [{"index": 1, "function": {"arguments": '1+1"}'}},
         {"index": 0, "function": {"name": "search", "arguments": 'Echo'}}],
        [{"id": "a", "function": {"name": "web_search", "arguments": '"}'}}],
    ]]
    client = _http_client("openai", packets)
    try:
        calls = client.stream_turn([]).tool_calls
        assert [(c.id, c.name, c.parsed_arguments()[0]) for c in calls] == [
            ("a", "web_search", {"query": "Echo"}),
            ("b", "calculate", {"expression": "1+1"}),
        ]
    finally:
        client.close()


def test_ambiguous_tool_fragment_fails_before_tool_execution():
    client = _http_client("gemini", [{"choices": [{"delta": {"tool_calls": [
        {"id": "a", "function": {"name": "first", "arguments": "{"}},
        {"id": "b", "function": {"name": "second", "arguments": "{"}},
        {"function": {"arguments": "}"}},
    ]}}]}])
    try:
        with pytest.raises(ProviderError, match="cannot safely associate"):
            client.stream_turn([])
    finally:
        client.close()


@pytest.mark.parametrize("model,expected", [("gemini-2.5-pro", "low"), ("gemini-2.5-flash", "none"), ("gemini-3.5-flash", "low")])
def test_gemini_thinking_off_respects_models_that_require_thinking(model, expected):
    from agent.lean.provider import reasoning_effort_for
    assert reasoning_effort_for(Endpoint(cloud.BASE_URLS["gemini"], "test-key", model, "gemini", False), False, "medium") == expected


def test_live_socket_is_kept_for_tool_result_then_closed(monkeypatch):
    import websockets.sync.client
    class Socket:
        def __init__(self):
            self.packets = iter([{"setupComplete": {}}, {"toolCall": {"functionCalls": [{"id": "c1", "name": "search", "args": {"q": "Echo"}}]}}, {"serverContent": {"outputTranscription": {"text": "Found it"}}}, {"serverContent": {"turnComplete": True}}])
            self.sent = []
            self.closed = False
        def send(self, message):
            self.sent.append(json.loads(message))
        def recv(self, timeout):
            return json.dumps(next(self.packets))
        def close(self):
            self.closed = True
    socket = Socket()
    monkeypatch.setattr(websockets.sync.client, "connect", lambda url, **kw: socket)
    client = ChatClient(Endpoint(cloud.BASE_URLS["gemini"], "test-key", "gemini-3.8-live", "gemini", False))
    try:
        first = client.stream_turn([{"role": "user", "content": "Search"}], tools=[{"type": "function", "function": {"name": "search", "parameters": {"type": "object", "properties": {"q": {"type": "string"}}}}}])
        assert first.tool_calls[0].id == "c1" and not socket.closed
        second = client.stream_turn([{"role": "tool", "tool_call_id": "c1", "content": "Result"}])
        assert second.content == "Found it" and socket.closed
        assert socket.sent[0]["setup"]["outputAudioTranscription"] == {}
        assert socket.sent[0]["setup"]["tools"][0]["functionDeclarations"][0]["parameters"]["type"] == "OBJECT"
        assert socket.sent[-1]["toolResponse"]["functionResponses"][0]["name"] == "search"
    finally:
        client.close()


def test_generic_switch_model_is_preserved(monkeypatch, tmp_path):
    from api.routes import settings as routes
    from agent.state import StateStore
    store = StateStore(tmp_path)
    monkeypatch.setattr(routes, "get_state_store", lambda: store)
    monkeypatch.setattr(routes, "_is_lmstudio_only_enabled", lambda: False)
    monkeypatch.setattr(routes, "_ensure_session_model_binding", lambda session: store.ensure_session_model_binding(session, provider_id="openai", model_id="old"))
    monkeypatch.setattr(routes, "_cancel_active_queries_for_session", lambda session: 0)
    monkeypatch.setattr(routes, "_cancel_incompatible_session_work", lambda *a, **k: 0)
    result = asyncio.run(routes.switch_provider(routes.SwitchProviderRequest(provider="gemini", model="gemini-custom", session_id="cloud-test", expected_revision=1)))
    assert result["model"] == "gemini-custom"


def test_new_provider_secrets_are_redacted_and_not_inherited(monkeypatch):
    from agent.lean import policy
    from agent.child_env import child_env
    monkeypatch.setattr(config.anthropic, "api_key", "private-claude-test-key")
    monkeypatch.setattr(config.xai, "api_key", "private-grok-test-key")
    monkeypatch.setattr(policy, "_SECRET_CACHE", {"at": 0, "values": []})
    assert "private-" not in policy.redact_secrets("private-claude-test-key private-grok-test-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "private-claude-test-key")
    monkeypatch.setenv("XAI_API_KEY", "private-grok-test-key")
    assert "ANTHROPIC_API_KEY" not in child_env() and "XAI_API_KEY" not in child_env()


def test_model_lists_report_the_saved_model_so_pickers_restore_it(monkeypatch):
    """Switching providers in the composer restores that provider's saved model, even
    when its catalog is empty (no key yet) or unreachable."""
    from api.routes import settings as routes

    monkeypatch.setattr(routes, "_is_lmstudio_only_enabled", lambda: False)
    monkeypatch.setattr(routes, "list_cloud_models", lambda provider: {"provider": provider, "models": [], "reachable": False})
    monkeypatch.setattr(config.openai, "model", "gpt-4o-mini", raising=False)
    cloud_out = asyncio.run(routes.list_provider_models(provider="openai"))
    assert cloud_out["saved_model"] == "gpt-4o-mini" and cloud_out["models"] == []

    import agent.model_runtime as runtime
    monkeypatch.setattr(runtime, "list_local_models", lambda provider, base, timeout=4.0: ["qwen/qwen3.5-9b"])
    monkeypatch.setattr(config.local, "provider", ModelProvider.LM_STUDIO, raising=False)
    monkeypatch.setattr(config.local, "model_name", "google/gemma-4-e4b", raising=False)
    assert asyncio.run(routes.list_provider_models(provider="lmstudio"))["saved_model"] == "google/gemma-4-e4b"
    # Another local app than the configured one has no saved model of its own.
    assert asyncio.run(routes.list_provider_models(provider="ollama"))["saved_model"] == ""
