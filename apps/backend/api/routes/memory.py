"""Memory (list, edit, compact, doctor, Obsidian sync) and uploaded documents."""

from pathlib import Path
from io import BytesIO
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Body
from pydantic import BaseModel, Field
from loguru import logger

from config import config
from agent.state import get_state_store
from api.deps import _normalize_thread_id, _require_automation_project_scope, get_agent

router = APIRouter()


def get_document_store():
    agent = get_agent()
    if not bool(getattr(config, "document_rag_enabled", False)):
        return None
    return getattr(agent, "document_store", None)


def _extract_text_from_upload(filename: str, content_type: Optional[str], data: bytes) -> str:
    name = (filename or "").lower()
    ctype = (content_type or "").lower()
    if name.endswith(".pdf") or ctype == "application/pdf":
        try:
            from pypdf import PdfReader  # type: ignore
        except Exception as exc:
            raise HTTPException(status_code=503, detail="pypdf is required to parse PDF files") from exc
        try:
            reader = PdfReader(BytesIO(data))
            parts = []
            for page in reader.pages:
                text = page.extract_text() or ""
                if text.strip():
                    parts.append(text.strip())
            return "\n\n".join(parts).strip()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Failed to parse PDF: {exc}") from exc

    try:
        return data.decode("utf-8", errors="ignore").strip()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Unsupported text encoding: {exc}") from exc


class MemoryItem(BaseModel):
    id: str
    text: str
    timestamp: Optional[str] = None
    metadata: dict = Field(default_factory=dict)
    memory_type: Optional[str] = None
    pinned: Optional[bool] = None
    owner_id: str = ""
    scope: str = "account"
    project_id: str = ""
    source_session_id: str = ""
    source_execution_id: str = ""
    source_item_id: str = ""
    updated_at: Optional[str] = None
    index_state: str = "pending"
    supersedes: str = ""
    superseded_by: str = ""
    status: str = "active"
    checksum: str = ""
    version: int = 1
    # Curated Studio projection fields (same canonical records.json source)
    subject: Optional[str] = None
    confidence: Optional[float] = None
    explicit: Optional[bool] = None
    structured_attributes: Optional[dict] = None
    source_text: Optional[str] = None
    active: bool = True


class MemoryUpdateRequest(BaseModel):
    id: str
    text: Optional[str] = None
    memory_type: Optional[str] = None
    pinned: Optional[bool] = None
    thread_id: Optional[str] = None
    project_id: str = ""


class MemoryCompactRequest(BaseModel):
    thread_id: Optional[str] = None
    project_id: str = ""
    similarity: float = Field(default=0.94, ge=0.5, le=1.0)
    max_scan: int = Field(default=250, ge=10, le=1000)


class MemoryListResponse(BaseModel):
    items: List[MemoryItem]
    count: int
    use_faiss: bool


class MemoryDoctorResponse(BaseModel):
    ok: bool
    memory_count: int
    scanned: int
    use_faiss: bool
    auto_store_conversations: bool
    session_memory: Dict[str, Any] = Field(default_factory=dict)
    type_counts: Dict[str, int]
    pinned_count: int
    profile_fact_count: int
    missing_type_count: int
    duplicate_groups: List[Dict[str, Any]]
    warnings: List[str]
    recommendations: List[str]
    # Canonical projection audit (same records.json as Studio)
    active_semantic_samples: List[Dict[str, Any]] = Field(default_factory=list)
    superseded_count: int = 0
    session_only_count: int = 0
    pending_confirmation: Optional[Dict[str, Any]] = None


class MemoryDeleteRequest(BaseModel):
    ids: List[str]
    thread_id: Optional[str] = None
    project_id: str = ""


class DocumentItem(BaseModel):
    id: str
    filename: str
    chunks: int
    source: Optional[str] = None
    mime: Optional[str] = None
    timestamp: Optional[str] = None
    project_id: str = ""
    session_id: str = ""


class DocumentListResponse(BaseModel):
    items: List[DocumentItem]
    count: int
    enabled: bool


