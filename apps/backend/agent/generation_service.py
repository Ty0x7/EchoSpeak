"""Lean generation orchestration using the existing durable job and asset stores."""
from __future__ import annotations

import hashlib
import io
import json
import os
import secrets
import threading
import time
from pathlib import Path

from agent.generation_runtime import GenerationJob, GenerationSettings, get_generation_job_store
from agent.media_library import MediaLibraryAsset, get_media_library_store
from agent.generation_providers import IMAGE_MODEL, VIDEO_MODEL, MODELS, generate, provider_statuses
from config import config

_LOCK = threading.RLock()
_ACTIVE: dict[str, threading.Event] = {}
_SLOTS = threading.BoundedSemaphore(2)


def resolve_selection(kind: str, provider: str = "", model: str = "") -> tuple[str, str]:
    if kind not in {"image", "video"}:
        raise ValueError("Choose image or video")
    chosen = provider or str(getattr(config, f"generation_{kind}_provider"))
    if not model:
        if chosen == getattr(config, f"generation_{kind}_provider"):
            model = str(getattr(config, f"generation_{kind}_model", "") or "")
        default = (IMAGE_MODEL if kind == "image" else VIDEO_MODEL) if chosen == "comfyui-local" else MODELS.get(chosen, "")
        model = model or default
    supported = {"image": {"gemini-images", "comfyui-local"}, "video": {"gemini-video", "minimax-video", "comfyui-local"}}
    if chosen not in supported[kind]:
        raise ValueError(f"Provider {chosen} does not support {kind} generation")
    return chosen, model


def _validated_output(data: bytes, kind: str) -> tuple[bytes, str]:
    if not data or len(data) > 256_000_000:
        raise ValueError("Empty or oversized generation output")
    if kind == "image":
        from PIL import Image
        with Image.open(io.BytesIO(data)) as img:
            if img.width * img.height > 40_000_000:
                raise ValueError("Image dimensions exceed the limit")
            img.load()
            output = io.BytesIO()
            img.convert("RGBA").save(output, format="PNG")
        return output.getvalue(), ".png"
    import av
    with av.open(io.BytesIO(data)) as clip:
        if not clip.streams.video or "mp4" not in clip.format.name:
            raise ValueError("Provider output is not an MP4 video")
        next(clip.decode(video=0))
    return data, ".mp4"


def _worker(job: GenerationJob, cancel: threading.Event):
    store = get_generation_job_store()
    acquired = False
    try:
        while not acquired:
            if cancel.is_set():
                raise InterruptedError("Cancelled before submission")
            acquired = _SLOTS.acquire(timeout=0.2)
        if not config.allow_generation_actions:
            raise RuntimeError("Generation has been disabled in settings")
        job.status = "running"
        store.save(job)
        def checkpoint(remote_id):
            job.provider_job_id = remote_id
            store.save(job)
        data, kind = generate(job, checkpoint, cancel)
        if cancel.is_set():
            raise InterruptedError("Cancelled. Provider billing may still apply.")
        data, suffix = _validated_output(data, kind)
        library = get_media_library_store()
        folder = library.root / "files"
        folder.mkdir(parents=True, exist_ok=True)
        filename = job.id + suffix
        target = folder / filename
        temp = target.with_suffix(suffix + ".part")
        temp.write_bytes(data)
        os.replace(temp, target)
        asset = library.register(MediaLibraryAsset(id="asset-" + job.id, project_id=job.project_id,
            session_id=job.session_id, name=filename, media_kind=kind, source_kind="generated",
            storage_scope="library", project_relative_path="files/" + filename,
            sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data), prompt=job.prompt,
            provider=job.provider_id, model=job.model, settings=job.settings.model_dump(), job_id=job.id,
            execution_id=job.execution_id))
        job.output_asset_ids = [asset.id]
        job.status, job.progress = "completed", 1
    except InterruptedError as exc:
        job.status, job.error_code, job.error = "cancelled", "cancelled", str(exc)
        job.cancellation_requested = True
    except Exception as exc:
        from agent.lean.policy import redact_secrets
        job.status, job.error_code, job.error = "failed", "generation_failed", redact_secrets(str(exc))[:700]
    finally:
        store.save(job)
        with _LOCK:
            _ACTIVE.pop(job.id, None)
        if acquired:
            _SLOTS.release()


