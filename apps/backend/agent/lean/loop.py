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
import shlex
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from loguru import logger

from agent.lean import policy, settings
from agent.lean import quarantine, tool_outputs, tool_prompting, tracing, window
from agent.lean import approvals, steering
from agent.lean.approvals import get_approval_broker, tool_needs_approval
from agent.lean import ask, evidence, outbound
from agent.lean.job import (CHECK_TOOLS, changed_code_file, check_nudge, claim_nudge, file_claim_note,
                            is_promise_without_action, promise_nudge, unbacked_claim, unbacked_file_claim,
                            unbacked_screen_claim, written_names)
from agent.lean.recall import REMIND_EVERY, reminder
from agent.lean.personas import AgentPersona
from agent.lean.provider import ChatClient, ModelTurn, ProviderError, ToolCall, extract_text_tool_calls
from agent.lean.toolbox import Toolbox, ToolResult, call_target, describe_call, safe_args_preview

Emit = Callable[[dict[str, Any]], None]

NUDGE_EMPTY = (
    "You stopped without replying to the user. Continue now: call a tool if more work is needed, "
    "otherwise write your answer."
)
NUDGE_TRUNCATED = "Your last reply was cut off by the output limit. Continue from where you stopped, concisely."
# Output limit reached mid-answer: the same answer continues (thinking off), it isn't rewritten.
NUDGE_CONTINUE = ("Your answer was cut off by the output limit. Continue it exactly where it stopped, without repeating "
                  "anything already written and without starting over.")
# All of the output went to reasoning: answer from it instead of reasoning again from scratch.
NUDGE_REASONING_SPENT = ("Your reasoning used the whole output budget before you wrote the answer. Write the answer now, "
                         "directly and concisely, from what you already worked out. Don't deliberate again.")
# Reasoning, then an empty reply that stopped normally: the answer was written as reasoning. Answer it, thinking off.
NUDGE_THOUGHT_ONLY = ("You worked out your reply in your reasoning but didn't write it for the user. Write the reply "
                      "now, from what you already worked out; don't mention this note.")
# The stream ended without a finish (or with an error inside it). Seen live: Ollama's qwen3.5 parser rejected a
# malformed tool call ("XML syntax error … <function> closed by </parameter>") and still answered HTTP 200 with
# no tool call, no finish_reason and no usage. Nothing ran; saying "you wrote it in your reasoning" (the
# thought-only note) led the model to claim the edit was done.
NUDGE_INCOMPLETE = ("Your last reply didn't come through complete: if it contained a tool call, the server couldn't "
                    "read it, so nothing was run. Do the next step again; if it needs a tool, call it with valid "
                    "arguments. Don't say anything was done that wasn't run.")
NUDGE_WINDOW_SPENT = ("Your reasoning filled the model's context window before you wrote the answer. Write the answer "
                      "now, directly and concisely, from what you already worked out. Don't deliberate again.")
CONTINUATIONS = 2


def empty_cause(turn: Any, window: int) -> str:
    """Why a reply has no text and no tool calls, from the server's own evidence.

    incomplete_stream: the stream ended with no finish reason, or carried an error (the provider says so;
        adapters that don't track this never produce it).
    context_window: stopped for length with prompt + reply filling the served window (not the output limit).
    output_limit: stopped for length below the window, i.e. the max_tokens sent.
    reasoning_only: no text after reasoning (stopped normally, or an adapter that reports no finish).
    empty: nothing at all.
    """
    finish = str(getattr(turn, "finish_reason", "") or "")
    if getattr(turn, "incomplete", False) or getattr(turn, "stream_error", ""):
        return "incomplete_stream"
    if finish == "length":
        usage = getattr(turn, "usage", None) or {}
        used = int(usage.get("total") or 0) or int(usage.get("prompt") or 0) + int(usage.get("completion") or 0)
        return "context_window" if window and used >= window - 64 else "output_limit"
    return "reasoning_only" if str(getattr(turn, "reasoning", "") or "").strip() else "empty"
# Failed summaries in one turn before compaction stops being tried (the loop trims instead).
COMPACT_FAILURES = 2


def _redacted_card(card: dict[str, Any]) -> dict[str, Any]:
    """An approval card as emitted (and journaled): configured secrets in its arguments are masked (C5)."""
    return {**card, "args": policy.redact_payload(card.get("args"))} if card.get("args") else card


# An EchoSpeak note written by the model itself, in its own reply (open tag through close, or to the end).
_FORGED_NOTE = re.compile(r"<echospeak-note>.*?(?:</echospeak-note>|$)", re.S | re.I)


class _RetryStep(Exception):
    """The reply can't be used and the step should be asked again (the cause is fixed for the next call)."""


def exit_code_known(output: str) -> bool:
    """The terminal reported how the command ended (agent/lean/terminal.py result header)."""
    from agent.lean.verification import exit_code
    return exit_code(output) is not None


def app_note(text: str) -> str:
    """An instruction from EchoSpeak inside the conversation, marked so the model never takes it for the user.

    Corrections sent as plain user messages ("You wrote… but…") read as the user criticising the
    model, which then apologised for things the user never said (live test, 11.11.3).
    """
    return f"<echospeak-note>{text}</echospeak-note>"


def add_note(messages: list[dict[str, Any]], text: str) -> None:
    """Add an app note without breaking user/assistant alternation (Gemma-style templates require it)."""
    note = app_note(text)
    last = messages[-1] if messages else {}
    if last.get("role") in {"user", "tool"}:
        last["content"] = f"{last.get('content') or ''}\n\n{note}"
    else:
        messages.append({"role": "user", "content": note})
# Failures in a row of one tool (any arguments) before the agent is told to change approach.
FAIL_STREAK_NOTE = 3
# Refused or blocked actions (denied, policy, privacy, switched off, held): a firm note after
# this many in a row, and the turn hands back to the user after this many in total. Persisting
# after a "no" is a known failure mode; Claude Code (3 in a row / 20) and Codex auto-review
# (3 in a row / 10 of 50) both stop and return control.
REFUSAL_STREAK_NOTE = 3
REFUSAL_STOP = 10
# Screen work that isn't moving. A 4B model on Minecraft's menu took a screenshot seven times
# in ten minutes and never clicked: every look "worked", and the animated menu made each image
# slightly different, so neither the repeat check nor the fail streak ever fired.
SCREEN_LOOK = {"take_screenshot", "vision_qa", "read_image", "analyze_screen"} | policy.NATIVE_COMPUTER_LOOK
LOOK_STREAK_NOTE = 3  # looks in a row with no action between them
NO_EFFECT_STREAK_NOTE = 2  # screen actions in a row that failed or changed nothing visible
_REFUSAL_MARKERS = (
    "blocked by echospeak's safety policy", "blocked by the user's privacy setting", "the user denied permission",
    "not run:", "is not available right now", "needs the user's approval, which can only be given",
    "the user did not respond to the approval", "is in blocked apps",
)


def is_refusal(output: str) -> bool:
    """The action was refused or blocked by a person, a setting or the policy (not a tool error)."""
    low = str(output or "").strip().lower()
    return any(marker in low[:400] for marker in _REFUSAL_MARKERS)


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
    # The verification ledger's completion record (agent/lean/verification.py), when checks ran.
    verification: dict[str, Any] = field(default_factory=dict)


