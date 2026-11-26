"""Kokoro-82M speech on the CPU through kokoro-onnx.

Kokoro is the local read-aloud voice for Linux and macOS, where Windows SAPI does not
exist. It runs on the CPU so speech never competes with the chat model for VRAM.
Measured on the reference workstation (16 threads): fp16 model RTF 0.22, int8 RTF 0.37.

The model and voices files are explicit paths. EchoSpeak never downloads them
implicitly; `scripts/setup_local_tts.py` fetches them when the owner asks.
"""

from __future__ import annotations

import importlib.util
import os
import threading
import wave
from pathlib import Path
from typing import Any, Optional

from config import DATA_DIR, config

DEFAULT_VOICE = "af_heart"
SAMPLE_RATE = 24000

_lock = threading.Lock()
_engine: Any = None
_engine_key: tuple[str, str] = ("", "")


class KokoroError(RuntimeError):
    pass


def default_model_dir() -> Path:
    return Path(os.path.expanduser("~/.local/share/echospeak/models/tts/kokoro"))


def _candidate(value: Any, names: tuple[str, ...]) -> Optional[Path]:
    text = str(value or "").strip()
    if text:
        path = Path(os.path.expanduser(text))
        return path if path.is_file() else None
    for name in names:
        path = default_model_dir() / name
        if path.is_file():
            return path
    return None


def model_path() -> Optional[Path]:
    return _candidate(getattr(config, "voice_kokoro_model_path", ""),
                      ("kokoro-v1.0.fp16.onnx", "kokoro-v1.0.int8.onnx", "kokoro-v1.0.onnx"))


def voices_path() -> Optional[Path]:
    return _candidate(getattr(config, "voice_kokoro_voices_path", ""), ("voices-v1.0.bin",))


def runtime_installed() -> bool:
    return importlib.util.find_spec("kokoro_onnx") is not None


def readiness() -> tuple[bool, bool, str]:
    """(detected, ready, detail) without loading the model."""

    installed = runtime_installed()
    model, voices = model_path(), voices_path()
    if installed and model and voices:
        return True, True, f"Ready on the CPU with {model.name}."
    if not installed:
        return False, False, "Kokoro is not installed. Run scripts/setup_local_tts.py kokoro."
    missing = "model" if not model else "voices file"
    return True, False, f"Kokoro is installed; the {missing} is missing. Run scripts/setup_local_tts.py kokoro."


def _load():
    global _engine, _engine_key
    model, voices = model_path(), voices_path()
    if not (runtime_installed() and model and voices):
        raise KokoroError(readiness()[2])
    key = (str(model), str(voices))
    if _engine is None or _engine_key != key:
        try:
            import onnxruntime as ort
            from kokoro_onnx import Kokoro

            # Leave cores for the chat model and the desktop.
            options = ort.SessionOptions()
            options.intra_op_num_threads = max(1, min(8, (os.cpu_count() or 4) // 2))
            options.log_severity_level = 3
            session = ort.InferenceSession(str(model), sess_options=options, providers=["CPUExecutionProvider"])
            _engine = Kokoro.from_session(session, str(voices))
        except Exception as exc:  # pragma: no cover - depends on the optional runtime
            raise KokoroError(f"Kokoro could not load ({type(exc).__name__}: {exc})") from exc
        _engine_key = key
    return _engine


def voices() -> list[str]:
    with _lock:
        return sorted(_load().get_voices())


def synthesize(text: str, output: Path, *, voice: str = "", speed: float = 1.0) -> float:
    """Write a 16-bit mono WAV; return its duration in seconds."""

    import numpy as np

    chosen = str(voice or getattr(config, "voice_kokoro_voice", "") or DEFAULT_VOICE).strip()
    rate = min(1.6, max(0.6, float(speed or 1.0)))
    with _lock:
        engine = _load()
        if chosen not in engine.get_voices():
            raise KokoroError(f"Kokoro has no voice named {chosen!r}")
        lang = "en-gb" if chosen.startswith("b") else "en-us"
        samples, sample_rate = engine.create(text, voice=chosen, speed=rate, lang=lang)
    pcm = (np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0) * 32767).astype("<i2")
    output.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(int(sample_rate))
        handle.writeframes(pcm.tobytes())
    return len(pcm) / float(sample_rate)
