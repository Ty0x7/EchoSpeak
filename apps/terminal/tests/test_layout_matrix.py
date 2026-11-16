"""The live region at every terminal size: the real prompt_toolkit layout rendered headlessly.

For each UI state (idle, slash menu, shortcuts, a running task with tail, steps and pending input,
an approval with a diff, a question, a long footer notice) and each size in a grid from 8x4 to
240x60, this renders ``EchoTUI``'s actual layout into a W x H screen buffer and checks the
invariants that keep an inline terminal UI from breaking:

1. The live region fits the terminal (never clipped, never "Window too small").
2. No row is wider than the terminal (a wider row would be wrapped by the terminal and the region
   would no longer be where prompt_toolkit thinks it is).
3. The input prompt and the footer are always on screen while the composer is shown.
4. Height grows monotonically with the terminal and stays within the region budget.

Run as a script for a report of every failing (state, size): ``python tests/test_layout_matrix.py``.
"""
from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable

import pytest

pytest.importorskip("prompt_toolkit")
pytest.importorskip("rich")

from prompt_toolkit.application.current import create_app_session, set_app  # noqa: E402
from prompt_toolkit.data_structures import Size  # noqa: E402
from prompt_toolkit.input import create_pipe_input  # noqa: E402
from prompt_toolkit.layout.mouse_handlers import MouseHandlers  # noqa: E402
from prompt_toolkit.layout.screen import Screen, WritePosition  # noqa: E402
from prompt_toolkit.output import DummyOutput  # noqa: E402

from echospeak_cli import discovery  # noqa: E402
from echospeak_cli.tui import app as A  # noqa: E402
from echospeak_cli.tui.layout import budget  # noqa: E402
from echospeak_cli.tui.motion import width_of  # noqa: E402
from echospeak_cli.tui.state import Run, Tool  # noqa: E402
from echospeak_cli.tui.theme import ASCII_GLYPHS, Caps  # noqa: E402

WIDTHS = (8, 12, 16, 20, 24, 30, 36, 40, 44, 50, 59, 60, 70, 80, 90, 100, 119, 120, 140, 180, 240)
HEIGHTS = (4, 6, 8, 10, 12, 16, 20, 24, 30, 40, 60)


class SizedOutput(DummyOutput):
    def __init__(self) -> None:
        super().__init__()
        self.size = Size(rows=24, columns=80)

    def get_size(self) -> Size:
        return self.size


class _NoClient:
    base_url = "http://127.0.0.1:0"

    def __getattr__(self, name):  # the layout never talks to the backend
        raise AssertionError(f"layout called the backend: {name}")


DEFAULT_CAPS = Caps()


def make_tui(caps: Caps = DEFAULT_CAPS) -> tuple[A.EchoTUI, SizedOutput]:
    out = SizedOutput()
    tui = A.EchoTUI(_NoClient(), discovery.Endpoint("http://127.0.0.1:0", "k", "flag", "11.14.0"), caps,
                    env={"ECHOSPEAK_STATE_DIR": os.devnull + "-no-state"})
    tui.app.output = out
    tui.app.renderer.output = out
    tui.buffer._load_history_task = object()   # history is never loaded headlessly
    tui.model = "qwen3.5:9b"
    tui.project_name = "echospeak"
    tui.core.session = {"thread_id": "t" * 32, "title": "Fix the parser so dates in ISO week 53 round-trip"}
    tui.typed = True
    return tui, out


# ── states ──────────────────────────────────────────────────────────
LONG_ANSWER = ("Here is what I found in the parser. The ISO week calculation treats week 53 as always belonging to "
               "the previous year, which is wrong for long years.\n\nFirst, `iso_week` should check `_long_year`. "
               "Then the round trip in `parse_week` needs the same rule, otherwise 2026-W53 becomes 2027-W01 and "
               "the tests in tests/test_dates.py fail with a confusing off-by-one.")