class DocumentDeleteRequest(BaseModel):
    ids: List[str]
    session_id: str
    project_id: str = ""


@router.post("/memory/compact")
async def compact_memory(
    request: Optional[MemoryCompactRequest] = Body(default=None),
    thread_id: Optional[str] = Query(default=None),
    project_id: str = Query(default=""),
    similarity: float = Query(default=0.94, ge=0.5, le=1.0),
    max_scan: int = Query(default=250, ge=10, le=1000),
):
    """Merge near-duplicate memory items within a thread by deleting redundant items.

    This is a lightweight compaction pass to reduce spam/duplicates.
    """
    try:
        import difflib

        direct_project_id = project_id if isinstance(project_id, str) else ""
        req = request or MemoryCompactRequest(
            thread_id=thread_id,
            project_id=direct_project_id,
            similarity=similarity,
            max_scan=max_scan,
        )
        session_id = str(req.thread_id or "").strip()
        scoped_project_id = _require_automation_project_scope(session_id, req.project_id)
        state = get_state_store().get_thread_state(session_id)
        agent = get_agent(req.thread_id)
        items = agent.memory.list_items(
            offset=0,
            limit=int(req.max_scan or 250),
            thread_id=session_id,
            project_id=scoped_project_id,
            project_path=str(state.project_path or ""),
            include_global=False,
        )
        if not items:
            return {"success": True, "deleted": 0, "kept": 0, "memory_count": 0}

        # Group by type.
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for it in items:
            meta = (it or {}).get("metadata") or {}
            if not isinstance(meta, dict):
                meta = {}
            mt = str(meta.get("type") or "note").strip().lower() or "note"
            groups.setdefault(mt, []).append(it)

        deleted_ids: List[str] = []
        kept_ids: set[str] = set()

        for mt, gitems in groups.items():
            # Newest first so we prefer keeping the newest canonical.
            gitems.sort(key=lambda x: (x.get("timestamp") or ""), reverse=True)
            canon: List[Dict[str, Any]] = []
            for it in gitems:
                iid = str((it or {}).get("id") or "").strip()
                txt = str((it or {}).get("text") or "").strip()
                if not iid or not txt:
                    continue
                meta = (it or {}).get("metadata") or {}
                if not isinstance(meta, dict):
                    meta = {}
                is_pinned = meta.get("pinned") is True

                merged = False
                for c in canon:
                    cid = str((c or {}).get("id") or "").strip()
                    ctxt = str((c or {}).get("text") or "").strip()
                    if not cid or not ctxt:
                        continue
                    ratio = difflib.SequenceMatcher(a=txt.lower(), b=ctxt.lower()).ratio()
                    if ratio >= float(req.similarity or 0.94):
                        deleted_ids.append(iid)
                        kept_ids.add(cid)
                        if is_pinned:
                            try:
                                agent.memory.update_item(
                                    cid,
                                    pinned=True,
                                    thread_id=session_id,
                                    project_id=scoped_project_id,
                                    include_global=False,
                                )
                            except Exception:
                                pass
                        merged = True
                        break
                if not merged:
                    canon.append(it)
                    kept_ids.add(iid)

        deleted = agent.memory.delete_items(
            deleted_ids,
            thread_id=session_id,
            project_id=scoped_project_id,
            include_global=False,
        )
        return {
            "success": True,
            "deleted": int(deleted),
            "kept": int(len(kept_ids)),
            "memory_count": agent.memory.count_items(
                thread_id=session_id,
                project_id=scoped_project_id,
                include_global=False,
            ),
        }
    except Exception as e:
        logger.error(f"Compact memory error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/documents", response_model=DocumentListResponse)
