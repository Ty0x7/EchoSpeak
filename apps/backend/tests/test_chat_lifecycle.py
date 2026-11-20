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
    model = Model([ModelTurn(content="Done. I've sent the email to Sam.")])
    turn, _run, events = _chat_turn(model, "email the report to Sam", email_send=lambda a: "sent")
    result = turn.run("email the report to Sam")
    assert len(model.calls) == 1 and not _erased(events)
    assert result.stop_reason == "unverified_claim" and any(e["type"] == "claim_unverified" for e in events)


def test_an_untrue_memory_claim_in_chat_is_made_true_without_rewriting_the_answer():
    """Evaluation LS3 (19 November): "I've saved that so I'll remember it" with no memory_save call. Chat noted it
    under the answer and the fact was lost; the next chat recalled the outdated value. One follow-up step now saves it;
    the answer stays as it was streamed."""
    saved = []
    model = Model([ModelTurn(content="Got it, your build server is now atlas-30. I've saved that."),
                   ModelTurn(tool_calls=[ToolCall("m1", "memory_save", json.dumps({"fact": "Build server is atlas-30",
                                                                                      "key": "build_server"}))]),
                   ModelTurn(content="Saved.")])
    turn, _run, events = _chat_turn(model, "my build server is now atlas-30, remember that instead",
                                    memory_save=lambda a: saved.append(a) or "Saved")
    turn.memory_repair = True
    result = turn.run("my build server is now atlas-30, remember that instead")
    assert saved and saved[0]["key"] == "build_server"                        # the save really happened
    assert not _erased(events)                                                  # the answer was not wiped
    assert result.text.startswith("Got it, your build server is now atlas-30")
    assert result.stop_reason != "unverified_claim"
    note = str(model.calls[1]["messages"][-1]["content"])
    assert "memory_save" in note and "key" in note
    assert sum(1 for e in events if e["type"] == "memory_repair") == 1


def test_an_unbacked_update_claim_is_repaired_when_the_user_asked_to_remember():
    """Evaluation LS3 r2 (ctx-after2, 19 November): "I've updated that the Saffron build server is now vega-97" names
    no memory word, so the repair didn't run; the user had said "Please remember that instead"."""
    saved = []
    model = Model([ModelTurn(content="Got it, I've updated that the Saffron build server is now vega-97."),
                   ModelTurn(tool_calls=[ToolCall("m1", "memory_save", json.dumps({"fact": "Saffron build server is vega-97",
                                                                                      "key": "saffron_build_server"}))]),
                   ModelTurn(content="")])
    ask = "Update: the Saffron build server is now vega-97. Please remember that instead."
    turn, _run, events = _chat_turn(model, ask, memory_save=lambda a: saved.append(a) or "Saved")
    turn.memory_repair = True
    turn.run(ask)
    assert saved and any(e["type"] == "memory_repair" for e in events)
    # Without a request to remember, an "updated" claim is only noted, as before.
    model = Model([ModelTurn(content="Got it, I've updated the config.")])
    turn, _run, events = _chat_turn(model, "the config looks off", memory_save=lambda a: "Saved")
    turn.memory_repair = True
    assert turn.run("the config looks off").stop_reason == "unverified_claim" and len(model.calls) == 1


def test_an_unbacked_update_of_a_recalled_fact_is_repaired():
    """Evaluation LSH2 r1 (ctx-after2, held-out): "FYI my Riverside dentist appointment got moved to Monday" with the
    saved appointment recalled on the request; "I've updated your Riverside dentist appointment" saved nothing."""
    saved = []
    model = Model([ModelTurn(content="Got it. I've updated your Riverside dentist appointment to Monday at 9:30."),
                   ModelTurn(tool_calls=[ToolCall("m1", "memory_save", json.dumps({"fact": "Riverside dental appointment "
                                                                                      "is Monday at 9:30",
                                                                                      "key": "dental_appointment"}))]),
                   ModelTurn(content="")])
    ask = "FYI my Riverside dentist appointment got moved to Monday at 9:30."
    turn, _run, events = _chat_turn(model, ask, memory_save=lambda a: saved.append(a) or "Saved")
    turn.memory_repair = True
    turn.request_note = memory_quality.render([{"content": "Riverside dental clinic appointment is on Wednesday at 14:15.",
                                                "scope": "account", "semantic_key": "dental_appointment"}])
    turn.run(ask)
    assert saved and saved[0]["key"] == "dental_appointment"