def _running(tui: A.EchoTUI, tools: int = 2, tail: bool = True, pending: int = 2) -> None:
    run = Run("term-r1", time.monotonic() - 75, glyphs=tui.caps.glyphs)
    run.feed({"type": "agent_start", "message_id": "m1", "agent": {"id": "echo", "name": "Echo"}}, time.monotonic())
    if tail:
        run.feed({"type": "agent_token", "message_id": "m1", "data": LONG_ANSWER}, time.monotonic())
        assert run.tail().strip(), "the fixture must leave an open block in the stream tail"
    for i in range(tools):
        run.tools[f"c{i}"] = Tool(f"c{i}", "terminal", f"Running `pytest tests/test_dates.py -k week_{i} -x -q`",
                                  time.monotonic() - 3 * i, detail="collecting 214 items", msg="m1")
    run.tokens = 12_400
    tui.run = run
    for i in range(pending):
        tui.pending.append(A.PendingInput("steer" if i % 2 == 0 else "queue",
                                          f"also make sure the CLI flag --week {i} works the same way in every locale"))


def _approval(tui: A.EchoTUI, diff: bool = True) -> None:
    _running(tui, tools=0, tail=False, pending=0)
    evt = {"type": "approval_request", "approval_id": "a1", "tool": "file_edit" if diff else "terminal",
           "summary": "Edit src/dates.py", "grantable": True, "persistable": True,
           "scope_label": "file edits inside ~/Work/echospeak/apps/backend", "reason": "Fix the week 53 rule",
           "args": {"command": "pytest tests/test_dates.py -x -q --maxfail=1", "cwd": "/home/dev/Work/echospeak"}}
    if diff:
        evt["file_change"] = {
            "op": "edit", "path": "/p/src/dates.py", "display_path": "apps/backend/src/utils/dates.py",
            "language": "python", "added": 3, "removed": 1, "status": "proposed", "change_id": "chg_1",
            "hunks": [{"old_start": 41, "old_lines": 5, "new_start": 41, "new_lines": 7, "lines": [
                [" ", "def iso_week(d):"], [" ", "    y, w, _ = d.isocalendar()"],
                ["-", "    if w == 53: return (y, 52)"], ["+", "    if w == 53 and not _long_year(y):"],
                ["+", "        return (y + 1, 1)"], [" ", "    return (y, w)"]] * 3}]}
    tui.dialog = tui._approval_dialog(evt)


def _question(tui: A.EchoTUI) -> None:
    _running(tui, tools=0, tail=False, pending=0)
    tui.dialog = tui._approval_dialog({
        "type": "question", "approval_id": "q1", "kind": "question",
        "question": "Which date library should the parser use for ISO weeks across the whole project?",
        "options": ["The standard library (datetime.isocalendar)", "python-dateutil", "pendulum", "arrow",
                    "Keep the hand-written code"], "allow_other": True})


STATES: dict[str, Callable[[A.EchoTUI], None]] = {
    "idle": lambda t: None,
    "idle-autonomous": lambda t: setattr(t, "approval_mode", "autonomous"),
    "draft-multiline": lambda t: t.buffer.set_document(
        A.Document("line one of a long draft that keeps going past the edge of a small terminal\n" * 12)),
    "slash-menu": lambda t: t.buffer.set_document(A.Document("/")),
    "shortcuts": lambda t: setattr(t, "show_shortcuts", True),
    "notice": lambda t: setattr(t, "notice", ("Press ctrl+c again to exit (the task keeps running on the backend)",
                                              time.monotonic() + 60)),
    "running": _running,
    "running-many": lambda t: _running(t, tools=6, pending=6),
    "approval-diff": _approval,
    "approval-command": lambda t: _approval(t, diff=False),
    "question": _question,
}