def submit(*, session_id: str, execution_id: str, prompt: str, kind: str, provider: str = "", model: str = "", settings: GenerationSettings | None = None) -> GenerationJob:
    if not config.allow_generation_actions:
        raise ValueError("Enable image/video creation in Creations settings first.")
    from agent.threads import get_thread_manager
    from agent.state import get_state_store
    if not get_thread_manager().get_thread(session_id):
        raise ValueError("Chat does not exist")
    provider, model = resolve_selection(kind, provider, model)
    key = hashlib.sha256(json.dumps([execution_id, kind, provider, model, prompt]).encode()).hexdigest()
    store = get_generation_job_store()
    with _LOCK:
        old = store.find_idempotent(session_id, key)
        if old:
            return old
        if len(_ACTIVE) >= 6:
            raise ValueError("Generation queue is full; wait for an existing job to finish.")
        state = get_state_store().get_thread_state(session_id)
        job = GenerationJob(idempotency_key=key, session_id=session_id, project_id=state.active_project_id or "",
            origin="lean_creation", execution_id=execution_id, prompt=prompt, kind=kind, provider_id=provider, model=model,
            settings=settings or GenerationSettings(width=512, height=512, seed=secrets.randbelow(2**32)))
        store.save(job)
        cancel = threading.Event()
        _ACTIVE[job.id] = cancel
        threading.Thread(target=_worker, args=(job, cancel), daemon=True, name="creation-" + job.id[-8:]).start()
    return job


def cancel_job(job_id: str):
    store = get_generation_job_store()
    with _LOCK:
        job = store.get(job_id)
        if not job:
            raise ValueError("Generation job not found")
        event = _ACTIVE.get(job_id)
        if event:
            event.set()
            job.cancellation_requested = True
            store.save(job)
        # Never interrupt a shared ComfyUI server or pretend a cloud charge was reversed.
        return {"requested": bool(event), "detail": "Stops waiting and retaining results. A provider job may continue and incur charges."}


def generation_tools(session_id: str, execution_id: str):
    from agent.lean.toolbox import NativeTool
    from agent.lean.widgets import attach
    def show(job):
        attach({"type": "creation", "data": {"id": job.id}})
        return json.dumps({"job_id": job.id, "status": job.status, "provider": job.provider_id, "model": job.model,
            "error": job.error, "assets": job.output_asset_ids,
            "next": "Use creation_status to wait until completed. The card also updates automatically."})
    def create(args):
        return show(submit(session_id=session_id, execution_id=execution_id,
            prompt=str(args.get("prompt") or ""), kind=str(args.get("kind") or "image"),
            provider=str(args.get("provider") or ""), model=str(args.get("model") or "")))
    def status(args):
        store = get_generation_job_store()
        job = store.get(str(args.get("job_id") or ""))
        if not job or job.session_id != session_id:
            return "Error: no such creation in this chat."
        until = time.monotonic() + min(20, max(0, int(args.get("wait_seconds", 10))))
        while job.status in {"queued", "running"} and time.monotonic() < until:
            time.sleep(0.5)
            job = store.get(job.id)
        return show(job)
    return [NativeTool(name="create_media", description="Create an image or video from a prompt using the user's selected cloud/local provider. "
        "Result appears in chat and Creations. Cloud requests ask approval and may cost money. Never resubmit a pending job; use creation_status.",
        parameters={"type": "object", "properties": {"kind": {"type": "string", "enum": ["image", "video"]},
            "prompt": {"type": "string"}, "provider": {"type": "string", "enum": ["gemini-images", "gemini-video", "minimax-video", "comfyui-local"]},
            "model": {"type": "string", "description": "Optional model ID; omit to use settings."}}, "required": ["kind", "prompt"]}, func=create),
        NativeTool(name="creation_status", description="Check or wait for a creation in this chat. Repeat while running, then report the result.",
            parameters={"type": "object", "properties": {"job_id": {"type": "string"}, "wait_seconds": {"type": "integer"}}, "required": ["job_id"]}, func=status, parallel_safe=True)]
