"""Tools for models whose server never shows them the tool definitions.

The OpenAI-compatible `tools` field only reaches a model if its chat template renders it. Ollama's
deepseek-r1:8b template doesn't (it formats past calls and results, but never the definitions), so
the model got a system prompt about tools it could not see, and answered "The script printed
Total: 4,285.75" without running anything (harness evaluation, 15 Nov 2026).

Detected from evidence, for any model: the server's own prompt count after a call with tools. If it
matches the messages alone, the definitions weren't rendered. From then on, that endpoint's calls
carry a compact catalog and one calling convention in the system message, and the loop's existing
text-call parser (provider.extract_text_tool_calls) reads `<tool_call>{...}</tool_call>`.
"""
from __future__ import annotations

import threading
from typing import Any, Optional

# Tool definitions must add at least this many estimated tokens to be measurable at all.
MIN_TOOL_TOKENS = 300
# Missing when the server counted less than the messages plus this share of the definitions. A rendered
# set adds all of it (the estimate runs 0.85-1.3x the server's count, so a rendered prompt lands far above).
RENDERED_SHARE = 0.35

_lock = threading.Lock()
_missing: dict[tuple[str, str], bool] = {}


def _key(endpoint: Any) -> tuple[str, str]:
    return str(getattr(endpoint, "base_url", "") or "").rstrip("/"), str(getattr(endpoint, "model", "") or "")


def needed(endpoint: Any) -> bool:
    """This endpoint's template drops tool definitions, so they go in the prompt."""
    with _lock:
        return bool(_missing.get(_key(endpoint)))


def known(endpoint: Any) -> bool:
    with _lock:
        return _key(endpoint) in _missing


def observe(endpoint: Any, with_tools: int, without_tools: int, reported: int) -> bool:
    """Record whether this call's definitions were rendered. True when they newly turn out to be missing.

    `with_tools` / `without_tools` are EchoSpeak's estimates of the prompt with and without them;
    `reported` is the server's own prompt count for the call.
    """
    if endpoint is None or reported <= 0 or known(endpoint):
        return False
    tool_tokens = with_tools - without_tools
    if tool_tokens < MIN_TOOL_TOKENS:
        return False
    missing = reported < without_tools + RENDERED_SHARE * tool_tokens
    with _lock:
        _missing[_key(endpoint)] = missing
    return missing


def _signature(schema: dict[str, Any]) -> str:
    fn = schema.get("function") or {}
    params = fn.get("parameters") or {}
    required = set(params.get("required") or [])
    names = [f"{name}{'*' if name in required else ''}" for name in (params.get("properties") or {})]
    description = " ".join(str(fn.get("description") or "").split())
    first = description.split(". ")[0][:180].rstrip(".")
    return f"- {fn.get('name')}({', '.join(names)}): {first}."


def catalog(tools: list[dict[str, Any]]) -> str:
    """The tools and how to call them, as text (required arguments marked *)."""
    lines = [
        "## Calling tools",
        "Your tools are listed here because this model doesn't receive them any other way. To use one, write a block "
        "exactly like this, with JSON arguments, and then stop writing:",
        '<tool_call>{"name": "file_read", "arguments": {"path": "notes.md"}}</tool_call>',
        "The result comes back in the next message. Use one block per call. Never write a tool's result yourself, and "
        "never say you ran, read or changed something unless a result in this conversation shows it.",
        "",
        "Tools (arguments marked * are required):",
    ]
    lines += [_signature(schema) for schema in tools]
    return "\n".join(lines)


def with_catalog(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A copy of the messages whose system message ends with the catalog (the stored history is untouched)."""
    if not tools or not messages or messages[0].get("role") != "system":
        return messages
    first = dict(messages[0])
    first["content"] = f"{first.get('content') or ''}\n\n{catalog(tools)}"
    return [first, *messages[1:]]


def reset() -> None:
    with _lock:
        _missing.clear()


def describe(endpoint: Any) -> Optional[str]:
    """For traces and settings: how this endpoint receives tools, once known."""
    if not known(endpoint):
        return None
    return "prompted" if needed(endpoint) else "native"


__all__ = ["needed", "known", "observe", "catalog", "with_catalog", "reset", "describe"]
