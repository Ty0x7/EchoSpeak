"""Agent roster: each agent is a persona with its own soul, model, and toolsets.

This is the Grok Bot idea on top of EchoSpeak: agents behave like contacts.
Echo is the built-in default and keeps using SOUL.md as its soul.
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

_STORE_PATH = Path(DATA_DIR) / "lean" / "agents.json"


class AgentModel(BaseModel):
    provider: str = ""
    model_id: str = ""


class AgentPersona(BaseModel):
    id: str
    name: str
    title: str = ""
    description: str = Field(default="", description="What this agent is for; used to route group messages and delegation.")
    soul: str = Field(default="", description="Personality and instructions. Empty on Echo means SOUL.md.")
    avatar: str = Field(default="", description="One or two characters or an emoji.")
    model: AgentModel = Field(default_factory=AgentModel)
    toolsets: list[str] = Field(default_factory=list)
    builtin: bool = False
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)

    def initials(self) -> str:
        if self.avatar.strip():
            return self.avatar.strip()[:2]
        parts = [part for part in re.split(r"\s+", self.name.strip()) if part]
        return "".join(part[0] for part in parts[:2]).upper() or "?"


def _seed() -> list[AgentPersona]:
    return [
        AgentPersona(
            id="echo",
            name="Echo",
            title="Personal agent",
            description="General assistant. Handles conversation, planning, files, research, and anything that does not clearly belong to a specialist.",
            avatar="E",
            toolsets=["core", "research", "terminal", "vision", "memory", "skills"],
            builtin=True,
        ),
        AgentPersona(
            id="scout",
            name="Scout",
            title="Researcher",
            description="Deep web research, fact checking, comparing sources, news, sports, weather, and summarizing long pages or videos.",
            soul=(
                "You are Scout, a meticulous researcher on Echo's team. You search widely, read the "
                "actual sources, cross-check claims, and report findings with the source links. "
                "You say plainly when sources disagree or when something could not be confirmed."
            ),
            avatar="S",
            toolsets=["research", "memory"],
        ),
        AgentPersona(
            id="forge",
            name="Forge",
            title="Builder",
            description="Writes and edits code, builds projects, runs terminal commands, debugs errors, and works inside project folders.",
            soul=(
                "You are Forge, the builder on Echo's team. You read the existing code before changing it, "
                "make complete working edits (never placeholder stubs), run commands to verify, and "
                "report exactly what changed."
            ),
            avatar="F",
            toolsets=["core", "terminal", "research", "memory"],
        ),
    ]


class PersonaStore:
    def __init__(self, path: Path = _STORE_PATH) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._agents: dict[str, AgentPersona] = {}
        self._load()

    def _load(self) -> None:
        with self._lock:
            rows: list[dict[str, Any]] = []
            if self.path.exists():
                try:
                    rows = list(json.loads(self.path.read_text(encoding="utf-8")).get("agents") or [])
                except Exception:
                    backup = self.path.with_suffix(f".corrupt-{int(time.time())}.json")
                    try:
                        self.path.replace(backup)
                    except Exception:
                        pass
                    rows = []
            self._agents = {}
            for row in rows:
                try:
                    agent = AgentPersona(**row)
                    self._agents[agent.id] = agent
                except Exception:
                    continue
            if not self._agents:
                for agent in _seed():
                    self._agents[agent.id] = agent
                self._save()
            elif "echo" not in self._agents:
                self._agents["echo"] = _seed()[0]
                self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "agents": [agent.model_dump() for agent in self._agents.values()]}
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        temp.replace(self.path)

    def list(self) -> list[AgentPersona]:
        with self._lock:
            agents = list(self._agents.values())
        agents.sort(key=lambda item: (not item.builtin, item.created_at))
        return [agent.model_copy() for agent in agents]

    def get(self, agent_id: str) -> Optional[AgentPersona]:
        with self._lock:
            agent = self._agents.get(str(agent_id or "").strip())
            return agent.model_copy() if agent else None

    def default(self) -> AgentPersona:
        return self.get("echo") or _seed()[0]

    def find_by_name(self, name: str) -> Optional[AgentPersona]:
        key = str(name or "").strip().lstrip("@").casefold()
        for agent in self.list():
            if agent.name.casefold() == key or agent.id.casefold() == key:
                return agent
        return None

    def create(self, data: dict[str, Any]) -> AgentPersona:
        name = str(data.get("name") or "").strip()
        if not name:
            raise ValueError("An agent needs a name")
        if self.find_by_name(name):
            raise ValueError(f"An agent named {name} already exists")
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "agent"
        agent_id = slug if not self.get(slug) else f"{slug}-{uuid.uuid4().hex[:6]}"
        agent = AgentPersona(
            id=agent_id,
            name=name[:40],
            title=str(data.get("title") or "")[:60],
            description=str(data.get("description") or "")[:600],
            soul=str(data.get("soul") or "")[:12000],
            avatar=str(data.get("avatar") or "")[:4],
            model=AgentModel(**dict(data.get("model") or {})),
            toolsets=[str(item) for item in (data.get("toolsets") or ["core", "research", "memory"])],
        )
        with self._lock:
            self._agents[agent.id] = agent
            self._save()
        return agent.model_copy()

    def update(self, agent_id: str, data: dict[str, Any]) -> AgentPersona:
        with self._lock:
            agent = self._agents.get(agent_id)
            if agent is None:
                raise KeyError(agent_id)
            updates: dict[str, Any] = {}
            for key in ("name", "title", "description", "soul", "avatar"):
                if key in data and data[key] is not None:
                    updates[key] = str(data[key])
            if "name" in updates:
                clash = self.find_by_name(updates["name"])
                if clash and clash.id != agent_id:
                    raise ValueError(f"An agent named {updates['name']} already exists")
            if "toolsets" in data and data["toolsets"] is not None:
                updates["toolsets"] = [str(item) for item in data["toolsets"]]
            if "model" in data and data["model"] is not None:
                updates["model"] = AgentModel(**dict(data["model"]))
            updates["updated_at"] = time.time()
            agent = agent.model_copy(update=updates)
            self._agents[agent_id] = agent
            self._save()
            return agent.model_copy()

    def delete(self, agent_id: str) -> bool:
        with self._lock:
            agent = self._agents.get(agent_id)
            if agent is None or agent.builtin:
                return False
            del self._agents[agent_id]
            self._save()
            return True


_STORE: Optional[PersonaStore] = None
_STORE_LOCK = threading.Lock()


def get_persona_store() -> PersonaStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = PersonaStore()
        return _STORE