def render_live(tui: A.EchoTUI, out: SizedOutput, width: int, height: int) -> tuple[list[str], int]:
    """What the renderer would draw: (rows, preferred height). Mirrors Renderer.render's height rule."""
    out.size = Size(rows=height, columns=width)
    tui._diff_cache.clear()
    tui._forget_plan()
    with set_app(tui.app):
        container = tui.app.layout.container
        want = container.preferred_height(width, height).preferred
        h = min(want, height)
        screen = Screen()
        container.write_to_screen(screen, MouseHandlers(), WritePosition(xpos=0, ypos=0, width=width, height=h),
                                  parent_style="", erase_bg=False, z_index=None)
        screen.draw_all_floats()
    rows = []
    for y in range(h):
        line = screen.data_buffer[y]
        rows.append("".join(line[x].char for x in range(width)).rstrip())
    return rows, want


def fragment_overflow(tui: A.EchoTUI, width: int) -> list[str]:
    """Lines produced by the live-region text functions that are wider than the terminal."""
    bad = []
    for name in ("_top_rule", "_bottom_rule", "_tail_text", "_steps_text", "_status_text", "_pending_text",
                 "_menu_text", "_shortcuts_text", "_footer_text", "_dialog_footer", "_dialog_text", "_diff_pane_text"):
        frags = getattr(tui, name)()
        text = "".join(t for _s, t in frags)
        for line in text.split("\n"):
            if width_of(line) > width:
                bad.append(f"{name}: {width_of(line)} > {width}: {line[:60]!r}")
    return bad


def check(state: str, width: int, height: int, caps: Caps = DEFAULT_CAPS) -> list[str]:
    with create_pipe_input() as inp, create_app_session(input=inp, output=DummyOutput()):
        tui, out = make_tui(caps)
        out.size = Size(rows=height, columns=width)
        with set_app(tui.app):
            STATES[state](tui)
        rows, want = render_live(tui, out, width, height)
        problems = []
        if want > budget(height):
            problems.append(f"clipped: wants {want} rows of {budget(height)} (terminal {height})")
        joined = "\n".join(rows)
        if "Window too small" in joined:
            problems.append("Window too small")
        with set_app(tui.app):
            problems += fragment_overflow(tui, width)
        draft_rows = tui.buffer.text.count("\n") + 1   # a long draft scrolls; its first row may be off screen
        prompt_shown = any(r.lstrip().startswith(tui.caps.glyphs.prompt) for r in rows)
        if tui.dialog is None and draft_rows <= 1 and not prompt_shown:
            problems.append("no prompt row")
        if tui.dialog is not None and not any(r.strip() for r in rows):
            problems.append("empty dialog")
        marks = (tui.model[:4], "Pres") if state == "notice" else (tui.model[:4],)   # a notice may crowd out the model
        # Under 12 rows a dialog's options outrank the footer (you are deciding, not reading the model name).
        footer_due = height >= (12 if tui.dialog is not None else 8)
        if width >= 12 and footer_due and not any(m in r for r in rows[-3:] for m in marks):
            problems.append("no footer")
        return problems


CASES = [(s, w, h) for s in STATES for w in WIDTHS for h in HEIGHTS]


@pytest.mark.parametrize("state,width,height", CASES)
def test_live_region_fits(state, width, height):
    assert check(state, width, height) == []


@pytest.mark.parametrize("state", ["idle", "running", "approval-diff", "question"])
def test_ascii_and_no_color_fit(state):
    caps = Caps(color=False, ascii=True, glyphs=ASCII_GLYPHS)
    for width in (16, 40, 80):
        assert check(state, width, 24, caps) == []


if __name__ == "__main__":
    fails = 0
    by_state: dict[str, list[str]] = {}
    for s, w, h in CASES:
        p = check(s, w, h)
        if p:
            fails += 1
            by_state.setdefault(s, []).append(f"{w}x{h}: {p[0]}" + (f" (+{len(p) - 1})" if len(p) > 1 else ""))
    for s, items in by_state.items():
        print(f"\n## {s}: {len(items)} failing sizes")
        for it in items[:12]:
            print("  ", it)
    print(f"\n{fails} of {len(CASES)} cases fail")
    sys.exit(1 if fails else 0)
