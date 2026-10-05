"""Native Claude Messages and Gemini Live adapters for the existing tool loop."""
from __future__ import annotations

import json
import time
from typing import Any
from agent.lean import settings
from agent.lean.provider import ModelTurn, ToolCall, ProviderError


def _content_parts(content: Any, *, google: bool = False) -> list[dict]:
    if isinstance(content, str):
        if not content:
            return []
        return [{"text": content}] if google else [{"type": "text", "text": content}]
    parts = []
    for item in content or []:
        if item.get("type") == "text" and item.get("text"):
            parts.append({"text": item["text"]} if google else {"type": "text", "text": item["text"]})
        elif item.get("type") == "image_url":
            url = (item.get("image_url") or {}).get("url", "")
            if url.startswith("data:") and ";base64," in url:
                mime, data = url[5:].split(";base64,", 1)
                parts.append({"inlineData": {"mimeType": mime, "data": data}} if google else {"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}})
            elif url and not google:
                parts.append({"type": "image", "source": {"type": "url", "url": url}})
            elif url:
                raise ValueError("Gemini Live image input requires an attached image, not a remote image URL.")
    return parts


def anthropic_messages(messages: list[dict]) -> tuple[str, list[dict]]:
    system, turns = [], []
    for message in messages:
        role = message.get("role")
        if role in ("system", "developer"):
            system.extend(p["text"] for p in _content_parts(message.get("content")) if p.get("type") == "text")
            continue
        if role == "tool":
            role = "user"
            parts = [{"type": "tool_result", "tool_use_id": message["tool_call_id"], "content": str(message.get("content") or "")}]
        else:
            role = "assistant" if role == "assistant" else "user"
            parts = message.get("provider_blocks") or _content_parts(message.get("content"))
            if not message.get("provider_blocks"):
                for call in message.get("tool_calls") or []:
                    function = call["function"]
                    parts.append({"type": "tool_use", "id": call["id"], "name": function["name"], "input": json.loads(function.get("arguments") or "{}")})
        if not parts:
            continue
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"].extend(parts)
        else:
            turns.append({"role": role, "content": list(parts)})
    return "\n\n".join(system), turns


def anthropic_turn(client, messages, tools, on_reasoning, on_content, cancel, max_tokens):
    system, turns = anthropic_messages(messages)
    body = {"model": client.endpoint.model, "messages": turns, "stream": True, "max_tokens": int(max_tokens or settings.max_output_tokens())}
    if system:
        body["system"] = system
    if tools:
        body["tools"] = [{"name": t["function"]["name"], "description": t["function"].get("description", ""), "input_schema": t["function"].get("parameters", {"type": "object"})} for t in tools]
    turn = ModelTurn()
    blocks: dict[int, dict] = {}
    arguments: dict[int, str] = {}
    prompt_tokens = 0
    from agent.cloud_providers import cloud_headers
    headers = cloud_headers("anthropic", client.endpoint.api_key)
    with client._http.stream("POST", "/messages", json=body, headers=headers) as response:
        if response.status_code >= 400:
            raise ProviderError(response.status_code, response.read().decode(errors="replace").replace(client.endpoint.api_key, "[redacted]"), "anthropic")
        for line in response.iter_lines():
            if cancel is not None and cancel.is_set():
                turn.finish_reason = "cancelled"
                return turn
            if not line.startswith("data:"):
                continue
            chunk = json.loads(line[5:])
            kind = chunk.get("type")
            index = int(chunk.get("index") or 0)
            if kind == "error":
                raise ProviderError(502, str(chunk.get("error", {}).get("message", "Stream failed")), "anthropic")
            if kind == "message_start":
                usage = chunk.get("message", {}).get("usage", {})
                prompt_tokens = sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
            elif kind == "content_block_start":
                blocks[index] = dict(chunk["content_block"])
            elif kind == "content_block_delta":
                delta = chunk.get("delta", {})
                block = blocks.setdefault(index, {})
                if delta.get("type") == "text_delta":
                    text = delta.get("text", "")
                    block["text"] = block.get("text", "") + text
                    turn.content += text
                    if on_content:
                        on_content(text)
                elif delta.get("type") == "thinking_delta":
                    text = delta.get("thinking", "")
                    block["thinking"] = block.get("thinking", "") + text
                    turn.reasoning += text
                    if on_reasoning:
                        on_reasoning(text)
                elif delta.get("type") == "signature_delta":
                    block["signature"] = block.get("signature", "") + delta.get("signature", "")
                elif delta.get("type") == "input_json_delta":
                    arguments[index] = arguments.get(index, "") + delta.get("partial_json", "")
            elif kind == "message_delta":
                turn.finish_reason = chunk.get("delta", {}).get("stop_reason") or ""
                completion = int(chunk.get("usage", {}).get("output_tokens") or 0)
                turn.usage = {"prompt": prompt_tokens, "completion": completion, "total": prompt_tokens + completion}
            elif kind == "message_stop":
                break
    for index in sorted(blocks):
        block = blocks[index]
        if block.get("type") == "tool_use":
            raw = arguments.get(index) or json.dumps(block.get("input") or {})
            block["input"] = json.loads(raw)
            turn.tool_calls.append(ToolCall(block["id"], block["name"], raw))
        turn.provider_blocks.append(block)
    return turn


def _google_schema(value):
    # Live's Schema dialect rejects OpenAI-only keywords.
    if isinstance(value, dict):
        return {k: (v.upper() if k == "type" and isinstance(v, str) else _google_schema(v)) for k, v in value.items() if k not in ("additionalProperties", "$schema", "strict")}
    if isinstance(value, list):
        return [_google_schema(v) for v in value]
    return value


def _google_turns(messages):
    turns, names = [], {}
    for m in messages:
        role = m.get("role")
        if role in ("system", "developer"):
            continue
        if role == "tool":
            parts = [{"functionResponse": {"id": m["tool_call_id"], "name": names.get(m["tool_call_id"], "tool"), "response": {"result": str(m.get("content") or "")}}}]
        else:
            parts = _content_parts(m.get("content"), google=True)
            for call in m.get("tool_calls") or []:
                names[call["id"]] = call["function"]["name"]
                parts.append({"functionCall": {"id": call["id"], "name": call["function"]["name"], "args": json.loads(call["function"].get("arguments") or "{}")}})
        if parts:
            turns.append({"role": "model" if role == "assistant" else "user", "parts": parts})
    return turns


def gemini_live_turn(client, messages, tools, on_reasoning, on_content, cancel, max_tokens):
    from websockets.sync.client import connect
    from websockets.exceptions import ConnectionClosed, InvalidStatus
    deadline = time.monotonic() + settings.request_timeout_seconds()
    turn = ModelTurn()

    def receive():
        while time.monotonic() < deadline:
            if cancel is not None and cancel.is_set():
                return None
            try:
                packet = json.loads(client._live.recv(timeout=0.5))
            except TimeoutError:
                continue
            if packet.get("error"):
                error = packet["error"]
                raise ProviderError(int(error.get("code") or 400), str(error.get("message") or "Live API error"), "gemini")
            return packet
        raise ProviderError(504, "Gemini Live timed out. Try again or select a standard Gemini chat model.", "gemini")

    try:
        if client._live is None:
            client._live = connect("wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent", additional_headers={"x-goog-api-key": client.endpoint.api_key}, open_timeout=15, close_timeout=2, max_size=16 * 1024 * 1024)
            setup = {"model": f"models/{client.endpoint.model}", "generationConfig": {"responseModalities": ["AUDIO"], "maxOutputTokens": int(max_tokens or settings.max_output_tokens())}, "outputAudioTranscription": {}}
            system = "\n\n".join(p["text"] for m in messages if m.get("role") in ("system", "developer") for p in _content_parts(m.get("content"), google=True) if "text" in p)
            if system:
                setup["systemInstruction"] = {"parts": [{"text": system}]}
            if tools:
                setup["tools"] = [{"functionDeclarations": [{"name": t["function"]["name"], "description": t["function"].get("description", ""), "parameters": _google_schema(t["function"].get("parameters", {"type": "object"}))} for t in tools]}]
            client._live.send(json.dumps({"setup": setup}))
            while True:
                packet = receive()
                if packet is None:
                    turn.finish_reason = "cancelled"
                    return turn
                if "setupComplete" in packet:
                    break
            client._live.send(json.dumps({"clientContent": {"turns": _google_turns(messages), "turnComplete": True}}))
        else:
            pending = getattr(client, "_live_pending", {})
            responses = [{"id": m["tool_call_id"], "name": pending[m["tool_call_id"]], "response": {"result": str(m.get("content") or "")}} for m in messages if m.get("role") == "tool" and m.get("tool_call_id") in pending]
            if {r["id"] for r in responses} != set(pending):
                raise ValueError("Gemini Live tool results were missing. Retry this turn.")
            client._live_pending = {}
            client._live.send(json.dumps({"toolResponse": {"functionResponses": responses}}))
        while True:
            packet = receive()
            if packet is None:
                turn.finish_reason = "cancelled"
                return turn
            usage = packet.get("usageMetadata") or {}
            if usage:
                turn.usage = {"prompt": int(usage.get("promptTokenCount") or 0), "completion": int(usage.get("responseTokenCount") or 0), "total": int(usage.get("totalTokenCount") or 0)}
            calls = (packet.get("toolCall") or {}).get("functionCalls") or []
            if calls:
                turn.tool_calls = [ToolCall(c["id"], c["name"], json.dumps(c.get("args") or {})) for c in calls]
                client._live_pending = {c.id: c.name for c in turn.tool_calls}
                turn.finish_reason = "tool_calls"
                return turn
            server = packet.get("serverContent") or {}
            text = (server.get("outputTranscription") or {}).get("text") or ""
            transcribed = bool(text)
            for part in (server.get("modelTurn") or {}).get("parts") or []:
                if part.get("thought"):
                    thought = part.get("text", "")
                    turn.reasoning += thought
                    if thought and on_reasoning:
                        on_reasoning(thought)
                elif not transcribed:
                    text += part.get("text", "")
            if text:
                turn.content += text
                if on_content:
                    on_content(text)
            if server.get("turnComplete"):
                turn.finish_reason = "stop"
                return turn
    except InvalidStatus as exc:
        raise ProviderError(exc.response.status_code, "Live API handshake rejected", "gemini") from None
    except ConnectionClosed as exc:
        detail = str(exc).replace(client.endpoint.api_key, "[redacted]")
        raise ProviderError(400, f"Gemini Live connection closed: {detail}", "gemini") from None
    finally:
        # Keep the socket only while the existing tool loop executes its calls.
        if not turn.tool_calls and client._live is not None:
            client._live.close()
            client._live = None
