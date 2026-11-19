"""The TUI polish campaign (cli-design.md §9): Echo's entrance, the input's prompt column, a clean start,
the two-level ← (picker, then the sessions page), and how steps, decisions and failures read.

Pure parts are tested directly; keys go through the app's real key bindings (test_sessions.py's
harness), so a navigation that draws right but routes keys wrong would fail here.
"""
from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("prompt_toolkit")
pytest.importorskip("rich")

from prompt_toolkit.keys import Keys  # noqa: E402

from echospeak_cli.tui import app as A  # noqa: E402
from echospeak_cli.tui import avatar  # noqa: E402
from echospeak_cli.tui import blocks as B  # noqa: E402
from echospeak_cli.tui.dialog import Dialog, Option  # noqa: E402
from echospeak_cli.tui.motion import width_of  # noqa: E402
from echospeak_cli.tui.sessions import SessionBrowser, age, day_group, plain_recap  # noqa: E402
from echospeak_cli.tui.state import search_sources, steps_summary  # noqa: E402
from echospeak_cli.tui.theme import ASCII_GLYPHS, Caps  # noqa: E402

from test_sessions import NOW, ROWS, RecordingClient, press, session, settle, text  # noqa: E402
from test_tui import plain, run_of, start  # noqa: E402

CAPS = Caps()


# ── A. Echo's face and entrance ─────────────────────────────────────
def test_every_entrance_frame_is_the_same_size_so_nothing_beside_it_moves():
    for t in range(-50, avatar.ENTRANCE_MS + 400, 5):
        rows = avatar.frame(t)
        assert len(rows) == 3 and all(width_of(r) == 7 for r in rows), (t, rows)


def test_the_resting_face_is_a_true_square_with_straight_sides_and_two_eyes():
    bm = avatar.bitmap(avatar.ENTRANCE_MS)
    # A half-cell is ~4.5 px and a half-row ~10 px (a 9 x 20 px cell): 14 x 4.5 = 63 px by 6 x 10 = 60 px.
    assert len(bm) == 6 and all(len(r) == 14 for r in bm)
    # Straight sides: every row reaches both edges (no notched corners, so nothing sticks out at the sides).
    assert all(r[0] == r[-1] == "#" for r in bm)
    assert all(r == r[::-1] for r in bm), "the face is mirror-symmetric"
    assert bm[2].count(".") == 4 and bm[3] == bm[2], "two eyes, each one cell wide and one row tall"
    assert avatar.FACE_REST == ("███████", "█▌▐█▌▐█", "███████")


def test_the_entrance_slides_in_from_the_left_and_never_moves_back():
    def left_edge(t):
        bm = avatar.bitmap(t)
        filled = [x for x in range(avatar.CANVAS_W) if any(r[x] == "#" for r in bm)]
        return max(filled) if filled else -1     # the face's right edge enters first, from the left

    edges = [left_edge(t) for t in range(0, avatar.SLIDE_MS + 1, 10)]
    assert edges[0] < 3 and edges[-1] == avatar.CANVAS_W - 1
    assert all(b >= a for a, b in zip(edges, edges[1:])), "monotonic: no jitter back and forth"
    steps = [b - a for a, b in zip(edges, edges[1:]) if b != a]
    assert max(steps) <= 2, "at most a cell per 10 ms: smooth at 30 fps"


def test_the_entrance_settles_and_reduced_motion_is_still():
    frames = {avatar.frame(t) for t in range(0, avatar.ENTRANCE_MS, 10)}
    assert len(frames) >= 6, "it slides in and blinks"
    assert avatar.frame(avatar.ENTRANCE_MS + 5000) == avatar.FACE_REST
    assert all(avatar.frame(t, reduced_motion=True) == avatar.FACE_REST for t in (0, 100, 700))
    assert avatar.done(0, reduced_motion=True) and not avatar.done(100)


def test_the_live_welcome_has_exactly_the_rows_the_printed_banner_will_have():
    def go(tui):
        async def inner(tui):
            tui.welcome = tui._banner()
            printed = B.render(tui.welcome, tui.width(), tui.caps).rstrip("\n").split("\n")
            for t in (0.0, 0.2, 0.6, 1.2, 3.0):
                tui.welcome_t0 = tui.now() - t
                tui._welcome_cache = None
                assert len(tui._welcome_lines()) == len(printed)
        return inner
    session(go(None))


