"""HTTP routes for the lean runtime: approvals, agents, rooms, toolsets."""

from __future__ import annotations

import time
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from agent.lean.approvals import get_approval_broker
from agent.lean.personas import get_persona_store
from agent.lean.rooms import get_room_store
from agent.lean.settings import approval_mode, context_tokens, lean_runtime_enabled, max_iterations
from agent.lean.toolbox import DEFAULT_TOOLSETS, TOOLSET_ALIASES, TOOLSETS

router = APIRouter(prefix="/lean", tags=["lean"])


@router.get("/observations/{observation_id}/image")
def observation_image(observation_id: str, session_id: str = Query(...)):
    """Authenticated, chat-scoped content; never an arbitrary filesystem endpoint."""
    from fastapi.responses import Response
    from agent.lean.observations import load
    try:
        _, data = load(observation_id, session_id)
    except PermissionError as exc:
        raise HTTPException(403, detail=str(exc)) from exc
    except (OSError, ValueError, KeyError) as exc:
        raise HTTPException(404, detail="Screenshot unavailable or expired") from exc
    return Response(data, media_type="image/png", headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


class ApprovalDecisionRequest(BaseModel):
    decision: str = Field(description="allow | deny | always, or the answer to an ask_user question")


class AgentModelPayload(BaseModel):
    provider: str = ""
    model_id: str = ""


class AgentPayload(BaseModel):
    name: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    soul: Optional[str] = None
    avatar: Optional[str] = None
    toolsets: Optional[list[str]] = None
    model: Optional[AgentModelPayload] = None


class RoomPayload(BaseModel):
    name: str = ""
    agent_ids: list[str] = Field(default_factory=list)
    kind: str = "group"
    mode: str = "reply"
    max_messages: int = 30


class RoomUpdatePayload(BaseModel):
    name: Optional[str] = None
    agent_ids: Optional[list[str]] = None
    mode: Optional[str] = None
    max_messages: Optional[int] = None


def _agent_public(agent: Any) -> dict[str, Any]:
    data = agent.model_dump()
    data["initials"] = agent.initials()
    return data


@router.get("/status")
def lean_status() -> dict[str, Any]:
    return {
        "enabled": lean_runtime_enabled(),
        "max_iterations": max_iterations(),
        "context_tokens": context_tokens(),
        "approval_mode": approval_mode(),
    }


_setup_state: dict[str, Any] = {"running": False, "message": "", "ok": None}


@router.get("/terminal")
def terminal_info() -> dict[str, Any]:
    from agent.lean.terminal import terminal_status

    return {**terminal_status(), "setup": dict(_setup_state)}


@router.post("/terminal/setup")
def terminal_setup() -> dict[str, Any]:
    """Start Docker Desktop if needed, build the sandbox image, start the container."""
    import threading

    from agent.lean.terminal import get_sandbox, mount_plan

    if _setup_state["running"]:
        return dict(_setup_state)

    def work() -> None:
        _setup_state.update(running=True, ok=None, message="Starting Docker…")
        try:
            sandbox = get_sandbox()
            ok, detail = sandbox.ensure_daemon(wait_seconds=150)
            if not ok:
                _setup_state.update(ok=False, message=detail)
                return
            _setup_state["message"] = "Building the sandbox image (first time takes a few minutes)…"
            sandbox.ensure_image()
            _setup_state["message"] = "Starting the sandbox container…"
            ok, detail = sandbox.ensure_container(mount_plan())
            _setup_state.update(ok=ok, message="Sandbox is ready." if ok else detail)
        except Exception as exc:
            _setup_state.update(ok=False, message=str(exc))
        finally:
            _setup_state["running"] = False

    threading.Thread(target=work, name="sandbox-setup", daemon=True).start()
    return {"running": True, "message": "Setting up…"}


@router.post("/terminal/reset")
def terminal_reset() -> dict[str, Any]:
    """Remove the sandbox container (installed packages are lost; your files are not)."""
    from agent.lean.terminal import get_sandbox

    try:
        get_sandbox().remove_container()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"ok": True}


# ── Automations (routines on the lean loop; no Project binding required) ──

