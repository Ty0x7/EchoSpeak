"""Guided local speech-to-text setup and the "Hey Echo" wake check.

Setup downloads a faster-whisper model into the data folder and switches
speech input to it, so the microphone works without manual configuration.
The wake check transcribes a short burst of speech with the same local model
and looks for the wake word; nothing leaves the PC.
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from config import DATA_DIR

# Sizes offered in Settings. Repos are the CTranslate2 conversions faster-whisper uses.
MODELS: dict[str, dict[str, Any]] = {
    "tiny": {"repo": "Systran/faster-whisper-tiny", "label": "Tiny", "mb": 75, "note": "Fastest, least accurate"},
    "base": {"repo": "Systran/faster-whisper-base", "label": "Base", "mb": 145, "note": "Recommended"},
    "small": {"repo": "Systran/faster-whisper-small", "label": "Small", "mb": 480, "note": "Most accurate, slower"},
}
MODELS_DIR = Path(DATA_DIR) / "voice" / "models"

_LOCK = threading.Lock()
_STATE: dict[str, Any] = {"running": False, "size": "", "error": "", "started_at": 0.0}
_MODEL_CACHE: dict[str, Any] = {}


def model_dir(size: str) -> Path:
    return MODELS_DIR / f"faster-whisper-{size}"


def _installed(size: str) -> bool:
    folder = model_dir(size)
    return (folder / "model.bin").is_file() and (folder / "config.json").is_file()


def _folder_mb(folder: Path) -> float:
    if not folder.exists():
        return 0.0
    return sum(p.stat().st_size for p in folder.rglob("*") if p.is_file()) / 1_048_576


def runtime_available() -> bool:
    import importlib.util

    return importlib.util.find_spec("faster_whisper") is not None


def status() -> dict[str, Any]:
    from config import config

    active = str(getattr(config, "voice_faster_whisper_model_path", "") or "")
    with _LOCK:
        state = dict(_STATE)
    progress = 0.0
    if state["running"] and state["size"] in MODELS:
        progress = min(0.99, _folder_mb(model_dir(state["size"])) / float(MODELS[state["size"]]["mb"]))
    return {
        "runtime_available": runtime_available(),
        "provider": str(getattr(config, "voice_local_stt_provider", "") or ""),
        "active_model_path": active,
        "models": [
            {"size": size, **{k: v for k, v in info.items() if k != "repo"}, "installed": _installed(size),
             "active": bool(active) and Path(active).resolve() == model_dir(size).resolve()}
            for size, info in MODELS.items()
        ],
        "download": {"running": state["running"], "size": state["size"], "progress": round(progress, 3), "error": state["error"]},
    }


def _activate(size: str) -> None:
    """Point speech input at the downloaded model and make it the provider."""
    from config import config, read_runtime_override_payload, write_runtime_override_payload

    payload = read_runtime_override_payload(include_secrets=False, migrate_legacy=False)
    payload["voice_faster_whisper_model_path"] = str(model_dir(size))
    payload["voice_local_stt_provider"] = "faster-whisper-local"
    write_runtime_override_payload(payload)
    config.apply_overrides({
        "voice_faster_whisper_model_path": str(model_dir(size)),
        "voice_local_stt_provider": "faster-whisper-local",
    })
    _MODEL_CACHE.clear()


def start_setup(size: str) -> dict[str, Any]:
    size = str(size or "base").strip().lower()
    if size not in MODELS:
        raise ValueError(f"Unknown model size: {size}")
    if not runtime_available():
        raise RuntimeError("The speech engine (faster-whisper) is not included in this build.")
    if _installed(size):
        _activate(size)
        return status()
    with _LOCK:
        if _STATE["running"]:
            return status()
        _STATE.update(running=True, size=size, error="", started_at=time.time())

    def work() -> None:
        try:
            from huggingface_hub import snapshot_download

            target = model_dir(size)
            target.mkdir(parents=True, exist_ok=True)
            snapshot_download(repo_id=MODELS[size]["repo"], local_dir=str(target))
            if not _installed(size):
                raise RuntimeError("The download finished but the model files are missing.")
            _activate(size)
            logger.info("Voice model {} ready at {}", size, target)
            with _LOCK:
                _STATE.update(running=False, error="")
        except Exception as exc:
            logger.warning("Voice model download failed: {}", exc)
            with _LOCK:
                _STATE.update(running=False, error=f"Download failed: {exc}")

    threading.Thread(target=work, name="voice-model-download", daemon=True).start()
    return status()


def load_wav_16k(path: Path) -> Any:
    """Read a PCM WAV as 16 kHz mono float32 for faster-whisper.

    Decoding it here avoids faster-whisper's PyAV decoder, whose API breaks
    across PyAV versions; the app only ever sends WAV.
    """
    import wave

    import numpy as np

    with wave.open(str(path), "rb") as source:
        channels, width, rate = source.getnchannels(), source.getsampwidth(), source.getframerate()
        raw = source.readframes(source.getnframes())
    if width == 2:
        audio = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        audio = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    elif width == 1:
        audio = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:  # 24-bit
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        ints = (b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int32) << 16))
        ints = np.where(ints >= 1 << 23, ints - (1 << 24), ints)
        audio = ints.astype(np.float32) / 8388608.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    if rate != 16000 and audio.size:
        target = int(round(audio.size * 16000 / rate))
        audio = np.interp(np.linspace(0, audio.size - 1, target), np.arange(audio.size), audio).astype(np.float32)
    return audio


def whisper_model(model_path: str) -> Any:
    """Load a faster-whisper model once and reuse it (loading takes seconds)."""
    key = str(Path(model_path).resolve())
    model = _MODEL_CACHE.get(key)
    if model is None:
        from faster_whisper import WhisperModel

        model = WhisperModel(key, device="cpu", compute_type="int8")
        _MODEL_CACHE.clear()
        _MODEL_CACHE[key] = model
    return model


# Words Whisper commonly hears for "Echo".
_WAKE_ALIASES = {"echo", "echoes", "ecko", "ekko", "eko", "eco", "echos", "ecco"}


def heard_wake_word(text: str, wake_word: str = "echo") -> bool:
    words = re.findall(r"[a-z]+", str(text or "").lower())
    targets = {wake_word.lower()} | (_WAKE_ALIASES if wake_word.lower() == "echo" else set())
    return any(word in targets for word in words[:6])


def check_wake(audio_path: Path, wake_word: str = "echo") -> dict[str, Any]:
    """Transcribe a short clip quickly and report whether it contains the wake word."""
    from config import config

    path = str(getattr(config, "voice_faster_whisper_model_path", "") or "")
    if not runtime_available() or not path or not Path(path).exists():
        raise RuntimeError("Set up voice in Settings > Voice first.")
    model = whisper_model(path)
    segments, _info = model.transcribe(load_wav_16k(audio_path), language="en", beam_size=1, vad_filter=False, without_timestamps=True)
    text = " ".join(str(s.text or "").strip() for s in segments).strip()
    return {"wake": heard_wake_word(text, wake_word), "text": text}
