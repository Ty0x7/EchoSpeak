"""The lean agent loop.

    messages -> model -> tool calls? -> run tools -> messages -> model -> ... -> answer

The loop ends when the model replies without calling a tool. Failures inside
the loop (bad arguments, tool errors, denied approvals, repeated calls) are
returned to the model as tool results so it can adapt; they never end the
turn on their own.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from loguru import logger

from agent.lean import settings
from agent.lean.approvals import get_approval_broker, tool_needs_approval
from agent.lean.personas import AgentPersona
from agent.lean.provider import ChatClient, ModelTurn, ProviderError, extract_text_tool_calls
from agent.lean.toolbox import Toolbox, describe_call, safe_args_preview

Emit = Callable[[dict[str, Any]], None]

NUDGE_EMPTY = (
    "You stopped without replying to the user. Continue now: call a tool if more work is needed, "
    "otherwise write your answer."
)
NUDGE_TRUNCATED = "Your last reply was cut off by the output limit. Continue from where you stopped, concisely."
NUDGE_BUDGET = (
    "You have used the tool budget for this request. Do not call tools. Tell the user what you finished, "
    "what you found, and what is left to do."
)


@dataclass
class TurnResult:
    text: str
    success: bool
    message_id: str
    agent_id: str
    timeline: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""


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
    ) -> None:
        self.interactive = interactive
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
    def run(self, user_message: str) -> TurnResult:
        self.emit({
            "type": "agent_start",
            "agent": {
                "id": self.persona.id,
                "name": self.persona.name,
                "title": self.persona.title,
                "initials": self.persona.initials(),
            },
        })
        tools = self.toolbox.schemas()
        messages: list[dict[str, Any]] = [{"role": "system", "content": self.system_prompt}]
        messages.extend(self.history)
        self._history_in_messages = len(self.history)
        messages.append({"role": "user", "content": user_message})

        max_steps = settings.max_iterations()
        call_counts: dict[str, int] = {}
        nudges = 0
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
                    final_text = content.strip()
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
        else:
            # Tool budget exhausted: one last model call without tools.
            messages.append({"role": "user", "content": NUDGE_BUDGET})
            try:
                step += 1
                self.emit({"type": "step_start", "step": step})
                turn = self._call_model(messages, [], step)
                final_text = turn.content.strip()
            except Exception as exc:
                error = str(exc)
                success = False

        visible = "\n\n".join(
            item["text"].strip() for item in self.timeline if item.get("kind") == "text" and item.get("text", "").strip()
        ).strip()
        if not visible:
            visible = final_text
        if not visible:
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
        })
        return TurnResult(
            text=visible,
            success=success,
            message_id=self.message_id,
            agent_id=self.persona.id,
            timeline=self.timeline,
            error=error,
        )

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
            signature = hashlib.sha1(f"{name}:{json.dumps(args, sort_keys=True, default=str)}".encode()).hexdigest()
            call_counts[signature] = call_counts.get(signature, 0) + 1
            prepared.append((call, name, args, arg_error or ("" if call_counts[signature] < 3 else "repeat")))

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
                    self._tool_finished(call, name, args, step, result.ok, result.output, result.duration_ms)
        else:
            sequential = prepared

        for call, name, args, problem in sequential:
            if call.id in results:
                continue
            if self.cancel.is_set():
                results[call.id] = (False, "Cancelled by the user before this tool ran.")
                continue
            self._tool_started(call, name, args, step)
            if problem == "repeat":
                output = (
                    f"You already called {name} with exactly these arguments {call_counts.get(self._sig(name, args), 3) - 1} times "
                    "and the result is above. Use that result, change the arguments, or answer the user."
                )
                results[call.id] = (False, output)
                self._tool_finished(call, name, args, step, False, output, 0)
                continue
            if problem:
                output = f"Error: {problem}. Call {name} again with a JSON object matching its parameters."
                results[call.id] = (False, output)
                self._tool_finished(call, name, args, step, False, output, 0)
                continue
            allowed, denial = self._approve(call, name, args, step)
            if not allowed:
                results[call.id] = (False, denial)
                self._tool_finished(call, name, args, step, False, denial, 0)
                continue
            result = self.toolbox.run(name, args)
            results[call.id] = (result.ok, result.output)
            self._tool_finished(call, name, args, step, result.ok, result.output, result.duration_ms)

        limit = 14000
        for call, name, _args, _ in prepared:
            ok, output = results.get(call.id, (False, "No result."))
            text = output if len(output) <= limit else output[:limit] + f"\n…[{len(output) - limit} more characters trimmed]"
            messages.append({"role": "tool", "tool_call_id": call.id, "name": name, "content": text or "(empty result)"})

    @staticmethod
    def _sig(name: str, args: dict[str, Any]) -> str:
        return hashlib.sha1(f"{name}:{json.dumps(args, sort_keys=True, default=str)}".encode()).hexdigest()

    def _approve(self, call: Any, name: str, args: dict[str, Any], step: int) -> tuple[bool, str]:
        entry = self.toolbox.entry(name)
        needs, reason = tool_needs_approval(entry, name, args)
        broker = get_approval_broker()
        if not needs or broker.granted(self.session_id, name):
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

    def _tool_finished(self, call: Any, name: str, args: dict[str, Any], step: int, ok: bool, output: str, duration_ms: int) -> None:
        preview = (output or "").strip()
        if len(preview) > 1600:
            preview = preview[:1600] + "…"
        for item in self.timeline:
            if item.get("kind") == "tool" and item.get("id") == call.id:
                item.update({"status": "done" if ok else "failed", "output": preview, "duration_ms": duration_ms})
        self.emit({"type": "tool_end", "step": step, "id": call.id, "name": name, "ok": ok,
                   "output": preview, "duration_ms": duration_ms})
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
        while _estimate_tokens(messages, tools) > budget and self._history_in_messages > 0:
            messages.pop(1)
            self._history_in_messages -= 1
        # 3. Last resort: shrink every tool result except the newest one.
        if _estimate_tokens(messages, tools) > budget:
            tool_indexes = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
            for index in tool_indexes[:-1]:
                if index < len(messages) and messages[index].get("role") == "tool":
                    content = str(messages[index].get("content") or "")
                    if len(content) > 300:
                        messages[index]["content"] = content[:250] + "\n…[trimmed]"


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
