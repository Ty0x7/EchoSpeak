import base64
import hashlib
import importlib.util
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from agent import cloud_providers as cloud
from agent.lean.provider import ChatClient, Endpoint, ModelTurn, ProviderError


@pytest.mark.parametrize("status,detail,category", [
    (401, "invalid API key", "authentication"), (403, "permission_denied", "permission"),
    (403, "unsupported_country_region_territory", "region"),
    (429, '{"error":{"code":"insufficient_quota"}}', "billing"),
    (402, "payment required", "billing"), (429, "rate_limit_exceeded", "rate_limit"),
    (404, "model_not_found", "model"), (529, "overloaded", "overloaded"),
])
def test_cloud_error_remedies(status, detail, category):
    assert cloud.cloud_error_category(status, detail) == category
    assert "HTTP" in cloud.provider_error("openai", status, detail)


def test_selected_model_test_uses_chat_adapter_and_does_not_execute_tools(monkeypatch):
    seen = []
    class Client:
        def __init__(self, endpoint):
            seen.append(endpoint)
            self._http = SimpleNamespace(timeout=None)
        def stream_turn(self, messages, **kwargs):
            assert "tools" not in kwargs
            assert kwargs["max_tokens"] == 512
            return ModelTurn(content="OK")
        def close(self):
            seen.append("closed")
    monkeypatch.setattr("agent.lean.provider.ChatClient", Client)
    result = cloud.test_cloud_model("gemini", "gemini-custom", "test-key")
    assert result["ok"] and result["check"] == "generation"
    assert seen[0].model == "gemini-custom" and seen[0].api_key == "test-key"
    assert seen[-1] == "closed"


def test_specialized_model_is_not_sent_to_chat(monkeypatch):
    monkeypatch.setattr("agent.lean.provider.ChatClient", lambda *args: pytest.fail("must reject before request"))
    result = cloud.test_cloud_model("openai", "gpt-image-1", "test-key")
    assert not result["ok"] and result["error_code"] == "configuration"


def test_notebook_reads_and_startup_physically_prune_expired_rows(tmp_path):
    from agent.research_notebook import ResearchNotebook, TTL
    book = ResearchNotebook(tmp_path)
    book.retain("chat-a", url="https://example.com", title="Evidence", text="A passage", inspected=True)
    book.notes("chat-a", "A private working note")
    with book.connect() as db:
        db.execute("UPDATE sources SET updated=0")
        db.execute("UPDATE notes SET updated=0")
    restarted = ResearchNotebook(tmp_path)
    assert restarted.sources("chat-a") == [] and restarted.notes("chat-a") == ""
    with restarted.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 0
        assert db.execute("PRAGMA secure_delete").fetchone()[0] == 1
    source = restarted.retain("chat-a", url="https://example.com", title="New", text="new")
    assert restarted.sources("chat-b") == []
    with pytest.raises(ValueError):
        restarted.read("chat-b", source)
    assert restarted.sources("chat-a")[0]["expires_at"] - restarted.sources("chat-a")[0]["updated"] == TTL


