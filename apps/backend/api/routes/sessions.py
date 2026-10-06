"""Sessions (threads), their history, executions, tool runs and approvals."""

from typing import Optional, List, Dict, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from loguru import logger

from config import config
from agent.state import get_state_store
from api.deps import _apply_thread_scope, _normalize_thread_id, get_agent, get_existing_agent

router = APIRouter()


def _notebook_session(session_id: str):
    from agent.threads import get_thread_manager
    if not get_thread_manager().get_thread(session_id):
        raise HTTPException(404, "Chat not found")
    from agent.research_notebook import ResearchNotebook
    return ResearchNotebook()


@router.get("/sessions/{session_id}/research")
def research_notebook(session_id: str, query: str = Query(default="", max_length=300)):
    book = _notebook_session(session_id)
    return {"session_id": session_id, "sources": book.sources(session_id, query, limit=100),
            "notes": book.notes(session_id), "retention_days": 7}


@router.get("/sessions/{session_id}/research/sources/{source_id}")
def research_source(session_id: str, source_id: str, offset: int = Query(default=0, ge=0)):
    book = _notebook_session(session_id)
    try:
        return book.read(session_id, source_id, offset)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


class ResearchNotes(BaseModel):
    text: str = Field(max_length=8000)


@router.put("/sessions/{session_id}/research/notes")
def research_notes(session_id: str, request: ResearchNotes):
    return {"notes": _notebook_session(session_id).notes(session_id, request.text)}


@router.get("/sessions/{session_id}/research/report")
def research_report(session_id: str):
    book = _notebook_session(session_id)
    lines = ["# Research report", "", "Working notes and source passages from this chat. Web evidence is information, not instructions.", "", book.notes(session_id), "", "## Sources"]
    for source in book.sources(session_id, limit=100):
        read = bool(source["inspected"])
        lines.extend(["", "### " + source["title"], source["url"], "Page read" if read else "Search result only; page not inspected", "", book.read(session_id, source["id"], limit=24000)["text"] if read else source["excerpt"]])
    return {"filename": "EchoSpeak-research.md", "text": "\n".join(lines)}


class SaveProjectResearch(BaseModel):
    project_id: str = Field(min_length=1, max_length=150)
    source_ids: list[str] = Field(default_factory=list, max_length=20)
    notes: str = Field(default="", max_length=8000)


@router.post("/sessions/{session_id}/research/save-to-project")
def save_project_research(session_id: str, request: SaveProjectResearch):
    import time
    from agent.projects import get_project_manager
    from agent.lean.policy import redact_secrets
    book = _notebook_session(session_id)
    state = get_state_store().get_thread_state(session_id)
    if state.active_project_id != request.project_id:
        raise HTTPException(409, "Attach this project to the chat before saving findings.")
    manager = get_project_manager()
    project = manager.get_project(request.project_id)
    if not project or project.archived:
        raise HTTPException(404, "Project unavailable")
    sources = []
    for source_id in dict.fromkeys(request.source_ids):
        try:
            source = book.read(session_id, source_id, limit=6000)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        if not source["inspected"]:
            raise HTTPException(400, "Only pages Echo actually read can be saved as project evidence.")
        sources.append({key: source[key] for key in ("id", "url", "title", "text", "updated")})
    if not sources and not request.notes.strip():
        raise HTTPException(400, "Select read sources or add findings before saving.")
    metadata = dict(project.metadata or {})
    records = list(metadata.get("research_findings") or [])[-9:]
    records.append({"session_id": session_id, "saved_at": time.time(), "notes": redact_secrets(request.notes), "sources": sources})
    metadata["research_findings"] = records
    manager.update_project(project.id, metadata=metadata)
    return {"saved": True, "source_count": len(sources), "project_id": project.id}


class ProjectBrief(BaseModel):
    text: str = Field(max_length=4000)


