"""The owner's chosen context window is actually served (campaign 24 November 2026, workstream 1).

Ollama's /v1 ignores a per-request size, so a chosen window becomes a model variant (agent/context_windows.py).
A fake Ollama records /api/create and answers /api/show and /api/ps like Ollama 0.40.16 did on the owner's PC.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agent import context_windows as cw
from agent.lean import window
from agent.lean.provider import ChatClient, Endpoint, resolve_endpoint
from config import config


class FakeOllama:
    def __init__(self, trained: int = 262144, create_ok: bool = True) -> None:
        self.created: list[dict] = []
        self.chat_models: list[str] = []
        self.loaded: dict[str, int] = {}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, body, code=200):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/api/ps":
                    self._json({"models": [{"name": n, "model": n, "context_length": c} for n, c in outer.loaded.items()]})
                else:
                    self._json({}, 404)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                if self.path == "/api/show":
                    self._json({"model_info": {"qwen35.context_length": trained, "general.architecture": "qwen35"}})
                elif self.path == "/api/create":
                    outer.created.append(body)
                    self._json({"status": "success"} if create_ok else {"error": "unknown parameter"},
                               200 if create_ok else 400)
                elif self.path == "/v1/chat/completions":
                    outer.chat_models.append(body.get("model"))
                    num_ctx = next((c["parameters"]["num_ctx"] for c in outer.created if c["model"] == body["model"]),
                                   16384)
                    outer.loaded = {body["model"]: num_ctx}
                    chunk = {"choices": [{"delta": {"content": "OK"}, "finish_reason": "stop", "index": 0}]}
                    data = f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    self._json({}, 404)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture(autouse=True)
def _fresh():
    cw.forget()
    window._served.clear()
    yield
    cw.forget()
    window._served.clear()


def test_variant_names_are_valid_ollama_names():
    assert cw.variant_name("qwen3.5:9b", 32768) == "echospeak/qwen3.5-9b:32k"
    assert cw.variant_name("gemma4:e4b-it-qat", 65536) == "echospeak/gemma4-e4b-it-qat:64k"
    assert cw.variant_name("llama3:latest", 16384) == "echospeak/llama3:16k"
    assert cw.is_variant("echospeak/qwen3.5-9b:32k") and not cw.is_variant("qwen3.5:9b")


def test_choices_stop_at_the_trained_window():
    server = FakeOllama(trained=65536)
    try:
        rows = cw.choices(server.url + "/v1", "small:7b")
        assert [r["tokens"] for r in rows] == [16384, 32768, 65536]
        assert [r["default"] for r in rows] == [False, True, False]
    finally:
        server.close()


def test_a_chosen_window_is_created_once_and_served(monkeypatch):
    server = FakeOllama()
    try:
        monkeypatch.setattr(config.local, "base_url", server.url)
        monkeypatch.setattr(config.local, "model_name", "qwen3.5:9b")
        monkeypatch.setattr(config.local, "context_length", 32768)
        endpoint = resolve_endpoint("ollama")
        assert endpoint.model == "qwen3.5:9b" and endpoint.want_context == 32768
        assert server.created == []                                 # resolving never touches the server
        ChatClient(endpoint).stream_turn([{"role": "user", "content": "hi"}])
        assert endpoint.wire_model == "echospeak/qwen3.5-9b:32k"    # identity and display stay the base model
        assert server.created == [{"model": "echospeak/qwen3.5-9b:32k", "from": "qwen3.5:9b",
                                   "parameters": {"num_ctx": 32768}, "stream": False}]
        again = resolve_endpoint("ollama")
        ChatClient(again).stream_turn([{"role": "user", "content": "hi"}])
        assert len(server.created) == 1                             # made once per server, model and size
        assert server.chat_models[0] == "echospeak/qwen3.5-9b:32k"  # the request goes to the variant
        window._served.clear()   # the pre-request check cached "not loaded yet" for its 10 s negative TTL
        assert window.served(endpoint) == 32768                     # and the server reports what it loaded
    finally:
        server.close()


def test_a_server_that_cannot_make_variants_is_reported_honestly(monkeypatch):
    server = FakeOllama(create_ok=False)
    try:
        monkeypatch.setattr(config.local, "base_url", server.url)
        monkeypatch.setattr(config.local, "model_name", "qwen3.5:9b")
        monkeypatch.setattr(config.local, "context_length", 65536)
        endpoint = resolve_endpoint("ollama")
        ChatClient(endpoint).stream_turn([{"role": "user", "content": "hi"}])
        assert endpoint.wire_model == "qwen3.5:9b" and endpoint.want_context == 0
        window._served.clear()
        assert window.served(endpoint) == 16384                     # the real window, not the 64K asked for
    finally:
        server.close()


def test_other_providers_keep_their_own_window():
    assert Endpoint("http://x/v1", "k", "gpt-x", "openai", False).wire_model == "gpt-x"
    assert cw.served_model("http://127.0.0.1:9/v1", "echospeak/qwen3.5-9b:32k", 65536) == ""   # never a variant of a variant


def test_a_restarted_backend_reads_the_variant_already_loaded(monkeypatch):
    """After a restart nothing is made yet in this process; the served window must still be the variant's."""
    server = FakeOllama()
    try:
        server.loaded = {"echospeak/qwen3.5-9b:32k": 32768}          # loaded by the backend before it restarted
        monkeypatch.setattr(config.local, "base_url", server.url)
        monkeypatch.setattr(config.local, "model_name", "qwen3.5:9b")
        monkeypatch.setattr(config.local, "context_length", 32768)
        endpoint = resolve_endpoint("ollama")
        assert endpoint.wire == "" and endpoint.served_name == "echospeak/qwen3.5-9b:32k"
        assert window.served(endpoint) == 32768 and server.created == []
    finally:
        server.close()


