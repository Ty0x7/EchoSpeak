"""Hand the GPU to the local model being called, when two local runtimes share one card.

On an 8 GB card a llama-server model (Ternary Bonsai 2.5 27B, ~6.5 GB) and an Ollama model (qwen3.5:9b, ~6 GB) can't
both be resident. llama-server started with ``--sleep-idle-seconds`` frees its VRAM when idle and reloads on the next
request (measured 27 Nov 2026: 7.7 GB -> 1.4 GB, ~3 s to wake), but a model Ollama still holds (keep_alive, 5 min by
default) leaves the waking server too little memory, and the request fails.

Both directions, immediately (``before_call``):
- calling an OpenAI-compatible server on this machine (LocalAI/vLLM/LM Studio providers, how llama-server is reached):
  ask the local Ollama to unload its models (``keep_alive: 0``). A llama-server woken without room aborts
  ("failed to reload model after sleeping"), so this is required, not tidy;
- calling Ollama: ask each llama-server in router mode on this machine (``LOCAL_GPU_PEERS``, default
  ``http://127.0.0.1:8095``) to unload its loaded models (``POST /models/unload``). It reloads on its next request.

Callers run this inside the local inference gate (agent/admission.py, one local call at a time), so no EchoSpeak
generation is in flight on either runtime while the other is unloaded. Loopback only, throttled per target, best
effort: a failure leaves things as they were. ``LOCAL_GPU_HANDOFF=0`` turns it off.
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


def _peers() -> list[str]:
    raw = os.environ.get("LOCAL_GPU_PEERS", "http://127.0.0.1:8095")
    return [p.strip().rstrip("/") for p in raw.split(",") if p.strip() and _loopback(p.strip())]


def _throttled(key: str) -> bool:
    now = time.monotonic()
    with _lock:
        if now - _last.get(key, -THROTTLE_SECONDS) < THROTTLE_SECONDS:
            return True
        _last[key] = now
    return False


def _forget(prefix: str) -> None:
    with _lock:
        for key in [k for k in _last if k.startswith(prefix)]:
            _last.pop(key, None)


def _unload_router_peers(base: str) -> list[str]:
    """Ask llama-server routers on loopback to unload their loaded models (not the server being called)."""
    own = urllib.parse.urlsplit(base).netloc
    unloaded = []
    for peer in _peers():
        if urllib.parse.urlsplit(peer).netloc == own or _throttled("peer:" + peer):
            continue
        try:
            with urllib.request.urlopen(peer + "/models", timeout=0.5) as reply:
                rows = json.load(reply).get("data") or []
        except (OSError, ValueError, AttributeError):
            continue
        for row in rows:
            status = row.get("status") if isinstance(row, dict) else None
            if not isinstance(status, dict) or status.get("value") != "loaded":
                continue  # not a router, or nothing loaded
            request = urllib.request.Request(peer + "/models/unload", method="POST",
                                             data=json.dumps({"model": row.get("id")}).encode(),
                                             headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(request, timeout=15):
                    unloaded.append(f"{peer}:{row.get('id')}")
            except (OSError, ValueError):
                continue
    return unloaded


def before_call(endpoint: Any) -> list[str]:
    """Free the GPU for the local runtime about to be called. Returns what was unloaded."""
    provider = str(getattr(endpoint, "provider", "") or "")
    base = str(getattr(endpoint, "base_url", "") or "")
    if not enabled() or not getattr(endpoint, "local", False) or not _loopback(base):
        return []
    if provider == "ollama":
        _forget("ollama:")  # a later call to llama-server must check Ollama again, however soon
        return _unload_router_peers(base.removesuffix("/v1").rstrip("/"))
    if provider not in OPENAI_COMPATIBLE:
        return []
    _forget("peer:")
    ollama = _ollama_url()
    if not _loopback(ollama) or urllib.parse.urlsplit(ollama).netloc == urllib.parse.urlsplit(base).netloc:
        return []
    if _throttled("ollama:" + ollama):
        return []
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
