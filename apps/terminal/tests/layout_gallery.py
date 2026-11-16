"""Print the live region for each state at a few sizes (a quick visual check; not a test).

    PYTHONPATH=apps/terminal apps/terminal/.venv/bin/python apps/terminal/tests/layout_gallery.py [state ...] [--sizes 80x24,40x12]
"""
from __future__ import annotations

import sys

import test_layout_matrix as T
from prompt_toolkit.application.current import create_app_session, set_app
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput


def show(state: str, width: int, height: int) -> str:
    with create_pipe_input() as inp, create_app_session(input=inp, output=DummyOutput()):
        tui, out = T.make_tui()
        out.size = Size(rows=height, columns=width)
        with set_app(tui.app):
            T.STATES[state](tui)
        rows, _ = T.render_live(tui, out, width, height)
    frame = "┄" * width
    return "\n".join([f"{state} @ {width}x{height} ({len(rows)} rows)", frame] + [r.ljust(width) + "┊" for r in rows] + [frame])


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    sizes = "120x40,80x24,50x16,30x10,16x8"
    for a in sys.argv[1:]:
        if a.startswith("--sizes="):
            sizes = a.split("=", 1)[1]
    for state in args or list(T.STATES):
        for s in sizes.split(","):
            w, h = (int(x) for x in s.split("x"))
            print(show(state, w, h), end="\n\n")
