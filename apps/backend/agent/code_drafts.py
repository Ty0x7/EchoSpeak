"""Explicit owner stashes. Uploaded images stay inside the selected trusted project."""
import base64
import io
import json
import threading
import time
import uuid

from agent import project_workspace
from agent.code_worktree import atomic_json

_lock = threading.RLock()


def _path():
    from config import DATA_DIR
    return DATA_DIR / "code-drafts.json"


def _read():
    return json.loads(_path().read_text(encoding="utf-8")) if _path().exists() else {}


def list_drafts():
    with _lock:
        return sorted(_read().values(), key=lambda r: r["created_at"], reverse=True)


def restore(identity):
    with _lock:
        row = _read().get(identity)
        if not row:
            raise KeyError("Draft unavailable")
        item = project_workspace.project(row["project_id"])
        if item.trust_state != "trusted" or str(project_workspace.root_for(item)) != row["project_root"]:
            raise PermissionError("Draft project folder or trust changed")
        return {k: v for k, v in row.items() if k != "project_root"}


def save(project_id, text, images=()):
    from config import config
    item = project_workspace.project(project_id)
    root = project_workspace.root_for(item)
    if item.trust_state != "trusted" or not text.strip() and not images:
        raise ValueError("Choose a trusted project and add a prompt or image")
    if len(text) > 50000 or len(images) > 4:
        raise ValueError("A stash holds at most 50,000 characters and four images")
    if images and not config.allow_file_write:
        raise PermissionError("Enable file writing in Settings before saving project image attachments")
    decoded = []
    from PIL import Image
    for value in images:
        if len(value) > 3_000_000:
            raise ValueError("Each draft image must be below 2 MB")
        raw = base64.b64decode(value.split(",", 1)[-1], validate=True)
        if len(raw) > 2_000_000:
            raise ValueError("Each draft image must be below 2 MB")
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in {"PNG", "JPEG", "WEBP"} or image.width * image.height > 20_000_000:
                raise ValueError("Use a bounded PNG, JPEG or WebP image")
            extension = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}[image.format]
            image.verify()
            decoded.append((raw, extension))
    with _lock:
        data = _read()
        if len(data) >= 50:
            raise ValueError("Remove or reuse a stash before adding another (50 maximum)")
        paths = []
        for raw, extension in decoded:
            relative = ".echospeak/attachments/" + uuid.uuid4().hex + "." + extension
            path = project_workspace.safe_path(root, relative, exists=False)
            path.parent.mkdir(parents=True, exist_ok=True)
            if project_workspace.safe_path(root, relative, exists=False) != path:
                raise ValueError("Attachment folder changed")
            with path.open("xb") as file:
                file.write(raw)
            paths.append(relative)
        content = text + ("\n\nAttached project images (use read_image with these project-relative paths):\n" + "\n".join(paths) if paths else "")
        row = {"id": uuid.uuid4().hex, "project_id": project_id, "project_root": str(root), "project_name": item.name,
               "text": content, "images": paths, "title": (text.strip().splitlines()[0] if text.strip() else "Image draft")[:100], "created_at": time.time()}
        data[row["id"]] = row
        _path().parent.mkdir(parents=True, exist_ok=True)
        atomic_json(_path(), data)
        return row


def remove(identity):
    with _lock:
        data = _read()
        data.pop(identity, None)
        _path().parent.mkdir(parents=True, exist_ok=True)
        atomic_json(_path(), data)
    return {"removed": True}  # User-uploaded project files are deliberately kept.
