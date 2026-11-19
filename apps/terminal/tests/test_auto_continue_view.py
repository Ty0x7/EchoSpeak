"""The backend's auto_continue event (a turn carrying on past its step limit) shows as one quiet line."""
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


def test_the_tui_shows_that_the_turn_kept_going():
    blocks = Run("r", 0.0, glyphs=Glyphs()).feed(EVENT, 1.0)
    assert len(blocks) == 1 and isinstance(blocks[0], B.RuleLine)
    assert "Kept going" in blocks[0].text and "120" in blocks[0].text


def test_the_plain_renderer_shows_it_too():
    out = "".join(Renderer().feed(EVENT))
    assert "kept going" in out and "120 steps" in out