class RoutinePayload(BaseModel):
    name: Optional[str] = None
    prompt: Optional[str] = None
    schedule: Optional[str] = Field(default=None, description="Cron expression, e.g. '0 8 * * *'")
    trigger_type: Optional[str] = None  # schedule | manual | webhook
    agent_id: Optional[str] = None
    channels: Optional[list[str]] = None
    enabled: Optional[bool] = None


def _routine_public(routine: Any) -> dict[str, Any]:
    data = routine.model_dump()
    config_ = routine.action_config or {}
    data["kind"] = str((routine.metadata or {}).get("kind") or "routine")
    # A reminder's "prompt" is its message (agent/reminders.py); a routine's is its instructions.
    data["prompt"] = str(config_.get("message") if data["kind"] == "reminder" and not config_.get("query")
                         else config_.get("query") or routine.description or "")
    data["agent_id"] = str((routine.metadata or {}).get("agent_id") or "echo")
    return data


# ── notifications (agent/notifications.py) ──────────────────────────────
@router.get("/notifications")
def list_notifications(after: int = 0) -> dict[str, Any]:
    """New notifications after a sequence number: the web UI refreshes the chat a reminder posted into."""
    from agent import notifications

    return notifications.since(after)


@router.post("/notifications/claim")
def claim_notifications() -> dict[str, Any]:
    """The desktop shell takes notifications it hasn't shown, so each Windows notification appears once.

    It polls every few seconds, so this also tells it whether closing the window keeps EchoSpeak in the tray.
    """
    from agent import notifications
    from config import config

    return {"items": notifications.claim(), "close_to_tray": bool(getattr(config, "desktop_close_to_tray", False))}


def _check_cron(expr: Optional[str]) -> None:
    if not expr:
        return
    try:
        from croniter import croniter

        croniter(expr)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid schedule: {exc}") from exc


@router.get("/routines")
def list_lean_routines() -> dict[str, Any]:
    from agent.routines import get_routine_manager

    items = [_routine_public(r) for r in get_routine_manager().list_routines()]
    items.sort(key=lambda r: r.get("created_at") or "")
    return {"items": items}


@router.post("/routines")
def create_lean_routine(payload: RoutinePayload) -> dict[str, Any]:
    from agent.routines import get_routine_manager

    name = (payload.name or "").strip()
    prompt = (payload.prompt or "").strip()
    if not name or not prompt:
        raise HTTPException(status_code=400, detail="A routine needs a name and instructions.")
    trigger = payload.trigger_type or ("schedule" if payload.schedule else "manual")
    _check_cron(payload.schedule if trigger == "schedule" else None)
    routine = get_routine_manager().create_routine(
        name=name,
        description=prompt[:200],
        enabled=True if payload.enabled is None else payload.enabled,
        trigger_type=trigger,
        schedule=payload.schedule if trigger == "schedule" else None,
        action_type="query",
        action_config={"query": prompt},
        metadata={"agent_id": payload.agent_id or "echo"},
        delivery_channels=["web", *[c for c in (payload.channels or []) if c != "web"]],
    )
    return _routine_public(routine)


