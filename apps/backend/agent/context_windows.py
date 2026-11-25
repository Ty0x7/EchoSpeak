"""Context windows the owner can choose, actually served (campaign 24 November 2026, workstream 1).

A local Ollama server fixes every model's window when it starts (``OLLAMA_CONTEXT_LENGTH``), and its
OpenAI-compatible endpoint, which EchoSpeak uses, ignores a per-request size. Measured on Ollama 0.40.16:
``options.num_ctx`` on ``/v1`` was served at 16,384; the native API honoured 32,768, but the next ``/v1``
call reloaded the model at 16,384 again. A model *variant* whose Modelfile sets ``num_ctx`` is served at
its own window through ``/v1`` (32,768 and 65,536 measured), shares the base model's weights (no disk
cost) and keeps its sampling parameters.

So a chosen window becomes a variant, ``echospeak/<model>:<N>k``, created once through ``/api/create``
and sent as the request's model (``Endpoint.wire``). EchoSpeak still names, looks up and displays the
base model. ``window.served`` then reads what the server really loaded, so a window that wasn't served
is reported as what it is, never as the one chosen.

- 32K is the standard default; 64K is for coding and long agent work; 16K stays for small GPUs and
  older setups. Larger windows are offered only up to the model's trained maximum.
- Other providers keep their own windows: LM Studio picks the window at load time (window.served
  reads it), and cloud models use their published limits.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_TOKENS = 32768
# (tokens, label, who it is for)
CHOICES = (
    (16384, "16K", "smaller GPUs and older setups"),
    (32768, "32K", "the standard default"),
    (65536, "64K", "coding and long agent work"),
    (131072, "128K", "very long work, if the model and memory allow"),
)
VARIANT_PREFIX = "echospeak/"
_lock = threading.Lock()
_made: dict[tuple[str, str, int], str] = {}
_trained: dict[tuple[str, str], int] = {}


def _base(url: str) -> str:
    base = str(url or "").rstrip("/")
    return base[:-3] if base.endswith("/v1") else base


def _post(url: str, body: dict[str, Any], timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"},
                                     method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read() or b"{}")


def variant_name(model: str, tokens: int) -> str:
    """``qwen3.5:9b`` at 32,768 → ``echospeak/qwen3.5-9b:32k`` (Ollama names: one tag, no second colon)."""
    stem = re.sub(r"[^a-z0-9._-]+", "-", str(model).lower().removesuffix(":latest")).strip("-")
    return f"{VARIANT_PREFIX}{stem}:{int(tokens) // 1024}k"


def is_variant(model: str) -> bool:
    return str(model or "").startswith(VARIANT_PREFIX)


def trained_max(base_url: str, model: str) -> int:
    """The window the model was trained for (``/api/show`` model_info ``*.context_length``), 0 if unknown."""
    key = (_base(base_url), model)
    if key in _trained:
        return _trained[key]
    value = 0
    try:
        info = _post(_base(base_url) + "/api/show", {"model": model}, 5.0).get("model_info") or {}
        value = max((int(v) for k, v in info.items() if str(k).endswith(".context_length") and str(v).isdigit()),
                    default=0)
    except Exception:  # noqa: BLE001 - unknown is fine; the choice list just isn't capped
        logger.debug("Could not read %s's trained window", model, exc_info=True)
    _trained[key] = value
    return value


def choices(base_url: str, model: str) -> list[dict[str, Any]]:
    """The windows to offer for this model: the standard ones, those above its trained maximum left out."""
    top = trained_max(base_url, model) if base_url else 0
    rows = []
    for tokens, label, note in CHOICES:
        if top and tokens > top:
            continue
        rows.append({"tokens": tokens, "label": label, "note": note, "default": tokens == DEFAULT_TOKENS})
    return rows


def served_model(base_url: str, model: str, tokens: int) -> str:
    """The Ollama model name that serves ``model`` at ``tokens``: its variant, created on first use.

    Returns "" when the variant can't be made (old server, no such model); the request then goes to the
    base model and window.served reports the window it really gets.
    """
    if not model or tokens <= 0 or is_variant(model):
        return ""
    base = _base(base_url)
    top = trained_max(base_url, model)
    if top and tokens > top:
        tokens = top   # never ask the server for more than the model was trained on; the served window says what it got
    key = (base, model, int(tokens))
    with _lock:
        if key in _made:
            return _made[key]
        name = variant_name(model, tokens)
        try:
            reply = _post(base + "/api/create", {"model": name, "from": model, "parameters": {"num_ctx": int(tokens)},
                                                 "stream": False}, 60.0)
            if str(reply.get("status") or "") != "success":
                raise RuntimeError(str(reply.get("error") or reply)[:200])
        except Exception:  # noqa: BLE001
            logger.warning("Could not prepare %s at a %s-token window; using the server's default window", model,
                           tokens, exc_info=True)
            _made[key] = ""
            return ""
        _made[key] = name
        return name


def effective(base_url: str, model: str, tokens: int) -> int:
    """The window a request for ``tokens`` will really ask for: capped at the model's trained window."""
    top = trained_max(base_url, model) if base_url and model else 0
    return min(int(tokens), top) if top and tokens > 0 else int(tokens)


def loaded(base_url: str, model: str) -> list[dict[str, Any]]:
    """What the server has loaded for ``model`` right now: the model itself or any of its variants, with windows."""
    try:
        with urllib.request.urlopen(_base(base_url) + "/api/ps", timeout=2.0) as response:
            rows = json.loads(response.read() or b"{}").get("models") or []
    except Exception:  # noqa: BLE001
        return []
    stem = variant_name(model, 1024).rsplit(":", 1)[0]
    out = []
    for row in rows:
        name = str(row.get("name") or row.get("model") or "")
        if name in {model, f"{model}:latest"} or name.startswith(stem + ":"):
            size, vram = int(row.get("size") or 0), int(row.get("size_vram") or 0)
            out.append({"name": name, "context": int(row.get("context_length") or 0),
                        "gpu_share": round(vram / size, 2) if size else None})
    return out


def forget() -> None:
    """Drop cached variants and trained windows (tests; a server that was reinstalled)."""
    with _lock:
        _made.clear()
        _trained.clear()