async def list_documents(
    session_id: Optional[str] = Query(default=None),
    project_id: Optional[str] = Query(default=None),
):
    store = get_document_store()
    if store is None:
        return DocumentListResponse(items=[], count=0, enabled=False)
    if session_id and project_id:
        state = get_state_store().get_thread_state(session_id)
        if str(state.active_project_id or "") != str(project_id or ""):
            raise HTTPException(status_code=409, detail="Document Project does not match the active Session Project")
    items = store.list_documents(project_id=str(project_id or ""), session_id=str(session_id or ""))
    return DocumentListResponse(items=[DocumentItem(**i) for i in items], count=len(items), enabled=True)


# ── Multi-Agent Orchestration Endpoints (v6.0.0) ────────────────

@router.post("/documents/upload", response_model=DocumentItem)
async def upload_document(
    file: UploadFile = File(...),
    source: Optional[str] = None,
    session_id: Optional[str] = None,
    project_id: Optional[str] = None,
):
    store = get_document_store()
    if store is None:
        raise HTTPException(status_code=503, detail="Document RAG is disabled")
    try:
        data = await file.read()
        max_bytes = int(getattr(config, "doc_upload_max_mb", 25) or 25) * 1024 * 1024
        if len(data) > max_bytes:
            raise HTTPException(status_code=413, detail="Upload too large")
        text = _extract_text_from_upload(file.filename or "document", file.content_type, data)
        if session_id and project_id:
            state = get_state_store().get_thread_state(session_id)
            if str(state.active_project_id or "") != str(project_id or ""):
                raise HTTPException(status_code=409, detail="Document Project does not match the active Session Project")
        meta = store.add_document(
            file.filename or "document",
            text,
            source=source or "",
            mime=file.content_type or "",
            project_id=str(project_id or ""),
            session_id=str(session_id or ""),
        )
        return DocumentItem(**meta)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Document upload failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/documents/delete")
async def delete_documents(request: DocumentDeleteRequest):
    store = get_document_store()
    if store is None:
        raise HTTPException(status_code=503, detail="Document RAG is disabled")
    state = get_state_store().get_thread_state(request.session_id)
    if request.project_id and str(state.active_project_id or "") != request.project_id:
        raise HTTPException(status_code=409, detail="Document Project does not match the active Session Project")
    if not request.project_id and state.active_project_id:
        raise HTTPException(status_code=409, detail="Project-bound Session requires a Project-scoped document mutation")
    try:
        deleted = store.delete_documents(
            request.ids,
            project_id=request.project_id,
            session_id=request.session_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"success": True, "deleted": deleted}


@router.post("/memory/rebuild-index")
async def memory_rebuild_index():
    """Rebuild FAISS from active canonical records only (forgotten stay out)."""
    agent = get_agent(None)
    if not hasattr(agent, "memory") or agent.memory is None:
        raise HTTPException(status_code=503, detail="Memory store unavailable")
    result = agent.memory.rebuild_faiss_from_canonical()
    return result


def _build_obsidian_sync_plan(session_id: str, project_id: str):
    if not bool(getattr(config, "obsidian_sync_enabled", False)):
        raise HTTPException(status_code=409, detail="Obsidian sync is disabled")
    vault = str(getattr(config, "obsidian_vault_path", "") or "").strip()
    if not vault:
        raise HTTPException(status_code=409, detail="Obsidian vault path is not configured")
    scoped_project_id = _require_automation_project_scope(session_id, project_id)
    state = get_state_store().get_thread_state(session_id)
    agent = get_agent(session_id)
    records = agent.memory.list_items(
        offset=0,
        limit=1000,
        thread_id=session_id,
        project_id=scoped_project_id,
        project_path=str(state.project_path or ""),
        include_global=False,
    )
    from agent.obsidian_sync import ObsidianMemorySync

    adapter = ObsidianMemorySync(Path(vault))
    plan = adapter.plan(records, project_id=scoped_project_id, session_id=session_id)
    return adapter, agent, state, plan


@router.get("/memory/obsidian/plan")
async def obsidian_sync_plan_api(
    session_id: str = Query(...),
    project_id: str = Query(default=""),
):
    """Return explicit imports, exports, deletions, and conflicts; make no changes."""
    _adapter, _session_agent, _state, plan = _build_obsidian_sync_plan(session_id, project_id)
    return plan.model_dump(mode="json")


