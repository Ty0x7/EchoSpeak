"""A turn doesn't stop for "Continue" while it is getting somewhere, and a look-up turn ends in its answer.

Owner report (19 November 2026): "I ran out of steps (60) before finishing … this should never say and stop".
The recorded turn (13:04, "give me the latest updates on the world") had used 6 steps, not 60: the look-up budget
ended it, and the one tool-free call for the answer came back with the whole answer written as reasoning
(qwen3.5:9b, thinking on) and no content, so the turn showed the out-of-steps note with the wrong limit.
"""
from __future__ import annotations

import json
import threading
from typing import Any

from agent.lean.loop import LeanTurn
from agent.lean.personas import AgentPersona
from agent.lean.provider import ModelTurn, ToolCall
from agent.lean.toolbox import NativeTool, Toolbox


class Model:
    """Plays back turns; records whether thinking was on for each call."""

    def __init__(self, turns: list[ModelTurn]) -> None:
        self.turns = list(turns)
        self.calls: list[dict[str, Any]] = []
        self.reasoning_effort = "medium"

    def stream_turn(self, messages, **kwargs):
        self.calls.append({"messages": [dict(m) for m in messages], "tools": list(kwargs.get("tools") or []),
                           "thinking": self.reasoning_effort != "none"})
        turn = self.turns.pop(0)
        if kwargs.get("on_reasoning") and turn.reasoning:
            kwargs["on_reasoning"](turn.reasoning)
        if kwargs.get("on_content") and turn.content:
            kwargs["on_content"](turn.content)
        return turn


def _turn(model: Model, *, max_steps: int, auto: bool, ok=lambda args: True) -> tuple[LeanTurn, list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    box = Toolbox(toolsets=["none"], session_id="t")
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}}
    box.native = {"work": NativeTool(name="work", description="do one piece of work", parameters=schema,
                                     func=lambda args: f"done {args.get('n')}" if ok(args) else "Error: service unavailable")}
    turn = LeanTurn(client=model, persona=AgentPersona(id="echo", name="Echo"), system_prompt="s", history=[], toolbox=box,
                    session_id="t", request_id="r", execution_id="e", emit=events.append, cancel=threading.Event(),
                    persist_tool_runs=False, goal="Do the ten pieces of work")
    turn.max_steps_override = max_steps
    turn.auto_continue = auto
    return turn, events


def _work(n: int) -> ModelTurn:
    return ModelTurn(tool_calls=[ToolCall(f"c{n}", "work", json.dumps({"n": n}))])


def test_a_turn_that_keeps_making_progress_continues_on_its_own():
    model = Model([_work(i) for i in range(10)] + [ModelTurn(content="All ten pieces are done.")])
    turn, events = _turn(model, max_steps=4, auto=True)
    result = turn.run("Do the ten pieces of work")
    assert result.text == "All ten pieces are done." and result.stop_reason != "max_steps"
    assert sum(1 for e in events if e["type"] == "auto_continue") == 2      # 4 → 8 → 12 steps
    assert any(item["kind"] == "note" and "Continued" in item["text"] for item in result.timeline)
    assert "ran out of steps" not in result.text


def test_continuing_also_extends_the_work_budget(tmp_path):
    """Live LT1: the steps went on but the work budget didn't, so the task stopped at "budget" mid-way."""
    from agent.state import StateStore

    ledger = StateStore(tmp_path).coordination
    row = ledger.create(objective="Do the ten pieces of work", agent_id="echo", tools=["work"])
    model = Model([_work(i) for i in range(6)] + [ModelTurn(content="Done.")])
    turn, _events = _turn(model, max_steps=4, auto=True)
    turn.work_context = ledger.claim(row["id"])
    result = turn.run("Do the ten pieces of work")
    assert result.text == "Done."
    assert ledger.get(row["id"])["budget"]["tokens"] == 128000, "the work budget was not extended with the step limit"


def test_a_turn_going_in_circles_still_stops_and_says_its_real_limit():
    model = Model([_work(i) for i in range(12)])
    turn, events = _turn(model, max_steps=4, auto=True, ok=lambda args: False)   # every call fails
    result = turn.run("Do the ten pieces of work")
    assert result.stop_reason == "max_steps" and not any(e["type"] == "auto_continue" for e in events)
    assert result.text.startswith("I haven't finished this yet.") and "step" not in result.text.lower()
    assert any(item["kind"] == "note" and "step limit (4)" in item["text"] for item in result.timeline)


def test_continuing_has_a_ceiling():
    model = Model([_work(i) for i in range(100)])
    turn, events = _turn(model, max_steps=4, auto=True)
    turn.auto_continue_limit = 3                                                  # at most 3 × 4 steps
    result = turn.run("Do the ten pieces of work")
    assert result.stop_reason == "max_steps" and sum(1 for e in events if e["type"] == "auto_continue") == 2
    assert any(item["kind"] == "note" and "step limit (12)" in item["text"] for item in result.timeline)
    assert len(model.calls) == 13    # 12 working steps, then one tool-free handoff call


