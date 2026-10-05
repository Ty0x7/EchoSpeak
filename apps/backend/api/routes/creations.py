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


@router.post("/jobs/{job_id}/recover")
def recover(job_id: str):
    from agent.generation_service import recover_job
    try:
        return recover_job(job_id).model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


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
    gpu_id: str = Field(default="", max_length=100)


@router.post("/local/setup")
def local_setup(request: LocalSetupRequest):
    from agent.generation_setup import start_setup
    try:
        return start_setup(request.profile, request.gpu_id)
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


class LocalRenderRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=200)
    profile: str = Field(default="image", pattern="^(image|video)$")


@router.post("/local/test")
def local_render_test(request: LocalRenderRequest):
    """Owner-requested local render, through the existing job and media pipeline."""
    import uuid
    from agent.generation_providers import validate_local_profile, IMAGE_MODEL, VIDEO_MODEL
    from agent.generation_service import submit
    from agent.generation_runtime import GenerationSettings
    try:
        validate_local_profile(request.profile)
        job = submit(session_id=request.session_id, execution_id="local-test-" + uuid.uuid4().hex,
                     prompt="A small white circle on a black background", kind=request.profile,
                     provider="comfyui-local", model=IMAGE_MODEL if request.profile == "image" else VIDEO_MODEL,
                     settings=GenerationSettings(width=512, height=512, duration_seconds=1, seed=1))
        return {"job_id": job.id, "status": job.status}
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(409, str(exc)) from exc
