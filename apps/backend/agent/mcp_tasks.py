"""Long-running MCP tool calls: the Tasks extension (io.modelcontextprotocol/tasks, spec 2026-07-28).

A server that supports it may answer tools/call with a task handle instead of
waiting: {"resultType": "task", "taskId": ..., "status": "working",
"pollIntervalMs": ...}. EchoSpeak declares the extension, then polls tasks/get
at the server's pace until the task is completed (its CallToolResult is inline),
failed, or cancelled. The tool's progress line shows the task's status message,
Stop cancels the task on the server (tasks/cancel), and a task that needs
input mid-way is cancelled with a clear note, since EchoSpeak can't answer
server questions yet.

The SDK has typed models only for the older core design, so the wire models are
defined here from the extension's specification.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Literal, Optional

from loguru import logger
from pydantic import ConfigDict

EXTENSION = "io.modelcontextprotocol/tasks"
TASK_SECONDS = 30 * 60  # a render or export can take a while; Stop ends it sooner


def supported() -> bool:
    try:
        from mcp.client.extension import ResultClaim  # noqa: F401
        from mcp_types import Request  # noqa: F401
        return True
    except ImportError:
        return False


def _models():
    from mcp_types import CallToolResult, Request, RequestParams, Result

    class TaskParams(RequestParams):
        task_id: str

    class TasksGet(Request[TaskParams, Literal["tasks/get"]]):
        method: Literal["tasks/get"] = "tasks/get"
        params: TaskParams
        name_param = "taskId"  # mirrored into the Mcp-Name header, which the extension requires

    class TasksCancel(Request[TaskParams, Literal["tasks/cancel"]]):
        method: Literal["tasks/cancel"] = "tasks/cancel"
        params: TaskParams
        name_param = "taskId"

    class TaskState(Result):
        model_config = ConfigDict(extra="allow")
        result_type: str = "complete"
        task_id: str
        status: str
        status_message: Optional[str] = None
        poll_interval_ms: Optional[int] = None
        result: Optional[dict[str, Any]] = None
        error: Optional[dict[str, Any]] = None
        input_requests: Optional[dict[str, Any]] = None

    class TaskHandle(Result):  # what tools/call returns; only the handle's own fields (inputRequests is reserved)
        model_config = ConfigDict(extra="allow")
        result_type: Literal["task"] = "task"
        task_id: str
        status: str = "working"
        status_message: Optional[str] = None
        poll_interval_ms: Optional[int] = None

    return CallToolResult, TaskParams, TasksGet, TasksCancel, TaskState, TaskHandle


def _text_result(CallToolResult, text: str, error: bool = True):
    return CallToolResult.model_validate({"content": [{"type": "text", "text": text}], "isError": error})


def claims(on_status: Callable[[str], None], cancelled: Callable[[], bool]) -> dict[str, list]:
    """result_claims for ClientSession: turn a task handle into the tool's ordinary result."""
    from mcp.client.extension import ResultClaim
    CallToolResult, TaskParams, TasksGet, TasksCancel, TaskState, TaskHandle = _models()

    async def resolve(handle, ctx):
        state, deadline = handle, time.monotonic() + TASK_SECONDS
        on_status(f"{ctx.tool_name}: started a long task")

        async def cancel(note: str):
            try:
                await ctx.session.send_request(TasksCancel(params=TaskParams(task_id=handle.task_id)), TaskState)
            except Exception:
                logger.debug("tasks/cancel for {} failed", handle.task_id)
            return _text_result(CallToolResult, note)

        while True:
            if cancelled():
                return await cancel("Stopped: the task was cancelled on the server.")
            if time.monotonic() > deadline:
                return await cancel(f"The task ran longer than {TASK_SECONDS // 60} minutes and was cancelled.")
            wait = min(30.0, max(0.5, (state.poll_interval_ms or 2000) / 1000))
            await asyncio.sleep(wait)
            state = await ctx.session.send_request(TasksGet(params=TaskParams(task_id=handle.task_id)), TaskState)
            if state.status_message:
                on_status(f"{ctx.tool_name}: {state.status_message}")
            if state.status == "completed":
                return CallToolResult.model_validate(state.result or {"content": []})
            if state.status == "failed":
                message = (state.error or {}).get("message") or state.status_message or "the server reported a failure"
                return _text_result(CallToolResult, f"The task failed: {message}")
            if state.status == "cancelled":
                return _text_result(CallToolResult, "The server cancelled the task.")
            if state.status == "input_required":
                return await cancel("The server asked a question mid-task, which EchoSpeak can't answer yet, "
                                    "so the task was cancelled. Try the step in the app itself.")

    return {EXTENSION: [ResultClaim(result_type="task", model=TaskHandle, resolve=resolve)]}


def server_supports(capabilities: dict[str, Any]) -> bool:
    return EXTENSION in dict(capabilities.get("extensions") or {})
