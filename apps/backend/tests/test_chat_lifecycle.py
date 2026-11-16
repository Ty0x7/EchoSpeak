"""Chat's generation lifecycle, from the live Gemma 4B test (11.11.3).

Reported: a long thinking phase, an answer starts streaming, the answer is cut off, a second
thinking phase begins ("reminding itself how to respond"), and a new answer repeats or changes
the first, sometimes apologising for criticism the user never made.

Causes found in the loop: (1) the citation check erased the streamed answer and asked the
model to rewrite it, in a user-role message; (2) the claim guard did the same; (3) an answer
cut off by the output limit was either accepted half-finished, or, when reasoning had used the
whole budget, thrown away and restarted with another full thinking phase.

These tests run the real loop with a scripted model and record every model call and stream event.
"""
from __future__ import annotations

import json
import threading
from typing import Any

from agent import search_rank
from agent.lean import chat_mode, chat_run, memory_quality
from agent.lean.loop import LeanTurn, add_note, app_note
from agent.lean.personas import AgentPersona
from agent.lean.provider import ModelTurn, ToolCall
from agent.lean.toolbox import NativeTool, Toolbox

PAGE = "https://vendor.example/widget-9"


class Model:
    """Plays back turns; records each call's messages, tools and whether thinking was on."""

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


def _fetch(args):
    return ("execution_status=success\nresult_state=data_found\n" + json.dumps({"url": args.get("url"), "text": "Widget 9 ships on 3 November for $499."})
            + "\nquality=" + json.dumps({"status": "ok", "relevance": 1.0, "source": "page"}))


def _chat_turn(model: Model, user: str, **tools: Any) -> tuple[LeanTurn, chat_run.ChatRun, list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    box = Toolbox(toolsets=["none"], session_id="t")
    schema = {"type": "object", "properties": {}}
    box.native = {"safe_web_fetch": NativeTool(name="safe_web_fetch", description="read", parameters=schema, func=_fetch, parallel_safe=True)}
    for name, func in tools.items():
        box.native[name] = NativeTool(name=name, description=name, parameters=schema, func=func)
    run = chat_run.ChatRun(session_id="t", profile="compact", user_message=user)
    turn = LeanTurn(client=model, persona=AgentPersona(id="chat", name="gemma"), system_prompt="s", history=[], toolbox=box,
                    session_id="t", request_id="r", execution_id="e", emit=events.append, cancel=threading.Event(), persist_tool_runs=False)
    # As LeanSession wires a Chat turn.
    turn.tool_observer = run.observe
    turn.answer_review = run.review
    turn.claim_nudge_limit = 0
    turn.max_steps_override = run.budget
    turn.final_grace = True
    turn.grace_prompt = chat_run.GRACE_PROMPT
    return turn, run, events


def _fetch_call() -> ModelTurn:
    return ModelTurn(tool_calls=[ToolCall("f1", "safe_web_fetch", json.dumps({"url": PAGE}))])


def _erased(events: list[dict[str, Any]]) -> bool:
    return any(e["type"] == "text_replace" and not e.get("text") for e in events)


def test_an_unverified_link_is_fixed_in_place_with_no_second_generation():
    invented = "https://made-up.example/report"
    model = Model([_fetch_call(), ModelTurn(content=f"It ships 3 November ([vendor]({PAGE}), [analysis]({invented})).", reasoning="Plan the answer.")])
    turn, run, events = _chat_turn(model, "When does widget 9 ship?")
    result = turn.run("When does widget 9 ship?")
    assert len(model.calls) == 2  # fetch, then the answer: no rewrite call, no second thinking phase
    assert not _erased(events)  # the streamed answer is never wiped
    assert result.text == f"It ships 3 November ([vendor]({PAGE}), analysis)."  # the invented link loses its target only
    notes = [e["note"] for e in events if e["type"] == "answer_note"]
    assert notes and "made-up.example" in notes[0]
    assert any(item["kind"] == "note" for item in result.timeline)  # persisted: shows again after reload
    assert run.trace(answer=result.text)["review"]["links_removed"] == 1


def test_an_untrue_claim_in_chat_is_noted_not_rewritten():
    model = Model([ModelTurn(content="Done. I've saved that to your memory.")])
    turn, _run, events = _chat_turn(model, "remember I like tea", memory_save=lambda a: "saved")
    result = turn.run("remember I like tea")
    assert len(model.calls) == 1 and not _erased(events)
    assert result.stop_reason == "unverified_claim" and any(e["type"] == "claim_unverified" for e in events)


def test_an_answer_cut_off_by_the_output_limit_continues_without_thinking_again():
    model = Model([ModelTurn(content="Widget 9 ships on 3 November. It costs", reasoning="Long reasoning…", finish_reason="length"),
                   ModelTurn(content=" $499 and needs firmware 2.", finish_reason="stop")])
    turn, _run, events = _chat_turn(model, "Tell me about widget 9")
    result = turn.run("Tell me about widget 9")
    assert result.text == "Widget 9 ships on 3 November. It costs\n\n $499 and needs firmware 2." or "It costs" in result.text and "$499" in result.text
    assert [c["thinking"] for c in model.calls] == [True, False]  # the continuation doesn't think again
    assert model.reasoning_effort == "medium"  # restored afterwards
    second = model.calls[1]["messages"]
    assert "<echospeak-note>" in str(second[-1]["content"]) and second[-1]["role"] == "user"
    assert not _erased(events) and any(e["type"] == "answer_continue" for e in events)


def test_reasoning_that_used_the_whole_budget_is_answered_from_not_restarted():
    model = Model([ModelTurn(content="", reasoning="The user wants the ship date. The vendor page says 3 November.", finish_reason="length"),
                   ModelTurn(content="Widget 9 ships on 3 November.")])
    turn, _run, _events = _chat_turn(model, "When does widget 9 ship?")
    result = turn.run("When does widget 9 ship?")
    assert result.text == "Widget 9 ships on 3 November."
    assert [c["thinking"] for c in model.calls] == [True, False]
    note = str(model.calls[1]["messages"][-1]["content"])
    assert "Write the answer now" in note and "The vendor page says 3 November" in note
    roles = [m["role"] for m in model.calls[1]["messages"][1:]]
    assert all(a != b or a == "tool" for a, b in zip(roles, roles[1:]))  # user/assistant alternation kept (Gemma)


def test_app_notes_never_look_like_the_user_and_keep_alternation():
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}]
    add_note(messages, "continue")
    assert len(messages) == 2 and messages[-1]["content"].endswith(app_note("continue"))
    messages.append({"role": "assistant", "content": "I'll check."})
    add_note(messages, "act now")
    assert messages[-1] == {"role": "user", "content": "<echospeak-note>act now</echospeak-note>"}
    rules = chat_mode.CHAT_RULES + chat_mode.CHAT_RULES_COMPACT
    assert rules.count("<echospeak-note>") == 2 and "never apologise to the user for it" in rules


