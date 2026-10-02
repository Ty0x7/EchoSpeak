"""OpenAI-compatible streaming client for every provider EchoSpeak supports.

LM Studio, Ollama, vLLM, LocalAI, OpenAI and Gemini all speak the
``/chat/completions`` dialect, so one client covers them. Talking to the
endpoint directly (instead of through LangChain) keeps the reasoning channel,
native tool-call deltas, and finish reasons intact.
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import httpx
from loguru import logger

from config import ModelProvider, config
from agent.lean import settings


@dataclass
class Endpoint:
    base_url: str
    api_key: str
    model: str
    provider: str
    local: bool


def reasoning_effort_for(endpoint: Endpoint, thinking_enabled: bool, effort: str) -> str:
    """Map the composer's Think toggle and Effort picker onto the request."""
    effort = str(effort or "medium").lower()
    level = {"minimal": "low", "low": "low", "medium": "medium", "high": "high",
             "extra_high": "high", "max": "high", "ultra": "high"}.get(effort, "medium")
    if endpoint.local:
        return level if thinking_enabled else "none"
    model = endpoint.model.lower()
    # Cloud: only reasoning models accept the parameter.
    if endpoint.provider == "openai" and (model.startswith(("o1", "o3", "o4", "gpt-5"))):
        return level if thinking_enabled else "low"
    if endpoint.provider == "gemini" and ("2.5" in model or "-3" in model):
        return level if thinking_enabled else "none"
    return ""


def resolve_endpoint(provider: str, model_id: str = "") -> Endpoint:
    """Map EchoSpeak's provider config onto an OpenAI-compatible endpoint."""
    from agent.model_runtime import resolve_local_provider_base_url

    try:
        resolved = ModelProvider(str(provider))
    except ValueError:
        resolved = ModelProvider.LM_STUDIO
    if resolved == ModelProvider.OPENAI:
        return Endpoint(
            base_url="https://api.openai.com/v1",
            api_key=str(config.openai.api_key or ""),
            model=model_id or str(config.openai.model or ""),
            provider=resolved.value,
            local=False,
        )
    if resolved == ModelProvider.GEMINI:
        return Endpoint(
            base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            api_key=str(config.gemini.api_key or ""),
            model=model_id or str(config.gemini.model or ""),
            provider=resolved.value,
            local=False,
        )
    if resolved == ModelProvider.LLAMA_CPP:
        raise RuntimeError(
            "The in-process llama.cpp provider has no HTTP endpoint. "
            "Serve the model with llama-server or LM Studio instead."
        )
    base = resolve_local_provider_base_url(resolved, str(config.local.base_url or ""))
    base = base.rstrip("/")
    if not base.endswith("/v1"):
        base = f"{base}/v1"
    return Endpoint(
        base_url=base,
        api_key="not-needed",
        model=model_id or str(config.local.model_name or ""),
        provider=resolved.value,
        local=True,
    )


