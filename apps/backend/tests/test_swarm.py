"""Research swarm phase 1 (agent/lean/swarm.py), with scripted models: read-only workers under a lead,
bounded and cancellable, structured findings, worker episodes. No real model or network."""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace
from typing import Any

import pytest

from agent import learning
from agent.lean import provider, settings, swarm
from agent.lean.personas import AgentPersona
from agent.lean.provider import ModelTurn, ToolCall
from agent.lean.toolbox import NativeTool, Toolbox
from agent.learning.store import ExperienceStore


class Scripted:
    """A worker's model: turns chosen by the worker's question (its first user message)."""

    scripts: dict[str, list[ModelTurn]] = {}
    fail: set[str] = set()
    calls: list[str] = []

    def __init__(self, endpoint: Any, **_kw: Any) -> None:
        self.turns: list[ModelTurn] | None = None

    def stream_turn(self, messages, **_kw):
        goal = next(m["content"] for m in messages if m["role"] == "user").split("\n")[0]
        Scripted.calls.append(goal)
        if goal in Scripted.fail:
            raise RuntimeError("model unavailable")
        if self.turns is None:
            self.turns = list(Scripted.scripts[goal])
        return self.turns.pop(0)

    def close(self) -> None:
        pass


def _findings(answer: str, *urls: str, confidence: str = "high") -> str:
    return json.dumps({"answer": answer, "sources": [{"url": u, "title": u.split("/")[-1]} for u in urls],
                       "confidence": confidence, "open_questions": []})


def _research(goal: str, url: str, answer: str, tokens: int = 1000) -> list[ModelTurn]:
    return [
        ModelTurn(tool_calls=[ToolCall(f"f-{goal[:6]}", "safe_web_fetch", json.dumps({"url": url}))],
                  usage={"prompt": tokens, "completion": 50}),
        ModelTurn(content=_findings(answer, url), usage={"prompt": tokens + 200, "completion": 120}),
    ]


@pytest.fixture(autouse=True)
def doubles(monkeypatch):
    Scripted.scripts, Scripted.fail, Scripted.calls = {}, set(), []
    monkeypatch.setattr(provider, "ChatClient", Scripted)
    monkeypatch.setattr(settings, "learning_mode", lambda: "off")
    yield


def _session(local: bool = True) -> SimpleNamespace:
    endpoint = SimpleNamespace(provider="lmstudio" if local else "anthropic", model="m", base_url="http://127.0.0.1:1234" if local else "https://api.example.com", local=local)
    return SimpleNamespace(session_id="sw", request_id="req", execution_id="exe", _taint=[], cancel=threading.Event(),
                           caller_role="owner", _endpoint_for=lambda persona: endpoint)


def _lead_toolbox() -> Toolbox:
    box = Toolbox(toolsets=["none"], session_id="sw")
    page = lambda a: f"Page text from {a.get('url')}"  # noqa: E731
    box.native = {
        "safe_web_fetch": NativeTool("safe_web_fetch", "read a page", {"type": "object", "properties": {}}, page, parallel_safe=True),
        "web_search": NativeTool("web_search", "search", {"type": "object", "properties": {}}, lambda a: "1. result", parallel_safe=True),
        # Tools a worker must never get, even though the lead has them:
        "file_edit": NativeTool("file_edit", "edit", {"type": "object", "properties": {}}, lambda a: "edited"),
        "terminal": NativeTool("terminal", "run", {"type": "object", "properties": {}}, lambda a: "ran"),
        "email_send": NativeTool("email_send", "send", {"type": "object", "properties": {}}, lambda a: "sent"),
        "delegate_to_agent": NativeTool("delegate_to_agent", "hand off", {"type": "object", "properties": {}}, lambda a: "x",
                                        handoff=lambda a: "ok", always=True),
    }
    return box


def _swarm(session=None, states=None) -> swarm.Swarm:
    persona = AgentPersona(id="echo", name="Echo")
    return swarm.Swarm(session=session or _session(), persona=persona, lead_toolbox=_lead_toolbox(),
                       cancel=(session or _session()).cancel if session else threading.Event(),
                       state=(states.append if states is not None else None))


# ── shape and boundaries ────────────────────────────────────────────────

def test_workers_get_only_read_only_research_tools_the_lead_has():
    names = set(_swarm().worker_toolbox().names)
    assert names == {"safe_web_fetch", "web_search"}


