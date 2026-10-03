"""Work-together groups keep going until the goal is verified done.

Discuss -> Decide (assign_tasks) -> Execute -> Observe -> Verify -> Continue.
Completion comes from evidence (tool calls that worked, tasks closed), not from
an agent saying it is done. Scripted model turns; no model server needed.
"""

from __future__ import annotations

import json
import threading
import uuid
from typing import Any

from agent.lean import runtime as lean_runtime
from agent.lean.job import Job, needs_action
from agent.lean.provider import ModelTurn, ToolCall
from agent.lean.rooms import Room
from agent.lean.toolbox import NativeTool
from tests.test_lean_runtime import ScriptedClient


class Project:
    """A tiny fake project: one file, and tests that pass once the bug is fixed."""

    def __init__(self) -> None:
        self.files: dict[str, str] = {}
        self.test_runs = 0

    def tools(self) -> list[NativeTool]:
        def write_file(args: dict[str, Any]) -> str:
            self.files[str(args.get("path"))] = str(args.get("content"))
            return f"Wrote {args.get('path')}"

        def run_tests(_args: dict[str, Any]) -> str:
            self.test_runs += 1
            body = self.files.get("counter.py", "")
            if not body:
                return "[exit code 1] counter.py not found"
            if "+ 1" not in body:
                return "[exit code 1] FAILED test_increment: expected 1, got 0"
            return "2 passed"

        schema = {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}}
        return [
            NativeTool(name="file_write", description="write a file", parameters=schema, func=write_file),
            NativeTool(name="terminal", description="run the tests", parameters={"type": "object", "properties": {}}, func=run_tests),
        ]


def _call(call_id: str, name: str, args: dict[str, Any] | None = None) -> ToolCall:
    return ToolCall(call_id, name, json.dumps(args or {}))


def _tools(call_id: str, *calls: tuple[str, dict[str, Any]]) -> ModelTurn:
    return ModelTurn(content="", tool_calls=[_call(f"{call_id}{i}", name, args) for i, (name, args) in enumerate(calls)])


def _done(call_id: str, summary: str) -> ModelTurn:
    return _tools(call_id, ("complete_task", {"summary": summary}))


class Reviewer:
    """The completion check, scripted verdict by verdict. Records what it was shown."""

    def __init__(self, verdicts: list[Any]) -> None:
        self.verdicts = list(verdicts)
        self.prompts: list[str] = []

    def stream_turn(self, messages, **kwargs):
        self.prompts.append(str(messages[-1]["content"]))
        verdict = self.verdicts.pop(0)
        return ModelTurn(content=verdict if isinstance(verdict, str) else json.dumps(verdict))


