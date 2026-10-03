"""The lean agent loop.

    messages -> model -> tool calls? -> run tools -> messages -> model -> ... -> answer

The loop ends when the model replies without calling a tool, or when it calls
a "final output" tool (complete_task). A reply that only promises work ("I'll
do that", "On it") does not end it: the agent is asked to act first. Failures
inside the loop (bad arguments, tool errors, denied approvals, repeated calls)
are returned to the model as tool results so it can adapt; they never end the
turn on their own.

Ending a turn is not the same as finishing a job: in group chats and handed-
off work, agent/lean/job.py decides when the user's request is done.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from loguru import logger

from agent.lean import policy, settings
from agent.lean.approvals import get_approval_broker, tool_needs_approval
from agent.lean.job import claim_nudge, is_promise_without_action, promise_nudge, unbacked_claim
from agent.lean.personas import AgentPersona
from agent.lean.provider import ChatClient, ModelTurn, ProviderError, extract_text_tool_calls
from agent.lean.toolbox import Toolbox, describe_call, safe_args_preview

Emit = Callable[[dict[str, Any]], None]

NUDGE_EMPTY = (
    "You stopped without replying to the user. Continue now: call a tool if more work is needed, "
    "otherwise write your answer."
)
NUDGE_TRUNCATED = "Your last reply was cut off by the output limit. Continue from where you stopped, concisely."


@dataclass
class TurnResult:
    text: str
    success: bool
    message_id: str
    agent_id: str
    timeline: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    # True for a continuation after a handoff that ended without saying anything.
    empty: bool = False
    # "max_steps" when the loop stopped at the step limit before an answer;
    # "promise_unfulfilled" when the agent kept promising work without doing it.
    stop_reason: str = ""
    # The summary passed to complete_task, if the agent marked its work done.
    completed: str = ""
    usage: dict[str, int] = field(default_factory=dict)


def _estimate_tokens(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> int:
    size = len(json.dumps(tools, ensure_ascii=False)) if tools else 0
    for message in messages:
        size += len(str(message.get("content") or "")) + 16
        for call in message.get("tool_calls") or []:
            size += len(str(call.get("function", {}).get("arguments") or "")) + 32
    return int(size / 3.4)


class LeanTurn:
    """One agent answering one message (optionally inside a group room)."""

    def __init__(
        self,
        *,
        client: ChatClient,
        persona: AgentPersona,
        system_prompt: str,
        history: list[dict[str, Any]],
        toolbox: Toolbox,
        session_id: str,
        request_id: str,
        execution_id: str,
        emit: Emit,
        cancel: threading.Event,
        temperature: Optional[float] = None,
        persist_tool_runs: bool = True,
        interactive: bool = True,
        on_seal: Optional[Callable[[TurnResult], None]] = None,
        meta: Optional[dict[str, Any]] = None,
        promise_guard: bool = True,
        taint: Optional[list[str]] = None,
    ) -> None:
        # Tools that brought outside content into this request (shared by every
        # agent working on it). Drives the Rule-of-Two policy in agent/lean/policy.py.
        self.taint: list[str] = taint if taint is not None else []
        self._policy_reasons: dict[str, str] = {}
        self.interactive = interactive
        # Re-prompt an agent whose reply only promises work (see agent/lean/job.py).
        self.promise_guard = promise_guard
        self.completed_summary: Optional[str] = None
        # Tools that worked in this turn: what a reply's "I've saved it" must be backed by.
        self.succeeded: set[str] = set()
        self._on_seal = on_seal
        self._handed_off = False
        self.compactions = 0
        # Extra fields on agent_start, e.g. who handed this work over.
        self.meta = dict(meta or {})
        self.client = client
        self.persona = persona
        self.system_prompt = system_prompt
        self.history = history
        self.toolbox = toolbox
        self.session_id = session_id
        self.request_id = request_id
        self.execution_id = execution_id
        self.cancel = cancel
        self.temperature = temperature
        self.persist_tool_runs = persist_tool_runs
        self.message_id = f"msg_{uuid.uuid4().hex[:12]}"
        self._emit = emit
        self.timeline: list[dict[str, Any]] = []
        self.usage = {"prompt": 0, "completion": 0, "total": 0}
        self._emit_lock = threading.Lock()

    # ── events ──────────────────────────────────────────────────────────
    def emit(self, event: dict[str, Any]) -> None:
        payload = {
            **event,
            "request_id": self.request_id,
            "agent_id": self.persona.id,
            "message_id": self.message_id,
            "at": time.time(),
        }
        with self._emit_lock:
            try:
                self._emit(payload)
            except Exception:
                logger.exception("Lean event emit failed")

    def _retract_text(self, step: int) -> None:
        """Take back text streamed in this step (a claim that turned out to be untrue)."""
        if any(item.get("kind") == "text" and item.get("step") == step for item in self.timeline):
            self.timeline = [item for item in self.timeline if not (item.get("kind") == "text" and item.get("step") == step)]
            self.emit({"type": "text_replace", "step": step, "text": ""})

    def _close_thinking(self) -> None:
        for item in reversed(self.timeline):
            if item.get("kind") == "thinking":
                item.setdefault("ended_at", time.time())
                break

    def _append_text(self, kind: str, step: int, text: str) -> None:
        if not text:
            return
        last = self.timeline[-1] if self.timeline else None
        if last and last.get("kind") == kind and last.get("step") == step:
            last["text"] += text
        else:
            if kind != "thinking":
                self._close_thinking()
            self.timeline.append({"kind": kind, "step": step, "text": text, "at": time.time()})

    # ── main loop ───────────────────────────────────────────────────────
    def _start_message(self, **extra: Any) -> None:
        self.emit({
            "type": "agent_start",
            "agent": {
                "id": self.persona.id,
                "name": self.persona.name,
                "title": self.persona.title,
                "initials": self.persona.initials(),
            },
            **extra,
        })

    def _visible_text(self) -> str:
        return "\n\n".join(
            item["text"].strip() for item in self.timeline if item.get("kind") == "text" and item.get("text", "").strip()
        ).strip()

    def _seal_for_handoff(self, step: int) -> None:
        """Close the current message so a teammate's reply lands after it.

        Whatever this agent says after the handoff streams into a new message
        below the teammate's, so the chat reads in the order things happened.
        """
        self._close_thinking()
        sealed = TurnResult(
            text=self._visible_text(),
            success=True,
            message_id=self.message_id,
            agent_id=self.persona.id,
            timeline=self.timeline,
        )
        self.emit({"type": "agent_done", "text": sealed.text, "success": True, "error": "",
                   "steps": step, "handoff": True, "usage": dict(self.usage)})
        if self._on_seal is not None:
            try:
                self._on_seal(sealed)
            except Exception:
                logger.exception("Lean handoff seal failed")
        self.message_id = f"msg_{uuid.uuid4().hex[:12]}"
        self.timeline = []
        self._handed_off = True

    def announce(self) -> None:
        """Open this agent's message. Parallel runs call it first, in order."""
        self._start_message(**self.meta)

    def run(self, user_message: str, *, announce: bool = True) -> TurnResult:
        if announce:
            self.announce()
        tools = self.toolbox.schemas()
        messages: list[dict[str, Any]] = [{"role": "system", "content": self.system_prompt}]
        messages.extend(self.history)
        self._history_in_messages = len(self.history)
        messages.append({"role": "user", "content": user_message})

        max_steps = settings.max_iterations()
        call_counts: dict[str, int] = {}
        stop_reason = ""
        nudges = 0
        promise_nudges = 0
        claim_nudges = 0
        final_text = ""
        error = ""
        success = True
        overflow_retries = 0
        step = 0

        while step < max_steps:
            if self.cancel.is_set():
                error = "cancelled"
                success = False
                break
            step += 1
            self._fit_context(messages, tools)
            self.emit({"type": "step_start", "step": step})
            try:
                turn = self._call_model(messages, tools, step)
            except ProviderError as exc:
                if exc.context_overflow and overflow_retries < 2:
                    overflow_retries += 1
                    self._fit_context(messages, tools, aggressive=True)
                    step -= 1
                    continue
                error = str(exc)
                success = False
                break
            except Exception as exc:  # network errors, model unloaded, etc.
                logger.exception("Lean model call failed")
                error = f"{type(exc).__name__}: {exc}"
                success = False
                break

            if turn.finish_reason == "cancelled":
                error = "cancelled"
                success = False
                break

            calls = list(turn.tool_calls)
            content = turn.content
            if not calls and content:
                # Recover tool calls written as text when native calling slipped.
                recovered, cleaned = extract_text_tool_calls(content, set(self.toolbox.names))
                if recovered:
                    calls, content = recovered, cleaned

            assistant: dict[str, Any] = {"role": "assistant", "content": content or ""}
            if calls:
                assistant["tool_calls"] = [
                    {"id": call.id, "type": "function", "function": {"name": call.name, "arguments": call.arguments or "{}"}}
                    for call in calls
                ]
            messages.append(assistant)

            if not calls:
                if content.strip():
                    if self.promise_guard and self.toolbox.names and is_promise_without_action(content):
                        # "Sure, I'll do that" is not the work. Ask for the action
                        # (twice at most) instead of ending the turn on a promise.
                        if promise_nudges < 2:
                            promise_nudges += 1
                            messages.append({"role": "user", "content": promise_nudge(self.toolbox.names)})
                            self.emit({"type": "promise_nudge", "step": step, "count": promise_nudges})
                            continue
                        stop_reason = "promise_unfulfilled"
                    claim = unbacked_claim(content, self.succeeded) if self.promise_guard and self.toolbox.names else ""
                    if claim and not stop_reason:
                        # "I've saved it" with nothing saved: ask once for the tool call or the truth.
                        if claim_nudges < 1:
                            claim_nudges += 1
                            messages.append({"role": "user", "content": claim_nudge(claim)})
                            self.emit({"type": "claim_nudge", "step": step, "claim": claim[:200]})
                            self._retract_text(step)
                            continue
                        stop_reason = "unverified_claim"
                        note = f"Not verified: no tool call in this reply did this (“{claim[:160]}”)."
                        self.timeline.append({"kind": "note", "step": step, "text": note, "at": time.time()})
                        self.emit({"type": "claim_unverified", "step": step, "claim": claim[:200], "note": note})
                    final_text = content.strip()
                    break
                if self._handed_off:
                    # The teammate already answered; nothing more is needed.
                    final_text = ""
                    break
                if nudges < 2:
                    nudges += 1
                    messages.pop()
                    messages.append({
                        "role": "user",
                        "content": NUDGE_TRUNCATED if turn.finish_reason == "length" else NUDGE_EMPTY,
                    })
                    continue
                final_text = ""
                break

            self._run_tools(calls, messages, step, call_counts)
            completed = self._completion_from(calls, messages)
            if completed is not None:
                # complete_task is a final-output tool: the agent's work is marked done.
                self.completed_summary = completed
                final_text = content.strip()
                break
        else:
            # Out of steps: stop honestly instead of forcing a rushed answer.
            # The note lists what was done so a "Continue" turn has context.
            stop_reason = "max_steps"
            note = self._out_of_steps_note(max_steps)
            self._append_text("text", step, note)
            self.emit({"type": "agent_token", "step": step, "data": note})

        visible = self._visible_text()
        if not visible:
            visible = final_text
        if not visible and self.completed_summary:
            # The agent only called complete_task: show what it delivered.
            visible = self.completed_summary
            self._append_text("text", step, visible)
            self.emit({"type": "agent_token", "step": step, "data": visible})
        empty = bool(self._handed_off and not visible and error in {"", "cancelled"})
        if not visible and not empty:
            if error == "cancelled":
                visible = "Stopped."
            elif error:
                visible = _friendly_error(error)
            else:
                visible = "I couldn't put together a reply this time. Try asking again or rephrasing."
            self._append_text("text", step, visible)
            self.emit({"type": "agent_token", "step": step, "data": visible})
        self.emit({
            "type": "agent_done",
            "text": visible,
            "success": success,
            "error": error,
            "steps": step,
            "usage": dict(self.usage),
            "empty": empty,
            "stop_reason": stop_reason,
            "completed": self.completed_summary or "",
        })
        return TurnResult(
            text=visible,
            success=success,
            message_id=self.message_id,
            agent_id=self.persona.id,
            timeline=self.timeline,
            error=error,
            empty=empty,
            stop_reason=stop_reason,
            completed=self.completed_summary or "",
            usage=dict(self.usage),
        )

    def _completion_from(self, calls: list[Any], messages: list[dict[str, Any]]) -> Optional[str]:
        """The summary from a successful complete_task call in this step, if any."""
        for call in calls:
            name = self.toolbox.resolve_name(call.name)
            if not self.toolbox.ends_turn(name):
                continue
            result = next((m for m in reversed(messages) if m.get("role") == "tool" and m.get("tool_call_id") == call.id), None)
            if result is None or str(result.get("content") or "").lower().startswith("error"):
                continue
            args, _ = call.parsed_arguments()
            return str(args.get("summary") or "").strip() or "Done."
        return None

    def _out_of_steps_note(self, max_steps: int) -> str:
        done = [str(item.get("label") or item.get("name") or "") for item in self.timeline if item.get("kind") == "tool"]
        done = [label for label in dict.fromkeys(done) if label]
        lines = [f"I ran out of steps ({max_steps}) before finishing."]
        if done:
            shown = done[-8:]
            lines.append("So far I: " + "; ".join(shown) + ("; and more." if len(done) > len(shown) else "."))
        lines.append("Press Continue and I'll pick up where I left off.")
        return "\n\n".join(lines)

    # ── model ───────────────────────────────────────────────────────────
    def _call_model(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], step: int) -> ModelTurn:
        def on_reasoning(text: str) -> None:
            self._append_text("thinking", step, text)
            self.emit({"type": "reasoning_delta", "step": step, "text": text})

        def on_content(text: str) -> None:
            self._append_text("text", step, text)
            self.emit({"type": "agent_token", "step": step, "data": text})

        turn = self.client.stream_turn(
            messages,
            tools=tools or None,
            on_reasoning=on_reasoning,
            on_content=on_content,
            cancel=self.cancel,
            temperature=self.temperature,
        )
        if turn.usage:
            for key in ("prompt", "completion", "total"):
                self.usage[key] += int(turn.usage.get(key) or 0)
            self.emit({"type": "token_usage", **turn.usage, "context_limit": settings.context_tokens()})
        # In group chats the transcript shows "[Name]: text"; models sometimes copy
        # that onto their own reply. Drop it.
        own_prefix = re.match(rf"\s*\[{re.escape(self.persona.name)}\]:\s*", turn.content or "")
        if own_prefix:
            turn.content = turn.content[own_prefix.end():]
            for item in reversed(self.timeline):
                if item.get("kind") == "text" and item.get("step") == step:
                    item["text"] = re.sub(rf"^\s*\[{re.escape(self.persona.name)}\]:\s*", "", item["text"])
                    self.emit({"type": "text_replace", "step": step, "text": item["text"]})
                    break
        # Text that turned out to be a text-encoded tool call should not stay visible.
        if not turn.tool_calls and turn.content:
            recovered, cleaned = extract_text_tool_calls(turn.content, set(self.toolbox.names))
            if recovered:
                for item in reversed(self.timeline):
                    if item.get("kind") == "text" and item.get("step") == step:
                        item["text"] = cleaned
                        break
                self.emit({"type": "text_replace", "step": step, "text": cleaned})
        return turn

    # ── tools ───────────────────────────────────────────────────────────
    def _run_tools(self, calls: list[Any], messages: list[dict[str, Any]], step: int, call_counts: dict[str, int]) -> None:
        prepared: list[tuple[Any, str, dict[str, Any], str]] = []
        for call in calls:
            name = self.toolbox.resolve_name(call.name)
            args, arg_error = call.parsed_arguments()
            signature = self._sig(name, args)
            call_counts[signature] = call_counts.get(signature, 0) + 1
            problem = arg_error or ("" if call_counts[signature] < 3 else "repeat")
            if not problem:
                # Checked for every call, parallel read-only ones included.
                decision = policy.evaluate(name, args, entry=self.toolbox.entry(name), tainted_by=self.taint,
                                           interactive=self.interactive)
                if decision.action == "deny":
                    policy.audit(decision, name=name, args=args, session_id=self.session_id, agent=self.persona.id, outcome="blocked")
                    self._policy_reasons[call.id] = decision.reason
                    problem = "policy"
                elif decision.action == "ask":
                    self._policy_reasons[call.id] = decision.reason
            prepared.append((call, name, args, problem))

        results: dict[str, tuple[bool, str]] = {}
        parallel = [item for item in prepared if not item[3] and self.toolbox.is_parallel_safe(item[1])]
        sequential = [item for item in prepared if item not in parallel]

        if len(parallel) > 1:
            for call, name, args, _ in parallel:
                self._tool_started(call, name, args, step)
            with ThreadPoolExecutor(max_workers=min(4, len(parallel))) as pool:
                futures = {call.id: pool.submit(self.toolbox.run, name, args) for call, name, args, _ in parallel}
                for call, name, args, _ in parallel:
                    result = futures[call.id].result()
                    results[call.id] = (result.ok, result.output)
                    self._note_source(name, args, result.ok)
                    self._tool_finished(call, name, args, step, result.ok, result.output, result.duration_ms, result.widgets)
        else:
            sequential = prepared

        for call, name, args, problem in sequential:
            if call.id in results:
                continue
            if self.cancel.is_set():
                results[call.id] = (False, "Cancelled by the user before this tool ran.")
                continue
            self._tool_started(call, name, args, step)
            if problem == "policy":
                output = (f"Blocked by EchoSpeak's safety policy: {self._policy_reasons.get(call.id, 'not allowed')}. "
                          "Don't retry it; continue without it or tell the user what was blocked.")
                results[call.id] = (False, output)
                self._tool_finished(call, name, args, step, False, output, 0)
                continue
            if problem == "repeat":
                output = (
                    f"You already called {name} with exactly these arguments {call_counts.get(self._sig(name, args), 3) - 1} times "
                    "and the result is above. Use that result, change the arguments, or answer the user."
                )
                results[call.id] = (False, output)
                self._tool_finished(call, name, args, step, False, output, 0)
                continue
            if problem:
                output = f"Error: {problem}. Call {name} again with a JSON object matching its parameters{self._param_hint(name)}."
                results[call.id] = (False, output)
                self._tool_finished(call, name, args, step, False, output, 0)
                continue
            handoff = self.toolbox.handoff(name)
            if handoff is not None:
                note = str(handoff(args) or "")
                if note.lower().startswith("error"):
                    results[call.id] = (False, note)
                    self._tool_finished(call, name, args, step, False, note, 0)
                    continue
                self._tool_finished(call, name, args, step, True, note, 0)
                self._seal_for_handoff(step)
                result = self.toolbox.run(name, args)
                results[call.id] = (result.ok, result.output)
                # Open the continuation only now, so it sorts after the teammate's reply.
                self._start_message(continues=True, **self.meta)
                continue
            allowed, denial = self._approve(call, name, args, step)
            if not allowed:
                results[call.id] = (False, denial)
                self._tool_finished(call, name, args, step, False, denial, 0)
                continue
            result = self.toolbox.run(name, args)
            results[call.id] = (result.ok, result.output)
            self._tool_finished(call, name, args, step, result.ok, result.output, result.duration_ms, result.widgets)
            self._note_source(name, args, result.ok)

        limit = 14000
        for call, name, _args, _ in prepared:
            ok, output = results.get(call.id, (False, "No result."))
            text = output if len(output) <= limit else output[:limit] + f"\n…[{len(output) - limit} more characters trimmed]"
            if ok and policy.is_untrusted_source(name, self.toolbox.entry(name), _args):
                text = policy.wrap_untrusted(name, text)  # data, not instructions
            messages.append({"role": "tool", "tool_call_id": call.id, "name": name, "content": text or "(empty result)"})

    def _param_hint(self, name: str) -> str:
        """' (required: a, b; optional: c)' so a bad call can be fixed in one try."""
        for schema in self.toolbox.schemas():
            fn = schema.get("function") or {}
            if fn.get("name") != name:
                continue
            params = fn.get("parameters") or {}
            required = list(params.get("required") or [])
            optional = [p for p in (params.get("properties") or {}) if p not in required]
            parts = [f"required: {', '.join(required)}"] if required else []
            if optional:
                parts.append(f"optional: {', '.join(optional)}")
            return f" ({'; '.join(parts)})" if parts else ""
        return ""

    @staticmethod
    def _sig(name: str, args: dict[str, Any]) -> str:
        return hashlib.sha1(f"{name}:{json.dumps(args, sort_keys=True, default=str)}".encode()).hexdigest()

    def _note_source(self, name: str, args: dict[str, Any], ok: bool) -> None:
        if ok and policy.is_untrusted_source(name, self.toolbox.entry(name), args) and name not in self.taint:
            self.taint.append(name)

    def _approve(self, call: Any, name: str, args: dict[str, Any], step: int) -> tuple[bool, str]:
        entry = self.toolbox.entry(name)
        needs, reason = tool_needs_approval(entry, name, args)
        broker = get_approval_broker()
        policy_reason = self._policy_reasons.get(call.id, "")
        if policy_reason:
            # Rule of Two: always ask, even with "always allow" grants or approvals off.
            needs, reason = True, policy_reason
        elif not needs or broker.granted(self.session_id, name):
            return True, ""
        if not self.interactive:
            return False, (
                f"{name} needs the user's approval, which can only be given in the EchoSpeak app. "
                "It was not run. Tell the user to ask for it from the app."
            )
        approval = broker.open(
            session_id=self.session_id,
            request_id=self.request_id,
            tool=name,
            summary=describe_call(name, args),
            args=safe_args_preview(args),
            reason=reason,
        )
        self.timeline.append({"kind": "approval", "step": step, "id": approval.id, "tool_call_id": call.id,
                              "summary": approval.summary, "reason": reason, "decision": "", "at": time.time()})
        self.emit({"type": "approval_request", "step": step, "tool_call_id": call.id, **approval.public()})
        decision = broker.wait(approval, self.cancel)
        if policy_reason:
            policy.audit(policy.Decision("ask", policy_reason, "rule_of_two"), name=name, args=args,
                         session_id=self.session_id, agent=self.persona.id, outcome=decision)
        for item in self.timeline:
            if item.get("kind") == "approval" and item.get("id") == approval.id:
                item["decision"] = decision
        self.emit({"type": "approval_resolved", "id": approval.id, "decision": decision, "tool_call_id": call.id})
        if decision == "allow":
            return True, ""
        if decision == "timeout":
            return False, f"The user did not respond to the approval request for {name}, so it was not run."
        if decision == "cancelled":
            return False, "The user stopped this request."
        return False, f"The user denied permission to run {name}. Do not retry it; continue without it or explain what is blocked."

    def _tool_started(self, call: Any, name: str, args: dict[str, Any], step: int) -> None:
        self._close_thinking()
        label = describe_call(name, args)
        self.timeline.append({"kind": "tool", "step": step, "id": call.id, "name": name, "label": label,
                              "status": "running", "output": "", "at": time.time()})
        self.emit({"type": "tool_start", "step": step, "id": call.id, "name": name, "label": label,
                   "input": json.dumps(safe_args_preview(args, 200), ensure_ascii=False)[:400]})
        if self.persist_tool_runs:
            try:
                from agent.state import get_state_store

                get_state_store().create_tool_run(
                    turn_id=self.execution_id, tool_name=name, session_id=self.session_id,
                    run_id=call.id, canonical_arguments=safe_args_preview(args, 2000),
                )
            except Exception:
                logger.debug("Lean tool run persistence failed", exc_info=True)

    def _tool_finished(self, call: Any, name: str, args: dict[str, Any], step: int, ok: bool, output: str, duration_ms: int,
                       widgets: Optional[list[dict[str, Any]]] = None) -> None:
        preview = (output or "").strip()
        if len(preview) > 1600:
            preview = preview[:1600] + "…"
        cards = list(widgets or []) if ok else []
        if ok:
            self.succeeded.add(name)
        for item in self.timeline:
            if item.get("kind") == "tool" and item.get("id") == call.id:
                item.update({"status": "done" if ok else "failed", "output": preview, "duration_ms": duration_ms})
                if cards:
                    item["widgets"] = cards
        event = {"type": "tool_end", "step": step, "id": call.id, "name": name, "ok": ok,
                 "output": preview, "duration_ms": duration_ms}
        if cards:
            event["widgets"] = cards
        self.emit(event)
        if self.persist_tool_runs:
            try:
                from agent.state import get_state_store

                get_state_store().finish_tool_run(call.id, {
                    "status": "complete" if ok else "failed",
                    "success": ok,
                    "output": (output or "")[:8000],
                    "error_message": "" if ok else (output or "")[:1000],
                    "tool_name": name,
                })
            except Exception:
                logger.debug("Lean tool run persistence failed", exc_info=True)

    # ── context ─────────────────────────────────────────────────────────
    def _fit_context(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], aggressive: bool = False) -> None:
        budget = int(settings.context_tokens() * (0.55 if aggressive else 0.8)) - settings.max_output_tokens() // 2
        # 0. Tool-result clearing: once the context is half full, raw output from
        #    tools used many steps ago is cut to its head. The agent already acted
        #    on it, and long sessions stay coherent instead of hitting the wall.
        if _estimate_tokens(messages, tools) > budget // 2:
            tool_indexes = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
            for index in tool_indexes[:-6]:
                content = str(messages[index].get("content") or "")
                if len(content) > 2000:
                    messages[index]["content"] = content[:1500] + "\n…[older tool output cleared; run the tool again if you need it]"
        if _estimate_tokens(messages, tools) <= budget:
            return
        # 1. Shrink older tool results, keeping the most recent ones intact.
        tool_indexes = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
        keep_recent = 2 if aggressive else 4
        for index in tool_indexes[:-keep_recent] if len(tool_indexes) > keep_recent else []:
            content = str(messages[index].get("content") or "")
            if len(content) > 600:
                messages[index]["content"] = content[:500] + "\n…[older tool output trimmed to save context]"
            if _estimate_tokens(messages, tools) <= budget:
                return
        # 2. Drop the oldest history messages (plain text, never this turn's work).
        #    Older chat is still covered by the chat summary in the system prompt.
        while _estimate_tokens(messages, tools) > budget and self._history_in_messages > 0:
            messages.pop(1)
            self._history_in_messages -= 1
        # 3. Summarize this turn's earlier steps instead of cutting them.
        if _estimate_tokens(messages, tools) > budget and self._compact_turn(messages, keep_steps=2 if aggressive else 3):
            if _estimate_tokens(messages, tools) <= budget:
                return
        # 4. Last resort: shrink every tool result except the newest one.
        if _estimate_tokens(messages, tools) > budget:
            tool_indexes = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
            for index in tool_indexes[:-1]:
                if index < len(messages) and messages[index].get("role") == "tool":
                    content = str(messages[index].get("content") or "")
                    if len(content) > 300:
                        messages[index]["content"] = content[:250] + "\n…[trimmed]"


    def _compact_turn(self, messages: list[dict[str, Any]], *, keep_steps: int) -> bool:
        """Replace this turn's older steps with a model-written summary.

        The summary is appended to the user's message (chat templates such as
        Gemma's require user/assistant turns to alternate), and the last
        `keep_steps` assistant steps stay verbatim. Returns False when there is
        too little to compact or the summary call fails.
        """
        user_index = 1 + self._history_in_messages
        if user_index >= len(messages) or messages[user_index].get("role") != "user":
            return False
        steps = [i for i in range(user_index + 1, len(messages)) if messages[i].get("role") == "assistant"]
        if len(steps) <= keep_steps + 1:
            return False
        cut = steps[-keep_steps]
        span = messages[user_index + 1:cut]
        lines: list[str] = []
        for message in span:
            if message.get("role") == "assistant":
                calls = ", ".join(
                    f"{c.get('function', {}).get('name')}({str(c.get('function', {}).get('arguments') or '')[:160]})"
                    for c in message.get("tool_calls") or []
                )
                text = str(message.get("content") or "").strip()[:400]
                lines.append("You: " + (text + " " if text else "") + (f"[called {calls}]" if calls else ""))
            elif message.get("role") == "tool":
                lines.append(f"Result of {message.get('name') or 'tool'}: {str(message.get('content') or '')[:900]}")
        body = "\n".join(lines)[-14000:]
        prompt = (
            "Summarize the work done so far on this task so you can keep going without the full tool output. "
            "Keep: the goal, what was tried, concrete findings (values, file paths, links, errors), and what is "
            "left to do. At most 200 words, plain sentences.\n\n" + body
        )
        try:
            turn = self.client.stream_turn([{"role": "user", "content": prompt}], temperature=0.2, max_tokens=700, cancel=self.cancel)
            summary = str(turn.content or "").strip()
        except Exception:
            logger.debug("Lean turn compaction failed", exc_info=True)
            return False
        if not summary:
            return False
        messages[user_index] = {
            **messages[user_index],
            "content": str(messages[user_index].get("content") or "")
            + f"\n\n[Progress so far on this task, summarized to fit the context window]:\n{summary}",
        }
        del messages[user_index + 1:cut]
        self.compactions += 1
        self.timeline.append({"kind": "note", "step": 0, "text": "Earlier steps summarized to fit the context window", "at": time.time()})
        self.emit({"type": "context_compacted", "steps": len(steps) - keep_steps})
        return True


def _friendly_error(error: str) -> str:
    low = error.lower()
    if "connect" in low or "connection" in low:
        return "I couldn't reach the model server. Is LM Studio (or your provider) running with a model loaded?"
    if "failed to load model" in low or "model unloaded" in low or "no models loaded" in low:
        return "The model server couldn't load the selected model. Load it in LM Studio and try again."
    if "context" in low and ("length" in low or "window" in low or "n_ctx" in low):
        return "This conversation is larger than the model's context window. Start a new chat or raise the context length."
    if "timeout" in low or "timed out" in low:
        return "The model took too long to respond. Try again, or use a smaller model."
    return f"The model request failed: {error[:300]}"
