"""Read immutable, chat/project-scoped image references from the media catalog."""
import hashlib
import io
from pathlib import Path


def read_references(session_id: str, project_id: str, asset_ids: list[str]) -> list[bytes]:
    from agent.media_library import get_media_library_store
    from agent.projects import get_project_manager
    from PIL import Image
    if len(asset_ids) > 4:
        raise ValueError("Use at most four reference images.")
    store = get_media_library_store()
    output = []
    for asset_id in asset_ids:
        asset = store.get(asset_id)
        if not asset or asset.archived or asset.status != "ready" or asset.media_kind != "image":
            raise ValueError("Reference image is unavailable.")
        if asset.session_id != session_id and not (project_id and asset.project_id == project_id):
            raise ValueError("Open the image's chat or attach its project before editing it.")
        project = get_project_manager().get_project(asset.project_id) if asset.storage_scope != "library" else None
        if asset.storage_scope != "library" and (not project or project.archived):
            raise ValueError("Reference project is unavailable.")
        root = store.root.resolve() if asset.storage_scope == "library" else Path(project.workspace_root).resolve()
        path = (root / asset.project_relative_path).resolve()
        if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size > 10_000_000:
            raise ValueError("Reference image is missing or exceeds 10 MB.")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != asset.sha256:
            raise ValueError("Reference image changed outside EchoSpeak; its saved identity no longer matches.")
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > 40_000_000:
                raise ValueError("Reference image dimensions exceed the limit.")
            image.load()
            image.thumbnail((2048, 2048))
            normalized = io.BytesIO()
            image.convert("RGB").save(normalized, format="PNG")
            output.append(normalized.getvalue())
    return output
