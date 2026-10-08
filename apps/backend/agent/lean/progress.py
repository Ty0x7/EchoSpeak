"""Live progress from inside a running tool.

The loop opens a reporting scope around each tool call (``reporting``); the
tool, or code it calls, says what it is doing with ``report("Reading rtings.com")``.
The text reaches the chat as a ``tool_progress`` event under that tool's row.
Outside a scope ``report`` does nothing, so tools never need to know whether
anyone is listening. Reports are throttled so a chatty tool can't flood the stream.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable, Iterator, Optional

_sink: ContextVar[Optional[Callable[[str], None]]] = ContextVar("echospeak_tool_progress", default=None)

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


@contextmanager
def reporting(emit: Callable[[str], None]) -> Iterator[None]:
    """Route ``report`` calls made in this context to ``emit``, throttled."""
    state = {"at": 0.0, "last": ""}

    def throttled(line: str) -> None:
        now = time.monotonic()
        if line == state["last"] or now - state["at"] < _MIN_GAP:
            state["pending"] = line  # type: ignore[assignment]
            return
        state["at"], state["last"] = now, line
        state.pop("pending", None)
        emit(line)

    token = _sink.set(throttled)
    try:
        yield
    finally:
        _sink.reset(token)
        pending = state.get("pending")
        if pending and pending != state["last"]:
            emit(str(pending))