def test_tasks_need_two_independent_questions_and_duplicates_collapse():
    tasks, problem = swarm.clean_tasks([{"goal": "Battery life of the Pixel 10"}])
    assert not tasks and "two or more" in problem
    tasks, problem = swarm.clean_tasks([
        {"goal": "Battery life of the Pixel 10", "expected_output": "hours"},
        {"goal": "Battery life of the Pixel 10 phone"},
        {"goal": "Battery life of the iPhone 18"},
    ] + [{"goal": f"Battery life of phone model {i}"} for i in range(10)])
    assert problem == "" and len(tasks) == swarm.MAX_WORKERS
    assert [t["goal"] for t in tasks[:2]] == ["Battery life of the Pixel 10", "Battery life of the iPhone 18"]


# ── a run ───────────────────────────────────────────────────────────────

def test_workers_research_in_parallel_and_report_structured_findings():
    Scripted.scripts = {
        "Pixel 10 battery": _research("Pixel 10 battery", "https://a.example/pixel", "Pixel 10: 30h video."),
        "iPhone 18 battery": _research("iPhone 18 battery", "https://b.example/iphone", "iPhone 18: 27h video."),
    }
    states: list[dict] = []
    result = _swarm(_session(local=False), states).run([{"goal": "Pixel 10 battery"}, {"goal": "iPhone 18 battery"}])
    assert result.ok and result.meta == {"workers": 2, "done": 2}
    assert "## Worker 1: Pixel 10 battery\nStatus: done · confidence high" in result.output
    assert "Pixel 10: 30h video." in result.output and "iPhone 18: 27h video." in result.output
    assert "Sources it opened:\n- https://a.example/pixel" in result.output
    final = states[-1]
    assert final["kind"] == "workers" and [w["status"] for w in final["workers"]] == ["done", "done"]
    assert all(w["sources"][0]["opened"] for w in final["workers"]) and final["used"] > 0
    assert any(w["status"] == "running" for s in states for w in s["workers"])  # live updates were sent


def test_a_source_the_worker_never_opened_is_marked_unverified():
    Scripted.scripts = {
        "Claim A question": [ModelTurn(content=_findings("A is 5", "https://never.example/x", confidence="medium"))],
        "Claim B question": _research("Claim B question", "https://b.example/y", "B is 7"),
    }
    result = _swarm().run([{"goal": "Claim A question"}, {"goal": "Claim B question"}])
    assert "Cited but not opened by this worker (treat as unverified): https://never.example/x" in result.output
    assert "Worker 1: Claim A question\nStatus: partial" in result.output and "answered without looking anything up" in result.output


def test_shared_sources_are_flagged_once():
    Scripted.scripts = {
        "Question one here": _research("Question one here", "https://same.example/review", "one"),
        "Question two here": _research("Question two here", "https://same.example/review", "two"),
    }
    result = _swarm().run([{"goal": "Question one here"}, {"goal": "Question two here"}])
    assert "Sources more than one worker used (count them once): https://same.example/review (workers 1, 2)" in result.output


def test_a_failing_model_is_retried_once_then_reported_without_stopping_the_others():
    Scripted.scripts = {"Good question here": _research("Good question here", "https://ok.example", "fine")}
    Scripted.fail = {"Broken question here"}
    result = _swarm().run([{"goal": "Broken question here"}, {"goal": "Good question here"}])
    assert result.ok
    assert Scripted.calls.count("Broken question here") == 2  # one retry
    assert "Worker 1: Broken question here\nStatus: failed" in result.output
    assert "Worker 2: Good question here\nStatus: done" in result.output


def test_the_shared_token_budget_stops_the_swarm(monkeypatch):
    monkeypatch.setattr(settings, "swarm_token_budget", lambda: 10_000)
    Scripted.scripts = {f"Expensive question {i}": _research(f"Expensive question {i}", f"https://x.example/{i}", "x", tokens=9_000)
                        for i in range(3)}
    s = _swarm()
    result = s.run([{"goal": f"Expensive question {i}"} for i in range(3)])
    assert "shared budget of 10,000 tokens ran out" in result.output
    assert [w.status for w in s.workers][-1] == "skipped"


