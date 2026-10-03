"""Referential search retry routing + false search-claim rejection."""
from __future__ import annotations

from agent.mode_controller import (
    TurnMode,
    allowed_tools_for_mode,
    classify_turn_mode,
    is_search_retry_utterance,
    _intent_relation,
)


def test_search_retry_utterances_are_detected():
    assert is_search_retry_utterance("can you try again with that search!")
    assert is_search_retry_utterance("retry the search please")
    assert is_search_retry_utterance("search it again")
    assert is_search_retry_utterance("sorry im from edmonton remember that! and retry the search...")
    assert not is_search_retry_utterance("what time is it")


def test_intent_relation_retry_for_search_phrases():
    assert (
        _intent_relation(
            "can you try again with that search! im using a different model",
            continues=False,
            explicit_new=False,
        )
        == "retry"
    )


def test_classify_search_retry_as_task_research_with_tools():
    text = "can you try again with that search! im using a different model lets see if it will work now..."
    decision = classify_turn_mode(text)
    assert decision.mode == TurnMode.TASK_RESEARCH
    assert decision.intent_relation == "retry"
    names = ["web_search", "sports_live", "calculate", "get_system_time", "system_info", "file_read"]
    allowed = allowed_tools_for_mode(decision, names)
    assert "web_search" in allowed
    assert "file_read" not in allowed


