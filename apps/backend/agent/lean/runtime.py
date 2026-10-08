"""Entry point for lean turns: direct chats, group rooms, and delegation."""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Optional

from loguru import logger

from agent.lean import git_context, memory_quality, recall, settings, soul, summaries
from agent.lean.job import ASSIGN_TASKS_DESCRIPTION, COMPLETE_TASK_DESCRIPTION, Job, Subtask, needs_action, parse_review
from agent.lean.loop import LeanTurn, TurnResult, _friendly_error
from agent.lean.personas import AgentPersona, get_persona_store
from agent.lean.prompt import build_system_prompt
from agent.lean.provider import ChatClient, reasoning_effort_for, resolve_endpoint
from agent.lean.rooms import Room, get_room_store, mentioned_agents
from agent.lean.coding import coding_tools, project_overview
from agent.lean.artifacts import artifact_tools
from agent.lean.rich_tools import rich_tools
from agent.lean.terminal import Terminal
from agent.lean.toolbox import DEFAULT_TOOLSETS, NativeTool, Toolbox, project_root_for_session
from agent.stopwords import keywords

Emit = Callable[[dict[str, Any]], None]

MAX_DELEGATION_DEPTH = 2
# Handoffs one user message may trigger in total, across all agents.
MAX_HANDOFFS_PER_MESSAGE = 6
# Work-together groups: opinions before the team must commit to tasks, and the
# default turn budget when a room still carries an old "messages" cap (≤ 12).
MAX_DISCUSS_TURNS = 3
DEFAULT_WORK_TURNS = 30
# Bookkeeping tools: calling them is not evidence that work got done.
_BOOKKEEPING_TOOLS = {"complete_task", "assign_tasks", "delegate_to_agent"}
# Sources with a live UI that can show an approval card.
INTERACTIVE_SOURCES = {"web", "desktop", "voice", "chat", "api"}

# Who is talking. Only the owner gets the full toolset, memory, and past chats;
# people reaching Echo through Discord, Telegram, Twitch, or Twitter as guests
# get look-up tools only, so nothing on this PC is exposed to them.
CALLER_ROLES = ("owner", "trusted", "public")
_CHANNEL_NAMES = {
    "discord_bot": "a Discord server channel", "discord_bot_dm": "a Discord direct message",
    "telegram": "Telegram", "twitch": "Twitch chat", "twitter": "Twitter/X", "twitter_autonomous": "Twitter/X",
}


def caller_note(source: str, role: str) -> str:
    """Tell the agent who is talking when the message comes from a channel, not the app."""
    channel = _CHANNEL_NAMES.get(str(source or "").lower())
    if not channel:
        return ""
    if role == "owner":
        return f"This message comes from your owner through {channel}. Keep replies short enough to read there."
    who = "someone your owner trusts" if role == "trusted" else "a member of the public, not your owner"
    return (
        f"This message comes from {who}, through {channel}. You can only look things up for them "
        "(web, weather, sports, time, math, EchoSpeak project updates). You have no access to your owner's files, terminal, memory, "
        "projects or other chats here, so never offer those or share anything private about your owner. "
        "If they ask for more, say that only your owner can do that from the EchoSpeak app."
    )


def request_text(message: str) -> str:
    """Channel bots send context, then 'User request: ...'. Memory lookups use just the request."""
    marker = "User request:"
    index = (message or "").rfind(marker)
    return message[index + len(marker):].strip() if index >= 0 else (message or "")


GUEST_TOOLS = {
    "public": ["get_system_time", "calculate", "web_search", "safe_web_fetch", "weather_live", "sports_live",
               "project_update_context", "stock_history", "product_search", "video_search", "image_search"],
    "trusted": ["get_system_time", "calculate", "web_search", "safe_web_fetch", "weather_live", "sports_live",
                "youtube_transcript", "project_update_context", "stock_history", "product_search", "video_search", "image_search"],
}
_HISTORY_MESSAGES = 30


_LOCKS_GUARD = threading.Lock()
_SESSION_LOCKS: dict[str, threading.RLock] = {}


def _session_lock(session_id: str) -> threading.RLock:
    """One turn at a time per chat; different chats run in parallel."""
    key = str(session_id or "default")
    with _LOCKS_GUARD:
        return _SESSION_LOCKS.setdefault(key, threading.RLock())


# Requests running now, and when the last one ended. Background work that needs
# the model (learning's reflections) waits until chats have been quiet a while.
_ACTIVITY = {"running": 0, "last_end": 0.0}