class ThinkTagScrubber:
    """Split inline ``<think>`` / ``<thought>`` blocks out of streamed text.

    Tags can be split across chunks (``"<thi"`` + ``"nk>"``), so the scrubber
    holds back any trailing partial tag until the next chunk resolves it.
    """

    OPEN = ("<think>", "<thought>", "<reasoning>")
    CLOSE = ("</think>", "</thought>", "</reasoning>")

    def __init__(self) -> None:
        self.in_think = False
        self.pending = ""

    def feed(self, text: str) -> tuple[str, str]:
        """Return (visible, reasoning) for this chunk."""
        data = self.pending + (text or "")
        self.pending = ""
        visible: list[str] = []
        reasoning: list[str] = []
        while data:
            tags = self.CLOSE if self.in_think else self.OPEN
            lower = data.lower()
            hits = [(lower.find(tag), tag) for tag in tags if lower.find(tag) >= 0]
            if hits:
                index, tag = min(hits)
                (reasoning if self.in_think else visible).append(data[:index])
                data = data[index + len(tag):]
                self.in_think = not self.in_think
                continue
            # No complete tag. Hold back a suffix that could start one.
            hold = 0
            for tag in tags:
                for size in range(len(tag) - 1, 0, -1):
                    if lower.endswith(tag[:size]):
                        hold = max(hold, size)
                        break
            if hold:
                self.pending = data[-hold:]
                data = data[:-hold]
            (reasoning if self.in_think else visible).append(data)
            data = ""
        return "".join(visible), "".join(reasoning)

    def flush(self) -> tuple[str, str]:
        rest, self.pending = self.pending, ""
        return ("", rest) if self.in_think else (rest, "")


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str

    def parsed_arguments(self) -> tuple[dict[str, Any], str]:
        """Return (arguments, error). Small models sometimes emit loose JSON."""
        raw = (self.arguments or "").strip()
        if not raw:
            return {}, ""
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            repaired = _repair_json(raw)
            if repaired is None:
                return {}, f"Arguments were not valid JSON: {raw[:200]}"
            value = repaired
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return {}, "Arguments must be a JSON object."
        if not isinstance(value, dict):
            return {}, "Arguments must be a JSON object."
        return value, ""


def _repair_json(raw: str) -> Optional[Any]:
    candidates = [raw]
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        candidates.append(raw[start:end + 1])
    for text in candidates:
        fixed = re.sub(r",\s*([}\]])", r"\1", text)
        for attempt in (fixed, fixed.replace("'", '"')):
            try:
                return json.loads(attempt)
            except json.JSONDecodeError:
                continue
    return None


@dataclass
class ModelTurn:
    content: str = ""
    reasoning: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = ""
    usage: dict[str, int] = field(default_factory=dict)


_TEXT_TOOL_PATTERNS = (
    re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S),
    re.compile(r"<\|tool_call\|>\s*(\{.*?\})\s*(?:<\|/tool_call\|>|$)", re.S),
    re.compile(r"```(?:json|tool_call)?\s*(\{\s*\"name\".*?\})\s*```", re.S),
)


def extract_text_tool_calls(content: str, known_tools: set[str]) -> tuple[list[ToolCall], str]:
    """Recover tool calls a model wrote as text when native calling slipped."""
    calls: list[ToolCall] = []
    cleaned = content or ""
    for pattern in _TEXT_TOOL_PATTERNS:
        for match in list(pattern.finditer(cleaned)):
            payload = _repair_json(match.group(1))
            if not isinstance(payload, dict):
                continue
            name = str(payload.get("name") or payload.get("tool") or "").strip()
            if name not in known_tools:
                continue
            args = payload.get("arguments", payload.get("parameters", payload.get("args", {})))
            if isinstance(args, str):
                args = _repair_json(args) or {}
            calls.append(ToolCall(
                id=f"call_{uuid.uuid4().hex[:12]}",
                name=name,
                arguments=json.dumps(args if isinstance(args, dict) else {}),
            ))
            cleaned = cleaned.replace(match.group(0), "")
    return calls, cleaned.strip()


