"""Queued follow-ups and live steering: the composer stays usable while an agent works, without
cancelling the run or running two turns against one chat."""

from __future__ import annotations

import threading
from typing import Any

import pytest

from agent.lean import steering
from agent.lean.loop import LeanTurn
from agent.lean.personas import AgentPersona
from agent.lean.provider import ModelTurn, ToolCall
from agent.lean.toolbox import NativeTool, Toolbox


@pytest.fixture(autouse=True)
def clean_inbox():
    steering.take("steer")
    yield
    steering.take("steer")


class _Scripted:
    def __init__(self, turns: list[ModelTurn], on_call=None) -> None:
        self.turns = list(turns)
        self.calls: list[list[dict[str, Any]]] = []
        self.on_call = on_call

    def stream_turn(self, messages, **kw):
        self.calls.append([dict(m) for m in messages])
        if self.on_call:
            self.on_call(len(self.calls))
        turn = self.turns.pop(0)
        if turn.content and kw.get("on_content"):
            kw["on_content"](turn.content)
        return turn


def _turn(tools: dict[str, Any], client: _Scripted, events: list[dict[str, Any]]) -> LeanTurn:
    box = Toolbox(toolsets=["none"], session_id="steer")
    box.native = {n: NativeTool(n, n, {"type": "object", "properties": {}}, f) for n, f in tools.items()}
    return LeanTurn(client=client, persona=AgentPersona(id="echo", name="Echo"), system_prompt="s", history=[],  # type: ignore[arg-type]
                    toolbox=box, session_id="steer", request_id="r", execution_id="e", emit=events.append,
                    cancel=threading.Event(), persist_tool_runs=False, goal="research laptops")


def test_a_steer_sent_during_a_tool_joins_the_next_step_without_restarting():
    ran: list[str] = []

    def search(args):
        ran.append(args.get("q", ""))
        if len(ran) == 1:
            steering.add("steer", "Only look at laptops under $1000.", "s1")  # typed while the tool ran
        return f"results for {args.get('q')}"

    client = _Scripted([
        ModelTurn(tool_calls=[ToolCall("c1", "search", '{"q": "laptops"}')]),
        ModelTurn(tool_calls=[ToolCall("c2", "search", '{"q": "laptops under 1000"}')]),
        ModelTurn(content="Here are three under $1000."),
    ])
    events: list[dict[str, Any]] = []
    result = _turn({"search": search}, client, events).run("research laptops")
    assert ran == ["laptops", "laptops under 1000"]     # the first tool result was kept, not redone
    second = client.calls[1]
    assert second[-1]["role"] == "tool" and "results for laptops" in second[-1]["content"]
    assert "Only look at laptops under $1000." in second[-1]["content"]  # added at the checkpoint
    assert any(e["type"] == "steer_applied" and e["id"] == "s1" for e in events)
    assert any(row.get("kind") == "steer" for row in result.timeline)
    assert steering.pending("steer") == []


def test_a_steer_that_arrives_with_the_final_answer_keeps_the_agent_going():
    def arrive(n):
        if n == 1:
            steering.add("steer", "Also add battery life.", "s2")

    client = _Scripted([ModelTurn(content="Laptop A is best."), ModelTurn(content="Laptop A is best; 18h battery.")],
                       on_call=arrive)
    events: list[dict[str, Any]] = []
    result = _turn({}, client, events).run("best laptop?")
    assert len(client.calls) == 2
    assert client.calls[1][-1]["role"] == "user" and "Also add battery life." in client.calls[1][-1]["content"]
    assert result.text == "Laptop A is best; 18h battery."


def test_helpers_never_swallow_the_users_steer():
    # A research worker or delegated teammate shares the chat's session id. A steer meant for
    # the agent the user is talking to must wait for that agent, not vanish into a helper.
    client = _Scripted([ModelTurn(content="worker done")])
    worker = _turn({}, client, [])
    worker.steerable = False
    steering.add("steer", "focus on prices", "s9")
    worker.run("look this up")
    assert [r["id"] for r in steering.pending("steer")] == ["s9"]
    assert "focus on prices" not in str(client.calls)


def test_steering_never_interrupts_a_running_tool():
    seen: list[str] = []

    def slow(args):
        steering.add("steer", "change of plan", "s3")
        seen.append("tool finished")
        return "done"

    client = _Scripted([ModelTurn(tool_calls=[ToolCall("c1", "slow", "{}")]), ModelTurn(content="ok")])
    _turn({"slow": slow}, client, []).run("go")
    assert seen == ["tool finished"]
    assert "change of plan" in client.calls[1][-1]["content"]