def test_notebook_api_scopes_sources_to_existing_chat(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.routes import sessions
    from agent.research_notebook import ResearchNotebook
    book = ResearchNotebook(tmp_path)
    source = book.retain("a", url="https://example.com", title="A", text="private passage")
    monkeypatch.setattr("agent.threads.get_thread_manager", lambda: SimpleNamespace(get_thread=lambda key: object() if key in {"a", "b"} else None))
    monkeypatch.setattr("agent.research_notebook.ResearchNotebook", lambda: book)
    app = FastAPI(); app.include_router(sessions.router)
    with TestClient(app) as client:
        assert client.get("/sessions/a/research").json()["sources"][0]["id"] == source
        assert client.get(f"/sessions/b/research/sources/{source}").status_code == 404
        assert client.get("/sessions/missing/research").status_code == 404


def _download_fixture(tmp_path, monkeypatch, handler):
    from agent import generation_setup as setup
    old_client = httpx.Client
    monkeypatch.setattr(setup.httpx, "Client", lambda **kwargs: old_client(**kwargs, transport=httpx.MockTransport(handler)))
    setup._CANCEL.clear()
    target = tmp_path / "model.bin"
    digest = hashlib.sha256(b"hello world").hexdigest()
    target.with_suffix(".bin.part").write_bytes(b"hello ")
    target.with_suffix(".bin.part.json").write_text(json.dumps({"url": "https://example.com/model", "sha256": digest, "size": 11, "etag": '"version1"'}))
    return setup, target, digest


def test_download_resumes_and_checks_complete_digest(tmp_path, monkeypatch):
    def handler(request):
        assert request.headers["range"] == "bytes=6-"
        assert request.headers["if-range"] == '"version1"'
        return httpx.Response(206, headers={"Content-Range": "bytes 6-10/11"}, content=b"world")
    setup, target, digest = _download_fixture(tmp_path, monkeypatch, handler)
    setup.download("https://example.com/model", target, digest, 11)
    assert target.read_bytes() == b"hello world"
    assert not target.with_suffix(".bin.part").exists()


def test_server_ignoring_range_restarts_download(tmp_path, monkeypatch):
    setup, target, digest = _download_fixture(tmp_path, monkeypatch, lambda request: httpx.Response(200, content=b"hello world"))
    setup.download("https://example.com/model", target, digest, 11)
    assert target.read_bytes() == b"hello world"


def test_bad_resume_response_is_never_installed(tmp_path, monkeypatch):
    setup, target, digest = _download_fixture(tmp_path, monkeypatch, lambda request: httpx.Response(206, headers={"Content-Range": "bytes 1-10/11"}, content=b"wrong"))
    with pytest.raises(ValueError, match="range"):
        setup.download("https://example.com/model", target, digest, 11)
    assert not target.exists() and not target.with_suffix(".bin.part").exists()


def test_driver_alone_does_not_choose_cuda13_for_old_gpu():
    from agent.generation_setup import runtime_variant
    assert runtime_variant({"driver": "581.00", "compute_capability": 6.1}) == "nvidia_cu126"
    assert runtime_variant({"driver": "581.00", "compute_capability": 8.6}) == "nvidia"
    with pytest.raises(ValueError, match="Blackwell"):
        runtime_variant({"driver": "560.00", "compute_capability": 12.0})
    with pytest.raises(ValueError, match="528.33"):
        runtime_variant({"driver": "527.00", "compute_capability": 8.6})


def test_explicit_gpu_selection_and_largest_default(tmp_path, monkeypatch):
    from agent import generation_setup as setup
    monkeypatch.setattr(setup, "ROOT", tmp_path)
    devices = [{"id": "GPU-a", "vram_mb": 4000}, {"id": "GPU-b", "vram_mb": 12000}]
    assert setup.hardware(devices=devices)["id"] == "GPU-b"
    assert setup.hardware("GPU-a", devices=devices)["id"] == "GPU-a"
    assert setup.hardware("GPU-gone", devices=devices) == {}


class Socket:
    def __init__(self, packets):
        self.packets = iter(packets)
        self.sent = []
        self.closed = False
    def send(self, value): self.sent.append(json.loads(value))
    def recv(self, timeout): return json.dumps(next(self.packets))
    def close(self): self.closed = True


def test_native_pcm_input_audio_output_and_prefetched_turn(monkeypatch):
    from agent.live_voice import LiveMicrophone, schema_fingerprint
    packets = [{"setupComplete": {}}, {"serverContent": {"inputTranscription": {"text": "Hello Echo"},
               "outputTranscription": {"text": "Hello"}, "modelTurn": {"parts": [{"inlineData": {"data": "AAA=", "mimeType": "audio/pcm;rate=24000"}}]}, "turnComplete": True}}]
    socket = Socket(packets)
    monkeypatch.setattr("websockets.sync.client.connect", lambda *args, **kwargs: socket)
    microphone = LiveMicrophone(lambda event: None)
    microphone.feed(b"\0\0" * 320)
    client = ChatClient(Endpoint(cloud.BASE_URLS["gemini"], "test", "gemini-test-live", "gemini", False))
    client.live_input, client.live_keep_open = microphone, True
    try:
        result = client.stream_turn([], tools=[])
        assert microphone.text == "Hello Echo" and result.content == "Hello"
        assert result.audio[0]["kind"] == "pcm" and not socket.closed
        assert any("realtimeInput" in packet for packet in socket.sent)
        assert not any("clientContent" in packet for packet in socket.sent)
        client._prefetched_turn, client._prefetched_schema = result, schema_fingerprint([])
        text, audio = [], []
        client.on_audio = audio.append
        assert client.stream_turn([{"role": "user", "content": "Hello Echo"}], on_content=text.append) is result
        assert text == ["Hello"] and audio and socket.closed
    finally:
        client.close()


def test_resumption_does_not_replay_user_turn(monkeypatch):
    first = Socket([{"setupComplete": {}}, {"sessionResumptionUpdate": {"resumable": True, "newHandle": "resume-test"}}, {"goAway": {"timeLeft": "1s"}}])
    second = Socket([{"setupComplete": {}}, {"serverContent": {"outputTranscription": {"text": "Resumed"}, "turnComplete": True}}])
    sockets = iter([first, second])
    monkeypatch.setattr("websockets.sync.client.connect", lambda *args, **kwargs: next(sockets))
    client = ChatClient(Endpoint(cloud.BASE_URLS["gemini"], "test", "gemini-test-live", "gemini", False))
    try:
        assert client.stream_turn([{"role": "user", "content": "Hello"}]).content == "Resumed"
        assert second.sent[0]["setup"]["sessionResumption"]["handle"] == "resume-test"
        assert not any("clientContent" in packet for packet in second.sent)
    finally:
        client.close()


def test_prefetched_tools_cannot_be_substituted():
    from agent.live_voice import schema_fingerprint
    client = ChatClient(Endpoint(cloud.BASE_URLS["gemini"], "test", "gemini-test-live", "gemini", False))
    client._prefetched_turn, client._prefetched_schema = ModelTurn(content="wrong scope"), schema_fingerprint([])
    try:
        with pytest.raises(ProviderError, match="tools changed"):
            client.stream_turn([], tools=[{"type": "function", "function": {"name": "new_tool"}}])
    finally:
        client.close()


def test_captured_tool_call_waits_for_the_ordinary_governed_loop(tmp_path, monkeypatch):
    from agent import live_voice
    from agent.lean.loop import LeanTurn
    from agent.lean.personas import AgentPersona
    from agent.lean.toolbox import Toolbox, NativeTool
    from agent.voice_transport import VoiceTransportStore
    calls = []
    persona = AgentPersona(id="echo", name="Echo")
    box = Toolbox(toolsets=["none"], session_id="t")
    box.native = {"lookup": NativeTool(name="lookup", description="lookup", parameters={"type": "object", "properties": {}}, func=lambda args: calls.append(1) or "42")}
    client = ChatClient(Endpoint(cloud.BASE_URLS["gemini"], "test", "gemini-test-live", "gemini", False))
    socket = Socket([{"setupComplete": {}}, {"serverContent": {"inputTranscription": {"text": "Look up the number"}},
                     "toolCall": {"functionCalls": [{"id": "c1", "name": "lookup", "args": {}}]}},
                     {"serverContent": {"outputTranscription": {"text": "The number is 42."}, "turnComplete": True}}])
    monkeypatch.setattr("websockets.sync.client.connect", lambda *args, **kwargs: socket)
    class Session:
        def __init__(self, **kwargs): self.personas = SimpleNamespace(default=lambda: persona)
        def _members(self): return []
        def _history(self): return []
        def _build_turn(self, *args, **kwargs): return SimpleNamespace(client=client, toolbox=box, system_prompt="system")
    monkeypatch.setattr("agent.lean.runtime.LeanSession", Session)
    monkeypatch.setattr("agent.lean.rooms.get_room_store", lambda: SimpleNamespace(by_thread=lambda key: None))
    monkeypatch.setattr("agent.voice_transport.validate_voice_scope", lambda *args: (None, None))
    store = VoiceTransportStore(tmp_path)
    monkeypatch.setattr("agent.voice_transport.get_voice_transport_store", lambda: store)
    monkeypatch.setattr(live_voice, "scope_fingerprint", lambda *args: "unchanged")
    try:
        voice = live_voice.capture(object(), session_id="t", project_id="", client_turn_id="microphone-test", microphone=live_voice.LiveMicrophone(lambda event: None), cancel=threading.Event())
        assert calls == [] and not socket.closed
        assert live_voice.claim_client("other-chat", "r", client.endpoint, persona, []) is None
        live_voice.bind_request(voice.id, "t", "r")
        claimed = live_voice.claim_client("t", "r", client.endpoint, persona, [])
        assert claimed is client
        assert live_voice.claim_client("t", "r", client.endpoint, persona, []) is None
        turn = LeanTurn(client=claimed, persona=persona, system_prompt="system", history=[], toolbox=box,
                        session_id="t", request_id="r", execution_id="e", emit=lambda event: None,
                        cancel=threading.Event(), persist_tool_runs=False)
        result = turn.run(voice.transcript)
        assert result.success and "42" in result.text
        assert calls == [1]
        assert socket.sent[-1]["toolResponse"]["functionResponses"][0]["id"] == "c1"
    finally:
        live_voice.stop_pending_voice()
        client.close()


def test_live_websocket_rejects_an_untrusted_origin_before_capture(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    from api.routes import live_voice
    monkeypatch.setattr(live_voice, "_get_client_ip", lambda request: "127.0.0.1")
    monkeypatch.setattr("agent.live_voice.capture", lambda *args, **kwargs: pytest.fail("must reject before any provider request"))
    app = FastAPI(); app.include_router(live_voice.router)
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as error:
            with client.websocket_connect("/media-runtime/voice/live", headers={"host": "localhost", "origin": "https://untrusted.example"}):
                pass
    assert error.value.code == 1008


def test_updater_signature_detects_artifact_and_comment_tampering(tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    from cryptography.exceptions import InvalidSignature
    script = Path(__file__).resolve().parents[2] / "desktop/scripts/verify-updater-signature.py"
    spec = importlib.util.spec_from_file_location("updater_verifier", script)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    artifact = tmp_path / "installer.exe"; artifact.write_bytes(b"installer")
    key_id = b"12345678"
    b64 = lambda value: base64.b64encode(value).decode()
    key = b64(("untrusted comment: test\n" + b64(b"Ed" + key_id + public) + "\n").encode())
    signature = private.sign(hashlib.blake2b(artifact.read_bytes(), digest_size=64).digest())
    lines = ["untrusted comment: test", b64(b"ED" + key_id + signature), "trusted comment: timestamp:123", b64(private.sign(signature + b"timestamp:123"))]
    encoded = b64("\n".join(lines).encode())
    module.verify(artifact, encoded, key)
    artifact.write_bytes(b"tampered")
    with pytest.raises(InvalidSignature): module.verify(artifact, encoded, key)
    artifact.write_bytes(b"installer")
    lines[2] = "trusted comment: altered"
    with pytest.raises(InvalidSignature): module.verify(artifact, b64("\n".join(lines).encode()), key)

@pytest.mark.parametrize("returncode", [0, 1])
def test_cuda_probe_uses_selected_device_and_propagates_failure(tmp_path, monkeypatch, returncode):
    from agent import generation_setup as setup
    observed = []
    def run(command, **kwargs):
        observed.append((command, kwargs))
        return SimpleNamespace(returncode=returncode, stderr="CUDA incompatible")
    monkeypatch.setattr(setup.subprocess, "run", run)
    if returncode:
        with pytest.raises(RuntimeError, match="selected GPU"):
            setup.verify_cuda(tmp_path, {"id": "GPU-selected"})
    else:
        setup.verify_cuda(tmp_path, {"id": "GPU-selected"})
    command, kwargs = observed[0]
    assert kwargs["env"]["CUDA_VISIBLE_DEVICES"] == "GPU-selected"
    assert "torch.cuda.synchronize" in command[-1]
    assert kwargs["timeout"] == 45