@router.put("/sessions/{session_id}/project-brief")
def project_brief(session_id: str, request: ProjectBrief):
    _notebook_session(session_id)
    from agent.projects import get_project_manager
    state = get_state_store().get_thread_state(session_id)
    project = get_project_manager().get_project(state.active_project_id or "")
    if not project or project.archived:
        raise HTTPException(409, "Attach a project to this chat first.")
    metadata = dict(project.metadata or {})
    metadata["brief"] = request.text
    get_project_manager().update_project(project.id, metadata=metadata)
    return {"project_id": project.id, "brief": request.text}

class ThreadSessionStateResponse(BaseModel):
    thread_id: str
    session_id: str = ""
    title: str = ""
    workspace_id: str = ""
    active_project_id: str = ""
    workspace_root: str = ""
    project_path: str = ""
    foreground_task_id: str = ""
    suspended_task_ids: List[str] = Field(default_factory=list)
    pending_approval_ids: List[str] = Field(default_factory=list)
    source_metadata: Dict[str, Any] = Field(default_factory=dict)
    semantic_schema_version: int = 0
    semantic_state_migrated_at: float = 0.0
    objective: str = ""
    current_subject: str = ""
    mode: str = "chat"
    phase: str = ""
    required_capabilities: List[str] = Field(default_factory=list)
    available_capabilities: List[str] = Field(default_factory=list)
    allowed_tool_names: List[str] = Field(default_factory=list)
    permissions: Dict[str, bool] = Field(default_factory=dict)
    constraints: List[str] = Field(default_factory=list)
    decisions: List[str] = Field(default_factory=list)
    completed_actions: List[Dict[str, Any]] = Field(default_factory=list)
    pending_actions: List[Dict[str, Any]] = Field(default_factory=list)
    failed_actions: List[Dict[str, Any]] = Field(default_factory=list)
    plan_steps: List[Dict[str, Any]] = Field(default_factory=list)
    retry_target: Dict[str, Any] = Field(default_factory=dict)
    last_tool_outcome: Dict[str, Any] = Field(default_factory=dict)
    operation_details: Dict[str, Any] = Field(default_factory=dict)
    continuity_notice: str = ""
    execution_status: str = "ready"
    safest_next_action: str = ""
    current_execution_id: str = ""
    active_turn_id: str = ""
    selected_model_id: str = ""
    model_binding: Optional[Dict[str, Any]] = None
    model_profile: Dict[str, Any] = Field(default_factory=dict)
    context_budget: Dict[str, Any] = Field(default_factory=dict)
    unfinished_workflow: Dict[str, Any] = Field(default_factory=dict)
    pending_approval_id: str = ""
    last_execution_id: str = ""
    last_trace_id: str = ""
    runtime_provider: str = ""
    ledger: List[Dict[str, Any]] = Field(default_factory=list)
    updated_at: float = 0.0


class ApprovalResponse(BaseModel):
    id: str
    thread_id: str
    session_id: str = "default"
    project_id: str = ""
    original_turn_id: str = ""
    tool_run_id: str = ""
    execution_id: Optional[str] = None
    task_run_id: str = ""
    requirement_id: str = ""
    attempt_id: str = ""
    task_run_revision: int = 0
    model_binding_revision: int = 0
    status: str
    tool: str
    kwargs: Dict[str, Any] = Field(default_factory=dict)
    original_input: str = ""
    preview: str = ""
    summary: str = ""
    risk_level: str = "safe"
    policy_flags: List[str] = Field(default_factory=list)
    permission_level: str = "modify"
    constraints: List[str] = Field(default_factory=list)
    policy_snapshot: Dict[str, Any] = Field(default_factory=dict)
    retry_count: int = 0
    execution_context: Dict[str, Any] = Field(default_factory=dict)
    action_id: str = ""
    plan_id: str = ""
    canonical_arguments_hash: str = ""
    required_capabilities: List[str] = Field(default_factory=list)
    session_permissions: Dict[str, bool] = Field(default_factory=dict)
    dry_run_available: bool = False
    source: str = "web"
    workspace_id: str = ""
    active_project_id: str = ""
    created_at: float
    updated_at: float
    decided_at: Optional[float] = None
    outcome_summary: str = ""


