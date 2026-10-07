"""Artifacts: substantial output (apps, documents, diagrams, code) shown in a side panel.

An agent creates one with ``create_artifact`` and revises it with
``update_artifact``; every change is a new version, so the user can step back
and restore. Content is stored as JSON under ``DATA_DIR/lean/artifacts``.

HTML and SVG artifacts run untrusted, model-written code. They are only ever
shown through ``frame_html`` from their own URL, with a CSP that sandboxes the
page (opaque origin, no network, no forms, no top navigation), inside an iframe
that adds ``sandbox="allow-scripts"`` on top.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from agent.lean.toolbox import NativeTool
from agent.lean.widgets import attach

KINDS = ("html", "svg", "mermaid", "markdown", "code")
MAX_CONTENT = 400_000
MAX_VERSIONS = 50

# Served with every HTML/SVG frame. `sandbox` without allow-same-origin gives the
# page an opaque origin even if opened directly; connect-src 'none' blocks fetch/XHR/WebSocket.
FRAME_CSP = (
    "sandbox allow-scripts allow-modals; "
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "img-src data: blob:; font-src data:; media-src data: blob:; connect-src 'none'; "
    "form-action 'none'; base-uri 'none'; frame-src 'none'; worker-src blob:"
)

_LOCK = threading.Lock()


def _dir() -> Path:
    from config import DATA_DIR

    path = Path(DATA_DIR) / "lean" / "artifacts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _path(artifact_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{12}", artifact_id or ""):
        raise KeyError(artifact_id)
    return _dir() / f"{artifact_id}.json"


def get(artifact_id: str) -> Optional[dict[str, Any]]:
    try:
        path = _path(artifact_id)
    except KeyError:
        return None
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _save(record: dict[str, Any]) -> None:
    path = _path(record["id"])
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def excerpt(kind: str, content: str, title: str = "", limit: int = 160) -> str:
    """A one-line glimpse of an artifact for the library: readable text, not markup."""
    text = str(content or "")
    if kind in {"svg", "mermaid", "code"}:
        return ""  # source isn't a readable glimpse; the library shows the type and size instead
    if kind == "html":
        text = re.sub(r"(?is)<(script|style|title)\b.*?</\1>", " ", text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
    elif kind == "markdown":
        text = re.sub(r"(?m)^\s{0,3}(#{1,6}|[-*+>]|\d+\.)\s+", "", text)
        text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
        text = re.sub(r"[*_`]{1,3}", "", text)
        text = re.sub(r"(?m)^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$", "", text)  # table rules
        text = text.replace("|", " ")
    text = " ".join(text.split())
    # The first heading is usually the title, which the library already shows.
    if title and text.lower().startswith(title.strip().lower()):
        text = text[len(title.strip()):].lstrip(" :-·")
    return text[:limit].rstrip() + ("…" if len(text) > limit else "")


def summary(record: dict[str, Any]) -> dict[str, Any]:
    latest = record["versions"][-1]
    content = str(latest.get("content") or "")
    title = latest.get("title") or record.get("title") or "Artifact"
    return {
        "id": record["id"],
        "title": title,
        "kind": record["kind"],
        "language": record.get("language", ""),
        "version": latest["n"],
        "versions": len(record["versions"]),
        "session_id": record.get("session_id", ""),
        "agent_id": record.get("agent_id", ""),
        "created_at": record.get("created_at", 0),
        "updated_at": record.get("updated_at", 0),
        "excerpt": excerpt(record["kind"], content, title),
        "lines": content.count("\n") + 1 if content else 0,
    }


def list_all(session_id: str = "") -> list[dict[str, Any]]:
    items = []
    for path in _dir().glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if session_id and record.get("session_id") != session_id:
            continue
        items.append(summary(record))
    items.sort(key=lambda r: r.get("updated_at") or 0, reverse=True)
    return items


def version(record: dict[str, Any], n: Optional[int] = None) -> dict[str, Any]:
    if n is None:
        return record["versions"][-1]
    for item in record["versions"]:
        if item["n"] == n:
            return item
    raise KeyError(n)


def _clean_kind(kind: str, content: str) -> str:
    kind = str(kind or "").strip().lower()
    aliases = {"web": "html", "app": "html", "page": "html", "website": "html", "md": "markdown", "doc": "markdown", "document": "markdown", "diagram": "mermaid", "image": "svg"}
    kind = aliases.get(kind, kind)
    if kind not in KINDS:
        head = content.lstrip()[:200].lower()
        kind = "html" if head.startswith(("<!doctype", "<html", "<div", "<body", "<style", "<script")) else "svg" if head.startswith(("<svg", "<?xml")) else "markdown"
    return kind


def _strip_fence(content: str) -> str:
    """Models often wrap the content in a ``` fence; the artifact is what's inside."""
    match = re.fullmatch(r"\s*```[\w+.#-]*[^\n]*\n(.*?)\n?```\s*", content, re.S)
    return match.group(1) if match else content


def create(*, title: str, kind: str, content: str, language: str = "", session_id: str = "", agent_id: str = "") -> dict[str, Any]:
    content = _strip_fence(str(content or ""))
    if not content.strip():
        raise ValueError("content is empty")
    if len(content) > MAX_CONTENT:
        raise ValueError(f"content is larger than {MAX_CONTENT // 1000} KB")
    now = time.time()
    record = {
        "id": uuid.uuid4().hex[:12],
        "title": (title or "Untitled").strip()[:120],
        "kind": _clean_kind(kind, content),
        "language": str(language or "").strip().lower()[:24],
        "session_id": session_id,
        "agent_id": agent_id,
        "created_at": now,
        "updated_at": now,
        "versions": [{"n": 1, "title": (title or "Untitled").strip()[:120], "content": content, "at": now, "note": "Created"}],
    }
    with _LOCK:
        _save(record)
    return record


def update(artifact_id: str, *, content: str = "", edits: Optional[list[dict[str, Any]]] = None, title: str = "", note: str = "") -> dict[str, Any]:
    with _LOCK:
        record = get(artifact_id)
        if record is None:
            raise KeyError(artifact_id)
        current = record["versions"][-1]
        if content:
            new_content = _strip_fence(content)
        elif edits:
            new_content = current["content"]
            for i, edit in enumerate(edits, 1):
                find, replace = str(edit.get("find") or ""), str(edit.get("replace") or "")
                if not find:
                    raise ValueError(f"edit {i} has no 'find' text")
                count = new_content.count(find)
                if count == 0:
                    raise ValueError(f"edit {i}: the 'find' text is not in the current version; send the full new content instead")
                if count > 1:
                    raise ValueError(f"edit {i}: the 'find' text appears {count} times; include more surrounding text")
                new_content = new_content.replace(find, replace, 1)
        else:
            raise ValueError("send either content (the full new version) or edits [{find, replace}]")
        if len(new_content) > MAX_CONTENT:
            raise ValueError(f"content is larger than {MAX_CONTENT // 1000} KB")
        now = time.time()
        record["versions"].append({
            "n": current["n"] + 1,
            "title": (title or current.get("title") or record["title"]).strip()[:120],
            "content": new_content,
            "at": now,
            "note": note[:200] or "Updated",
        })
        record["versions"] = record["versions"][-MAX_VERSIONS:]
        record["updated_at"] = now
        _save(record)
        return record


def restore(artifact_id: str, n: int) -> dict[str, Any]:
    record = get(artifact_id)
    if record is None:
        raise KeyError(artifact_id)
    old = version(record, n)
    return update(artifact_id, content=old["content"], title=old.get("title", ""), note=f"Restored version {n}")


def delete(artifact_id: str) -> bool:
    try:
        path = _path(artifact_id)
    except KeyError:
        return False
    if path.exists():
        path.unlink()
        return True
    return False


def frame_html(record: dict[str, Any], n: Optional[int] = None) -> str:
    """The document served inside the sandboxed iframe (HTML and SVG kinds)."""
    item = version(record, n)
    content = item["content"]
    meta = f'<meta http-equiv="Content-Security-Policy" content="{FRAME_CSP.split("; ", 1)[1]}">'
    if record["kind"] == "svg":
        return (
            f"<!doctype html><html><head><meta charset=\"utf-8\">{meta}"
            "<style>html,body{margin:0;height:100%;background:#fff}body{display:grid;place-items:center}svg{max-width:100%;max-height:100vh;height:auto}</style>"
            f"</head><body>{content}</body></html>"
        )
    head = f'<meta charset="utf-8">{meta}<meta name="viewport" content="width=device-width, initial-scale=1">'
    if re.search(r"<html[\s>]", content, re.I):
        # Put our CSP first inside <head> (meta CSP must come before other content).
        if re.search(r"<head[^>]*>", content, re.I):
            return re.sub(r"(<head[^>]*>)", lambda m: m.group(1) + head, content, count=1, flags=re.I)
        return re.sub(r"(<html[^>]*>)", lambda m: m.group(1) + f"<head>{head}</head>", content, count=1, flags=re.I)
    return f"<!doctype html><html><head>{head}<style>body{{font-family:system-ui,sans-serif;margin:16px}}</style></head><body>{content}</body></html>"


# ── tools ─────────────────────────────────────────────────────────────────

def _context() -> dict[str, Any]:
    from agent.tools import _tool_execution_context

    try:
        return dict(_tool_execution_context.get() or {})
    except LookupError:
        return {}


def _announce(record: dict[str, Any]) -> None:
    latest = record["versions"][-1]
    attach({"type": "artifact", "data": {"id": record["id"], "title": latest.get("title") or record["title"], "kind": record["kind"],
                                         "version": latest["n"], "language": record.get("language", "")}})


def create_artifact_tool(args: dict[str, Any]) -> str:
    ctx = _context()
    try:
        record = create(
            title=str(args.get("title") or ""),
            kind=str(args.get("type") or args.get("kind") or ""),
            content=str(args.get("content") or ""),
            language=str(args.get("language") or ""),
            session_id=str(ctx.get("session_id") or ""),
            agent_id=str(ctx.get("agent_id") or ""),
        )
    except ValueError as exc:
        return f"Error: {exc}."
    _announce(record)
    return (f"Created artifact {record['id']} ({record['kind']}, version 1): \"{record['title']}\". It is open in the side panel. "
            f"For changes later, call update_artifact with artifact_id=\"{record['id']}\".")


def update_artifact_tool(args: dict[str, Any]) -> str:
    artifact_id = str(args.get("artifact_id") or args.get("id") or "").strip()
    note = ""
    if not artifact_id or get(artifact_id) is None:
        # Small models often drop or misremember the id; the chat's latest artifact is the obvious target.
        recent = list_all(str(_context().get("session_id") or ""))
        if not recent:
            return "Error: no artifact in this chat yet. Use create_artifact first."
        if artifact_id:
            note = f" (there is no artifact {artifact_id}; updated this chat's latest one)"
        artifact_id = recent[0]["id"]
    edits = args.get("edits") if isinstance(args.get("edits"), list) else None
    try:
        record = update(artifact_id, content=str(args.get("content") or ""), edits=edits, title=str(args.get("title") or ""), note=str(args.get("note") or ""))
    except KeyError:
        return f"Error: no artifact with id {artifact_id}."
    except ValueError as exc:
        return f"Error: {exc}."
    _announce(record)
    latest = record["versions"][-1]
    return f"Updated artifact {record['id']} to version {latest['n']} (\"{latest['title']}\"){note}. The side panel shows the new version."


def artifact_tools() -> list[NativeTool]:
    return [
        NativeTool(
            name="create_artifact",
            description=(
                "Show substantial output in a side panel the user can run, copy, download and iterate on: an interactive app or "
                "calculator (type html: one self-contained file with inline CSS/JS, no external scripts or network), an SVG image, "
                "a mermaid diagram, a markdown document, or a code file. Use it for anything over ~15 lines the user will use or keep; "
                "short answers stay in chat."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Short name, e.g. 'Tip calculator'"},
                    "type": {"type": "string", "enum": list(KINDS)},
                    "content": {"type": "string", "description": "The full content (for html: a complete page)"},
                    "language": {"type": "string", "description": "For type code: the language, e.g. python"},
                },
                "required": ["title", "type", "content"],
            },
            func=create_artifact_tool,
        ),
        NativeTool(
            name="update_artifact",
            description=(
                "Change an artifact you made; this saves a new version (older ones stay restorable). Send content (the full new "
                "version) or edits [{find, replace}] for small changes."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "artifact_id": {"type": "string", "description": "Id from create_artifact"},
                    "content": {"type": "string", "description": "Full new content"},
                    "edits": {
                        "type": "array",
                        "items": {"type": "object", "properties": {"find": {"type": "string"}, "replace": {"type": "string"}}, "required": ["find", "replace"]},
                    },
                    "title": {"type": "string"},
                },
                "required": ["artifact_id"],
            },
            func=update_artifact_tool,
        ),
    ]


# ── frame tokens ──────────────────────────────────────────────────────────
# An <iframe src> can't send the desktop app's auth header, so the app asks for
# a short-lived token (an authenticated call) and puts it in the frame URL. A
# token opens one artifact's frame and nothing else, and expires on its own.
FRAME_TOKEN_TTL = 300
_FRAME_TOKENS: dict[str, tuple[str, float]] = {}


def issue_frame_token(artifact_id: str) -> str:
    import secrets

    now = time.time()
    with _LOCK:
        for token, (_, expires) in list(_FRAME_TOKENS.items()):
            if expires < now:
                _FRAME_TOKENS.pop(token, None)
        token = secrets.token_urlsafe(24)
        _FRAME_TOKENS[token] = (artifact_id, now + FRAME_TOKEN_TTL)
    return token


def frame_token_ok(artifact_id: str, token: str) -> bool:
    entry = _FRAME_TOKENS.get(token or "")
    return bool(entry and entry[0] == artifact_id and entry[1] >= time.time())
