"""Scrollback blocks at every width: no exceptions and no line wider than the terminal.

A line wider than the terminal is wrapped by the terminal itself, which breaks hanging indents,
rules and diff gutters. Prose also respects the readable measure (layout.measure).
"""
from __future__ import annotations

import re

import pytest

pytest.importorskip("prompt_toolkit")
pytest.importorskip("rich")

from echospeak_cli.tui import blocks as B  # noqa: E402
from echospeak_cli.tui import diffview as DV  # noqa: E402
from echospeak_cli.tui import layout as L  # noqa: E402
from echospeak_cli.tui.motion import width_of  # noqa: E402
from echospeak_cli.tui.theme import ASCII_GLYPHS, Caps  # noqa: E402

ANSI = re.compile(r"\x1b\[[0-9;:]*m")
CAPS = Caps()

CHANGE = DV.parse({
    "op": "edit", "path": "/p/src/dates.py", "display_path": "apps/backend/src/utils/dates.py", "language": "python",
    "added": 2, "removed": 1, "status": "applied", "change_id": "c1",
    "hunks": [{"old_start": 41, "old_lines": 3, "new_start": 41, "new_lines": 4, "lines": [
        [" ", "def iso_week(d):"], ["-", "    if w == 53: return (y, 52)"],
        ["+", "    if w == 53 and not _long_year(y):"], ["+", "        return (y + 1, 1)"]]}]})

BLOCKS = {
    "banner": B.Banner("11.14.0", "Echo", "Personal agent", "qwen3.5:9b (ollama)", "~/Work/echospeak", "echospeak",
                       "ask for risky actions", "Build a feature, fix a bug, or explore your code.",
                       resumed='Resumed "Fix parser" · 14 messages · last active 2h ago'),
    "user": B.UserLine("please fix the ISO week 53 bug in the parser and add tests for long years"),
    "header": B.AgentHeader("jarvis", "Jarvis", "Research agent", delegated_by="Echo"),
    "markdown": B.MarkdownBlock("## Week 53\n\nThe parser treats **week 53** as the previous year.\n\n"
                                "- check `_long_year`\n- add tests\n\n```python\ndef _long_year(y):\n"
                                "    return date(y, 12, 28).isocalendar()[1] == 53\n```", bullet=True),
    "tool": B.ToolLine("ok", "Ran `pytest tests/test_dates.py -k week -x -q`", "3 passed", 1500,
                       ["tests/test_dates.py ...", "3 passed in 0.12s"], more=4),
    "thinking": B.ThinkingLine(8.0, "thinking text"),
    "note": B.Note("⚠", "The backend restarted; your session was kept and the run resumed where it stopped.",
                   "attention", 2),
    "rule": B.RuleLine("Worked for 1m 05s · Changed 1 file · Ran 1 command", gap=True),
    "outcome": B.Outcome("◇", "Finished (not verified)", "bold", "Not verified: steps ran, but no check measured it"),
    "tasks": B.TaskBoard([{"owner": "Jarvis", "task": "Collect ISO 8601 edge cases for week 53", "status": "doing"}]),
    "table": B.Table([["id", "title", "kind", "msgs"], ["a1b2c3d4", "Fix the parser so dates round-trip", "code", "14"]],
                     header=True, title="Sessions"),
    "diff": B.FileDiff(CHANGE, "ok", 120),
}


@pytest.mark.parametrize("name", list(BLOCKS))
@pytest.mark.parametrize("caps", [CAPS, Caps(color=False, ascii=True, glyphs=ASCII_GLYPHS)], ids=["utf8", "ascii"])
def test_block_fits_every_width(name, caps):
    block = BLOCKS[name]
    for width in range(1, 141):
        text = ANSI.sub("", B.render(block, width, caps, verbose=True))
        floor = 8   # below this, a glyph plus one letter is all that can fit; nothing may raise
        if width < floor:
            continue
        for line in text.split("\n"):
            assert width_of(line) <= width, (name, width, line)


def test_prose_measure():
    assert L.measure(240, {}) == L.DEFAULT_MEASURE
    assert L.measure(80, {}) == 80
    assert L.measure(240, {"ECHO_TUI_MEASURE": "0"}) == 240
    assert L.measure(240, {"ECHO_TUI_MEASURE": "140"}) == 140
    assert L.measure(240, {"ECHO_TUI_MEASURE": "nonsense"}) == L.DEFAULT_MEASURE
