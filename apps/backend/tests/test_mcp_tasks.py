"""Long MCP tool calls (Tasks extension): poll to the end, relay status, cancel on Stop or a mid-task question."""
import asyncio
import threading
from types import SimpleNamespace

import pytest

from agent import mcp_tasks
from agent.lean import progress
from agent.mcp_client import MCPServerState, MCPSession

pytestmark = pytest.mark.skipif(not mcp_tasks.supported(), reason="needs MCP SDK 2.x")


class FakeServer:
    """tasks/get answers in order; records what the client sent."""

    def __init__(self, *states):
        self.states, self.sent = list(states), []

    async def send_request(self, request, result_type):
        self.sent.append((request.method, request.params.task_id))
        if request.method == "tasks/cancel":
            return result_type.model_validate({"taskId": request.params.task_id, "status": "cancelled"})
        return result_type.model_validate(self.states.pop(0))


def resolve(server, stop=lambda: False):
    statuses = []
    claim = mcp_tasks.claims(statuses.append, stop)[mcp_tasks.EXTENSION][0]
    handle = claim.model.model_validate({"resultType": "task", "taskId": "t1", "status": "working", "pollIntervalMs": 10})
    ctx = SimpleNamespace(session=server, tool_name="render", read_timeout_seconds=None)
    return asyncio.run(claim.resolve(handle, ctx)), statuses


def test_polls_until_completed_and_returns_the_tools_own_result():
    server = FakeServer({"taskId": "t1", "status": "working", "statusMessage": "Rendering frame 40 of 120", "pollIntervalMs": 10},
                        {"taskId": "t1", "status": "completed", "result": {"content": [{"type": "text", "text": "render.mp4 saved"}]}})
    result, statuses = resolve(server)
    assert result.content[0].text == "render.mp4 saved" and not result.is_error
    assert "render: Rendering frame 40 of 120" in statuses
    assert server.sent == [("tasks/get", "t1"), ("tasks/get", "t1")]


def test_failure_and_mid_task_questions_come_back_as_errors():
    failed, _ = resolve(FakeServer({"taskId": "t1", "status": "failed", "error": {"code": -1, "message": "GPU out of memory"}}))
    assert failed.is_error and "GPU out of memory" in failed.content[0].text
    server = FakeServer({"taskId": "t1", "status": "input_required", "inputRequests": {"a": {"method": "elicitation/create"}}})
    asked, _ = resolve(server)
    assert asked.is_error and ("tasks/cancel", "t1") in server.sent


def test_stop_cancels_the_task_on_the_server():
    server = FakeServer()
    result, _ = resolve(server, stop=lambda: True)
    assert result.is_error and server.sent == [("tasks/cancel", "t1")]


def test_waiting_call_relays_status_and_turns_stop_into_a_cancel():
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    session = MCPSession(MCPServerState(name="blender", transport="streamable_http", url="https://x.example/mcp",
                                        server_capabilities={"extensions": {mcp_tasks.EXTENSION: {}}}, running=True))
    session._loop, session._session = loop, object()

    async def long_task():
        session._task_status("render: Rendering frame 1 of 120")
        while not session._stop_tasks.is_set():
            await asyncio.sleep(0.05)
        return "cancelled on server"

    lines, stop = [], threading.Event()
    threading.Timer(0.8, stop.set).start()
    try:
        with progress.reporting(lines.append, cancel=stop):
            assert session._wait_for_call(long_task()) == "cancelled on server"
    finally:
        loop.call_soon_threadsafe(loop.stop)
    assert "render: Rendering frame 1 of 120" in lines
