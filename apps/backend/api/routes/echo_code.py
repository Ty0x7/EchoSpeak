"""Owner-facing Echo Code foundation; protected by the normal API auth middleware."""
import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
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


class ControlGrant(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    projects: list[str] = Field(default_factory=list, max_length=100)
    agents: list[str] = Field(default_factory=list, max_length=20)
    control: bool = False
    days: int = Field(default=7, ge=1, le=30)


@router.get("/control/clients")
def control_clients():
    from agent.code_control import grants
    return {"clients": grants().list()}


@router.get("/control/bridge")
def control_bridge():
    import sys
    from pathlib import Path
    if getattr(sys, "frozen", False):
        return {"command": sys.executable, "args": ["--control-mcp"]}
    return {"command": sys.executable, "args": ["-m", "agent.control_mcp"], "cwd": str(Path(__file__).resolve().parents[2])}


@router.get("/diagnostics")
def code_diagnostics():
    from agent.code_sessions import manager
    from agent.code_diagnostics import snapshot
    return _call(snapshot, manager())


@router.post("/control/clients")
def create_control_client(request: ControlGrant):
    from agent.code_control import grants
    return _call(grants().create, request.name, request.projects, request.agents, request.control, request.days)


@router.delete("/control/clients/{identity}")
def revoke_control_client(identity: str):
    from agent.code_control import grants
    return _call(grants().revoke, identity)


class ControlCall(BaseModel):
    action: Literal["projects", "runtimes", "threads", "status", "create", "send", "stop", "triage"]
    arguments: dict = Field(default_factory=dict)


@router.post("/control/invoke")
def control_invoke(call: ControlCall, request: Request):
    # This exact endpoint uses only its separate scoped grant, never owner auth.
    from api.auth import _is_local_client, _request_hostname
    from agent.code_control import invoke
    import json
    if (not _is_local_client(request.client.host if request.client else "") or request.headers.get("origin")
            or _request_hostname(request.headers.get("host", "")) not in {"localhost", "127.0.0.1", "::1"}):
        raise HTTPException(403, "MCP control currently accepts local non-browser clients only")
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer ") or len(json.dumps(call.arguments)) > 64000:
        raise HTTPException(403, "A bounded call and separate client grant are required")
    return _call(invoke, auth[7:], call.action, call.arguments)


class BackgroundTask(BaseModel):
    thread_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=50000)
    request_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    effort: Literal["minimal", "low", "medium", "high", "extra_high", "max", "ultra"] = "medium"


@router.post("/background")
def background_task(request: BackgroundTask):
    from agent.code_tasks import submit
    if not echo_code.is_code_thread(request.thread_id):
        raise HTTPException(404, "Coding session unavailable")
    return _call(submit, request.thread_id, request.message, request.request_id, request.effort)


@router.get("/overview")
def overview():
    """Code's sidebar: projects with their native and external coding sessions, plus unorganized ones."""
    data = echo_code.overview()
    from agent.code_drafts import list_drafts
    data["drafts"] = [{k: v for k, v in row.items() if k != "project_root"} for row in list_drafts()]
    for project in data["projects"]:
        try:
            project["external"] = _external("list", project["id"], summary=True)
        except HTTPException:
            project["external"] = []
    return data


class MoveSession(BaseModel):
    project_id: str = Field(min_length=1, max_length=128)


class Stash(BaseModel):
    project_id: str = Field(min_length=1, max_length=128)
    text: str = Field(default="", max_length=50000)
    images: list[str] = Field(default_factory=list, max_length=4)


@router.post("/drafts")
def save_draft(request: Stash):
    from agent.code_drafts import save
    return _call(save, request.project_id, request.text, request.images)


@router.delete("/drafts/{identity}")
def remove_draft(identity: str):
    from agent.code_drafts import remove
    return _call(remove, identity)


@router.get("/drafts/{identity}")
def restore_draft(identity: str):
    from agent.code_drafts import restore
    return _call(restore, identity)


class InboxChoice(BaseModel):
    action: Literal["settled", "snoozed", "reopen"]
    activity: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]+$")
    snooze_seconds: int = Field(default=0, ge=0, le=30 * 86400)


@router.post("/sessions/{thread_id}/inbox")
def triage_session(thread_id: str, request: InboxChoice):
    return _call(echo_code.triage_session, thread_id, request.action, request.activity, request.snooze_seconds)


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


@router.post("/external-sessions/{session_id}/inbox")
def external_triage(session_id: str, request: InboxChoice):
    return _external("triage", session_id, request.action, request.activity, request.snooze_seconds)


class InboxTarget(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    external: bool = False
    activity: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]+$")


class InboxBatch(BaseModel):
    targets: list[InboxTarget] = Field(min_length=1, max_length=100)
    action: Literal["settled", "snoozed", "reopen"]
    snooze_seconds: int = Field(default=0, ge=0, le=30 * 86400)


class LinkPullRequest(InboxTarget):
    number: int = Field(ge=1, le=1_000_000_000)


@router.post("/inbox/pull-request")
def link_pull_request(request: LinkPullRequest):
    from agent.code_prs import link
    from agent.code_sessions import manager
    service = manager()
    return _call(service.call, link(service, request.id, request.external, request.number, request.activity))


@router.post("/inbox/sync")
def sync_inbox():
    from agent.code_prs import sync
    from agent.code_sessions import manager
    service = manager()
    return _call(service.call, sync(service))


@router.post("/inbox/batch")
def inbox_batch(request: InboxBatch):
    results = []
    for target in request.targets:
        try:
            value = (_external("triage", target.id, request.action, target.activity, request.snooze_seconds)
                     if target.external else _call(echo_code.triage_session, target.id, request.action, target.activity, request.snooze_seconds))
            results.append({"id": target.id, "external": target.external, "ok": True, "inbox": value})
        except HTTPException as exc:
            results.append({"id": target.id, "external": target.external, "ok": False, "error": str(exc.detail)})
    return {"results": results}


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
    # The coding agent's own effort control (Codex turn/start effort, Claude Code --effort); "" = its default.
    effort: str = Field(default="", max_length=20, pattern=r"^[a-z_]*$")


@router.post("/external-sessions/{session_id}/turns")
def external_turn(session_id: str, request: Task):
    return _external("prompt", session_id, request.text, request.model, request.effort)


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
