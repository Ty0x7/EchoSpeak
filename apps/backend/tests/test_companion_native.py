"""Echo's own agents report to the companion in-process, with EchoSpeak's measured evidence."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from agent.companion import native
from agent.companion.commentary import compose
from agent.companion.digest import analyse
from agent.companion import knowledge


class Session:
    def __init__(self):
        self.session_id = "thread-1"
        self.source = "desktop"
        self._pending_prompt = "Run the harness evals"
        self.results = [SimpleNamespace(agent_id="p-glados", text="## Results\n- 12 passed.")]

    def _name_of(self, agent_id):
        return {"p-glados": "Glados"}.get(agent_id, "Echo")

    def _active_project(self):
        return ""


@pytest.fixture()
def published(monkeypatch):
    out = []
    monkeypatch.setattr(native, "_publish", out.append)
    return out


def test_only_relevant_events_are_forwarded(published):
    s = Session()
    for kind in ("agent_token", "tool_start", "memory_saved"):
        native.observe(s, {"type": kind})
    assert published == []
    native.observe(s, {"type": "run_start", "execution_id": "x1"})
    native.observe(s, {"type": "delegation", "from": "echo", "to": "p-glados", "task": "Clean the logs"})
    native.observe(s, {"type": "run_outcome", "summary": "All three subtasks done", "status": "done"})
    kinds = [(e["agent"], e["kind"]) for e in published]
    assert kinds == [("echo", "prompt"), ("glados", "prompt"), ("echo", "stage_completed")]
    assert published[0]["text"] == "Run the harness evals" and published[1]["meta"]["delegated"]


def test_heartbeat_prompts_are_not_the_owner(published):
    s = Session()
    s.source = "heartbeat"
    native.observe(s, {"type": "run_start", "execution_id": "x"})
    assert published == []


def test_finished_turn_is_attributed_and_carries_measured_status(published):
    native.finished(Session(), final_text="## Results\n- 12 passed.", success=True, cancelled=False,
                    execution_id="x1", verification={"p-glados": {"status": "checks_pass"}})
    [event] = published
    assert event["agent"] == "glados" and event["kind"] == "turn_completed"
    assert event["meta"]["measured"] == "checks_pass" and event["meta"]["agent_name"] == "Glados"
    native.finished(Session(), final_text="x", success=True, cancelled=True, execution_id="x2")
    assert len(published) == 1  # cancelled turns aren't news


def test_measured_evidence_is_said_and_remembered_differently():
    report = "## Summary\nRebuilt the index.\n\n## Results\n- 38 passed, 0 failed.\n\n## Changes\n- Added retention.\n" + "x " * 200
    d = analyse(report)
    event = {"id": "e1", "agent": "glados", "session_id": "t", "kind": "turn_completed", "at": time.time(),
             "project": "echospeak", "text": report, "meta": {"measured": "verified"}}
    line = compose(event, d, humor=2)
    assert "EchoSpeak's own checks pass" in line and "by its account" not in line and "suspicious" not in line
    assert "EchoSpeak's own checks passed" in knowledge.memory_text(event, d)
    reported = {**event, "meta": {}}
    assert "by its account" in compose(reported, d, humor=1)
    assert "not independently verified" in knowledge.memory_text(reported, d)


def test_a_broken_companion_never_breaks_a_turn(monkeypatch):
    monkeypatch.setattr(native, "_publish", lambda e: (_ for _ in ()).throw(RuntimeError("boom")))
    native.observe(Session(), {"type": "run_start"})  # swallowed
    native.finished(Session(), final_text="hi", success=True, cancelled=False, execution_id="x")
