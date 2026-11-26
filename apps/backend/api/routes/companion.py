"""Echo Companion: what the owner's agents are doing (read-only), plus registered adapters."""

from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

router = APIRouter(prefix="/companion", tags=["companion"])

ADAPTER_KINDS = {"session_started", "prompt", "turn_completed", "subagent_completed", "needs_permission",
                 "waiting_input", "session_ended", "stage_completed", "report_ready"}


class AdapterEvent(BaseModel):
    """An event from a registered integration adapter (future agents, EchoSpeak subsystems)."""

    agent: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,39}$")
    session_id: str = Field(min_length=1, max_length=200)
    kind: str = Field(max_length=40)
    text: str = Field(default="", max_length=12000)
    cwd: str = Field(default="", max_length=1000)
    turn_id: str = Field(default="", max_length=200)


@router.get("/activity")
def activity(include_echo: bool = True):
    from agent.companion.observe import activity as build, get_companion_store
    return build(get_companion_store(), include_echo=include_echo)


@router.get("/events")
def events(since: float = Query(default=0.0, ge=0), agent: str = "", session_id: str = "", kind: list[str] = Query(default=[]),
           limit: int = Query(default=100, ge=1, le=1000)):
    from agent.companion.observe import get_companion_store
    store = get_companion_store()
    store.ingest_spool()
    return {"items": store.events(since=since, agent=agent, session_id=session_id, kinds=kind, limit=limit)}


@router.post("/events")
def add_event(event: AdapterEvent):
    import hashlib
    import json

    from agent.companion.observe import get_companion_store

    if event.kind not in ADAPTER_KINDS:
        raise HTTPException(400, "Unknown event kind")
    if event.agent in {"claude-code", "codex"}:
        raise HTTPException(400, "Claude Code and Codex report through their own hooks")
    at = time.time()
    basis = [event.agent, event.session_id, event.kind, event.turn_id or round(at, 1), event.text[:200]]
    added = get_companion_store().add({
        "id": hashlib.sha256(json.dumps(basis).encode()).hexdigest()[:24], "agent": event.agent,
        "session_id": event.session_id, "kind": event.kind, "at": at, "turn_id": event.turn_id, "cwd": event.cwd,
        "project": Path(event.cwd).name if event.cwd else "", "text": event.text, "transcript_path": "",
        "subagent": "", "meta": {"via": "adapter"},
    })
    return {"added": added}


@router.get("/hooks/status")
def hooks_status():
    import os

    from agent.companion.install import status
    return status(claude_dir=Path(os.path.expanduser("~/.claude")),
                  codex_dir=Path(os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")))


class AskRequest(BaseModel):
    topic: str = Field(pattern=r"^(activity|latest_report|discovery|recap|agent:[a-z0-9-]{2,40})$")
    speak: bool = True
    since: float | None = None


@router.post("/ask")
def ask(request: AskRequest):
    """The owner clicked Echo Companion: answer from what was observed, and say it."""
    from agent.companion import recap
    from agent.companion.observe import activity as build, get_companion_store
    from agent.speech_output import get_speech_output

    store = get_companion_store()
    text = recap.answer(request.topic, store, build(store), request.since)
    spoken = None
    if request.speak:
        spoken = get_speech_output().speak(text, channel="companion", priority=70, interrupt=True, source="companion")
    return {"topic": request.topic, "text": text, "speech": spoken}


@router.get("/commentary")
def commentary(limit: int = Query(default=30, ge=1, le=100)):
    from agent.companion.commentary import get_commentary
    return {"items": list(get_commentary().feed)[-limit:]}


@router.get("/stream")
async def stream():
    """Server-sent events for the companion window: commentary items and speech events."""
    import asyncio
    import json as _json

    from fastapi.responses import StreamingResponse

    from agent.companion.commentary import get_commentary
    from agent.speech_output import get_speech_output

    loop = asyncio.get_running_loop()
    events: asyncio.Queue = asyncio.Queue(maxsize=500)

    def push(kind):
        return lambda item: loop.call_soon_threadsafe(lambda: events.full() or events.put_nowait((kind, item)))

    commentary = get_commentary()
    unsubscribe_speech = get_speech_output().subscribe(push("speech"))
    unsubscribe_commentary = commentary.on_item(push("commentary"))

    async def body():
        try:
            yield "event: hello\ndata: {}\n\n"
            while True:
                try:
                    kind, item = await asyncio.wait_for(events.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                name = item.get("type", kind) if kind == "speech" else "commentary"
                yield f"event: {name}\ndata: {_json.dumps(item, default=str)}\n\n"
        finally:
            unsubscribe_speech()
            unsubscribe_commentary()

    return StreamingResponse(body(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})
