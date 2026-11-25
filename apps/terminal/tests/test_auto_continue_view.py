"""The backend's auto_continue event (a turn carrying on past its step limit) is internal: verbose mode shows it."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from echospeak_cli.render import Renderer  # noqa: E402
from echospeak_cli.tui import blocks as B  # noqa: E402
from echospeak_cli.tui.state import Run  # noqa: E402
from echospeak_cli.tui.theme import Glyphs  # noqa: E402

EVENT = {"type": "auto_continue", "step": 60, "limit": 120, "note": "Continued automatically after 60 steps.",
         "message_id": "m"}


def test_the_tui_keeps_it_out_of_the_conversation():
    assert Run("r", 0.0, glyphs=Glyphs()).feed(EVENT, 1.0) == []


def test_verbose_mode_shows_that_the_turn_kept_going():
    run = Run("r", 0.0, glyphs=Glyphs())
    run.verbose = True
    blocks = run.feed(EVENT, 1.0)
    assert len(blocks) == 1 and isinstance(blocks[0], B.Note)
    assert "Kept going" in blocks[0].text and "120" in blocks[0].text


def test_the_plain_renderer_shows_it_too():
    out = "".join(Renderer().feed(EVENT))
    assert "kept going" in out and "120 steps" in out


def _done(stop: str, verbose: bool = False):
    run = Run("r", 0.0, glyphs=Glyphs())
    run.verbose = verbose
    run.feed({"type": "agent_start", "message_id": "m", "agent": {"id": "echo", "name": "Echo"}}, 0.5)
    run.feed({"type": "agent_token", "message_id": "m", "step": 1, "data": "I haven't finished this yet."}, 0.6)
    blocks = run.feed({"type": "agent_done", "message_id": "m", "success": True, "stop_reason": stop}, 1.0)
    return run, " ".join(getattr(b, "text", "") for b in blocks)


def test_the_step_limit_is_said_once_in_plain_words():
    run, shown = _done("max_steps")
    assert "step limit" not in shown and "max_steps" not in shown
    assert run.unfinished
    from echospeak_cli.tui.state import outcome_blocks
    line = outcome_blocks(outcome=None, work=None, journal="", success=True, error="", caps_glyphs=Glyphs(),
                          unfinished=True)
    assert line and "Not finished" in line[0].headline and "continue" in line[0].headline


def test_internal_stop_codes_stay_out_of_the_conversation():
    for stop in ("unverified_claim", "refusals", "promise_unfulfilled", "deterministic_reply", "budget"):
        _run, shown = _done(stop)
        assert stop not in shown and "stopped:" not in shown
    _run, verbose = _done("unverified_claim", verbose=True)
    assert "unverified_claim" in verbose


def test_a_repeating_model_is_said_plainly():
    _run, shown = _done("model_repetition")
    assert "repeating itself" in shown and "model_repetition" not in shown
