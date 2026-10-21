"""Live progress from inside a running tool.

The loop opens a reporting scope around each tool call (``reporting``); the
tool, or code it calls, says what it is doing with ``report("Reading rtings.com")``.
The text reaches the chat as a ``tool_progress`` event under that tool's row.
Outside a scope ``report`` does nothing, so tools never need to know whether
anyone is listening. Reports are throttled so a chatty tool can't flood the stream.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Iterator, Optional

_sink: ContextVar[Optional[Callable[[str], None]]] = ContextVar("echospeak_tool_progress", default=None)
# The running turn's Stop, for tools that wait a long time (MCP tasks, agent/mcp_tasks.py).
_cancel: ContextVar[Optional[threading.Event]] = ContextVar("echospeak_tool_cancel", default=None)

# At most this many updates a second per tool; the latest one always wins at the end.
_MIN_GAP = 0.25


def report(text: str) -> None:
    """Say what the current tool is doing right now (one short line)."""
    sink = _sink.get()
    if sink is None:
        return
    line = " ".join(str(text or "").split())[:160]
    if line:
        sink(line)


_state_sink: ContextVar[Optional[Callable[[dict], None]]] = ContextVar("echospeak_tool_state", default=None)
# The running turn's trace spans (agent/lean/tracing.py), for tools that do traced work of their own.
_span_factory: ContextVar[Optional[Callable[..., Any]]] = ContextVar("echospeak_tool_spans", default=None)


def span_factory() -> Optional[Callable[..., Any]]:
    """``factory(name, **attrs)`` -> a span context manager on the turn's trace, or None."""
    return _span_factory.get()


def state_sink() -> Optional[Callable[[dict], None]]:
    """The live-card sink, captured to call from worker threads (contextvars don't follow threads)."""
    return _state_sink.get()


def report_state(state: dict) -> None:
    """Structured live state for a card under the tool's row (e.g. research workers). Latest wins."""
    sink = _state_sink.get()
    if sink is not None:
        sink(dict(state))


def stop_requested() -> bool:
    """The user stopped the turn this tool is running for."""
    event = _cancel.get()
    return bool(event is not None and event.is_set())


@contextmanager
def reporting(emit: Callable[[str], None], cancel: Optional[threading.Event] = None,
              emit_state: Optional[Callable[[dict], None]] = None,
              spans: Optional[Callable[..., Any]] = None) -> Iterator[None]:
    """Route ``report`` calls made in this context to ``emit``, throttled; expose the turn's Stop."""
    state = {"at": 0.0, "last": ""}
    state_token = _state_sink.set(emit_state)
    span_token = _span_factory.set(spans)

    def throttled(line: str) -> None:
        now = time.monotonic()
        if line == state["last"] or now - state["at"] < _MIN_GAP:
            state["pending"] = line  # type: ignore[assignment]
            return
        state["at"], state["last"] = now, line
        state.pop("pending", None)
        emit(line)

    token, stop = _sink.set(throttled), _cancel.set(cancel)
    try:
        yield
    finally:
        _sink.reset(token)
        _cancel.reset(stop)
        _state_sink.reset(state_token)
        _span_factory.reset(span_token)
        pending = state.get("pending")
        if pending and pending != state["last"]:
            emit(str(pending))
