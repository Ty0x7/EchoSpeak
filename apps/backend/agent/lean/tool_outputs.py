"""Large tool outputs kept whole, outside the context window, behind a chat-scoped handle.

A result too long for the context is saved here and the model gets its head and
tail plus an ID ("out-…"). read_output reopens any slice or searches it, so
nothing is lost to a cut. Older results cleared from context to save room are
saved the same way. Like screenshots (agent/lean/observations.py), outputs stay
in this chat's store, separate from memory, for up to seven days.
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

RETENTION = 7 * 86400
MAX_OUTPUT_CHARS = 4_000_000
MAX_STORE_BYTES = 256_000_000
READ_DEFAULT, READ_MAX = 6000, 12000
_lock = threading.RLock()
_ID = re.compile(r"out-[a-f0-9]{24}\Z")


def root() -> Path:
    from config import DATA_DIR
    return Path(DATA_DIR) / "lean" / "outputs"


def _path(id_: str, suffix: str) -> Path:
    if not _ID.fullmatch(str(id_ or "")):
        raise ValueError("Invalid output ID; it looks like out- followed by 24 letters and digits")
    return root() / (id_ + suffix)


def cleanup() -> None:
    with _lock:
        if not root().exists():
            return
        files = sorted(root().glob("out-*.txt"), key=lambda p: p.stat().st_mtime)
        total = sum(p.stat().st_size for p in files)
        for path in files:
            size = path.stat().st_size
            if time.time() - path.stat().st_mtime <= RETENTION and total <= MAX_STORE_BYTES:
                continue
            path.unlink(missing_ok=True)
            path.with_suffix(".json").unlink(missing_ok=True)
            total -= size


def save(session: str, tool: str, text: str, *, untrusted: bool) -> str:
    """Keep the full text; returns its ID. Very long text keeps its first 4M characters."""
    item = {"id": "out-" + uuid.uuid4().hex[:24], "session_id": session, "tool": str(tool)[:80],
            "untrusted": bool(untrusted), "at": time.time(), "chars": len(text)}
    with _lock:
        root().mkdir(parents=True, exist_ok=True)
        cleanup()
        _path(item["id"], ".txt").write_text(text[:MAX_OUTPUT_CHARS], encoding="utf-8")
        _path(item["id"], ".json").write_text(json.dumps(item), encoding="utf-8")
    return item["id"]


def load(id_: str, session: str) -> tuple[dict[str, Any], str]:
    with _lock:
        meta, body = _path(id_, ".json"), _path(id_, ".txt")
        if meta.is_symlink() or body.is_symlink() or not meta.is_file():
            raise FileNotFoundError("That output is no longer available; run the tool again")
        item = json.loads(meta.read_text(encoding="utf-8"))
        if item.get("session_id") != session:
            raise PermissionError("That output belongs to another chat")
        if time.time() - float(item["at"]) > RETENTION:
            body.unlink(missing_ok=True)
            meta.unlink(missing_ok=True)
            raise FileNotFoundError("That output has expired; run the tool again")
        return item, body.read_text(encoding="utf-8")


def is_untrusted(id_: Any, session: str = "") -> bool:
    """Reopened web or email text stays outside content. Unknown IDs count as outside too."""
    try:
        meta = json.loads(_path(str(id_), ".json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    return bool(meta.get("untrusted", True)) or bool(session and meta.get("session_id") != session)


def preview(text: str, id_: str, limit: int) -> str:
    """Head and tail (errors and totals usually come last) around a pointer to the rest."""
    head, tail = text[: limit // 3], text[-(limit * 2) // 3:]
    return (f"{head}\n…[{len(text) - len(head) - len(tail):,} characters in the middle are not shown. The full "
            f"output ({len(text):,} characters) is saved as {id_}: use read_output with that id and an offset, "
            f"or a query to find lines]…\n{tail}")


def read(session: str, args: dict[str, Any]) -> str:
    item, text = load(str(args.get("id") or ""), session)
    query = str(args.get("query") or "").strip()
    if query:
        needle = query.casefold()
        hits = [f"{n}: {line[:400]}" for n, line in enumerate(text.splitlines(), 1) if needle in line.casefold()]
        shown = hits[:200]
        more = f"\n…{len(hits) - len(shown)} more matching lines; narrow the query" if len(hits) > len(shown) else ""
        return (f"{item['id']} ({item['tool']}, {item['chars']:,} characters): {len(hits)} lines contain "
                f"{query!r}\n" + ("\n".join(shown) or "(no matches)") + more)
    try:
        offset = max(0, int(args.get("offset") or 0))
        length = min(READ_MAX, max(1, int(args.get("length") or READ_DEFAULT)))
    except (TypeError, ValueError):
        raise ValueError("offset and length must be whole numbers of characters") from None
    part = text[offset:offset + length]
    end = offset + len(part)
    rest = f" Next: offset {end}." if end < len(text) else " This is the end."
    return f"{item['id']} ({item['tool']}), characters {offset:,}–{end:,} of {len(text):,}.{rest}\n{part}"


def tool(session: str):
    from agent.lean.toolbox import NativeTool
    return NativeTool(
        "read_output",
        "Read more of a long tool result saved as out-…: a slice by character offset, or the lines that contain a query.",
        {"type": "object", "properties": {
            "id": {"type": "string", "description": "The out-… ID from the shortened result."},
            "offset": {"type": "integer", "description": "First character to show (default 0)."},
            "length": {"type": "integer", "description": f"Characters to show, up to {READ_MAX} (default {READ_DEFAULT})."},
            "query": {"type": "string", "description": "Show only lines containing this text, with line numbers."}},
         "required": ["id"]},
        lambda args: read(session, args), parallel_safe=True, always=True)
