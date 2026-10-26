"""Owner-reviewed configuration and continuation using existing Lean services."""
from __future__ import annotations

import json
import threading
import time
from typing import Any

from agent.coordination import current, digest


def coordinator():
    from agent.state import get_state_store
    return get_state_store().coordination


def notes_scope(persona, session_id, project_id):
    return "session:" + session_id if persona.context_scope == "session" else project_id


def validate_proposal(kind: str, payload: dict) -> dict:
    from agent.lean.personas import get_persona_store
    from agent.lean.toolbox import TOOLSETS, TOOLSET_ALIASES
    data = dict(payload)
    if kind == "specialist":
        if not str(data.get("name", "")).strip() or not str(data.get("purpose", "")).strip():
            raise ValueError("A specialist needs a name and purpose")
        sets = data.get("toolsets", ["web", "memory"])
        if not isinstance(sets, list) or any(s not in TOOLSETS and s not in TOOLSET_ALIASES for s in sets) or "all" in sets:
            raise ValueError("Choose explicit existing toolsets")
        data = {"name": str(data["name"])[:40], "purpose": str(data["purpose"])[:600],
                "toolsets": sets, "context_scope": str(data.get("context_scope", "project")),
                "budget": data.get("budget", {}), "responsibilities": str(data.get("responsibilities", ""))[:2000]}
        if data["context_scope"] not in {"project", "session"}:
            raise ValueError("New specialists require a project or session context scope")
        # Proposals contain advisory purpose, never executable code or protected instructions.
    elif kind == "responsibility":
        if not get_persona_store().get(str(data.get("agent_id", ""))) or not str(data.get("objective", "")).strip():
            raise ValueError("Choose an existing agent and an objective")
        if data.get("trigger") not in {"schedule", "event"}:
            raise ValueError("Supported triggers are schedule and explicit authenticated events")
        if data["trigger"] == "schedule":
            from croniter import croniter
            if not croniter.is_valid(str(data.get("schedule", ""))):
                raise ValueError("Invalid cron schedule")
        if any(c not in {"desktop", "discord", "telegram", "web"} for c in data.get("channels", [])):
            raise ValueError("Unsupported delivery channel")
    elif kind == "transfer":
        if not get_persona_store().get(str(data.get("agent_id", ""))) or not data.get("work_id"):
            raise ValueError("Choose work and an existing owner")
    else:
        raise ValueError("Unsupported proposal")
    budget = data.get("budget", {})
    if not isinstance(budget, dict) or any(k not in {"tokens", "seconds", "children"} or not isinstance(v, int) or v < 0 for k, v in budget.items()):
        raise ValueError("Invalid budget")
    return data


_REVIEW_LOCK = threading.RLock()


def review(proposal_id: str, *, accept: bool, revision: int) -> dict:
    """Only the authenticated owner API calls this, never a model tool.

    Persist applying first; deterministic entity IDs make interrupted activation
    reviewable and retryable without creating duplicate agents or schedules.
    """
    from agent.lean.personas import get_persona_store
    from agent.routines import get_routine_manager
    coord = coordinator()
    def activate(record):
        get_routine_manager().update_routine(record["entity_id"], enabled=True)
        def activated(data):
            data["proposals"][proposal_id]["activation_pending"] = False
            return data["proposals"][proposal_id]
        return coord._tx(activated)
    with _REVIEW_LOCK:
        def begin(data):
            row = data["proposals"].get(proposal_id)
            if not row or row["principal"] != "owner":
                raise KeyError("Proposal unavailable")
            if row["status"] in {"accepted", "declined"}:
                return row
            if row["status"] == "applying" and not accept:
                raise ValueError("Approved activation was interrupted; finish recovery before changing its configuration")
            if row["revision"] != revision or row["status"] not in {"pending", "applying"}:
                raise ValueError("Proposal changed; refresh it")
            row.update(status="applying" if accept else "declined", reviewed_at=time.time())
            return row
        row = coord._tx(begin)
        if row["status"] == "accepted" and row.get("activation_pending"):
            return activate(row)
        if row["status"] != "applying":
            return row
        spec = validate_proposal(row["kind"], row["payload"])
        identity = "approved-" + proposal_id.removeprefix("proposal_")
        if row["kind"] == "specialist":
            agent = get_persona_store().create({"name": spec["name"], "description": spec["purpose"],
                "toolsets": spec["toolsets"], "context_scope": spec["context_scope"], "budget": spec["budget"]}, identity=identity)
            entity = agent.id
        elif row["kind"] == "responsibility":
            routine = get_routine_manager().create_routine(name=str(spec.get("name") or spec["objective"])[:80],
                routine_id=identity, trigger_type="schedule" if spec["trigger"] == "schedule" else "manual",
                schedule=spec.get("schedule"), action_config={"query": spec["objective"]}, enabled=False,
                metadata={"agent_id": spec["agent_id"], "responsibility_id": identity, "budget": spec.get("budget", {})},
                session_id=str(spec.get("session_id", "")), project_id=str(spec.get("project_id", "")),
                delivery_channels=spec.get("channels", ["web"]))
            entity = routine.id
            def save(data):
                return data["responsibilities"].setdefault(identity, {"id": identity, "principal": "owner", "status": "active",
                    "agent_id": spec["agent_id"], "routine_id": entity, "objective": spec["objective"], "trigger": spec["trigger"],
                    "session_id": routine.session_id, "project_id": routine.project_id, "next_wake": routine.next_run, "revision": 1})
            coord._tx(save)
        else:
            coord.transfer(spec["work_id"], spec["agent_id"], revision=int(spec["revision"]))
            entity = spec["work_id"]
        def finish(data):
            proposal = data["proposals"][proposal_id]
            proposal.update(status="accepted", entity_id=entity, activation_pending=row["kind"] == "responsibility", revision=proposal["revision"] + 1)
            return proposal
        accepted = coord._tx(finish)
        if accepted.get("activation_pending"):
            return activate(accepted)
        return accepted