def test_switching_windows_reports_the_old_one_until_the_new_one_loads(monkeypatch):
    server = FakeOllama()
    try:
        server.loaded = {"echospeak/qwen3.5-9b:64k": 65536}
        monkeypatch.setattr(config.local, "base_url", server.url)
        monkeypatch.setattr(config.local, "model_name", "qwen3.5:9b")
        monkeypatch.setattr(config.local, "context_length", 32768)
        endpoint = resolve_endpoint("ollama")
        assert window.served(endpoint) == 0                          # 32K isn't loaded: never reported as served
        assert [r["context"] for r in cw.loaded(endpoint.base_url, endpoint.model)] == [65536]
        ChatClient(endpoint).stream_turn([{"role": "user", "content": "hi"}])
        window._served.clear()
        assert window.served(endpoint) == 32768
    finally:
        server.close()


def test_a_window_above_the_trained_one_is_capped(monkeypatch):
    server = FakeOllama(trained=65536)
    try:
        monkeypatch.setattr(config.local, "base_url", server.url)
        monkeypatch.setattr(config.local, "model_name", "small:7b")
        monkeypatch.setattr(config.local, "context_length", 131072)
        endpoint = resolve_endpoint("ollama")
        assert endpoint.served_name == "echospeak/small-7b:64k"
        ChatClient(endpoint).stream_turn([{"role": "user", "content": "hi"}])
        assert server.created[0]["parameters"]["num_ctx"] == 65536 and endpoint.wire == "echospeak/small-7b:64k"
    finally:
        server.close()


def test_the_old_16k_default_moves_to_32k_once(tmp_path, monkeypatch):
    import config as cfg

    settings = tmp_path / "settings.json"
    monkeypatch.setattr(cfg, "SETTINGS_PATH", settings)
    monkeypatch.setattr(cfg, "_CONTEXT_32K_MARKER", tmp_path / ".marker")
    moved = cfg._migrate_context_32k({"local": {"provider": "ollama", "context_length": 16384}})
    assert moved["local"]["context_length"] == 32768 and json.loads(settings.read_text())["local"]["context_length"] == 32768
    # A 16K chosen afterwards stays: the migration runs once.
    assert cfg._migrate_context_32k({"local": {"provider": "ollama", "context_length": 16384}})["local"]["context_length"] == 16384
    # Other providers and other sizes are left alone.
    (tmp_path / ".marker").unlink()
    assert cfg._migrate_context_32k({"local": {"provider": "lmstudio", "context_length": 16384}})["local"]["context_length"] == 16384
    (tmp_path / ".marker").unlink()
    assert cfg._migrate_context_32k({"local": {"provider": "ollama", "context_length": 65536}})["local"]["context_length"] == 65536