@router.post("/memory/obsidian/apply")
async def obsidian_sync_apply_api(payload: Dict[str, Any] = Body(default_factory=dict)):
    """Apply only selected deterministic actions after fresh scope revalidation."""
    session_id = str(payload.get("session_id") or "").strip()
    project_id = str(payload.get("project_id") or "").strip()
    direction = str(payload.get("direction") or "").strip().lower()
    action_ids = [str(item) for item in (payload.get("action_ids") or []) if str(item)]
    if direction not in {"export", "import"} or not action_ids:
        raise HTTPException(status_code=422, detail="direction and action_ids are required")
    adapter, agent, state, plan = _build_obsidian_sync_plan(session_id, project_id)
    current_ids = {action.id for action in plan.actions}
    if any(action_id not in current_ids for action_id in action_ids):
        raise HTTPException(status_code=409, detail="Obsidian sync plan changed; review it again")
    try:
        if direction == "export":
            manifest = adapter.apply_exports(plan, action_ids)
        else:
            manifest = adapter.apply_imports(
                plan,
                action_ids,
                memory=agent.memory,
                project_path=str(state.project_path or ""),
            )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "manifest": manifest.model_dump(mode="json")}


def _resolve_memory_read_scope(
    thread_id: Optional[str],
    project_id: str = "",
) -> tuple[str, str, str]:
    """Read-only Studio/list scope — never requires a bound Project.

    Returns (session_id, project_id, project_path). Empty project_id lists
    account/global memories plus any Project memories only when a Project is
    active on the Session. Does not mutate memory stores.
    """
    session_id = _normalize_thread_id(thread_id)
    state = get_state_store().get_thread_state(session_id)
    active_project_id = str(getattr(state, "active_project_id", "") or "").strip()
    requested = str(project_id or "").strip()
    if requested and active_project_id and requested != active_project_id:
        raise HTTPException(
            status_code=409,
            detail="Session is not bound to the requested Project",
        )
    scoped_project_id = requested or active_project_id
    if scoped_project_id:
        from agent.projects import get_project_manager

        if get_project_manager().get_project(scoped_project_id) is None:
            # Detached / missing Project: still allow account memory, drop project filter.
            scoped_project_id = ""
    project_path = str(getattr(state, "project_path", "") or "").strip()
    return session_id, scoped_project_id, project_path


def _memory_item_from_payload(payload: dict) -> Optional[MemoryItem]:
    """Project one canonical record to API MemoryItem; skip corrupt rows without erasing."""
    try:
        meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        mt = str(meta.get("type") or "").strip() if isinstance(meta, dict) else ""
        pinned = meta.get("pinned") if isinstance(meta, dict) else None
        raw_id = str(payload.get("id") or "").strip()
        raw_text = str(payload.get("text") or "")
        if not raw_id:
            return None
        base = {k: v for k, v in payload.items() if k in MemoryItem.model_fields}
        base["id"] = raw_id
        base["text"] = raw_text
        if not isinstance(base.get("metadata"), dict):
            base["metadata"] = dict(meta) if isinstance(meta, dict) else {}
        mi = MemoryItem.model_validate(base)
        mi.memory_type = mt or str(meta.get("curator_type") or "") or None
        mi.pinned = bool(pinned) if pinned is not None else None
        mi.owner_id = str(meta.get("owner_id") or "")
        mi.scope = str(meta.get("scope") or "account")
        mi.project_id = str(meta.get("project_id") or payload.get("project_id") or "")
        mi.source_session_id = str(meta.get("source_session_id") or "")
        mi.source_execution_id = str(meta.get("source_execution_id") or "")
        mi.source_item_id = str(meta.get("source_item_id") or "")
        mi.index_state = str(meta.get("index_state") or "pending")
        mi.supersedes = str(meta.get("supersedes") or "")
        mi.superseded_by = str(meta.get("superseded_by") or "")
        mi.status = str(meta.get("status") or "active")
        mi.checksum = str(meta.get("checksum") or "")
        try:
            mi.version = int(meta.get("version") or 1)
        except Exception:
            mi.version = 1
        mi.subject = str(meta.get("subject") or "") or None
        conf = meta.get("confidence")
        try:
            mi.confidence = float(conf) if conf is not None and conf != "" else None
        except Exception:
            mi.confidence = None
        mi.explicit = bool(meta.get("explicit")) if meta.get("explicit") is not None else None
        attrs = meta.get("structured_attributes")
        mi.structured_attributes = dict(attrs) if isinstance(attrs, dict) else None
        mi.source_text = str(meta.get("source_text") or "") or None
        mi.active = True
        semantic = str(meta.get("semantic_text") or "").strip()
        if semantic:
            mi.text = semantic
        return mi
    except Exception as exc:
        logger.warning("Skipping corrupt memory projection (preserved on disk): {}", exc)
        return None


