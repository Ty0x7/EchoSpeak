"""Small persistent first-run checklist; capability configuration stays in settings."""
import json
import os
import threading
import time
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from config import DATA_DIR

router = APIRouter(prefix="/onboarding", tags=["onboarding"])
STATE_PATH = Path(DATA_DIR) / "onboarding.json"
_LOCK = threading.Lock()


@router.get("")
def status():
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    from agent.threads import get_thread_manager
    existing = any(t.message_count > 0 for t in get_thread_manager().list_threads(include_archived=True, limit=10000))
    return {"status": state.get("status", "new"), "step": state.get("step", 0),
            "show": not existing and state.get("status", "new") not in {"completed", "skipped"}}


class Progress(BaseModel):
    status: Literal["in_progress", "completed", "skipped"] = "in_progress"
    step: int = Field(default=0, ge=0, le=5)


@router.put("")
def progress(request: Progress):
    with _LOCK:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        temp = STATE_PATH.with_suffix(".tmp")
        temp.write_text(json.dumps({**request.model_dump(), "updated_at": time.time(), "version": 1}), encoding="utf-8")
        os.replace(temp, STATE_PATH)
    return request.model_dump()


@router.get("/local-models")
def local_models():
    from agent.local_model_setup import catalog
    try:
        return catalog()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


class ModelDownload(BaseModel):
    provider: Literal["lmstudio", "ollama"]
    model: str = Field(min_length=1, max_length=150)


@router.post("/local-models/jobs")
def download_model(request: ModelDownload):
    from agent.local_model_setup import start
    try:
        return start(request.provider, request.model)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/local-models/jobs/{job_id}")
def download_status(job_id: str):
    from agent.local_model_setup import get_job
    try:
        return get_job(job_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
