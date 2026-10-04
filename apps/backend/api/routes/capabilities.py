"""What the app can do right now: capabilities, doctor, tool-calling diagnostics, skills and connections."""

from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from loguru import logger

from config import config
from agent.state import get_state_store
from api.deps import (
    _apply_thread_scope,
    _require_automation_project_scope,
    get_agent,
    get_existing_agent,
)

router = APIRouter()

# Base directory for relative path resolution
BASE_DIR = Path(__file__).resolve().parents[2]  # apps/backend


def _mcp_trust_summary(
    mcp_servers: Any,
    mcp_client_present: bool,
    mcp_tool_count: int,
    manager_status: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Summarize MCP availability without overstating configured-only capability.

    Configured ≠ available. Loaded tools come from a live tools/list, not from
    merely having MCP_SERVERS set. Optional manager_status adds per-server errors.
    """
    configured_count = len(mcp_servers) if isinstance(mcp_servers, dict) else 0
    if isinstance(manager_status, dict) and manager_status.get("configured_count") is not None:
        # Prefer live manager count (includes disabled/failed rows from init)
        try:
            configured_count = max(configured_count, int(manager_status.get("configured_count") or 0))
        except Exception:
            pass
    loaded_tools = int(mcp_tool_count or 0)
    if isinstance(manager_status, dict) and manager_status.get("loaded_tool_count") is not None:
        try:
            loaded_tools = max(loaded_tools, int(manager_status.get("loaded_tool_count") or 0))
        except Exception:
            pass
    mcp_available = bool(configured_count and mcp_client_present and loaded_tools > 0)
    warnings: List[str] = []
    if mcp_available:
        status = "available"
    elif configured_count and mcp_client_present and loaded_tools <= 0:
        status = "configured_no_tools"
        warnings.append(
            "MCP servers are configured and the MCP bridge is present, but no MCP tools are loaded yet."
        )
    elif configured_count and not mcp_client_present:
        status = "client_missing"
        warnings.append(
            "MCP servers are configured, but agent.mcp_client.py is missing, so MCP tools cannot load."
        )
    elif mcp_client_present:
        status = "not_configured"
    else:
        status = "not_configured"

    # Surface loud start failures (config ≠ available)
    if isinstance(manager_status, dict):
        for row in manager_status.get("servers") or []:
            if not isinstance(row, dict):
                continue
            err = str(row.get("last_error") or "").strip()
            running = bool(row.get("running"))
            if err and not running:
                name = str(row.get("name") or "server")
                warnings.append(f"MCP server '{name}' failed to load: {err}")
        if manager_status.get("last_error") and loaded_tools <= 0:
            le = str(manager_status.get("last_error") or "").strip()
            if le and not any(le in w for w in warnings):
                warnings.append(f"MCP last error: {le}")

    out: Dict[str, Any] = {
        "mcp_configured_count": configured_count,
        "mcp_tool_count": loaded_tools,
        "mcp_available_tool_count": loaded_tools if mcp_available else 0,
        "mcp_client_present": bool(mcp_client_present),
        "mcp_available": mcp_available,
        "mcp_status": status,
        "warnings": warnings,
        "client_version": (
            str(manager_status.get("client_version") or "official-python-sdk")
            if mcp_client_present and isinstance(manager_status, dict)
            else "official-python-sdk"
            if mcp_client_present
            else ""
        ),
    }
    if isinstance(manager_status, dict):
        out["mcp_running_count"] = int(manager_status.get("running_count") or 0)
        out["mcp_servers_detail"] = manager_status.get("servers") or []
    return out


class DoctorResponse(BaseModel):
    ok: bool
    report: Dict[str, Any]
    text: str


class CapabilitiesResponse(BaseModel):
    ok: bool
    provider: str
    workspace: Dict[str, Any]
    tools: Dict[str, Any]
    features: Dict[str, Any]
    skills: List[Dict[str, Any]] = []
    trust: Dict[str, Any] = Field(default_factory=dict)
    capability_registry: Dict[str, Any] = Field(default_factory=dict)
    thread_context: Dict[str, Any] = Field(default_factory=dict)


@router.get("/capabilities", response_model=CapabilitiesResponse)
async def capabilities(thread_id: Optional[str] = Query(default=None)):
    try:
        agent = get_agent(thread_id)
        # Bind the exact active Session Project before reporting tool readiness.
        # Without this, the shared agent keeps a stale skill-workspace ("chat")
        # and never activates the folder attached to this thread_id.
        _apply_thread_scope(agent, thread_id)
        report = agent.get_doctor_report() or {}
        # Skill-workspace TOOLS.txt is not a hard allowlist. Availability uses
        # registration + policy + Project scope (see coding readiness).
        allowlist = None
        allowset = None

        # Import tool metadata
        from agent.tools import TOOL_METADATA
        from agent.tool_registry import ToolRegistry

        items = []
        risk_counts: Dict[str, int] = {}
        origin_counts: Dict[str, int] = {}
        mcp_tool_count = 0
        mcp_servers = getattr(config, "mcp_servers", None) or {}
        mcp_client_present = bool((BASE_DIR / "agent" / "mcp_client.py").exists())
        manager_status: Dict[str, Any] = {}
        try:
            from agent.mcp_client import get_mcp_manager, is_mcp_client_present as _mcp_present

            mcp_client_present = bool(_mcp_present())
            manager_status = get_mcp_manager().status()
            mcp_tool_count = max(mcp_tool_count, int(manager_status.get("loaded_tool_count") or 0))
        except Exception:
            manager_status = {}

        # Canonical inventory: ToolRegistry is the single tool owner for capability
        # reporting. agent.tools / lc_tools may be subsets; never under-report
        # video/skill tools that are registered and executable via the invoke path.
        seen_tool_names: set = set()
        tool_names: list[str] = []
        try:
            tool_names = sorted(ToolRegistry.get_names())
        except Exception:
            tool_names = []
        for name in tool_names:
            if not name or name in seen_tool_names:
                continue
            seen_tool_names.add(name)
            allowed_by_workspace = True
            if allowset is not None:
                allowed_by_workspace = name in allowset
            entry = ToolRegistry.get(name)
            is_action = False
            try:
                is_action = bool(agent._is_action_tool(name))  # type: ignore[attr-defined]
            except Exception:
                is_action = bool(getattr(entry, "is_action", False)) if entry else False
            if entry is not None and getattr(entry, "is_action", False):
                is_action = True
            allowed_by_policy = True
            blocked_reason = ""
            blocked_by_policy_flags: List[str] = []
            # Prefer ToolRegistry policy_flags; fall back to TOOL_METADATA.
            meta = TOOL_METADATA.get(name, {})
            policy_flags = list(getattr(entry, "policy_flags", None) or meta.get("policy_flags") or [])
            if is_action:
                try:
                    allowed_by_policy = bool(agent._action_configured(name))  # type: ignore[attr-defined]
                except Exception:
                    allowed_by_policy = False
                if not allowed_by_policy:
                    blocked_reason = "Blocked by EchoSpeak role or configuration"
                    for flag in policy_flags:
                        if not bool(getattr(config, str(flag).lower(), False)):
                            blocked_by_policy_flags.append(flag)

            risk_level = str(
                (getattr(entry, "risk_level", None) if entry else None)
                or meta.get("risk_level")
                or "safe"
            )
            requires_confirmation = bool(
                is_action
                or meta.get("requires_confirmation", False)
            )
            category = str(getattr(entry, "category", "") or "")
            origin = "mcp" if category == "mcp" or name.startswith("mcp__") else "local"
            mcp_server = ""
            if origin == "mcp":
                mcp_tool_count += 1
                parts = name.split("__", 2)
                if len(parts) >= 3:
                    mcp_server = parts[1]
            if origin == "mcp":
                # Resolve config by sanitized or raw server key
                server_cfg = None
                if isinstance(mcp_servers, dict):
                    server_cfg = mcp_servers.get(mcp_server)
                    if server_cfg is None:
                        for sk, sv in mcp_servers.items():
                            safe = "".join(c if c.isalnum() or c == "_" else "_" for c in str(sk)).strip("_")
                            if safe == mcp_server:
                                server_cfg = sv
                                break
                if isinstance(server_cfg, dict):
                    trust_state = (
                        "capability_destructive"
                        if risk_level == "destructive"
                        else "capability_governed"
                        if is_action
                        else "capability_read"
                    )
                    transport = str(server_cfg.get("transport") or "stdio")
                else:
                    # Loaded tool without matching config key — infer from registry risk
                    if entry is not None and not entry.is_action:
                        trust_state = "capability_read"
                    else:
                        trust_state = (
                            "capability_destructive"
                            if risk_level == "destructive"
                            else "capability_governed"
                        )
                    transport = "stdio"
                if not mcp_client_present:
                    allowed_by_policy = False
                    trust_state = "client_missing"
                    blocked_reason = blocked_reason or "MCP client is missing; configured MCP servers are not available."
            else:
                trust_state = "built_in"
                transport = ""
            risk_counts[str(risk_level)] = risk_counts.get(str(risk_level), 0) + 1
            origin_counts[origin] = origin_counts.get(origin, 0) + 1

            # Get usage statistics
            try:
                from agent.tool_registry import ToolUsageStats
                usage = ToolUsageStats.get_stats(name)
            except Exception:
                usage = {"usage_count": 0, "error_count": 0, "last_used_at": None, "success_rate": None}

            allowed = bool(allowed_by_workspace and allowed_by_policy)
            items.append(
                {
                    "name": name,
                    "allowed": allowed,
                    "allowed_by_workspace": allowed_by_workspace,
                    "allowed_by_policy": allowed_by_policy,
                    "is_action": is_action,
                    "blocked_reason": blocked_reason,
                    "blocked_by_policy_flags": blocked_by_policy_flags,
                    "risk_level": risk_level,
                    "requires_confirmation": requires_confirmation,
                    "policy_flags": policy_flags,
                    "origin": origin,
                    "category": category or ("mcp" if origin == "mcp" else "local"),
                    "trust_state": trust_state,
                    "mcp_server": mcp_server or None,
                    "transport": transport or None,
                    "usage_count": usage["usage_count"],
                    "error_count": usage["error_count"],
                    "last_used_at": usage["last_used_at"],
                    "success_rate": usage["success_rate"],
                }
            )

        # Prefer live manager count over double-count from list walk
        if isinstance(manager_status, dict) and manager_status.get("loaded_tool_count") is not None:
            try:
                mcp_tool_count = max(
                    int(manager_status.get("loaded_tool_count") or 0),
                    sum(1 for it in items if it.get("origin") == "mcp"),
                )
            except Exception:
                pass

        tools = (report.get("tools") or {}) if isinstance(report.get("tools"), dict) else {}
        features = (report.get("features") or {}) if isinstance(report.get("features"), dict) else {}
        provider = str(report.get("provider") or {}).strip() if isinstance(report.get("provider"), str) else ""
        if not provider:
            provider = str(getattr(agent, "llm_provider", "") or "")

        scope = agent.project_scope_report(thread_id)
        # Annotate project-scoped tools with scope/policy readiness for the panel.
        project_read = {"project_status", "file_list", "file_read"}
        project_write = {"file_write", "file_mkdir", "file_move", "file_copy", "file_delete", "artifact_write", "notepad_write"}
        project_term = {"terminal_run"}
        for it in items:
            name = str(it.get("name") or "")
            if name in project_read:
                if not scope.get("project_attached"):
                    it["allowed"] = False
                    it["allowed_by_workspace"] = False
                    it["blocked_reason"] = it.get("blocked_reason") or "No Project attached to this Session."
                else:
                    it["allowed_by_workspace"] = True
                    it["allowed"] = bool(it.get("allowed_by_policy", True))
            elif name in project_write:
                if not scope.get("project_attached"):
                    it["allowed"] = False
                    it["allowed_by_workspace"] = False
                    it["blocked_reason"] = it.get("blocked_reason") or "No Project attached to this Session."
                elif not (scope.get("permissions") or {}).get("filesystem_write"):
                    it["allowed"] = False
                    it["allowed_by_policy"] = False
                    it["blocked_reason"] = it.get("blocked_reason") or "Write permission is disabled."
                else:
                    it["allowed_by_workspace"] = True
            elif name in project_term:
                if not scope.get("project_attached"):
                    it["allowed"] = False
                    it["allowed_by_workspace"] = False
                    it["blocked_reason"] = it.get("blocked_reason") or "No Project attached to this Session."
                elif not (scope.get("permissions") or {}).get("terminal"):
                    it["allowed"] = False
                    it["allowed_by_policy"] = False
                    it["blocked_reason"] = it.get("blocked_reason") or "Terminal permission is disabled."
                else:
                    it["allowed_by_workspace"] = True

        # Skills list: SkillsRegistry is the selection/executable owner; workspace
        # _active_skill_defs remain prompt projection. Surface both without forking.
        skills_list = []
        skills_dir = Path(getattr(config, "skills_dir", "") or "").expanduser()
        seen_skill_ids: set = set()
        try:
            from agent.skills_registry import SkillsRegistry

            SkillsRegistry.refresh()
            for man in SkillsRegistry.list_manifests() or []:
                sid = str(getattr(man, "id", "") or "").strip()
                if not sid or sid in seen_skill_ids:
                    continue
                seen_skill_ids.add(sid)
                skills_list.append(
                    {
                        "id": sid,
                        "name": str(getattr(man, "name", "") or sid),
                        "description": str(getattr(man, "description", "") or "")[:100],
                        "status": str(getattr(getattr(man, "status", None), "value", getattr(man, "status", "")) or ""),
                        "executable": bool(getattr(man, "executable", False)),
                        "origin": str(getattr(getattr(man, "origin", None), "value", getattr(man, "origin", "")) or ""),
                        "has_tools": bool(getattr(man, "required_tools", None) or getattr(man, "tools_reachable", None)),
                        "has_plugin": False,
                    }
                )
        except Exception:
            pass
        for skill_def in getattr(agent, "_active_skill_defs", []):
            sid = str(getattr(skill_def, "id", "") or "").strip()
            if not sid or sid in seen_skill_ids:
                continue
            seen_skill_ids.add(sid)
            skill_path = skills_dir / sid
            skills_list.append(
                {
                    "id": sid,
                    "name": skill_def.name,
                    "description": skill_def.description[:100] if skill_def.description else "",
                    "status": "workspace_active",
                    "executable": False,
                    "origin": "workspace",
                    "has_tools": (skill_path / "tools.py").exists() if skill_path.exists() else False,
                    "has_plugin": (skill_path / "plugin.py").exists() if skill_path.exists() else False,
                }
            )

        mcp_summary = _mcp_trust_summary(
            mcp_servers,
            mcp_client_present,
            mcp_tool_count,
            manager_status=manager_status or None,
        )

        return CapabilitiesResponse(
            ok=bool(report.get("ok", True)),
            provider=str(getattr(agent, "llm_provider", "") or ""),
            workspace=scope,
            tools={"count": len(items), "items": items, "allowlist": tools.get("allowlist")},
            features=features,
            skills=skills_list,
            capability_registry=agent._capability_registry(),
            thread_context=get_state_store().get_thread_state(thread_id).model_dump(),
            trust={
                "risk_counts": risk_counts,
                "origin_counts": origin_counts,
                **mcp_summary,
                "recommendations": [
                    "Treat local/MCP tools as executable capability. Keep exact commands, risk, and confirmation visible before use.",
                    "Prefer built-in read-only tools for inspection; require explicit approval for writes, terminal commands, desktop actions, and MCP actions.",
                    "Project scope and permissions are independent of chat/research/coding interaction mode.",
                ],
            },
        )
    except Exception as e:
        logger.error(f"Capabilities error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/doctor", response_model=DoctorResponse)
async def doctor(thread_id: Optional[str] = Query(default=None)):
    try:
        agent = get_agent(thread_id)
        report = agent.get_doctor_report()
        text = ""
        try:
            text = str(agent.format_doctor_report(report) or "")
        except Exception:
            text = ""
        return DoctorResponse(ok=bool(report.get("ok")), report=report, text=text)
    except Exception as e:
        logger.error(f"Doctor error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/skills/status")
async def skills_status_api():
    """Truthful skill executable classification (prompt-only never marked executable)."""
    from agent.skill_status_audit import audit_all_skills

    rows = audit_all_skills(
        available_capabilities={"approvals", "research"},
        available_artifacts=set(),
    )
    return {
        "items": rows,
        "count": len(rows),
        "executable_ids": [r["id"] for r in rows if r.get("executable")],
        "prompt_only_ids": [r["id"] for r in rows if r.get("status") == "prompt_only"],
        "blocked_ids": [
            r["id"]
            for r in rows
            if str(r.get("status") or "").startswith("blocked") or r.get("status") in {"disabled", "invalid", "deprecated"}
        ],
    }


@router.get("/connections")
async def list_connections_api(
    session_id: str = Query(...),
    project_id: str = Query(default=""),
):
    """Secret-free Connection capabilities in the active exact scope."""
    from agent.connection_lifecycle import get_connection_lifecycle_service
    from agent.connections import get_connection_registry

    scoped_project_id = _require_automation_project_scope(session_id, project_id)
    get_connection_lifecycle_service().migrate_legacy_settings(config)
    rows = get_connection_registry().list(
        project_id=scoped_project_id,
        session_id=session_id,
    )
    return {"items": [row.model_dump(mode="json") for row in rows], "count": len(rows)}


class ConnectionAuthorizationRequest(BaseModel):
    provider_id: str
    session_id: str
    project_id: str = ""
    display_name: str = ""
    configuration: dict[str, Any] = Field(default_factory=dict)
    credentials: dict[str, Any] = Field(default_factory=dict)
    allow_global: bool = False


class ConnectionRevisionRequest(BaseModel):
    session_id: str
    project_id: str = ""
    expected_revision: int = Field(ge=1)


class ConnectionCapabilityUpdateRequest(ConnectionRevisionRequest):
    enabled: bool


class ConnectionAuthorizationCallbackRequest(BaseModel):
    provider_id: str
    transaction_id: str
    code: str = ""
    state: str = ""
    error: str = ""


def _connection_api_error(exc: Exception) -> HTTPException:
    from agent.connections import (
        ConnectionConflictError,
        ConnectionScopeError,
    )

    if isinstance(exc, ConnectionConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ConnectionScopeError):
        return HTTPException(status_code=403, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


def _require_connection_scope(
    connection_id: str,
    *,
    session_id: str,
    project_id: str,
):
    from agent.connections import ConnectionScopeError, get_connection_registry

    scoped_project_id = _require_automation_project_scope(session_id, project_id)
    record = get_connection_registry().get(
        connection_id,
        project_id=scoped_project_id,
        session_id=session_id,
    )
    if record is None:
        raise ConnectionScopeError("Connection not found in the active Project and Session")
    return scoped_project_id, record


@router.get("/connections/catalog")
async def connection_catalog_api(
    session_id: str = Query(...),
    project_id: str = Query(default=""),
):
    """Consumer catalog joined to secret-free canonical Connection state."""
    from agent.connection_lifecycle import get_connection_lifecycle_service

    service = get_connection_lifecycle_service()
    service.migrate_legacy_settings(config)
    scoped_project_id = ""
    if str(project_id or "").strip():
        scoped_project_id = _require_automation_project_scope(session_id, project_id)
    else:
        from agent.threads import get_thread_manager

        if get_thread_manager().get_thread(str(session_id or "").strip()) is None:
            raise HTTPException(status_code=404, detail="Session not found")
    items = service.catalog(project_id=scoped_project_id, session_id=session_id)
    return {"items": items, "count": len(items)}


@router.post("/connections/authorize")
async def begin_connection_authorization_api(request: ConnectionAuthorizationRequest):
    """Begin one explicit setup transaction; credentials never enter a projection."""
    from agent.connection_lifecycle import get_connection_lifecycle_service

    try:
        scoped_project_id = _require_automation_project_scope(
            request.session_id,
            request.project_id,
        )
        result = get_connection_lifecycle_service().begin(
            provider_id=request.provider_id,
            project_id=scoped_project_id,
            session_id=request.session_id,
            display_name=request.display_name,
            configuration=request.configuration,
            credentials=request.credentials,
            allow_global=request.allow_global,
        )
        return result.model_dump(mode="json")
    except Exception as exc:
        raise _connection_api_error(exc) from exc


@router.post("/connections/authorization/callback")
async def complete_connection_authorization_api(
    _request: ConnectionAuthorizationCallbackRequest,
):
    """Reserved provider callback boundary.

    No generic callback is accepted because OAuth code exchange, redirect and
    state validation belong to a concrete provider adapter. This endpoint is
    deliberately fail-closed until that adapter owns the transaction.
    """
    raise HTTPException(
        status_code=501,
        detail="This provider OAuth callback adapter is not installed; no Connection state changed",
    )


@router.post("/connections/{connection_id}/probe")
async def probe_connection_api(
    connection_id: str,
    request: ConnectionRevisionRequest,
):
    from agent.connection_lifecycle import get_connection_lifecycle_service
    from agent.connections import get_connection_registry

    try:
        _require_connection_scope(
            connection_id,
            session_id=request.session_id,
            project_id=request.project_id,
        )
        record = get_connection_lifecycle_service().probe(
            connection_id,
            expected_revision=request.expected_revision,
        )
        return {"connection": get_connection_registry()._project(record).model_dump(mode="json")}
    except Exception as exc:
        raise _connection_api_error(exc) from exc


@router.put("/connections/{connection_id}/capabilities/{capability_id}")
async def update_connection_capability_api(
    connection_id: str,
    capability_id: str,
    request: ConnectionCapabilityUpdateRequest,
):
    from agent.connection_lifecycle import get_connection_lifecycle_service
    from agent.connections import get_connection_registry

    try:
        _require_connection_scope(
            connection_id,
            session_id=request.session_id,
            project_id=request.project_id,
        )
        record = get_connection_lifecycle_service().set_capability(
            connection_id,
            capability_id,
            expected_revision=request.expected_revision,
            enabled=request.enabled,
        )
        return {"connection": get_connection_registry()._project(record).model_dump(mode="json")}
    except Exception as exc:
        raise _connection_api_error(exc) from exc


@router.post("/connections/{connection_id}/reconnect")
async def reconnect_connection_api(
    connection_id: str,
    request: ConnectionRevisionRequest,
):
    from agent.connection_lifecycle import get_connection_lifecycle_service
    from agent.connections import get_connection_registry

    try:
        _require_connection_scope(
            connection_id,
            session_id=request.session_id,
            project_id=request.project_id,
        )
        record = get_connection_lifecycle_service().reconnect(
            connection_id,
            expected_revision=request.expected_revision,
        )
        return {"connection": get_connection_registry()._project(record).model_dump(mode="json")}
    except Exception as exc:
        raise _connection_api_error(exc) from exc


@router.post("/connections/{connection_id}/disable")
async def disable_connection_api(
    connection_id: str,
    request: ConnectionRevisionRequest,
):
    from agent.connection_lifecycle import get_connection_lifecycle_service
    from agent.connections import get_connection_registry

    try:
        _require_connection_scope(
            connection_id,
            session_id=request.session_id,
            project_id=request.project_id,
        )
        record = get_connection_lifecycle_service().disable(
            connection_id,
            expected_revision=request.expected_revision,
        )
        return {"connection": get_connection_registry()._project(record).model_dump(mode="json")}
    except Exception as exc:
        raise _connection_api_error(exc) from exc


@router.delete("/connections/{connection_id}")
async def disconnect_connection_api(
    connection_id: str,
    session_id: str = Query(...),
    project_id: str = Query(default=""),
    expected_revision: int = Query(..., ge=1),
):
    from agent.connection_lifecycle import get_connection_lifecycle_service

    try:
        _require_connection_scope(
            connection_id,
            session_id=session_id,
            project_id=project_id,
        )
        removed = get_connection_lifecycle_service().disconnect(
            connection_id,
            expected_revision=expected_revision,
        )
        return {"disconnected": True, "connection_id": removed.id}
    except Exception as exc:
        raise _connection_api_error(exc) from exc


@router.get("/diagnostics/tool-calling")
async def tool_calling_diagnostics_api(thread_id: Optional[str] = Query(default=None)):
    """Honest provider tool-calling capability matrix for operators."""
    agent = get_existing_agent(thread_id) or get_agent(thread_id)
    diag = agent._tool_calling_diagnostics() if hasattr(agent, "_tool_calling_diagnostics") else {}
    provider = str(diag.get("provider") or getattr(agent, "llm_provider", ""))
    disabled = bool(diag.get("disable_native_tool_calling"))
    native_supported = bool(diag.get("native_tool_calling_supported"))
    native_enabled = bool(diag.get("native_tool_calling_enabled")) and not disabled
    matrix = {
        "provider": provider,
        "execution_loop": "lean",
        "native_tool_calls": native_enabled,
        "native_tool_calls_supported": native_supported,
        "strict_agent_decision_validation": True,
        "deterministic_direct_tool_fallback": False,
        "printed_tool_syntax_executable": False,
        "lmstudio_tool_calling_flag": bool(diag.get("lmstudio_tool_calling")),
        "disable_native_tool_calling": disabled,
        "notes": (
            "Native calls and bounded structured decisions enter the same "
            "ToolRun, approval, authority, and completion boundaries."
        ),
    }
    return {"diagnostics": diag, "capability_matrix": matrix, "mode_label": agent._tool_calling_mode_label()}
