"""Entry point for lean turns: direct chats, group rooms, and delegation."""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from typing import Any, Callable, Optional

from loguru import logger

from agent.lean import settings
from agent.lean.loop import LeanTurn, TurnResult
from agent.lean.personas import AgentPersona, get_persona_store
from agent.lean.prompt import build_system_prompt
from agent.lean.provider import ChatClient, reasoning_effort_for, resolve_endpoint
from agent.lean.rooms import Room, get_room_store, mentioned_agents
from agent.lean.coding import coding_tools, project_overview
from agent.lean.terminal import Terminal
from agent.lean.toolbox import NativeTool, Toolbox, project_root_for_session

Emit = Callable[[dict[str, Any]], None]

MAX_DELEGATION_DEPTH = 2
# Sources with a live UI that can show an approval card.
INTERACTIVE_SOURCES = {"web", "desktop", "voice", "chat", "api"}
_HISTORY_MESSAGES = 30


_LOCKS_GUARD = threading.Lock()
_SESSION_LOCKS: dict[str, threading.RLock] = {}


def _session_lock(session_id: str) -> threading.RLock:
    """One turn at a time per chat; different chats run in parallel."""
    key = str(session_id or "default")
    with _LOCKS_GUARD:
        return _SESSION_LOCKS.setdefault(key, threading.RLock())


