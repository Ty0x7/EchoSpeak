"""HTTP routes for the lean runtime: approvals, agents, rooms, toolsets."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from agent.lean.approvals import get_approval_broker
from agent.lean.personas import get_persona_store
from agent.lean.rooms import get_room_store
from agent.lean.settings import approval_mode, context_tokens, lean_runtime_enabled, max_iterations
from agent.lean.toolbox import DEFAULT_TOOLSETS, TOOLSETS

router = APIRouter(prefix="/lean", tags=["lean"])


class ApprovalDecisionRequest(BaseModel):
    decision: str = Field(description="allow | deny | always")


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
    max_messages: int = 6


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
    data["prompt"] = str((routine.action_config or {}).get("query") or routine.description or "")
    data["agent_id"] = str((routine.metadata or {}).get("agent_id") or "echo")
    return data


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
        ],
        "default": DEFAULT_TOOLSETS,
    }


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
