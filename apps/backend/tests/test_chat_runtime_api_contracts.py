from __future__ import annotations

import queue
import threading
import asyncio
from types import SimpleNamespace

import pytest
import api.routes.chat as chat_routes


def test_query_request_accepts_exact_chat_controls():
    from api.routes.chat import QueryRequest

    request = QueryRequest(
        message="hello",
        thread_id="session-a",
        client_request_id="request-1234",
        thinking_enabled=False,
        reasoning_effort="ultra",
    )

    assert request.thinking_enabled is False
    assert request.reasoning_effort == "ultra"


def test_chat_runtime_routes_have_one_owner():
    from api.server import app

    from tests.route_paths import iter_routes

    route_pairs = [
        (method, path)
        for path, route in iter_routes(app)
        for method in list(getattr(route, "methods", None) or [])
    ]
    for method, path in (
        ("POST", "/query"),
        ("POST", "/query/stream"),
        ("POST", "/query/cancel"),
        ("POST", "/query/queue"),
        ("GET", "/query/queue"),
        ("POST", "/query/queue/claim"),
        ("GET", "/provider"),
        ("POST", "/provider/switch"),
        ("GET", "/startup/readiness"),
    ):
        assert route_pairs.count((method, path)) == 1


def test_cancel_matches_exact_session_and_execution(monkeypatch: pytest.MonkeyPatch):
    from api import deps

    event = threading.Event()
    monkeypatch.setitem(deps._ACTIVE_QUERY_CANCELLATIONS, "request-1234", ("session-a", event, "execution-a"))
    result = asyncio.run(
        chat_routes.cancel_query(
            chat_routes.QueryCancelRequest(
                request_id="request-1234",
                thread_id="session-a",
                execution_id="execution-a",
            )
        )
    )

    assert result["cancelled"] is True
    assert event.is_set()


def test_session_queue_is_durable_and_claimed_fifo(tmp_path):
    from agent.state import StateStore

    store = StateStore(tmp_path)
    store.enqueue_turn(
        "session-a",
        message="first",
        client_request_id="queue-request-1",
    )
    store.enqueue_turn(
        "session-a",
        message="second",
        client_request_id="queue-request-2",
    )

    reloaded = StateStore(tmp_path)
    assert [item["message"] for item in reloaded.list_queued_turns("session-a")] == ["first", "second"]
    assert reloaded.claim_queued_turn("session-a")["message"] == "first"
    assert reloaded.claim_queued_turn("session-a")["message"] == "second"
    assert reloaded.claim_queued_turn("session-a") is None