def _save_then_quiet(fact: str, key: str) -> list[ModelTurn]:
    return [ModelTurn(tool_calls=[ToolCall("m1", "memory_save", json.dumps({"fact": fact, "key": key}))]), ModelTurn(content="")]


def _recalled(content: str, key: str) -> str:
    return memory_quality.render([{"content": content, "scope": "account", "semantic_key": key}])


def test_a_change_to_a_recalled_fact_is_saved_even_when_nothing_is_claimed():
    """Memory ×4 run, LS6 r2 (19 November): "I switched to the 7:40 Crosstown train" got "That sounds like a good
    move" and no save, with the 6:55 train recalled on the request; the next chat gave 6:55."""
    saved = []
    ask = "I switched to the 7:40 Crosstown train this week, the earlier one was always late."
    model = Model([ModelTurn(content="That sounds like a good move.")] + _save_then_quiet("Takes the 7:40 Crosstown train", "work_train"))
    turn, _run, events = _chat_turn(model, ask, memory_save=lambda a: saved.append(a) or "Saved")
    turn.memory_repair = True
    turn.request_note = _recalled("Takes the 6:55 AM Crosstown train to work.", "work_train")
    result = turn.run(ask)
    assert saved and saved[0]["key"] == "work_train" and result.text.startswith("That sounds like a good move.")
    assert "work_train" in str(model.calls[1]["messages"][-1]["content"])      # the note names the key to use


def test_a_vague_notes_claim_about_a_moved_friend_is_made_true():
    """LSH3 r2: "Oh, Ines moved to Gdansk last month." → "I've updated that in my notes.", nothing saved."""
    saved = []
    ask = "Oh, Ines moved to Gdansk last month."
    model = Model([ModelTurn(content="Got it. I've updated that in my notes.")] + _save_then_quiet("Friend Ines lives in Gdansk", "friend_ines_location"))
    turn, _run, _events = _chat_turn(model, ask, memory_save=lambda a: saved.append(a) or "Saved")
    turn.memory_repair = True
    turn.request_note = _recalled("Friend Ines lives in Valencia.", "friend_ines_location")
    turn.run(ask)
    assert saved


def test_remember_answered_with_only_a_reminder_is_also_saved():
    """LSH2 r1: "Remember: my appointment … is on Monday at 16:45." set a reminder and saved nothing."""
    saved = []
    ask = "Remember: my appointment at the Riverside dental clinic is on Monday at 16:45."
    model = Model([ModelTurn(tool_calls=[ToolCall("r1", "set_reminder", json.dumps({"when": "2026-11-23T16:45"}))]),
                   ModelTurn(content="Done! I've set a reminder for Monday at 4:45 PM.")] + _save_then_quiet("Dental appointment Monday 16:45", "dental_appointment"))
    turn, _run, _events = _chat_turn(model, ask, memory_save=lambda a: saved.append(a) or "Saved",
                                     set_reminder=lambda a: "Reminder set")
    turn.memory_repair = True
    turn.run(ask)
    assert saved


def test_questions_and_unrelated_changes_never_ask_for_a_save():
    for ask, note in (("What's the weather in Edmonton now?", _recalled("Lives in Edmonton.", "home_city")),
                      ("I switched the app to dark mode.", _recalled("Takes the 6:55 AM Crosstown train to work.", "work_train")),
                      ("Did Ines move to Gdansk?", _recalled("Friend Ines lives in Valencia.", "friend_ines_location"))):
        model = Model([ModelTurn(content="Sure.")])
        turn, _run, events = _chat_turn(model, ask, memory_save=lambda a: "Saved")
        turn.memory_repair = True
        turn.request_note = note
        turn.run(ask)
        assert len(model.calls) == 1 and not any(e["type"] == "memory_repair" for e in events), ask


