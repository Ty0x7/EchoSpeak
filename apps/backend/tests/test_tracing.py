"""Per-turn traces: spans for model calls and tools, saved per reply, exportable as OTLP, never message text."""
import json
import re
import threading

import pytest

from agent import privacy
from agent.lean import policy, tracing
from agent.lean.loop import LeanTurn
from agent.lean.personas import AgentPersona
from agent.lean.provider import ModelTurn, ToolCall
from agent.lean.toolbox import NativeTool, Toolbox
from config import config

SECRET_QUESTION = "What is in my private diary file?"


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(tracing, "root", lambda: tmp_path / "traces")
    monkeypatch.setattr(policy, "_secret_values", lambda: [])
    monkeypatch.setattr(config, "trace_export_url", "", raising=False)


def run_turn(calls):
    tools = [NativeTool(f"look_{n}", "Look", {"type": "object", "properties": {}}, lambda a, n=n: f"diary page {n}",
                        parallel_safe=True) for n in range(3)]
    box = Toolbox(toolsets=["core"], extra_tools=tools, session_id="chat")
    for tool in tools:
        box.native[tool.name] = tool

    class Model:
        count = 0

        def stream_turn(self, messages, **kwargs):
            self.count += 1
            if self.count == 1:
                return ModelTurn(tool_calls=[ToolCall(f"c{n}", f"look_{n}", "{}") for n in range(calls)],
                                 usage={"prompt": 120, "completion": 8})
            return ModelTurn(content="Your diary says hello.", usage={"prompt": 200, "completion": 6})

    turn = LeanTurn(client=Model(), persona=AgentPersona(id="echo", name="Echo"), system_prompt="t", history=[], toolbox=box,
                    session_id="chat", request_id="r1", execution_id="e", emit=lambda e: None, cancel=threading.Event(),
                    persist_tool_runs=False)
    result = turn.run(SECRET_QUESTION)
    return result, tracing.load("chat", result.message_id)


def test_a_reply_is_traced_without_its_content():
    result, record = run_turn(1)
    names = [span["name"] for span in record["spans"]]
    assert names[0] == "agent.turn" and names.count("model.call") == 2 and "tool.run" in names
    root = record["spans"][0]
    assert all(span["parent"] == root["id"] for span in record["spans"][1:])
    assert root["attrs"]["prompt_tokens"] == 320 and root["status"] == "ok"
    model = next(s for s in record["spans"] if s["name"] == "model.call")
    assert model["attrs"]["tool_calls"] == 1 and model["attrs"]["prompt_tokens"] == 120
    text = json.dumps(record)
    assert SECRET_QUESTION not in text and "diary page" not in text and "hello" not in text


def test_parallel_tools_each_get_their_own_span():
    _, record = run_turn(3)
    tools = [s for s in record["spans"] if s["name"] == "tool.run"]
    assert sorted(s["attrs"]["tool"] for s in tools) == ["look_0", "look_1", "look_2"]
    assert all(s["attrs"]["ok"] is True for s in tools)


def test_traces_stay_in_their_chat():
    result, _ = run_turn(1)
    with pytest.raises(PermissionError):
        record = json.loads(tracing._path("chat", result.message_id).read_text())
        record["session_id"] = "chat"
        tracing._path("other", result.message_id).parent.mkdir(parents=True, exist_ok=True)
        tracing._path("other", result.message_id).write_text(json.dumps(record))
        tracing.load("other", result.message_id)
    with pytest.raises(ValueError):
        tracing.load("chat", "../../settings")


def test_otlp_export_shape_and_privacy_gate(monkeypatch):
    _, record = run_turn(1)
    body = tracing.to_otlp(record)
    spans = body["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert re.fullmatch(r"[0-9a-f]{32}", spans[0]["traceId"]) and re.fullmatch(r"[0-9a-f]{16}", spans[0]["spanId"])
    assert all(int(s["endTimeUnixNano"]) >= int(s["startTimeUnixNano"]) for s in spans)

    class Now:  # run the export thread inline
        def __init__(self, target, **kw): self.target = target
        def start(self): self.target()
    monkeypatch.setattr(tracing.threading, "Thread", Now)
    monkeypatch.setattr(config, "trace_export_url", "https://collector.example.com/v1/traces", raising=False)
    posted = []
    import httpx
    monkeypatch.setattr(httpx, "post", lambda url, **kw: posted.append((url, kw["json"])))

    def blocked(component, target):
        raise privacy.PrivacyBlocked("Private mode")
    monkeypatch.setattr(privacy, "require", blocked)
    tracing.export(record)
    assert posted == []
    monkeypatch.setattr(privacy, "require", lambda component, target: None)
    tracing.export(record)
    assert posted and posted[0][0].endswith("/v1/traces")