@router.get("/memory", response_model=MemoryListResponse)
async def list_memory(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
    thread_id: Optional[str] = Query(default=None),
    project_id: str = Query(default=""),
):
    """List memories for Studio. Read-only — never mutates canonical records or indexes."""
    try:
        session_id, scoped_project_id, project_path = _resolve_memory_read_scope(thread_id, project_id)
        agent = get_agent(thread_id)
        memory = getattr(agent, "memory", None)
        if memory is None:
            return MemoryListResponse(items=[], count=0, use_faiss=False)
        # Account/global always included; Project rows only when a Project is scoped.
        items = memory.list_items(
            offset=offset,
            limit=limit,
            thread_id=session_id,
            project_id=scoped_project_id,
            project_path=project_path,
            include_global=True,
        )
        out_items: List[MemoryItem] = []
        for i in items:
            payload = (i or {}) if isinstance(i, dict) else {}
            mi = _memory_item_from_payload(payload)
            if mi is not None:
                out_items.append(mi)
        count_fn = getattr(memory, "count_items", None)
        if callable(count_fn):
            count = int(
                count_fn(
                    thread_id=session_id,
                    project_id=scoped_project_id,
                    include_global=True,
                )
                or 0
            )
        else:
            count = len(out_items)
        return MemoryListResponse(
            items=out_items,
            count=count,
            use_faiss=bool(getattr(memory, "use_faiss", False)),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"List memory error: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "code": "memory_list_failed",
                "message": "Memory list failed. Canonical records were not modified.",
                "error": str(e),
            },
        ) from e


def _normalize_memory_audit_text(text: str) -> str:
    cleaned = " ".join(str(text or "").lower().split())
    return cleaned[:500]