def test_the_banner_is_printed_before_anything_else_and_its_live_copy_waits_for_the_flush():
    async def go(tui):
        tui._out_event = asyncio.Event()
        tui.welcome = tui._banner()
        tui.note("first note")
        assert "EchoSpeak" in tui._out[0] and "first note" in tui._out[-1]
        assert tui.welcome is not None, "kept on screen until the banner is really written"
        for hook in tui._after_flush:
            hook()
        assert tui.welcome is None
    session(go)


def test_ascii_terminals_get_the_ascii_face():
    caps = Caps(ascii=True, glyphs=ASCII_GLYPHS)
    banner = B.Banner("11.14", "Echo", "Personal agent", "m", "~/w", "", "ask for risky actions")
    out = plain(B.render(banner, 100, caps))
    assert "█" not in out and "+-----+" in out


# ── B. The input's prompt column ────────────────────────────────────
def test_the_input_starts_two_cells_after_the_prompt_and_wraps_at_the_same_column():
    async def go(tui):
        first = text(tui._line_prefix(0, 0))
        assert first == tui.caps.glyphs.prompt + "  "
        assert text(tui._line_prefix(0, 1)) == " " * len(first)    # a wrapped line
        assert text(tui._line_prefix(1, 0)) == " " * len(first)    # a second line (ctrl+j)
        w = tui.width()
        tui.buffer.text = "x" * (w - 3)
        assert tui._input_rows() == 1
        tui.buffer.text = "x" * (w - 2)
        assert tui._input_rows() == 2
        tui.buffer.text = "日本語" * 40                               # wide characters count two cells
        assert tui._input_rows() == -(-(240) // (w - 3))
    session(go)


def test_a_sent_message_keeps_the_column_it_was_typed_in():
    line = plain(B.render(B.UserLine("fix it"), 80, CAPS))
    assert line.strip("\n").startswith(CAPS.glyphs.prompt + "  fix it")


# ── C. A clean start ────────────────────────────────────────────────
LAST = {"thread_id": "t-models", "title": "Which local model should I use", "environment": "chat",
        "message_count": 14, "last_active_at": NOW, "project_id": "p1"}


def _startup(tui, continue_last: bool):
    tui.core.session = None
    tui.start_continue = continue_last
    tui.core.default_session = lambda cwd: dict(LAST)
    tui.core.project_for_path = lambda *a: "p1"

    async def nothing(*_a, **_k):
        return None

    tui._probe = nothing
    tui._load_settings = nothing


def test_a_plain_start_reopens_nothing_and_offers_the_last_conversation():
    async def go(tui):
        _startup(tui, continue_last=False)
        await tui._prepare()
        assert tui.sid == "" and tui.core.session is None              # nothing reopened, nothing replayed
        assert tui.last_here and tui.last_here["thread_id"] == "t-models"
        banner = tui.welcome or next(b for b in tui.transcript if isinstance(b, B.Banner))
        assert "Last here" in banner.last and "Which local model" in banner.last and "←" in banner.last
        assert not banner.resumed
        press(tui, Keys.Left)                                          # ← offers it first
        await settle(tui)
        assert tui.browser.chosen()["thread_id"] == "t-models"
    session(go)


def test_continue_reopens_the_last_conversation():
    async def go(tui):
        _startup(tui, continue_last=True)
        await tui._prepare()
        assert tui.sid == "t-models"
        banner = tui.welcome or next(b for b in tui.transcript if isinstance(b, B.Banner))
        assert banner.resumed.startswith("Resumed") and not banner.last
    session(go)


def test_the_cli_has_a_continue_flag():
    from echospeak_cli.main import build_parser

    assert build_parser().parse_args(["-c"]).continue_last is True
    assert build_parser().parse_args([]).continue_last is False


# ── D. ← twice: the picker, then the sessions page ─────────────────
def _scripted_page(monkeypatch, *results):
    """Stand in for the full-screen page: return these results in turn, recording what it was given."""
    from echospeak_cli.tui import sessionpage

    calls = []

    async def fake(browser, caps, style, now=None):
        calls.append((browser.mode, browser.chosen()))
        browser.mode = "page"
        result = results[len(calls) - 1]
        return result(browser) if callable(result) else result

    monkeypatch.setattr(sessionpage, "run", fake)
    return calls


def test_left_twice_opens_the_full_screen_page_and_back_returns_to_the_picker(monkeypatch):
    calls = _scripted_page(monkeypatch, ("back", None), ("close", None))

    async def go(tui):
        press(tui, Keys.Left)
        await settle(tui)
        assert tui.browser.mode == "picker"
        press(tui, Keys.Left)                                          # the page
        await settle(tui)
        assert len(calls) == 1 and calls[0][0] == "picker"           # opened from the picker, on its selection
        assert tui.browser is not None and tui.browser.mode == "picker"   # back: the picker as it was
        assert tui.app.layout.has_focus(tui.browser_window)
        press(tui, Keys.Left)
        await settle(tui)
        assert len(calls) == 2 and tui.browser is None               # close: straight back to the conversation
        assert tui.app.layout.has_focus(tui.composer_window)
    session(go)


def test_enter_on_the_page_resumes_the_selected_conversation(monkeypatch):
    def pick_needs_you(browser):
        browser.jump_page(False)
        return ("resume", browser.chosen())

    _scripted_page(monkeypatch, pick_needs_you)

    async def go(tui):
        press(tui, Keys.Left)
        await settle(tui)
        press(tui, Keys.Left)
        await settle(tui)
        assert tui.browser is None and tui.sid == "t-pong"            # the page's first row: what needs you
    session(go, RecordingClient())


def _page(**kw) -> SessionBrowser:
    b = SessionBrowser(current_id="t-models", project_id="p1", mode="page", **kw)
    b.load([dict(r) for r in ROWS])
    return b


def test_the_page_groups_what_needs_you_first_then_running_then_by_day():
    b = _page()
    names = [name for name, _ in b.groups(NOW)]
    assert names[:2] == ["Needs you", "Running"]
    assert sorted(b.page_order(NOW)) == list(range(len(ROWS)))       # every conversation, once
    b.jump_page(False, NOW)
    seen = []
    for _ in ROWS:
        seen.append(b.chosen()["thread_id"])
        b.move_page(1, NOW)
    assert seen[:2] == ["t-pong", "t-crew"] and seen[2] == "t-oilers"  # ↓ walks the page's order


def test_the_page_lists_beside_a_details_pane_with_the_real_facts():
    from echospeak_cli.tui import sessionpage

    b = _page()
    b.selected = next(i for i, r in enumerate(b.visible()) if r["thread_id"] == "t-oilers")
    lines = [text(line) for line in sessionpage.render(b, 130, 30, CAPS, NOW)]
    body = "\n".join(lines)
    assert lines[0].lstrip().startswith("Sessions") and "5 conversations" in lines[0]
    assert "Needs you" in body and "Running" in body and "Release crew" in body
    row = next(line for line in lines if "│" in line and "When do the Oilers" in line.split("│", 1)[0])
    left, right = row.split("│", 1)
    assert "Echo" in left                                             # the list: title, agent, age
    assert "Agent" in body and "Project   Work" in body                 # the details pane, from the row's own fields
    assert "Status    running" in body and "Updated" in body and "Messages  3" in body
    assert "when do the edmonton oilers play next?" in body and "The Oilers play Tuesday" in body
    assert "1 / 5" in body or "/ 5" in body                         # the position rule


def test_below_96_columns_the_details_go_under_the_list():
    from echospeak_cli.tui import sessionpage

    lines = [text(line) for line in sessionpage.render(_page(), 80, 30, CAPS, NOW)]
    assert not any("│" in line for line in lines) and any("Agent" in line for line in lines)


@pytest.mark.parametrize("width", [20, 30, 45, 60, 80, 96, 120, 200])
@pytest.mark.parametrize("rows", [3, 5, 8, 14, 30, 50])
def test_the_page_fills_the_screen_exactly(width, rows):
    from echospeak_cli.tui import sessionpage

    b = _page()
    for _ in range(len(ROWS)):
        lines = sessionpage.render(b, width, rows, CAPS, NOW)
        assert len(lines) == rows
        assert all(width_of(text(line)) == width for line in lines), [text(line) for line in lines]
        b.move_page(1, NOW)


def test_several_outputs_at_once_print_the_banner_once():
    """A resume prints a rule and several recap lines in one go; each used to commit the banner again (lab, 19 Nov)."""
    async def go(tui):
        tui._out_event = asyncio.Event()
        tui.welcome = tui._banner()
        for n in range(4):
            tui.note(f"recap line {n}")
        said = plain("".join(tui._out))
        assert said.count("EchoSpeak v") == 1 and said.index("EchoSpeak v") < said.index("recap line 0")
        for hook in tui._after_flush:
            hook()
        assert tui.welcome is None
    session(go)
