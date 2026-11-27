"""Two local runtimes on one 8 GB card (agent/lean/gpu_handoff.py, Bonsai integration 27 Nov 2026)."""
from __future__ import annotations

import http.server
import json
import threading

import pytest

from agent.lean import gpu_handoff
from agent.lean.provider import Endpoint


@pytest.fixture
def fake_ollama(monkeypatch):
    seen: list[tuple[str, dict]] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = json.dumps({"models": [{"name": "qwen3.5:9b"}]}).encode()
            self.send_response(200); self.end_headers(); self.wfile.write(body)

        def do_POST(self):  # noqa: N802
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((self.path, data))
            self.send_response(200); self.end_headers(); self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("OLLAMA_HOST", f"127.0.0.1:{server.server_port}")
    monkeypatch.delenv("LOCAL_GPU_HANDOFF", raising=False)
    gpu_handoff.reset()
    yield seen
    server.shutdown()
    gpu_handoff.reset()


def test_calling_llama_server_unloads_ollamas_model_first(fake_ollama):
    bonsai = Endpoint("http://127.0.0.1:8095/v1", "not-needed", "Ternary-Bonsai-2.5-27B", "localai", True)
    assert gpu_handoff.before_call(bonsai) == ["qwen3.5:9b"]
    assert fake_ollama == [("/api/generate", {"model": "qwen3.5:9b", "keep_alive": 0})]
    assert gpu_handoff.before_call(bonsai) == []  # throttled: not on every step


@pytest.mark.parametrize("endpoint", [
    Endpoint("http://127.0.0.1:11434/v1", "", "qwen3.5:9b", "ollama", True),           # Ollama itself
    Endpoint("http://192.168.1.5:8095/v1", "", "m", "localai", True),                  # not this machine
    Endpoint("https://api.example.com/v1", "k", "m", "openai", False),                 # cloud
])
def test_other_endpoints_never_touch_ollama(fake_ollama, endpoint, monkeypatch):
    monkeypatch.setenv("LOCAL_GPU_PEERS", "")
    assert gpu_handoff.before_call(endpoint) == []
    assert fake_ollama == []


def test_it_can_be_turned_off(fake_ollama, monkeypatch):
    monkeypatch.setenv("LOCAL_GPU_HANDOFF", "0")
    assert gpu_handoff.before_call(Endpoint("http://127.0.0.1:8095/v1", "", "m", "localai", True)) == []
    assert fake_ollama == []


@pytest.fixture
def fake_router(monkeypatch):
    state = {"loaded": True, "unloads": 0}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = json.dumps({"data": [{"id": "Ternary-Bonsai-2.5-27B",
                                         "status": {"value": "loaded" if state["loaded"] else "unloaded"}}]}).encode()
            self.send_response(200); self.end_headers(); self.wfile.write(body)

        def do_POST(self):  # noqa: N802
            self.rfile.read(int(self.headers["Content-Length"]))
            state["loaded"] = False; state["unloads"] += 1
            self.send_response(200); self.end_headers(); self.wfile.write(b'{"success":true}')

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("LOCAL_GPU_PEERS", f"http://127.0.0.1:{server.server_port}")
    yield state, server.server_port
    server.shutdown()


def test_calling_ollama_unloads_a_loaded_router_model_and_switching_back_and_forth_always_hands_over(
        fake_ollama, fake_router):
    state, port = fake_router
    qwen = Endpoint("http://127.0.0.1:11434/v1", "", "qwen3.5:9b", "ollama", True)
    bonsai = Endpoint(f"http://127.0.0.1:{port}/v1", "", "Ternary-Bonsai-2.5-27B", "localai", True)
    assert gpu_handoff.before_call(qwen) == [f"http://127.0.0.1:{port}:Ternary-Bonsai-2.5-27B"]
    assert gpu_handoff.before_call(bonsai) == ["qwen3.5:9b"]          # immediately after: not throttled
    state["loaded"] = True
    assert gpu_handoff.before_call(qwen) != []                       # and back again at once
    assert state["unloads"] == 2


def test_a_plain_llama_server_without_router_status_is_left_alone(fake_ollama, monkeypatch):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200); self.end_headers(); self.wfile.write(b'{"data":[{"id":"m"}]}')

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("LOCAL_GPU_PEERS", f"http://127.0.0.1:{server.server_port}")
    try:
        assert gpu_handoff.before_call(Endpoint("http://127.0.0.1:11434/v1", "", "q", "ollama", True)) == []
    finally:
        server.shutdown()
