"""Rolling summaries of long chats.

The agent sees the recent turns word for word (see LeanSession._history). Older
turns used to fall out of view entirely; now they are folded into one short
summary per chat that the system prompt carries as "Earlier in this chat".
The summary is updated in the background after a turn, a few turns at a time.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from config import DATA_DIR

# Turns the agent sees verbatim. Everything older is summarized.
RECENT_TURNS = 12
# Summarize once at least this many older turns are not covered yet.
BATCH_TURNS = 4
_MAX_INPUT_CHARS = 14000
_LOCK = threading.Lock()


def _path(session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(session_id or "default"))[:120]
    return Path(DATA_DIR) / "lean" / "summaries" / f"{safe}.json"


def load(session_id: str) -> dict[str, Any]:
    try:
        return json.loads(_path(session_id).read_text(encoding="utf-8"))
    except Exception:
        return {"summary": "", "covered": []}


def summary_text(session_id: str) -> str:
    return str(load(session_id).get("summary") or "").strip()


def _save(session_id: str, data: dict[str, Any]) -> None:
    path = _path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def _turn_lines(turn: dict[str, Any]) -> list[str]:
    lines = []
    for msg in turn.get("messages") or []:
        text = re.sub(r"\s+", " ", str(msg.get("text") or "")).strip()
        if not text:
            continue
        who = "User" if msg.get("role") == "user" else str(msg.get("agent_name") or "Assistant")
        lines.append(f"{who}: {text[:700]}")
    return lines


def update(session_id: str, client: Any) -> bool:
    """Fold uncovered older turns into the chat's summary. Returns True if it changed."""
    from agent.state import get_state_store

    with _LOCK:
        turns = get_state_store().session_timeline(session_id, limit=400).get("turns") or []
        older = turns[:-RECENT_TURNS] if len(turns) > RECENT_TURNS else []
        data = load(session_id)
        covered = set(data.get("covered") or [])
        fresh = [t for t in older if str(t.get("execution_id") or "") not in covered]
        if len(fresh) < BATCH_TURNS:
            return False
        body = "\n".join(line for t in fresh for line in _turn_lines(t))[-_MAX_INPUT_CHARS:]
        previous = str(data.get("summary") or "").strip()
        prompt = (
            "You keep a running summary of a long chat between a user and their AI agents, so the agents "
            "remember it later.\n\n"
            + (f"Summary so far:\n{previous}\n\n" if previous else "")
            + f"New messages to add:\n{body}\n\n"
            "Write the updated summary in at most 250 words. Keep decisions made, facts about the user and "
            "their projects, names, numbers, file paths, links, and anything still to do. Drop small talk. "
            "Plain sentences, no heading, no preamble."
        )
        turn = client.stream_turn([{"role": "user", "content": prompt}], temperature=0.2, max_tokens=900)
        text = re.sub(r"<think>.*?</think>", "", str(turn.content or ""), flags=re.S).strip()
        if not text:
            return False
        data = {
            "summary": text,
            "covered": sorted(covered | {str(t.get("execution_id") or "") for t in fresh}),
            "updated_at": time.time(),
        }
        _save(session_id, data)
        return True


def update_in_background(session_id: str, make_client: Any) -> Optional[threading.Thread]:
    def work() -> None:
        client = None
        try:
            client = make_client()
            if update(session_id, client):
                logger.info("Chat summary updated for {}", session_id)
        except Exception:
            logger.debug("Chat summary update failed", exc_info=True)
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()

    thread = threading.Thread(target=work, name="lean-chat-summary", daemon=True)
    thread.start()
    return thread