def test_the_memory_repair_happens_once_and_never_for_other_claims():
    model = Model([ModelTurn(content="I've saved that."), ModelTurn(content="I've saved it, really.")])
    turn, _run, events = _chat_turn(model, "remember I like tea", memory_save=lambda a: "Saved")
    turn.memory_repair = True
    result = turn.run("remember I like tea")
    assert len(model.calls) == 2 and result.stop_reason == "unverified_claim"   # asked once, then noted as before


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
    assert "The agents, all of them: Echo" in facts and "is the harness" in facts and "https://github.com/Ty0x7/EchoSpeak" in facts


def test_official_sources_win_for_rules_and_regulation():
    from agent.web_search_providers import SearchHit as Hit
    hits = [Hit(title="Alberta daylight saving 2026 explained", url="https://blog.example.com/alberta-dst", snippet="Alberta daylight saving 2026"),
            Hit(title="Daylight saving time", url="https://www.alberta.ca/daylight-saving-time", snippet="Alberta daylight saving time rules")]
    assert search_rank.rank_hits("Alberta daylight saving time rules 2026", [hits])[0].url == "https://www.alberta.ca/daylight-saving-time"


def test_recalled_memories_carry_when_and_where_they_were_saved():
    text = memory_quality.render([{"content": "Prefers metric units.", "scope": "account", "saved_at": "2026-10-20"},
                                  {"content": "Uses pnpm in this repo.", "scope": "project", "saved_at": "2026-10-21"}])
    assert "Prefers metric units. (saved 2026-10-20)" in text and "Uses pnpm in this repo. (saved 2026-10-21, this project)" in text


def test_recalled_memories_show_their_key_so_a_change_replaces_them():
    """Evaluation LSH2 (19 November): "my appointment got moved to Thursday" set a reminder and left the saved
    appointment as it was, so the next chat answered with the old day. The key is shown, and the section says a
    change is saved under the same key (it replaces the old value: agent/memory.py supersession)."""
    text = memory_quality.render([{"content": "Dental appointment at Northgate on Wednesday at 9:30.", "scope": "account",
                                   "saved_at": "2026-11-19", "semantic_key": "dental_appointment"},
                                  {"content": "Build key for forge.", "scope": "account", "semantic_key": "agent:forge:build_key"}])
    assert "(saved 2026-11-19, key dental_appointment)" in text
    assert "key build_key" in text and "agent:forge" not in text
    assert "has changed" in text and "same key" in text


def test_sports_times_say_whose_clock_they_are_on():
    from agent.sports_espn import _local_time
    assert _local_time("2026-10-30T01:00:00Z", zone=True).endswith("(your time)")
    assert "your time" not in _local_time("2026-10-30T01:00:00Z")  # cards stay short


def test_release_lookup_requires_a_request_and_keeps_evidence_for_separate_turns():
    from agent.state import get_state_store

    paste = "The backup restored successfully. See EchoSpeak release notes in backup-log.md."
    assert chat_run.ChatRun(session_id="t", user_message=paste).prefetch() == []
    ask_text = "What’s new in EchoSpeak?"
    store = get_state_store()
    ids = set()
    for _ in range(2):
        model = Model([ModelTurn(content="Here is the release history.")])
        turn, run, _ = _chat_turn(model, ask_text)
        execution = store.create_execution(thread_id="prefetch-evidence", query=ask_text)
        turn.execution_id = execution.id
        turn.persist_tool_runs = True
        turn.prefetch = run.prefetch
        turn.run(ask_text)
        tool_runs = store.list_tool_runs(execution.id)
        assert len(tool_runs) == 2
        current = {row.id for row in tool_runs}
        assert not current & ids
        ids.update(current)


def test_an_unfulfilled_same_turn_promise_ends_with_one_failure():
    ask_text = "Search for the Widget 9 manual."
    model = Model([ModelTurn(content="I'll search for it now.") for _ in range(3)])
    turn, _, events = _chat_turn(model, ask_text)
    result = turn.run(ask_text)
    assert result.stop_reason == "promise_unfulfilled"
    assert not result.success
    assert "couldn’t complete" in result.text
    assert "I'll search" not in result.text
    assert sum(e["type"] == "promise_nudge" for e in events) == 2
