"""Owner-facing Echo Code foundation; protected by the normal API auth middleware."""
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from agent import echo_code

router = APIRouter(prefix="/echo-code", tags=["echo code"])


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except echo_code.RuntimeUnavailable as exc:
        raise HTTPException(409, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, "Project unavailable") from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except (ValueError, OSError, RuntimeError, TimeoutError) as exc:
        raise HTTPException(400, str(exc)[:300]) from exc


@router.get("/runtimes")
def runtimes():
    return echo_code.runtime_catalog()


@router.get("/projects/{project_id}")
def workspace(project_id: str):
    return _call(echo_code.workspace, project_id)


class StartSession(BaseModel):
    runtime_id: Literal["echo", "codex", "claude_code", "acp"]
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")


@router.post("/projects/{project_id}/sessions")
def create_session(project_id: str, request: StartSession):
    return _call(echo_code.create_session, project_id, runtime_id=request.runtime_id,
                 idempotency_key=request.idempotency_key)


# External runtimes have their own durable native session identities and never
# become Echo threads. These routes inherit the same owner auth/origin checks.
class ExternalStart(StartSession):
    runtime_id: Literal["codex", "claude_code", "acp"]
    acknowledge_dirty: bool = False
    acknowledge_execution: bool = False
    command: list[str] = Field(default_factory=list, max_length=32)


def _external(method, *args, **kwargs):
    from agent.code_sessions import manager
    instance = manager()
    return _call(instance.call, getattr(instance, method)(*args, **kwargs))


@router.get("/projects/{project_id}/external-sessions")
def external_sessions(project_id: str):
    _call(echo_code.workspace, project_id)
    return _external("list", project_id)


@router.post("/projects/{project_id}/external-sessions")
def external_start(project_id: str, request: ExternalStart):
    if not request.acknowledge_execution:
        raise HTTPException(400, "Acknowledge the external harness's execution authority first")
    return _external("create", project_id, request.runtime_id, request.idempotency_key,
                     request.acknowledge_dirty, request.command)


@router.get("/external-sessions/{session_id}")
def external_poll(session_id: str, cursor: int = 0):
    return _external("poll", session_id, max(0, cursor))


class Connection(BaseModel):
    api_key: str = Field(default="", max_length=1000)


@router.post("/external-sessions/{session_id}/connect")
def external_connect(session_id: str, request: Connection):
    return _external("connect", session_id, request.api_key)


@router.post("/external-sessions/{session_id}/login")
def external_login(session_id: str):
    return _external("login", session_id)


class Task(BaseModel):
    text: str = Field(min_length=1, max_length=60_000)
    model: str = Field(default="", max_length=200)


@router.post("/external-sessions/{session_id}/turns")
def external_turn(session_id: str, request: Task):
    return _external("prompt", session_id, request.text, request.model)


class Decision(BaseModel):
    allow: bool


@router.post("/external-sessions/{session_id}/approvals/{request_id}")
def external_decide(session_id: str, request_id: str, request: Decision):
    return _external("decide", session_id, request_id, request.allow)


@router.post("/external-sessions/{session_id}/stop")
def external_stop(session_id: str):
    return _external("stop", session_id)


@router.get("/external-sessions/{session_id}/diff")
def external_diff(session_id: str):
    return _external("inspect", session_id)


@router.post("/external-sessions/{session_id}/rollback")
def external_rollback(session_id: str):
    return _external("rollback", session_id)
