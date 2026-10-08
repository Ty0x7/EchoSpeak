"""When is a group chat's job done? Scripted scenarios, no model server needed.

Covers the bug where an agent replied "Sure, I'll do that" and the chat ended
with nothing done, plus a real completion, a ping-pong loop, and a hand-off
chain. See agent/lean/job.py for the rules.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from agent.lean import runtime as lean_runtime
from agent.lean.job import Job, is_promise_without_action, near_duplicate
from agent.lean.provider import ModelTurn, ToolCall
from agent.lean.toolbox import NativeTool
from tests.test_lean_group import _delegate, _room, _session
from tests.test_lean_runtime import ScriptedClient


def _call(call_id: str, name: str, **args: Any) -> ToolCall:
    return ToolCall(call_id, name, json.dumps(args))


def _complete(call_id: str, summary: str) -> ToolCall:
    return _call(call_id, "complete_task", summary=summary)


def _review(*verdicts: dict[str, Any]) -> ScriptedClient:
    return ScriptedClient([ModelTurn(content=json.dumps(v)) for v in verdicts])


@pytest.fixture()
def written(monkeypatch) -> list[dict[str, Any]]:
    """A stand-in file_write tool, so 'doing the work' is observable."""
    calls: list[dict[str, Any]] = []

    def file_write(args: dict[str, Any]) -> str:
        calls.append(args)
        return f"Wrote {args.get('path')}"

    tool = NativeTool(name="file_write", description="Write a file.", parameters={"type": "object", "properties": {
        "path": {"type": "string"}, "content": {"type": "string"}}}, func=file_write)
    monkeypatch.setattr(lean_runtime, "coding_tools", lambda: [tool])
    return calls


def _group(monkeypatch, scripts: dict[str, Any]):
    sid = f"job-{uuid.uuid4().hex[:6]}"
    scripts.setdefault("scout", ScriptedClient([]))
    scripts.setdefault("forge", ScriptedClient([]))
    return _session(monkeypatch, scripts, room=_room(sid, ["echo", "scout", "forge"]), session_id=sid)


def _outcome(events: list[dict[str, Any]]) -> dict[str, Any]:
    outcomes = [e for e in events if e["type"] == "run_outcome"]
    assert len(outcomes) == 1, "every group job must end with exactly one visible outcome"
    return outcomes[0]


# ── the reported bug ────────────────────────────────────────────────────

def test_saying_ill_do_that_without_acting_does_not_end_the_chat(monkeypatch, written):
    scripts = {
        "echo": ScriptedClient([
            ModelTurn(tool_calls=[_delegate("d1", "Glados", "write hello.py that prints hi")]),
            ModelTurn(content="Glados wrote it."),
        ]),
        # The bug: "Sure, I'll do that!" used to be Glados's whole turn.
        "forge": ScriptedClient([
            ModelTurn(content="Sure, I'll do that!"),
            ModelTurn(tool_calls=[_call("w1", "file_write", path="hello.py", content="print('hi')")]),
            ModelTurn(tool_calls=[_complete("c1", "Wrote hello.py, which prints hi.")]),
        ]),
        "_review": _review({"done": True, "summary": "hello.py was written."}),
    }
    session, events = _group(monkeypatch, scripts)
    out = session.run("@Echo get Glados to write hello.py")

    assert written == [{"path": "hello.py", "content": "print('hi')"}]
    assert [e for e in events if e["type"] == "promise_nudge"]
    delegate_result = next(m["content"] for m in scripts["echo"].calls[1] if m.get("role") == "tool")
    assert delegate_result.startswith("Glados finished the task.")
    assert _outcome(events) == {**_outcome(events), "status": "done"}
    assert out["outcome"]["status"] == "done"


def test_a_teammate_who_only_ever_promises_is_sent_back_to_finish(monkeypatch, written):
    scripts = {
        "echo": ScriptedClient([
            ModelTurn(tool_calls=[_delegate("d1", "Glados", "write notes.md")]),
            ModelTurn(content="Glados is on it!"),
        ]),
        "forge": ScriptedClient([
            ModelTurn(content="On it."),
            ModelTurn(content="Will do."),
            ModelTurn(content="I'll get that written."),  # still only promising: the task stays open
            # The orchestrator's continuation round:
            ModelTurn(tool_calls=[_call("w1", "file_write", path="notes.md", content="# Notes")]),
            ModelTurn(tool_calls=[_complete("c1", "Wrote notes.md.")]),
        ]),
        "_review": _review({"done": True, "summary": "notes.md was written."}),
    }
    session, events = _group(monkeypatch, scripts)
    out = session.run("@Echo have Glados write notes.md")

    assert written and written[0]["path"] == "notes.md"
    cont = [e for e in events if e["type"] == "job_continue"]
    assert cont and cont[0]["agent"] == "Glados" and "hasn't finished" in cont[0]["reason"]
    delegate_result = next(m["content"] for m in scripts["echo"].calls[1] if m.get("role") == "tool")
    assert "did NOT do anything" in delegate_result
    assert out["outcome"]["status"] == "done"


# ── a real completion stops ─────────────────────────────────────────────

def test_real_completion_ends_the_chat_after_one_check(monkeypatch, written):
    reviewer = _review({"done": True, "summary": "Wrote todo.txt with three items."})
    scripts = {
        "forge": ScriptedClient([
            ModelTurn(tool_calls=[_call("w1", "file_write", path="todo.txt", content="a\nb\nc")]),
            ModelTurn(tool_calls=[_complete("c1", "Wrote todo.txt with three items.")]),
        ]),
        "echo": ScriptedClient([]),  # must not be asked to continue
        "_review": reviewer,
    }
    session, events = _group(monkeypatch, scripts)
    out = session.run("@Glados write todo.txt with three items")

    assert len(reviewer.calls) == 1
    assert "Glados says it is complete: Wrote todo.txt" in reviewer.calls[0][0]["content"]
    assert not [e for e in events if e["type"] == "job_continue"]
    assert out["outcome"] == {"status": "done", "summary": "Wrote todo.txt with three items."}
    assert [m["agent_id"] for m in out["messages"]] == ["forge"]
    assert out["messages"][0]["text"] == "Wrote todo.txt with three items."  # complete_task alone still shows a message


def test_a_failed_check_continues_with_the_stated_reason(monkeypatch, written):
    scripts = {
        "forge": ScriptedClient([
            ModelTurn(tool_calls=[_complete("c1", "Wrote the page.")]),  # claims done, wrote nothing
            ModelTurn(tool_calls=[_call("w1", "file_write", path="index.html", content="<h1>Hi</h1>")]),
            ModelTurn(tool_calls=[_complete("c2", "Wrote index.html.")]),
        ]),
        "echo": ScriptedClient([]),
        "_review": _review(
            {"done": False, "reason": "no file was written", "next": "Glados", "instruction": "write index.html"},
            {"done": True, "summary": "index.html was written."},
        ),
    }
    session, events = _group(monkeypatch, scripts)
    out = session.run("@Glados make index.html")

    cont = [e for e in events if e["type"] == "job_continue"]
    assert [c["reason"] for c in cont] == ["no file was written"]
    brief = scripts["forge"].calls[1][-1]["content"]
    assert "isn't finished yet: no file was written" in brief and "write index.html" in brief
    assert written and out["outcome"]["status"] == "done"


# ── backstops ───────────────────────────────────────────────────────────

def test_ping_pong_loop_trips_a_backstop_with_a_visible_reason(monkeypatch):
    same = "I think we should look at this from another angle before deciding anything at all."
    not_done = {"done": False, "reason": "nothing concrete yet", "next": "Jarvis", "instruction": "decide"}
    scripts = {
        "scout": ScriptedClient([ModelTurn(content=same) for _ in range(10)]),
        "echo": ScriptedClient([]),
        "_review": _review(*[not_done] * 10),
    }
    session, events = _group(monkeypatch, scripts)
    out = session.run("@Jarvis pick a name for the project")

    outcome = _outcome(events)
    assert outcome["status"] == "stopped"
    assert "repeating themselves" in outcome["reason"] and "nothing concrete yet" in outcome["reason"]
    assert len(scripts["scout"].calls) <= 3
    assert out["outcome"]["status"] == "stopped"


def test_round_limit_stops_a_job_that_never_finishes(monkeypatch):
    monkeypatch.setattr(lean_runtime.settings, "group_max_rounds", lambda: 3)
    answers = ["First idea: Falcon.", "Second idea, quite different: Orchard.", "Third, unrelated: Basalt.",
               "Fourth: Meridian.", "Fifth: Lantern."]
    not_done = {"done": False, "reason": "the user wanted one final name", "next": "Jarvis", "instruction": "choose"}
    scripts = {
        "scout": ScriptedClient([ModelTurn(content=a) for a in answers]),
        "echo": ScriptedClient([]),
        "_review": _review(*[not_done] * 5),
    }
    session, events = _group(monkeypatch, scripts)
    session.run("@Jarvis pick a name")

    outcome = _outcome(events)
    assert outcome["status"] == "stopped" and "limit of 3 rounds" in outcome["reason"]
    assert len(scripts["scout"].calls) == 3


def test_token_budget_stops_the_job(monkeypatch):
    monkeypatch.setattr(lean_runtime.settings, "group_token_budget", lambda: 1000)
    not_done = {"done": False, "reason": "keep going", "next": "Jarvis"}
    scripts = {
        "scout": ScriptedClient([ModelTurn(content=f"Long answer number {i}.", usage={"total": 800}) for i in range(5)]),
        "echo": ScriptedClient([]),
        "_review": _review(*[not_done] * 5),
    }
    session, events = _group(monkeypatch, scripts)
    session.run("@Jarvis research everything")

    outcome = _outcome(events)
    assert outcome["status"] == "stopped" and "token budget" in outcome["reason"]
    assert len(scripts["scout"].calls) == 2


# ── a hand-off chain that completes ─────────────────────────────────────

def test_multi_agent_handoff_chain_completes(monkeypatch, written):
    scripts = {
        "echo": ScriptedClient([
            ModelTurn(tool_calls=[_delegate("d1", "Jarvis", "find the population of Edmonton")]),
            ModelTurn(tool_calls=[_delegate("d2", "Glados", "write edmonton.md with the population 1,010,899")]),
            ModelTurn(tool_calls=[_complete("c3", "Jarvis found the population; Glados wrote edmonton.md.")]),
        ]),
        "scout": ScriptedClient([ModelTurn(tool_calls=[_complete("c1", "Edmonton's population is 1,010,899 (2021 census).")])]),
        "forge": ScriptedClient([
            ModelTurn(tool_calls=[_call("w1", "file_write", path="edmonton.md", content="Population: 1,010,899")]),
            ModelTurn(tool_calls=[_complete("c2", "Wrote edmonton.md.")]),
        ]),
        "_review": _review({"done": True, "summary": "edmonton.md has Edmonton's population."}),
    }
    session, events = _group(monkeypatch, scripts)
    out = session.run("@Echo look up Edmonton's population and save it to edmonton.md")

    assert [e["to"] for e in events if e["type"] == "delegation"] == ["scout", "forge"]
    assert [m["agent_id"] for m in out["messages"] if m["text"]] == ["scout", "forge", "echo"]
    assert written[0]["path"] == "edmonton.md"
    assert session.job is not None and [s.status for s in session.job.subtasks] == ["done", "done"]
    assert out["outcome"]["status"] == "done"


def test_one_to_one_chat_without_handoffs_is_unchanged(monkeypatch):
    scripts = {"echo": ScriptedClient([ModelTurn(content="Paris.")])}
    session, events = _session(monkeypatch, scripts)
    out = session.run("capital of France?", persona_id="echo")

    assert out["response"] == "Paris." and out["outcome"] is None
    assert not [e for e in events if e["type"] in {"run_outcome", "job_continue"}]
    tools = [t["function"]["name"] for t in []]
    assert "complete_task" not in tools


# ── the detectors ───────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "Sure, I'll do that!", "On it.", "Let me check that for you.", "I'm going to write it now.",
    "Will do 👍", "Got it, I'll get started.", "Give me a sec.", "Leave it with me.",
])
def test_promises_are_recognised(text):
    assert is_promise_without_action(text)


@pytest.mark.parametrize("text", [
    "Paris is the capital of France.",
    "Should I write it to notes.md or README.md?",
    "I'll be honest: " + "the answer depends on your budget and timeline. " * 12,  # a long real answer
    "Done: hello.py prints hi.",
    "",
])
def test_answers_and_questions_are_not_promises(text):
    assert not is_promise_without_action(text)


def test_near_duplicates_are_detected():
    assert near_duplicate("We should look at this from another angle first.", "We should look at this from another angle first!")
    assert not near_duplicate("Falcon is a good name.", "Orchard sounds calmer and fits the brand.")


def test_groups_have_no_token_budget_unless_one_is_set(monkeypatch):
    """A team building something works until it's done, not until a token count runs out."""
    monkeypatch.setattr(lean_runtime.settings, "_setting", lambda key, default=None: default)
    assert lean_runtime.settings.group_token_budget() == 0