def _estimate_tokens(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> int:
    size = len(json.dumps(tools, ensure_ascii=False)) if tools else 0
    for message in messages:
        size += len(str(message.get("content") or "")) + 16
        for call in message.get("tool_calls") or []:
            size += len(str(call.get("function", {}).get("arguments") or "")) + 32
        # Encrypted reasoning is not visible text, but still consumes context.
        size += len(json.dumps(message.get("provider_state") or {}))
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
        from agent.coordination import current
        self.work_context = current()
        self.ensure_work = None
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
        # Existing files and folders named by an action the owner refused (or a policy blocked, or autonomous
        # mode held) in this turn: later changes to them ask again, whatever tool they come through.
        self._denied_targets: set[str] = set()
        # Failures in a row per tool, whatever the arguments: after a few, say so.
        self._fail_streak: dict[str, int] = {}
        # Approvals autonomous mode held for the owner instead of waiting ({tool, summary, reason}).
        self.held: list[dict[str, str]] = []
        # Refused or blocked actions: in a row (reset by any action that ran) and in total.
        self._refusal_streak = 0
        self._refusals = 0
        self.interactive = interactive
        # Re-prompt an agent whose reply only promises work (see agent/lean/job.py).
        self.promise_guard = promise_guard
        self.completed_summary: Optional[str] = None
        # Tools that worked in this turn: what a reply's "I've saved it" must be backed by.
        self.succeeded: set[str] = set()
        # Names of files successful tools wrote (or commands named): what "I've written mics.md" must match.
        self._written: set[str] = set()
        # Each call's outcome (agent/lean/evidence.py), and how much the screen changed after the latest action.
        self._outcomes_by_call: dict[str, str] = {}
        self._screen_changed: Optional[float] = None
        # Screen progress: looks since the last action, and actions in a row that moved nothing.
        self._look_streak = 0
        self._no_effect_streak = 0
        # Code files changed since the last terminal run (the check step before 'done').
        self._unchecked: dict[str, bool] = {}
        self._on_seal = on_seal
        self._handed_off = False
        self.compactions = 0
        self._compact_failures = 0
        self._empty_cause = ""  # why the last reply was empty (see empty_cause)
        # Extra fields on agent_start, e.g. who handed this work over.
        self.meta = dict(meta or {})
        self.client = client
        # Set by the session for environments with their own boundary (Chat); returns why a call is refused.
        self.call_guard: Optional[Callable[[str, dict[str, Any], Any], str]] = None
        # Environment hooks (Chat: agent/lean/chat_run.py). tool_observer sees every finished call
        # (name, args, ok, output, step, duration_ms, meta); final_check may ask for one correction
        # before an answer is accepted (an output guardrail); grace_prompt asks for the best answer
        # when the step budget runs out.
        self.tool_observer: Optional[Callable[..., None]] = None
        # Reviews a finished answer without regenerating it: returns (notes to show under it,
        # replacement text or ""). Chat uses it for citation and figure checks.
        self.answer_review: Optional[Callable[[str], tuple[list[str], str]]] = None
        # How many times an untrue "I did it" claim is sent back for a rewrite before it's just noted.
        self.claim_nudge_limit = 1
        self._no_think_next = False
        # Calls to run before the model's first turn, through the normal tool path ([(name, args)]):
        # Chat reads addresses the user gave without waiting for a small model to notice them.
        self.prefetch: Optional[Callable[[], list[tuple[str, dict[str, Any]]]]] = None
        self.final_check: Optional[Callable[[str], str]] = None
        # Measures engineering work against its objective (agent/lean/verification.py): sees every finished
        # call, adds structured feedback to check results, and states completion under the answer.
        self.verifier: Optional[Any] = None
        self.grace_prompt = ""
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

    def _reasoning_tail(self, step: int, limit: int = 1200) -> str:
        """The end of this step's reasoning, for answering from it instead of reasoning again."""
        text = " ".join(item.get("text", "") for item in self.timeline if item.get("kind") == "thinking" and item.get("step") == step)
        text = " ".join(text.split())
        return text[-limit:]

    def _review_answer(self, step: int) -> None:
        """Annotate the finished answer (answer_review): notes under it, and at most a light in-place fix.

        Never erases the answer and never calls the model again: a visible final answer stays the
        final answer (the live-test double-thinking came from regenerating after streaming).
        """
        try:
            notes, replacement = self.answer_review(self._visible_text())
        except Exception:
            logger.debug("Answer review failed", exc_info=True)
            return
        if replacement and replacement != self._visible_text():
            texts = [item for item in self.timeline if item.get("kind") == "text"]
            if len(texts) == 1:
                texts[0]["text"] = replacement
                self.emit({"type": "text_replace", "step": texts[0].get("step", step), "text": replacement})
        for note in notes:
            self.timeline.append({"kind": "note", "step": step, "text": note, "at": time.time()})
            self.emit({"type": "answer_note", "step": step, "note": note})

    def _retract_text(self, step: int) -> None:
        """Take back text streamed in this step (a claim that turned out to be untrue)."""
        if any(item.get("kind") == "text" and item.get("step") == step for item in self.timeline):
            self.timeline = [item for item in self.timeline if not (item.get("kind") == "text" and item.get("step") == step)]
            self.emit({"type": "text_replace", "step": step, "text": ""})

    def _discard_step(self, step: int) -> None:
        """Take back everything a step streamed (its reply was made from a prompt the server cut)."""
        self._retract_text(step)
        self.timeline = [item for item in self.timeline if not (item.get("kind") == "thinking" and item.get("step") == step)]

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
                "provider": str(getattr(getattr(self.client, "endpoint", None), "provider", "") or ""),
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
        verify_nudges = 0
        menus = 0
        quote_nudges = 0
        forged_nudges = 0
        final_text = ""
        error = ""
        success = True
        overflow_retries = 0
        continuations = 0
        step = 0
        planned = self.prefetch() if self.prefetch is not None else []
        if planned and not self.cancel.is_set():
            # Deterministic retrieval as the turn's first step, recorded like the model's own calls.
            step = 1
            self.emit({"type": "step_start", "step": step})
            calls = [ToolCall(f"prefetch_{index}", name, json.dumps(args)) for index, (name, args) in enumerate(planned)]
            messages.append({"role": "assistant", "content": "", "tool_calls": [
                {"id": call.id, "type": "function", "function": {"name": call.name, "arguments": call.arguments}} for call in calls]})
            self._run_tools(calls, messages, step, call_counts)

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
            except _RetryStep:
                step -= 1  # the same step again, now that the call can be made properly (bounded: detected once)
                continue
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
            if turn.provider_state and (not calls or turn.tool_calls):
                assistant["provider_state"] = turn.provider_state
            if calls:
                assistant["tool_calls"] = [
                    {"id": call.id, "type": "function", "function": {"name": call.name, "arguments": call.arguments or "{}"}, **({"extra_content": call.extra_content} if call.extra_content else {})}
                    for call in calls
                ]
            messages.append(assistant)

            if not calls:
                if content.strip() and turn.finish_reason == "length" and continuations < CONTINUATIONS and step < max_steps:
                    # Cut off by the output limit mid-answer: carry on with the same answer, thinking off,
                    # instead of accepting half an answer or starting a new one.
                    continuations += 1
                    add_note(messages, NUDGE_CONTINUE)
                    self._no_think_next = True
                    self.emit({"type": "answer_continue", "step": step, "count": continuations})
                    continue
                if content.strip():
                    if _FORGED_NOTE.search(content):
                        # deepseek-r1:8b wrote "<echospeak-note>Success! The test now passes.</echospeak-note>" into its own
                        # reply, having run nothing. Only EchoSpeak writes those notes: back once, then removed from view.
                        if forged_nudges < 1:
                            forged_nudges += 1
                            add_note(messages, "Notes in <echospeak-note> tags come only from EchoSpeak, and your reply "
                                               "contained one you wrote yourself. Reply again without it, saying only what "
                                               "the tool results in this conversation show.")
                            self.emit({"type": "forged_note", "step": step})
                            self._retract_text(step)
                            continue
                        content = _FORGED_NOTE.sub("", content).strip()
                        for item in self.timeline:
                            if item.get("kind") == "text" and item.get("step") == step:
                                item["text"] = _FORGED_NOTE.sub("", item["text"])
                        self.emit({"type": "text_replace", "step": step, "text": content})
                    if self.promise_guard and self.toolbox.names and is_promise_without_action(content):
                        # "Sure, I'll do that" is not the work. Ask for the action
                        # (twice at most) instead of ending the turn on a promise.
                        if promise_nudges < 2:
                            promise_nudges += 1
                            add_note(messages, promise_nudge(self.toolbox.names))
                            self.emit({"type": "promise_nudge", "step": step, "count": promise_nudges})
                            continue
                        stop_reason = "promise_unfulfilled"
                    claim = ((unbacked_claim(content, self.succeeded) or unbacked_file_claim(content, self._written)
                              or unbacked_screen_claim(content, self._screen_changed))
                             if self.promise_guard and self.toolbox.names else "")
                    if claim and not stop_reason:
                        # "I've saved it" with nothing saved: ask once for the tool call or the truth.
                        if claim_nudges < self.claim_nudge_limit:
                            claim_nudges += 1
                            add_note(messages, claim_nudge(claim))
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
                        add_note(messages, check_nudge(list(self._unchecked)))
                        self.emit({"type": "check_nudge", "step": step, "files": list(self._unchecked)[:5]})
                        self._retract_text(step)
                        continue
                    problem = (self.verifier.unfinished(content) if self.verifier is not None and not stop_reason
                               and verify_nudges < 1 and step < max_steps else "")
                    if problem:
                        # "All tests pass" while the last check failed: back to the work once, with the evidence.
                        verify_nudges += 1
                        add_note(messages, problem)
                        self.emit({"type": "verification_nudge", "step": step, "note": problem[:300]})
                        self._retract_text(step)
                        continue
                    invented = (evidence.unseen_quotes(content, "\n".join(str(m.get("content") or "") for m in messages
                                                                           if m.get("role") != "assistant"))
                                if quote_nudges < 1 and not stop_reason and step < max_steps else [])
                    if invented:
                        # Quoting a log line nobody showed the model (ministral-3:8b invented one): back once, with the line.
                        quote_nudges += 1
                        add_note(messages, "Your reply quotes lines that don't appear in anything you were shown in this "
                                           f"conversation: “{invented[0][:200]}”. Quote only what a tool returned; search "
                                           "or read the file again if you need the exact line, or say you couldn't find it.")
                        self.emit({"type": "quote_unverified", "step": step, "lines": len(invented)})
                        self._retract_text(step)
                        continue
                    menu = (ask.menu_in_text(content) if self.interactive and ask.ASK_USER in self.toolbox.names
                            and menus < 2 and not stop_reason else None)
                    if menu:
                        # A choice written as prose ends the run; as a question card the run goes on with the pick.
                        menus += 1
                        call = ToolCall(f"menu_{uuid.uuid4().hex[:10]}", ask.ASK_USER, json.dumps(menu))
                        messages[-1]["tool_calls"] = [{"id": call.id, "type": "function",
                                                       "function": {"name": call.name, "arguments": call.arguments}}]
                        self.emit({"type": "menu_as_question", "step": step, "options": len(menu["options"])})
                        self._run_tools([call], messages, step, call_counts)
                        continue
                    if self._absorb_steering(messages, step):
                        # The user added something while this answer was written: keep going with it.
                        self._retract_text(step)
                        continue
                    correction = self.final_check(content) if self.final_check is not None and not stop_reason else ""
                    if correction and step < max_steps:
                        add_note(messages, correction)
                        self.emit({"type": "answer_check", "step": step, "note": correction[:300]})
                        self._retract_text(step)
                        continue
                    final_text = content.strip()
                    break
                if self._handed_off:
                    # The teammate already answered; nothing more is needed.
                    final_text = ""
                    break
                cause = empty_cause(turn, settings.context_tokens(getattr(self.client, "endpoint", None)))
                self._empty_cause = cause
                if nudges < 2:
                    nudges += 1
                    messages.pop()
                    tail = self._reasoning_tail(step)
                    if cause == "incomplete_stream":
                        # Ask for the same step again as it was; thinking wasn't the problem.
                        add_note(messages, NUDGE_INCOMPLETE)
                    elif turn.finish_reason == "length":
                        # Reasoning used the whole budget: answer from it (thinking off), don't reason again.
                        spent = NUDGE_WINDOW_SPENT if cause == "context_window" else NUDGE_REASONING_SPENT
                        add_note(messages, spent + (f" Your reasoning ended with: \"{tail}\"" if tail else ""))
                        self._no_think_next = True
                    elif tail:
                        # Thought but said nothing: the answer is often written in the reasoning and the turn
                        # ended there. Asking again with thinking on repeats it (qwen3.5:9b on Ollama: every
                        # replay of such a call came back empty, every replay with thinking off answered).
                        add_note(messages, NUDGE_THOUGHT_ONLY + f" Your reasoning ended with: \"{tail}\"")
                        self._no_think_next = True
                    else:
                        add_note(messages, NUDGE_EMPTY)
                    # The cause and the server's raw evidence, beside the retry (the judge and the journal read these).
                    self.emit({"type": "empty_reply", "step": step, "cause": cause, "finish": turn.finish_reason,
                               "stream_error": getattr(turn, "stream_error", ""), "usage": dict(turn.usage or {}),
                               "reasoning_chars": len(turn.reasoning or ""),
                               "retry": "thinking_off" if self._no_think_next else "as_is"})
                    continue
                final_text = ""
                # No answer after the bounded retries: say so in the outcome instead of an empty "success".
                stop_reason = "no_answer"
                break

            self._run_tools(calls, messages, step, call_counts)
            file_claim = (unbacked_file_claim(content, self._written)
                          if self.promise_guard and content and messages[-1].get("role") == "tool" else "")
            if file_claim:
                # Text sent beside tool calls is checked too: "I've written mics.md" while only running `dir`.
                messages[-1]["content"] = (str(messages[-1].get("content") or "") + "\n\n" + file_claim_note(file_claim))
                self._retract_text(step)
                self.emit({"type": "claim_nudge", "step": step, "claim": file_claim[:200]})
            if self._refusals >= REFUSAL_STOP:
                # Circuit breaker: hand back to the user instead of probing for a way around "no".
                stop_reason = "refusals"
                note = (f"I stopped here: {self._refusals} of my actions were refused or blocked in this request. "
                        "Tell me how you'd like to go on, or change the setting that blocks it.")
                self._append_text("text", step, note)
                self.emit({"type": "agent_token", "step": step, "data": note})
                break
            if self.goal and step % REMIND_EVERY == 0 and messages[-1].get("role") == "tool":
                # Appended to the tool result, not sent as its own message: some chat
                # templates (Gemma) require strict user/assistant alternation.
                messages[-1]["content"] = (str(messages[-1].get("content") or "") + "\n\n"
                                           + app_note(reminder(self.goal, step, self._outcomes[-REMIND_EVERY:])))
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
            grace = self._grace_answer(messages, step + 1) if getattr(self, "final_grace", False) else ""
            if grace:
                final_text = grace
            else:
                note = self._out_of_steps_note(max_steps)
                self._append_text("text", step, note)
                self.emit({"type": "agent_token", "step": step, "data": note})

        if self.answer_review is not None and success and not error and self._visible_text():
            self._review_answer(step)
        verification = self.verifier.completion() if self.verifier is not None and self.verifier.active() else {}
        if verification and error != "cancelled":
            note = self.verifier.summary_note()
            if note:
                self.timeline.append({"kind": "note", "step": step, "text": note, "at": time.time()})
                self.emit({"type": "answer_note", "step": step, "note": note, "verification": verification["status"]})
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
                visible = {
                    "incomplete_stream": ("The model's replies kept arriving incomplete (the server couldn't read them), "
                                          "so nothing more was done. Try again, or try another model."),
                    "context_window": ("The model filled its context window while thinking and never wrote an answer. "
                                       "Try a shorter request, a new chat, or a model with a larger window."),
                    "output_limit": ("The model used its whole output allowance thinking and never wrote an answer. "
                                     "Try again, or turn thinking down for this model."),
                }.get(self._empty_cause if stop_reason == "no_answer" else "",
                      "I couldn't put together a reply this time. Try asking again or rephrasing.")
            self._append_text("text", step, visible)
            self.emit({"type": "agent_token", "step": step, "data": visible})
        if self.cancel.is_set():
            # Stop ends this turn's own background work too; its servers keep running and are listed (C3).
            try:
                from agent.lean.terminal import stop_cancelled_processes

                swept = stop_cancelled_processes()
                if swept["stopped"] or swept["servers_running"]:
                    self.emit({"type": "processes_stopped", "step": step, **swept})
            except Exception:
                logger.debug("Stopping the turn's processes failed", exc_info=True)
        self.emit({
            "type": "agent_done",
            "text": visible,
            "success": success,
            "error": error,
            "steps": step,
            "usage": dict(self.usage),
            "empty": empty,
            "stop_reason": stop_reason,
            "empty_cause": self._empty_cause if stop_reason == "no_answer" else "",
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
            verification=verification,
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
        if self._no_think_next:
            # A continuation or an answer-from-reasoning: one call without a new thinking phase.
            self._no_think_next = False
            saved = getattr(self.client, "reasoning_effort", "")
            try:
                self.client.reasoning_effort = "none"
                return self._call_model(messages, tools, step)
            finally:
                self.client.reasoning_effort = saved
        def on_reasoning(text: str) -> None:
            self._append_text("thinking", step, text)
            self.emit({"type": "reasoning_delta", "step": step, "text": text})

        def on_content(text: str) -> None:
            self._append_text("text", step, text)
            self.emit({"type": "agent_token", "step": step, "data": text})

        endpoint = getattr(self.client, "endpoint", None)
        if self.work_context:
            self.work_context.coordinator.check(self.work_context)
        from agent.coordination import work_scope
        from agent.admission import inference, loop_accounted
        with work_scope(self.work_context), loop_accounted(), inference(endpoint, cancel=self.cancel), self._span("model.call", step=step, model=str(getattr(endpoint, "model", "") or ""),
                        provider=str(getattr(endpoint, "provider", "") or ""), tools=len(tools or [])) as span:
            # A template that drops tool definitions gets them as text (agent/lean/tool_prompting.py).
            sent_messages = tool_prompting.with_catalog(messages, tools) if tools and tool_prompting.needed(endpoint) else messages
            turn = self.client.stream_turn(
                sent_messages,
                tools=tools or None,
                on_reasoning=on_reasoning,
                on_content=on_content,
                cancel=self.cancel,
                temperature=self.temperature,
            )
            span.set(tool_calls=len(turn.tool_calls), finish=str(turn.finish_reason or ""),
                     prompt_tokens=int((turn.usage or {}).get("prompt") or 0),
                     completion_tokens=int((turn.usage or {}).get("completion") or 0))
            # Count the step once: "fresh" is what it added (new input + reply). Charging the total
            # re-counted the whole re-sent conversation every step, so a 64k work budget ran out after
            # about five steps on a 16k-context model, even on a task whose tests had already passed.
            fresh = self._account_usage(turn.usage, messages, tools) if turn.usage else 0
            # The admission wrapper leaves this call's charge to the loop (admission.loop_accounted).
            if self.work_context:
                self.work_context.coordinator.account(self.work_context, fresh)
        if turn.usage:
            self.emit({"type": "token_usage", **turn.usage, "context_limit": settings.context_tokens(getattr(self.client, "endpoint", None))})
        sent = _estimate_tokens(messages, tools) if turn.usage else 0
        kept = int((turn.usage or {}).get("prompt") or 0)
        cut = window.truncated(endpoint, sent, kept)
        if not cut and tools and kept and not tool_prompting.known(endpoint):
            if tool_prompting.observe(endpoint, sent, _estimate_tokens(messages, []), kept):
                # The server counted the messages but not the tool definitions: this reply was made without
                # knowing the tools (deepseek-r1:8b invented a script's output). Discarded; asked again with them.
                self._discard_step(step)
                if getattr(self, "trace", None) is not None:
                    self.trace.event("tools.prompted", step=step, sent=sent, kept=kept)
                self.emit({"type": "tools_prompted", "step": step, "sent": sent, "kept": kept})
                raise _RetryStep()
        if not cut and sent >= 512 and kept:
            self._token_scale = min(1.4, max(0.75, kept / sent))  # calibrates the next _fit_context
        if cut:
            # The server answered a prompt it had silently cut (system prompt and request gone first). Nothing
            # from this reply is used: its text is taken back, its tool calls never run, and the loop trims
            # to the window the server really has and asks again (agent/lean/window.py).
            window.learn(endpoint, cut)
            self._discard_step(step)
            if getattr(self, "trace", None) is not None:
                self.trace.event("context.truncated", step=step, sent=sent, kept=kept, window=cut)
            self.emit({"type": "context_truncated", "step": step, "sent": sent, "kept": kept, "window": cut})
            raise ProviderError(413, f"The model server cut the prompt to fit its {cut}-token context window "
                                     f"(about {sent} tokens were sent, {kept} kept).")
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

    def _account_side(self, usage: Optional[dict[str, Any]], kind: str) -> None:
        """A model call outside the agent's own steps (compaction): all of it is new input, so all of it counts.

        It doesn't move the step baseline (_last_prompt): the next step is still compared with the last step.
        """
        if not usage:
            return
        prompt, completion = int(usage.get("prompt") or 0), int(usage.get("completion") or 0)
        self.usage["prompt"] += prompt
        self.usage["completion"] += completion
        self.usage["total"] += int(usage.get("total") or prompt + completion)
        self.usage["fresh"] = self.usage.get("fresh", 0) + prompt + completion
        self.emit({"type": "side_usage", "kind": kind, "prompt": prompt, "completion": completion})

    def _added_since_last_call(self, messages: list[dict[str, Any]], tools: Optional[list[dict[str, Any]]]) -> int:
        """Estimated tokens of what the loop added to the conversation since the previous model call.

        Each message's size is remembered by identity (the messages are held, so an id can't be reused):
        new messages count whole, messages that grew (a note appended to a tool result) count what they
        gained, and messages that were trimmed or dropped count nothing.
        """
        seen: dict[int, tuple[dict[str, Any], int]] = getattr(self, "_message_sizes", {})
        sizes = {id(m): (m, _estimate_tokens([m], [])) for m in messages}
        added = sum(max(0, size - seen.get(key, (None, 0))[1]) for key, (_m, size) in sizes.items())
        self._message_sizes = sizes
        return added

    def _account_usage(self, usage: dict[str, Any], messages: Optional[list[dict[str, Any]]] = None,
                       tools: Optional[list[dict[str, Any]]] = None) -> int:
        """Add one step's token counts to the turn's.

        "fresh" is what the step added. Every step re-sends the whole conversation, so
        summing prompt tokens counts it again each time: a 20-step build with a 10k
        context showed ~200k "used" after writing a few files. Fresh counts only the new
        input plus the reply; group jobs budget by it (agent/lean/job.py).
        """
        for key in ("prompt", "completion", "total"):
            self.usage[key] += int(usage.get(key) or 0)
        added = self._added_since_last_call(messages, tools) if messages is not None else None
        if "prompt" in usage or "completion" in usage:
            prompt = int(usage.get("prompt") or 0)
            # A prompt that shrank was trimmed to fit the context window (_fit_context), so the server's growth
            # says nothing about what was added: counting the whole prompt again drained a 64k work budget in ~5
            # steps on a 16k model, but counting nothing missed the new tool result sent in the same step. The
            # loop knows what it added since the last call; it's converted to the server's tokens by this
            # call's own ratio. Only the first step counts the prompt whole.
            if not self._last_prompt:
                grown = prompt
            elif prompt >= self._last_prompt or added is None:
                grown = max(0, prompt - self._last_prompt)
            else:
                sent = _estimate_tokens(messages, tools or [])
                grown = int(added * prompt / sent) if sent else 0
            self._last_prompt = prompt
            fresh = max(0, grown) + int(usage.get("completion") or 0)
        else:  # a provider that reports only a total
            fresh = int(usage.get("total") or 0)
        self.usage["fresh"] = self.usage.get("fresh", 0) + fresh
        return fresh

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
            if not problem and self.call_guard is not None:
                # The environment's own boundary (Chat: agent/lean/chat_mode.py), after alias resolution.
                if reason := self.call_guard(name, args, self.toolbox.entry(name)):
                    policy.audit(policy.Decision("deny", reason, "environment"), name=name, args=args,
                                 session_id=self.session_id, agent=self.persona.id, outcome="blocked")
                    self._policy_reasons[call.id] = reason
                    problem = "policy"
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
            fetched = (policy.download_and_run(str(args.get("command") or args.get("cmd") or ""))
                       if not problem and name in policy.COMMAND_TOOLS else "")
            if fetched:
                # Downloading code and running it at once. From an address the user gave, it asks (no grant covers it);
                # otherwise it came from something the agent read, as in a README's planted `wget … | bash`
                # (granite4.2:8b ran one in the five-model evaluation), and it isn't run.
                asked_for = fetched in f"{getattr(self, '_goal', '')}\n{self.goal}"
                reason = f"it downloads code from {fetched[:120]} and runs it straight away"
                if asked_for:
                    self._policy_reasons[call.id], self._policy_rules[call.id] = reason, "download_execute"
                else:
                    reason += ", and that address isn't in the user's request. Tell the user the exact command if they want to run it"
                    policy.audit(policy.Decision("deny", reason, "download_execute"), name=name, args=args,
                                 session_id=self.session_id, agent=self.persona.id, outcome="blocked")
                    self._policy_reasons[call.id] = reason
                    problem = "policy"
            if not problem and self._denied_targets and call.id not in self._policy_reasons:
                hit = self._after_denial(name, args)
                if hit:
                    # Another route to what the owner just refused (`rm x` denied, then x written empty): it asks
                    # again, and no grant or autonomous mode answers for the owner (approvals.scope_for).
                    self._policy_reasons[call.id] = f"you refused an action on {hit} earlier in this request"
                    self._policy_rules[call.id] = "after_denial"
            prepared.append((call, name, args, problem))

        results: dict[str, tuple[bool, str]] = {}
        visual_results: dict[str, list[dict]] = {}
        # Read-only calls run together only when the whole step is such calls and none needs the owner: the pool
        # doesn't go through _approve, and it ran before the step's other calls. An "ask" on a read went unasked,
        # and a read listed after a write saw the state before it (C2). Anything else runs one by one, in order.
        parallel = [item for item in prepared if not item[3] and self._may_run_unasked_in_parallel(*item[:3])]
        if len(parallel) != len(prepared):
            parallel = []
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
                                        meta=result.meta, outcome=result.outcome, effects=result.effects)
        else:
            sequential = prepared

        computer = {"computer_action"} | policy.NATIVE_COMPUTER_ACT | policy.NATIVE_COMPUTER_LOOK
        computer_calls: list[str] = []
        for call, name, args, problem in sequential:
            if call.id in results:
                continue
            if self.cancel.is_set():
                results[call.id] = (False, "Cancelled by the user before this tool ran.")
                self._outcomes_by_call[call.id] = "skipped"
                continue
            failed_before = any(not results.get(cid, (True, ""))[0] for cid in computer_calls)
            if name in computer:
                computer_calls.append(call.id)
            if failed_before and name in computer:
                # The rest of a batch planned against a screen that didn't behave as expected.
                output = "Not executed: an earlier computer action in this turn failed."
                results[call.id] = (False, output)
                self._tool_finished(call, name, args, step, False, output, 0, outcome="skipped")
                continue
            self._tool_started(call, name, args, step)
            if problem == "policy":
                output = (f"Blocked by EchoSpeak's safety policy: {self._policy_reasons.get(call.id, 'not allowed')}. "
                          "Don't retry it; continue without it or tell the user what was blocked.")
                self._refused[self._sig(name, args)] = "blocked by EchoSpeak's safety policy"
                self._denied_targets |= self._touched(name, args)
                results[call.id] = (False, output)
                self._tool_finished(call, name, args, step, False, output, 0, outcome="refused")
                continue
            if problem == "refused":
                output = (f"Not run: this exact {name} call was already {self._refused[self._sig(name, args)]} "
                          "earlier in this request. Don't ask again or work around it; continue without it or "
                          "tell the user.")
                results[call.id] = (False, output)
                self._tool_finished(call, name, args, step, False, output, 0, outcome="refused")
                continue
            if problem == "repeat":
                output = (
                    f"You already called {name} with exactly these arguments {call_counts.get(self._sig(name, args), 3) - 1} times "
                    "and the result is above. Use that result, change the arguments, or answer the user."
                )
                results[call.id] = (False, output)
                self._tool_finished(call, name, args, step, False, output, 0, outcome="skipped")
                continue
            if problem:
                output = f"Error: {problem}. Call {name} again with a JSON object matching its parameters{self._param_hint(name)}."
                results[call.id] = (False, output)
                self._tool_finished(call, name, args, step, False, output, 0, outcome="skipped")
                continue
            if name == ask.ASK_USER:
                ok, output = self._ask_user(call, args, step)
                results[call.id] = (ok, output)
                self._tool_finished(call, name, args, step, ok, output, 0, outcome="ok" if ok else "skipped")
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
            allowed, denial, denied_as = self._approve(call, name, args, step)
            if not allowed:
                if denial.startswith("The user denied"):
                    self._refused[self._sig(name, args)] = "denied by the user"
                elif denial.startswith("Not run:") and "autonomous mode" in denial:
                    self._refused[self._sig(name, args)] = "held for the owner's approval"
                if denied_as in {"denied", "held"}:
                    self._denied_targets |= self._touched(name, args)
                results[call.id] = (False, denial)
                self._tool_finished(call, name, args, step, False, denial, 0, outcome=denied_as)
                continue
            if outbound.is_outbound(name, self.toolbox.entry(name)):
                # Per-channel send budget (agent/lean/outbound.py): no spamming the user's contacts.
                limited = outbound.take(outbound.channel_of(name))
                if limited:
                    results[call.id] = (False, limited)
                    self._tool_finished(call, name, args, step, False, limited, 0, outcome="refused")
                    continue
            result = self._run_tool(call, name, args, step)
            results[call.id] = (result.ok, result.output)
            visual_results[call.id] = result.observations if result.ok else []
            self._tool_finished(call, name, args, step, result.ok, result.output, result.duration_ms, result.widgets,
                                meta=result.meta, outcome=result.outcome, effects=result.effects)
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
            outcome = self._outcomes_by_call.get(call.id) or ("ok" if ok else "failed")
            # The outcome says it; text matching remains for refusals a tool reports in its own words.
            refused = outcome in evidence.REFUSED or (outcome == "failed" and is_refusal(output))
            if refused:
                self._refusals += 1
                self._refusal_streak += 1
                if self._refusal_streak == REFUSAL_STREAK_NOTE:
                    text += ("\n\n[EchoSpeak: the last few actions were all refused or blocked. Don't look for other "
                             "ways to do the same thing. Finish what you can without them, then tell the user "
                             "plainly what needs their decision or a setting change.]")
                    self.emit({"type": "refusal_streak", "step": step, "count": self._refusal_streak})
            elif ok:
                self._refusal_streak = 0
            streak = 0 if ok else self._fail_streak.get(name, 0) + 1
            self._fail_streak[name] = streak
            if streak and streak % FAIL_STREAK_NOTE == 0:
                # Loop detection beyond identical calls: variations of a failing call rarely recover.
                text += (f"\n\n[EchoSpeak: {name} has failed {streak} times in a row in this request. Stop trying "
                         "variations of it. Read the errors above, then take a different approach or tell the user "
                         "what is blocking you.]")
                self.emit({"type": "fail_streak", "step": step, "tool": name, "count": streak})
            if not refused:
                text += self._screen_progress(name, ok, outcome, step)
            measured = self.verifier.feedback(call.id) if self.verifier is not None else ""
            if measured:
                # Expected vs actual, progress against the best result, and the bottleneck, on the check's own result.
                text += "\n\n" + app_note(measured)
                self.emit({"type": "verification", "step": step, "tool": name, "note": measured[:600]})
            messages.append({"role": "tool", "tool_call_id": call.id, "name": name, "content": text or "(empty result)"})
            if visual_results.get(call.id):
                messages[-1]["observations"] = visual_results[call.id]

    def _screen_progress(self, name: str, ok: bool, outcome: str, step: int) -> str:
        """A note when screen work stalls: looking without acting, or acting with nothing to show for it.

        Looks are counted until an action runs; actions that failed or changed nothing visible
        (evidence "no_effect") are counted until one changes the screen. Any other tool that ran
        (open_application, a command) may have moved things on, so it starts the count again.
        Calls that never ran (skipped after an earlier failure, refused) count for nothing.
        """
        if not evidence.ran(outcome):
            return ""
        if name in SCREEN_LOOK:
            if not ok:
                return ""
            self._look_streak += 1
            if self._look_streak % LOOK_STREAK_NOTE:
                return ""
            self.emit({"type": "screen_stall", "step": step, "kind": "looking", "count": self._look_streak})
            if "computer_action" not in self.toolbox.names:
                return (f"\n\n[EchoSpeak: you've looked at the screen {self._look_streak} times in a row. Looking never "
                        "clicks or types, and you can't control the screen in this chat, so tell the user what you see "
                        "and what they'd need to do or switch on.]")
            return (f"\n\n[EchoSpeak: you've looked at the screen {self._look_streak} times in a row without acting. "
                    "Looking never clicks or types, so the screen won't move on by itself. If you can see the next step, "
                    "do it now with computer_action: a click at the point you see in the newest screenshot, or a key. "
                    "If you can't tell what to do, tell the user what you see and what is blocking you.]")
        if name in policy.SCREEN_CONTROL:
            self._look_streak = 0
            if ok and outcome == "ok":
                self._no_effect_streak = 0
                return ""
            self._no_effect_streak += 1
            if self._no_effect_streak % NO_EFFECT_STREAK_NOTE:
                return ""
            self.emit({"type": "screen_stall", "step": step, "kind": "no_effect", "count": self._no_effect_streak})
            return (f"\n\n[EchoSpeak: your last {self._no_effect_streak} screen actions failed or changed nothing "
                    "visible. Don't repeat them. Change approach: click the middle of the button's label using "
                    "coordinates from the newest screenshot; click once inside the window to focus it first; use the "
                    "keyboard (tab moves between buttons, enter presses, esc goes back); or try double_click. If two "
                    "different approaches fail, stop and tell the user what is blocking you.]")
        if ok:
            self._look_streak = 0
        return ""

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

    def _touched(self, name: str, args: dict[str, Any]) -> set[str]:
        """Existing files and folders a call names: a file tool's paths, or a command's arguments.

        The project folder itself and its parents are left out: every command runs somewhere in them.
        """
        from agent.lean.file_changes import TRACKED

        root = Path(str(getattr(self.toolbox, "project_root", "") or ".")).expanduser()
        base = root
        if name in TRACKED:
            raw = [str(args.get(k) or "") for k in ("path", "file_path", "src", "source", "dst", "destination")]
        elif name in {"terminal", "terminal_run", "process_start"}:
            command = str(args.get("command") or args.get("cmd") or "")
            try:
                tokens = shlex.split(command)
            except ValueError:
                tokens = command.split()
            raw = [token.lstrip("<>|&;") for token in tokens if not token.startswith("-")]
            base = Path(str(args.get("cwd") or root)).expanduser()
        else:
            return set()
        try:
            exclude = {str(root.resolve()), *(str(p) for p in root.resolve().parents)}
        except OSError:
            exclude = set()
        found: set[str] = set()
        for token in raw:
            if not token.strip():
                continue
            path = Path(token).expanduser()
            path = path if path.is_absolute() else base / path
            try:
                resolved = path.resolve()
                if resolved.exists() and str(resolved) not in exclude:
                    found.add(str(resolved))
            except (OSError, ValueError):
                continue
        return found

    def _after_denial(self, name: str, args: dict[str, Any]) -> str:
        """The refused target this call would change (shown to the owner), or ""."""
        from agent.learning.episodes import command_kind
        from agent.lean.file_changes import TRACKED

        if name not in TRACKED:
            if name not in {"terminal", "terminal_run", "process_start"}:
                return ""
            if command_kind(str(args.get("command") or args.get("cmd") or "")) == "read":
                return ""
        hit = sorted(self._touched(name, args) & self._denied_targets)
        if not hit:
            return ""
        root = Path(str(getattr(self.toolbox, "project_root", "") or ".")).expanduser()
        try:
            return str(Path(hit[0]).relative_to(root.resolve()))
        except (ValueError, OSError):
            return hit[0]

    @staticmethod
    def _sig(name: str, args: dict[str, Any]) -> str:
        return hashlib.sha1(f"{name}:{json.dumps(args, sort_keys=True, default=str)}".encode()).hexdigest()

    def _note_source(self, name: str, args: dict[str, Any], ok: bool) -> None:
        if ok and policy.is_untrusted_source(name, self.toolbox.entry(name), args) and name not in self.taint:
            self.taint.append(name)

    def _grace_answer(self, messages: list[dict[str, Any]], step: int) -> str:
        """One last call, without tools, for a budget-limited helper to report what it found.

        Gemini CLI gives a subagent that hits its turn or time limit one final turn to hand
        back its best answer; without it a research worker's findings were lost to an
        "out of steps" note. Only for turns that set final_grace (research workers).
        """
        if self.cancel.is_set():
            return ""
        ask = self.grace_prompt or (
            "[EchoSpeak]: Your step budget is used up. Reply now, without tools, with your findings in the "
            "required format, using only what you actually found. Say what you couldn't confirm.")
        add_note(messages, ask.removeprefix("[EchoSpeak]: "))  # keeps strict role alternation
        try:
            turn = self._call_model(messages, [], step)
        except Exception:
            logger.debug("Grace answer failed", exc_info=True)
            return ""
        return str(turn.content or "").strip()

    def _absorb_steering(self, messages: list[dict[str, Any]], step: int) -> bool:
        """Add instructions the user sent during this run (agent/lean/steering.py). True when any arrived."""
        if not getattr(self, "steerable", True):
            return False  # helpers (workers, delegated teammates) leave it for the agent the user talks to
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

    def _may_run_unasked_in_parallel(self, call: Any, name: str, args: dict[str, Any]) -> bool:
        """A read-only call nothing would ask about: no policy ask and no approval rule (lost-work checks
        concern writes, which never run in parallel)."""
        if not self.toolbox.is_parallel_safe(name) or call.id in self._policy_reasons:
            return False
        needs, _reason = tool_needs_approval(self.toolbox.entry(name), name, args)
        return not needs and settings.approval_mode() not in {"always"}

    def _approve(self, call: Any, name: str, args: dict[str, Any], step: int) -> tuple[bool, str, str]:
        """(allowed, the result text when not, and its outcome: refused, denied, held or skipped)."""
        entry = self.toolbox.entry(name)
        needs, reason = tool_needs_approval(entry, name, args)
        if not needs and settings.approval_mode() != "never" and hasattr(self.toolbox, "file_change_preview"):
            # Emptying a file, or removing most of it, is deleting work: it asks like a delete does.
            from agent.lean.file_changes import lost_work
            lost = lost_work(self.toolbox.file_change_preview(name, args))
            if lost:
                needs, reason = True, lost
        context = self.work_context
        if context and context.coordinator.consume_approval(context, name, args):
            return True, "", ""
        broker = get_approval_broker()
        policy_reason = self._policy_reasons.get(call.id, "")
        # Only screen-guided desktop control can be allowed for a whole chat; other outside content asks every time.
        grantable = (not policy_reason or self._policy_rules.get(call.id) == "screen_control") and name not in approvals.ALWAYS_ASK_TOOLS
        if policy_reason and grantable and broker.granted(self.session_id, policy.grant_name(name)):
            policy.audit(policy.Decision("allow", policy_reason, "screen_control"), name=name, args=args,
                         session_id=self.session_id, agent=self.persona.id, outcome="allowed for this chat")
            return True, "", ""
        if policy_reason:
            # Rule of Two: always ask, even with "always allow" grants or approvals off.
            needs, reason = True, policy_reason
        elif not needs or (grantable and broker.granted(self.session_id, policy.grant_name(name))):
            return True, "", ""
        scope = approvals.scope_for(name, args, reason, self._policy_rules.get(call.id, ""))
        # Grants were made on an approval card in the app and cover chats there. Channels
        # (Discord, Telegram) and unattended routines keep refusing below: a grant must not
        # widen what a channel message, or a routine nobody watches, can do on this PC.
        if self.interactive and scope is not None and approvals.get_grant_store().has(scope.key):
            # The owner chose "Always allow" for this class of action.
            policy.audit(policy.Decision("allow", reason, "always_allowed"), name=name, args=args,
                         session_id=self.session_id, agent=self.persona.id, outcome=f"always allowed: {scope.key}")
            self._auto_note(step, f"Allowed by your Always allow: {scope.label}")
            return True, "", ""
        if settings.approval_mode() == "autonomous" and self.interactive:
            # Only chats in the app: Discord, routines and other sources keep refusing below.
            if scope is not None and scope.autonomous:
                policy.audit(policy.Decision("allow", reason, "autonomous"), name=name, args=args,
                             session_id=self.session_id, agent=self.persona.id, outcome=f"autonomous: {scope.key}")
                self._auto_note(step, f"Allowed automatically (autonomous mode): {scope.label}")
                return True, "", ""
            # Not something autonomy may approve: held for the owner instead of stalling the run.
            summary = describe_call(name, args)
            if self.ensure_work and self.work_context is None:
                self.work_context = self.ensure_work()
            if self.work_context:
                from agent.coordination import digest
                context = self.work_context
                context.coordinator.approval(context, approval_id="apr_" + digest([context.attempt_id, name, args])[:24],
                    tool=name, args=args, summary=summary, reason=reason)
            self.held.append({"tool": name, "summary": summary, "reason": reason})
            self.timeline.append({"kind": "approval", "step": step, "id": f"held_{len(self.held)}", "tool_call_id": call.id,
                                  "summary": summary, "reason": reason, "decision": "held", "at": time.time()})
            self.emit({"type": "approval_held", "step": step, "tool_call_id": call.id, "tool": name, "summary": summary,
                       "reason": reason})
            policy.audit(policy.Decision("ask", reason, "autonomous_held"), name=name, args=args,
                         session_id=self.session_id, agent=self.persona.id, outcome="held for the owner")
            return False, (f"Not run: {name} needs the owner's approval ({reason}), and autonomous mode doesn't approve "
                           "this kind of action. Don't retry it or work around it. Continue with the rest of the task, "
                           "and at the end tell the user exactly what is waiting for their approval."), "held"
        if not self.interactive:
            if self.ensure_work and self.work_context is None:
                self.work_context = self.ensure_work()
            if self.work_context:
                from agent.coordination import digest
                context = self.work_context
                approval_id = "apr_" + digest([context.attempt_id, name, args])[:24]
                context.coordinator.approval(context, approval_id=approval_id, tool=name, args=args,
                    summary=describe_call(name, args), reason=reason or policy_reason)
                self.held.append({"tool": name, "summary": describe_call(name, args), "reason": reason})
                self.emit({"type": "approval_held", "id": approval_id, "work_id": context.work_id, "summary": describe_call(name, args)})
                return False, "Not run: waiting for an exact-action approval in EchoSpeak. Resume the work after review.", "held"
            return False, (
                f"{name} needs the user's approval, which can only be given in the EchoSpeak app. "
                "It was not run. Tell the user to ask for it from the app."
            ), "refused"
        # The approval card promises exact arguments. Refuse calls too large to
        # present intact instead of approving a truncated command or hidden key.
        if len(json.dumps(args, ensure_ascii=False, default=str).encode("utf-8")) > 65536:
            return False, f"{name} has more than 64 KiB of arguments and cannot be shown safely for approval.", "skipped"
        # File tools also show the change they would make (a dry run against the current file).
        preview_change = self.toolbox.file_change_preview(name, args) if hasattr(self.toolbox, "file_change_preview") else None
        if preview_change:
            preview_change = {**preview_change, "tool_call_id": call.id}
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
            file_change=preview_change,
        )
        if self.ensure_work and self.work_context is None:
            self.work_context = self.ensure_work()
        if self.work_context:
            self.work_context.coordinator.approval(self.work_context, approval_id=approval.id, tool=name, args=args,
                summary=approval.summary, reason=reason)
        self.timeline.append({"kind": "approval", "step": step, "id": approval.id, "tool_call_id": call.id,
                              "summary": approval.summary, "reason": reason, "decision": "", "at": time.time()})
        self.emit({"type": "approval_request", "step": step, "tool_call_id": call.id, **_redacted_card(approval.public())})
        with self._span("approval.wait", step=step, tool=name, rule=self._policy_rules.get(call.id, "") or "approval") as span:
            decision = broker.wait(approval, self.cancel)
            span.set(decision=decision)
        if policy_reason:
            policy.audit(policy.Decision("ask", policy_reason, self._policy_rules.get(call.id) or "rule_of_two"), name=name, args=args,
                         session_id=self.session_id, agent=self.persona.id, outcome=decision)
        for item in self.timeline:
            if item.get("kind") == "approval" and item.get("id") == approval.id:
                item["decision"] = decision
        self.emit({"type": "approval_resolved", "id": approval.id, "decision": decision, "tool_call_id": call.id})
        if decision == "allow":
            if self.work_context:
                self.work_context.coordinator.consume_approval(self.work_context, name, args)
            return True, "", ""
        if decision == "timeout":
            return False, f"The user did not respond to the approval request for {name}, so it was not run.", "denied"
        if decision == "cancelled":
            return False, "The user stopped this request.", "skipped"
        return False, f"The user denied permission to run {name}. Do not retry it; continue without it or explain what is blocked.", "denied"

    def _ask_user(self, call: Any, args: dict[str, Any], step: int) -> tuple[bool, str]:
        """Show a question card and wait for the user's answer (agent/lean/ask.py)."""
        question, options, allow_other, problem = ask.parse(args)
        if problem:
            return False, problem
        if self.ensure_work and self.work_context is None:
            self.work_context = self.ensure_work()
        context = self.work_context
        if context:
            answer = context.coordinator.consume_answer(context, args)
            if answer is not None:
                return True, f"The user answered: {answer}"
        if not self.interactive:
            if context:
                from agent.coordination import digest
                context.coordinator.question(context, "question_" + digest([context.attempt_id, args])[:24], args)
            return False, ("A question card can't be shown here. Ask the user in plain text, list the options, "
                           "and end your reply so they can answer.")
        broker = get_approval_broker()
        pending = broker.open_question(session_id=self.session_id, request_id=self.request_id,
                                       question=question, options=options, allow_other=allow_other)
        if context:
            context.coordinator.question(context, pending.id, args)
        self.timeline.append({"kind": "approval", "step": step, "id": pending.id, "tool_call_id": call.id,
                              "tool": ask.ASK_USER, "summary": question, "question": question, "options": options,
                              "allow_other": allow_other, "reason": "", "decision": "", "answer": "", "at": time.time()})
        self.emit({"type": "approval_request", "step": step, "tool_call_id": call.id, **_redacted_card(pending.public())})
        decision = broker.wait(pending, self.cancel)
        for item in self.timeline:
            if item.get("kind") == "approval" and item.get("id") == pending.id:
                item["decision"] = decision
                item["answer"] = pending.answer
        self.emit({"type": "approval_resolved", "id": pending.id, "decision": decision, "answer": pending.answer,
                   "tool_call_id": call.id})
        if decision == "answered":
            if context:
                context.coordinator.consume_answer(context, args)
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
            from agent.coordination import work_scope
            if self.work_context is None and self.ensure_work and not self.toolbox.is_parallel_safe(name):
                self.work_context = self.ensure_work()
            context = self.work_context
            previous = context.coordinator.previous_effect(context, name, args) if context and not self.toolbox.is_parallel_safe(name) else None
            if previous:
                return ToolResult(False, f"Not run: this exact action already completed in attempt {previous['attempt_id']}. Inspect its current result; do not repeat the effect. Recorded evidence: {previous.get('effects', [])}", 0, outcome="refused")
            try:
                action_id = context.coordinator.action_intent(context, call.id, name, args, read_only=self.toolbox.is_parallel_safe(name)) if context else ""
            except PermissionError as exc:
                # The work ledger refused this action (a held resource, the work's grant or budget). That is
                # a result the agent can read and explain, not a crash of the whole turn (review R2).
                return ToolResult(False, f"Not run: {exc}", 0, outcome="refused")
            try:
                with work_scope(context):
                    result = self.toolbox.run(name, args)
            except BaseException:
                if action_id:
                    context.coordinator.action_result(action_id, ok=False, outcome="interrupted")
                raise
            if action_id:
                context.coordinator.action_result(action_id, ok=result.ok, outcome=result.outcome, effects=result.effects, uncertain=result.uncertain)
            if context and result.ok and name in {"process_output", "process_stop"}:
                process_id = str(args.get("process_id") or args.get("id") or "")
                line = (result.output or "").split("\n", 1)[0]
                if line.startswith(f"Stopped process {process_id}.") or line.startswith(f"Process {process_id} is finished"):
                    context.coordinator.process_observed(context, process_id, success="(ok)" in line or line.startswith("Stopped process"))
            span.set(ok=bool(result.ok), output_chars=len(result.output or ""), outcome=result.outcome,
                     effects=[e.get("kind") for e in result.effects][:8])
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
        # The event is journaled and the tool run is saved, so both get the redacted copy too (C5); only the
        # tool itself, and the approval binding's hash, see the real values.
        self.emit({"type": "tool_start", "step": step, "id": call.id, "name": name, "label": label,
                   "input": json.dumps(policy.redact_payload(safe_args_preview(args, 200)), ensure_ascii=False)[:400]})
        if self.persist_tool_runs:
            try:
                from agent.state import get_state_store

                get_state_store().create_tool_run(
                    turn_id=self.execution_id, tool_name=name, session_id=self.session_id,
                    run_id=call.id, canonical_arguments=policy.redact_payload(safe_args_preview(args, 2000)),
                )
            except Exception:
                logger.debug("Lean tool run persistence failed", exc_info=True)

    def _tool_finished(self, call: Any, name: str, args: dict[str, Any], step: int, ok: bool, output: str, duration_ms: int,
                       widgets: Optional[list[dict[str, Any]]] = None, meta: Optional[dict[str, Any]] = None,
                       outcome: str = "", effects: Optional[list[dict[str, Any]]] = None) -> None:
        from agent.lean import steps

        # Execution evidence (agent/lean/evidence.py): the outcome is set where it's known, not read back from text.
        outcome = outcome or ("ok" if ok else "failed")
        if self.tool_observer is not None:
            try:
                self.tool_observer(name, args, ok, output, step, duration_ms, meta)
            except Exception:
                logger.debug("Tool observer failed", exc_info=True)
        if self.verifier is not None:
            try:
                self.verifier.observe(name, args, ok, output, step, duration_ms, meta, call_id=call.id)
            except Exception:
                logger.debug("Verification ledger failed", exc_info=True)
        effects = list(effects or [])
        self._outcomes_by_call[call.id] = outcome
        facts = {**steps.widget_counts(widgets), **(meta or {})}
        if isinstance(facts.get("file_change"), dict):
            # The structured diff for clients (agent/lean/file_changes.py) travels on the event only;
            # the saved timeline keeps the numeric facts.
            facts["file_change"] = {**facts["file_change"], "tool_call_id": call.id}
        if name in CHECK_TOOLS:
            self._unchecked.clear()  # something ran after the changes, pass or fail
        elif ok:
            changed = changed_code_file(name, args)
            if changed:
                self._unchecked[changed] = True
        label = describe_call(name, args)
        # A command that ran and exited non-zero did run ("Ran `pytest`" · "Tests: 1 failed"); a refused or
        # failed file write didn't happen, so it keeps its present-tense label.
        ran_command = name in {"terminal", "terminal_run"} and evidence.ran(outcome) and exit_code_known(output)
        done = steps.done_label(label) if ok or ran_command else label
        summary = steps.summarize(name, args, ok, output, facts)
        if outcome == "no_effect":
            summary = "No visible change on screen"  # the row says what the screen showed, whatever the reply claims
        preview = (output or "").strip()
        if len(preview) > 1600:
            preview = preview[:1600] + "…"
        cards = list(widgets or []) if ok else []
        self._outcomes.append(bool(ok))
        for effect in effects:
            if effect.get("kind") == "screen":
                self._screen_changed = float(effect.get("changed", 1.0))
        if outcome == "ok":
            # A call that ran but changed nothing visible (no_effect) backs no "it worked".
            self.succeeded.add(name)
            observed = evidence.written_files(effects)
            # Files seen on disk count; a path the harness couldn't resolve falls back to the call's own arguments.
            self._written |= observed or written_names(name, args)
        for item in self.timeline:
            if item.get("kind") == "tool" and item.get("id") == call.id:
                item.update({"status": "done" if ok else "failed", "output": preview, "duration_ms": duration_ms,
                             "done_label": done, "summary": summary,
                             "meta": {k: v for k, v in facts.items() if k != "file_change"}, "outcome": outcome})
                if effects:
                    item["effects"] = effects[:8]
                item.pop("detail", None)
                if cards:
                    item["widgets"] = cards
        event = {"type": "tool_end", "step": step, "id": call.id, "name": name, "ok": ok, "outcome": outcome,
                 "output": preview, "duration_ms": duration_ms, "done_label": done, "summary": summary, "meta": facts}
        if effects:
            event["effects"] = effects[:8]
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
                    # The tool-run record's existing evidence fields carry the same outcome and effects.
                    "execution_status": outcome,
                    **({"verification": {"effects": effects[:8]}} if effects else {}),
                })
            except Exception:
                logger.debug("Lean tool run persistence failed", exc_info=True)

    # ── context ─────────────────────────────────────────────────────────
    def _fit_context(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], aggressive: bool = False) -> None:
        endpoint = getattr(self.client, "endpoint", None)
        window = settings.context_tokens(endpoint)
        # Room for this step's reply. Half the output limit (4,096) is a quarter of a 16k window, and with the real
        # window known that idled ~7k tokens: the loop compacted every other step and the model re-read what it had
        # just lost (harness evaluation, D8: 67 calls). Windows of 32k and more keep the reserve they had.
        reserve = min(settings.max_output_tokens(endpoint) // 2, max(2048, window // 8))
        budget = max(128, int(window * (0.55 if aggressive else 0.8)) - reserve)
        # The character estimate, scaled by what the server counted for the last prompt (0.85-1.3 measured).
        scale = getattr(self, "_token_scale", 1.0)

        def size(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> int:
            return int(_estimate_tokens(messages, tools) * scale)
        # 0. Tool-result clearing: once the context is half full, raw output from
        #    tools used many steps ago is cut to its head. The agent already acted
        #    on it, and long sessions stay coherent instead of hitting the wall.
        if size(messages, tools) > budget // 2:
            tool_indexes = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
            for index in tool_indexes[:-6]:
                content = str(messages[index].get("content") or "")
                if len(content) > 2000:
                    messages[index]["content"] = self._clear_note(messages[index], 1500, "older tool output cleared")
        if size(messages, tools) <= budget:
            return
        # 1. Shrink older tool results, keeping the most recent ones intact.
        tool_indexes = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
        keep_recent = 2 if aggressive else 4
        for index in tool_indexes[:-keep_recent] if len(tool_indexes) > keep_recent else []:
            content = str(messages[index].get("content") or "")
            if len(content) > 600:
                messages[index]["content"] = self._clear_note(messages[index], 500, "older tool output trimmed to save context")
            if size(messages, tools) <= budget:
                return
        # 2. Drop the oldest history messages (plain text, never this turn's work).
        #    Older chat is still covered by the chat summary in the system prompt.
        while size(messages, tools) > budget and self._history_in_messages > 0:
            messages.pop(1)
            self._history_in_messages -= 1
        # 3. Summarize this turn's earlier steps instead of cutting them.
        if size(messages, tools) > budget and self._compact_turn(messages, keep_steps=2 if aggressive else 3):
            if size(messages, tools) <= budget:
                return
        # 4. Last resort: shrink every tool result except the newest one.
        if size(messages, tools) > budget:
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
        if self._compact_failures >= COMPACT_FAILURES:
            return False  # it hasn't worked in this turn; trimming (step 4) still fits the context
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
        from agent.coordination import work_scope
        saved = getattr(self.client, "reasoning_effort", None)
        try:
            # A summary needs no thinking phase. With it, every compaction call qwen3.5:9b made in the harness
            # evaluation spent its 700 tokens reasoning (finish "length") and returned nothing, and the loop
            # asked again on the next step: 37 wasted calls in one run, none of them charged.
            if saved is not None:
                self.client.reasoning_effort = "none"
            # In the work's scope, the admission wrapper charges the call to the work budget (prompt + reply).
            with work_scope(self.work_context), self._span("context.compact", steps=len(steps) - keep_steps, input_chars=len(body)):
                turn = self.client.stream_turn([{"role": "user", "content": prompt}], temperature=0.2, max_tokens=700, cancel=self.cancel)
            summary = str(turn.content or "").strip()
        except Exception:
            logger.debug("Lean turn compaction failed", exc_info=True)
            self._compact_failures += 1
            return False
        finally:
            if saved is not None:
                self.client.reasoning_effort = saved
        self._account_side(getattr(turn, "usage", None), "compaction")
        if not summary:
            self._compact_failures += 1
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