class ApprovalListResponse(BaseModel):
    items: List[ApprovalResponse]
    count: int


class ApprovalDecisionResponse(BaseModel):
    approval: ApprovalResponse
    success: bool
    response: str = ""
    execution_id: Optional[str] = None
    thread_state: Dict[str, Any] = Field(default_factory=dict)
    tool_run_id: str = ""


class ExecutionResponse(BaseModel):
    id: str
    request_id: str
    kind: str
    thread_id: str
    session_id: str = "default"
    project_id: str = ""
    source: str
    status: str
    query: str
    workspace_id: str = ""
    active_project_id: str = ""
    runtime_provider: str = ""
    model_id: str = ""
    model_snapshot: Dict[str, Any] = Field(default_factory=dict)
    context_budget: Dict[str, Any] = Field(default_factory=dict)
    intent: str = ""
    mode: str = "chat"
    phase: str = ""
    constraints: List[str] = Field(default_factory=list)
    verification: Dict[str, Any] = Field(default_factory=dict)
    terminal_status: str = "started"
    created_at: float
    updated_at: float
    completed_at: Optional[float] = None
    success: Optional[bool] = None
    response_preview: str = ""
    error: str = ""
    approvals: List[str] = Field(default_factory=list)
    tools_used: List[str] = Field(default_factory=list)
    tool_latencies_ms: List[Dict[str, Any]] = Field(default_factory=list)
    trace_id: Optional[str] = None
    evaluation: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ExecutionListResponse(BaseModel):
    items: List[ExecutionResponse]
    count: int


class HistoryResponse(BaseModel):
    """Conversation history + durable Turn timeline for page-refresh hydration."""
    history: list = Field(default_factory=list)
    count: int = 0
    session: Dict[str, Any] = Field(default_factory=dict)
    session_id: str = ""
    turns: List[Dict[str, Any]] = Field(default_factory=list)


class PendingActionResponse(BaseModel):
    has_pending: bool
    action: Optional[Dict[str, Any]] = None
    approval_id: Optional[str] = None
    risk_level: Optional[str] = None
    risk_color: Optional[str] = None
    policy_flags: List[str] = []
    session_permissions: Dict[str, bool] = {}
    dry_run_available: bool = False


@router.get("/pending-action", response_model=PendingActionResponse)
async def get_pending_action(thread_id: Optional[str] = Query(default=None)):
    """Get structured pending action info for confirmation UI with risk levels and session permissions."""
    try:
        store = get_state_store()
        pending_record = store.get_pending_approval(thread_id)
        pending = pending_record.model_dump() if pending_record else None

        if not pending:
            return PendingActionResponse(
                has_pending=False,
                action=None,
                approval_id=None,
                risk_level=None,
                risk_color=None,
                policy_flags=[],
                session_permissions={},
                dry_run_available=False,
            )

        tool_name = str(pending.get("tool") or "").strip()

        # Import tool metadata
        from agent.tools import TOOL_METADATA

        meta = TOOL_METADATA.get(tool_name, {})
        risk_level = meta.get("risk_level", "safe")
        policy_flags = meta.get("policy_flags", [])

        # Risk color mapping for UI
        risk_colors = {
            "safe": "#22c55e",      # green
            "moderate": "#f59e0b",  # amber
            "destructive": "#ef4444",  # red
        }
        risk_color = risk_colors.get(risk_level, "#6b7280")

        # Check dry-run availability (desktop automation tools support dry_run)
        dry_run_tools = {"desktop_click", "desktop_type_text", "desktop_activate_window", "desktop_send_hotkey"}
        dry_run_available = tool_name in dry_run_tools

        # Session permissions - what actions are allowed this session
        session_permissions = {
            "system_actions": bool(getattr(config, "enable_system_actions", False)),
            "file_write": bool(getattr(config, "allow_file_write", False)),
            "terminal": bool(getattr(config, "allow_terminal_commands", False)),
            "desktop": bool(getattr(config, "allow_desktop_automation", False)),
            "playwright": bool(getattr(config, "allow_playwright", False)),
        }

        return PendingActionResponse(
            has_pending=True,
            action=pending,
            approval_id=str(pending.get("id") or "") or None,
            risk_level=risk_level,
            risk_color=risk_color,
            policy_flags=policy_flags,
            session_permissions=session_permissions,
            dry_run_available=dry_run_available,
        )
    except Exception as e:
        logger.error(f"Pending action error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/threads/{thread_id}/state", response_model=ThreadSessionStateResponse)