@router.patch("/routines/{routine_id}")
def update_lean_routine(routine_id: str, payload: RoutinePayload) -> dict[str, Any]:
    from agent.routines import get_routine_manager

    manager = get_routine_manager()
    existing = manager.get_routine(routine_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Routine not found")
    if payload.schedule is not None:
        _check_cron(payload.schedule)
    metadata = dict(existing.metadata or {})
    if payload.agent_id is not None:
        metadata["agent_id"] = payload.agent_id
    action_config = dict(existing.action_config or {})
    if payload.prompt is not None:
        action_config["query"] = payload.prompt
    routine = manager.update_routine(
        routine_id,
        name=payload.name,
        description=payload.prompt[:200] if payload.prompt is not None else None,
        enabled=payload.enabled,
        trigger_type=payload.trigger_type,
        schedule=payload.schedule,
        action_config=action_config,
        metadata=metadata,
        delivery_channels=(["web", *[c for c in payload.channels if c != "web"]] if payload.channels is not None else None),
    )
    return _routine_public(routine)


@router.delete("/routines/{routine_id}")
def delete_lean_routine(routine_id: str) -> dict[str, Any]:
    from agent.routines import get_routine_manager

    if not get_routine_manager().delete_routine(routine_id):
        raise HTTPException(status_code=404, detail="Routine not found")
    return {"deleted": True, "id": routine_id}


@router.post("/routines/{routine_id}/run")
def run_lean_routine(routine_id: str) -> dict[str, Any]:
    """Run now in the background; the result appears in the routine's chat."""
    import threading

    from agent.routines import get_routine_manager

    manager = get_routine_manager()
    if manager.get_routine(routine_id) is None:
        raise HTTPException(status_code=404, detail="Routine not found")
    threading.Thread(target=manager.run_routine, args=(routine_id,), name="routine-run", daemon=True).start()
    return {"started": True, "id": routine_id}


@router.get("/toolsets")
def list_toolsets() -> dict[str, Any]:
    return {
        "toolsets": [
            {"id": key, "tools": [t for t in tools if not t.startswith("@")]}
            for key, tools in TOOLSETS.items()
            if key not in TOOLSET_ALIASES
        ],
        "default": DEFAULT_TOOLSETS,
    }


@router.get("/traces/{message_id}")
def message_trace(message_id: str, session_id: str = Query(...)) -> dict[str, Any]:
    """Timings for one reply (agent/lean/tracing.py): names, durations and counts, no message text."""
    from agent.lean import tracing
    try:
        return tracing.load(session_id, message_id)
    except PermissionError as exc:
        raise HTTPException(403, detail=str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(404, detail="No trace for that reply; traces are kept for seven days") from exc


@router.get("/approvals")
def list_approvals(session_id: str = Query(default="")) -> dict[str, Any]:
    return {"items": get_approval_broker().pending_for(session_id)}


@router.post("/approvals/{approval_id}")
def decide_approval(approval_id: str, request: ApprovalDecisionRequest) -> dict[str, Any]:
    try:
        approval = get_approval_broker().resolve(approval_id, request.decision)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if approval is None:
        raise HTTPException(status_code=404, detail="That approval is no longer waiting.")
    return {"ok": True, "approval": approval.public()}


@router.get("/approval-grants")
def list_approval_grants() -> dict[str, Any]:
    """The owner's "Always allow" grants, and the approval mode they work with."""
    from agent.lean import settings
    from agent.lean.approvals import get_grant_store

    return {"items": get_grant_store().list(), "mode": settings.approval_mode()}


@router.delete("/approval-grants/{key:path}")
def remove_approval_grant(key: str) -> dict[str, Any]:
    from agent.lean.approvals import get_grant_store

    if not get_grant_store().remove(key):
        raise HTTPException(status_code=404, detail="That grant doesn't exist.")
    return {"ok": True}


class SkillImportRequest(BaseModel):
    path: str = Field(default="", description="A folder that contains SKILL.md")
    text: str = Field(default="", description="The contents of a SKILL.md file")


class SkillApproveRequest(BaseModel):
    digest: str = Field(default="", description="The digest shown when the skill was reviewed")


class SkillFromLessonRequest(BaseModel):
    lesson_id: str
    name: str = ""


@router.get("/mcp/approvals")
def mcp_approvals() -> dict[str, Any]:
    """MCP servers waiting for the owner's review (agent/mcp_trust.py)."""
    from agent import mcp_trust
    from agent.mcp_client import get_mcp_manager
    from config import config

    raw_servers = dict(getattr(config, "mcp_servers", None) or {})
    items = [
        {"name": row["name"], "approval": row["approval"], "detail": mcp_trust.describe(raw_servers.get(row["name"]) or {}),
         "message": row["last_error"]}
        for row in get_mcp_manager().status()["servers"] if row.get("approval")
    ]
    return {"items": items}


@router.post("/mcp/{name}/approve")
def approve_mcp_server(name: str) -> dict[str, Any]:
    from agent import mcp_trust
    from agent.mcp_client import get_mcp_manager
    from config import config

    raw_servers = dict(getattr(config, "mcp_servers", None) or {})
    raw = raw_servers.get(name)
    if not isinstance(raw, dict):
        raise HTTPException(status_code=404, detail="No MCP server with that name in settings")
    mcp_trust.approve(name, raw)
    status = get_mcp_manager().initialize_servers(raw_servers)
    return {"ok": True, "status": status}


def _hosted_server(name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    from config import config

    raw_servers = dict(getattr(config, "mcp_servers", None) or {})
    raw = raw_servers.get(name)
    if not isinstance(raw, dict):
        raise HTTPException(status_code=404, detail="No MCP server with that name in settings")
    if not str(raw.get("url") or raw.get("endpoint") or "").strip():
        raise HTTPException(status_code=400, detail="Only hosted (HTTP) servers sign in; this one runs on this PC")
    return raw_servers, raw


class AppToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class AppResourceRead(BaseModel):
    uri: str


class AppContext(BaseModel):
    content: Optional[list[Any]] = None
    structuredContent: Optional[Any] = None


def _app_view(app_id: str, session_id: str) -> dict[str, Any]:
    from agent import mcp_apps
    try:
        return mcp_apps.load(app_id, session_id)
    except PermissionError as exc:
        raise HTTPException(403, detail=str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(404, detail="That app view is no longer available") from exc


@router.get("/mcp-apps/{app_id}")
def mcp_app(app_id: str, session_id: str = Query(...)) -> dict[str, Any]:
    """An MCP App view's tool input and result, for the host bridge (apps/web/src/widgets/McpAppView.tsx)."""
    from agent import mcp_apps
    record = _app_view(app_id, session_id)
    return {**mcp_apps.public(record), "input": record["input"], "result": record["result"]}


@router.post("/mcp-apps/{app_id}/frame-token")
def mcp_app_frame_token(app_id: str, session_id: str = Query(...)) -> dict[str, Any]:
    from agent import mcp_apps
    _app_view(app_id, session_id)
    return {"token": mcp_apps.issue_token(app_id, session_id), "ttl": mcp_apps.TOKEN_TTL}


@router.get("/mcp-apps/{app_id}/frame")
def mcp_app_frame(app_id: str):
    """The view's page, from the backend's origin, under the CSP its server declared. Loaded only in a sandboxed iframe."""
    import json as _json

    from fastapi.responses import HTMLResponse

    from agent import mcp_apps
    try:
        record = _json.loads(mcp_apps._path(app_id).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(404, detail="That app view is no longer available") from exc
    return HTMLResponse(record["html"], headers={
        "Content-Security-Policy": mcp_apps.csp(record.get("ui") or {}, offline=mcp_apps.offline()),
        "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "Cache-Control": "no-store",
        "Cross-Origin-Resource-Policy": "same-origin",
    })


@router.post("/mcp-apps/{app_id}/call")
def mcp_app_call(app_id: str, request: AppToolCall, session_id: str = Query(...)) -> dict[str, Any]:
    """tools/call from a view: read-only, app-visible tools of its own server only."""
    import json as _json

    from agent import mcp_apps
    from agent.mcp_client import get_mcp_manager
    record = _app_view(app_id, session_id)
    refused = mcp_apps.app_call_allowed(record, request.name)
    if refused:
        raise HTTPException(403, detail=refused)
    raw = get_mcp_manager().call_app_tool(record["server"], request.name, request.arguments)
    try:
        return _json.loads(raw)
    except ValueError:
        return {"content": [{"type": "text", "text": str(raw)}]}


@router.post("/mcp-apps/{app_id}/read")
def mcp_app_read(app_id: str, request: AppResourceRead, session_id: str = Query(...)) -> dict[str, Any]:
    from agent.mcp_client import get_mcp_manager
    record = _app_view(app_id, session_id)
    return get_mcp_manager().read_resource(record["server"], request.uri)


@router.post("/mcp-apps/{app_id}/context")
def mcp_app_context(app_id: str, request: AppContext, session_id: str = Query(...)) -> dict[str, Any]:
    from agent import mcp_apps
    _app_view(app_id, session_id)
    mcp_apps.update_context(app_id, session_id, request.model_dump())
    return {}


class RemoteAgentRequest(BaseModel):
    url: str
    fingerprint: str = ""
    token: str = ""


@router.get("/a2a/agents")
def remote_agents() -> dict[str, Any]:
    """Other agents you pinned for ask_remote_agent (agent/a2a_trust.py). Tokens never leave the broker."""
    from agent import a2a_trust
    return {"items": [{k: v for k, v in a.items() if k != "token_ref"} | {"has_token": bool(a.get("token_ref"))}
                      for a in a2a_trust.trusted_agents()]}


@router.post("/a2a/agents/inspect")
def inspect_remote_agent(request: RemoteAgentRequest) -> dict[str, Any]:
    """Fetch and verify an agent's signed card, and show the fingerprint to confirm."""
    from agent import a2a_trust, privacy
    try:
        return a2a_trust.inspect_agent(request.url)
    except privacy.PrivacyBlocked as exc:
        raise HTTPException(403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(400, detail=str(exc)[:300]) from exc


@router.post("/a2a/agents")
def trust_remote_agent(request: RemoteAgentRequest) -> dict[str, Any]:
    """Pin an agent, only if its card is still signed by the key whose fingerprint you confirmed."""
    from agent import a2a_trust, privacy
    try:
        info = a2a_trust.inspect_agent(request.url)
    except privacy.PrivacyBlocked as exc:
        raise HTTPException(403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(400, detail=str(exc)[:300]) from exc
    if not request.fingerprint or request.fingerprint != info["fingerprint"]:
        raise HTTPException(409, detail="The agent's key changed since you looked. Check it again before trusting it.")
    return a2a_trust.trust(info, request.token.strip())


@router.delete("/a2a/agents")
def forget_remote_agent(url: str = Query(...)) -> dict[str, Any]:
    from agent import a2a_trust
    return {"removed": a2a_trust.forget(url)}


@router.get("/mcp/signin")
def mcp_sign_ins() -> dict[str, Any]:
    """Hosted servers that sign in, and where each one stands (agent/mcp_oauth.py)."""
    from agent.mcp_client import get_mcp_manager

    rows = get_mcp_manager().status()["servers"]
    return {"items": [{"name": row["name"], "running": row["running"], **row["auth"]}
                      for row in rows if row.get("auth", {}).get("state")]}


@router.post("/mcp/{name}/signin")
def sign_in_mcp_server(name: str) -> dict[str, Any]:
    """Open the server's sign-in page in the browser; tokens are saved with DPAPI (agent/mcp_oauth.py)."""
    from agent import mcp_oauth, privacy
    from agent.mcp_client import get_mcp_manager

    raw_servers, raw = _hosted_server(name)
    url = str(raw.get("url") or raw.get("endpoint")).strip()
    try:
        flow = mcp_oauth.start_sign_in(name, url, on_done=lambda: get_mcp_manager().initialize_servers(raw_servers))
    except privacy.PrivacyBlocked as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {"ok": True, "auth": flow}


@router.post("/mcp/{name}/signout")
def sign_out_mcp_server(name: str) -> dict[str, Any]:
    from agent import mcp_oauth
    from agent.mcp_client import get_mcp_manager

    raw_servers, raw = _hosted_server(name)
    removed = mcp_oauth.sign_out(str(raw.get("url") or raw.get("endpoint")).strip())
    get_mcp_manager().initialize_servers(raw_servers)
    return {"ok": True, "removed": removed}


class ProposalApproveRequest(BaseModel):
    values: dict[str, str] = Field(default_factory=dict, description="The settings the user typed, by name")


@router.get("/integrations/proposals")
def integration_proposals() -> dict[str, Any]:
    """Connections agents suggested, waiting for the owner (agent/integrations.py)."""
    from agent import integrations

    return {"items": integrations.proposals("waiting")}


@router.post("/integrations/proposals/{proposal_id}/approve")
def approve_integration(proposal_id: str, request: ProposalApproveRequest) -> dict[str, Any]:
    """Add a suggested connection as an MCP server, pin it, and start it.

    Keys go through the settings path that stores MCP env and headers with DPAPI.
    """
    from agent import integrations, mcp_trust
    from agent.mcp_client import get_mcp_manager
    from api.routes.settings import _apply_settings_patch, _sanitize_incoming_settings
    from config import config

    proposal = integrations.get_proposal(proposal_id)
    if proposal is None or proposal.get("status") != "waiting":
        raise HTTPException(status_code=404, detail="No suggestion waiting with that id")
    name = str(proposal["server_name"])
    if name in dict(getattr(config, "mcp_servers", None) or {}):
        raise HTTPException(status_code=409, detail=f"A connection called {name} already exists")
    try:
        server = integrations.server_config(proposal, request.values)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _apply_settings_patch(_sanitize_incoming_settings({"mcp_servers": {name: server}}))
    raw_servers = dict(getattr(config, "mcp_servers", None) or {})
    raw = raw_servers.get(name)
    if not isinstance(raw, dict):
        raise HTTPException(status_code=500, detail="The connection was not saved")
    mcp_trust.approve(name, raw)
    status = get_mcp_manager().initialize_servers(raw_servers)
    integrations.mark(proposal_id, "approved", approved_at=time.time())
    row = next((r for r in status.get("servers") or [] if r.get("name") == name), {})
    return {"ok": True, "name": name, "server": {k: row.get(k) for k in ("running", "tool_count", "last_error")}}


@router.post("/integrations/proposals/{proposal_id}/dismiss")
def dismiss_integration(proposal_id: str) -> dict[str, Any]:
    from agent import integrations

    if integrations.mark(proposal_id, "dismissed") is None:
        raise HTTPException(status_code=404, detail="No suggestion with that id")
    return {"ok": True}


class ContradictionResolveRequest(BaseModel):
    a: str
    b: str
    keep: str = Field(description="The id of the memory to keep, or 'both'")


def _memory_store():
    from api.deps import get_agent

    memory = getattr(get_agent(), "memory", None)
    if memory is None or not hasattr(memory, "_records_lock"):
        raise HTTPException(status_code=503, detail="Memory isn't available right now")
    return memory


@router.get("/memory/tidy")
def memory_tidy_status() -> dict[str, Any]:
    """The last idle clean-up and the contradictions waiting for the owner (agent/lean/memory_tidy.py)."""
    from agent.lean import memory_tidy

    return {"report": memory_tidy.last_report(), "contradictions": memory_tidy.open_contradictions(_memory_store())}


@router.post("/memory/tidy")
def memory_tidy_now() -> dict[str, Any]:
    from agent.lean import memory_tidy

    memory = _memory_store()
    report = memory_tidy.tidy(memory)
    return {"report": report, "contradictions": memory_tidy.open_contradictions(memory)}


@router.post("/memory/contradictions/resolve")
def resolve_contradiction(request: ContradictionResolveRequest) -> dict[str, Any]:
    from agent.lean import memory_tidy

    memory = _memory_store()
    try:
        memory_tidy.resolve(memory, request.a, request.b, request.keep)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Memory not found") from exc
    return {"ok": True, "contradictions": memory_tidy.open_contradictions(memory)}


@router.post("/stop-all")
def stop_all() -> dict[str, Any]:
    """Stop everything: cancel every running agent and pause routines, channels and A2A."""
    from agent.lean import stop

    return stop.stop_everything("Stopped from the app")


@router.post("/resume")
def resume_all() -> dict[str, Any]:
    from agent.lean import stop

    return stop.resume()


@router.get("/stop-status")
def stop_status() -> dict[str, Any]:
    from agent.lean import outbound, stop

    return {**stop.status(), "outbound": outbound.counts(),
            "limits": {"per_minute": outbound.per_minute(), "per_hour": outbound.per_hour()}}


@router.get("/agent-skills")
def list_agent_skills() -> dict[str, Any]:
    """Open-format Agent Skills (agent/lean/agent_skills.py), with their review status."""
    from agent.lean import agent_skills

    return {"items": [skill.public(with_body=True) for skill in agent_skills.list_skills()]}


@router.post("/agent-skills/import")
def import_agent_skill(request: SkillImportRequest) -> dict[str, Any]:
    from agent.lean import agent_skills

    try:
        if request.text.strip():
            skill = agent_skills.import_text(request.text)
        elif request.path.strip():
            skill = agent_skills.import_folder(request.path)
        else:
            raise ValueError("Give a folder or paste a SKILL.md.")
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "skill": skill.public(with_body=True)}


@router.post("/agent-skills/from-lesson")
def agent_skill_from_lesson(request: SkillFromLessonRequest) -> dict[str, Any]:
    from agent import learning
    from agent.lean import agent_skills

    lesson = learning.get_experience_store().get_lesson(request.lesson_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found")
    try:
        skill = agent_skills.skill_from_lesson(lesson, request.name.strip())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "skill": skill.public(with_body=True)}


@router.post("/agent-skills/documentation/import")
def import_documentation_skill() -> dict[str, Any]:
    from agent.lean import agent_skills
    try:
        skill = agent_skills.import_documentation_skill()
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "skill": skill.public(with_body=True)}


@router.post("/agent-skills/{name}/approve")
def approve_agent_skill(name: str, request: SkillApproveRequest) -> dict[str, Any]:
    from agent.lean import agent_skills

    try:
        skill = agent_skills.approve(name, request.digest.strip())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Skill not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "skill": skill.public(with_body=True)}


@router.delete("/agent-skills/{name}")
def remove_agent_skill(name: str) -> dict[str, Any]:
    from agent.lean import agent_skills

    if not agent_skills.remove(name):
        raise HTTPException(status_code=404, detail="Skill not found")
    return {"ok": True}


@router.get("/agents")
def list_agents() -> dict[str, Any]:
    return {"items": [_agent_public(agent) for agent in get_persona_store().list()]}


@router.post("/agents")
def create_agent(payload: AgentPayload) -> dict[str, Any]:
    data = payload.model_dump(exclude_none=True)
    try:
        agent = get_persona_store().create(data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _agent_public(agent)


@router.patch("/agents/{agent_id}")
def update_agent(agent_id: str, payload: AgentPayload) -> dict[str, Any]:
    try:
        agent = get_persona_store().update(agent_id, payload.model_dump(exclude_none=True))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Agent not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _agent_public(agent)


@router.delete("/agents/{agent_id}")
def delete_agent(agent_id: str) -> dict[str, Any]:
    if not get_persona_store().delete(agent_id):
        raise HTTPException(status_code=400, detail="That agent can't be deleted.")
    return {"deleted": True, "id": agent_id}


@router.get("/rooms")
def list_rooms() -> dict[str, Any]:
    return {"items": [room.model_dump() for room in get_room_store().list()]}


@router.post("/rooms")
def create_room(payload: RoomPayload) -> dict[str, Any]:
    store = get_persona_store()
    missing = [agent_id for agent_id in payload.agent_ids if store.get(agent_id) is None]
    if missing:
        raise HTTPException(status_code=400, detail=f"Unknown agents: {', '.join(missing)}")
    try:
        room = get_room_store().create(
            name=payload.name, agent_ids=payload.agent_ids, kind=payload.kind,
            mode=payload.mode, max_messages=payload.max_messages,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return room.model_dump()


@router.patch("/rooms/{room_id}")
def update_room(room_id: str, payload: RoomUpdatePayload) -> dict[str, Any]:
    try:
        room = get_room_store().update(
            room_id, name=payload.name, agent_ids=payload.agent_ids,
            mode=payload.mode, max_messages=payload.max_messages,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Room not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return room.model_dump()


@router.delete("/rooms/{room_id}")
def delete_room(room_id: str) -> dict[str, Any]:
    if not get_room_store().delete(room_id):
        raise HTTPException(status_code=404, detail="Room not found")
    return {"deleted": True, "id": room_id}


@router.get("/search")
def search_chats(q: str = "", limit: int = 20) -> dict[str, Any]:
    """Search every past chat. One row per chat, best match first."""
    from agent.state import get_state_store
    from agent.threads import get_thread_manager

    hits = get_state_store().search_messages(q, limit=max(1, min(limit, 50)) * 4)
    manager = get_thread_manager()
    chats: dict[str, dict[str, Any]] = {}
    for hit in hits:
        chat = chats.get(hit["session_id"])
        if chat is None:
            thread = manager.get_thread(hit["session_id"])
            if thread is None:
                continue  # deleted chat
            chat = chats[hit["session_id"]] = {
                "session_id": hit["session_id"],
                "title": thread.title or "Untitled chat",
                "snippet": hit["snippet"],
                "role": hit["role"],
                "agent": hit["agent"],
                "created_at": hit["created_at"],
                "matches": 0,
            }
        chat["matches"] += 1
    return {"query": q, "items": list(chats.values())[:limit]}


@router.get("/media")
def proxy_media(url: str = Query(..., max_length=2048)):
    """Remote images for chat cards, fetched by the backend (SSRF-checked, size and time capped, cached)."""
    from fastapi.responses import Response

    from agent.lean.media_proxy import MediaError, fetch_image, media_response_headers

    try:
        body, content_type = fetch_image(url)
    except MediaError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    return Response(content=body, media_type=content_type, headers=media_response_headers(content_type))


# ── Artifacts ─────────────────────────────────────────────────────────────

class ArtifactRestore(BaseModel):
    version: int


@router.get("/artifacts")
def list_artifacts(session_id: str = Query(default="")) -> dict[str, Any]:
    from agent.lean import artifacts

    return {"items": artifacts.list_all(session_id)}


@router.get("/artifacts/{artifact_id}")
def get_artifact(artifact_id: str, version: Optional[int] = Query(default=None)) -> dict[str, Any]:
    from agent.lean import artifacts

    record = artifacts.get(artifact_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    try:
        current = artifacts.version(record, version)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Version not found") from exc
    return {
        **artifacts.summary(record),
        "history": [{"n": v["n"], "title": v.get("title", ""), "at": v.get("at", 0), "note": v.get("note", "")} for v in record["versions"]],
        "current": {"n": current["n"], "title": current.get("title", ""), "content": current["content"]},
    }


@router.post("/artifacts/{artifact_id}/frame-token")
def artifact_frame_token(artifact_id: str) -> dict[str, Any]:
    from agent.lean import artifacts

    record = artifacts.get(artifact_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    if record["kind"] not in {"html", "svg"}:
        raise HTTPException(status_code=400, detail="Only HTML and SVG artifacts run in a frame")
    return {"token": artifacts.issue_frame_token(artifact_id), "ttl": artifacts.FRAME_TOKEN_TTL}


@router.get("/artifacts/{artifact_id}/frame")
def artifact_frame(artifact_id: str, v: Optional[int] = Query(default=None)):
    """The sandboxed page for HTML/SVG artifacts. Only ever loaded inside a sandboxed iframe."""
    from fastapi.responses import HTMLResponse

    from agent.lean import artifacts

    record = artifacts.get(artifact_id)
    if record is None or record["kind"] not in {"html", "svg"}:
        raise HTTPException(status_code=404, detail="Artifact not found")
    try:
        html = artifacts.frame_html(record, v)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Version not found") from exc
    return HTMLResponse(html, headers={
        "Content-Security-Policy": artifacts.FRAME_CSP,
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Cache-Control": "no-store",
        "Cross-Origin-Resource-Policy": "same-origin",
    })


@router.post("/artifacts/{artifact_id}/restore")
def restore_artifact(artifact_id: str, payload: ArtifactRestore) -> dict[str, Any]:
    from agent.lean import artifacts

    try:
        record = artifacts.restore(artifact_id, payload.version)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Artifact or version not found") from exc
    return artifacts.summary(record)


@router.delete("/artifacts/{artifact_id}")
def delete_artifact(artifact_id: str) -> dict[str, Any]:
    from agent.lean import artifacts

    if not artifacts.delete(artifact_id):
        raise HTTPException(status_code=404, detail="Artifact not found")
    return {"deleted": True, "id": artifact_id}