def test_work_keeps_going_past_the_round_limit_while_it_progresses():
    job = Job(goal="build a zombie shooter game in index.html with waves and a score", max_rounds=2)
    job.rounds = 5
    assert job.backstop() == ""  # last round got something done: keep working
    job.stalls = 1
    assert "limit of 2 rounds" in job.backstop()  # stopped moving: the limit applies again
    job.stalls, job.rounds = 0, 60
    assert "60 rounds" in job.backstop()  # the hard ceiling holds even while progressing


def test_budget_counts_new_tokens_not_the_re_sent_conversation():
    """Live report: 'used its token budget (234,845 of 200,000)' after only a project skeleton.
    Each step re-sends the whole chat; only what a step adds should count."""
    from agent.lean.loop import LeanTurn

    turn = LeanTurn.__new__(LeanTurn)
    turn.usage, turn._last_prompt = {"prompt": 0, "completion": 0, "total": 0, "fresh": 0}, 0
    turn.emit = lambda event: None
    turn.persona = type("P", (), {"name": "Echo"})()
    turn.timeline = []
    steps = [(10_000, 300), (10_400, 250), (10_800, 400)]  # the same chat, a little longer each step
    for prompt, completion in steps:
        usage = {"prompt": prompt, "completion": completion, "total": prompt + completion}
        LeanTurn._account_usage(turn, usage)
    assert turn.usage["total"] == 32_150  # the old measure: the chat counted three times
    assert turn.usage["fresh"] == 10_000 + 300 + 400 + 250 + 400 + 400  # 11,750 actually new


def test_job_backstops_report_their_reason():
    job = Job(goal="x", max_rounds=2, token_budget=100)
    assert job.backstop() == ""
    job.record_text("A", "short", tokens=150)
    assert "token budget" in job.backstop()
    job = Job(goal="x", max_rounds=2, token_budget=0)
    job.rounds = 2
    assert "limit of 2 rounds" in job.backstop()
