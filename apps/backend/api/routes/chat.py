"""Chat turns: /query, /query/stream, cancel, the follow-up queue and stream replay."""

import json
import queue
import threading
import time
import uuid
import hashlib
from typing import Optional, Any, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from loguru import logger

import anyio

from config import config
from agent.state import get_state_store
from api.deps import (
    _ACTIVE_QUERY_CANCELLATIONS,
    _ACTIVE_QUERY_CANCEL_LOCK,
    _metric_inc,
    _normalize_thread_id,
    _register_query_cancellation,
    _release_query_cancellation,
    _safe_stream_failure,
    _start_agent_thread,
    get_agent,
)

router = APIRouter()


class _EventSink:
    """Collects a lean turn's events for the non-streaming /query response."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def _put(self, event: dict[str, Any]) -> None:
        self.events.append(event)


class QueryRequest(BaseModel):
    """Request model for query endpoint."""
    message: str = Field(..., description="User message to process", max_length=50000)
    include_memory: bool = Field(default=True, description="Include conversation memory")
    thread_id: Optional[str] = Field(default=None, description="Conversation Session id")
    workspace: Optional[str] = Field(default=None, description="Optional workspace/mode override (ex: auto|chat|coding|research)")
    thinking_enabled: bool = Field(
        default=True,
        description="Request provider-native reasoning controls when the selected provider supports them",
    )
    reasoning_effort: Literal[
        "minimal", "low", "medium", "high", "extra_high", "max", "ultra"
    ] = Field(default="medium")
    client_request_id: Optional[str] = Field(
        default=None,
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
        description="Client-owned cancellation identity for this exact Turn",
    )
    transport: Literal["chat", "voice"] = Field(
        default="chat",
        description="User-input transport only; it never changes semantic or execution authority",
    )
    voice_turn_id: Optional[str] = Field(
        default=None,
        max_length=200,
        pattern=r"^[A-Za-z0-9._:-]+$",
        description="Durable local Voice transport turn whose final transcript equals message",
    )
    agent_id: Optional[str] = Field(
        default=None,
        max_length=80,
        description="Lean runtime: which agent persona answers in a direct chat (default Echo)",
    )


class QueryResponse(BaseModel):
    """Response model for query endpoint."""
    response: str
    success: bool
    memory_count: int
    request_id: Optional[str] = None
    doc_sources: Optional[list] = None
    research: Optional[list[dict[str, Any]]] = None
    execution_id: Optional[str] = None
    trace_id: Optional[str] = None
    thread_state: Optional[dict[str, Any]] = None
    voice_turn_id: Optional[str] = None


def _prepare_query_transport(request: QueryRequest, request_id: str) -> str:
    """Validate user-input transport without creating another intent router."""

    voice_turn_id = str(request.voice_turn_id or "").strip()
    if request.transport != "voice":
        if voice_turn_id:
            raise HTTPException(status_code=400, detail="voice_turn_id requires the Voice transport")
        return "web"
    if not voice_turn_id:
        raise HTTPException(status_code=400, detail="Voice transport requires a durable voice_turn_id")
    try:
        from agent.voice_transport import prepare_voice_turn_submission

        prepare_voice_turn_submission(
            voice_turn_id,
            session_id=_normalize_thread_id(request.thread_id),
            request_id=request_id,
            transcript=request.message,
        )
        from agent.live_voice import bind_request
        bind_request(voice_turn_id, _normalize_thread_id(request.thread_id), request_id)
    except Exception as exc:
        from agent.voice_transport import VoiceTransportError

        if isinstance(exc, VoiceTransportError):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise
    return "voice"


class QueryCancelRequest(BaseModel):
    request_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    thread_id: str = Field(min_length=1, max_length=200)
    execution_id: str = Field(default="", max_length=128, pattern=r"^[A-Za-z0-9._:-]*$")
    voice_turn_id: Optional[str] = Field(default=None, max_length=200, pattern=r"^[A-Za-z0-9._:-]+$")
    voice_transcript: Optional[str] = Field(default=None, max_length=10000)


class QueryQueueRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=50000)
    client_request_id: Optional[str] = Field(default=None)


@router.post("/query/queue")
async def queue_query(request: QueryQueueRequest):
    """Queue a follow-up prompt to run after the active turn finishes."""
    thread_id = _normalize_thread_id(request.thread_id)
    store = get_state_store()
    item = store.enqueue_turn(
        thread_id,
        message=request.message,
        client_request_id=request.client_request_id or str(uuid.uuid4()),
    )
    queue_length = len(store.list_queued_turns(thread_id))
    return {
        "queued": True,
        "thread_id": thread_id,
        "queue_length": queue_length,
        "client_request_id": item["client_request_id"],
    }


@router.get("/query/queue")
async def get_queued_queries(thread_id: str):
    """List pending queued turns for a Session."""
    key = _normalize_thread_id(thread_id)
    items = get_state_store().list_queued_turns(key)
    return {"thread_id": key, "queue": items}


@router.post("/query/queue/claim")
async def claim_queued_query(thread_id: str):
    """Claim one durable follow-up only after the Session has no active Turn."""
    key = _normalize_thread_id(thread_id)
    with _ACTIVE_QUERY_CANCEL_LOCK:
        session_active = any(owner == key for owner, _event, _execution in _ACTIVE_QUERY_CANCELLATIONS.values())
    if session_active:
        raise HTTPException(status_code=409, detail="This Session still has an active Turn.")
    item = get_state_store().claim_queued_turn(key)
    return {
        "thread_id": key,
        "claimed": item is not None,
        "item": item,
        "queue_length": len(get_state_store().list_queued_turns(key)),
    }


@router.post("/query/cancel")
async def cancel_query(request: QueryCancelRequest):
    """Cancel one exact request; never infer by newest Session activity."""
    with _ACTIVE_QUERY_CANCEL_LOCK:
        active = _ACTIVE_QUERY_CANCELLATIONS.get(request.request_id)
    if active is None:
        return {"cancelled": False, "request_id": request.request_id, "reason": "not_active"}
    owner_session, event, execution_id = active
    if owner_session != _normalize_thread_id(request.thread_id):
        raise HTTPException(status_code=409, detail="Cancellation request belongs to a different Session")
    if request.execution_id and execution_id and request.execution_id != execution_id:
        raise HTTPException(status_code=409, detail="Cancellation request belongs to a different Execution")
    if request.voice_turn_id:
        if not str(request.voice_transcript or "").strip():
            raise HTTPException(status_code=400, detail="Voice cancellation requires its exact final transcript")
        try:
            from agent.voice_transport import (
                bind_voice_turn_submission,
                cancel_voice_playback,
                prepare_voice_turn_submission,
            )

            prepare_voice_turn_submission(
                request.voice_turn_id,
                session_id=owner_session,
                request_id=request.request_id,
                transcript=str(request.voice_transcript or ""),
            )
            if execution_id:
                execution = get_state_store().get_execution(execution_id)
                if execution is None or execution.session_id != owner_session:
                    raise HTTPException(status_code=409, detail="Voice cancellation Execution is unavailable")
                bind_voice_turn_submission(
                    request.voice_turn_id,
                    session_id=owner_session,
                    request_id=request.request_id,
                    execution_id=execution.id,
                    task_run_id=str(execution.task_run_id or ""),
                    query_completed=True,
                )
            cancel_voice_playback(request.voice_turn_id, session_id=owner_session)
        except HTTPException:
            raise
        except Exception as exc:
            from agent.voice_transport import VoiceTransportError

            if isinstance(exc, VoiceTransportError):
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            raise
    event.set()
    from agent.query_journal import get_query_journal
    get_query_journal().finish(request.request_id, "cancelled")
    return {
        "cancelled": True,
        "request_id": request.request_id,
        "execution_id": execution_id,
        "thread_id": owner_session,
    }


@router.post("/query", response_model=QueryResponse)
async def query(request: QueryRequest):
    """
    Process a user query through the agent.

    Args:
        request: Query request with message.

    Returns:
        Agent response.
    """
    request_id = str(request.client_request_id or "").strip() or str(uuid.uuid4())
    cancel_event = threading.Event()
    _register_query_cancellation(
        request_id, request.thread_id or "default", cancel_event
    )
    _metric_inc("requests", 1)
    try:
        source = _prepare_query_transport(request, request_id)
        logger.debug(
            "Query request_id={} thread_id={} include_memory={} msg_len={}",
            request_id,
            _normalize_thread_id(request.thread_id),
            bool(request.include_memory),
            len((request.message or "")),
        )
        # The explicit Session exists independently of model readiness. Every
        # accepted message proceeds to process_query so the canonical runtime
        # creates an Execution before provider/understanding failure is recorded.
        _record_session_message(request.thread_id, request.message)
        agent = get_agent(request.thread_id)
        # process_query restores Session scope under its request lock. Query
        # payloads do not override backend-selected workspace/mode.
        thread_state = get_state_store().get_thread_state(request.thread_id).model_dump()
        handler = _EventSink()
        response, success = agent.process_query(
            request.message,
            include_memory=request.include_memory,
            callbacks=[handler],
            thread_id=request.thread_id,
            source=source,
            cancel_event=cancel_event,
            request_id=request_id,
            thinking_enabled=request.thinking_enabled,
            reasoning_effort=request.reasoning_effort,
        )
        doc_sources = agent.get_last_doc_sources() if request.include_memory else []
        store = get_state_store()
        latest_state = store.get_thread_state(request.thread_id).model_dump()
        worker_execution_id = agent.completed_execution_id_for_current_worker()
        if not worker_execution_id and not request.voice_turn_id:
            worker_execution_id = str(latest_state.get("last_execution_id") or "")
        if request.voice_turn_id and not worker_execution_id:
            raise RuntimeError("Voice query completed without an exact worker Execution identity")
        execution = store.get_execution(worker_execution_id) if worker_execution_id else None
        if request.voice_turn_id and execution is None:
            raise RuntimeError("Voice query completed without its durable Execution")
        if request.voice_turn_id and execution is not None:
            from agent.voice_transport import bind_voice_turn_submission

            bind_voice_turn_submission(
                request.voice_turn_id,
                session_id=_normalize_thread_id(request.thread_id),
                request_id=request_id,
                execution_id=execution.id,
                task_run_id=str(execution.task_run_id or ""),
                query_completed=True,
            )

        return QueryResponse(
            response=response,
            success=success,
            memory_count=agent.memory.memory_count,
            request_id=request_id,
            doc_sources=doc_sources,
            research=[],
            execution_id=execution.id if execution else None,
            trace_id=execution.trace_id if execution else None,
            thread_state=latest_state or thread_state,
            voice_turn_id=request.voice_turn_id,
        )
    except Exception as e:
        if request.voice_turn_id:
            try:
                from agent.voice_transport import fail_voice_turn

                fail_voice_turn(
                    request.voice_turn_id,
                    session_id=_normalize_thread_id(request.thread_id),
                    error_code="voice_query_failed",
                )
            except Exception:
                pass
        _metric_inc("errors", 1)
        diagnostic_id = hashlib.sha256(
            f"{request_id}:{type(e).__name__}:{e}".encode("utf-8", errors="ignore")
        ).hexdigest()[:12]
        logger.exception(
            "Query failed request_id={} diagnostic_id={}", request_id, diagnostic_id
        )
        raise HTTPException(
            status_code=500,
            detail={"message": _safe_stream_failure(e), "diagnostic_id": diagnostic_id},
        )
    finally:
        _release_query_cancellation(request_id, cancel_event)


def _record_session_message(thread_id: Optional[str], message: str) -> None:
    tid = _normalize_thread_id(thread_id)
    if not tid:
        return
    from agent.threads import get_thread_manager
    manager = get_thread_manager()
    if manager.get_thread(tid) is None:
        # Session creation belongs exclusively to POST /threads (the explicit
        # New Session/+ UI). Query completion must never manufacture a sidebar
        # Session from a missing/default/stale id.
        logger.warning("Skipped message metadata for unknown Session {}; no Session was created", tid)
        return
    manager.record_user_message(tid, message)


# ── Observability Dashboard (v6.0.0) ────────────────────────────────

# ── NDJSON Streaming (v6.0.0) ────────────────────────────────────────

@router.get("/stream/{request_id}")
async def stream_events(request_id: str):
    """Stream tool-execution events as NDJSON for real-time UI updates.

    Returns a StreamingResponse with Content-Type: application/x-ndjson.
    Each line is a JSON object with event_type, timestamp, tool_name, data, etc.
    """
    from starlette.responses import StreamingResponse
    from agent.stream_events import get_stream_buffer

    buffer = get_stream_buffer(request_id)
    return StreamingResponse(
        buffer.stream(),
        media_type="application/x-ndjson",
        headers={
            "X-Request-Id": request_id,
            "Cache-Control": "no-cache",
        },
    )


@router.post("/query/stream")
async def query_stream(request: QueryRequest):
    from agent.query_journal import get_query_journal
    journal = get_query_journal()
    session = _normalize_thread_id(request.thread_id)
    q: queue.Queue = queue.Queue()
    request_id = str(request.client_request_id or "").strip() or str(uuid.uuid4())
    fingerprint = hashlib.sha256(request.model_dump_json().encode()).hexdigest()
    try:
        claimed = journal.claim(request_id, session, fingerprint)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not claimed:
        return _journal_response(request_id, session, 0, replay=True)
    cancel_event = threading.Event()
    _register_query_cancellation(request_id, request.thread_id or "default", cancel_event)
    _metric_inc("requests", 1)

    logger.debug(
        "QueryStream request_id={} thread_id={} include_memory={} msg_len={}",
        request_id,
        _normalize_thread_id(request.thread_id),
        bool(request.include_memory),
        len((request.message or "")),
    )
    # Unknown Session ids still fail closed in _record_session_message. Provider
    # and coding readiness are diagnosed only after the Turn owns an Execution.
    try:
        source = _prepare_query_transport(request, request_id)
        _record_session_message(request.thread_id, request.message)
        agent = get_agent(request.thread_id)
        _start_agent_thread(
            agent=agent,
            message=request.message,
            include_memory=request.include_memory,
            thread_id=request.thread_id,
            workspace=request.workspace,
            request_id=request_id,
            q=q,
            cancel_event=cancel_event,
            thinking_enabled=request.thinking_enabled,
            reasoning_effort=request.reasoning_effort,
            source=source,
            voice_turn_id=str(request.voice_turn_id or ""),
            agent_id=str(request.agent_id or ""),
        )
    except Exception as exc:
        cancel_event.set()
        _release_query_cancellation(request_id, cancel_event)
        journal.append(request_id, {"type": "error", "message": _safe_stream_failure(exc), "request_id": request_id})
        journal.finish(request_id, "failed")
        if request.voice_turn_id:
            try:
                from agent.voice_transport import fail_voice_turn

                fail_voice_turn(
                    str(request.voice_turn_id),
                    session_id=str(request.thread_id or "default"),
                    error_code="voice_query_start_failed",
                )
            except Exception as voice_exc:
                logger.warning(
                    "Voice transport startup failure could not be persisted voice_turn_id={} request_id={} error_type={}",
                    request.voice_turn_id,
                    request_id,
                    type(voice_exc).__name__,
                )
        raise

    def collect():
        # The collector belongs to the run, not the HTTP connection. A browser
        # disconnect only detaches a reader and never cancels governed work.
        first = True
        startup_timeout = max(
            1.0,
            float(getattr(config, "stream_startup_timeout_seconds", 15.0) or 15.0),
        )
        try:
            while True:
                try:
                    item = q.get(timeout=startup_timeout if first else 60)
                except queue.Empty:
                    if not first:
                        continue
                    cancel_event.set()
                    journal.append(request_id, {
                                "type": "error",
                                "message": "The selected model did not start responding in time. This run was cancelled.",
                                "request_id": request_id,
                                "at": time.time(),
                            })
                    break
                first = False
                if item is None:
                    break
                journal.append(request_id, item)
        except Exception as exc:
            cancel_event.set()
            logger.exception("Stream journal failed request_id={}", request_id)
            journal.append(request_id, {"type": "error", "message": _safe_stream_failure(exc), "request_id": request_id})
        finally:
            journal.finish(request_id, "cancelled" if cancel_event.is_set() else "completed")
            _release_query_cancellation(request_id, cancel_event)

    threading.Thread(target=collect, name="query-journal", daemon=True).start()
    return _journal_response(request_id, session, 0)


def _journal_response(request_id: str, session: str, after: int, replay: bool = False):
    from agent.query_journal import get_query_journal
    journal = get_query_journal()
    if not journal.get(request_id, session):
        raise HTTPException(404, "Run not found in this chat, or its replay expired.")

    async def events():
        cursor = after
        while True:
            items = await anyio.to_thread.run_sync(lambda: journal.read(request_id, cursor))
            for item in items:
                cursor = item["_replay_seq"]
                # Stored sound is never played twice after reconnection.
                if replay and item.get("type") == "voice_audio":
                    continue
                yield (json.dumps({**item, "_recovered": replay}, ensure_ascii=False) + "\n").encode()
            if not items:
                run = journal.get(request_id, session)
                if not run or run["status"] != "running":
                    yield (json.dumps({"type": "journal_done", "status": run["status"] if run else "expired", "_replay_seq": cursor}) + "\n").encode()
                    return
                await anyio.sleep(0.2)

    return StreamingResponse(events(), media_type="application/x-ndjson", headers={"Cache-Control": "no-store", "X-Request-Id": request_id})


@router.get("/query/runs")
def query_runs(thread_id: str):
    from agent.query_journal import get_query_journal
    return {"items": get_query_journal().list(_normalize_thread_id(thread_id))}


@router.get("/query/runs/{request_id}/events")
def replay_query(request_id: str, thread_id: str, after: int = Query(default=0, ge=0)):
    return _journal_response(request_id, _normalize_thread_id(thread_id), after, replay=True)
