"""Expiring, session-scoped web evidence; deliberately separate from personal memory."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

TTL = 7 * 86400
MAX_SOURCES = 100


class ResearchNotebook:
    def __init__(self, root: Path | None = None):
        if root is None:
            from config import DATA_DIR
            root = Path(DATA_DIR) / "research"
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "notebook.db"
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS sources (
                session TEXT, id TEXT, url TEXT, title TEXT, text TEXT, metadata TEXT,
                inspected INTEGER, updated REAL, PRIMARY KEY(session,id))""")
            db.execute("""CREATE TABLE IF NOT EXISTS notes (
                session TEXT PRIMARY KEY, text TEXT, updated REAL)""")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def retain(self, session: str, *, url: str, title: str, text: str,
               inspected: bool = False, metadata: dict | None = None) -> str:
        from agent.lean.policy import redact_secrets
        from agent.safe_web_retrieval import _normalize_url
        url = _normalize_url(url)
        source_id = "src_" + hashlib.sha256(url.encode()).hexdigest()[:16]
        now = time.time()
        with self.connect() as db:
            db.execute("DELETE FROM sources WHERE updated < ?", (now - TTL,))
            db.execute("DELETE FROM notes WHERE updated < ?", (now - TTL,))
            old = db.execute("SELECT inspected FROM sources WHERE session=? AND id=?", (session, source_id)).fetchone()
            # A subsequent search snippet must not replace an inspected page.
            if old and old["inspected"] and not inspected:
                return source_id
            db.execute("INSERT OR REPLACE INTO sources VALUES (?,?,?,?,?,?,?,?)", (
                session, source_id, url, redact_secrets(title[:500]), redact_secrets(text[:100000]),
                json.dumps(metadata or {}, ensure_ascii=False) if len(json.dumps(metadata or {})) <= 40000 else "{}", int(inspected), now))
            db.execute("""DELETE FROM sources WHERE session=? AND id NOT IN
                (SELECT id FROM sources WHERE session=? ORDER BY updated DESC LIMIT ?)""",
                (session, session, MAX_SOURCES))
        return source_id

    def sources(self, session: str, query: str = "") -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM sources WHERE session=? AND updated>? ORDER BY updated DESC",
                              (session, time.time() - TTL)).fetchall()
        words = query.casefold().split()[:12]
        scored = []
        for row in rows:
            item = dict(row)
            haystack = (item["title"] + " " + item["text"]).casefold()
            score = sum(haystack.count(word) for word in words)
            if words and not score:
                continue
            item["excerpt"] = item.pop("text")[:600]
            item.pop("session")
            item.pop("metadata")
            scored.append((score, item))
        return [item for _, item in sorted(scored, key=lambda row: row[0], reverse=True)[:30]]

    def read(self, session: str, source_id: str, offset: int = 0, limit: int = 12000) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM sources WHERE session=? AND id=? AND updated>?",
                             (session, source_id, time.time() - TTL)).fetchone()
        if row is None:
            raise ValueError("Source not found in this chat, or expired. Search/open it again.")
        item = dict(row)
        offset, limit = max(0, offset), max(1000, min(limit, 24000))
        full = item["text"]
        item.update(text=full[offset:offset + limit], offset=offset, total_chars=len(full),
                    next_offset=offset + limit if offset + limit < len(full) else None)
        item["metadata"] = json.loads(item["metadata"])
        item.pop("session")
        return item

    def notes(self, session: str, text: str | None = None) -> str:
        with self.connect() as db:
            if text is not None:
                from agent.lean.policy import redact_secrets
                db.execute("INSERT OR REPLACE INTO notes VALUES (?,?,?)", (session, redact_secrets(text[:8000]), time.time()))
            row = db.execute("SELECT text FROM notes WHERE session=? AND updated>?", (session, time.time() - TTL)).fetchone()
        return row[0] if row else ""

    def clear(self, session: str):
        with self.connect() as db:
            db.execute("DELETE FROM sources WHERE session=?", (session,))
            db.execute("DELETE FROM notes WHERE session=?", (session,))


def current_session() -> str:
    from agent.tools import get_tool_execution_context
    return str((get_tool_execution_context() or {}).get("thread_id") or "")


def research_tools(session: str):
    from agent.lean.toolbox import NativeTool

    def run(args: dict[str, Any]) -> str:
        book = ResearchNotebook()
        action = args.get("action", "list")
        if action == "read":
            result = book.read(session, str(args.get("source_id") or ""), int(args.get("offset") or 0))
        elif action == "note":
            result = {"notes": book.notes(session, str(args.get("text") or ""))}
        elif action in {"list", "search"}:
            result = {"notes": book.notes(session), "sources": book.sources(session, str(args.get("query") or "")),
                      "retention": "7 days; this chat only; not personal memory"}
        else:
            return "Error: action must be list, search, read or note."
        return json.dumps(result, ensure_ascii=False)

    return [NativeTool(
        name="research_notebook",
        description="Recall sources and research notes from this chat (7 days). Read full pages by source_id and offset. "
                    "Use note to replace the working summary: findings, source IDs, disagreements and unanswered questions.",
        parameters={"type": "object", "properties": {
            "action": {"type": "string", "enum": ["list", "search", "read", "note"]},
            "query": {"type": "string"}, "source_id": {"type": "string"},
            "offset": {"type": "integer"}, "text": {"type": "string"},
        }, "required": ["action"]}, func=run)]
