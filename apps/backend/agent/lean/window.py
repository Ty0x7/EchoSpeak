"""The context window a local model server really serves, and prompts it cut without saying so.

A local server's window is fixed where the server starts (Ollama: ``OLLAMA_CONTEXT_LENGTH``), not by
EchoSpeak's settings, and Ollama's OpenAI-compatible endpoint ignores a per-request size. A prompt
longer than the window is not refused: llama.cpp keeps the first few tokens and the newest half of
the window and answers with HTTP 200 (only the server log says ``truncating input prompt``). The
system prompt, the safety rules and the user's request are the part that goes.

Measured on this project's own setup (Ollama 0.40.16, qwen3.5:9b, served window 16,384): EchoSpeak
assumed 32,768 (``LOCAL_MODEL_CONTEXT``'s default), so it only started trimming at about 22k
estimated tokens, and a 43,104-token prompt reached the model as 8,194 tokens with no error.

Two defences, both cheap:

- ``served(endpoint)``: the loaded model's real window (Ollama ``/api/ps`` → ``context_length``;
  LM Studio ``/api/v1/models`` → ``loaded_instances[].config.context_length``; llama-server behind the generic
  OpenAI-compatible providers ``/props`` → ``default_generation_settings.n_ctx``), cached briefly. ``settings.context_tokens`` takes the smaller of this and what is configured.
- ``truncated(...)``: after a call, the server's own prompt count compared with what was sent. A
  count far below the estimate (or exactly half the known window) means the prompt was cut. The
  loop then discards that reply, never runs its tool calls, trims harder and asks again; the
  apparent window is remembered so the next steps fit.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request
from typing import Any

POSITIVE_TTL = 60.0
NEGATIVE_TTL = 10.0
LEARNED_TTL = 600.0
# A cut prompt: the server counted under this share of what was sent (and the prompt wasn't small).
CUT_RATIO = 0.6
MIN_PROMPT = 2048

_lock = threading.Lock()
_served: dict[tuple[str, str], tuple[int, float]] = {}
_learned: dict[tuple[str, str], tuple[int, float]] = {}


def _key(endpoint: Any) -> tuple[str, str]:
    base = str(getattr(endpoint, "base_url", "") or "").rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    return base, str(getattr(endpoint, "served_name", "") or getattr(endpoint, "model", "") or "")


def _loopback(base: str) -> bool:
    host = (urllib.parse.urlsplit(base).hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"} or host.startswith("127.")


def _fetch_ollama(base: str, model: str) -> int:
    """The serving window of `model` if Ollama has it loaded (0 if not loaded or unknown)."""
    with urllib.request.urlopen(base + "/api/ps", timeout=1.0) as response:
        data = json.loads(response.read() or b"{}")
    for row in data.get("models") or []:
        names = {str(row.get("name") or ""), str(row.get("model") or "")}
        if model in names or f"{model}:latest" in names:
            return int(row.get("context_length") or 0)
    return 0


def _fetch_lmstudio(base: str, model: str) -> int:
    """The window LM Studio loaded `model` with (REST v1 ``/api/v1/models`` →
    ``loaded_instances[].config.context_length``), 0 if not loaded. LM Studio picks the window at
    load time (its default is often 4,096), and its OpenAI endpoint doesn't say. Unloaded models
    report only ``max_context_length``, the trained maximum, which is not what is served."""
    request = urllib.request.Request(base + "/api/v1/models", headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=1.0) as response:
        data = json.loads(response.read() or b"{}")
    wanted = model.casefold()
    for row in data.get("models") or []:
        instances = [i for i in row.get("loaded_instances") or [] if isinstance(i, dict)]
        names = {str(row.get("key") or "").casefold(), str(row.get("selected_variant") or "").casefold()}
        names |= {str(i.get("id") or "").casefold() for i in instances}
        if wanted in names:
            for instance in instances:
                value = int((instance.get("config") or {}).get("context_length") or 0)
                if value:
                    return value
    return 0


def _fetch_llama_server(base: str, model: str) -> int:
    """The per-slot window a llama.cpp ``llama-server`` was started with (``GET /props`` →
    ``default_generation_settings.n_ctx``), 0 if the server has no such route. EchoSpeak reaches llama-server through a
    generic OpenAI-compatible provider (LocalAI or vLLM in Settings); its window is ``-c`` divided across ``-np`` slots
    and fixed at start, and nothing on its OpenAI routes says what it is. A server without ``/props`` (LocalAI, vLLM)
    answers 404, which leaves the configured window in charge. ``model`` is unused: llama-server serves one model."""
    request = urllib.request.Request(base + "/props", headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=1.0) as response:
        data = json.loads(response.read() or b"{}")
    settings = data.get("default_generation_settings") if isinstance(data, dict) else None
    value = (settings or {}).get("n_ctx") if isinstance(settings, dict) else None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(data, dict) and data.get("role") == "router":
        # Router mode (--models-preset): /props describes the router (n_ctx 0), and /props?model= would LOAD the
        # model. The model list carries each child server's launch arguments without loading anything.
        with urllib.request.urlopen(urllib.request.Request(base + "/models"), timeout=1.0) as response:
            rows = (json.loads(response.read() or b"{}") or {}).get("data") or []
        for row in rows:
            if not isinstance(row, dict) or (model and row.get("id") != model and model not in (row.get("aliases") or [])):
                continue
            args = [str(a) for a in ((row.get("status") or {}).get("args") or [])]
            ctx = next((args[i + 1] for i in range(len(args) - 1) if args[i] in ("--ctx-size", "-c")), "")
            slots = next((args[i + 1] for i in range(len(args) - 1) if args[i] in ("--parallel", "-np")), "1")
            if ctx.isdigit() and int(ctx) > 0:
                return int(ctx) // max(1, int(slots) if slots.isdigit() else 1)
    return 0


def served(endpoint: Any) -> int:
    """The context window the local server is actually serving for this model, or 0 if unknown."""
    if endpoint is None or not getattr(endpoint, "local", False):
        return 0
    key = _key(endpoint)
    now = time.monotonic()
    with _lock:
        cached = _served.get(key)
        learned = _learned.get(key)
    if cached is None or cached[1] < now:
        value = 0
        fetch = {"ollama": _fetch_ollama, "lmstudio": _fetch_lmstudio, "localai": _fetch_llama_server,
                 "vllm": _fetch_llama_server}.get(str(getattr(endpoint, "provider", "")))
        if fetch and _loopback(key[0]) and key[1]:
            try:
                value = fetch(*key)
            except (OSError, ValueError, TypeError):
                value = 0
        cached = (value, now + (POSITIVE_TTL if value else NEGATIVE_TTL))
        with _lock:
            _served[key] = cached
    if cached[0]:
        return cached[0]
    return learned[0] if learned and learned[1] >= now else 0


def learn(endpoint: Any, window: int) -> None:
    """Remember a window inferred from a cut prompt, until the server reports one itself."""
    if window <= 0:
        return
    with _lock:
        _learned[_key(endpoint)] = (int(window), time.monotonic() + LEARNED_TTL)


def truncated(endpoint: Any, estimated: int, reported: int) -> int:
    """If the server cut this prompt, the window it seems to have (else 0).

    `estimated` is EchoSpeak's estimate of what it sent; `reported` the server's prompt count.
    llama.cpp (Ollama, LM Studio, llama-server) keeps about half its window when it cuts.
    """
    if endpoint is None or not getattr(endpoint, "local", False) or reported <= 0 or estimated < MIN_PROMPT:
        return 0
    window = served(endpoint)
    if window:
        # Only a prompt that could have overflowed can have been cut. The estimate runs at most ~1.3x low
        # (digit-heavy text), so under 3/4 of the window nothing was. Without this, a template that renders
        # less than EchoSpeak sends (deepseek-r1:8b drops the tool schemas: 2,699 counted of ~4.6k sent)
        # looked like a cut, and three good replies were thrown away.
        if estimated < 0.75 * window:
            return 0
        if abs(reported - window // 2) <= 16 or reported < CUT_RATIO * estimated:
            return window
        return 0
    if reported < CUT_RATIO * estimated:
        return 2 * reported
    return 0


def reset() -> None:
    """Forget cached and learned windows (tests, or after the user changes models)."""
    with _lock:
        _served.clear()
        _learned.clear()
