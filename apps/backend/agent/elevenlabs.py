"""ElevenLabs text-to-speech: a cloud voice for reading replies aloud.

Chosen in Settings › Voice › Read aloud. Each request sends the text being read
(one chunk of a reply, up to 1,200 characters) to ElevenLabs and costs characters
on your ElevenLabs plan. Private and Offline modes block it like any cloud
service. Audio comes back as raw 16-bit PCM at 22,050 Hz (available on every
plan; 44.1 kHz needs Pro) and is wrapped as WAV, so the rest of the voice
pipeline is the same as for Windows voices and Piper.

API: POST /v1/text-to-speech/{voice_id}?output_format=pcm_22050 and
GET /v2/voices, authenticated with the xi-api-key header.
"""
from __future__ import annotations

import wave
from pathlib import Path
from typing import Any

API = "https://api.elevenlabs.io"
SAMPLE_RATE = 22050
DEFAULT_VOICE = "JBFqnCBsd6RMkjVDRZzb"  # "George", a premade voice every account has
MODELS = {
    "eleven_flash_v2_5": "Flash v2.5: fastest, lowest cost",
    "eleven_turbo_v2_5": "Turbo v2.5: fast, higher quality",
    "eleven_multilingual_v2": "Multilingual v2: most natural, slower",
    "eleven_v3": "v3: most expressive",
}


class ElevenLabsError(RuntimeError):
    pass


def _settings() -> tuple[str, str, str, float]:
    from config import config
    key = str(getattr(config, "elevenlabs_api_key", "") or "").strip()
    voice = str(getattr(config, "elevenlabs_voice_id", "") or "").strip() or DEFAULT_VOICE
    model = str(getattr(config, "elevenlabs_model", "") or "").strip() or "eleven_flash_v2_5"
    try:
        speed = min(1.2, max(0.7, float(getattr(config, "elevenlabs_speed", 1.0) or 1.0)))
    except (TypeError, ValueError):
        speed = 1.0
    return key, voice, model, speed


def configured() -> bool:
    return bool(_settings()[0])


def _explain(status: int, body: str) -> str:
    """What went wrong, in words the owner can act on."""
    text = body.lower()
    if status == 401:
        return "ElevenLabs refused the API key. Check it in Settings › Voice."
    if status in {402, 429} or "quota" in text or "credits" in text:
        return "Your ElevenLabs plan is out of characters or busy. Check your usage at elevenlabs.io, then try again."
    if status == 404 or "voice_not_found" in text:
        return "ElevenLabs doesn't have that voice on your account. Pick another in Settings › Voice."
    if status == 422:
        return f"ElevenLabs rejected the request: {body[:200]}"
    return f"ElevenLabs answered with an error ({status})."


def synthesize(text: str, output: Path, options: dict[str, Any] | None = None) -> None:
    """Speak one chunk into a mono 16-bit WAV file."""
    import httpx
    from agent import privacy

    key, voice, model, speed = _settings()
    options = dict(options or {})
    voice = str(options.get("voice_id") or voice)
    if not key:
        raise ElevenLabsError("Add your ElevenLabs API key in Settings › Voice to use this voice.")
    url = f"{API}/v1/text-to-speech/{voice}"
    privacy.require("voice", url)  # the reply text leaves this PC only where Private mode allows
    try:
        response = httpx.post(url, params={"output_format": f"pcm_{SAMPLE_RATE}"}, timeout=60,
                              headers={"xi-api-key": key, "Content-Type": "application/json"},
                              json={"text": text, "model_id": str(options.get("model_id") or model),
                                    "voice_settings": {"stability": 0.5, "similarity_boost": 0.75, "speed": speed}})
    except httpx.HTTPError as exc:
        raise ElevenLabsError(f"Couldn't reach ElevenLabs: {type(exc).__name__}") from exc
    if response.status_code != 200:
        raise ElevenLabsError(_explain(response.status_code, response.text))
    audio = response.content
    if len(audio) < 2 or "json" in response.headers.get("content-type", ""):
        raise ElevenLabsError("ElevenLabs returned no audio.")
    output.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output), "wb") as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(SAMPLE_RATE)
        clip.writeframes(audio[: len(audio) - len(audio) % 2])


def voices() -> list[dict[str, Any]]:
    """The voices on this account (premade, yours and saved), for the picker in Settings."""
    import httpx
    from agent import privacy

    key = _settings()[0]
    if not key:
        raise ElevenLabsError("Add your ElevenLabs API key first.")
    url = f"{API}/v2/voices"
    privacy.require("voice", url)
    found: list[dict[str, Any]] = []
    token = ""
    for _ in range(5):  # up to 500 voices
        params: dict[str, Any] = {"page_size": 100, "include_total_count": "false"}
        if token:
            params["next_page_token"] = token
        response = httpx.get(url, params=params, headers={"xi-api-key": key}, timeout=20)
        if response.status_code != 200:
            raise ElevenLabsError(_explain(response.status_code, response.text))
        data = response.json()
        for voice in data.get("voices") or []:
            labels = voice.get("labels") or {}
            found.append({"voice_id": str(voice.get("voice_id") or ""), "name": str(voice.get("name") or "Voice"),
                          "category": str(voice.get("category") or ""),
                          "description": ", ".join(str(v) for v in labels.values() if v)[:120]})
        token = str(data.get("next_page_token") or "")
        if not data.get("has_more") or not token:
            break
    return [v for v in found if v["voice_id"]]