def test_stop_cancels_every_worker_and_records_nothing(monkeypatch):
    session = _session()
    recorded: list[Any] = []
    monkeypatch.setattr(swarm.Swarm, "_learn", lambda self: recorded.append(1) if not self.lead_cancel.is_set() else None)

    def stop_on_fetch(args):
        session.cancel.set()  # the user pressed Stop while worker 1 was reading
        return "page"

    s = _swarm(session)
    s.lead_toolbox.native["safe_web_fetch"].func = stop_on_fetch
    Scripted.scripts = {f"Stoppable question {i}": _research(f"Stoppable question {i}", "https://s.example", "x") for i in range(2)}
    result = s.run([{"goal": f"Stoppable question {i}"} for i in range(2)])
    assert not result.ok
    assert {w.status for w in s.workers} == {"cancelled"}
    assert recorded == []


def test_privacy_mode_still_blocks_a_worker_tool(monkeypatch):
    from agent import privacy

    monkeypatch.setattr(privacy, "tool_block_reason",
                        lambda name, args=None: "Offline mode is on." if name == "safe_web_fetch" else "")
    Scripted.scripts = {f"Private question {i}": _research(f"Private question {i}", "https://p.example", "x") for i in range(2)}
    s = _swarm()
    result = s.run([{"goal": f"Private question {i}"} for i in range(2)])
    item = next(i for i in s.workers[0].turn.timeline if i.get("kind") == "tool")
    assert item["status"] == "failed" and "privacy setting" in item["output"]
    assert "Sources it opened" not in result.output


def test_workers_share_the_requests_outside_content_flag():
    session = _session()
    Scripted.scripts = {f"Taint question {i}": _research(f"Taint question {i}", "https://t.example", "x") for i in range(2)}
    _swarm(session).run([{"goal": f"Taint question {i}"} for i in range(2)])
    assert "safe_web_fetch" in session._taint  # the lead's later sends will ask first (Rule of Two)


# ── learning ────────────────────────────────────────────────────────────

def test_each_worker_becomes_its_own_graded_worker_episode(monkeypatch, tmp_path):
    store = ExperienceStore(tmp_path / "experience.db")
    learning.set_experience_store(store)
    monkeypatch.setattr(settings, "learning_mode", lambda: "on")
    try:
        Scripted.scripts = {
            "Checked question": _research("Checked question", "https://c.example", "checked"),
            "Guessed question": [ModelTurn(content=_findings("a guess", confidence="low"))],
        }
        _swarm().run([{"goal": "Checked question"}, {"goal": "Guessed question"}])
        episodes = {e.goal: e for e in store.episodes()}
        assert set(episodes) == {"Checked question", "Guessed question"}
        assert all(e.role == "worker" and e.source == "swarm" for e in episodes.values())
        assert episodes["Checked question"].level >= 1 and episodes["Checked question"].task_kind == "research"
        assert not episodes["Guessed question"].verified_success  # an answer with nothing looked up is no proof
        assert store.pending_reflections() == []  # workers don't write lessons
    finally:
        learning.set_experience_store(None)
        store.close()


# ── offered only where it belongs ───────────────────────────────────────

def test_spawn_workers_is_offered_only_when_switched_on(monkeypatch, tmp_path):
    from agent.lean import runtime

    monkeypatch.setattr(settings, "research_swarm", lambda: False)
    session = SimpleNamespace(caller_role="owner")
    assert "spawn_workers" not in [t.name for t in _native(monkeypatch, session, depth=0)]
    monkeypatch.setattr(settings, "research_swarm", lambda: True)
    assert "spawn_workers" in [t.name for t in _native(monkeypatch, session, depth=0)]
    assert "spawn_workers" not in [t.name for t in _native(monkeypatch, session, depth=1)]  # teammates never lead workers


def _native(monkeypatch, session, depth):
    from agent.lean import runtime

    fake = runtime.LeanSession.__new__(runtime.LeanSession)
    fake.agent = SimpleNamespace(memory=None)
    fake.session_id, fake.execution_id, fake.caller_role, fake.job = "sw", "exe", "owner", None
    fake.room = None
    fake.personas = SimpleNamespace(list=lambda: [])
    persona = AgentPersona(id="echo", name="Echo")
    monkeypatch.setattr(runtime.LeanSession, "_is_group", lambda self: False)
    return runtime.LeanSession._native_tools(fake, persona, depth, allow_handoff=False)