class _NoTerminal:
    """The real terminal tool would replace the fake one; these tests use Project's."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def tools(self) -> list[NativeTool]:
        return []

    def describe(self) -> str:
        return ""


def _work_room(session_id: str, cap: int = 8) -> Room:
    # cap 8: an old "stop after 8 messages" room; it now gets the default turn budget.
    return Room(id=f"room_{session_id}", name="Team", kind="group", agent_ids=["echo", "scout", "forge"],
                thread_id=session_id, mode="discussion", max_messages=cap)


def _session(monkeypatch, scripts: dict[str, Any], reviewer: Reviewer, project: Project, *, cap: int = 8):
    monkeypatch.setattr(lean_runtime.LeanSession, "_client_for", lambda self, persona, routing=False: scripts[persona.id])
    monkeypatch.setattr(lean_runtime.LeanSession, "_recall", lambda self, query, limit=8: [])
    monkeypatch.setattr(lean_runtime.LeanSession, "_review_client", lambda self, persona: reviewer)
    monkeypatch.setattr(lean_runtime, "coding_tools", project.tools)
    monkeypatch.setattr(lean_runtime, "rich_tools", lambda: [])
    monkeypatch.setattr(lean_runtime, "artifact_tools", lambda: [])
    monkeypatch.setattr(lean_runtime, "Terminal", _NoTerminal)
    session_id = f"work-{uuid.uuid4().hex[:8]}"
    events: list[dict[str, Any]] = []
    session = lean_runtime.LeanSession(
        agent=type("Agent", (), {"memory": None})(), session_id=session_id, request_id=f"req-{uuid.uuid4().hex[:6]}",
        emit=events.append, cancel=threading.Event(), source="web", room=_work_room(session_id, cap),
    )
    return session, events


def test_group_runs_well_past_eight_turns_with_corrections_and_finishes_verified(monkeypatch):
    project = Project()
    buggy = "def inc(n):\n    return n\n"
    fixed = "def inc(n):\n    return n + 1\n"
    scripts = {
        # Discuss (one short round), then Echo decides and later wraps up.
        "echo": ScriptedClient([
            ModelTurn(content="Glados should write counter.py, Jarvis should test it."),
            _tools("e1", ("assign_tasks", {"tasks": [
                {"owner": "Glados", "task": "write counter.py with inc(n)"},
                {"owner": "Jarvis", "task": "run the tests for counter.py"},
            ]})),
            ModelTurn(content="Glados writes it, Jarvis tests it."),
            ModelTurn(content="Done: counter.py is written and both tests pass. Want anything else?"),
        ]),
        "scout": ScriptedClient([
            ModelTurn(content="Agreed. I'll run the tests once it exists."),
            # t2: tests fail (the file has a bug). A real attempt, reported honestly.
            _tools("s1", ("terminal", {})), ModelTurn(content="Tests fail: inc returns n, expected n + 1."),
            # t4: still failing after the first fix.
            _tools("s2", ("terminal", {})), ModelTurn(content="Still failing."),
            # t6: passes.
            _tools("s3", ("terminal", {})), _done("s4", "Ran the tests: 2 passed."),
        ]),
        "forge": ScriptedClient([
            ModelTurn(content="Sounds good."),
            _tools("f1", ("file_write", {"path": "counter.py", "content": buggy})), _done("f2", "Wrote counter.py."),
            # t3: a fix that doesn't fix it.
            _tools("f3", ("file_write", {"path": "counter.py", "content": buggy + "# fixed?\n"})), _done("f4", "Fixed it."),
            # t5: the real fix.
            _tools("f5", ("file_write", {"path": "counter.py", "content": fixed})), _done("f6", "inc now returns n + 1."),
        ]),
    }
    reviewer = Reviewer([
        {"done": False, "reason": "the tests fail", "next": "Glados", "instruction": "fix inc so it returns n + 1"},
        {"done": False, "reason": "the fix hasn't been tested", "next": "Jarvis", "instruction": "run the tests again"},
        {"done": False, "reason": "the tests still fail", "next": "Glados", "instruction": "fix inc properly"},
        {"done": False, "reason": "the new fix hasn't been tested", "next": "Jarvis", "instruction": "run the tests again"},
        {"done": True, "summary": "counter.py written; tests pass."},
    ])
    session, events = _session(monkeypatch, scripts, reviewer, project)
    out = session.run("@Echo build counter.py with an inc function and make its tests pass")

    speakers = [m["agent_id"] for m in out["messages"]]
    assert len(speakers) >= 11, speakers  # well past the old 8-message cap
    assert out["outcome"] == {"status": "done", "summary": "counter.py written; tests pass."}
    assert project.files["counter.py"] == fixed and project.test_runs == 3
    assert out["response"].endswith("Want anything else?")  # Echo's wrap-up
    # Decisions became owned tasks on a board everyone saw.
    assert any(e["type"] == "task_board" for e in events)
    assert "Task board:" in str(scripts["forge"].calls[1][-1]["content"])
    # The completion check judged from what actually ran, failures included.
    assert "terminal" in reviewer.prompts[0] and "FAILED" in reviewer.prompts[0]
    assert sum(1 for e in events if e["type"] == "job_continue") == 4


def test_agents_that_only_agree_are_made_to_do_the_work(monkeypatch):
    """The reported bug: they discuss, agree, and stop without doing anything."""
    project = Project()
    scripts = {
        "echo": ScriptedClient([
            ModelTurn(content="Let's have Glados write counter.py."),
            # Decide: no assign_tasks, just agreement.
            ModelTurn(content="We all agree the plan is good."),
        ]),
        "scout": ScriptedClient([ModelTurn(content="Agreed.")]),
        "forge": ScriptedClient([
            ModelTurn(content="Agreed, good plan."),
            _tools("f1", ("file_write", {"path": "counter.py", "content": "def inc(n):\n    return n + 1\n"})),
            _done("f2", "Wrote counter.py."),
            ModelTurn(content="counter.py is written. Need anything else?"),
        ]),
    }
    # The check can't produce a verdict: the evidence decides (nothing done yet -> not done).
    reviewer = Reviewer(["hmm", "not sure", "hmm", "still unsure"])
    session, events = _session(monkeypatch, scripts, reviewer, project)
    monkeypatch.setattr(lean_runtime.LeanSession, "_continuation_agent", lambda self, name, default: self.personas.get("forge"))
    out = session.run("@Echo write counter.py with an inc function")

    assert project.files.get("counter.py"), "the work was never done"
    assert out["outcome"]["status"] == "done"
    assert any(e["type"] == "job_continue" and "only discussed" in e["reason"] for e in events)


def test_rounds_without_progress_stop_with_a_visible_reason(monkeypatch):
    project = Project()
    scripts = {
        "echo": ScriptedClient([
            ModelTurn(content="Glados, write it."),
            _tools("e1", ("assign_tasks", {"tasks": [{"owner": "Glados", "task": "write counter.py"}]})),
            ModelTurn(content="Glados has it."),
        ]),
        "scout": ScriptedClient([ModelTurn(content="Fine.")]),
        # Glados never touches a tool: talk only, every time.
        "forge": ScriptedClient([ModelTurn(content="OK.")] + [ModelTurn(content="It's a tricky one, still thinking.")] * 6),
    }
    reviewer = Reviewer([{"done": False, "reason": "counter.py doesn't exist", "next": "Glados", "instruction": "write it"}] * 6)
    session, _ = _session(monkeypatch, scripts, reviewer, project, cap=60)
    out = session.run("@Echo write counter.py")

    assert out["outcome"]["status"] == "stopped"
    assert "made no progress" in out["outcome"]["reason"]
    assert len(reviewer.prompts) <= 3  # stopped early, long before the 60-turn budget


def test_done_without_any_tool_call_is_reopened():
    job = Job(goal="write counter.py")
    item = job.open_subtask(owner="Glados", task="write counter.py", assigned_by="Echo")
    job.close_subtask(item, done=True, summary="Done!")
    assert job.unbacked_tasks() == [item]
    item.actions = 1
    assert job.unbacked_tasks() == []
    answer = job.open_subtask(owner="Jarvis", task="explain what counter.py does", assigned_by="Echo")
    job.close_subtask(answer, done=True, summary="It counts.")
    assert answer not in job.unbacked_tasks()  # an answer needs no tool call


def test_progress_means_work_not_new_tasks():
    job = Job(goal="g", max_stalls=2)
    assert job.note_progress() is False and job.stalls == 1  # nothing yet
    job.open_subtask(owner="A", task="t", assigned_by="B")
    assert job.note_progress() is False and job.stalls == 2  # a new task is not progress
    assert "made no progress" in job.backstop()
    job.record_tool("A", "run_tests", "run tests", True)
    assert job.note_progress() is True and job.stalls == 0 and job.backstop() == ""


def test_action_words():
    assert needs_action("write hello.py and run it") and needs_action("Fix the failing test")
    assert not needs_action("which option is better?") and not needs_action("explain the tradeoffs")
