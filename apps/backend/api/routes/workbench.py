"""Authenticated owner workbench APIs; paths are always resolved server-side."""
from typing import Literal
import asyncio
import json
import time

from fastapi import APIRouter, Query, WebSocket
from pydantic import BaseModel, Field

from api.routes.echo_code import _call
from api.auth import _api_auth_ok, _get_client_ip, _local_request_guard

router = APIRouter(prefix="/workbench", tags=["workbench"])


class Context(BaseModel):
    project_id: str = Field(min_length=1, max_length=120)
    external_session_id: str = Field(default="", max_length=64)


def run(method, project_id, external_session_id="", **kwargs):
    from agent.code_sessions import manager
    from agent.workbench import dispatch
    instance = manager()
    return _call(instance.call, dispatch(instance, method, project_id, external_session_id, **kwargs))


@router.get("/{project_id}/summary")
def summary(project_id: str, external_session_id: str = ""):
    return run("summary", project_id, external_session_id)


@router.get("/{project_id}/files")
def files(project_id: str, external_session_id: str = "", path: str = Query(default="", max_length=1000)):
    return run("files", project_id, external_session_id, path=path)


@router.get("/{project_id}/file")
def file(project_id: str, external_session_id: str = "", path: str = Query(max_length=1000)):
    return run("file", project_id, external_session_id, path=path)


@router.get("/{project_id}/diff")
def diff(project_id: str, external_session_id: str = "", path: str = Query(default="", max_length=1000)):
    return run("diff", project_id, external_session_id, path=path)


@router.get("/{project_id}/pull-requests")
def pull_requests(project_id: str, external_session_id: str = ""):
    return run("pull_requests", project_id, external_session_id)


@router.get("/{project_id}/usage")
def usage(project_id: str, external_session_id: str = ""):
    return run("usage", project_id, external_session_id)


@router.get("/{project_id}/activity")
def activity(project_id: str, external_session_id: str = ""):
    return run("activity", project_id, external_session_id)


class GitReview(Context):
    action: Literal["commit", "push", "switch", "create_branch"]
    paths: list[str] = Field(default_factory=list, max_length=100)
    message: str = Field(default="", max_length=2000)
    branch: str = Field(default="", max_length=120)


@router.post("/git/review")
def review(request: GitReview):
    return run("review", request.project_id, request.external_session_id, action=request.action,
               paths=request.paths, message=request.message, branch=request.branch)


class GitApply(Context):
    review_id: str = Field(min_length=32, max_length=32)


@router.post("/git/apply")
def apply(request: GitApply):
    return run("apply", request.project_id, request.external_session_id, review_id=request.review_id)


class TerminalStart(Context):
    rows: int = Field(default=24, ge=5, le=120)
    cols: int = Field(default=100, ge=20, le=300)


@router.post("/terminals")
def terminal_start(request: TerminalStart):
    return run("terminal_start", request.project_id, request.external_session_id, rows=request.rows, cols=request.cols)


@router.get("/{project_id}/terminals/{terminal_id}")
def terminal_poll(project_id: str, terminal_id: str, external_session_id: str = "", cursor: int = 0):
    return run("terminal", project_id, external_session_id, terminal_id=terminal_id, operation="poll", cursor=max(0, cursor))


class TerminalAction(TerminalStart):
    operation: Literal["write", "resize", "stop"]
    text: str = Field(default="", max_length=8192)


@router.post("/terminals/{terminal_id}")
def terminal_action(terminal_id: str, request: TerminalAction):
    return run("terminal", request.project_id, request.external_session_id, terminal_id=terminal_id,
               operation=request.operation, text=request.text, rows=request.rows, cols=request.cols)


@router.websocket("/{project_id}/terminals/{terminal_id}/socket")
async def terminal_socket(websocket: WebSocket, project_id: str, terminal_id: str, external_session_id: str = ""):
    host = _get_client_ip(websocket)
    if (_local_request_guard("POST", websocket.headers.get("host", ""), websocket.headers.get("origin", ""), host)
            or not _api_auth_ok(websocket.headers, host)):
        await websocket.close(code=1008, reason="Owner authentication and allowed origin required")
        return
    protocols = str(websocket.headers.get("sec-websocket-protocol") or "").split(",")
    await websocket.accept(subprotocol="echospeak" if "echospeak" in {value.strip() for value in protocols} else None)
    async def output():
        cursor, previous = 0, ""
        while True:
            data = await asyncio.to_thread(run, "terminal", project_id, external_session_id,
                                          terminal_id=terminal_id, operation="wait", cursor=cursor)
            if data["output"] or data["state"] != previous:
                await websocket.send_json(data)
            cursor, previous = data["cursor"], data["state"]
            if data["state"] != "running":
                return
    async def input():
        start, count = time.monotonic(), 0
        while True:
            raw = await websocket.receive_text()
            if len(raw) > 16_000:
                raise ValueError("Terminal frame too large")
            if time.monotonic() - start >= 1:
                start, count = time.monotonic(), 0
            count += 1
            if count > 100:
                raise ValueError("Terminal input rate exceeded")
            value = json.loads(raw)
            if value.get("operation") not in {"write", "resize"}:
                raise ValueError("Unsupported terminal operation")
            request = TerminalAction.model_validate({**value, "project_id": project_id, "external_session_id": external_session_id})
            await asyncio.to_thread(run, "terminal", project_id, external_session_id, terminal_id=terminal_id,
                                    operation=request.operation, text=request.text, rows=request.rows, cols=request.cols)
    tasks = [asyncio.create_task(output()), asyncio.create_task(input())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except Exception:
        # Do not send raw worker/server errors or credentials over this channel.
        pass
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await websocket.close()
        except RuntimeError:
            pass


class Script(Context):
    name: str = Field(min_length=1, max_length=80)
    command: str = Field(min_length=1, max_length=2000)


@router.post("/scripts")
def script_save(request: Script):
    return run("script_save", request.project_id, request.external_session_id, name=request.name, command=request.command)


@router.post("/scripts/{script_id}/run")
def script_run(script_id: str, request: Context):
    return run("script_run", request.project_id, request.external_session_id, script_id=script_id)


class Editor(Context):
    path: str = Field(default="", max_length=1000)


@router.post("/editor")
def editor(request: Editor):
    return run("editor", request.project_id, request.external_session_id, path=request.path)
