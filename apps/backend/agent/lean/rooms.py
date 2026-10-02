"""Rooms: direct chats with one agent, and group chats with several.

A room is backed by an ordinary EchoSpeak Session (thread), so history,
tool runs, and reload all use the existing durable records.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from config import DATA_DIR

_STORE_PATH = Path(DATA_DIR) / "lean" / "rooms.json"


class Room(BaseModel):
    id: str
    name: str
    kind: str = "group"  # group | direct
    agent_ids: list[str] = Field(default_factory=list)
    thread_id: str
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    last_message_at: float = 0.0
    last_preview: str = ""


class RoomStore:
    def __init__(self, path: Path = _STORE_PATH) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._rooms: dict[str, Room] = {}
        if path.exists():
            try:
                for row in json.loads(path.read_text(encoding="utf-8")).get("rooms") or []:
                    room = Room(**row)
                    self._rooms[room.id] = room
            except Exception:
                self._rooms = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps({"version": 1, "rooms": [r.model_dump() for r in self._rooms.values()]}, indent=2, ensure_ascii=False), encoding="utf-8")
        temp.replace(self.path)

    def list(self) -> list[Room]:
        with self._lock:
            rooms = [room.model_copy() for room in self._rooms.values()]
        rooms.sort(key=lambda r: max(r.last_message_at, r.updated_at), reverse=True)
        return rooms

    def get(self, room_id: str) -> Optional[Room]:
        with self._lock:
            room = self._rooms.get(room_id)
            return room.model_copy() if room else None

    def by_thread(self, thread_id: str) -> Optional[Room]:
        with self._lock:
            for room in self._rooms.values():
                if room.thread_id == thread_id:
                    return room.model_copy()
        return None

    def create(self, *, name: str, agent_ids: list[str], kind: str = "group") -> Room:
        from agent.threads import get_thread_manager

        agent_ids = [a for a in dict.fromkeys(str(x).strip() for x in agent_ids) if a]
        if not agent_ids:
            raise ValueError("A room needs at least one agent")
        kind = "direct" if kind == "direct" or len(agent_ids) == 1 and kind != "group" else "group"
        title = (name or "").strip()[:60] or "Group chat"
        thread = get_thread_manager().create_thread(title=title, source="room")
        room = Room(id=f"room_{uuid.uuid4().hex[:10]}", name=title, kind=kind, agent_ids=agent_ids, thread_id=thread.thread_id)
        with self._lock:
            self._rooms[room.id] = room
            self._save()
        return room.model_copy()

    def update(self, room_id: str, *, name: Optional[str] = None, agent_ids: Optional[list[str]] = None) -> Room:
        with self._lock:
            room = self._rooms.get(room_id)
            if room is None:
                raise KeyError(room_id)
            updates: dict[str, Any] = {"updated_at": time.time()}
            if name is not None and name.strip():
                updates["name"] = name.strip()[:60]
            if agent_ids is not None:
                ids = [a for a in dict.fromkeys(str(x).strip() for x in agent_ids) if a]
                if not ids:
                    raise ValueError("A room needs at least one agent")
                updates["agent_ids"] = ids
            room = room.model_copy(update=updates)
            self._rooms[room_id] = room
            self._save()
            return room.model_copy()

    def touch(self, room_id: str, preview: str) -> None:
        with self._lock:
            room = self._rooms.get(room_id)
            if room is None:
                return
            room.last_message_at = time.time()
            room.last_preview = re.sub(r"\s+", " ", preview or "").strip()[:140]
            self._save()

    def delete(self, room_id: str) -> bool:
        from agent.threads import get_thread_manager

        with self._lock:
            room = self._rooms.pop(room_id, None)
            if room is None:
                return False
            self._save()
        try:
            get_thread_manager().delete_thread(room.thread_id)
        except Exception:
            pass
        return True


_STORE: Optional[RoomStore] = None
_STORE_LOCK = threading.Lock()


def get_room_store() -> RoomStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = RoomStore()
        return _STORE


_MENTION = re.compile(r"(?<![\w@])@([A-Za-z][\w-]{0,39})")


def mentioned_agents(message: str, candidates: list[Any]) -> list[Any]:
    names = {m.group(1).casefold() for m in _MENTION.finditer(message or "")}
    if not names:
        return []
    if names & {"all", "everyone", "team"}:
        return list(candidates)
    return [agent for agent in candidates if agent.name.casefold() in names or agent.id.casefold() in names]