def _build_memory_doctor_report(
    agent,
    thread_id: Optional[str],
    project_id: str = "",
    max_scan: int = 300,
) -> MemoryDoctorResponse:
    max_scan = max(10, min(int(max_scan or 300), 1000))
    memory = getattr(agent, "memory", None)
    if memory is None:
        return MemoryDoctorResponse(
            ok=False,
            memory_count=0,
            scanned=0,
            use_faiss=False,
            auto_store_conversations=bool(getattr(config, "memory_auto_store_conversations", False)),
            session_memory={},
            type_counts={},
            pinned_count=0,
            profile_fact_count=0,
            missing_type_count=0,
            duplicate_groups=[],
            warnings=["Memory manager is not initialized."],
            recommendations=["Restart the backend or check memory initialization logs."],
        )

    items = memory.list_items(
        offset=0,
        limit=max_scan,
        thread_id=thread_id,
        project_id=project_id,
        include_global=False,
    )
    type_counts: Dict[str, int] = {}
    pinned_count = 0
    missing_type_count = 0
    duplicate_map: Dict[str, list[dict[str, Any]]] = {}
    active_semantic_samples: List[Dict[str, Any]] = []

    for item in items:
        payload = item if isinstance(item, dict) else {}
        meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        text = str(meta.get("semantic_text") or payload.get("text") or "")
        mem_type = str(meta.get("curator_type") or meta.get("type") or "").strip() or "unknown"
        type_counts[mem_type] = type_counts.get(mem_type, 0) + 1
        if mem_type == "unknown":
            missing_type_count += 1
        if bool(meta.get("pinned")):
            pinned_count += 1
        if len(active_semantic_samples) < 12:
            active_semantic_samples.append(
                {
                    "id": str(payload.get("id") or ""),
                    "text": text[:240],
                    "type": mem_type,
                    "scope": str(meta.get("scope") or "account"),
                    "source_execution_id": str(meta.get("source_execution_id") or ""),
                    "index_state": str(meta.get("index_state") or "pending"),
                    "explicit": bool(meta.get("explicit")),
                    "confidence": meta.get("confidence"),
                    "active": True,
                    "supersedes": str(meta.get("supersedes") or ""),
                }
            )
        norm = _normalize_memory_audit_text(text)
        if len(norm) >= 24:
            duplicate_map.setdefault(norm, []).append(
                {
                    "id": str(payload.get("id") or ""),
                    "type": mem_type,
                    "preview": text[:180],
                    "timestamp": payload.get("timestamp"),
                }
            )

    superseded_count = 0
    try:
        for rec in (getattr(memory, "_records", None) or {}).values():
            if not memory._record_matches_scope(
                rec,
                project_id=project_id,
                thread_id=str(thread_id or ""),
                include_global=False,
            ):
                continue
            if not bool(rec.get("active", True)) and str(rec.get("supersedes") or rec.get("deleted_at") or ""):
                superseded_count += 1
            elif not bool(rec.get("active", True)):
                superseded_count += 1
    except Exception:
        superseded_count = 0

    session_only_count = 0
    pending_confirmation = None
    try:
        from agent.memory_curator import MemoryCurator

        cur = MemoryCurator(memory)
        sid = str(thread_id or "default")
        session_only_count = len(cur.list_session_only(sid))
        pending_confirmation = cur.get_pending_confirmation(sid)
        if pending_confirmation:
            pending_confirmation = {
                "id": pending_confirmation.get("id"),
                "status": pending_confirmation.get("status"),
                "candidate_count": len(pending_confirmation.get("candidates") or []),
            }
    except Exception:
        pass

    duplicate_groups = []
    for norm, group in duplicate_map.items():
        if len(group) > 1:
            duplicate_groups.append(
                {
                    "count": len(group),
                    "preview": group[0].get("preview", ""),
                    "items": group[:8],
                }
            )
    duplicate_groups.sort(key=lambda g: int(g.get("count") or 0), reverse=True)
    duplicate_groups = duplicate_groups[:12]

    profile_fact_count = int(type_counts.get("profile", 0))

    memory_count = memory.count_items(
        thread_id=thread_id,
        project_id=project_id,
        include_global=False,
    )
    auto_store = bool(getattr(config, "memory_auto_store_conversations", False))
    conversation_count = int(type_counts.get("conversation", 0))
    warnings: list[str] = []
    recommendations: list[str] = []

    if auto_store:
        warnings.append("Raw conversation auto-store is enabled.")
        recommendations.append("Keep raw conversation auto-store off unless explicitly debugging; prefer profile facts and curated memories.")
    if duplicate_groups:
        warnings.append(f"Found {len(duplicate_groups)} exact duplicate-looking memory group(s) in the scanned items.")
        recommendations.append("Run memory compaction or review duplicate groups before injecting more long-term memory.")
    if missing_type_count:
        warnings.append(f"{missing_type_count} scanned memory item(s) are missing a typed memory category.")
        recommendations.append("Backfill missing memory types so retrieval can distinguish profile, preference, project, contacts, and notes.")
    if conversation_count > max(20, len(items) // 2):
        warnings.append("Conversation memories dominate the scanned sample.")
        recommendations.append("Prefer searchable chat history plus curated durable memories instead of storing every conversation turn.")
    if profile_fact_count == 0:
        recommendations.append("Add deterministic profile facts for stable personal recall, such as name and core preferences.")
    if memory_count > 250:
        warnings.append("Memory count is high enough that retrieval quality and startup cost may degrade.")
        recommendations.append("Use memory doctor plus compaction to reduce stale or duplicated memories.")
    if not warnings:
        recommendations.append("Memory looks healthy in the scanned sample.")

    return MemoryDoctorResponse(
        ok=not bool(warnings),
        memory_count=memory_count,
        scanned=len(items),
        use_faiss=bool(getattr(memory, "use_faiss", False)),
        auto_store_conversations=auto_store,
        type_counts=type_counts,
        pinned_count=pinned_count,
        profile_fact_count=profile_fact_count,
        missing_type_count=missing_type_count,
        duplicate_groups=duplicate_groups,
        warnings=warnings,
        recommendations=recommendations,
        active_semantic_samples=active_semantic_samples,
        superseded_count=superseded_count,
        session_only_count=session_only_count,
        pending_confirmation=pending_confirmation,
    )


@router.get("/memory/doctor", response_model=MemoryDoctorResponse)
async def memory_doctor(
    thread_id: Optional[str] = Query(default=None),
    project_id: str = Query(default=""),
    max_scan: int = Query(default=300, ge=10, le=1000),
):
    """Read-only memory health report for duplicate/stale/untyped memory diagnosis."""
    try:
        session_id, scoped_project_id, _path = _resolve_memory_read_scope(thread_id, project_id)
        agent = get_agent(session_id)
        return _build_memory_doctor_report(
            agent,
            session_id,
            scoped_project_id,
            max_scan=max_scan,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Memory doctor error: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "code": "memory_doctor_failed",
                "message": "Memory doctor failed. Canonical records were not modified.",
                "error": str(e),
            },
        ) from e