async def get_thread_state(thread_id: str):
    store = get_state_store()
    return ThreadSessionStateResponse(**store.get_thread_state(thread_id).model_dump())


@router.get("/approvals", response_model=ApprovalListResponse)
async def list_approvals(
    thread_id: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    store = get_state_store()
    items = store.list_approvals(thread_id=thread_id, status=status, limit=limit)
    return ApprovalListResponse(items=[ApprovalResponse(**item.model_dump()) for item in items], count=len(items))


@router.post("/approvals/{approval_id}/confirm", response_model=ApprovalDecisionResponse)
async def confirm_approval(
    approval_id: str,
    expected_session_id: Optional[str] = Query(default=None),
):
    store = get_state_store()
    approval = store.get_approval(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    if expected_session_id and str(expected_session_id) != str(approval.thread_id):
        raise HTTPException(status_code=409, detail="Approval belongs to a different Session; nothing was executed")
    state = store.get_thread_state(approval.thread_id)
    if approval.status != "pending" or state.pending_approval_id != approval_id:
        raise HTTPException(status_code=409, detail="Approval is stale or is not the current pending action")
    agent = get_agent(approval.thread_id)
    response, success = agent.process_query(
        "confirm",
        include_memory=False,
        thread_id=approval.thread_id,
        requested_approval_id=approval_id,
    )
    updated = store.get_approval(approval_id)
    if updated is None:
        raise HTTPException(status_code=500, detail="Approval missing after confirm")
    thread_state = store.get_thread_state(approval.thread_id)
    try:
        from agent.skill_execution import (
            finalize_skill_executions_for_turn,
            record_skill_tool_outcome,
            resume_skill_executions_for_approval,
        )

        original_execution_id = str(approval.execution_id or approval.original_turn_id or "")
        continuation_id = str(thread_state.last_execution_id or "")
        if original_execution_id and continuation_id:
            resume_skill_executions_for_approval(
                original_execution_id,
                continuation_execution_id=continuation_id,
                approval_id=approval.id,
                state_store=store,
            )
            for run in store.list_tool_runs(continuation_id):
                record_skill_tool_outcome(store, run, owner_execution_id=original_execution_id)
            finalize_skill_executions_for_turn(
                original_execution_id,
                state_store=store,
                turn_success=bool(success),
            )
    except Exception as exc:
        logger.warning(f"Skill approval reconciliation failed: {exc}")
    return ApprovalDecisionResponse(
        approval=ApprovalResponse(**updated.model_dump()),
        success=bool(success),
        response=str(response or ""),
        execution_id=thread_state.last_execution_id or None,
        thread_state=thread_state.model_dump(),
        tool_run_id=str(updated.tool_run_id or ""),
    )


@router.post("/approvals/{approval_id}/cancel", response_model=ApprovalDecisionResponse)
async def cancel_approval(
    approval_id: str,
    expected_session_id: Optional[str] = Query(default=None),
):
    store = get_state_store()
    approval = store.get_approval(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    if expected_session_id and str(expected_session_id) != str(approval.thread_id):
        raise HTTPException(status_code=409, detail="Approval belongs to a different Session; nothing was canceled")
    state = store.get_thread_state(approval.thread_id)
    if approval.status != "pending" or state.pending_approval_id != approval_id:
        raise HTTPException(status_code=409, detail="Approval is stale or is not the current pending action")
    try:
        from agent.skill_execution import cancel_skill_execution, list_skill_executions_for_turn

        original_execution_id = str(approval.execution_id or approval.original_turn_id or "")
        for record in list_skill_executions_for_turn(original_execution_id):
            if record.status.value == "pending_approval":
                cancel_skill_execution(record.id, state_store=store)
    except Exception as exc:
        logger.warning(f"Skill cancellation projection failed: {exc}")
    updated = store.update_approval(approval_id, status="canceled", outcome_summary="Canceled by user")
    if updated is None:
        raise HTTPException(status_code=500, detail="Approval missing after cancel")
    thread_state = store.get_thread_state(approval.thread_id)
    return ApprovalDecisionResponse(
        approval=ApprovalResponse(**updated.model_dump()),
        success=True,
        response=f"Canceled: {updated.summary or updated.tool}.",
        execution_id=thread_state.last_execution_id or None,
        thread_state=thread_state.model_dump(),
    )


@router.get("/executions", response_model=ExecutionListResponse)
async def list_executions(
    thread_id: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    store = get_state_store()
    items = store.list_executions(thread_id=thread_id, limit=limit)
    return ExecutionListResponse(items=[ExecutionResponse(**item.model_dump()) for item in items], count=len(items))


@router.get("/executions/{execution_id}", response_model=ExecutionResponse)
async def get_execution(execution_id: str):
    store = get_state_store()
    execution = store.get_execution(execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="Execution not found")
    return ExecutionResponse(**execution.model_dump())


class ToolRunResponse(BaseModel):
    """Canonical ToolRun projection for Session/Execution/Project hydration."""
    id: str
    project_id: str = ""
    session_id: str = "default"
    turn_id: str
    item_id: str = ""
    tool_name: str
    action_id: str = ""
    approval_id: str = ""
    status: str = "started"
    canonical_arguments: Dict[str, Any] = Field(default_factory=dict)
    canonical_arguments_hash: str = ""
    outcome: Dict[str, Any] = Field(default_factory=dict)
    verification: Dict[str, Any] = Field(default_factory=dict)
    retry_of: str = ""
    parent_tool_run_id: str = ""
    has_children: bool = False
    created_at: float = 0.0
    updated_at: float = 0.0
    completed_at: Optional[float] = None


class ToolRunListResponse(BaseModel):
    items: List[ToolRunResponse]
    count: int
    session_id: str = ""
    execution_id: str = ""
    project_id: str = ""


def _project_tool_runs(
    *,
    session_id: str = "",
    execution_id: str = "",
    project_id: str = "",
    limit: int = 120,
) -> ToolRunListResponse:
    store = get_state_store()
    runs = store.query_tool_runs(
        session_id=session_id,
        execution_id=execution_id,
        project_id=project_id,
        limit=limit,
    )
    items = [ToolRunResponse(**store.project_tool_run(run)) for run in runs]
    return ToolRunListResponse(
        items=items,
        count=len(items),
        session_id=str(session_id or ""),
        execution_id=str(execution_id or ""),
        project_id=str(project_id or ""),
    )


@router.get("/tool-runs", response_model=ToolRunListResponse)
async def list_tool_runs(
    session_id: Optional[str] = Query(default=None, description="Session / thread id"),
    thread_id: Optional[str] = Query(default=None, description="Alias for session_id"),
    execution_id: Optional[str] = Query(default=None),
    project_id: Optional[str] = Query(default=None),
    limit: int = Query(default=120, ge=1, le=500),
):
    """Canonical ToolRun list for refresh/restart hydration.

    Filter by Session, Execution (Turn), and/or Project. Returns parent/child
    identity (retry_of), terminal status, errors, verification, and approval_id.
    """
    sid = str(session_id or thread_id or "").strip()
    eid = str(execution_id or "").strip()
    pid = str(project_id or "").strip()
    if not sid and not eid and not pid:
        raise HTTPException(
            status_code=422,
            detail="Provide session_id/thread_id, execution_id, and/or project_id",
        )
    return _project_tool_runs(session_id=sid, execution_id=eid, project_id=pid, limit=limit)


@router.get("/executions/{execution_id}/tool-runs", response_model=ToolRunListResponse)
async def list_execution_tool_runs(
    execution_id: str,
    limit: int = Query(default=120, ge=1, le=500),
):
    """ToolRuns for one Execution / Turn (alias of /tool-runs?execution_id=)."""
    store = get_state_store()
    execution = store.get_execution(execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="Execution not found")
    return _project_tool_runs(
        session_id=str(execution.thread_id or execution.session_id or ""),
        execution_id=execution_id,
        project_id=str(execution.project_id or execution.active_project_id or ""),
        limit=limit,
    )


# ── Thread Management (v6.0.0) ──────────────────────────────────────

class ThreadCreateRequest(BaseModel):
    title: str = Field(default="", description="Thread title")
    source: str = Field(default="web", description="Source: web, discord, telegram, whatsapp, api")
    workspace_id: str = Field(default="", description="Optional workspace ID")
    project_id: str = Field(default="", description="Optional containing Project")
    idempotency_key: str = Field(
        default="", max_length=128, description="Opaque key that makes creation retries idempotent"
    )

class ThreadUpdateRequest(BaseModel):
    title: Optional[str] = Field(default=None, description="New title")
    pinned: Optional[bool] = Field(default=None, description="Pin/unpin thread")
    archived: Optional[bool] = Field(default=None, description="Archive/unarchive thread")

class ThreadResponse(BaseModel):
    thread_id: str
    title: str = ""
    created_at: float = 0.0
    last_active_at: float = 0.0
    message_count: int = 0
    source: str = "web"
    workspace_id: str = ""
    pinned: bool = False
    archived: bool = False
    project_id: str = ""


def _thread_response(thread) -> ThreadResponse:
    payload = thread.to_dict()
    payload["project_id"] = get_state_store().get_thread_state(thread.thread_id).active_project_id
    return ThreadResponse(**payload)


@router.get("/threads")
async def list_threads(
    include_archived: bool = Query(default=False),
    source: Optional[str] = Query(default=None),
    limit: int = Query(default=50),
):
    """List conversation threads."""
    from agent.threads import get_thread_manager
    tm = get_thread_manager()
    threads = tm.list_threads(include_archived=include_archived, source=source, limit=limit)
    return [_thread_response(t) for t in threads]


@router.post("/threads", response_model=ThreadResponse)
async def create_thread(request: ThreadCreateRequest):
    """Create a new conversation thread."""
    from agent.threads import get_thread_manager
    tm = get_thread_manager()
    try:
        thread = tm.create_thread(
            title=request.title,
            source=request.source,
            workspace_id=request.workspace_id,
            idempotency_key=request.idempotency_key,
            idempotency_context=request.project_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if request.project_id:
        agent = get_agent(thread.thread_id)
        with agent._request_lock:
            _apply_thread_scope(agent, thread.thread_id)
            if not agent.activate_project(request.project_id):
                tm.delete_thread(thread.thread_id)
                raise HTTPException(status_code=404, detail="Project not found")
    return _thread_response(thread)


@router.get("/threads/{thread_id}", response_model=ThreadResponse)
async def get_thread(thread_id: str):
    """Get a conversation thread by ID."""
    from agent.threads import get_thread_manager
    tm = get_thread_manager()
    thread = tm.get_thread(thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail=f"Thread {thread_id} not found")
    return _thread_response(thread)


@router.patch("/threads/{thread_id}", response_model=ThreadResponse)
async def update_thread(thread_id: str, request: ThreadUpdateRequest):
    """Update a conversation thread."""
    from agent.threads import get_thread_manager
    tm = get_thread_manager()
    thread = tm.update_thread(
        thread_id=thread_id,
        title=request.title,
        pinned=request.pinned,
        archived=request.archived,
    )
    if not thread:
        raise HTTPException(status_code=404, detail=f"Thread {thread_id} not found")
    return _thread_response(thread)


class PromptBranchRequest(BaseModel):
    execution_id: str = Field(default="", max_length=200)
    client_request_id: str = Field(default="", max_length=200)


@router.post("/threads/{thread_id}/branch", response_model=ThreadResponse)
def branch_prompt(thread_id: str, request: PromptBranchRequest):
    if not request.execution_id and not request.client_request_id:
        raise HTTPException(422, "A prompt identity is required")
    agent = get_existing_agent(thread_id)
    lock = getattr(agent, "_request_lock", None)
    if lock and not lock.acquire(blocking=False):
        raise HTTPException(409, "This chat is busy. Stop it or wait before retrying.")
    try:
        from agent.chat_branches import branch_before_prompt
        return _thread_response(branch_before_prompt(thread_id, execution_id=request.execution_id,
                                                    request_id=request.client_request_id))
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    finally:
        if lock:
            lock.release()


@router.delete("/threads/{thread_id}")
async def delete_thread(thread_id: str):
    """Delete a conversation thread."""
    from agent.threads import get_thread_manager
    tm = get_thread_manager()
    deleted = tm.delete_thread(thread_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Thread {thread_id} not found")
    from agent.research_notebook import ResearchNotebook
    ResearchNotebook().clear(thread_id)
    from agent.query_journal import get_query_journal
    get_query_journal().clear(thread_id)
    return {"deleted": True, "thread_id": thread_id}


@router.get("/history", response_model=HistoryResponse)
def get_history(thread_id: Optional[str] = Query(default=None)):
    """
    Complete Session history for page refresh.

    Returns:
      - turns: durable Turn projections (messages, ToolRuns, research, approvals, verification)
      - history: legacy Human/Assistant strings (backward compatible)
    """
    try:
        store = get_state_store()
        session_id = _normalize_thread_id(thread_id)
        agent = get_existing_agent(thread_id)
        if agent is not None:
            with agent._request_lock:
                _apply_thread_scope(agent, thread_id)

        timeline = store.session_timeline(session_id, limit=80)
        turns = list(timeline.get("turns") or [])

        # Legacy string history from durable Turn messages (prefer durable over ephemeral agent memory).
        normalized_history: list[str] = []
        for turn in turns:
            for msg in list(turn.get("messages") or []):
                role = str(msg.get("role") or "").strip().lower()
                text = str(msg.get("text") or "").strip()
                if not text:
                    continue
                if role == "user":
                    normalized_history.append(f"Human: {text}")
                elif role == "assistant":
                    normalized_history.append(f"Assistant: {text}")

        # Fallback: agent conversation buffer when no durable turns yet.
        if not normalized_history and agent is not None:
            try:
                history = agent.get_history()
            except Exception:
                history = []

            def _history_content(item: Any) -> str:
                if isinstance(item, dict):
                    return str(item.get("content") or "")
                return str(item or "")

            def _history_role(item: Any) -> str:
                if isinstance(item, dict):
                    return str(item.get("role") or "").strip().lower()
                text = str(item or "")
                if text.startswith("Human:"):
                    return "human"
                if text.startswith("Assistant:"):
                    return "ai"
                return ""

            def _is_internal_background_turn(item: Any) -> bool:
                role = _history_role(item)
                if role != "human":
                    return False
                low = _history_content(item).lower()
                markers = [
                    "check your memory for any pending follow-ups",
                    "review your recent conversation memories",
                    "based on everything you know about the user, generate one brief",
                    "if something is overdue or coming up, prepare a brief notification",
                    "otherwise reply no_action",
                    "reply no_action",
                ]
                return any(marker in low for marker in markers)

            skip_next_ai = False
            for item in history:
                if _is_internal_background_turn(item):
                    skip_next_ai = True
                    continue
                role = _history_role(item)
                content = _history_content(item).strip()
                if not content:
                    continue
                if skip_next_ai and role == "ai":
                    skip_next_ai = False
                    continue
                if role == "human":
                    normalized_history.append(f"Human: {content}")
                elif role == "ai":
                    normalized_history.append(f"Assistant: {content}")
                else:
                    normalized_history.append(content)

        return HistoryResponse(
            history=normalized_history,
            count=len(normalized_history),
            session=dict(timeline.get("session") or {}),
            session_id=str(timeline.get("session_id") or session_id),
            turns=turns,
        )
    except Exception as e:
        logger.error(f"History error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