def test_figures_not_in_the_sources_are_flagged_after_research():
    model = Model([_fetch_call(), ModelTurn(content=f"It ships 3 November for $499 ([vendor]({PAGE})), with 12.5% faster charging.")])
    turn, run, events = _chat_turn(model, "widget 9 price")
    turn.run("widget 9 price")
    notes = [e["note"] for e in events if e["type"] == "answer_note"]
    assert notes == ["Not found in the sources read: 12.5%. Treat these figures as unverified."]
    assert run.reviewed["figures_unverified"] == ["12.5%"]
    assert chat_run.unsupported_figures("about 15 users in 2026, step 3", ["nothing"], "") == []


def test_capabilities_name_real_tools_and_files_are_described_truthfully():
    section = chat_mode.capability_section({"weather_live", "sports_live", "web_search", "safe_web_fetch"}, False)
    assert "weather_live, sports_live" in section and "tools to call by name, not skills to load" in section
    assert "creating PDF or Office files" in section
    assert "Chat can't create PDF, Word or Excel files" in chat_mode.CHAT_RULES
    facts = chat_mode.app_facts()
    assert "Echo coordinates" in facts and "https://github.com/Ty0x7/EchoSpeak" in facts


def test_official_sources_win_for_rules_and_regulation():
    from agent.web_search_providers import SearchHit as Hit
    hits = [Hit(title="Alberta daylight saving 2026 explained", url="https://blog.example.com/alberta-dst", snippet="Alberta daylight saving 2026"),
            Hit(title="Daylight saving time", url="https://www.alberta.ca/daylight-saving-time", snippet="Alberta daylight saving time rules")]
    assert search_rank.rank_hits("Alberta daylight saving time rules 2026", [hits])[0].url == "https://www.alberta.ca/daylight-saving-time"


def test_recalled_memories_carry_when_and_where_they_were_saved():
    text = memory_quality.render([{"content": "Prefers metric units.", "scope": "account", "saved_at": "2026-10-20"},
                                  {"content": "Uses pnpm in this repo.", "scope": "project", "saved_at": "2026-10-21"}])
    assert "Prefers metric units. (saved 2026-10-20)" in text and "Uses pnpm in this repo. (saved 2026-10-21, this project)" in text


def test_sports_times_say_whose_clock_they_are_on():
    from agent.sports_espn import _local_time
    assert _local_time("2026-10-30T01:00:00Z", zone=True).endswith("(your time)")
    assert "your time" not in _local_time("2026-10-30T01:00:00Z")  # cards stay short
