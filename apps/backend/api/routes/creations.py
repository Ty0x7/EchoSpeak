"""Creations projects the existing generation jobs and media catalog for the owner UI."""
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from agent.generation_runtime import get_generation_job_store
from agent.generation_service import cancel_job
from agent.generation_providers import provider_statuses
from agent.media_library import get_media_library_store, MediaLibraryError

router = APIRouter(prefix="/creations", tags=["creations"])


@router.get("/providers")
def providers():
    return {"items": provider_statuses()}


@router.get("")
def library(archived: bool = False, kind: str = "", query: str = ""):
    rows = get_media_library_store().list(limit=500)
    rows = [row for row in rows if row.source_kind == "generated" and row.media_kind in {"image", "video"}
            and row.archived == archived and (not kind or row.media_kind == kind)
            and (not query or query.lower() in (row.name + " " + row.prompt).lower())]
    jobs = get_generation_job_store().list(limit=100)
    return {"items": [row.model_dump(mode="json") for row in rows],
            "jobs": [job.model_dump(mode="json") for job in jobs if job.status != "completed"]}


@router.get("/jobs/{job_id}")
def job_status(job_id: str):
    job = get_generation_job_store().get(job_id)
    if job is None:
        raise HTTPException(404, "Creation job not found")
    assets = [get_media_library_store().get(asset) for asset in job.output_asset_ids]
    return {"job": job.model_dump(mode="json"), "assets": [a.model_dump(mode="json") for a in assets if a]}


@router.post("/jobs/{job_id}/cancel")
def cancel(job_id: str):
    try:
        return cancel_job(job_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


class AssetUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=160)
    archived: bool | None = None


@router.patch("/assets/{asset_id}")
def update_asset(asset_id: str, request: AssetUpdate):
    try:
        return get_media_library_store().update(asset_id, name=request.name, archived=request.archived).model_dump(mode="json")
    except MediaLibraryError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/assets/{asset_id}/content")
def content(asset_id: str, download: bool = False):
    store = get_media_library_store()
    try:
        asset = store.get(asset_id)
    except MediaLibraryError as exc:
        raise HTTPException(400, str(exc)) from exc
    if asset is None or asset.source_kind != "generated" or asset.media_kind not in {"image", "video"}:
        raise HTTPException(404, "Creation not found")
    if asset.storage_scope == "library":
        root = store.root.resolve()
    else:
        from agent.projects import get_project_manager
        project = get_project_manager().get_project(asset.project_id)
        if project is None or project.archived:
            raise HTTPException(404, "Project unavailable")
        root = Path(project.workspace_root).resolve()
    path = (root / asset.project_relative_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404, "Creation file unavailable")
    return FileResponse(path, filename=asset.name if download else None,
                        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})


@router.get("/local/setup")
def local_status():
    from agent.generation_setup import status
    return status()


class LocalSetupRequest(BaseModel):
    profile: str = "image"


@router.post("/local/setup")
def local_setup(request: LocalSetupRequest):
    from agent.generation_setup import start_setup
    try:
        return start_setup(request.profile)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/local/start")
def local_start():
    from agent.generation_setup import start_runtime
    try:
        return start_runtime()
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/local/cancel")
def local_cancel():
    from agent.generation_setup import stop_setup
    return stop_setup()
