"""What the owner's agents are doing: Echo Companion's observation layer.

Sources, all read-only:

- **Claude Code and Codex sessions the owner runs directly** (Workenv, tmux). These come
  from their official hooks via the spool written by ``hook_client.py``.
- **Echo's own conversations, including named agents.** These come from
  ``agent.session_overview``, the same list the terminal session manager shows.

Events are normalised, deduplicated and kept in one small SQLite file. Each session gets a
state derived from its events:

- ``working``: a prompt was sent and no reply has arrived yet.
- ``your_turn``: a turn finished and the agent is waiting for the owner.
- ``needs_permission``: the agent asked to be allowed to do something.
- ``ended``: the session closed.
- ``quiet`` and ``idle``: ``working`` or ``your_turn`` with no events for a long time.

"Interacting" is the session the owner most recently prompted. When two sessions were
prompted within a few seconds of each other, the answer is reported as uncertain rather
than guessed.

Everything an agent wrote is untrusted text. It is stored and summarised, and never
followed as an instruction. A finished turn is not treated as a finished objective.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Optional

from config import DATA_DIR

from agent.companion.text import clean, is_system_prompt, project_label, task_title

COMPANION_HOME = Path(os.environ.get("ECHOSPEAK_COMPANION_HOME") or os.path.expanduser("~/.local/share/echospeak/companion"))
RETAIN_SECONDS = 30 * 86400
RETAIN_ROWS = 20000
QUIET_AFTER = 45 * 60
IDLE_AFTER = 30 * 60
INTERACTING_WINDOW = 15 * 60
UNCERTAIN_GAP = 30.0

AGENT_LABELS = {"claude-code": "Claude Code", "codex": "Codex", "echo": "Echo", "auto-test-fix": "The test-fix loop"}

# Official hook / notify event -> companion kind.
KINDS = {
    "SessionStart": "session_started",
    "UserPromptSubmit": "prompt",
    "Stop": "turn_completed",
    "notify": "turn_completed",  # Codex notify: agent-turn-complete
    "SubagentStop": "subagent_completed",
    "SubagentStart": "subagent_started",
    "Notification": "attention",
    "PermissionRequest": "needs_permission",
    "SessionEnd": "session_ended",
    "PreCompact": "compacting",
}


def _text(value: Any, limit: int) -> str:
    return str(value or "")[:limit]


def normalise(record: dict[str, Any]) -> Optional[dict[str, Any]]:
    """One spool record -> one companion event, or None when it isn't useful."""

    agent = str(record.get("agent") or "")
    event = record.get("event") if isinstance(record.get("event"), dict) else {}
    if agent not in {"claude-code", "codex"}:
        return None
    name = str(event.get("hook_event_name") or "")
    if name == "notify" and str(event.get("type") or "") != "agent-turn-complete":
        return None
    kind = KINDS.get(name)
    if not kind:
        return None
    session = _text(event.get("session_id") or event.get("thread-id"), 200)
    if not session:
        return None
    turn = _text(event.get("turn_id") or event.get("turn-id") or event.get("prompt_id"), 200)
    message = event.get("last_assistant_message") or event.get("last-assistant-message") or ""
    if kind == "prompt":
        text = event.get("prompt") or ""
        if is_system_prompt(text):  # harness traffic through the prompt hook, never the owner's request
            kind, text = "system_prompt", ""
    elif kind in {"attention", "needs_permission"}:
        text = event.get("message") or event.get("tool_name") or ""
    else:
        text = message
    if kind == "attention":
        note = f"{event.get('notification_type', '')} {event.get('message', '')}".lower()
        if "permission" in note or "approv" in note:
            kind = "needs_permission"
        elif "idle" in note or "waiting for your input" in note:
            kind = "waiting_input"
    at = float(record.get("received_at") or time.time())
    # A Codex turn can arrive both from the Stop hook and from notify: one turn, one event.
    basis = [agent, session, kind, turn] if turn and kind in {"turn_completed", "prompt", "subagent_completed"} \
        else [agent, session, kind, turn, round(at, 1), _text(text, 200)]
    ident = hashlib.sha256(json.dumps(basis).encode()).hexdigest()[:24]
    cwd = _text(event.get("cwd"), 1000)
    return {
        "id": ident, "agent": agent, "session_id": session, "kind": kind, "at": at, "turn_id": turn,
        "cwd": cwd, "project": Path(cwd).name if cwd else "", "text": _text(text, 12000),
        "transcript_path": _text(event.get("transcript_path"), 1000),
        "subagent": _text(event.get("agent_type"), 100),
        "meta": {k: event[k] for k in ("source", "reason", "permission_mode", "model") if k in event},
    }


