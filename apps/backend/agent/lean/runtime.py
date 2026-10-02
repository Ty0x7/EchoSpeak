"""Entry point for lean turns: direct chats, group rooms, and delegation."""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional

from loguru import logger

from agent.lean import settings, summaries
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
# Handoffs one user message may trigger in total, across all agents.
MAX_HANDOFFS_PER_MESSAGE = 6
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
        self._user_message = ""
        self._handoffs = 0
        # Parallel agents share these.
        self._emit_lock = threading.Lock()
        self._state_lock = threading.Lock()

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
        with self._emit_lock:
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

        self._user_message = message
        history = self._history(exclude_execution=execution.id)
        responders = self._route(message, default_persona)
        success = True
        error = ""
        try:
            turn_history = history
            prompt_text = self._format_user(message)
            if self._is_discussion():
                success, error = self._run_discussion(responders, prompt_text, history)
                responders = []
            elif self._should_fan_out(message, responders):
                success, error = self._run_fan_out(responders, prompt_text, history)
                responders = []
            for index, persona in enumerate(responders):
                if self.cancel.is_set():
                    break
                before = len(self.results)
                result = self._run_agent(persona, prompt_text, history=turn_history, depth=0)
                if not result.empty:
                    self.results.append(result)
                success = success and result.success
                error = error or result.error
                if index + 1 < len(responders):
                    # The next responder sees the user message, then everything said
                    # since (including teammates this agent handed work to).
                    if index == 0:
                        turn_history = turn_history + [{"role": "user", "content": prompt_text}]
                    spoken = "\n\n".join(
                        f"[{self._name_of(r.agent_id)}]: {r.text}" for r in self.results[before:] if r.text
                    )
                    turn_history = turn_history + [{"role": "assistant", "content": spoken}]
                    nxt = responders[index + 1]
                    prompt_text = (
                        f"[System]: {nxt.name}, it's your turn. Reply to the user's last message from your side. "
                        "If you agree with what was said, say so in one line. If something is wrong or missing, "
                        "correct it or add it. Don't repeat what's already been said."
                    )
        except Exception as exc:
            logger.exception("Lean session failed")
            success = False
            error = f"{type(exc).__name__}: {exc}"

        final_text = next((r.text for r in reversed(self.results) if r.text), "")
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
        if not cancelled and final_text:
            summaries.update_in_background(self.session_id, lambda: self._side_client(default_persona))
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
        last = self._last_speaker(members)
        picked = self._model_route(message, members, last=last)
        # Router unsure: keep talking to whoever answered last.
        return picked or [last or members[0]]

    def _last_speaker(self, members: list[AgentPersona]) -> Optional[AgentPersona]:
        from agent.state import get_state_store

        ids = {m.id for m in members}
        try:
            timeline = get_state_store().session_timeline(self.session_id, limit=10)
        except Exception:
            return None
        for turn in reversed(timeline.get("turns") or []):
            if str(turn.get("execution_id") or "") == self.execution_id:
                continue
            for msg in reversed(turn.get("messages") or []):
                agent_id = str(msg.get("agent_id") or "")
                if msg.get("role") != "user" and agent_id in ids and str(msg.get("text") or "").strip():
                    return next(m for m in members if m.id == agent_id)
        return None

    def _model_route(self, message: str, members: list[AgentPersona], *, last: Optional[AgentPersona] = None) -> list[AgentPersona]:
        """Ask the model which agent(s) should answer. Never gates tools."""
        roster = "\n".join(f"- {m.name}: {m.description or m.title}" for m in members)
        recent = self._history(exclude_execution=self.execution_id)[-4:]
        context = "\n".join(str(item.get("content") or "")[:300] for item in recent)
        prompt = (
            "You route messages in a group chat between a user and AI agents.\n"
            f"Agents:\n{roster}\n\n"
            + (f"Recent conversation:\n{context}\n\n" if context else "")
            + (f"The last agent to reply was {last.name}. If the new message continues that exchange "
               f"(a follow-up, a yes/no, \"and…?\"), pick {last.name}.\n\n" if last else "")
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

    def _run_agent(
        self,
        persona: AgentPersona,
        message: str,
        *,
        history: list[dict[str, Any]],
        depth: int,
        delegated_by: Optional[AgentPersona] = None,
    ) -> TurnResult:
        turn = self._build_turn(persona, message, history=history, depth=depth, delegated_by=delegated_by)
        result = turn.run(message)
        if not result.empty:
            self._persist(persona, result, depth, meta=turn.meta)
        return result

    # ── discussion mode ─────────────────────────────────────────────────
    def _is_discussion(self) -> bool:
        return bool(self.room and self.room.kind == "group" and self.room.mode == "discussion" and len(self._members()) >= 2)

    def _run_discussion(self, first: list[AgentPersona], prompt_text: str, history: list[dict[str, Any]]) -> tuple[bool, str]:
        """Agents take turns on the user's message until one says DONE or the cap is reached.

        Speaking order starts with whoever was chosen to answer, then goes round
        the room. Each agent sees the whole discussion so far. The room's lead
        then writes a short conclusion.
        """
        members = self._members()
        order = list({p.id: p for p in [*first, *members]}.values())
        cap = max(2, int(self.room.max_messages or 6))
        spoken: list[tuple[AgentPersona, TurnResult]] = []
        success, error = True, ""
        for index in range(cap):
            if self.cancel.is_set():
                break
            persona = order[index % len(order)]
            done_rule = "If the group has reached a good answer, end your message with the word DONE."
            if index == 0:
                brief = (
                    f"{prompt_text}\n\n[System]: Discussion mode. {persona.name}, open the discussion with your view. "
                    f"Keep it under 120 words. {done_rule}"
                )
                turn_history = history
            else:
                brief = (
                    f"[System]: Discussion mode, message {index + 1} of up to {cap}. {persona.name}, read the discussion "
                    "above and add your view: build on it, question it, or correct it. Don't repeat what was said. "
                    f"Keep it under 120 words. {done_rule}"
                )
                transcript = "\n\n".join(f"[{p.name}]: {r.text}" for p, r in spoken if r.text)
                turn_history = history + [{"role": "user", "content": prompt_text}, {"role": "assistant", "content": transcript}]
            turn = self._build_turn(
                persona, brief, history=turn_history, depth=0, allow_handoff=False,
                meta={"discussion": index + 1}, end_marker="DONE",
            )
            result = turn.run(brief)
            if not result.empty:
                self._persist(persona, result, 0, meta=turn.meta)
                self.results.append(result)
            success = success and result.success
            error = error or result.error
            if not result.success:
                break
            spoken.append((persona, result))
            # Stop on DONE, but always let at least two agents speak.
            if turn.marker_found and len(spoken) >= 2:
                break
        if len(spoken) >= 2 and settings.group_merge() and not self.cancel.is_set():
            lead = members[0]
            transcript = "\n\n".join(f"[{p.name}]: {r.text}" for p, r in spoken if r.text)
            conclude_history = history + [{"role": "user", "content": prompt_text}, {"role": "assistant", "content": transcript}]
            brief = (
                f"[System]: {lead.name}, the discussion is over. Write a short conclusion for the user: the answer the "
                "group reached and any disagreement still open. Under 120 words."
            )
            turn = self._build_turn(lead, brief, history=conclude_history, depth=0, allow_handoff=False, meta={"role": "merge"})
            result = turn.run(brief)
            if not result.empty:
                self._persist(lead, result, 0, meta=turn.meta)
                self.results.append(result)
            success = success and result.success
            error = error or result.error
        return success, error

    # ── parallel fan-out ────────────────────────────────────────────────
    def _should_fan_out(self, message: str, responders: list[AgentPersona]) -> bool:
        """Several agents were asked by name (or @all), and they share one model.

        A local server answering two personas on different models would have to
        load both at once, so mixed-model groups keep taking turns.
        """
        if len(responders) < 2 or not settings.group_fan_out():
            return False
        if len(mentioned_agents(message, self._members())) < 2:
            return False
        endpoints = {(e.base_url, e.model) for e in (self._endpoint_for(p) for p in responders)}
        return len(endpoints) == 1

    def _run_fan_out(self, responders: list[AgentPersona], prompt_text: str, history: list[dict[str, Any]]) -> tuple[bool, str]:
        # Small models otherwise tend to answer for every agent named in the message.
        briefs = {
            persona.id: (
                f"{prompt_text}\n\n[System]: {persona.name}, answer for yourself only. "
                + ", ".join(p.name for p in responders if p.id != persona.id)
                + " will answer separately, at the same time."
            )
            for persona in responders
        }
        turns = [
            (persona, self._build_turn(persona, briefs[persona.id], history=history, depth=0, allow_handoff=False, meta={"parallel": True}))
            for persona in responders
        ]
        # Open every message up front, in the order the user named them.
        for _, turn in turns:
            turn.announce()
        with ThreadPoolExecutor(max_workers=len(turns), thread_name_prefix="lean-fanout") as pool:
            futures = [pool.submit(turn.run, briefs[persona.id], announce=False) for persona, turn in turns]
            results = [future.result() for future in futures]
        success, error = True, ""
        answers: list[tuple[AgentPersona, TurnResult]] = []
        # Saved in the same order they were shown, whichever finished first.
        for (persona, turn), result in zip(turns, results):
            if not result.empty:
                self._persist(persona, result, 0, meta=turn.meta)
                self.results.append(result)
            success = success and result.success
            error = error or result.error
            if result.success and result.text:
                answers.append((persona, result))
        if len(answers) >= 2 and settings.group_merge() and not self.cancel.is_set():
            merged = self._merge(answers, prompt_text, history)
            success = success and merged.success
            error = error or merged.error
        return success, error

    def _merge(self, answers: list[tuple[AgentPersona, TurnResult]], prompt_text: str, history: list[dict[str, Any]]) -> TurnResult:
        """The room's lead turns independent answers into one short reply."""
        members = self._members()
        lead = members[0] if members else answers[0][0]
        merge_history = history + [
            {"role": "user", "content": prompt_text},
            {"role": "assistant", "content": "\n\n".join(f"[{p.name}]: {r.text}" for p, r in answers)},
        ]
        names = ", ".join(p.name for p, _ in answers)
        brief = (
            f"[System]: {lead.name}, {names} each answered the user's message on their own (above). "
            "Write a short merged reply: where they agree, where they differ and which is more likely right "
            "(check with a tool only if it's quick), and the answer you recommend. Don't restate everything."
        )
        turn = self._build_turn(lead, brief, history=merge_history, depth=0, allow_handoff=False, meta={"role": "merge"})
        result = turn.run(brief)
        if not result.empty:
            self._persist(lead, result, 0, meta=turn.meta)
            self.results.append(result)
        return result

    def _build_turn(
        self,
        persona: AgentPersona,
        message: str,
        *,
        history: list[dict[str, Any]],
        depth: int,
        delegated_by: Optional[AgentPersona] = None,
        allow_handoff: bool = True,
        meta: Optional[dict[str, Any]] = None,
        end_marker: str = "",
    ) -> LeanTurn:
        meta = dict(meta or {})
        if delegated_by is not None:
            meta["delegated_by"] = {"id": delegated_by.id, "name": delegated_by.name}
        can_hand_off = allow_handoff and depth < MAX_DELEGATION_DEPTH
        teammates = self._members() if self.room else [p for p in self.personas.list() if p.id != persona.id]
        terminal = Terminal(self.project_root)
        toolbox = Toolbox(
            toolsets=persona.toolsets or None,
            extra_tools=self._native_tools(persona, depth, delegated_by=delegated_by, allow_handoff=can_hand_off)
            + coding_tools()
            + terminal.tools(),
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
            teammates=teammates if can_hand_off else [],
            room_name=self.room.name if self.room and self.room.kind == "group" else "",
            memories=memories,
            chat_summary=summaries.summary_text(self.session_id) if depth == 0 else "",
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
            on_seal=lambda part: self._record(persona, part, depth, meta),
            meta=meta,
            end_marker=end_marker,
        )
        return turn

    def _record(self, persona: AgentPersona, part: TurnResult, depth: int, meta: dict[str, Any]) -> None:
        """A message closed before a handoff: save it now so it sorts before the teammate's."""
        self.results.append(part)
        self._persist(persona, part, depth, meta=meta)

    def _persist(self, persona: AgentPersona, result: TurnResult, depth: int, *, meta: Optional[dict[str, Any]] = None) -> None:
        from agent.state import get_state_store

        meta = meta or {}
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
                    "delegated_by": meta.get("delegated_by") or None,
                    "role": str(meta.get("role") or ""),
                    "stop_reason": result.stop_reason,
                },
            )
        except Exception:
            logger.exception("Lean assistant message persistence failed")

    # ── native tools ────────────────────────────────────────────────────
    def _native_tools(
        self,
        persona: AgentPersona,
        depth: int,
        *,
        delegated_by: Optional[AgentPersona] = None,
        allow_handoff: bool = True,
    ) -> list[NativeTool]:
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

        if allow_handoff and depth < MAX_DELEGATION_DEPTH:
            candidates = self._members() if self.room else self.personas.list()
            # Never offer a hand-back to whoever handed this work over.
            excluded = {persona.id, delegated_by.id if delegated_by else ""}
            candidates = [c for c in candidates if c.id not in excluded]
            if candidates:
                def check(args: dict[str, Any]) -> str:
                    target = self.personas.find_by_name(str(args.get("agent") or ""))
                    if delegated_by is not None and target is not None and target.id == delegated_by.id:
                        return (f"Error: {target.name} handed this task to you. Finish it yourself and reply; "
                                "your answer goes back to them automatically.")
                    if target is None or target.id not in {c.id for c in candidates}:
                        return "Error: unknown agent. Choose one of: " + ", ".join(c.name for c in candidates)
                    if not str(args.get("task") or "").strip():
                        return "Error: describe the task in 'task'."
                    with self._state_lock:
                        if self._handoffs >= MAX_HANDOFFS_PER_MESSAGE:
                            return (f"Error: this request has already used {MAX_HANDOFFS_PER_MESSAGE} handoffs. "
                                    "Finish the work yourself with what you have.")
                    return f"Handed to {target.name}. Their reply is below."

                def delegate(args: dict[str, Any]) -> str:
                    problem = check(args)
                    if problem.startswith("Error"):
                        return problem
                    target = self.personas.find_by_name(str(args.get("agent") or ""))
                    task = str(args.get("task") or "").strip()
                    with self._state_lock:
                        self._handoffs += 1
                    self.emit({"type": "delegation", "from": persona.id, "to": target.id, "task": task[:400]})
                    brief = f"{persona.name} handed you this task:\n{task}"
                    if self._user_message:
                        brief += f"\n\nFor context, the user's message was:\n{self._user_message[:800]}"
                    result = self._run_agent(target, brief, history=[], depth=depth + 1, delegated_by=persona)
                    if not result.empty:
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
                    handoff=check,
                ))
        return tools

    # ── helpers ─────────────────────────────────────────────────────────
    def _name_of(self, agent_id: str) -> str:
        persona = self.personas.get(agent_id)
        return persona.name if persona else agent_id

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

    def _side_client(self, persona: AgentPersona) -> ChatClient:
        """A separate, thinking-off client for background work (summaries)."""
        endpoint = self._endpoint_for(persona)
        return ChatClient(endpoint, reasoning_effort=reasoning_effort_for(endpoint, False, self.reasoning_effort))

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
        turns = timeline.get("turns") or []
        # Recent turns verbatim; older ones only if the chat summary doesn't cover them yet.
        covered = set(summaries.load(self.session_id).get("covered") or [])
        recent_start = max(0, len(turns) - summaries.RECENT_TURNS)
        for index, turn in enumerate(turns):
            if str(turn.get("execution_id") or "") == exclude_execution:
                continue
            if index < recent_start and str(turn.get("execution_id") or "") in covered:
                continue
            for msg in turn.get("messages") or []:
                text = str(msg.get("text") or "").strip()
                if not text:
                    continue
                if msg.get("role") == "user":
                    rows.append({"role": "user", "content": f"[User]: {text}" if group else text})
                else:
                    # Current name, so renamed agents read correctly in old history.
                    name = self._name_of(str(msg.get("agent_id"))) if msg.get("agent_id") else str(msg.get("agent_name") or "")
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
