"""Owner-facing project workspace, subject to the existing local API auth guard."""
from fastapi import APIRouter, HTTPException, Query, Request, Response

from agent import project_workspace as workspace

router = APIRouter(prefix="/projects/{project_id}/workspace", tags=["project workspace"])


def call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except KeyError as exc:
        raise HTTPException(404, "Project or record unavailable") from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)[:500]) from exc


@router.get("")
def overview(project_id: str):
    return call(workspace.overview, project_id)


@router.get("/records/{record_id}")
def record(project_id: str, record_id: str):
    item = call(workspace.project, project_id)
    result = next((r for r in item.workspace_records if r["id"] == record_id), None)
    if not result:
        raise HTTPException(404, "Report not found")
    return result


@router.get("/files")
def files(project_id: str, path: str = Query(default="", max_length=500)):
    return {"items": call(workspace.files, call(workspace.project, project_id), path)}


@router.get("/file")
def file(project_id: str, path: str = Query(max_length=500)):
    data, _ = call(workspace.read_file, call(workspace.project, project_id), path)
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(400, "File is not UTF-8 text") from exc
    from agent.lean.policy import redact_secrets
    text = redact_secrets(text)
    result = {"path": path, "text": text}
    if path.lower().endswith((".html", ".htm")):
        result["html_preview"] = call(workspace.document_html, call(workspace.project, project_id), path, text)
    return result


@router.get("/media")
def media(project_id: str, request: Request, path: str = Query(max_length=500)):
    data, mime = call(workspace.read_file, call(workspace.project, project_id), path, media=True)
    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Accept-Ranges": "bytes"}
    # Single byte ranges allow native video seeking without a public/tokenized media endpoint.
    interval = request.headers.get("range", "")
    if interval:
        import re
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", interval)
        try:
            if not match or not any(match.groups()):
                raise ValueError()
            start = int(match[1]) if match[1] else max(0, len(data) - int(match[2]))
            end = min(len(data) - 1, int(match[2])) if match[1] and match[2] else len(data) - 1
            if start > end or start >= len(data):
                raise ValueError()
        except ValueError:
            return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{len(data)}"})
        headers["Content-Range"] = f"bytes {start}-{end}/{len(data)}"
        return Response(data[start:end + 1], status_code=206, media_type=mime, headers=headers)
    return Response(data, media_type=mime, headers=headers)
