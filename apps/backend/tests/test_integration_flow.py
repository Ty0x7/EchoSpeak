"""One owner request through the whole lean session, with scripted models and fake web tools:
identity and capability context, a research swarm, untrusted wrapping and taint, the Workers
state, and the lead's and workers' learning episodes, checked together rather than one by one."""

from __future__ import annotations

import json
import threading
import uuid
from typing import Any

import pytest

from agent import learning
from agent.lean import provider, runtime as lean_runtime, settings
from agent.lean.provider import ModelTurn, ToolCall
from agent.lean.toolbox import NativeTool
from agent.learning import playbook
from agent.learning.store import ExperienceStore
from tests.test_lean_runtime import ScriptedClient
from version import APP_VERSION


class WorkerModel:
    """Workers' model, scripted by each worker's question."""

    def __init__(self, endpoint: Any, **_kw: Any) -> None:
        self.turns: list[ModelTurn] | None = None

    def stream_turn(self, messages, **_kw):
        goal = next(m["content"] for m in messages if m["role"] == "user").split("\n")[0]
        if self.turns is None:
            url = f"https://example.test/{goal.split()[0].lower()}"
            self.turns = [
                ModelTurn(tool_calls=[ToolCall(f"f{uuid.uuid4().hex[:4]}", "safe_web_fetch", json.dumps({"url": url}))],
                          usage={"prompt": 800, "completion": 40}),
                ModelTurn(content=json.dumps({"answer": f"{goal}: 30 hours", "sources": [{"url": url, "title": "spec"}],
                                              "confidence": "high", "open_questions": []}),
                          usage={"prompt": 1000, "completion": 80}),
            ]
        return self.turns.pop(0)

    def close(self) -> None:
        pass


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("LEARNING_ENABLED", "1")
    monkeypatch.setenv("LEARNING_MODE", "on")
    monkeypatch.setattr(playbook, "HOLDOUT_UNPROVEN", 0.0)
    monkeypatch.setattr(playbook, "HOLDOUT_PROVEN", 0.0)
    fresh = ExperienceStore(tmp_path / "experience.db")
    learning.set_experience_store(fresh)
    yield fresh
    learning.set_experience_store(None)
    fresh.close()


def test_one_request_through_swarm_policy_and_learning(monkeypatch, store):
    pages = lambda a: f"Spec page for {a.get('url')}: battery 30 hours."  # noqa: E731
    fake_web = [
        NativeTool("safe_web_fetch", "read a page", {"type": "object", "properties": {"url": {"type": "string"}}}, pages, parallel_safe=True),
        NativeTool("web_search", "search", {"type": "object", "properties": {"query": {"type": "string"}}}, lambda a: "1. a result", parallel_safe=True),
    ]
    monkeypatch.setattr(lean_runtime, "rich_tools", lambda: fake_web)
    monkeypatch.setattr(settings, "research_swarm", lambda: True)
    monkeypatch.setattr(provider, "ChatClient", WorkerModel)
    lead = ScriptedClient([
        ModelTurn(tool_calls=[ToolCall("w1", "spawn_workers", json.dumps({"tasks": [
            {"goal": "Pixel battery life", "expected_output": "hours"},
            {"goal": "iPhone battery life", "expected_output": "hours"},
        ]}))]),
        ModelTurn(content="Both last about 30 hours of video (https://example.test/pixel, https://example.test/iphone)."),
    ])
    monkeypatch.setattr(lean_runtime.LeanSession, "_client_for", lambda self, persona, routing=False: lead)
    monkeypatch.setattr(lean_runtime.LeanSession, "_recall", lambda self, query, limit=8: [])
    events: list[dict[str, Any]] = []
    session = lean_runtime.LeanSession(agent=type("Agent", (), {"memory": None})(), session_id=f"flow-{uuid.uuid4().hex[:6]}",
                                       request_id="req-flow", emit=events.append, cancel=threading.Event(), source="web")

    out = session.run("Compare the battery life of the Pixel and the iPhone", persona_id="echo")

    # Context: the identity and capability sections reached the lead's system prompt.
    system = lead.calls[0][0]["content"]
    assert f"version {APP_VERSION}" in system and "## What you can use right now" in system
    assert any(t["function"]["name"] == "spawn_workers" for t in lead.tools[0])
    # Workers ran, live state was emitted under the lead's tool row, and the lead got their findings as outside content.
    states = [e["state"] for e in events if e.get("type") == "tool_state"]
    assert states and [w["status"] for w in states[-1]["workers"]] == ["done", "done"]
    findings = next(m["content"] for m in lead.calls[1] if m.get("role") == "tool")
    assert "<untrusted-content" in findings and "Pixel battery life: 30 hours" in findings
    # Rule of Two: what the workers read taints the whole request.
    assert {"safe_web_fetch", "spawn_workers"} <= set(session._taint)
    assert "30 hours" in out.get("response", "") or "30 hours" in json.dumps(out)
    # Learning: one episode for the lead's reply, one per worker, kept apart.
    episodes = store.episodes(limit=20)
    workers = [e for e in episodes if e.source == "swarm"]
    leads = [e for e in episodes if e.source != "swarm"]
    assert len(workers) == 2 and all(e.role == "worker" and e.verified_success for e in workers)
    assert len(leads) == 1 and leads[0].agent_id == "echo" and not leads[0].trusted  # it read outside content
