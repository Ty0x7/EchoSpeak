"""Guided voice setup and the wake check (no model download needed here)."""

from __future__ import annotations

import base64
import io
import wave

import numpy as np

from agent import voice_setup


def _wav(seconds: float = 0.5, rate: int = 48000, channels: int = 1) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(rate)
        tone = (np.sin(np.linspace(0, 2 * np.pi * 440 * seconds, int(rate * seconds))) * 8000).astype("<i2")
        out.writeframes(np.repeat(tone, channels).tobytes())
    return buffer.getvalue()


def test_wake_word_matching_handles_common_mishearings():
    assert voice_setup.heard_wake_word("Hey Echo, what time is it?")
    assert voice_setup.heard_wake_word("hey ekko")
    assert voice_setup.heard_wake_word("OK eco.")
    assert not voice_setup.heard_wake_word("The weather is nice today.")
    # Only near the start: "echo" deep in a sentence is not a wake word.
    assert not voice_setup.heard_wake_word("I was reading about the history of sound and the echo")
    assert voice_setup.heard_wake_word("hey jarvis", wake_word="jarvis")


def test_wav_is_decoded_to_16k_mono_without_pyav(tmp_path):
    path = tmp_path / "clip.wav"
    path.write_bytes(_wav(seconds=0.5, rate=48000, channels=2))
    audio = voice_setup.load_wav_16k(path)
    assert audio.dtype == np.float32 and abs(len(audio) - 8000) <= 1
    assert float(np.abs(audio).max()) <= 1.0


def test_setup_status_lists_the_three_sizes(monkeypatch, tmp_path):
    monkeypatch.setattr(voice_setup, "MODELS_DIR", tmp_path)
    info = voice_setup.status()
    assert [m["size"] for m in info["models"]] == ["tiny", "base", "small"]
    assert not any(m["installed"] for m in info["models"])
    assert info["download"]["running"] is False


def test_wake_route_says_to_set_up_voice_first(monkeypatch):
    from fastapi.testclient import TestClient
    from fastapi import FastAPI

    from api.media_runtime import router
    from config import config

    monkeypatch.setattr(config, "voice_faster_whisper_model_path", "", raising=False)
    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).post("/media-runtime/voice/wake", json={"audio_base64": base64.b64encode(_wav()).decode()})
    assert response.status_code == 409 and "Settings > Voice" in response.json()["detail"]
