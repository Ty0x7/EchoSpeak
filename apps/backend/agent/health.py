"""System check: what works on this computer right now, in plain words.

Like `/doctor` in Claude Code: problems show up in Settings and as a notice when
the app starts, instead of the user finding out mid-chat (11.4.0 shipped without
web search and Echo only learned when a search failed). The agent also gets a
one-line note about broken capabilities, so it doesn't promise a search that
can't run.

Each check returns {id, label, status: ok|warn|fail, detail, fix}. Checks are
cheap (imports and settings, plus the model provider's own quick probe) and
cached for a minute.
"""

from __future__ import annotations

import importlib
import importlib.util
import threading
import time
from typing import Any, Callable

_CACHE_SECONDS = 60.0
_lock = threading.Lock()
_cache: tuple[float, list[dict[str, Any]]] | None = None

UPDATE_FIX = "Update EchoSpeak from Settings › About, or reinstall it."


def _check(id_: str, label: str, status: str, detail: str, fix: str = "") -> dict[str, Any]:
    return {"id": id_, "label": label, "status": status, "detail": detail, "fix": fix}


def _imports(*modules: str) -> str:
    """'' when every module imports, otherwise the first one that doesn't."""
    for name in modules:
        try:
            importlib.import_module(name)
        except Exception:
            return name
    return ""


def _installed(*modules: str) -> bool:
    return all(importlib.util.find_spec(name) is not None for name in modules)


def web_search(config: Any = None) -> dict[str, Any]:
    if config is None:
        from config import config  # noqa: PLW0127
    keyed = [label for label, value in (
        ("Brave", getattr(config, "brave_search_api_key", "")),
        ("Tavily", getattr(config, "tavily_api_key", "")),
        ("SearXNG", getattr(config, "searxng_base_url", "")),
    ) if str(value or "").strip()]
    missing = _imports("ddgs", "primp")
    if keyed:
        extra = "" if not missing else " The built-in DuckDuckGo fallback is missing, so there's no backup if it fails."
        return _check("web_search", "Web search", "ok" if not missing else "warn", f"Using {', '.join(keyed)}.{extra}",
                      "" if not missing else UPDATE_FIX)
    if not missing:
        return _check("web_search", "Web search", "ok", "Using DuckDuckGo (no key needed).")
    return _check("web_search", "Web search", "fail",
                  "Not working: the built-in search (DuckDuckGo) is missing from this install.",
                  "Update EchoSpeak, or add a Brave or Tavily key in Settings › Web search.")


def page_reading() -> dict[str, Any]:
    if _installed("trafilatura", "lxml"):
        return _check("page_reading", "Reading web pages", "ok", "Pages are read with the full article reader.")
    return _check("page_reading", "Reading web pages", "warn",
                  "Using a simpler reader, so long or busy pages may come out messy.", UPDATE_FIX)


def model() -> dict[str, Any]:
    from api.routes.settings import _check_provider_readiness, _resolve_runtime_provider

    provider = _resolve_runtime_provider()
    result = _check_provider_readiness(provider, timeout=1.0)
    name = str(getattr(provider, "value", provider))
    if result.get("ok"):
        return _check("model", "Model", "ok", f"{name} is ready.")
    return _check("model", "Model", "fail", str(result.get("detail") or f"{name} isn't reachable."),
                  "Check Settings › Models: the API key, or that LM Studio or Ollama is running.")


def voice() -> dict[str, Any]:
    if _installed("faster_whisper"):
        return _check("voice", "Speech to text", "ok", "Local speech recognition is available.")
    return _check("voice", "Speech to text", "warn", "Local speech recognition is missing; voice falls back to Windows speech.",
                  UPDATE_FIX)


def memory_search() -> dict[str, Any]:
    if _installed("onnxruntime", "tokenizers"):
        return _check("memory_search", "Memory search", "ok", "Memories and documents can be searched by meaning.")
    return _check("memory_search", "Memory search", "warn", "Searching memories by meaning is unavailable; exact matches still work.",
                  UPDATE_FIX)


CHECKS: list[Callable[[], dict[str, Any]]] = [web_search, page_reading, model, voice, memory_search]


def check_all(force: bool = False) -> list[dict[str, Any]]:
    global _cache
    with _lock:
        if not force and _cache and time.monotonic() - _cache[0] < _CACHE_SECONDS:
            return [dict(item) for item in _cache[1]]
    results = []
    for check in CHECKS:
        try:
            results.append(check())
        except Exception as exc:  # a broken check must not break the app
            name = getattr(check, "__name__", "check")
            results.append(_check(name, name.replace("_", " ").capitalize(), "warn", f"Couldn't check: {exc}"))
    with _lock:
        _cache = (time.monotonic(), results)
    return [dict(item) for item in results]


def prompt_note() -> str:
    """One line for the agent about broken research tools, or '' when they work."""
    try:
        problems = [item for item in (web_search(), page_reading()) if item["status"] != "ok"]
    except Exception:
        return ""
    if not problems:
        return ""
    parts = [f"{item['label']}: {item['detail']}" for item in problems]
    return ("Known problems on this computer right now: " + " ".join(parts) +
            " Don't promise what these can't do; use another route or tell the user, and suggest: "
            + problems[0]["fix"])