class CompanionStore:
    def __init__(self, path: Optional[Path] = None, spool: Optional[Path] = None):
        self.path = Path(path or (Path(DATA_DIR) / "companion" / "observe.sqlite3"))
        self.spool = Path(spool or (COMPANION_HOME / "spool"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._listeners: list[Any] = []
        with self._db() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY, agent TEXT, session_id TEXT, kind TEXT, at REAL, turn_id TEXT,
                    cwd TEXT, project TEXT, text TEXT, transcript_path TEXT, subagent TEXT, meta TEXT);
                CREATE INDEX IF NOT EXISTS events_session ON events(agent, session_id, at);
                CREATE INDEX IF NOT EXISTS events_at ON events(at);
                CREATE TABLE IF NOT EXISTS handled (id TEXT PRIMARY KEY, at REAL);
                CREATE TABLE IF NOT EXISTS sessions (
                    agent TEXT, session_id TEXT, cwd TEXT, project TEXT, state TEXT,
                    started_at REAL, updated_at REAL, last_prompt TEXT, last_prompt_at REAL,
                    last_message TEXT, last_message_at REAL, turns INTEGER DEFAULT 0,
                    attention TEXT, transcript_path TEXT, PRIMARY KEY (agent, session_id));
                """
            )
            added = False
            for column in ("task TEXT", "turn_started_at REAL"):
                try:
                    db.execute(f"ALTER TABLE sessions ADD COLUMN {column}")
                    added = True
                except sqlite3.OperationalError:
                    pass  # already there
            if added:
                self._migrate_sessions(db)

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _migrate_sessions(db: sqlite3.Connection) -> None:
        """Sessions recorded before tasks existed: the task from the owner's last real request, and the
        project from where the session started (older code followed the agent's cd)."""
        for row in db.execute("SELECT agent, session_id FROM sessions").fetchall():
            key = (row["agent"], row["session_id"])
            prompts = db.execute("SELECT text FROM events WHERE agent=? AND session_id=? AND kind='prompt' ORDER BY at DESC",
                                 key).fetchall()
            task = next((t for t in (task_title(p["text"] or "") for p in prompts) if t), "")
            first = db.execute("SELECT cwd FROM events WHERE agent=? AND session_id=? AND cwd != '' ORDER BY at LIMIT 1",
                               key).fetchone()
            project = (project_label(first["cwd"]) or Path(first["cwd"]).name) if first else None
            db.execute("UPDATE sessions SET task=COALESCE(NULLIF(task, ''), ?), project=COALESCE(?, project) "
                       "WHERE agent=? AND session_id=?", (task, project, *key))

    def on_event(self, callback) -> None:
        self._listeners.append(callback)

    # ---- ingest -------------------------------------------------------------
    def ingest_spool(self) -> int:
        """Read and remove spool files. Malformed files are discarded; returns new events."""

        if not self.spool.is_dir():
            return 0
        files = sorted(p for p in self.spool.glob("*.json") if p.is_file())
        added = 0
        for path in files[:2000]:
            try:
                if path.stat().st_size > 4 * 1024 * 1024:
                    raise ValueError("too large")
                record = json.loads(path.read_text(encoding="utf-8"))
                event = normalise(record) if isinstance(record, dict) else None
                if event and self.add(event):
                    added += 1
            except (OSError, ValueError):
                pass
            finally:
                try:
                    path.unlink()
                except OSError:
                    pass
        return added

    def add(self, event: dict[str, Any]) -> bool:
        with self._lock, self._db() as db:
            inserted = db.execute(
                "INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (event["id"], event["agent"], event["session_id"], event["kind"], event["at"], event.get("turn_id", ""),
                 event.get("cwd", ""), event.get("project", ""), event.get("text", ""),
                 event.get("transcript_path", ""), event.get("subagent", ""), json.dumps(event.get("meta", {}))),
            ).rowcount
            if not inserted:
                return False
            self._apply(db, event)
            row = db.execute("SELECT project, task, turn_started_at FROM sessions WHERE agent=? AND session_id=?",
                             (event["agent"], event["session_id"])).fetchone()
            if row is not None:  # downstream sees the session's stable project and the owner's task
                event["project"] = row["project"] or event.get("project", "")
                event["task"] = row["task"] or ""
                event["turn_started_at"] = float(row["turn_started_at"] or 0)
        for callback in list(self._listeners):
            try:
                callback(event)
            except Exception:
                pass
        return True

    def _apply(self, db: sqlite3.Connection, event: dict[str, Any]) -> None:
        key = (event["agent"], event["session_id"])
        row = db.execute("SELECT * FROM sessions WHERE agent=? AND session_id=?", key).fetchone()
        if row is None:
            cwd = event.get("cwd", "")
            # The project is fixed when the session starts: an agent changing directory doesn't change it.
            project = (project_label(cwd) if cwd else None) or event.get("project", "")
            db.execute(
                "INSERT INTO sessions (agent, session_id, cwd, project, state, started_at, updated_at, turns, transcript_path)"
                " VALUES (?,?,?,?,?,?,?,0,?)",
                (*key, cwd, project, "your_turn", event["at"], event["at"], event.get("transcript_path", "")),
            )
            row = {"project": project}
        updates: dict[str, Any] = {"updated_at": event["at"]}
        if not row["project"] and event.get("cwd"):
            updates.update(cwd=event["cwd"], project=project_label(event["cwd"]) or event.get("project", ""))
        if event.get("transcript_path"):
            updates["transcript_path"] = event["transcript_path"]
        kind = event["kind"]
        if kind == "prompt":
            updates.update(state="working", last_prompt=clean(event.get("text", ""))[:600], last_prompt_at=event["at"],
                           attention="", turn_started_at=event["at"])
            title = task_title(event.get("text", ""))
            if title:  # "continue" keeps the task it continues
                updates["task"] = title
        elif kind == "system_prompt":  # the agent works on, but the owner didn't ask anything new
            updates["state"] = "working"
            if not isinstance(row, sqlite3.Row) or row["state"] != "working":
                updates["turn_started_at"] = event["at"]
        elif kind == "turn_completed":
            updates.update(state="your_turn", last_message=event.get("text", ""), last_message_at=event["at"], attention="")
            db.execute("UPDATE sessions SET turns = turns + 1 WHERE agent=? AND session_id=?", key)
        elif kind == "needs_permission":
            updates.update(state="needs_permission", attention=event.get("text", ""))
        elif kind == "waiting_input":
            updates.update(state="your_turn")
        elif kind == "session_ended":
            updates.update(state="ended")
        assignments = ", ".join(f"{name}=?" for name in updates)
        db.execute(f"UPDATE sessions SET {assignments} WHERE agent=? AND session_id=?", (*updates.values(), *key))

    def mark_handled(self, event_id: str) -> None:
        with self._lock, self._db() as db:
            db.execute("INSERT OR IGNORE INTO handled VALUES (?, ?)", (event_id, time.time()))

    def unhandled(self, since: float, kinds: Iterable[str] = ("turn_completed", "report_ready")) -> list[dict[str, Any]]:
        """Events the commentary pipeline never finished (the backend stopped first), oldest first."""
        kinds = list(kinds)
        with self._db() as db:
            rows = [dict(row) for row in db.execute(
                f"SELECT * FROM events WHERE at > ? AND kind IN ({','.join('?' * len(kinds))}) "
                "AND id NOT IN (SELECT id FROM handled) ORDER BY at", [since, *kinds])]
        for row in rows:
            row["meta"] = json.loads(row.get("meta") or "{}")
        return rows

    def prune(self) -> None:
        with self._lock, self._db() as db:
            db.execute("DELETE FROM events WHERE at < ?", (time.time() - RETAIN_SECONDS,))
            db.execute("DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY at DESC LIMIT ?)", (RETAIN_ROWS,))
            db.execute("DELETE FROM sessions WHERE updated_at < ?", (time.time() - RETAIN_SECONDS,))
            db.execute("DELETE FROM handled WHERE at < ?", (time.time() - RETAIN_SECONDS,))

    # ---- read ---------------------------------------------------------------
    def events(self, *, since: float = 0.0, agent: str = "", session_id: str = "", kinds: Iterable[str] = (),
               limit: int = 200) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM events WHERE at > ?", [since]
        if agent:
            sql, args = sql + " AND agent = ?", args + [agent]
        if session_id:
            sql, args = sql + " AND session_id = ?", args + [session_id]
        kinds = list(kinds)
        if kinds:
            sql += f" AND kind IN ({','.join('?' * len(kinds))})"
            args += kinds
        sql += " ORDER BY at DESC LIMIT ?"
        args.append(max(1, min(int(limit), 2000)))
        with self._db() as db:
            rows = [dict(row) for row in db.execute(sql, args)]
        for row in rows:
            row["meta"] = json.loads(row.get("meta") or "{}")
        return rows

    def sessions(self, now: Optional[float] = None) -> list[dict[str, Any]]:
        now = time.time() if now is None else now
        with self._db() as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM sessions ORDER BY updated_at DESC LIMIT 200")]
        for row in rows:
            for key in ("cwd", "project", "last_prompt", "last_message", "attention", "transcript_path"):
                row[key] = row.get(key) or ""
            quiet_for = now - float(row["updated_at"] or 0)
            if row["state"] == "working" and quiet_for > QUIET_AFTER:
                row["state"] = "quiet"
            elif row["state"] == "your_turn" and quiet_for > IDLE_AFTER:
                row["state"] = "idle"
            row["label"] = AGENT_LABELS.get(row["agent"]) or row["agent"].replace("-", " ").title()
        return rows


def echo_sessions(limit: int = 30) -> list[dict[str, Any]]:
    """Echo's own conversations (and named agents) in the companion's session shape."""

    try:
        from agent.session_overview import overview

        items = overview(limit=limit)
    except Exception:
        return []
    # An idle Echo conversation is resting, not waiting on the owner; only a question is "your turn".
    states = {"running": "working", "streaming": "working", "needs_approval": "needs_permission",
              "needs_answer": "your_turn", "failed": "idle", "idle": "idle"}
    rows = []
    for item in items:
        agents = item.get("agents") or [{"id": "echo", "name": "Echo"}]
        name = ", ".join(str(a.get("name") or a.get("id")) for a in agents)
        project = item.get("project") or {}
        rows.append({
            "agent": "echo", "label": name if item.get("kind") != "echo" else "Echo",
            "session_id": str(item.get("thread_id") or ""), "cwd": str(project.get("root") or ""),
            "project": str(project.get("name") or ""), "state": states.get(str(item.get("status") or "idle"), "idle"),
            "started_at": float(item.get("created_at") or 0), "updated_at": float(item.get("last_active_at") or 0),
            "last_prompt": str(item.get("last_request") or ""), "last_message": str(item.get("preview") or ""),
            "title": str(item.get("title") or ""), "turns": int(item.get("message_count") or 0) // 2,
        })
    return rows


def activity(store: CompanionStore, *, include_echo: bool = True, now: Optional[float] = None,
             ingest: bool = True) -> dict[str, Any]:
    """Who is doing what right now, and which session the owner is interacting with."""

    now = time.time() if now is None else now
    if ingest:
        store.ingest_spool()
    rows = [r for r in store.sessions(now) if r["state"] != "ended" or now - float(r["updated_at"] or 0) < 3600]
    if include_echo:
        known = {r["session_id"] for r in rows}  # native events already describe these conversations
        rows += [r for r in echo_sessions() if r["session_id"] not in known and now - float(r["updated_at"] or 0) < 86400]
    prompted = sorted(
        (r for r in rows if r["state"] not in {"ended"} and now - float(r.get("last_prompt_at") or r["updated_at"] or 0) < INTERACTING_WINDOW
         and (r.get("last_prompt_at") or r["agent"] == "echo")),
        key=lambda r: float(r.get("last_prompt_at") or r["updated_at"] or 0), reverse=True,
    )
    focus: dict[str, Any] = {"agent": "", "session_id": "", "label": "", "project": "", "uncertain": False,
                             "method": "latest_prompt", "candidates": []}
    if prompted:
        top = prompted[0]
        top_at = float(top.get("last_prompt_at") or top["updated_at"] or 0)
        close = [r for r in prompted[1:] if top_at - float(r.get("last_prompt_at") or r["updated_at"] or 0) < UNCERTAIN_GAP]
        focus.update(agent=top["agent"], session_id=top["session_id"], label=top["label"], project=top.get("project", ""))
        if close:
            focus["uncertain"] = True
            focus["candidates"] = [{"agent": r["agent"], "session_id": r["session_id"], "label": r["label"]} for r in [top, *close]]
    for row in rows:
        row["interacting"] = bool(focus["session_id"]) and row["session_id"] == focus["session_id"] and row["agent"] == focus["agent"]
        row["background"] = row["state"] == "working" and not row["interacting"]
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["state"]] = counts.get(row["state"], 0) + 1
    return {"at": now, "focus": focus, "sessions": rows, "counts": counts}


_store: Optional[CompanionStore] = None
_store_lock = threading.Lock()


def get_companion_store() -> CompanionStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = CompanionStore()
        return _store
