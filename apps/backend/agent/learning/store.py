"""Where learning keeps what it knows: data/learning/experience.db.

A separate SQLite file, not more record kinds in agent/state.py: that store
loads every record into memory at startup, and episodes grow without bound.
Here everything is indexed and bounded (old episodes are pruned), and deleting
this one file resets learning without touching chats, memory or settings.

Every change to a lesson is written to lesson_events with the lesson before
and after, so the app can show its history and roll any change back.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from config import DATA_DIR

SCHEMA_VERSION = 1
MAX_EPISODES = 5000
# Outcomes kept per tool or search provider for "how is it doing lately".
RECENT_WINDOW = 20

# Why an episode turned out as it did (agent/learning/episodes.py classify_cause). Some causes aren't the
# agent's strategy at work, so they never count for or against a lesson or an agent: a missing dependency or
# an outage says nothing about whether "reproduce the bug first" helps, and counting it would retire good
# lessons (negative transfer). The owner's feedback can reclassify an episode (feedback_cause).
NOT_ATTRIBUTABLE = frozenset({"environment", "authorization", "tool_failure", "information_gap", "model_error", "misunderstood"})
LOSS_CAUSES = frozenset({"failed_check", "false_success", "action_failed", "stalled", "verifier_gap", "owner_failed"})

LESSON_STATUSES = ("pending_review", "probation", "established", "retired", "quarantined")
# Lessons an agent may read: approved ones only. pending_review waits for the owner.
ACTIVE_STATUSES = ("probation", "established")


@dataclass
class Episode:
    """One agent's part in one finished request, graded from what actually ran."""
    agent_id: str
    goal: str
    outcome: str  # success | failure | stopped | answered | error (the model call failed)
    id: str = field(default_factory=lambda: f"ep_{uuid.uuid4().hex[:12]}")
    created_at: float = field(default_factory=time.time)
    session_id: str = ""
    execution_id: str = ""
    agent_name: str = ""
    task_kind: str = "chat"
    level: int = 0  # verification ladder, V0..V4 (agent/learning/episodes.py)
    reasons: list[str] = field(default_factory=list)  # why it got that level
    trusted: bool = True  # False when the request read outside content
    feedback: int = 0  # owner's Worked (+1) / Didn't work (-1)
    feedback_note: str = ""
    summary: str = ""  # what was delivered, or why it stopped
    stop_reason: str = ""
    source: str = ""
    work_id: str = ""
    attempt_id: str = ""
    skill_versions: dict[str, str] = field(default_factory=dict)
    provider: str = ""
    model: str = ""
    team: bool = False
    tools: list[dict[str, Any]] = field(default_factory=list)  # {name, target, ok, output}
    taint: list[str] = field(default_factory=list)
    tamper: list[dict[str, Any]] = field(default_factory=list)
    lessons_used: list[str] = field(default_factory=list)
    # Lessons that matched this task but were held back at random: the lesson's control group.
    lessons_withheld: list[str] = field(default_factory=list)
    # What this episode counted for, for the lessons it used: win | loss | ''.
    # Kept so the owner's feedback can correct it later without double counting.
    attribution: str = ""
    # For model profiles (agent/learning/routing.py). error_kind says why a model call failed
    # (outage | config | rate_limit | context | other), so an outage never counts against a model.
    error_kind: str = ""
    duration_s: float = 0.0
    tokens: int = 0  # new tokens: input added plus replies (agent/lean/loop.py "fresh")
    role: str = "solo"  # solo | lead | worker
    routed: str = "user"  # who picked the model: the user's setting, or the router (auto mode)
    # The verification ledger's completion record (agent/lean/verification.py), for measured engineering work.
    verification: dict[str, Any] = field(default_factory=dict)
    # Why it turned out this way, from the evidence (episodes.classify_cause), and as the owner's
    # feedback reclassified it (episodes.feedback_cause); the owner's word wins while it stands.
    cause: str = ""
    feedback_cause: str = ""
    # owner: pressed Worked / Didn't work. implicit: their next message said so ("that worked", "that's wrong").
    feedback_source: str = ""
    # Where the work happened: chat | agents | code, and the project it was about (for project-scoped lessons).
    environment: str = ""
    project_id: str = ""
    # Proven lessons from another agent read on this task (transfer; counted apart from the home agent's results).
    lessons_shared: list[str] = field(default_factory=list)
    # Transferable lessons held back at random on this task: the control group for transfer.
    lessons_shared_withheld: list[str] = field(default_factory=list)
    # on | control (evaluation: placebo notes instead of lessons), and how many characters of lessons it read.
    learning_mode: str = "on"
    lesson_chars: int = 0

    @property
    def effective_cause(self) -> str:
        return self.feedback_cause or self.cause

    @property
    def confirmed(self) -> bool:
        """The owner said it worked with the button. A casual "thanks, that worked" isn't proof (implicit)."""
        return self.feedback > 0 and self.feedback_source != "implicit"

    @property
    def verified_success(self) -> bool:
        """Success backed by a check after the work (V2+), or the owner said it worked."""
        if self.feedback < 0 or self.outcome == "error":
            return False
        if self.confirmed:
            return self.outcome in {"success", "answered"}
        if self.effective_cause in LOSS_CAUSES or self.effective_cause in NOT_ATTRIBUTABLE:
            return False  # a passing read-back doesn't outweigh a test that still fails
        return self.outcome == "success" and self.level >= 2

    @property
    def failed(self) -> bool:
        """The agent's work fell short. A failed model call, a broken environment or a refusal is nobody's lesson."""
        if self.outcome == "error" or self.effective_cause in NOT_ATTRIBUTABLE:
            return False
        return self.feedback < 0 or self.outcome in {"failure", "stopped"} or self.effective_cause in LOSS_CAUSES

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "verified_success": self.verified_success, "failed": self.failed,
                "effective_cause": self.effective_cause}


