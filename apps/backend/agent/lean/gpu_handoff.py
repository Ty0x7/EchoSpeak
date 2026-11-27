"""Hand the GPU to the local model being called, when two local runtimes share one card.

On an 8 GB card a llama-server model (Ternary Bonsai 2.5 27B, ~6.5 GB) and an Ollama model (qwen3.5:9b, ~6 GB) can't
both be resident. llama-server started with ``--sleep-idle-seconds`` frees its VRAM when idle and reloads on the next
request (measured 27 Nov 2026: 7.7 GB -> 1.4 GB, ~3 s to wake), but a model Ollama still holds (keep_alive, 5 min by
default) leaves the waking server too little memory, and the request fails.

Before a call to an OpenAI-compatible local server on this machine (LocalAI/vLLM/LM Studio providers, which is how
llama-server is reached), ``before_call`` asks the local Ollama service to unload what it has loaded. Ollama reloads
on its own next request, and the sleeping llama-server makes room the other way. Loopback only, throttled, and best
effort: any failure leaves things as they were. ``LOCAL_GPU_HANDOFF=0`` turns it off.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.parse
import urllib.request
from typing import Any

OPENAI_COMPATIBLE = {"localai", "vllm", "lmstudio"}
THROTTLE_SECONDS = 20.0
_lock = threading.Lock()
_last: dict[str, float] = {}


def _loopback(url: str) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"} or host.startswith("127.")


def _ollama_url() -> str:
    host = os.environ.get("OLLAMA_HOST", "").strip() or "127.0.0.1:11434"
    return host if "://" in host else f"http://{host}"


def enabled() -> bool:
    return os.environ.get("LOCAL_GPU_HANDOFF", "1").strip().lower() not in {"0", "false", "off", "no"}


def before_call(endpoint: Any) -> list[str]:
    """Unload Ollama's loaded models before calling another local runtime on loopback. Returns what was unloaded."""
    provider = str(getattr(endpoint, "provider", "") or "")
    base = str(getattr(endpoint, "base_url", "") or "")
    if not enabled() or not getattr(endpoint, "local", False) or provider not in OPENAI_COMPATIBLE or not _loopback(base):
        return []
    ollama = _ollama_url()
    if not _loopback(ollama) or urllib.parse.urlsplit(ollama).netloc == urllib.parse.urlsplit(base).netloc:
        return []
    now = time.monotonic()
    with _lock:
        if now - _last.get(ollama, -THROTTLE_SECONDS) < THROTTLE_SECONDS:
            return []
        _last[ollama] = now
    try:
        with urllib.request.urlopen(ollama.rstrip("/") + "/api/ps", timeout=0.5) as reply:
            loaded = [str(m.get("name") or m.get("model") or "") for m in (json.load(reply).get("models") or [])]
    except (OSError, ValueError, AttributeError):
        return []
    unloaded = []
    for name in filter(None, loaded):
        request = urllib.request.Request(ollama.rstrip("/") + "/api/generate", method="POST",
                                         data=json.dumps({"model": name, "keep_alive": 0}).encode(),
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=10):
                unloaded.append(name)
        except (OSError, ValueError):
            continue
    return unloaded


def reset() -> None:
    with _lock:
        _last.clear()
