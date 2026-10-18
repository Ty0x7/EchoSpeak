"""Per-turn traces: what took the time in one reply, in OpenTelemetry's shape.

Each agent turn records spans for its model calls, tool runs, approval waits,
policy decisions, reader calls and context compaction. Spans carry names,
timings, counts and outcomes, never message text, tool arguments or results.
They are saved next to the chat (seven days) for the Activity panel, and can be
sent to any OpenTelemetry collector you run (OTLP/HTTP JSON, Settings > Advanced),
which Private and Offline modes gate like every other connection.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

from loguru import logger

RETENTION = 7 * 86400
MAX_STORE_BYTES = 64_000_000
MAX_SPANS = 2000
_lock = threading.Lock()
_MESSAGE = re.compile(r"msg_[a-f0-9]{12}\Z")


@dataclass
class Span:
    name: str
    span_id: str
    parent_id: str
    start_ns: int
    end_ns: int = 0
    status: str = "ok"
    attrs: dict[str, Any] = field(default_factory=dict)

    def set(self, **attrs: Any) -> None:
        self.attrs.update({k: v for k, v in attrs.items() if isinstance(v, (str, int, float, bool))})

    def public(self, origin: int) -> dict[str, Any]:
        end = self.end_ns or time.time_ns()
        return {"name": self.name, "id": self.span_id, "parent": self.parent_id,
                "start_ms": round((self.start_ns - origin) / 1e6, 2), "duration_ms": round((end - self.start_ns) / 1e6, 2),
                "status": self.status, "attrs": self.attrs}


class Trace:
    def __init__(self, session_id: str, agent: str, request_id: str = "") -> None:
        self.session_id, self.agent, self.request_id = session_id, agent, request_id
        self.trace_id = secrets.token_hex(16)
        self.spans: list[Span] = []
        self._local = threading.local()
        self._lock = threading.Lock()
        self.root = self._new("agent.turn", "", agent=agent)
        self._saved = False

    def _new(self, name: str, parent: str, **attrs: Any) -> Span:
        span = Span(name, secrets.token_hex(8), parent, time.time_ns())
        span.set(**attrs)
        with self._lock:
            if len(self.spans) < MAX_SPANS:
                self.spans.append(span)
        return span

    @contextmanager
    def span(self, name: str, **attrs: Any) -> Iterator[Span]:
        """A timed child of whatever span is open on this thread (tools in parallel each get their own)."""
        parent = getattr(self._local, "current", None) or self.root
        span = self._new(name, parent.span_id, **attrs)
        self._local.current = span
        try:
            yield span
        except BaseException as exc:
            span.status = "error"
            span.set(error=type(exc).__name__)
            raise
        finally:
            span.end_ns = time.time_ns()
            self._local.current = parent

    def event(self, name: str, **attrs: Any) -> None:
        """A zero-length span, for decisions such as a policy ask or a reader flag."""
        with self.span(name, **attrs):
            pass

    def finish(self, message_id: str, *, ok: bool, **attrs: Any) -> Optional[dict[str, Any]]:
        if self._saved:
            return None
        self._saved = True
        self.root.end_ns = time.time_ns()
        self.root.status = "ok" if ok else "error"
        self.root.set(**attrs)
        record = self.public(message_id)
        try:
            save(record)
        except (OSError, ValueError):
            logger.debug("Could not save the trace for {}", message_id, exc_info=True)
        export(record)
        return record

    def public(self, message_id: str) -> dict[str, Any]:
        origin = self.root.start_ns
        with self._lock:
            spans = [span.public(origin) for span in self.spans]
        return {"trace_id": self.trace_id, "session_id": self.session_id, "message_id": message_id,
                "request_id": self.request_id, "agent": self.agent, "started_at": origin / 1e9, "spans": spans}


@contextmanager
def null_span() -> Iterator[Span]:
    """For code paths outside a traced turn (side calls, tests that build a turn by hand)."""
    yield Span("unused", "", "", 0)


# ── storage ─────────────────────────────────────────────────────────────

def root() -> Path:
    from config import DATA_DIR
    return Path(DATA_DIR) / "lean" / "traces"


def _path(session_id: str, message_id: str) -> Path:
    if not _MESSAGE.fullmatch(str(message_id or "")):
        raise ValueError("Invalid message ID")
    chat = hashlib.sha256(str(session_id).encode()).hexdigest()[:20]  # any session string, never a path
    return root() / chat / f"{message_id}.json"


def save(record: dict[str, Any]) -> None:
    path = _path(record["session_id"], record["message_id"])
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record), encoding="utf-8")


def load(session_id: str, message_id: str) -> dict[str, Any]:
    record = json.loads(_path(session_id, message_id).read_text(encoding="utf-8"))
    if record.get("session_id") != session_id:
        raise PermissionError("That trace belongs to another chat")
    return record


def cleanup() -> None:
    with _lock:
        if not root().exists():
            return
        files = sorted(root().glob("*/msg_*.json"), key=lambda p: p.stat().st_mtime)
        total = sum(p.stat().st_size for p in files)
        for path in files:
            size = path.stat().st_size
            if time.time() - path.stat().st_mtime <= RETENTION and total <= MAX_STORE_BYTES:
                continue
            path.unlink(missing_ok=True)
            total -= size


# ── OpenTelemetry export (optional) ─────────────────────────────────────

def _value(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    return {"stringValue": str(value)}


def to_otlp(record: dict[str, Any]) -> dict[str, Any]:
    """OTLP/HTTP JSON (opentelemetry-proto trace/v1). The service is "echospeak"."""
    origin = int(record["started_at"] * 1e9)
    spans = []
    for span in record["spans"]:
        start = origin + int(span["start_ms"] * 1e6)
        spans.append({
            "traceId": record["trace_id"], "spanId": span["id"], "parentSpanId": span["parent"] or "",
            "name": span["name"], "kind": 1, "startTimeUnixNano": str(start),
            "endTimeUnixNano": str(start + int(span["duration_ms"] * 1e6)),
            "attributes": [{"key": f"echospeak.{k}", "value": _value(v)} for k, v in span["attrs"].items()],
            "status": {"code": 2 if span["status"] == "error" else 1},
        })
    resource = [{"key": "service.name", "value": {"stringValue": "echospeak"}},
                {"key": "echospeak.agent", "value": {"stringValue": record["agent"]}}]
    return {"resourceSpans": [{"resource": {"attributes": resource},
                               "scopeSpans": [{"scope": {"name": "echospeak.lean"}, "spans": spans}]}]}


def export(record: dict[str, Any]) -> None:
    from config import config
    url = str(getattr(config, "trace_export_url", "") or "").strip()
    if not url:
        return

    def send() -> None:
        from agent import privacy
        try:
            privacy.require("traces", url)
            import httpx
            httpx.post(url, json=to_otlp(record), timeout=3.0)
        except Exception as exc:  # a collector that is down never slows or breaks a chat
            logger.debug("Trace export to {} failed: {}", url, exc)

    threading.Thread(target=send, name="trace-export", daemon=True).start()
