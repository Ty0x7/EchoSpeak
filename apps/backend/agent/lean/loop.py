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
from agent.lean import quarantine, tool_outputs, tracing
from agent.lean import approvals, steering
from agent.lean.approvals import get_approval_broker, tool_needs_approval
from agent.lean import ask, outbound
from agent.lean.job import (CHECK_TOOLS, changed_code_file, check_nudge, claim_nudge, is_promise_without_action,
                            promise_nudge, unbacked_claim)
from agent.lean.recall import REMIND_EVERY, reminder
from agent.lean.personas import AgentPersona
from agent.lean.provider import ChatClient, ModelTurn, ProviderError, extract_text_tool_calls
from agent.lean.toolbox import Toolbox, call_target, describe_call, safe_args_preview

Emit = Callable[[dict[str, Any]], None]

NUDGE_EMPTY = (
    "You stopped without replying to the user. Continue now: call a tool if more work is needed, "
    "otherwise write your answer."
)
NUDGE_TRUNCATED = "Your last reply was cut off by the output limit. Continue from where you stopped, concisely."
# Failures in a row of one tool (any arguments) before the agent is told to change approach.
FAIL_STREAK_NOTE = 3


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
    visual = sum(len(m.get("observations", [])) for m in messages)
    return int(size / 3.4) + min(visual, 3) * 1800


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
        goal: str = "",
    ) -> None:
        # What this turn is for, restated every few steps of a long turn (recall.reminder).
        self.goal = goal
        self._outcomes: list[bool] = []
        # Tools that brought outside content into this request (shared by every
        # agent working on it). Drives the Rule-of-Two policy in agent/lean/policy.py.
        self.taint: list[str] = taint if taint is not None else []
        self._policy_reasons: dict[str, str] = {}
        self._policy_rules: dict[str, str] = {}
        # Calls (by signature) the user denied or a policy blocked in this turn: never asked
        # or tried a second time. Persistence after a "no" is a failure mode, not diligence.
        self._refused: dict[str, str] = {}
        # Failures in a row per tool, whatever the arguments: after a few, say so.
        self._fail_streak: dict[str, int] = {}
        # Approvals autonomous mode held for the owner instead of waiting ({tool, summary, reason}).
        self.held: list[dict[str, str]] = []
        self.interactive = interactive
        # Re-prompt an agent whose reply only promises work (see agent/lean/job.py).
        self.promise_guard = promise_guard
        self.completed_summary: Optional[str] = None
        # Tools that worked in this turn: what a reply's "I've saved it" must be backed by.
        self.succeeded: set[str] = set()
        # Code files changed since the last terminal run (the check step before 'done').
        self._unchecked: dict[str, bool] = {}
        self._on_seal = on_seal
        self._handed_off = False
        self.compactions = 0
        # Extra fields on agent_start, e.g. who handed this work over.
        self.meta = dict(meta or {})
        self.client = client
        self.persona = persona
        self.system_prompt = system_prompt
        self.history = history
        if any(row.get("observations") for row in history) and "read_image" not in self.taint:
            self.taint.append("read_image")
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
        self.usage = {"prompt": 0, "completion": 0, "total": 0, "fresh": 0}
        self._last_prompt = 0
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
        if self.meta.get("model_route"):
            # Smart model choice explains itself under the reply (agent/learning/routing.py).
            self.timeline.append({"kind": "note", "step": 0, "text": str(self.meta["model_route"]), "at": time.time()})
        self._start_message(**self.meta)

    def run(self, user_message: str, *, announce: bool = True) -> TurnResult:
        """One agent turn, traced end to end (agent/lean/tracing.py)."""
        self.trace = tracing.Trace(self.session_id, self.persona.id, self.request_id)
        result: Optional[TurnResult] = None
        try:
            result = self._run(user_message, announce=announce)
            return result
        finally:
            self.trace.finish(self.message_id, ok=bool(result and result.success),
                              stop=str(getattr(result, "stop_reason", "") or ("error" if result is None else "")),
                              prompt_tokens=int(self.usage.get("prompt", 0)), completion_tokens=int(self.usage.get("completion", 0)))

    def _run(self, user_message: str, *, announce: bool = True) -> TurnResult:
        if announce:
            self.announce()
        tools = self.toolbox.schemas()
        tools_version = getattr(self.toolbox, "schema_version", 0)
        messages: list[dict[str, Any]] = [{"role": "system", "content": self.system_prompt}]
        messages.extend(self.history)
        self._history_in_messages = len(self.history)
        messages.append({"role": "user", "content": user_message})
        self._goal = user_message  # what the quarantined reader extracts facts for

        # A research worker gets its own, smaller step budget (agent/lean/swarm.py).
        max_steps = int(getattr(self, "max_steps_override", 0) or 0) or settings.max_iterations()
        call_counts: dict[str, int] = {}
        stop_reason = ""
        nudges = 0
        promise_nudges = 0
        claim_nudges = 0
        check_nudges = 0
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
            # Safe checkpoint: tool results are in, the next model call hasn't started.
            self._absorb_steering(messages, step)
            if getattr(self.toolbox, "schema_version", 0) != tools_version:
                # find_tools loaded connected-app tools in the last step.
                tools_version = self.toolbox.schema_version
                tools = self.toolbox.schemas()
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
            if turn.provider_blocks:
                assistant["provider_blocks"] = turn.provider_blocks
            if calls:
                assistant["tool_calls"] = [
                    {"id": call.id, "type": "function", "function": {"name": call.name, "arguments": call.arguments or "{}"}, **({"extra_content": call.extra_content} if call.extra_content else {})}
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
                    if (self.promise_guard and not stop_reason and self._unchecked and check_nudges < 1
                            and any(name in self.toolbox.names for name in CHECK_TOOLS)):
                        # Code changed and nothing ran since: check it before calling it done (once).
                        check_nudges += 1
                        messages.append({"role": "user", "content": check_nudge(list(self._unchecked))})
                        self.emit({"type": "check_nudge", "step": step, "files": list(self._unchecked)[:5]})
                        self._retract_text(step)
                        continue
                    if self._absorb_steering(messages, step):
                        # The user added something while this answer was written: keep going with it.
                        self._retract_text(step)
                        continue
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
            if self.goal and step % REMIND_EVERY == 0 and messages[-1].get("role") == "tool":
                # Appended to the tool result, not sent as its own message: some chat
                # templates (Gemma) require strict user/assistant alternation.
                messages[-1]["content"] = (str(messages[-1].get("content") or "") + "\n\n"
                                           + reminder(self.goal, step, self._outcomes[-REMIND_EVERY:]))
                self.emit({"type": "goal_reminder", "step": step})
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
            # Approvals autonomous mode held for the owner: the actionable exceptions of a walk-away run.
            "held": list(self.held),
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
        self.client.on_audio = lambda packet: self.emit({"type": "voice_audio", **packet})
        def on_reasoning(text: str) -> None:
            self._append_text("thinking", step, text)
            self.emit({"type": "reasoning_delta", "step": step, "text": text})

        def on_content(text: str) -> None:
            self._append_text("text", step, text)
            self.emit({"type": "agent_token", "step": step, "data": text})

        endpoint = getattr(self.client, "endpoint", None)
        with self._span("model.call", step=step, model=str(getattr(endpoint, "model", "") or ""),
                        provider=str(getattr(endpoint, "provider", "") or ""), tools=len(tools or [])) as span:
            turn = self.client.stream_turn(
                messages,
                tools=tools or None,
                on_reasoning=on_reasoning,
                on_content=on_content,
                cancel=self.cancel,
                temperature=self.temperature,
            )
            span.set(tool_calls=len(turn.tool_calls), finish=str(turn.finish_reason or ""),
                     prompt_tokens=int((turn.usage or {}).get("prompt") or 0),
                     completion_tokens=int((turn.usage or {}).get("completion") or 0))
        if turn.usage:
            self._account_usage(turn.usage)
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

    def _account_usage(self, usage: dict[str, Any]) -> None:
        """Add one step's token counts to the turn's.

        "fresh" is what the step added. Every step re-sends the whole conversation, so
        summing prompt tokens counts it again each time: a 20-step build with a 10k
        context showed ~200k "used" after writing a few files. Fresh counts only the new
        input plus the reply; group jobs budget by it (agent/lean/job.py).
        """
        for key in ("prompt", "completion", "total"):
            self.usage[key] += int(usage.get(key) or 0)
        if "prompt" in usage or "completion" in usage:
            prompt = int(usage.get("prompt") or 0)
            grown = prompt - self._last_prompt if self._last_prompt and prompt >= self._last_prompt else prompt
            self._last_prompt = prompt
            fresh = max(0, grown) + int(usage.get("completion") or 0)
        else:  # a provider that reports only a total
            fresh = int(usage.get("total") or 0)
        self.usage["fresh"] = self.usage.get("fresh", 0) + fresh

    # ── tools ───────────────────────────────────────────────────────────
    def _run_tools(self, calls: list[Any], messages: list[dict[str, Any]], step: int, call_counts: dict[str, int]) -> None:
        prepared: list[tuple[Any, str, dict[str, Any], str]] = []
        # Screens change, so the same screenshot or key press can rightly repeat.
        computer_names = policy.NATIVE_COMPUTER_ACT | policy.NATIVE_COMPUTER_LOOK
        for call in calls:
            args, arg_error = call.parsed_arguments()
            try:
                name, args = self.toolbox.normalize_call(call.name, args)
            except (ValueError, TypeError) as exc:
                name, arg_error = call.name, f"Invalid tool arguments: {exc}"
            signature = self._sig(name, args)
            call_counts[signature] = call_counts.get(signature, 0) + 1
            problem = arg_error or ("" if call_counts[signature] < 3 or name in {"creation_status", "take_screenshot", "vision_qa", "computer_action"} | computer_names else "repeat")
            if not problem and signature in self._refused:
                problem = "refused"
            if not problem:
                # Checked for every call, parallel read-only ones included.
                decision = policy.evaluate(name, args, entry=self.toolbox.entry(name), tainted_by=self.taint,
                                           interactive=self.interactive)
                if decision.action != "allow" and getattr(self, "trace", None) is not None:
                    self.trace.event("policy.decision", step=step, tool=name, action=decision.action, rule=decision.rule)
                if decision.action == "deny":
                    policy.audit(decision, name=name, args=args, session_id=self.session_id, agent=self.persona.id, outcome="blocked")
                    self._policy_reasons[call.id] = decision.reason
                    problem = "policy"
                elif decision.action == "ask":
                    self._policy_reasons[call.id] = decision.reason
                    self._policy_rules[call.id] = decision.rule
            prepared.append((call, name, args, problem))

        results: dict[str, tuple[bool, str]] = {}
        visual_results: dict[str, list[dict]] = {}
        parallel = [item for item in prepared if not item[3] and self.toolbox.is_parallel_safe(item[1])]
        sequential = [item for item in prepared if item not in parallel]

        if len(parallel) > 1:
            for call, name, args, _ in parallel:
                self._tool_started(call, name, args, step)
            with ThreadPoolExecutor(max_workers=min(4, len(parallel))) as pool:
                futures = {call.id: pool.submit(self._run_tool, call, name, args, step) for call, name, args, _ in parallel}
                for call, name, args, _ in parallel:
                    result = futures[call.id].result()
                    results[call.id] = (result.ok, result.output)
                    visual_results[call.id] = result.observations if result.ok else []
                    self._note_source(name, args, result.ok)
                    self._tool_finished(call, name, args, step, result.ok, result.output, result.duration_ms, result.widgets,
                                        meta=result.meta)
        else:
            sequential = prepared

        computer = {"computer_action"} | policy.NATIVE_COMPUTER_ACT | policy.NATIVE_COMPUTER_LOOK
        computer_calls: list[str] = []
        for call, name, args, problem in sequential:
            if call.id in results:
                continue
            if self.cancel.is_set():
                results[call.id] = (False, "Cancelled by the user before this tool ran.")
                continue
            failed_before = any(not results.get(cid, (True, ""))[0] for cid in computer_calls)
            if name in computer:
                computer_calls.append(call.id)
            if failed_before and name in computer:
                # The rest of a batch planned against a screen that didn't behave as expected.
                output = "Not executed: an earlier computer action in this turn failed."
                results[call.id] = (False, output)
                self._tool_finished(call, name, args, step, False, output, 0)
                continue
            self._tool_started(call, name, args, step)
            if problem == "policy":
                output = (f"Blocked by EchoSpeak's safety policy: {self._policy_reasons.get(call.id, 'not allowed')}. "
                          "Don't retry it; continue without it or tell the user what was blocked.")
                self._refused[self._sig(name, args)] = "blocked by EchoSpeak's safety policy"
                results[call.id] = (False, output)
                self._tool_finished(call, name, args, step, False, output, 0)
                continue
            if problem == "refused":
                output = (f"Not run: this exact {name} call was already {self._refused[self._sig(name, args)]} "
                          "earlier in this request. Don't ask again or work around it; continue without it or "
                          "tell the user.")
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
            if name == ask.ASK_USER:
                ok, output = self._ask_user(call, args, step)
                results[call.id] = (ok, output)
                self._tool_finished(call, name, args, step, ok, output, 0)
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
                result = self._run_tool(call, name, args, step)
                results[call.id] = (result.ok, result.output)
                # Open the continuation only now, so it sorts after the teammate's reply.
                self._start_message(continues=True, **self.meta)
                continue
            allowed, denial = self._approve(call, name, args, step)
            if not allowed:
                if denial.startswith("The user denied"):
                    self._refused[self._sig(name, args)] = "denied by the user"
                elif denial.startswith("Not run:") and "autonomous mode" in denial:
                    self._refused[self._sig(name, args)] = "held for the owner's approval"
                results[call.id] = (False, denial)
                self._tool_finished(call, name, args, step, False, denial, 0)
                continue
            if outbound.is_outbound(name, self.toolbox.entry(name)):
                # Per-channel send budget (agent/lean/outbound.py): no spamming the user's contacts.
                limited = outbound.take(outbound.channel_of(name))
                if limited:
                    results[call.id] = (False, limited)
                    self._tool_finished(call, name, args, step, False, limited, 0)
                    continue
            result = self._run_tool(call, name, args, step)
            results[call.id] = (result.ok, result.output)
            visual_results[call.id] = result.observations if result.ok else []
            self._tool_finished(call, name, args, step, result.ok, result.output, result.duration_ms, result.widgets,
                                meta=result.meta)
            self._note_source(name, args, result.ok)

        limit = 14000
        reader = quarantine.mode()
        for call, name, _args, _ in prepared:
            ok, output = results.get(call.id, (False, "No result."))
            untrusted = ok and policy.is_untrusted_source(name, self.toolbox.entry(name), _args)
            if untrusted and quarantine.applies(name, self.toolbox.entry(name), reader):
                # The acting agent gets a tool-less reader's digest, not the raw text. Still outside content.
                with self._span("reader", step=step, tool=name, input_chars=len(output)) as span:
                    read = quarantine.read(self.client, getattr(self, "_goal", ""), name, output, self.cancel)
                    span.set(used=bool(read), instructions_found=bool(read and read[1]))
                if read:
                    output = quarantine.digest_text(name, read[0], read[1], len(output))
                    if read[1]:
                        note = f"{name} contained instructions aimed at an AI. Echo's reader left them out."
                        self.timeline.append({"kind": "note", "step": step, "text": note, "at": time.time()})
                        self.emit({"type": "security_note", "step": step, "tool": name, "note": note})
                        policy.audit(policy.Decision("allow", note, "quarantine_flag"), name=name, args=_args,
                                     session_id=self.session_id, agent=self.persona.id, outcome="instructions withheld")
            text = self._shorten(name, output, limit, untrusted)
            if untrusted:
                text = policy.wrap_untrusted(name, text)  # data, not instructions
            streak = 0 if ok else self._fail_streak.get(name, 0) + 1
            self._fail_streak[name] = streak
            if streak and streak % FAIL_STREAK_NOTE == 0:
                # Loop detection beyond identical calls: variations of a failing call rarely recover.
                text += (f"\n\n[EchoSpeak: {name} has failed {streak} times in a row in this request. Stop trying "
                         "variations of it. Read the errors above, then take a different approach or tell the user "
                         "what is blocking you.]")
                self.emit({"type": "fail_streak", "step": step, "tool": name, "count": streak})
            messages.append({"role": "tool", "tool_call_id": call.id, "name": name, "content": text or "(empty result)"})
            if visual_results.get(call.id):
                messages[-1]["observations"] = visual_results[call.id]

    def _shorten(self, name: str, output: str, limit: int, untrusted: bool) -> str:
        """Fit one result into the context. A longer one is saved whole behind an out-… handle."""
        if len(output) <= limit:
            return output
        if "read_output" in self.toolbox.names:
            try:
                ref = tool_outputs.save(self.session_id, name, output, untrusted=untrusted)
                return tool_outputs.preview(output, ref, limit)
            except OSError:
                logger.warning("Could not save a long tool output; trimming it instead")
        return output[:limit] + f"\n…[{len(output) - limit} more characters trimmed]"

    def _clear_note(self, message: dict[str, Any], keep: int, note: str) -> str:
        """Cut an old result to its head; when it can be saved, say how to reopen it."""
        content = str(message.get("content") or "")
        if "read_output" in self.toolbox.names and "is saved as out-" not in content:
            try:
                ref = tool_outputs.save(self.session_id, str(message.get("name") or "tool"), content,
                                        untrusted="<untrusted-content" in content)
                return content[:keep] + f"\n…[{note}; the full result is saved as {ref}, read it with read_output]"
            except OSError:
                pass
        return content[:keep] + f"\n…[{note}]"

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

    def _absorb_steering(self, messages: list[dict[str, Any]], step: int) -> bool:
        """Add instructions the user sent during this run (agent/lean/steering.py). True when any arrived."""
        rows = steering.take(self.session_id)
        if not rows:
            return False
        text = steering.as_user_text(rows)
        last = messages[-1] if messages else {}
        if last.get("role") in {"tool", "user"}:
            # Appended, not a new message: some chat templates (Gemma) need strict alternation.
            last["content"] = f"{last.get('content') or ''}\n\n{text}"
        else:
            messages.append({"role": "user", "content": text})
        for row in rows:
            self.timeline.append({"kind": "steer", "step": step, "text": row["text"], "at": time.time()})
            self.emit({"type": "steer_applied", "step": step, "id": row["id"], "text": row["text"][:2000]})
        if self.goal:
            self.goal = f"{self.goal}\n(The user added: {' / '.join(r['text'] for r in rows)[:600]})"
        return True

    def _auto_note(self, step: int, text: str) -> None:
        """A visible line in the message for an approval that didn't need the owner this time."""
        self.timeline.append({"kind": "note", "step": step, "text": text, "at": time.time()})
        self.emit({"type": "auto_approved", "step": step, "note": text})

    def _approve(self, call: Any, name: str, args: dict[str, Any], step: int) -> tuple[bool, str]:
        entry = self.toolbox.entry(name)
        needs, reason = tool_needs_approval(entry, name, args)
        broker = get_approval_broker()
        policy_reason = self._policy_reasons.get(call.id, "")
        # Only screen-guided desktop control can be allowed for a whole chat; other outside content asks every time.
        grantable = not policy_reason or self._policy_rules.get(call.id) == "screen_control"
        if policy_reason and grantable and broker.granted(self.session_id, policy.grant_name(name)):
            policy.audit(policy.Decision("allow", policy_reason, "screen_control"), name=name, args=args,
                         session_id=self.session_id, agent=self.persona.id, outcome="allowed for this chat")
            return True, ""
        if policy_reason:
            # Rule of Two: always ask, even with "always allow" grants or approvals off.
            needs, reason = True, policy_reason
        elif not needs or broker.granted(self.session_id, policy.grant_name(name)):
            return True, ""
        scope = approvals.scope_for(name, args, reason, self._policy_rules.get(call.id, ""))
        if scope is not None and approvals.get_grant_store().has(scope.key):
            # The owner chose "Always allow" for this class of action.
            policy.audit(policy.Decision("allow", reason, "always_allowed"), name=name, args=args,
                         session_id=self.session_id, agent=self.persona.id, outcome=f"always allowed: {scope.key}")
            self._auto_note(step, f"Allowed by your Always allow: {scope.label}")
            return True, ""
        if settings.approval_mode() == "autonomous" and self.interactive:
            # Only chats in the app: Discord, routines and other sources keep refusing below.
            if scope is not None and scope.autonomous:
                policy.audit(policy.Decision("allow", reason, "autonomous"), name=name, args=args,
                             session_id=self.session_id, agent=self.persona.id, outcome=f"autonomous: {scope.key}")
                self._auto_note(step, f"Allowed automatically (autonomous mode): {scope.label}")
                return True, ""
            # Not something autonomy may approve: held for the owner instead of stalling the run.
            summary = describe_call(name, args)
            self.held.append({"tool": name, "summary": summary, "reason": reason})
            self.timeline.append({"kind": "approval", "step": step, "id": f"held_{len(self.held)}", "tool_call_id": call.id,
                                  "summary": summary, "reason": reason, "decision": "held", "at": time.time()})
            self.emit({"type": "approval_held", "step": step, "tool_call_id": call.id, "tool": name, "summary": summary,
                       "reason": reason})
            policy.audit(policy.Decision("ask", reason, "autonomous_held"), name=name, args=args,
                         session_id=self.session_id, agent=self.persona.id, outcome="held for the owner")
            return False, (f"Not run: {name} needs the owner's approval ({reason}), and autonomous mode doesn't approve "
                           "this kind of action. Don't retry it or work around it. Continue with the rest of the task, "
                           "and at the end tell the user exactly what is waiting for their approval.")
        if not self.interactive:
            return False, (
                f"{name} needs the user's approval, which can only be given in the EchoSpeak app. "
                "It was not run. Tell the user to ask for it from the app."
            )
        # The approval card promises exact arguments. Refuse calls too large to
        # present intact instead of approving a truncated command or hidden key.
        if len(json.dumps(args, ensure_ascii=False, default=str).encode("utf-8")) > 65536:
            return False, f"{name} has more than 64 KiB of arguments and cannot be shown safely for approval."
        approval = broker.open(
            session_id=self.session_id,
            request_id=self.request_id,
            tool=policy.grant_name(name),  # one grant covers every computer action
            summary=describe_call(name, args),
            # The card shows these in full: a summary can hide "; iwr … | iex" after padding.
            args=args,
            reason=reason,
            grantable=grantable,
            scope=scope,
        )
        self.timeline.append({"kind": "approval", "step": step, "id": approval.id, "tool_call_id": call.id,
                              "summary": approval.summary, "reason": reason, "decision": "", "at": time.time()})
        self.emit({"type": "approval_request", "step": step, "tool_call_id": call.id, **approval.public()})
        with self._span("approval.wait", step=step, tool=name, rule=self._policy_rules.get(call.id, "") or "approval") as span:
            decision = broker.wait(approval, self.cancel)
            span.set(decision=decision)
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

    def _ask_user(self, call: Any, args: dict[str, Any], step: int) -> tuple[bool, str]:
        """Show a question card and wait for the user's answer (agent/lean/ask.py)."""
        question, options, allow_other, problem = ask.parse(args)
        if problem:
            return False, problem
        if not self.interactive:
            return False, ("A question card can't be shown here. Ask the user in plain text, list the options, "
                           "and end your reply so they can answer.")
        broker = get_approval_broker()
        pending = broker.open_question(session_id=self.session_id, request_id=self.request_id,
                                       question=question, options=options, allow_other=allow_other)
        self.timeline.append({"kind": "approval", "step": step, "id": pending.id, "tool_call_id": call.id,
                              "tool": ask.ASK_USER, "summary": question, "question": question, "options": options,
                              "allow_other": allow_other, "reason": "", "decision": "", "answer": "", "at": time.time()})
        self.emit({"type": "approval_request", "step": step, "tool_call_id": call.id, **pending.public()})
        decision = broker.wait(pending, self.cancel)
        for item in self.timeline:
            if item.get("kind") == "approval" and item.get("id") == pending.id:
                item["decision"] = decision
                item["answer"] = pending.answer
        self.emit({"type": "approval_resolved", "id": pending.id, "decision": decision, "answer": pending.answer,
                   "tool_call_id": call.id})
        if decision == "answered":
            return True, f"The user answered: {pending.answer}"
        if decision == "cancelled":
            return False, "The user stopped this request."
        return False, ("The user didn't answer. Don't ask again right away: go ahead with the safest option, "
                       "or end your reply saying what you need from them.")

    def _run_tool(self, call: Any, name: str, args: dict[str, Any], step: int):
        """Run one tool with a live progress line: what it reports shows under its row (agent/lean/progress.py)."""
        from agent.lean import progress

        def emit(text: str) -> None:
            for item in self.timeline:
                if item.get("kind") == "tool" and item.get("id") == call.id:
                    item["detail"] = text
            self.emit({"type": "tool_progress", "step": step, "id": call.id, "text": text})

        def emit_state(state: dict) -> None:
            # A live card under the tool's row (research workers); kept in the timeline for reloads.
            for item in self.timeline:
                if item.get("kind") == "tool" and item.get("id") == call.id:
                    item["state"] = state
            self.emit({"type": "tool_state", "step": step, "id": call.id, "state": state})

        with self._span("tool.run", step=step, tool=name) as span, \
                progress.reporting(emit, cancel=self.cancel, emit_state=emit_state, spans=self._span):
            result = self.toolbox.run(name, args)
            span.set(ok=bool(result.ok), output_chars=len(result.output or ""))
            if not result.ok:
                span.status = "error"
            return result

    def _span(self, name: str, **attrs: Any):
        """A trace span when this turn is traced (run() sets one up); a no-op otherwise."""
        trace = getattr(self, "trace", None)
        return trace.span(name, **attrs) if trace is not None else tracing.null_span()

    def _tool_started(self, call: Any, name: str, args: dict[str, Any], step: int) -> None:
        self._close_thinking()
        label = describe_call(name, args)
        item = {"kind": "tool", "step": step, "id": call.id, "name": name, "label": label,
                "status": "running", "output": "", "at": time.time()}
        target = call_target(args)
        if target:
            item["target"] = target
        # A short, redacted copy of the arguments, so a saved chat can become a replay case (agent/lean/replays.py).
        item["args"] = policy.redact_payload(safe_args_preview(args, 200))
        self.timeline.append(item)
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
                       widgets: Optional[list[dict[str, Any]]] = None, meta: Optional[dict[str, Any]] = None) -> None:
        from agent.lean import steps

        facts = {**steps.widget_counts(widgets), **(meta or {})}
        if name in CHECK_TOOLS:
            self._unchecked.clear()  # something ran after the changes, pass or fail
        elif ok:
            changed = changed_code_file(name, args)
            if changed:
                self._unchecked[changed] = True
        label = describe_call(name, args)
        done = steps.done_label(label) if ok else label
        summary = steps.summarize(name, args, ok, output, facts)
        preview = (output or "").strip()
        if len(preview) > 1600:
            preview = preview[:1600] + "…"
        cards = list(widgets or []) if ok else []
        self._outcomes.append(bool(ok))
        if ok:
            self.succeeded.add(name)
        for item in self.timeline:
            if item.get("kind") == "tool" and item.get("id") == call.id:
                item.update({"status": "done" if ok else "failed", "output": preview, "duration_ms": duration_ms,
                             "done_label": done, "summary": summary, "meta": facts})
                item.pop("detail", None)
                if cards:
                    item["widgets"] = cards
        event = {"type": "tool_end", "step": step, "id": call.id, "name": name, "ok": ok,
                 "output": preview, "duration_ms": duration_ms, "done_label": done, "summary": summary, "meta": facts}
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
                    messages[index]["content"] = self._clear_note(messages[index], 1500, "older tool output cleared")
        if _estimate_tokens(messages, tools) <= budget:
            return
        # 1. Shrink older tool results, keeping the most recent ones intact.
        tool_indexes = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
        keep_recent = 2 if aggressive else 4
        for index in tool_indexes[:-keep_recent] if len(tool_indexes) > keep_recent else []:
            content = str(messages[index].get("content") or "")
            if len(content) > 600:
                messages[index]["content"] = self._clear_note(messages[index], 500, "older tool output trimmed to save context")
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
            with self._span("context.compact", steps=len(steps) - keep_steps, input_chars=len(body)):
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