def test_unread_steers_become_queued_follow_ups(monkeypatch):
    queued: list[tuple[str, str, str]] = []

    class Store:
        def enqueue_turn(self, session, *, message, client_request_id):
            queued.append((session, message, client_request_id))

    import agent.state as state

    monkeypatch.setattr(state, "get_state_store", lambda: Store())
    steering.add("steer", "do this next", "late")
    assert [r["id"] for r in steering.to_queue("steer")] == ["late"]
    assert queued == [("steer", "do this next", "late")] and steering.pending("steer") == []


def test_steering_is_deduplicated_and_bounded():
    steering.add("steer", "a", "same")
    steering.add("steer", "a", "same")
    assert len(steering.pending("steer")) == 1
    for i in range(steering.MAX_PENDING - 1):
        steering.add("steer", f"x{i}", f"id{i}")
    with pytest.raises(ValueError):
        steering.add("steer", "one too many", "overflow")
    with pytest.raises(ValueError):
        steering.add("steer", "   ", "blank")


def test_queued_follow_ups_can_be_edited_and_removed_before_they_run(tmp_path, monkeypatch):
    from agent.state import StateStore

    store = StateStore(tmp_path / "state")
    store.enqueue_turn("t", message="first", client_request_id="q1")
    store.enqueue_turn("t", message="second", client_request_id="q2")
    store.enqueue_turn("t", message="second", client_request_id="q2")  # a retried POST: no duplicate
    assert [i["client_request_id"] for i in store.list_queued_turns("t")] == ["q1", "q2"]
    store.update_queued_turn("t", "q2", message="second, edited")
    assert store.list_queued_turns("t")[1]["message"] == "second, edited"
    assert store.update_queued_turn("t", "q1", remove=True)["message"] == "first"
    assert [i["message"] for i in store.list_queued_turns("t")] == ["second, edited"]
    assert store.claim_queued_turn("t")["client_request_id"] == "q2"
    assert store.update_queued_turn("t", "q2", message="too late") is None  # already claimed
    store.enqueue_turn("t", message="x", client_request_id="q3")
    with pytest.raises(ValueError):
        store.update_queued_turn("t", "q3", message="  ")


def test_stop_pauses_the_queue_durably_until_run_next(tmp_path, monkeypatch):
    import asyncio

    import api.routes.chat as chat_routes
    from agent.state import StateStore

    store = StateStore(tmp_path / "s")
    monkeypatch.setattr(chat_routes, "get_state_store", lambda: store)
    store.enqueue_turn("t", message="next thing", client_request_id="q1")
    store.set_queue_paused("t", True)  # what /query/cancel and Stop everything do
    reopened = StateStore(tmp_path / "s")  # the app restarted: the pause is still there
    assert reopened.queue_paused("t") and reopened.claim_queued_turn("t") is None
    assert asyncio.run(chat_routes.get_queued_queries("t"))["paused"] is True
    auto = asyncio.run(chat_routes.claim_queued_query("t", force=False))
    assert auto["claimed"] is False and auto["paused"] is True
    run_next = asyncio.run(chat_routes.claim_queued_query("t", force=True))
    assert run_next["claimed"] and run_next["item"]["message"] == "next thing" and run_next["paused"] is False


def test_steer_route_needs_a_running_turn_in_that_session(monkeypatch):
    import asyncio

    import api.routes.chat as chat_routes
    from api import deps
    from fastapi import HTTPException

    request = chat_routes.QuerySteerRequest(thread_id="steer", message="focus on price", client_request_id="r1")
    with pytest.raises(HTTPException) as idle:
        asyncio.run(chat_routes.steer_query(request))
    assert idle.value.status_code == 409  # nothing to steer: the UI sends it as a message instead
    monkeypatch.setitem(deps._ACTIVE_QUERY_CANCELLATIONS, "run-1", ("steer", threading.Event(), "exec-1"))
    result = asyncio.run(chat_routes.steer_query(request))
    assert result["steered"] and steering.pending("steer")[0]["text"] == "focus on price"


def test_queue_routes_edit_and_remove_by_client_id(tmp_path, monkeypatch):
    import asyncio

    import api.routes.chat as chat_routes
    from agent.state import StateStore
    from fastapi import HTTPException

    store = StateStore(tmp_path / "s")
    monkeypatch.setattr(chat_routes, "get_state_store", lambda: store)
    store.enqueue_turn("t", message="draft", client_request_id="q1")
    edited = asyncio.run(chat_routes.edit_queued_query("q1", chat_routes.QueryQueueEdit(thread_id="t", message="final")))
    assert edited["queue"][0]["message"] == "final"
    assert asyncio.run(chat_routes.remove_queued_query("q1", thread_id="t"))["queue"] == []
    with pytest.raises(HTTPException) as gone:
        asyncio.run(chat_routes.remove_queued_query("q1", thread_id="t"))
    assert gone.value.status_code == 404
