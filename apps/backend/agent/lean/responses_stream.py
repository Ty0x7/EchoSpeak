"""Stateless Responses transport for Echo's existing model/tool loop.

Opaque items are replayed only to the exact provider, endpoint and model that
created them. Only completed function calls enter the normal Toolbox path.
"""
from __future__ import annotations

import copy
import hashlib
import json

from agent.lean import settings
from agent.lean.provider import ModelTurn, ProviderError, ToolCall


def affinity(endpoint):
    return {"transport": "responses", "provider": endpoint.provider,
            "base_url": endpoint.base_url.rstrip("/"), "model": endpoint.model,
            "credential": hashlib.sha256(endpoint.api_key.encode()).hexdigest()}


def response_input(messages, endpoint):
    items = []
    for message in messages:
        role = message.get("role")
        state = message.get("provider_state") or {}
        if role == "assistant" and state.get("items") and all(state.get(k) == v for k, v in affinity(endpoint).items()):
            items.extend(copy.deepcopy(state.get("items") or []))
            continue
        if role == "tool":
            items.append({"type": "function_call_output", "call_id": message["tool_call_id"],
                          "output": str(message.get("content") or "")})
            continue
        content = message.get("content")
        if content:
            if isinstance(content, list):
                parts = []
                for part in content:
                    if part.get("type") == "text":
                        parts.append({"type": "output_text" if role == "assistant" else "input_text", "text": part["text"]})
                    elif part.get("type") == "image_url" and role != "assistant":
                        image = part["image_url"]
                        parts.append({"type": "input_image", "image_url": image["url"],
                                      **({"detail": image["detail"]} if image.get("detail") else {})})
                content = parts
            if content:
                items.append({"role": role, "content": content})
        for call in message.get("tool_calls") or []:
            function = call["function"]
            items.append({"type": "function_call", "call_id": call["id"],
                          "name": function["name"], "arguments": function.get("arguments") or "{}"})
    return items


def responses_turn(client, messages, tools, on_reasoning, on_content, cancel, max_tokens):
    endpoint = client.endpoint
    body = {"model": endpoint.model, "input": response_input(messages, endpoint),
            "stream": True, "store": False,
            "max_output_tokens": min(int(max_tokens or settings.max_output_tokens(endpoint)), settings.max_output_tokens(endpoint))}
    if client.reasoning_effort:
        body["reasoning"] = {"effort": client.reasoning_effort}
    if tools:
        # Echo's existing schemas have optional fields. Responses' default strict
        # normalization would silently change their meaning; keep them non-strict.
        body["tools"] = [{"type": "function", "name": t["function"]["name"],
                          "description": t["function"].get("description", ""),
                          "parameters": t["function"].get("parameters", {"type": "object"}),
                          "strict": False} for t in tools]
        body["tool_choice"] = "auto"
    turn = ModelTurn()
    final = None
    with client._http.stream("POST", "/responses", json=body) as response:
        if response.status_code >= 400:
            detail = response.read().decode(errors="replace").replace(endpoint.api_key, "[redacted]")
            raise ProviderError(response.status_code, detail, endpoint.provider)
        for line in response.iter_lines():
            if cancel is not None and cancel.is_set():
                turn.finish_reason = "cancelled"
                return turn
            if not line.startswith("data:") or line[5:].strip() == "[DONE]":
                continue
            event = json.loads(line[5:])
            kind = event.get("type")
            if kind in ("response.output_text.delta", "response.refusal.delta"):
                delta = event.get("delta") or ""
                turn.content += delta
                if on_content:
                    on_content(delta)
            elif kind == "response.reasoning_summary_text.delta":
                delta = event.get("delta") or ""
                turn.reasoning += delta
                if on_reasoning:
                    on_reasoning(delta)
            elif kind in ("error", "response.failed"):
                # Do not expose raw output/state or execute partial tool deltas.
                raise ProviderError(502, "Responses stream failed; no tool calls from this turn were executed.", endpoint.provider)
            elif kind in ("response.completed", "response.incomplete"):
                final = event.get("response") or {}
                break
    if final is None:
        raise ProviderError(502, "Responses stream ended before completion; no tool calls from this turn were executed.", endpoint.provider)
    output = final.get("output") or []
    complete = final.get("status") == "completed"
    if not complete and any(item.get("type") == "function_call" for item in output):
        raise ProviderError(502, "Responses tool turn was incomplete; no tool calls from this turn were executed.", endpoint.provider)
    if not turn.content:
        turn.content = "".join(part.get("text", "") if part.get("type") == "output_text" else part.get("refusal", "")
                               for item in output if item.get("type") == "message"
                               for part in item.get("content") or [] if part.get("type") in ("output_text", "refusal"))
        if turn.content and on_content:
            on_content(turn.content)
    for item in output:
        if item.get("type") == "function_call":
            if not all(isinstance(item.get(k), str) and item[k] for k in ("call_id", "name", "arguments")):
                raise ProviderError(502, "Responses returned an invalid function call.", endpoint.provider)
            turn.tool_calls.append(ToolCall(item["call_id"], item["name"], item["arguments"]))
    turn.provider_state = {**affinity(endpoint), "items": output}
    usage = final.get("usage") or {}
    prompt, completion = int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)
    turn.usage = {"prompt": prompt, "completion": completion, "total": prompt + completion}
    turn.finish_reason = "tool_calls" if turn.tool_calls else "stop" if complete else "length"
    return turn
