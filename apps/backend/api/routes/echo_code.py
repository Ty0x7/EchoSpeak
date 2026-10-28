"""Owner-facing Echo Code foundation; protected by the normal API auth middleware."""
import time
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


@router.get("/overview")
def overview():
    """Code's sidebar: projects with their native and external coding sessions, plus unorganized ones."""
    data = echo_code.overview()
    for project in data["projects"]:
        try:
            project["external"] = _external("list", project["id"])
        except HTTPException:
            project["external"] = []
    return data


class MoveSession(BaseModel):
    project_id: str = Field(min_length=1, max_length=128)


@router.post("/sessions/{thread_id}/project")
def move_session(thread_id: str, request: MoveSession):
    return _call(echo_code.move_session, thread_id, request.project_id)


@router.get("/projects/{project_id}")
def workspace(project_id: str):
    return _call(echo_code.workspace, project_id)


class StartSession(BaseModel):
    runtime_id: Literal["echo", "codex", "claude_cli", "acp"]
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")


@router.post("/projects/{project_id}/sessions")
def create_session(project_id: str, request: StartSession):
    return _call(echo_code.create_session, project_id, runtime_id=request.runtime_id,
                 idempotency_key=request.idempotency_key)


# External runtimes have their own durable native session identities and never
# become Echo threads. These routes inherit the same owner auth/origin checks.
class ExternalStart(StartSession):
    runtime_id: Literal["codex", "claude_cli", "acp"]
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


@router.post("/external-sessions/{session_id}/connect")
def external_connect(session_id: str):
    # Coding agents use the owner's own sign-in; no API key is accepted here.
    return _external("connect", session_id)


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


# Settings › Models › Coding agents: explicit owner checks and the vendors' own sign-in.
@router.get("/runtimes/{runtime_id}/account")
def runtime_account(runtime_id: Literal["codex", "claude_cli"]):
    """The last status Echo saw (signed in, plan, models; no secrets). Runs nothing."""
    from agent import code_accounts
    status = code_accounts.remembered(runtime_id)
    return {**status, "stale": not status or time.time() - float(status.get("checked_at") or 0) > code_accounts.STALE_SECONDS}


@router.post("/runtimes/{runtime_id}/check")
def check_runtime(runtime_id: Literal["codex", "claude_cli"]):
    from agent import code_accounts
    from agent.code_sessions import manager
    def run():
        manager().admit()
        if runtime_id == "codex":
            return manager().call(code_accounts.codex_status())
        return code_accounts.claude_status()
    return _call(run)


@router.post("/runtimes/{runtime_id}/login")
def login_runtime(runtime_id: Literal["codex", "claude_cli"]):
    from agent import code_accounts
    from agent.code_sessions import manager
    def run():
        manager().admit()
        if runtime_id == "codex":
            return manager().call(code_accounts.codex_login())
        return code_accounts.claude_login()
    return _call(run)