class LeanSession:
    """Runs one user message through one or more agents in a Session."""

    def __init__(
        self,
        *,
        agent: Any,
        session_id: str,
        request_id: str,
        emit: Emit,
        cancel: threading.Event,
        source: str = "web",
        room: Optional[Room] = None,
        thinking_enabled: bool = True,
        reasoning_effort: str = "medium",
    ) -> None:
        self.thinking_enabled = thinking_enabled
        self.reasoning_effort = reasoning_effort
        self.agent = agent
        self.session_id = session_id
        self.request_id = request_id or str(uuid.uuid4())
        self._emit = emit
        self.cancel = cancel
        self.source = source
        self.room = room
        self.personas = get_persona_store()
        self.project_root = project_root_for_session(session_id)
        self.execution_id = ""
        self.results: list[TurnResult] = []
        self._clients: dict[str, ChatClient] = {}
        self._soul_text: Optional[str] = None

    # ── public ──────────────────────────────────────────────────────────
    def run(self, message: str, *, persona_id: str = "") -> dict[str, Any]:
        with _session_lock(self.session_id):
            try:
                return self._run_locked(message, persona_id=persona_id)
            finally:
                for client in self._clients.values():
                    client.close()

    # ── internals ───────────────────────────────────────────────────────
    def emit(self, event: dict[str, Any]) -> None:
        event.setdefault("request_id", self.request_id)
        event.setdefault("at", time.time())
        try:
            self._emit(event)
        except Exception:
            logger.exception("Lean session emit failed")

    def _members(self) -> list[AgentPersona]:
        if self.room is None:
            return []
        members = [self.personas.get(agent_id) for agent_id in self.room.agent_ids]
        return [m for m in members if m is not None]

    def _run_locked(self, message: str, *, persona_id: str) -> dict[str, Any]:
        from agent.state import get_state_store

        store = get_state_store()
        state = store.get_thread_state(self.session_id)
        default_persona = self.personas.get(persona_id) if persona_id else None
        if default_persona is None:
            members = self._members()
            default_persona = members[0] if members else self.personas.default()
        endpoint = self._endpoint_for(default_persona)
        execution = store.create_execution(
            request_id=self.request_id,
            kind="query",
            thread_id=self.session_id,
            source=self.source,
            status="running",
            query=message[:4000],
            record_user_message=True,
            workspace_id=str(state.workspace_id or ""),
            active_project_id=str(state.active_project_id or ""),
            runtime_provider=endpoint.provider,
            model_id=endpoint.model,
            intent="lean",
            mode="agent",
            phase="running",
            metadata={"runtime": "lean_v1", "room_id": self.room.id if self.room else ""},
        )
        self.execution_id = execution.id
        self.emit({"type": "run_start", "execution_id": execution.id, "room_id": self.room.id if self.room else ""})

        history = self._history(exclude_execution=execution.id)
        responders = self._route(message, default_persona)
        success = True
        error = ""
        try:
            turn_history = history
            prompt_text = self._format_user(message)
            for index, persona in enumerate(responders):
                if self.cancel.is_set():
                    break
                result = self._run_agent(persona, prompt_text, history=turn_history, depth=0)
                self.results.append(result)
                success = success and result.success
                error = error or result.error
                if index + 1 < len(responders):
                    # The next responder sees the user message, then what this agent said.
                    if index == 0:
                        turn_history = turn_history + [{"role": "user", "content": prompt_text}]
                    turn_history = turn_history + [{"role": "assistant", "content": f"[{persona.name}]: {result.text}"}]
                    nxt = responders[index + 1]
                    prompt_text = (
                        f"[System]: {nxt.name}, it's your turn. Reply to the user's last message from your "
                        "side. Don't repeat what the others already said."
                    )
        except Exception as exc:
            logger.exception("Lean session failed")
            success = False
            error = f"{type(exc).__name__}: {exc}"

        final_text = self.results[-1].text if self.results else ""
        cancelled = self.cancel.is_set()
        store.update_execution(
            execution.id,
            status="canceled" if cancelled else ("completed" if success else "failed"),
            success=bool(success and not cancelled),
            phase="cancelled" if cancelled else "complete",
            response_preview=final_text[:500],
            error=error[:1000],
        )
        store.update_thread_state(
            self.session_id,
            execution_status="cancelled" if cancelled else ("complete" if success else "failed"),
            current_execution_id="",
            active_turn_id="",
        )
        if self.room is not None and final_text:
            get_room_store().touch(self.room.id, final_text)
        self._remember_async(message, final_text)
        return {
            "execution_id": execution.id,
            "success": bool(success and not cancelled),
            "response": final_text,
            "messages": [
                {"message_id": r.message_id, "agent_id": r.agent_id, "text": r.text, "success": r.success}
                for r in self.results
            ],
            "error": error,
        }

    def _route(self, message: str, default_persona: AgentPersona) -> list[AgentPersona]:
        members = self._members()
        if not members or len(members) == 1:
            return [members[0] if members else default_persona]
        named = mentioned_agents(message, members)
        if named:
            return named[:4]
        picked = self._model_route(message, members)
        return picked or [members[0]]

    def _model_route(self, message: str, members: list[AgentPersona]) -> list[AgentPersona]:
        """Ask the model which agent(s) should answer. Never gates tools."""
        roster = "\n".join(f"- {m.name}: {m.description or m.title}" for m in members)
        recent = self._history(exclude_execution=self.execution_id)[-4:]
        context = "\n".join(str(item.get("content") or "")[:300] for item in recent)
        prompt = (
            "You route messages in a group chat between a user and AI agents.\n"
            f"Agents:\n{roster}\n\n"
            + (f"Recent conversation:\n{context}\n\n" if context else "")
            + f"New user message:\n{message[:1500]}\n\n"
            "Who should reply? Usually pick exactly one agent. Pick two only if both clearly add something different. "
            'Answer with JSON only, like {"agents": ["Name"]}.'
        )
        self.emit({"type": "routing", "agents": [m.name for m in members]})
        try:
            client = self._client_for(members[0], routing=True)
            turn = client.stream_turn(
                [{"role": "user", "content": prompt}],
                cancel=self.cancel,
                temperature=0.1,
                max_tokens=1200,
            )
            text = turn.content or ""
            match = re.search(r"\{.*\}", text, re.S)
            names: list[str] = []
            if match:
                try:
                    names = [str(n) for n in json.loads(match.group(0)).get("agents") or []]
                except Exception:
                    names = []
            if not names:
                names = [m.name for m in members if re.search(rf"\b{re.escape(m.name)}\b", text, re.I)]
            chosen = [m for m in members if m.name.casefold() in {n.strip().lstrip("@").casefold() for n in names}]
            return chosen[:2]
        except Exception as exc:
            logger.warning("Group routing failed, using lead agent: {}", exc)
            return []

    def _run_agent(self, persona: AgentPersona, message: str, *, history: list[dict[str, Any]], depth: int) -> TurnResult:
        from agent.state import get_state_store

        teammates = self._members() if self.room else [p for p in self.personas.list() if p.id != persona.id]
        terminal = Terminal(self.project_root)
        toolbox = Toolbox(
            toolsets=persona.toolsets or None,
            extra_tools=self._native_tools(persona, depth) + coding_tools() + terminal.tools(),
            session_id=self.session_id,
            project_root=self.project_root,
        )
        memories = self._recall(message)
        prompt = build_system_prompt(
            persona=persona,
            soul_text=self._soul() if persona.id == "echo" else "",
            project_root=self.project_root,
            notes=toolbox.notes,
            terminal_note=terminal.describe() if "terminal" in toolbox.names else "",
            project_overview=project_overview(self.project_root) if self.project_root else "",
            teammates=teammates if depth < MAX_DELEGATION_DEPTH else [],
            room_name=self.room.name if self.room and self.room.kind == "group" else "",
            memories=memories,
        )
        turn = LeanTurn(
            client=self._client_for(persona),
            persona=persona,
            system_prompt=prompt,
            history=history,
            toolbox=toolbox,
            session_id=self.session_id,
            request_id=self.request_id,
            execution_id=self.execution_id,
            emit=self.emit,
            cancel=self.cancel,
            temperature=self._temperature(),
            interactive=self.source in INTERACTIVE_SOURCES,
        )
        result = turn.run(message)
        try:
            get_state_store().add_item(
                turn_id=self.execution_id,
                item_type="assistant_message",
                status="complete" if result.success else "failed",
                session_id=self.session_id,
                payload={
                    "text": result.text,
                    "agent_id": persona.id,
                    "agent_name": persona.name,
                    "message_id": result.message_id,
                    "timeline": _bounded_timeline(result.timeline),
                    "backend_success": result.success,
                    "error": result.error,
                    "delegation_depth": depth,
                },
            )
        except Exception:
            logger.exception("Lean assistant message persistence failed")
        return result

    # ── native tools ────────────────────────────────────────────────────
    def _native_tools(self, persona: AgentPersona, depth: int) -> list[NativeTool]:
        memory = getattr(self.agent, "memory", None)
        tools: list[NativeTool] = []
        if memory is not None:
            def memory_save(args: dict[str, Any]) -> str:
                fact = str(args.get("fact") or args.get("text") or "").strip()
                if not fact:
                    return "Error: give the fact to remember in 'fact'."
                memory_id = memory.add_memory_item(
                    fact,
                    memory_type=str(args.get("type") or "note"),
                    pinned=bool(args.get("pinned", False)),
                    thread_id=self.session_id,
                    source="agent",
                    scope="account",
                    source_execution_id=self.execution_id,
                )
                if not memory_id:
                    return "Failed: that could not be saved (empty, duplicate, or looks like a secret)."
                self.emit({"type": "memory_saved", "memory_count": int(memory.count_items() or 0)})
                return f"Saved to memory: {fact}"

            def memory_search(args: dict[str, Any]) -> str:
                query = str(args.get("query") or "").strip()
                rows = self._recall(query, limit=10)
                try:
                    docs = memory.retrieve_relevant(query, k=5, thread_id=self.session_id)
                    rows += [{"content": getattr(doc, "page_content", "")} for doc in docs]
                except Exception:
                    pass
                lines = list(dict.fromkeys(str(r.get("content") or "").strip() for r in rows if str(r.get("content") or "").strip()))
                return "\n".join(f"- {line}" for line in lines[:12]) or "No matching memories."

            tools.append(NativeTool(
                name="memory_save",
                description="Save a lasting fact about the user or their preferences, projects, or people so you remember it in future chats.",
                parameters={"type": "object", "properties": {
                    "fact": {"type": "string", "description": "The fact, written as a full sentence."},
                    "pinned": {"type": "boolean", "description": "True for core facts that should always be recalled."},
                }, "required": ["fact"]},
                func=memory_save,
            ))
            tools.append(NativeTool(
                name="memory_search",
                description="Search what you remember about the user and past conversations.",
                parameters={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                func=memory_search,
                parallel_safe=True,
            ))

        if depth < MAX_DELEGATION_DEPTH:
            candidates = self._members() if self.room else self.personas.list()
            candidates = [c for c in candidates if c.id != persona.id]
            if candidates:
                def delegate(args: dict[str, Any]) -> str:
                    target = self.personas.find_by_name(str(args.get("agent") or ""))
                    if target is None or target.id == persona.id or target.id not in {c.id for c in candidates}:
                        return "Error: unknown agent. Choose one of: " + ", ".join(c.name for c in candidates)
                    task = str(args.get("task") or "").strip()
                    if not task:
                        return "Error: describe the task in 'task'."
                    self.emit({"type": "delegation", "from": persona.id, "to": target.id, "task": task[:400]})
                    brief = f"{persona.name} handed you this task:\n{task}"
                    result = self._run_agent(target, brief, history=[], depth=depth + 1)
                    self.results.append(result)
                    return f"{target.name} replied:\n{result.text}"

                tools.append(NativeTool(
                    name="delegate_to_agent",
                    description="Hand a task to a teammate who is better suited and get their answer back. Teammates: "
                    + "; ".join(f"{c.name} ({c.title or c.description[:60]})" for c in candidates),
                    parameters={"type": "object", "properties": {
                        "agent": {"type": "string", "enum": [c.name for c in candidates]},
                        "task": {"type": "string", "description": "Everything they need to do the task."},
                    }, "required": ["agent", "task"]},
                    func=delegate,
                ))
        return tools

    # ── helpers ─────────────────────────────────────────────────────────
    def _endpoint_for(self, persona: AgentPersona):
        provider = persona.model.provider or str(getattr(getattr(self.agent, "llm_provider", None), "value", "") or "lmstudio")
        model_id = persona.model.model_id if persona.model.provider or persona.model.model_id else ""
        if not model_id:
            runtime = getattr(self.agent, "model_runtime", None)
            model_id = str(getattr(runtime, "model_id", "") or "")
        return resolve_endpoint(provider, model_id)

    def _client_for(self, persona: AgentPersona, *, routing: bool = False) -> ChatClient:
        endpoint = self._endpoint_for(persona)
        # Routing is a one-word decision: never spend thinking tokens on it.
        effort = reasoning_effort_for(endpoint, False if routing else self.thinking_enabled, self.reasoning_effort)
        key = f"{endpoint.base_url}|{endpoint.model}|{effort}"
        if key not in self._clients:
            self._clients[key] = ChatClient(endpoint, reasoning_effort=effort)
        return self._clients[key]

    def _temperature(self) -> Optional[float]:
        try:
            from config import config

            return float(config.local.temperature)
        except Exception:
            return None

    def _soul(self) -> str:
        if self._soul_text is None:
            try:
                self._soul_text = str(self.agent._load_soul() or "")
            except Exception:
                self._soul_text = ""
        return self._soul_text

    def _recall(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        memory = getattr(self.agent, "memory", None)
        if memory is None or not query:
            return []
        try:
            from agent.state import get_state_store

            project_id = str(get_state_store().get_thread_state(self.session_id).active_project_id or "")
            return list(memory.runtime_memory_projection(
                query, session_id=self.session_id, project_id=project_id,
                project_path=self.project_root or None, limit=limit,
            ) or [])
        except Exception:
            logger.debug("Lean memory recall failed", exc_info=True)
            return []

    def _format_user(self, message: str) -> str:
        return message if not (self.room and self.room.kind == "group") else f"[User]: {message}"

    def _history(self, *, exclude_execution: str = "") -> list[dict[str, Any]]:
        from agent.state import get_state_store

        try:
            timeline = get_state_store().session_timeline(self.session_id, limit=_HISTORY_MESSAGES)
        except Exception:
            return []
        group = bool(self.room and self.room.kind == "group")
        rows: list[dict[str, Any]] = []
        for turn in timeline.get("turns") or []:
            if str(turn.get("execution_id") or "") == exclude_execution:
                continue
            for msg in turn.get("messages") or []:
                text = str(msg.get("text") or "").strip()
                if not text:
                    continue
                if msg.get("role") == "user":
                    rows.append({"role": "user", "content": f"[User]: {text}" if group else text})
                else:
                    name = str(msg.get("agent_name") or "")
                    rows.append({"role": "assistant", "content": f"[{name}]: {text}" if group and name else text})
        # Merge consecutive same-role messages: chat templates expect alternation.
        merged: list[dict[str, Any]] = []
        for row in rows[-_HISTORY_MESSAGES:]:
            if merged and merged[-1]["role"] == row["role"]:
                merged[-1]["content"] += "\n\n" + row["content"]
            else:
                merged.append(dict(row))
        while merged and merged[0]["role"] != "user":
            merged.pop(0)
        return merged

    def _remember_async(self, message: str, answer: str) -> None:
        memory = getattr(self.agent, "memory", None)
        if memory is None or not answer or self.source in {"routine", "heartbeat", "proactive"}:
            return

        def work() -> None:
            try:
                memory.add_conversation(message, answer, thread_id=self.session_id)
            except Exception:
                logger.debug("Lean conversation memory write failed", exc_info=True)

        threading.Thread(target=work, name="lean-memory", daemon=True).start()


def _bounded_timeline(timeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bounded: list[dict[str, Any]] = []
    for item in timeline[:200]:
        row = dict(item)
        for key, limit in (("text", 12000), ("output", 1600)):
            if isinstance(row.get(key), str) and len(row[key]) > limit:
                row[key] = row[key][:limit] + "…"
        bounded.append(row)
    return bounded


def run_lean_query(
    agent: Any,
    *,
    message: str,
    session_id: str,
    request_id: str,
    emit: Emit,
    cancel: threading.Event,
    source: str = "web",
    persona_id: str = "",
    thinking_enabled: bool = True,
    reasoning_effort: str = "medium",
) -> dict[str, Any]:
    room = get_room_store().by_thread(session_id)
    session = LeanSession(
        agent=agent,
        session_id=session_id,
        request_id=request_id,
        emit=emit,
        cancel=cancel,
        source=source,
        room=room,
        thinking_enabled=thinking_enabled,
        reasoning_effort=reasoning_effort,
    )
    return session.run(message, persona_id=persona_id)


def run_lean_text(agent: Any, *, message: str, session_id: str, source: str, request_id: str = "") -> tuple[str, bool]:
    """Non-streaming entry for channels (Discord, Telegram, routines, /query)."""
    result = run_lean_query(
        agent,
        message=message,
        session_id=session_id,
        request_id=request_id or str(uuid.uuid4()),
        emit=lambda _event: None,
        cancel=threading.Event(),
        source=source,
    )
    return str(result.get("response") or ""), bool(result.get("success"))
