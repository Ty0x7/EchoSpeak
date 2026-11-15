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

- ``served(endpoint)``: the loaded model's real window (Ollama ``/api/ps`` → ``context_length``),
  cached briefly. ``settings.context_tokens`` takes the smaller of this and what is configured.
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
    return base, str(getattr(endpoint, "model", "") or "")


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
        if str(getattr(endpoint, "provider", "")) == "ollama" and _loopback(key[0]) and key[1]:
            try:
                value = _fetch_ollama(*key)
            except (OSError, ValueError):
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
