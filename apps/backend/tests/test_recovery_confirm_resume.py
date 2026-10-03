"""Recovery-evidence honesty, confirmation-resume, and corrupted-write safety."""

from __future__ import annotations

import re
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent.mode_controller import (
    CodingPhaseName,
    TurnMode,
    _intent_relation,
    classify_turn_mode,
)
from agent.active_work import ActiveWorkState


# ---------------------------------------------------------------------------
# Confirm phrase matching
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        "yes",
        "yes proceed with the changes",
        "yes proceed",
        "proceed with the changes",
        "okay do it",
        "yes, please proceed with the changes",
    ],
)
def test_intent_relation_confirm_phrases(text):
    assert _intent_relation(text, continues=True, explicit_new=False) == "confirm"


def test_confirm_on_active_project_enters_implement_phase():
    active = ActiveWorkState(
        thread_id="t1",
        kind="coding_project",
        phase="inspect",
        project_path=r"C:\Users\ty0x7\Desktop\2d-shooter-game",
        project_name="2d-shooter-game",
        goal="make player move and shoot",
    )
    decision = classify_turn_mode("yes proceed with the changes", active_work=active)
    assert decision.mode == TurnMode.CODING
    assert decision.intent_relation == "confirm"
    assert decision.coding_phase == CodingPhaseName.IMPLEMENT


# ---------------------------------------------------------------------------
# Recovery claim honesty
# ---------------------------------------------------------------------------

def _minimal_agent_for_honesty():
    """Build a thin object that only needs recovery/mutation honesty methods."""
    from agent.core import EchoSpeakAgent

    # Avoid full __init__ if possible — use unbound methods on a SimpleNamespace
    # with the required attributes.
    agent = object.__new__(EchoSpeakAgent)
    agent._current_execution_id = "exec-test-1"
    agent._partial_tool_results = []
    agent._pending_action = None
    agent._state_store = MagicMock()
    agent._state_store.list_tool_runs.return_value = []
    return agent


def test_content_has_unresolved_edit_markers():
    agent = _minimal_agent_for_honesty()
    clean = "function update() { player.x += 1; }"
    dirty = (
        "<<<<<<< SEARCH\nfunction update() {}\n=======\n"
        "function update() { player.x += 1; }\n>>>>>>> REPLACE\n"
    )
    assert agent._content_has_unresolved_edit_markers(clean) is False
    assert agent._content_has_unresolved_edit_markers(dirty) is True