@dataclass
class Lesson:
    """A short strategy an agent reads before similar tasks. Advisory text only."""
    agent_id: str
    title: str
    text: str
    id: str = field(default_factory=lambda: f"ls_{uuid.uuid4().hex[:12]}")
    status: str = "probation"
    kind: str = "do"  # do | avoid
    task_kind: str = ""
    trusted: bool = True
    origin: str = "reflection"  # reflection | owner
    source_episodes: list[str] = field(default_factory=list)
    level: int = 0  # verification level of the best episode behind it
    uses: int = 0
    wins: int = 0
    losses: int = 0
    # Checked results of matching tasks where it was held back at random (playbook.holdout).
    held_wins: int = 0
    held_losses: int = 0
    last_used_at: float = 0.0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    edited: bool = False
    note: str = ""  # why it has its status (shown to the owner)
    # Set when it was learned about one project: only read in that project.
    project_id: str = ""
    # Uses by other agents once proven (transfer), counted apart from its home results. Transfer stops
    # when it fails there (negative transfer), without touching the lesson's standing with its own agent.
    shared_uses: int = 0
    shared_wins: int = 0
    shared_losses: int = 0
    transfer: bool = True
    # Matching tasks in other agents' work where it was held back at random: transfer's control group.
    shared_held_wins: int = 0
    shared_held_losses: int = 0
    # What a waiting lesson waits for: "owner" (risky: only the owner releases it) or "confirmation"
    # (learned while reading outside content: released when work in another conversation independently teaches it).
    awaiting: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Lesson":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


def _episode_from(row: sqlite3.Row) -> Episode:
    data = json.loads(row["body"])
    known = {f for f in Episode.__dataclass_fields__}  # type: ignore[attr-defined]
    return Episode(**{k: v for k, v in data.items() if k in known})


def _lesson_from(row: sqlite3.Row) -> Lesson:
    return Lesson.from_dict(json.loads(row["body"]))


class ExperienceStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=10)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
            columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(reflections)")}
            if "priority" not in columns:  # databases from before 11.11.7
                self._conn.execute("ALTER TABLE reflections ADD COLUMN priority INTEGER NOT NULL DEFAULT 4")
            self._conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema', ?)", (str(SCHEMA_VERSION),))

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ── episodes ────────────────────────────────────────────────────────
    def add_episode(self, episode: Episode) -> Episode:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO episodes(id, created_at, session_id, execution_id, agent_id, task_kind, outcome,"
                " level, trusted, feedback, body) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (episode.id, episode.created_at, episode.session_id, episode.execution_id, episode.agent_id,
                 episode.task_kind, episode.outcome, episode.level, int(episode.trusted), episode.feedback,
                 json.dumps(asdict(episode), ensure_ascii=False)),
            )
        return episode

    update_episode = add_episode

    def get_episode(self, episode_id: str) -> Optional[Episode]:
        with self._lock:
            row = self._conn.execute("SELECT body FROM episodes WHERE id = ?", (episode_id,)).fetchone()
        return _episode_from(row) if row else None

    def _episode_filter(self, agent_id: str, task_kind: str, since: float, execution_id: str,
                        role: str, session_id: str = "") -> tuple[str, list[Any]]:
        clauses, params = ["created_at >= ?"], [since]
        for column, value in (("agent_id", agent_id), ("task_kind", task_kind), ("execution_id", execution_id),
                              ("session_id", session_id)):
            if value:
                clauses.append(f"{column} = ?")
                params.append(value)
        if role == "worker":
            clauses.append("json_extract(body, '$.role') = 'worker'")
        elif role == "own":  # the agents' own replies: everything but research workers
            clauses.append("COALESCE(json_extract(body, '$.role'), 'solo') != 'worker'")
        return " AND ".join(clauses), params

    def episodes(self, *, agent_id: str = "", task_kind: str = "", since: float = 0.0, limit: int = 50,
                 execution_id: str = "", offset: int = 0, role: str = "", session_id: str = "") -> list[Episode]:
        where, params = self._episode_filter(agent_id, task_kind, since, execution_id, role, session_id)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT body FROM episodes WHERE {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (*params, max(1, int(limit)), max(0, int(offset))),
            ).fetchall()
        return [_episode_from(row) for row in rows]

    def count_episodes(self, *, agent_id: str = "", role: str = "") -> int:
        where, params = self._episode_filter(agent_id, "", 0.0, "", role)
        with self._lock:
            return int(self._conn.execute(f"SELECT COUNT(*) FROM episodes WHERE {where}", params).fetchone()[0])

    def reset(self) -> None:
        """Forget everything learned: episodes, lessons and their history, statistics, queued reviews.

        Per-agent pauses are kept: they are the owner's settings, not something learned.
        """
        with self._lock, self._conn:
            for table in ("episodes", "lessons", "lesson_events", "reliability", "reflections"):
                self._conn.execute(f"DELETE FROM {table}")

    def prune(self, keep: int = MAX_EPISODES) -> int:
        """Drop the oldest episodes beyond ``keep``. Lessons keep their ids as provenance."""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "DELETE FROM episodes WHERE id IN (SELECT id FROM episodes ORDER BY created_at DESC LIMIT -1 OFFSET ?)",
                (keep,),
            )
            self._conn.execute("DELETE FROM reflections WHERE episode_id NOT IN (SELECT id FROM episodes)")
            return cur.rowcount or 0

    # ── lessons ─────────────────────────────────────────────────────────
    def add_lesson(self, lesson: Lesson, *, actor: str, reason: str = "") -> Lesson:
        with self._lock, self._conn:
            self._write_lesson(lesson)
            self._event(lesson.id, actor, "created", reason, None, lesson)
        return lesson

    def update_lesson(self, lesson: Lesson, *, actor: str, action: str, reason: str = "",
                      before: Optional[Lesson] = None, quiet: bool = False) -> Lesson:
        """Save a change and log it. ``quiet`` skips the log for a plain use with no result."""
        with self._lock, self._conn:
            if before is None and not quiet:
                before = self.get_lesson(lesson.id)
            lesson.updated_at = time.time()
            self._write_lesson(lesson)
            if not quiet:
                self._event(lesson.id, actor, action, reason, before, lesson)
        return lesson

    def delete_lesson(self, lesson_id: str, *, actor: str, reason: str = "") -> bool:
        with self._lock, self._conn:
            before = self.get_lesson(lesson_id)
            if before is None:
                return False
            self._conn.execute("DELETE FROM lessons WHERE id = ?", (lesson_id,))
            # Kept, so the deletion itself is on record and can be undone.
            self._event(lesson_id, actor, "deleted", reason, before, None)
        return True

    def get_lesson(self, lesson_id: str) -> Optional[Lesson]:
        with self._lock:
            row = self._conn.execute("SELECT body FROM lessons WHERE id = ?", (lesson_id,)).fetchone()
        return _lesson_from(row) if row else None

    def lessons(self, *, agent_id: str = "", statuses: Iterable[str] = ()) -> list[Lesson]:
        clauses, params = [], []
        if agent_id:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        statuses = list(statuses)
        if statuses:
            clauses.append(f"status IN ({','.join('?' * len(statuses))})")
            params.extend(statuses)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._conn.execute(f"SELECT body FROM lessons {where} ORDER BY updated_at DESC", params).fetchall()
        return [_lesson_from(row) for row in rows]

    def lesson_events(self, lesson_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM lesson_events WHERE lesson_id = ? ORDER BY id", (lesson_id,)
            ).fetchall()
        return [_event_dict(row) for row in rows]

    def lesson_event(self, event_id: int) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM lesson_events WHERE id = ?", (int(event_id),)).fetchone()
        return _event_dict(row) if row else None

    def _write_lesson(self, lesson: Lesson) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO lessons(id, agent_id, status, task_kind, created_at, updated_at, body)"
            " VALUES (?,?,?,?,?,?,?)",
            (lesson.id, lesson.agent_id, lesson.status, lesson.task_kind, lesson.created_at, lesson.updated_at,
             json.dumps(lesson.to_dict(), ensure_ascii=False)),
        )

    def _event(self, lesson_id: str, actor: str, action: str, reason: str,
               before: Optional[Lesson], after: Optional[Lesson]) -> None:
        self._conn.execute(
            "INSERT INTO lesson_events(lesson_id, at, actor, action, reason, before, after) VALUES (?,?,?,?,?,?,?)",
            (lesson_id, time.time(), actor, action, str(reason or "")[:500],
             json.dumps(before.to_dict(), ensure_ascii=False) if before else None,
             json.dumps(after.to_dict(), ensure_ascii=False) if after else None),
        )

    # ── reliability ─────────────────────────────────────────────────────
    def record_outcome(self, kind: str, name: str, ok: bool, *, at: Optional[float] = None) -> None:
        now = float(at or time.time())
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT ok, failed, recent, last_failure_at FROM reliability WHERE kind = ? AND name = ?", (kind, name)
            ).fetchone()
            ok_n, fail_n, recent, last_fail = (row["ok"], row["failed"], row["recent"], row["last_failure_at"]) if row else (0, 0, "", 0.0)
            recent = (recent + ("1" if ok else "0"))[-RECENT_WINDOW:]
            self._conn.execute(
                "INSERT OR REPLACE INTO reliability(kind, name, ok, failed, recent, last_failure_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (kind, name, ok_n + int(ok), fail_n + int(not ok), recent, last_fail if ok else now, now),
            )

    def reliability(self, kind: str = "") -> list[dict[str, Any]]:
        with self._lock:
            if kind:
                rows = self._conn.execute("SELECT * FROM reliability WHERE kind = ? ORDER BY name", (kind,)).fetchall()
            else:
                rows = self._conn.execute("SELECT * FROM reliability ORDER BY kind, name").fetchall()
        return [dict(row) for row in rows]

    # ── reflection queue ────────────────────────────────────────────────
    def queue_reflection(self, episode_id: str, agent_id: str, priority: int = 4) -> None:
        """Queue (or re-queue) a review. Lower ``priority`` is reviewed sooner (reflector.priority)."""
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO reflections(episode_id, agent_id, status, created_at, priority) VALUES (?,?, 'pending', ?, ?)"
                " ON CONFLICT(episode_id) DO UPDATE SET status = 'pending', priority = MIN(reflections.priority, excluded.priority)"
                " WHERE reflections.status IN ('done', 'skipped', 'pending')",
                (episode_id, agent_id, time.time(), int(priority)),
            )

    def pending_reflections(self, limit: int = 5) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM reflections WHERE status = 'pending' ORDER BY priority, created_at LIMIT ?", (max(1, limit),)
            ).fetchall()
        return [dict(row) for row in rows]

    def finish_reflection(self, episode_id: str, status: str, note: str = "") -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE reflections SET status = ?, note = ?, finished_at = ?, attempts = attempts + 1 WHERE episode_id = ?",
                (status, str(note or "")[:500], time.time(), episode_id),
            )

    def reflection_status(self, episode_id: str) -> str:
        with self._lock:
            row = self._conn.execute("SELECT status FROM reflections WHERE episode_id = ?", (episode_id,)).fetchone()
        return str(row["status"]) if row else ""

    def reflections_since(self, since: float) -> int:
        """Model calls spent on reflection since ``since`` (finished, whatever the result)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM reflections WHERE finished_at >= ? AND status IN ('done', 'failed')", (since,)
            ).fetchone()
        return int(row["n"] or 0)

    # ── per-agent switches ──────────────────────────────────────────────
    def set_paused(self, agent_id: str, paused: bool) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO agent_settings(agent_id, paused, updated_at) VALUES (?,?,?)",
                (agent_id, int(bool(paused)), time.time()),
            )

    def paused_agents(self) -> set[str]:
        with self._lock:
            rows = self._conn.execute("SELECT agent_id FROM agent_settings WHERE paused = 1").fetchall()
        return {str(row["agent_id"]) for row in rows}

    # ── overview ────────────────────────────────────────────────────────
    def counts(self) -> dict[str, Any]:
        with self._lock:
            episodes = self._conn.execute("SELECT COUNT(*) AS n FROM episodes").fetchone()["n"]
            lessons = {row["status"]: row["n"] for row in self._conn.execute(
                "SELECT status, COUNT(*) AS n FROM lessons GROUP BY status").fetchall()}
            pending = self._conn.execute("SELECT COUNT(*) AS n FROM reflections WHERE status = 'pending'").fetchone()["n"]
        return {"episodes": int(episodes), "lessons": {s: int(lessons.get(s, 0)) for s in LESSON_STATUSES},
                "reflections_pending": int(pending)}


def _event_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    for key in ("before", "after"):
        data[key] = json.loads(data[key]) if data.get(key) else None
    return data


_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS episodes (
    id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    session_id TEXT NOT NULL DEFAULT '',
    execution_id TEXT NOT NULL DEFAULT '',
    agent_id TEXT NOT NULL,
    task_kind TEXT NOT NULL DEFAULT 'chat',
    outcome TEXT NOT NULL,
    level INTEGER NOT NULL DEFAULT 0,
    trusted INTEGER NOT NULL DEFAULT 1,
    feedback INTEGER NOT NULL DEFAULT 0,
    body TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS episodes_agent ON episodes(agent_id, created_at);
CREATE INDEX IF NOT EXISTS episodes_execution ON episodes(execution_id);
CREATE TABLE IF NOT EXISTS lessons (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    status TEXT NOT NULL,
    task_kind TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    body TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS lessons_agent ON lessons(agent_id, status);
CREATE TABLE IF NOT EXISTS lesson_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lesson_id TEXT NOT NULL,
    at REAL NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    before TEXT,
    after TEXT
);
CREATE INDEX IF NOT EXISTS lesson_events_lesson ON lesson_events(lesson_id, id);
CREATE TABLE IF NOT EXISTS reliability (
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    ok INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    recent TEXT NOT NULL DEFAULT '',
    last_failure_at REAL NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,
    PRIMARY KEY (kind, name)
);
CREATE TABLE IF NOT EXISTS reflections (
    episode_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at REAL NOT NULL,
    finished_at REAL NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS reflections_status ON reflections(status, created_at);
CREATE TABLE IF NOT EXISTS agent_settings (
    agent_id TEXT PRIMARY KEY,
    paused INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL
);
"""


_STORE: Optional[ExperienceStore] = None
_STORE_LOCK = threading.Lock()


def get_experience_store() -> ExperienceStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = ExperienceStore(Path(DATA_DIR) / "learning" / "experience.db")
        return _STORE


def set_experience_store(store: Optional[ExperienceStore]) -> None:
    """Swap the store (tests use a fresh file per test)."""
    global _STORE
    with _STORE_LOCK:
        _STORE = store
