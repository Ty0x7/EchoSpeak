"""Replayable transport output. Execution and tool authority remain in the runtime."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from config import DATA_DIR


class QueryJournal:
    def __init__(self, path=None):
        self.path = Path(path or Path(DATA_DIR) / "query-streams.sqlite3")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, session TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, status TEXT NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS events(run TEXT NOT NULL, seq INTEGER NOT NULL,
                    body TEXT NOT NULL, PRIMARY KEY(run,seq));
                CREATE INDEX IF NOT EXISTS run_session ON runs(session,updated);
            """)
            interrupted = db.execute("SELECT id FROM runs WHERE status='running'").fetchall()
            for row in interrupted:
                self._append(db, row[0], {"type": "error", "message": "EchoSpeak restarted during this run. Saved output is available; tools were not automatically run again.", "request_id": row[0]})
            db.execute("UPDATE runs SET status='interrupted' WHERE status='running'")
        self.cleanup()

    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA secure_delete=ON")
        return db

    def cleanup(self):
        with self.lock, self.connect() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM runs WHERE status!='running' AND updated<?", (time.time() - 2 * 86400,))]
            db.executemany("DELETE FROM events WHERE run=?", [(x,) for x in ids])
            db.executemany("DELETE FROM runs WHERE id=?", [(x,) for x in ids])

    def claim(self, run, session, fingerprint):
        with self.lock, self.connect() as db:
            old = db.execute("SELECT * FROM runs WHERE id=?", (run,)).fetchone()
            if old:
                if old["session"] != session or old["fingerprint"] != fingerprint:
                    raise ValueError("This request ID already belongs to a different chat or request.")
                return False
            active = db.execute("SELECT id FROM runs WHERE session=? AND status='running'", (session,)).fetchone()
            if active:
                raise ValueError("This chat already has a running request. Reconnect or stop it before starting another.")
            db.execute("INSERT INTO runs VALUES(?,?,?,'running',?)", (run, session, fingerprint, time.time()))
            return True

    @staticmethod
    def _append(db, run, event):
        seq = db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM events WHERE run=?", (run,)).fetchone()[0]
        event = {**event, "_replay_seq": seq}
        db.execute("INSERT INTO events VALUES(?,?,?)", (run, seq, json.dumps(event, ensure_ascii=False)))
        db.execute("UPDATE runs SET updated=? WHERE id=?", (time.time(), run))

    def append(self, run, event):
        # Bound oversized audio packets while preserving their text transcript.
        if event.get("type") == "voice_audio" and len(json.dumps(event)) > 2_000_000:
            event = {"type": "audio_omitted", "message": "Oversized audio frame omitted; transcript remains available."}
        with self.lock, self.connect() as db:
            if db.execute("SELECT 1 FROM runs WHERE id=?", (run,)).fetchone():
                self._append(db, run, event)

    def clear(self, session):
        with self.lock, self.connect() as db:
            db.execute("DELETE FROM events WHERE run IN (SELECT id FROM runs WHERE session=?)", (session,))
            db.execute("DELETE FROM runs WHERE session=?", (session,))

    def finish(self, run, status="completed"):
        with self.lock, self.connect() as db:
            db.execute("UPDATE runs SET status=?,updated=? WHERE id=?", (status, time.time(), run))
        self.cleanup()

    def get(self, run, session):
        with self.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id=? AND session=?", (run, session)).fetchone()
            return dict(row) if row else None

    def list(self, session):
        self.cleanup()
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT id,session,status,updated FROM runs WHERE session=? ORDER BY updated DESC LIMIT 10", (session,))]

    def read(self, run, after):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT body FROM events WHERE run=? AND seq>? ORDER BY seq LIMIT 100", (run, after))]


_STORE = None
_LOCK = threading.Lock()


def get_query_journal():
    global _STORE
    with _LOCK:
        if _STORE is None:
            _STORE = QueryJournal()
    return _STORE
