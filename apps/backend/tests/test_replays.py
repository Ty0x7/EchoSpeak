"""Replay cases from real chats (tests/replays/*.json, agent/lean/replays.py).

Without a model: the recorded turns still play through the harness, the case's
expectations flag that recorded run as a failure (so the case really catches the
bug), and the hand-written fixed run passes. Live runs against your model are
scripts/replay_eval.py.
"""
from __future__ import annotations

import pytest

from agent.lean import replays

CASES = replays.load_cases()


def test_there_are_replay_cases():
    assert CASES, "tests/replays should hold at least one case"


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_case_file_is_usable(case):
    assert replays.check_case(case) is None
    assert case.recorded_turns, "a case records what went wrong"


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_recorded_failure_replays_and_is_caught(case):
    outcome = replays.run_case(case, replays.ScriptedTurns(case.recorded_turns))
    assert not outcome.error
    recorded_tools = [call["name"] for turn in case.recorded_turns for call in turn.get("tool_calls") or []]
    assert outcome.tools == recorded_tools
    assert replays.score(case, outcome), "the expectations should flag the recorded failure"


@pytest.mark.parametrize("case", [c for c in CASES if c.fixed_turns], ids=[c.name for c in CASES if c.fixed_turns])
def test_fixed_run_passes(case):
    outcome = replays.run_case(case, replays.ScriptedTurns(case.fixed_turns))
    assert replays.score(case, outcome) == []


def test_capture_turns_a_saved_chat_into_a_case():
    from types import SimpleNamespace

    execution = SimpleNamespace(id="t1", query="can you search latest ai news", created_at=1)
    item = SimpleNamespace(item_type="assistant_message", payload={"timeline": [
        {"kind": "tool", "step": 1, "name": "web_search", "args": {"query": "latest AI news"}, "output": "Web search failed."},
        {"kind": "text", "step": 2, "text": "Sorry, I can't search."},
    ]})
    store = SimpleNamespace(list_executions=lambda session_id, limit=50: [execution], list_items=lambda turn_id: [item])
    case = replays.capture("chat-1", "demo", {"tools_any": ["ask_user"]}, store=store)
    assert case["user"] == "can you search latest ai news"
    assert case["tool_outputs"] == {"web_search": "Web search failed."}
    assert case["recorded_turns"] == [
        {"content": "", "tool_calls": [{"name": "web_search", "arguments": {"query": "latest AI news"}}]},
        {"content": "Sorry, I can't search.", "tool_calls": []},
    ]