@router.post("/memory/delete")
async def delete_memory(request: MemoryDeleteRequest):
    try:
        session_id = _normalize_thread_id(request.thread_id)
        scoped_project_id = _require_automation_project_scope(session_id, request.project_id)
        agent = get_agent(session_id)
        deleted = agent.memory.delete_items(
            request.ids,
            project_id=scoped_project_id,
            thread_id=session_id,
        )
        if deleted != len(set(request.ids)):
            raise HTTPException(status_code=409, detail="One or more memories are outside the active scope")
        return {
            "success": True,
            "deleted": deleted,
            "memory_count": agent.memory.count_items(
                thread_id=session_id,
                project_id=scoped_project_id,
            ),
        }
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Delete memory error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/memory/update")
async def update_memory(request: MemoryUpdateRequest):
    try:
        session_id = _normalize_thread_id(request.thread_id)
        scoped_project_id = _require_automation_project_scope(session_id, request.project_id)
        agent = get_agent(session_id)
        ok = agent.memory.update_item(
            request.id,
            text=request.text,
            memory_type=request.memory_type,
            pinned=request.pinned,
            project_id=scoped_project_id,
            thread_id=session_id,
        )
        if not ok:
            raise HTTPException(status_code=404, detail="Memory not found in active scope")
        return {
            "success": True,
            "memory_count": agent.memory.count_items(
                thread_id=session_id,
                project_id=scoped_project_id,
            ),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Update memory error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/memory/clear")
async def clear_memory(
    thread_id: Optional[str] = Query(default=None),
    project_id: str = Query(default=""),
):
    try:
        session_id = _normalize_thread_id(thread_id)
        scoped_project_id = _require_automation_project_scope(session_id, project_id)
        agent = get_agent(session_id)
        deleted = agent.memory.clear_scope(
            project_id=scoped_project_id,
            thread_id=session_id,
            include_global=False,
        )
        return {
            "success": True,
            "deleted": deleted,
            "memory_count": agent.memory.count_items(
                thread_id=session_id,
                project_id=scoped_project_id,
            ),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Clear memory error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