class ChatClient:
    """Streams one model turn and reports reasoning/text deltas as they arrive."""

    def __init__(self, endpoint: Endpoint, *, reasoning_effort: str = "") -> None:
        self.endpoint = endpoint
        # "none" turns thinking off (LM Studio honors it for Gemma/Qwen);
        # low/medium/high set its depth. Dropped automatically if rejected.
        self.reasoning_effort = reasoning_effort
        timeout = settings.request_timeout_seconds()
        self._http = httpx.Client(
            base_url=endpoint.base_url,
            timeout=httpx.Timeout(timeout, connect=15.0),
            headers={
                "Authorization": f"Bearer {endpoint.api_key or 'not-needed'}",
                "Content-Type": "application/json",
            },
        )

    def close(self) -> None:
        try:
            self._http.close()
        except Exception:
            pass

    def stream_turn(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: Optional[list[dict[str, Any]]] = None,
        on_reasoning: Optional[Callable[[str], None]] = None,
        on_content: Optional[Callable[[str], None]] = None,
        cancel: Optional[threading.Event] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> ModelTurn:
        body: dict[str, Any] = {
            "model": self.endpoint.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            "max_tokens": int(max_tokens or settings.max_output_tokens()),
        }
        if temperature is not None:
            body["temperature"] = temperature
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        try:
            return self._stream(body, on_reasoning, on_content, cancel)
        except ProviderError as exc:
            if self.reasoning_effort and exc.status == 400 and "reason" in exc.detail.lower():
                logger.info("Provider rejected reasoning_effort; retrying without it")
                self.reasoning_effort = ""
                body.pop("reasoning_effort", None)
                return self._stream(body, on_reasoning, on_content, cancel)
            raise

    def _stream(
        self,
        body: dict[str, Any],
        on_reasoning: Optional[Callable[[str], None]],
        on_content: Optional[Callable[[str], None]],
        cancel: Optional[threading.Event],
    ) -> ModelTurn:
        turn = ModelTurn()
        scrubber = ThinkTagScrubber()
        partial_calls: dict[int, dict[str, str]] = {}
        content_parts: list[str] = []
        reasoning_parts: list[str] = []

        def emit_reasoning(text: str) -> None:
            if text:
                reasoning_parts.append(text)
                if on_reasoning:
                    on_reasoning(text)

        def emit_content(text: str) -> None:
            if text:
                content_parts.append(text)
                if on_content:
                    on_content(text)

        with self._http.stream("POST", "/chat/completions", json=body) as response:
            if response.status_code >= 400:
                detail = response.read().decode("utf-8", errors="ignore")[:800]
                raise ProviderError(response.status_code, detail)
            for line in response.iter_lines():
                if cancel is not None and cancel.is_set():
                    turn.finish_reason = "cancelled"
                    break
                if not line or not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                usage = chunk.get("usage")
                if isinstance(usage, dict):
                    turn.usage = {
                        "prompt": int(usage.get("prompt_tokens") or 0),
                        "completion": int(usage.get("completion_tokens") or 0),
                        "total": int(usage.get("total_tokens") or 0),
                    }
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    emit_reasoning(str(delta.get("reasoning_content") or delta.get("reasoning") or ""))
                    text = delta.get("content")
                    if isinstance(text, str) and text:
                        visible, hidden = scrubber.feed(text)
                        emit_reasoning(hidden)
                        emit_content(visible)
                    for fragment in delta.get("tool_calls") or []:
                        index = int(fragment.get("index") or 0)
                        slot = partial_calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                        if fragment.get("id"):
                            slot["id"] = str(fragment["id"])
                        function = fragment.get("function") or {}
                        if function.get("name"):
                            slot["name"] += str(function["name"])
                        if function.get("arguments"):
                            slot["arguments"] += str(function["arguments"])
                    if choice.get("finish_reason"):
                        turn.finish_reason = str(choice["finish_reason"])

        visible, hidden = scrubber.flush()
        emit_reasoning(hidden)
        emit_content(visible)
        turn.content = "".join(content_parts)
        turn.reasoning = "".join(reasoning_parts)
        for index in sorted(partial_calls):
            slot = partial_calls[index]
            if not slot["name"]:
                continue
            turn.tool_calls.append(ToolCall(
                id=slot["id"] or f"call_{uuid.uuid4().hex[:12]}",
                name=slot["name"].strip(),
                arguments=slot["arguments"],
            ))
        logger.debug(
            "Lean model turn finish={} content={} reasoning={} tools={}",
            turn.finish_reason, len(turn.content), len(turn.reasoning),
            [call.name for call in turn.tool_calls],
        )
        return turn


class ProviderError(RuntimeError):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"Model provider returned HTTP {status}: {detail}")
        self.status = status
        self.detail = detail

    @property
    def context_overflow(self) -> bool:
        text = self.detail.lower()
        return any(marker in text for marker in ("context length", "n_ctx", "context window", "too many tokens", "maximum context"))