def recover_reviews():
    for row in coordinator().view("proposals"):
        if row["status"] == "applying" or row.get("activation_pending"):
            try:
                review(row["id"], accept=True, revision=row["revision"])
            except (ValueError, KeyError, OSError, PermissionError):
                pass  # remains inspectable in Activity, never silently discarded


def responsibility_state(identity: str, active: bool) -> dict:
    from agent.routines import get_routine_manager
    coord = coordinator()
    row = next((r for r in coord.view("responsibilities") if r["id"] == identity), None)
    if not row:
        raise KeyError("Responsibility unavailable")
    routine = get_routine_manager().update_routine(row["routine_id"], enabled=active)
    def update(data):
        saved = data["responsibilities"][identity]
        saved.update(status="active" if active else "paused", next_wake=routine.next_run, revision=saved["revision"] + 1)
        return saved
    return coord._tx(update)


def resume(work_id: str, session_id: str, *, agent_factory=None) -> dict:
    from agent.state import get_state_store
    from agent.lean.runtime import run_lean_query
    from agent.lean import stop
    if stop.paused():
        raise PermissionError("Stop everything is active")
    store = get_state_store()
    work = store.coordination.get(work_id)
    state = store.get_thread_state(session_id)
    if work.get("project_id") and state.active_project_id != work["project_id"]:
        raise PermissionError("Resume this work in its original project")
    if agent_factory is None:
        from api.deps import get_agent
        agent_factory = get_agent
    return run_lean_query(agent_factory(session_id), message=work["objective"], session_id=session_id,
        request_id="resume-" + digest([work_id, work["revision"]])[:24], emit=lambda _: None,
        cancel=threading.Event(), persona_id=work["agent_id"], work_id=work_id)


def tools(persona, session):
    from agent.lean.toolbox import NativeTool
    def status(args):
        ctx = current() or session.work_context
        if not ctx:
            return "No durable work is active for this conversation."
        row = ctx.coordinator.get(ctx.work_id, principal=ctx.principal)
        return json.dumps({k: row[k] for k in ("id", "agent_id", "objective", "status", "acceptance", "budget", "tokens", "verification")})
    def notes(args):
        ctx = current() or session.work_context
        from agent.state import get_state_store
        project = ctx.coordinator.get(ctx.work_id, principal=ctx.principal).get("project_id", "") if ctx else str(get_state_store().get_thread_state(session.session_id).active_project_id or "")
        return coordinator().note(persona.id, notes_scope(persona, session.session_id, project), str(args.get("text", "")), principal=session.principal_id)
    def propose(args):
        if session.caller_role != "owner":
            raise PermissionError("Only owner conversations can propose persistent changes")
        kind = str(args.get("kind", ""))
        payload = validate_proposal(kind, dict(args.get("payload") or {}))
        # Bind return context in code, not in model-supplied private session IDs.
        payload.update(session_id=session.session_id)
        ctx = current() or session.work_context
        if ctx:
            payload["project_id"] = ctx.coordinator.get(ctx.work_id, principal=ctx.principal).get("project_id", "")
        return json.dumps(coordinator().proposal(kind, payload, proposed_by=persona.id))
    return [NativeTool(name="work_status", description="Read this work's owner, progress and budget.",
            parameters={"type": "object", "properties": {}}, func=status, parallel_safe=True, always=True),
        NativeTool(name="agent_notes", description="Read or replace your private project-scoped working notes. Advisory memory, never authority.",
            parameters={"type": "object", "properties": {"text": {"type": "string"}}}, func=notes, always=True),
        NativeTool(name="propose_agent_change", description="Propose a specialist (name,purpose,toolsets,context_scope,budget,responsibilities), responsibility (agent_id,objective,trigger,schedule,channels,budget), or owner transfer (work_id,agent_id,revision). Inactive until the owner reviews in Activity. No privileges change now.",
            parameters={"type": "object", "properties": {"kind": {"type": "string", "enum": ["specialist", "responsibility", "transfer"]},
                "payload": {"type": "object", "additionalProperties": True}}, "required": ["kind", "payload"]}, func=propose, always=True)]