def is_busy(quiet_seconds: float = 30.0) -> bool:
    with _LOCKS_GUARD:
        return _ACTIVITY["running"] > 0 or time.time() - float(_ACTIVITY["last_end"]) < quiet_seconds


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
        caller_role: str = "owner",
        untrusted_sources: Optional[list[str]] = None,
    ) -> None:
        self.caller_role = caller_role if caller_role in CALLER_ROLES else "public"
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
        self._past_chat_lines: Optional[list[str]] = None
        self._user_message = ""
        self._handoffs = 0
        # Completion tracking for group chats and handed-off work (agent/lean/job.py).
        self.job: Optional[Job] = None
        # The task-board item being worked on right now; its owner's tool calls count toward it.
        self._active_subtask: Optional[Subtask] = None
        # Outside content read anywhere in this request (agent/lean/policy.py). Shared
        # by every agent on it: a handoff brief written after reading a web page can
        # carry that page's instructions.
        # A channel can hand over outside text with the message (e.g. Discord channel history).
        self._taint: list[str] = list(untrusted_sources or [])
        # For learning (agent/learning): which lessons each agent read, and which model it ran on.
        self._lessons_used: dict[str, list[str]] = {}
        self._endpoints: dict[str, tuple[str, str]] = {}
        # Parallel agents share these.
        self._emit_lock = threading.Lock()
        self._state_lock = threading.Lock()

    # ── public ──────────────────────────────────────────────────────────
    def run(self, message: str, *, persona_id: str = "") -> dict[str, Any]:
        with _session_lock(self.session_id):
            with _LOCKS_GUARD:
                _ACTIVITY["running"] += 1
            try:
                return self._run_locked(message, persona_id=persona_id)
            finally:
                with _LOCKS_GUARD:
                    _ACTIVITY["running"] -= 1
                    _ACTIVITY["last_end"] = time.time()
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

    def _is_group(self) -> bool:
        return bool(self.room and self.room.kind == "group" and len(self._members()) >= 2)

    def _add_result(self, result: TurnResult) -> None:
        """Every agent message of this request goes through here, in order."""
        self.results.append(result)
        if self.job is None:
            return
        name = self._name_of(result.agent_id)
        self.job.record_text(name, result.text, int((result.usage or {}).get("total") or 0))
        # What the agent actually ran is the evidence the completion check reads.
        for item in result.timeline or []:
            if item.get("kind") != "tool" or str(item.get("name") or "") in _BOOKKEEPING_TOOLS:
                continue
            ok = item.get("status") == "done"
            self.job.record_tool(name, str(item.get("name") or ""), str(item.get("label") or ""), ok, str(item.get("output") or ""))
            active = self._active_subtask
            if active is not None and active.owner == name:
                active.attempts += 1
                active.actions += 1 if ok else 0

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
        self.job = Job(goal=message, max_rounds=settings.group_max_rounds(), token_budget=settings.group_token_budget())
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
                    self._add_result(result)
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
            if not self._is_discussion() and success:
                # A text reply ended each agent's turn; it does not by itself finish the job.
                self._close_job(default_persona, history, self._format_user(message))
        except Exception as exc:
            logger.exception("Lean session failed")
            success = False
            error = f"{type(exc).__name__}: {exc}"

        final_text = next((r.text for r in reversed(self.results) if r.text), "")
        cancelled = self.cancel.is_set()
        outcome = self.job.outcome if self.job is not None else None
        if cancelled and self._job_needs_closing():
            outcome = self.job.stop("you stopped it")
        elif not success and self._job_needs_closing() and not outcome:
            outcome = self.job.stop(f"an agent hit an error: {_plain_error(error)}")
        if outcome:
            self.emit({"type": "run_outcome", **outcome})
        store.update_execution(
            execution.id,
            status="canceled" if cancelled else ("completed" if success else "failed"),
            success=bool(success and not cancelled),
            phase="cancelled" if cancelled else "complete",
            response_preview=final_text[:500],
            error=error[:1000],
            metadata={**dict(execution.metadata or {}), **({"outcome": outcome} if outcome else {})},
        )
        self._learn(cancelled)
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
            from agent.chat_titles import update_in_background as title_in_background
            title_in_background(self.session_id, message, lambda: self._side_client(default_persona))
        return {
            "execution_id": execution.id,
            "success": bool(success and not cancelled),
            "response": final_text,
            "messages": [
                {"message_id": r.message_id, "agent_id": r.agent_id, "text": r.text, "success": r.success}
                for r in self.results
            ],
            "error": error,
            "outcome": outcome,
        }

    def _learn(self, cancelled: bool) -> None:
        """Grade what each agent did and keep it as experience (agent/learning).

        Only the owner's requests; guests, channels' public callers and A2A never teach.
        Learning catches its own errors: this can't fail or slow the reply.
        """
        from agent.learning import record_request

        record_request(
            goal=request_text(self._user_message),
            results=self.results,
            job=self.job,
            taint=self._taint,
            session_id=self.session_id,
            execution_id=self.execution_id,
            source=self.source,
            caller_role=self.caller_role,
            names={r.agent_id: self._name_of(r.agent_id) for r in self.results},
            endpoints=dict(self._endpoints),
            lessons_used={k: list(v) for k, v in self._lessons_used.items()},
            team=self._job_needs_closing(),
            cancelled=cancelled,
        )

    # ── completion: when is the job done? ───────────────────────────────
    def _job_needs_closing(self) -> bool:
        """Group chats and any request where work was handed to a teammate."""
        return bool(self.job is not None and (self._is_group() or self.job.subtasks))

    def _close_job(self, default_persona: AgentPersona, history: list[dict[str, Any]], prompt_text: str) -> None:
        """Keep going until the request is done, or stop with a visible reason.

        After each round: open hand-offs mean not done; otherwise (in a group)
        a short check compares what was delivered with the request. If it
        isn't done, the right agent is told what is missing and continues.
        """
        job = self.job
        if job is None or not self._job_needs_closing():
            return
        job.rounds = max(job.rounds, 1)
        job.note_progress()
        while True:
            if self.cancel.is_set():
                job.stop("you stopped it")
                return
            verdict = self._judge(default_persona)
            if verdict.get("done"):
                job.finish(str(verdict.get("summary") or ""))
                if job.rounds > 1:
                    self._wrap_up(default_persona, history, prompt_text)
                return
            missing = str(verdict.get("reason") or "the request isn't finished").strip().rstrip(".")
            limit = job.backstop()
            if limit:
                job.stop(f"{limit}. Still missing: {missing}")
                return
            persona = self._continuation_agent(str(verdict.get("next") or ""), default_persona)
            instruction = str(verdict.get("instruction") or "").strip().rstrip(".")
            board = job.task_board()
            brief = (
                f"[System]: {persona.name}, the user's request isn't finished yet: {missing}."
                + (f" Next: {instruction}." if instruction else "")
                + (f"\n\nTask board:\n{board}\n\n" if board else " ")
                + "Do the work now with your tools (or hand it to the right teammate), then call complete_task "
                "with a short summary of what you delivered. Don't just say you will."
            )
            job.rounds += 1
            self.emit({"type": "job_continue", "round": job.rounds, "agent": persona.name, "reason": missing[:300]})
            continue_history = history + [
                {"role": "user", "content": prompt_text},
                {"role": "assistant", "content": job.transcript()},
            ]
            owned = next((item for item in job.open_subtasks() if item.owner == persona.name), None)
            previous_active, self._active_subtask = self._active_subtask, owned
            try:
                result = self._run_agent(persona, brief, history=continue_history, depth=0, meta={"continuation": job.rounds},
                                         task=instruction or missing)
                if not result.empty:
                    self._add_result(result)
            finally:
                self._active_subtask = previous_active
            for item in job.open_subtasks():
                if item.owner == persona.name and result.completed:
                    job.close_subtask(item, done=True, summary=result.completed)
            job.note_progress()
            if not result.success:
                job.stop(f"{persona.name} hit an error: {_plain_error(result.error)}")
                return

    def _wrap_up(self, default_persona: AgentPersona, history: list[dict[str, Any]], prompt_text: str) -> None:
        """After multi-step work is verified done: Echo tells the user what was done and asks what's next."""
        job = self.job
        if job is None or self.cancel.is_set():
            return
        members = self._members()
        speaker = next((m for m in members if m.id == "echo"), None) or (members[0] if members else default_persona)
        board = job.task_board()
        brief = (
            f"[System]: {speaker.name}, the team finished the user's request. Tell the user, in under 120 words, "
            "what was done (results, files, commands that passed), mention anything still open, then ask if they "
            "want anything else. Don't repeat the whole transcript.\n\n"
            + (f"Task board:\n{board}\n\n" if board else "")
            + f"What actually ran:\n{job.evidence_log(15)}"
        )
        wrap_history = history + [{"role": "user", "content": prompt_text}, {"role": "assistant", "content": job.transcript()}]
        turn = self._build_turn(speaker, brief, history=wrap_history, depth=0, allow_handoff=False,
                                meta={"role": "merge"}, allow_complete=False)
        result = turn.run(brief)
        if not result.empty:
            self._persist(speaker, result, 0, meta=turn.meta)
            self.results.append(result)

    def _judge(self, default_persona: AgentPersona) -> dict[str, Any]:
        job = self.job
        assert job is not None
        open_items = job.open_subtasks()
        if open_items:
            item = open_items[0]
            return {
                "done": False,
                "reason": f"{item.owner} hasn't finished the task {item.assigned_by} gave them ({item.task[:160]})",
                "next": item.owner,
                "instruction": f"finish it: {item.task[:300]}",
            }
        unbacked = job.unbacked_tasks()
        if unbacked:
            # "Done" with no tool call behind it, on a task that needed real work.
            item = unbacked[0]
            item.status = "open"
            return {
                "done": False,
                "reason": f"{item.owner} marked \"{item.task[:120]}\" done but made no tool call, so nothing was actually done",
                "next": item.owner,
                "instruction": f"actually do it with your tools: {item.task[:300]}",
            }
        delivered = [s.summary for s in job.subtasks if s.summary]
        last_text = next((text for _, text in reversed(job.texts) if text), "")
        fallback = (job.claim or {}).get("summary") or "; ".join(delivered) or _first_sentence(last_text)
        if not self._is_group():
            # One-to-one chat with hand-offs: every handed-off task is done and backed by work.
            return {"done": True, "summary": fallback}
        members = [_roster_line(m) for m in self._members()]
        data: dict[str, Any] = {}
        for _attempt in range(2):
            try:
                turn = self._review_client(default_persona).stream_turn(
                    [{"role": "user", "content": job.review_prompt(members)}],
                    cancel=self.cancel, temperature=0.1, max_tokens=700,
                )
                data = parse_review(turn.content or "")
            except Exception as exc:
                logger.warning("Completion check failed: {}", exc)
                data = {}
            if data and "done" in data:
                break
        if not data or "done" not in data:
            # No usable verdict: decide from the evidence instead of trusting the last message.
            last = job.evidence[-1] if job.evidence else None
            if last is not None and not last.ok:
                return {"done": False, "reason": f"the last action failed ({last.label})", "next": last.agent,
                        "instruction": "look at the error and fix it"}
            if needs_action(job.goal) and not job.successful_actions():
                return {"done": False, "reason": "nothing has been done yet, only discussed", "next": "",
                        "instruction": "do the work with your tools"}
            return {"done": True, "summary": fallback}
        if data.get("done") in (True, "true", "yes"):
            return {"done": True, "summary": str(data.get("summary") or fallback)}
        reason = str(data.get("reason") or "")
        if data.get("in_loop") in (True, "true", "yes"):
            job.stalls += 1
            reason = (reason + " (the team is going in circles)").strip()
        return {"done": False, "reason": reason, "next": str(data.get("next") or ""),
                "instruction": str(data.get("instruction") or "")}

    def _continuation_agent(self, name: str, default_persona: AgentPersona) -> AgentPersona:
        pool = self._members() or self.personas.list()
        # The check sees "Glados (Builder; can: …)" and may copy it whole.
        wanted = name.split("(")[0].strip().lstrip("@").casefold()
        for persona in pool:
            if wanted and wanted in {persona.name.casefold(), persona.id.casefold()}:
                return persona
        claimer = (self.job.claim or {}).get("by", "") if self.job else ""
        for persona in pool:
            if persona.name == claimer:
                return persona
        last = next((self.personas.get(r.agent_id) for r in reversed(self.results) if r.text), None)
        return last or default_persona

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
        from agent.learning import track_record

        # Track records come from graded past work (agent/learning/profiles.py), not self-description.
        roster = "\n".join(
            f"- {m.name}: {m.description or m.title}" + (f" ({record})" if (record := track_record(m.id)) else "")
            for m in members
        )
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
        meta: Optional[dict[str, Any]] = None,
        task: str = "",
    ) -> TurnResult:
        turn = self._build_turn(persona, message, history=history, depth=depth, delegated_by=delegated_by, meta=meta,
                                task=task)
        result = turn.run(message)
        if not result.empty:
            self._persist(persona, result, depth, meta=turn.meta)
        return result

    # ── work together (continuous groups) ──────────────────────────────
    def _is_discussion(self) -> bool:
        """A "work together" room (stored as mode "discussion" for older rooms)."""
        return bool(self.room and self.room.kind == "group" and self.room.mode == "discussion" and len(self._members()) >= 2)

    def _turn_budget(self) -> int:
        cap = int(self.room.max_messages or 0) if self.room else 0
        # Rooms made before 10.0.4 stored a talk cap of 4–8 messages; give those the default budget.
        return cap if cap > 12 else DEFAULT_WORK_TURNS

    def _run_discussion(self, first: list[AgentPersona], prompt_text: str, history: list[dict[str, Any]]) -> tuple[bool, str]:
        """Work together until the goal is verified done.

        Discuss -> Decide -> Execute -> Observe -> Verify -> Continue:
        a short round of views, then the lead turns them into owned tasks on the
        shared task board (assign_tasks), each owner does theirs with tools,
        every tool call is recorded as evidence, and the completion check judges
        the goal against that evidence. Whatever is missing becomes a new task.
        Backstops (turn budget, tokens, repetition, rounds without progress) end
        it with a visible reason; a verified finish ends with Echo's wrap-up.
        """
        job = self.job
        assert job is not None
        members = self._members()
        lead = members[0]
        order = list({p.id: p for p in [*first, *members]}.values())
        budget = self._turn_budget()
        job.max_rounds = max(job.max_rounds, budget)
        turns = 0
        success, error = True, ""

        def spend(persona: AgentPersona, brief: str, *, meta: dict[str, Any], allow_complete: bool = False,
                  allow_assign: bool = False, task: Optional[Subtask] = None, planning: bool = False) -> TurnResult:
            nonlocal turns, success, error
            turns += 1
            transcript = job.transcript()
            turn_history = history + [{"role": "user", "content": prompt_text}]
            if transcript:
                turn_history.append({"role": "assistant", "content": transcript})
            turn = self._build_turn(
                persona, brief, history=turn_history if turns > 1 else history, depth=0, allow_handoff=False,
                meta=meta, allow_complete=allow_complete, allow_assign=allow_assign,
                # Planning turns may look things up but not act, and "I'll run the tests once
                # it exists" is a plan there, not a broken promise.
                read_only=planning, promise_guard=not planning, task=task.task if task else "",
            )
            previous_active, self._active_subtask = self._active_subtask, task
            try:
                result = turn.run(brief if turns > 1 else f"{prompt_text}\n\n{brief}")
                if not result.empty:
                    # While the task is still active, so its owner's tool calls count toward it.
                    self._persist(persona, result, 0, meta=turn.meta)
                    self._add_result(result)
            finally:
                self._active_subtask = previous_active
            success = success and result.success
            error = error or result.error
            return result

        def out_of_budget() -> str:
            if turns >= budget:
                return f"it used its budget of {budget} agent turns"
            return job.backstop() if job.rounds >= 1 else ""

        goal = f"The user's goal: {self._user_message[:600]}"

        # 1. Discuss: one short round of views. Facts may be checked; work waits for the plan.
        for index, persona in enumerate(order[:MAX_DISCUSS_TURNS]):
            if self.cancel.is_set():
                break
            brief = (
                f"[System]: Work-together mode. {persona.name}, "
                + ("open with" if index == 0 else "read the views above and add")
                + " your take on how to get this done, or the answer if it's only a question. Under 100 words. "
                "You may check facts with read-only tools. Don't start the work yet: the lead assigns tasks next. "
                f"{goal}"
            )
            result = spend(persona, brief, meta={"discussion": index + 1}, planning=True)
            if not result.success:
                break
        if self.cancel.is_set() or not success:
            return self._end_work(success, error)

        # 2. Decide: the lead commits the discussion to owned tasks (or answers a plain question).
        brief = (
            f"[System]: {lead.name}, decide now. If the goal needs work (files, commands, changes, look-ups), call "
            "assign_tasks with concrete tasks for the right teammates, yourself included. If it was only a question, "
            f"answer it directly in one message instead. {goal}"
        )
        spend(lead, brief, meta={"role": "decide"}, allow_assign=True)
        if self.cancel.is_set() or not success:
            return self._end_work(success, error)
        job.note_progress(baseline=True)

        # 3–6. Execute -> Observe -> Verify -> Continue, until verified or a backstop trips.
        while not self.cancel.is_set():
            item = next(iter(job.open_subtasks()), None)
            if item is not None:
                stop = out_of_budget()
                if stop:
                    job.stop(f"{stop}. Still open: {item.task[:160]}")
                    break
                owner = self._continuation_agent(item.owner, lead)
                board = job.task_board()
                brief = (
                    f"[System]: {owner.name}, your task ({item.id}): {item.task}\n\n"
                    f"Task board:\n{board}\n\n{goal}\n\n"
                    "Do it now with your tools, check that it worked (run it, read it back, or test it), then call "
                    "complete_task with what you delivered. If you're blocked, say exactly what is blocking you."
                )
                result = spend(owner, brief, meta={"task": item.id}, allow_complete=True, task=item)
                if result.completed:
                    job.close_subtask(item, done=True, summary=result.completed)
                elif not result.success:
                    job.close_subtask(item, done=False, summary=_plain_error(result.error))
                elif item.actions and result.stop_reason != "promise_unfulfilled":
                    # Real work but no complete_task: done as far as the agent knows; Verify checks it.
                    job.close_subtask(item, done=True, summary=_first_sentence(result.text))
                elif item.attempts:
                    job.close_subtask(item, done=False, summary="tried, but it didn't work: " + _first_sentence(result.text, 160))
                else:
                    job.close_subtask(item, done=False, summary="no action was taken: " + _first_sentence(result.text, 160))
                if not result.success:
                    job.stop(f"{owner.name} hit an error: {_plain_error(result.error)}")
                    break
                continue

            # Every task on the board is closed: Verify against the goal and the evidence.
            job.rounds += 1
            job.note_progress()
            verdict = self._judge(lead)
            if verdict.get("done"):
                job.finish(str(verdict.get("summary") or ""))
                if job.subtasks or job.evidence:
                    self._wrap_up(lead, history, prompt_text)
                break
            missing = str(verdict.get("reason") or "the goal isn't met yet").strip().rstrip(".")
            stop = out_of_budget()
            if stop:
                job.stop(f"{stop}. Still missing: {missing}")
                break
            # Continue: what's missing becomes a new owned task on the board.
            owner = self._continuation_agent(str(verdict.get("next") or ""), lead)
            instruction = str(verdict.get("instruction") or "").strip().rstrip(".") or missing
            if not any(s.status == "open" and s.owner == owner.name for s in job.subtasks):
                job.open_subtask(owner=owner.name, task=instruction, assigned_by="check")
            self.emit({"type": "job_continue", "round": job.rounds, "agent": owner.name, "reason": missing[:300]})
        return self._end_work(success, error)

    def _end_work(self, success: bool, error: str) -> tuple[bool, str]:
        job = self.job
        if job is not None and not job.outcome and not self.cancel.is_set():
            job.stop("the team couldn't continue" + (f": {_plain_error(error)}" if error else ""))
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
            (persona, self._build_turn(
                persona, briefs[persona.id], history=history, depth=0, allow_handoff=False,
                meta={"parallel": True}, allow_complete=False,
            ))
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
                self._add_result(result)
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
        turn = self._build_turn(
            lead, brief, history=merge_history, depth=0, allow_handoff=False, meta={"role": "merge"}, allow_complete=False,
        )
        result = turn.run(brief)
        if not result.empty:
            self._persist(lead, result, 0, meta=turn.meta)
            self._add_result(result)
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
        allow_complete: bool = True,
        allow_assign: bool = False,
        read_only: bool = False,
        promise_guard: bool = True,
        task: str = "",
    ) -> LeanTurn:
        meta = dict(meta or {})
        if delegated_by is not None:
            meta["delegated_by"] = {"id": delegated_by.id, "name": delegated_by.name}
        guest = self.caller_role != "owner"
        can_hand_off = allow_handoff and depth < MAX_DELEGATION_DEPTH and not guest
        teammates = self._members() if self.room else [p for p in self.personas.list() if p.id != persona.id]
        if guest:
            toolbox = Toolbox(toolsets=GUEST_TOOLS[self.caller_role], extra_tools=rich_tools(), session_id=self.session_id)
        else:
            terminal = Terminal(self.project_root)
            toolbox = Toolbox(
                toolsets=persona.toolsets or None,
                extra_tools=self._native_tools(
                    persona, depth, delegated_by=delegated_by, allow_handoff=can_hand_off, allow_complete=allow_complete,
                    allow_assign=allow_assign,
                )
                + coding_tools()
                + rich_tools()
                + artifact_tools()
                + terminal.tools(),
                session_id=self.session_id,
                project_root=self.project_root,
            )
        if read_only:
            # Planning turns: look things up, but nothing that changes state.
            toolbox.restrict_to_read_only()
        # Recall from what is actually being asked (the user's request, plus the task this
        # agent was given), never from a "[System]: ..." brief full of boilerplate words.
        goal = self._goal(message, task)
        memories = [] if guest else self._recall(goal)
        from agent.project_context import context_for_session
        project_brief, project_evidence = ("", "") if guest else context_for_session(self.session_id)
        # Lessons from this agent's own checked past work, and tools that keep failing lately.
        from agent.learning import TurnLearning, prepare_turn
        learned = TurnLearning() if guest else prepare_turn(persona.id, goal, toolbox.names)
        if learned.lesson_ids:
            with self._state_lock:
                self._lessons_used.setdefault(persona.id, []).extend(learned.lesson_ids)
        # Where the repo stands (branch, ahead/behind, changes), like a coding agent's git status.
        repo_note = git_context.summary(self.project_root) if self.project_root and "terminal" in toolbox.names else ""
        prompt = build_system_prompt(
            persona=persona,
            soul_text=self._soul() if persona.id == "echo" else "",
            project_root=self.project_root,
            notes=list(toolbox.notes) + learned.notes,
            terminal_note=terminal.describe() if "terminal" in toolbox.names else "",
            git_note=repo_note,
            git_rules="terminal" in toolbox.names and git_context.wants_rules(goal, repo_note),
            project_overview=project_overview(self.project_root) if self.project_root else "",
            teammates=teammates if can_hand_off else [],
            room_name=self.room.name if self.room and self.room.kind == "group" else "",
            memories=memories,
            # Teammates start with a clean history, not without the story so far.
            chat_summary=summaries.summary_text(self.session_id),
            caller_note=caller_note(self.source, self.caller_role),
            past_chats=[] if guest else self._past_chats(),
            project_brief=project_brief,
            project_evidence=project_evidence,
            playbook=learned.section,
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
            promise_guard=promise_guard,
            taint=self._taint,
            goal=goal,
        )
        if self.source == "voice" and depth == 0:
            from agent.live_voice import claim_client
            live_client = claim_client(self.session_id, self.request_id, turn.client.endpoint, persona, history)
            if live_client is not None:
                previous = turn.client
                for key, value in self._clients.items():
                    if value is previous:
                        self._clients[key] = live_client
                previous.close()
                turn.client = live_client
        endpoint = getattr(turn.client, "endpoint", None)
        if endpoint is not None:
            # The reflector later uses the same model this agent worked with.
            with self._state_lock:
                self._endpoints[persona.id] = (str(endpoint.provider or ""), str(endpoint.model or ""))
        return turn

    def _record(self, persona: AgentPersona, part: TurnResult, depth: int, meta: dict[str, Any]) -> None:
        """A message closed before a handoff: save it now so it sorts before the teammate's."""
        self._add_result(part)
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
        allow_complete: bool = True,
        allow_assign: bool = False,
    ) -> list[NativeTool]:
        memory = getattr(self.agent, "memory", None)
        tools: list[NativeTool] = []
        from agent.research_notebook import research_tools
        tools.extend(research_tools(self.session_id))
        from agent.generation_service import generation_tools
        tools.extend(generation_tools(self.session_id, self.execution_id))

        # Work-together groups: the lead turns a decision into owned tasks on the shared board.
        if allow_assign and self.job is not None:
            members = self._members()

            def assign_tasks(args: dict[str, Any]) -> str:
                rows = args.get("tasks")
                if not isinstance(rows, list) or not rows:
                    return "Error: give 'tasks' as a list of {owner, task}."
                names = {m.name.casefold(): m.name for m in members} | {m.id.casefold(): m.name for m in members}
                added, problems = [], []
                for row in rows[:8]:
                    row = row if isinstance(row, dict) else {}
                    owner = names.get(str(row.get("owner") or "").strip().lstrip("@").casefold())
                    task = str(row.get("task") or "").strip()
                    if not owner or not task:
                        problems.append(str(row)[:80])
                        continue
                    item = self.job.open_subtask(owner=owner, task=task, assigned_by=persona.name)
                    added.append(f"{item.id} {owner}: {task[:120]}")
                if not added:
                    return "Error: no valid tasks. Owners must be one of: " + ", ".join(m.name for m in members)
                self.emit({"type": "task_board", "tasks": [
                    {"id": s.id, "owner": s.owner, "task": s.task[:200], "status": s.status} for s in self.job.subtasks
                ]})
                note = "" if not problems else f" Skipped {len(problems)} task(s) with an unknown owner or no text."
                return ("On the task board: " + "; ".join(added) + "." + note +
                        " Each owner does theirs next. End your message now with one short line on who does what; "
                        "don't start the work in this message.")

            tools.append(NativeTool(
                name="assign_tasks",
                # Who can do what, so work that needs a terminal or files goes to someone who has them.
                description=ASSIGN_TASKS_DESCRIPTION + " Team: " + "; ".join(_roster_line(m) for m in members),
                parameters={"type": "object", "properties": {
                    "tasks": {"type": "array", "items": {"type": "object", "properties": {
                        "owner": {"type": "string", "enum": [m.name for m in members]},
                        "task": {"type": "string", "description": "A concrete action with a checkable result."},
                    }, "required": ["owner", "task"]}},
                }, "required": ["tasks"]},
                func=assign_tasks,
                always=True,
            ))

        # Group chats and handed-off work end through complete_task, never by text alone.
        if allow_complete and (self._is_group() or depth > 0):
            def complete_task(args: dict[str, Any]) -> str:
                summary = str(args.get("summary") or "").strip()
                if not summary:
                    return "Error: say what you delivered in 'summary' (one or two sentences)."
                if depth == 0 and self.job is not None:
                    self.job.claim_complete(persona.name, summary)
                return "Marked as done."

            tools.append(NativeTool(
                name="complete_task",
                description=COMPLETE_TASK_DESCRIPTION,
                parameters={"type": "object", "properties": {
                    "summary": {"type": "string", "description": "What you delivered, in one or two sentences."},
                }, "required": ["summary"]},
                func=complete_task,
                ends_turn=True,
                always=True,
            ))
        # Every agent can stop and ask the user when it is blocked or the choice is theirs.
        from agent.lean.ask import ask_user_tool

        tools.append(ask_user_tool())
        if memory is not None:
            def memory_save(args: dict[str, Any]) -> str:
                fact = memory_quality.tidy_fact(str(args.get("fact") or args.get("text") or ""))
                problem = memory_quality.check_fact(fact)
                if problem:
                    return problem
                key = memory_quality.normalize_key(str(args.get("key") or ""))
                if not key:
                    # A reworded copy of something already saved: keep the one we have.
                    owner_of = getattr(memory, "_owner_id", None)
                    owner = owner_of() if callable(owner_of) else ""
                    existing = memory_quality.find_duplicate(fact, (
                        record for record in list((getattr(memory, "_records", None) or {}).values())
                        if (not owner or str(record.get("owner_id") or "") == owner)
                        and str(record.get("scope") or "account") == "account"
                    ))
                    if existing is not None:
                        return f"Already remembered: {existing.get('text')}"
                memory_id = memory.add_memory_item(
                    fact,
                    memory_type=memory_quality.KINDS.get(str(args.get("type") or "fact").lower(), "note"),
                    pinned=bool(args.get("pinned", False)),
                    thread_id=self.session_id,
                    source="agent",
                    scope="account",
                    source_execution_id=self.execution_id,
                    # Same key, new value: the old memory is superseded, not kept alongside.
                    semantic_key=key,
                )
                if not memory_id:
                    return "Failed: that could not be saved (empty, duplicate, or looks like a secret)."
                # Read back from the durable record file, not this process's copy.
                stored = _memory_on_disk(memory, str(memory_id))
                if stored is False:
                    return "Failed: the memory store reported a save, but it isn't on disk. Tell the user it did not save."
                self.emit({"type": "memory_saved", "memory_count": int(memory.count_items() or 0)})
                if isinstance(stored, str) and " ".join(stored.split()) != " ".join(fact.split()):
                    return f"Already remembered (verified on disk): {stored}"
                return f"Saved and verified: {fact}"

            def memory_search(args: dict[str, Any]) -> str:
                query = str(args.get("query") or "").strip()
                rows = self._recall(query, limit=10)
                try:
                    docs = memory.retrieve_relevant(query, k=5, thread_id=self.session_id)
                    # Saved facts only; old chat transcripts are found with chat_search.
                    rows += [{"content": getattr(doc, "page_content", "")} for doc in docs
                             if str((getattr(doc, "metadata", None) or {}).get("type") or "") != "conversation"]
                except Exception:
                    pass
                lines = list(dict.fromkeys(str(r.get("content") or "").strip() for r in rows if str(r.get("content") or "").strip()))
                return "\n".join(f"- {line}" for line in lines[:12]) or "No matching memories."

            tools.append(NativeTool(
                name="memory_save",
                description="Save one lasting fact about the user so you remember it in future chats: who they are, what they "
                "prefer, people and projects in their life, how they like you to work. Save when the user tells you something "
                "durable or asks you to remember it. Don't save one-off requests, things that only matter in this chat, web "
                "content, or secrets. One fact per call.",
                parameters={"type": "object", "properties": {
                    "fact": {"type": "string", "description": "One short third-person sentence that still makes sense later, "
                             "e.g. \"Prefers short answers.\" or \"Partner is called Sam.\""},
                    "type": {"type": "string", "enum": list(memory_quality.KINDS),
                             "description": "What kind of fact this is."},
                    "key": {"type": "string", "description": "For facts that can change, a short stable name like home_city, "
                            "job or diet. A new fact with the same key replaces the old one."},
                    "pinned": {"type": "boolean", "description": "True only for core facts that matter in almost every chat "
                               "(name, key preferences). Pinned facts are always in your context."},
                }, "required": ["fact"]},
                func=memory_save,
            ))
            tools.append(NativeTool(
                name="memory_search",
                description="Search the facts you have saved about the user (preferences, people, projects). "
                "To find what was said in past conversations, use chat_search.",
                parameters={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                func=memory_search,
                parallel_safe=True,
            ))

        tools.append(NativeTool(
            name="soul_update",
            description=soul.DESCRIPTION,
            parameters=soul.PARAMETERS,
            func=lambda args: self._update_soul(persona, args),
        ))

        def chat_search(args: dict[str, Any]) -> str:
            from agent.state import get_state_store

            hits = get_state_store().search_messages(str(args.get("query") or ""), limit=8)
            hits = [hit for hit in hits if hit["turn_id"] != self.execution_id]
            if not hits:
                return "No past messages match."
            lines = []
            for hit in hits:
                when = time.strftime("%Y-%m-%d", time.localtime(float(hit["created_at"] or 0)))
                who = "user" if hit["role"] == "user" else (hit["agent"] or "assistant")
                here = " (this chat)" if hit["session_id"] == self.session_id else ""
                lines.append(f"- {when}{here} {who}: {hit['snippet']}")
            return "\n".join(lines)

        store = getattr(self.agent, "document_store", None)
        if store is not None and getattr(store, "enabled", False):
            def document_search(args: dict[str, Any]) -> str:
                from agent.state import get_state_store

                query = str(args.get("query") or "").strip()
                if not query:
                    return "Give a query."
                project_id = str(get_state_store().get_thread_state(self.session_id).active_project_id or "")
                context, sources = store.query(query, k=5, project_id=project_id, session_id=self.session_id)
                if not sources:
                    return "No uploaded document matches."
                return context

            tools.append(NativeTool(
                name="document_search",
                description="Search the documents the user uploaded (PDFs, notes, text files) for passages about a topic. "
                "Results name the document they came from.",
                parameters={"type": "object", "properties": {"query": {"type": "string", "description": "What to look for."}}, "required": ["query"]},
                func=document_search,
                parallel_safe=True,
            ))

        tools.append(NativeTool(
            name="chat_search",
            description="Search the words of every past conversation with the user (all chats, including group chats). "
            "Use it when the user refers to something discussed before. For saved facts about the user, use memory_search.",
            parameters={"type": "object", "properties": {"query": {"type": "string", "description": "A few keywords."}}, "required": ["query"]},
            func=chat_search,
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
                    subtask = self.job.open_subtask(owner=target.name, task=task, assigned_by=persona.name) if self.job else None
                    brief = (
                        f"{persona.name} handed you this task:\n{task}\n\n"
                        "Do it yourself with your tools now. When it's finished, call complete_task with a short "
                        "summary of what you delivered. If you can't finish it, say what is blocking you."
                    )
                    if self._user_message:
                        brief += f"\n\nFor context, the user's message was:\n{self._user_message[:800]}"
                    previous_active, self._active_subtask = self._active_subtask, subtask
                    try:
                        result = self._run_agent(target, brief, history=[], depth=depth + 1, delegated_by=persona, task=task)
                        if not result.empty:
                            self._add_result(result)
                    finally:
                        self._active_subtask = previous_active
                    promised_only = result.stop_reason == "promise_unfulfilled" or not result.text.strip()
                    if subtask is not None:
                        if result.completed:
                            self.job.close_subtask(subtask, done=True, summary=result.completed)
                        elif not result.success:
                            self.job.close_subtask(subtask, done=False, summary=result.error)
                        elif not promised_only:
                            # A real answer without complete_task: answered. In a group,
                            # the completion check still verifies it against the request.
                            self.job.close_subtask(subtask, done=True, summary=_first_sentence(result.text))
                    # A structured result, so the caller can tell "done" from "said they would".
                    if result.completed:
                        return f"{target.name} finished the task.\nDelivered: {result.completed}\n\n{target.name}'s reply:\n{result.text}"
                    if not result.success:
                        return f"{target.name} could not do the task ({result.error[:200]}).\n{result.text}"
                    if promised_only:
                        return (
                            f"{target.name} said they would do it but did NOT do anything:\n{result.text}\n\n"
                            "The task is still open. Do it yourself, or tell the user what is blocking it."
                        )
                    return f"{target.name} replied:\n{result.text}"

                tools.append(NativeTool(
                    name="delegate_to_agent",
                    description="Hand a task to a teammate who is better suited. They do the work with their own tools "
                    "and report back whether it is finished. Use it to get work done, not to announce or chat. Teammates: "
                    + "; ".join(_roster_line(c) for c in candidates),
                    parameters={"type": "object", "properties": {
                        "agent": {"type": "string", "enum": [c.name for c in candidates]},
                        "task": {"type": "string", "description": "Everything they need to do the task."},
                    }, "required": ["agent", "task"]},
                    func=delegate,
                    handoff=check,
                    always=True,
                ))
        return tools

    def _update_soul(self, persona: AgentPersona, args: dict[str, Any]) -> str:
        """Edit the soul this agent's prompt is actually built from, then prove it took.

        A persona with its own soul text uses that; Echo without one uses SOUL.md.
        The read-back goes through the loader the next chat uses, so "Saved and
        verified" means a new session will see the change.
        """
        from agent.lean.personas import PersonaStore

        action, text, old_text = (str(args.get(key) or "") for key in ("action", "text", "old_text"))
        current = self.personas.get(persona.id) or persona
        if current.id == "echo" and not current.soul.strip():
            enabled, _, max_chars = soul.soul_settings()
            if not enabled:
                return "Failed: the soul is turned off in Settings, so it can't be changed. Nothing was changed."
            path = soul.soul_path()
            loader = getattr(self.agent, "_load_soul", None)
            ok, message, new_text = soul.update_soul(
                action=action, text=text, old_text=old_text,
                read=lambda: soul.read_file(path),
                write=lambda body: soul.write_atomic(path, body),
                read_back=soul.load_check(loader if callable(loader) else None, path),
                max_chars=max_chars,
            )
        else:
            store = self.personas
            base = current.soul.strip() or f"You are {current.name}, a capable personal agent."
            ok, message, new_text = soul.update_soul(
                action=action, text=text, old_text=old_text,
                read=lambda: base,
                write=lambda body: store.update(current.id, {"soul": body.strip()}),
                # A fresh store reads the file, not this process's copy.
                read_back=lambda: (PersonaStore(store.path).get(current.id) or current).soul,
                max_chars=12000,
            )
        if ok:
            if current.id == "echo" and not current.soul.strip():
                self._soul_text = new_text.strip()
            self.emit({"type": "soul_updated", "agent_id": current.id})
        return message

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

    def _review_client(self, persona: AgentPersona) -> ChatClient:
        """The completion check: a short, thinking-off call, closed with the session."""
        return self._client_for(persona, routing=True)

    def _side_client(self, persona: AgentPersona) -> ChatClient:
        """A separate, thinking-off client for background work (summaries)."""
        endpoint = self._endpoint_for(persona)
        return ChatClient(endpoint, reasoning_effort=reasoning_effort_for(endpoint, False, self.reasoning_effort))

    def _temperature(self) -> Optional[float]:
        try:
            from config import config

            from agent.cloud_providers import CLOUD_PROVIDERS, cloud_config
            provider = str(getattr(getattr(self.agent, "llm_provider", None), "value", ""))
            return float(cloud_config(provider).temperature if provider in CLOUD_PROVIDERS else config.local.temperature)
        except Exception:
            return None

    def _soul(self) -> str:
        if self._soul_text is None:
            try:
                self._soul_text = str(self.agent._load_soul() or "")
            except Exception:
                self._soul_text = ""
        return self._soul_text

    def _goal(self, message: str, task: str = "") -> str:
        """What this agent is working toward: its task (if it was given one) and the user's request."""
        request = request_text(self._user_message or message).strip()
        task = str(task or "").strip()
        return f"{task}\n(The user asked: {request})" if task and task not in request else request

    def _past_chats(self) -> list[str]:
        """When the user points back ("last time", "what we discussed"), the matching past messages.

        Retrieval in code rather than hoping the agent calls chat_search: this is
        exactly the moment agents otherwise act lost. Other chats only; this one
        is already in the history and its summary.
        """
        if self._past_chat_lines is not None:
            return self._past_chat_lines
        self._past_chat_lines = []
        request = request_text(self._user_message)
        if not recall.points_back(request):
            return self._past_chat_lines
        terms = keywords(request, limit=8)
        if not terms:
            return self._past_chat_lines
        try:
            from agent.state import get_state_store

            hits = get_state_store().search_messages(" ".join(terms), limit=12, any_term=True)
        except Exception:
            logger.debug("Past-chat lookup failed", exc_info=True)
            return self._past_chat_lines
        lines: list[str] = []
        for hit in hits:
            if hit["session_id"] == self.session_id or hit["turn_id"] == self.execution_id:
                continue
            when = time.strftime("%Y-%m-%d", time.localtime(float(hit["created_at"] or 0)))
            who = "user" if hit["role"] == "user" else (hit["agent"] or "assistant")
            lines.append(f"{when} {who}: {hit['snippet']}")
            if len(lines) >= 5:
                break
        self._past_chat_lines = lines
        return lines

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
        if self.caller_role != "owner":
            return
        # Raw transcripts are opt-in (Settings › Memory). Chats are already kept and searchable
        # (chat_search); copying every turn into memory only crowded out the real facts.
        from config import config

        if not bool(getattr(config, "memory_auto_store_conversations", False)):
            return

        def work() -> None:
            try:
                memory.add_conversation(request_text(message), answer, thread_id=self.session_id)
            except Exception:
                logger.debug("Lean conversation memory write failed", exc_info=True)

        threading.Thread(target=work, name="lean-memory", daemon=True).start()


def _memory_on_disk(memory: Any, memory_id: str) -> "str | bool | None":
    """The saved text if the record is active in records.json, False if it isn't,
    None when this memory store has no record file to check."""
    path = getattr(memory, "_records_path", None)
    if not path:
        return None
    try:
        records = json.loads(Path(path).read_text(encoding="utf-8")).get("records") or {}
    except (OSError, ValueError):
        return False
    record = records.get(memory_id) if isinstance(records, dict) else None
    if not isinstance(record, dict) or not record.get("active", True):
        return False
    return str(record.get("text") or "")


def _plain_error(error: str) -> str:
    """A short, readable reason (no HTML error pages from model servers)."""
    text = _friendly_error(str(error or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split())[:200].rstrip(" .") or "unknown error"


# What each toolset lets an agent do, in the words a model choosing an owner needs.
_TOOLSET_ABILITIES = {
    "core": "read and write files", "terminal": "run terminal commands", "web": "search the web",
    "live": "look up live data (weather, sports, stocks, shopping)", "research": "search the web and live data",
    "vision": "see the screen", "desktop": "control desktop apps", "comms": "email and Discord",
    "memory": "recall memory", "self": "edit EchoSpeak itself", "skills": "installed skills",
}


def _roster_line(persona: AgentPersona) -> str:
    """'Jarvis (Researcher; can: search the web, recall memory; can't: read and write files, run terminal commands)'."""
    from agent.learning import track_record

    record = track_record(persona.id)
    suffix = f" [{record}]" if record else ""
    toolsets = list(persona.toolsets or DEFAULT_TOOLSETS)
    if "all" in toolsets:
        return f"{persona.name} ({persona.title or 'agent'}; can use every tool){suffix}"
    can = [_TOOLSET_ABILITIES[t] for t in toolsets if t in _TOOLSET_ABILITIES]
    cannot = [_TOOLSET_ABILITIES[t] for t in ("core", "terminal") if t not in toolsets]
    return (f"{persona.name} ({persona.title or 'agent'}; can: {', '.join(can) or 'answer from what it knows'}"
            + (f"; can't: {', '.join(cannot)}" if cannot else "") + ")" + suffix)


def _first_sentence(text: str, limit: int = 200) -> str:
    body = " ".join(str(text or "").split())
    match = re.match(r"(.+?[.!?])(\s|$)", body)
    return (match.group(1) if match else body)[:limit]


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
    caller_role: str = "owner",
    untrusted_sources: Optional[list[str]] = None,
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
        caller_role=caller_role,
        untrusted_sources=untrusted_sources,
    )
    return session.run(message, persona_id=persona_id)


def run_lean_text(agent: Any, *, message: str, session_id: str, source: str, request_id: str = "",
                  caller_role: str = "owner") -> tuple[str, bool]:
    """Non-streaming entry for channels (Discord, Telegram, routines, /query)."""
    result = run_lean_query(
        agent,
        message=message,
        session_id=session_id,
        request_id=request_id or str(uuid.uuid4()),
        emit=lambda _event: None,
        cancel=threading.Event(),
        source=source,
        caller_role=caller_role,
    )
    return str(result.get("response") or ""), bool(result.get("success"))
