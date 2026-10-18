"""ElevenLabs read-aloud: the request it sends, the WAV it makes, its errors in plain words, and privacy."""
import io
import wave

import httpx
import pytest

from agent import elevenlabs, privacy, voice_runtime
from config import config

PCM = b"\x01\x00\xff\xff" * 11025  # half a second of 16-bit mono at 22,050 Hz


class Reply:
    def __init__(self, status=200, content=PCM, json_body=None, content_type="audio/pcm"):
        self.status_code, self.content, self._json = status, content, json_body
        self.headers = {"content-type": content_type}
        self.text = content.decode("latin-1") if isinstance(content, bytes) else str(content)

    def json(self):
        return self._json


@pytest.fixture
def account(monkeypatch):
    monkeypatch.setattr(config, "elevenlabs_api_key", "el-test-key", raising=False)
    monkeypatch.setattr(config, "elevenlabs_voice_id", "voice-123", raising=False)
    monkeypatch.setattr(config, "elevenlabs_model", "eleven_flash_v2_5", raising=False)
    monkeypatch.setattr(config, "elevenlabs_speed", 1.1, raising=False)
    monkeypatch.setattr(config, "voice_local_tts_provider", "elevenlabs", raising=False)
    monkeypatch.setattr(privacy, "mode", lambda: "standard")
    sent = []
    monkeypatch.setattr(httpx, "post", lambda url, **kw: sent.append((url, kw)) or Reply())
    return sent


def test_a_reply_chunk_becomes_a_wav_clip(account, tmp_path):
    out = tmp_path / "clip.wav"
    assert voice_runtime.synthesize_voice_audio("Hello from Echo.", out) == "elevenlabs"
    url, request = account[0]
    assert url == "https://api.elevenlabs.io/v1/text-to-speech/voice-123"
    assert request["params"] == {"output_format": "pcm_22050"} and request["headers"]["xi-api-key"] == "el-test-key"
    assert request["json"]["text"] == "Hello from Echo." and request["json"]["model_id"] == "eleven_flash_v2_5"
    assert request["json"]["voice_settings"]["speed"] == 1.1
    with wave.open(str(out)) as clip:
        assert (clip.getnchannels(), clip.getsampwidth(), clip.getframerate()) == (1, 2, 22050)
        assert clip.getnframes() == len(PCM) // 2


@pytest.mark.parametrize("status, words", [(401, "refused the API key"), (429, "out of characters"),
                                           (404, "doesn't have that voice")])
def test_errors_say_what_to_fix(account, monkeypatch, tmp_path, status, words):
    monkeypatch.setattr(httpx, "post", lambda url, **kw: Reply(status, b'{"detail": "x"}', content_type="application/json"))
    with pytest.raises(voice_runtime.VoiceRuntimeError, match=words):
        voice_runtime.synthesize_voice_audio("Hi", tmp_path / "x.wav")


def test_the_message_reaches_the_chat_instead_of_a_generic_one():
    from agent.voice_transport import _bounded_failure
    _, message = _bounded_failure("synthesis", voice_runtime.VoiceRuntimeError("ElevenLabs refused the API key. Check it in Settings › Voice."))
    assert message.startswith("ElevenLabs refused the API key")
    _, generic = _bounded_failure("synthesis", RuntimeError("socket closed with token=abc"))
    assert "token" not in generic  # anything else stays generic


def test_private_mode_keeps_reply_text_home(account, monkeypatch, tmp_path):
    monkeypatch.setattr(privacy, "mode", lambda: "private")
    status = next(p for p in voice_runtime.voice_provider_statuses() if p.id == "elevenlabs")
    assert not status.execution_ready and status.detail
    with pytest.raises(voice_runtime.VoiceRuntimeError):
        voice_runtime.synthesize_voice_audio("Hi", tmp_path / "x.wav")
    assert account == []  # nothing was sent


def test_without_a_key_it_says_where_to_add_one(monkeypatch):
    monkeypatch.setattr(config, "elevenlabs_api_key", "", raising=False)
    status = next(p for p in voice_runtime.voice_provider_statuses() if p.id == "elevenlabs")
    assert not status.execution_ready and "API key" in status.detail


def test_voice_picker_reads_every_page(account, monkeypatch):
    pages = [{"voices": [{"voice_id": "a", "name": "Aria", "category": "premade", "labels": {"accent": "american"}}],
              "has_more": True, "next_page_token": "p2"},
             {"voices": [{"voice_id": "b", "name": "Brian", "category": "premade", "labels": {}}], "has_more": False}]
    asked = []
    monkeypatch.setattr(httpx, "get", lambda url, **kw: asked.append(kw["params"]) or Reply(json_body=pages.pop(0)))
    found = elevenlabs.voices()
    assert [v["name"] for v in found] == ["Aria", "Brian"] and found[0]["description"] == "american"
    assert asked[1]["next_page_token"] == "p2"


def test_test_voice_button_returns_a_playable_sample(account, monkeypatch):
    from fastapi.testclient import TestClient
    import api.server as server
    monkeypatch.setattr(server, "_api_auth_ok", lambda headers, ip: True)  # the app's own window, as on 127.0.0.1
    app = server.app
    response = TestClient(app).post("/media-runtime/voice/preview", json={"provider_id": "elevenlabs"})
    assert response.status_code == 200 and response.headers["content-type"] == "audio/wav"
    with wave.open(io.BytesIO(response.content)) as clip:
        assert clip.getframerate() == 22050