def test_without_auto_continue_the_limit_is_where_it_was():
    model = Model([_work(i) for i in range(6)])
    turn, events = _turn(model, max_steps=4, auto=False)
    result = turn.run("Do the ten pieces of work")
    assert result.stop_reason == "max_steps" and len(model.calls) == 4


def test_the_look_up_budget_ends_in_the_answer_even_when_the_model_writes_it_as_reasoning():
    answer_in_reasoning = "Ty, here is what's happening: the summit, the grocery cyberattack, and the AI news."
    model = Model([_work(0), _work(1),
                   ModelTurn(content="", reasoning=answer_in_reasoning, finish_reason="stop"),  # grace, thinking on
                   ModelTurn(content="Here's what's happening: the summit, the grocery cyberattack, and the AI news.")])
    turn, _events = _turn(model, max_steps=60, auto=True)
    turn.step_limit = lambda: 2                    # the research budget while it only looks things up
    result = turn.run("give me the latest updates on the world")
    assert result.text.startswith("Here's what's happening") and "ran out of steps" not in result.text
    assert model.calls[2]["thinking"] is False      # the answer call doesn't start another thinking phase


def test_the_grace_answer_asks_with_thinking_off():
    model = Model([_work(0), _work(1), ModelTurn(content="The answer from what I found.")])
    turn, _events = _turn(model, max_steps=60, auto=False)
    turn.step_limit = lambda: 2
    result = turn.run("look it up")
    assert result.text == "The answer from what I found." and model.calls[2]["thinking"] is False


# ── At the limit, the agent says where things stand in its own words (campaign 24 November 2026) ──
def _stalled(handoff: ModelTurn) -> tuple[Model, LeanTurn, list[dict[str, Any]]]:
    model = Model([_work(i) for i in range(4)] + [handoff])
    turn, events = _turn(model, max_steps=4, auto=True, ok=lambda args: False)    # no progress: it stops at 4
    return model, turn, events


def test_the_agent_hands_over_in_plain_words_at_the_limit():
    handoff = ModelTurn(content="The service kept refusing the work, so none of the ten pieces went through. "
                                "Next I'd check whether it is up. Tell me to continue when you want me to retry.")
    model, turn, _events = _stalled(handoff)
    result = turn.run("Do the ten pieces of work")
    assert result.stop_reason == "max_steps" and result.text.startswith("The service kept refusing")
    assert model.calls[-1]["tools"] == [] and model.calls[-1]["thinking"] is False
    assert "Don't mention steps" in model.calls[-1]["messages"][-1]["content"]


def test_a_handoff_that_calls_unfinished_work_done_is_withdrawn():
    model, turn, events = _stalled(ModelTurn(content="All done: the work is complete."))
    result = turn.run("Do the ten pieces of work")
    assert result.text.startswith("I haven't finished this yet.") and "complete" not in result.text
    assert any(e["type"] == "text_replace" for e in events)


def test_a_handoff_claiming_an_unmade_change_is_withdrawn():
    model, turn, _events = _stalled(ModelTurn(content="I've saved the results to report.md; three pieces remain."))
    result = turn.run("Do the ten pieces of work")
    assert result.text.startswith("I haven't finished this yet.") and "report.md" not in result.text


def test_helpers_without_auto_continue_get_no_handoff_call():
    model = Model([_work(i) for i in range(6)])
    turn, _events = _turn(model, max_steps=4, auto=False)
    result = turn.run("Do the ten pieces of work")
    assert len(model.calls) == 4 and result.text.startswith("I haven't finished this yet.")


def test_a_handoff_reporting_unfinished_work_as_working_is_withdrawn():
    """Live PC1/PCH1 (25 November 2026): "The tests pass (12/0 failed)" and "tests are ready" with tests failing."""
    for claim in ("I fixed the CLI. The tests pass (12/0 failed). Next I'll fix search.",
                  "The core logic and tests are ready. Next I'll run them.",
                  "I also confirmed the core implementation looks good; search still needs work."):
        model, turn, _events = _stalled(ModelTurn(content=claim))
        result = turn.run("Do the ten pieces of work")
        assert result.text.startswith("I haven't finished this yet."), claim


def test_an_honest_handoff_about_failing_tests_is_kept():
    honest = ModelTurn(content="I added the CLI, but two tests still fail on search. Next I'll fix search. "
                               "Say “continue” and I'll pick up from here.")
    model = Model([_work(i) for i in range(4)] + [honest])
    turn, _events = _turn(model, max_steps=4, auto=True)          # the work calls succeed (a change was made)
    turn.auto_continue_limit = 1                                  # but there is no second stretch
    assert turn.run("Do the ten pieces of work").text.startswith("I added the CLI")


def test_the_plain_note_names_files_not_paths():
    model = Model([ModelTurn(tool_calls=[ToolCall(f"c{i}", "work", json.dumps({"n": i}))]) for i in range(4)])
    turn, _events = _turn(model, max_steps=4, auto=False)
    turn.timeline.append({"kind": "tool", "label": "Reading /home/me/.cache/run/heval-PC1/notesapp/storage.py"})
    note = turn._out_of_steps_note(4)
    assert "Reading storage.py" in note and "/home/me" not in note
