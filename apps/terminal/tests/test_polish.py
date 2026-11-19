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
        assert len(rows) == 3 and all(width_of(r) == 8 for r in rows), (t, rows)


def test_the_resting_face_is_square_symmetric_and_has_two_eyes():
    bm = avatar.bitmap(avatar.ENTRANCE_MS)
    cols = [x for x in range(avatar.CANVAS_W) if any(r[x] == "#" for r in bm)]
    width_half_cells, height_half_rows = len(cols), sum(1 for r in bm if "#" in r)
    # A half-cell is ~4.5 px and a half-row ~10 px (a 9 × 20 px cell): 14 × 4.5 = 63 px by 6 × 10 = 60 px.
    assert width_half_cells == 14 and height_half_rows == 6
    assert all(r == r[::-1] for r in bm), "the face is mirror-symmetric"
    eye_row = bm[2]
    assert eye_row.count(".") - 2 == 4, "two eyes, each one cell wide"
    assert avatar.FACE_REST == ("▗██████▖", "▐█ ██ █▌", "▝██████▘")


def test_the_entrance_moves_then_settles_and_reduced_motion_is_still():
    frames = {avatar.frame(t) for t in range(0, avatar.ENTRANCE_MS, 10)}
    assert len(frames) >= 6, "it rises, lands, glances and blinks"
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
def test_left_twice_opens_the_page_and_back_goes_one_level_at_a_time():
    async def go(tui):
        press(tui, Keys.Left)
        await settle(tui)
        assert tui.browser.mode == "picker"
        picker_rows = tui._rows("sessions")
        press(tui, Keys.Left)
        assert tui.browser.mode == "page" and tui._rows("sessions") > picker_rows
        press(tui, Keys.Left)                                          # nowhere deeper: still the page
        assert tui.browser.mode == "page"
        for ch in "gla":
            press(tui, ch)
        press(tui, Keys.Escape)                                        # a search is cleared first
        assert tui.browser is not None and tui.browser.mode == "page" and tui.browser.query == ""
        press(tui, Keys.Escape)                                        # then back to the picker
        assert tui.browser.mode == "picker"
        press(tui, Keys.Escape)                                        # then back to your conversation
        assert tui.browser is None and tui.app.layout.has_focus(tui.composer_window)
        press(tui, Keys.Left)
        await settle(tui)
        press(tui, Keys.Left, Keys.Right)                              # → also goes back one level
        assert tui.browser.mode == "picker"
        press(tui, Keys.Right)
        assert tui.browser is None
    session(go)


def test_enter_on_the_page_resumes_the_selected_conversation():
    async def go(tui):
        press(tui, Keys.Left)
        await settle(tui)
        press(tui, Keys.Left, Keys.Home)                               # the page's first row: what needs you
        assert tui.browser.chosen()["status"] in {"needs_answer", "needs_approval"}
        target = tui.browser.chosen()["thread_id"]
        press(tui, Keys.Enter)
        await settle(tui)
        assert tui.browser is None and tui.sid == target
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


def test_the_page_shows_agent_project_state_age_and_the_selected_conversations_last_exchange():
    b = _page()
    b.selected = next(i for i, r in enumerate(b.visible()) if r["thread_id"] == "t-oilers")
    body = "\n".join(text(line) for line in b.lines(120, 30, CAPS, NOW))
    assert "All sessions" in body and "Needs you" in body and "Running" in body
    assert "Release crew" in body and "needs approval" in body and "needs your answer" in body
    assert "when do the edmonton oilers play next?" in body and "The Oilers play Tuesday" in body
    assert "here" in body and "5 conversations" in body


@pytest.mark.parametrize("width", [20, 30, 45, 60, 80, 120, 200])
@pytest.mark.parametrize("rows", [3, 5, 8, 14, 30])
def test_the_page_never_draws_past_the_width_or_the_rows(width, rows):
    b = _page()
    for _ in range(len(ROWS)):
        lines = b.lines(width, rows, CAPS, NOW)
        assert 1 <= len(lines) <= rows
        assert all(width_of(text(line)) <= width for line in lines), [text(line) for line in lines]
        b.move_page(1, NOW)


def test_picker_rows_have_no_dead_gap_after_a_short_title():
    b = SessionBrowser(current_id="t-models")
    b.load([dict(r) for r in ROWS])
    line = next(text(x) for x in b.lines(140, 20, CAPS, NOW) if "Which local model" in text(x))
    title_end = line.index("Which local model should I use") + len("Which local model should I use")
    assert line[title_end:].lstrip().startswith("Echo"), line         # the agent follows the longest title closely
    assert line.rstrip().endswith("ago")                               # the age sits at the right edge


def test_previews_are_plain_text_and_ages_are_short():
    assert plain_recap("The fix is in `iso_week`, **really**.") == "The fix is in iso_week, really."
    assert age(NOW - 30, NOW) == "now" and age(NOW - 300, NOW) == "5m" and age(NOW - 3 * 86400, NOW) == "3d"
    assert day_group(NOW, NOW) == "Today" and day_group(NOW - 40 * 86400, NOW) == "Earlier"


# ── E. Steps, decisions and failures ────────────────────────────────
def test_two_model_steps_never_run_their_words_together():
    run, out = run_of([(0, start()), (1, {"type": "agent_token", "message_id": "m1", "data": "Saved."}),
                       (2, {"type": "step_start", "message_id": "m1", "step": 2}),
                       (3, {"type": "agent_token", "message_id": "m1", "data": "Done."}),
                       (4, {"type": "agent_done", "message_id": "m1"})])
    out += run.finish("completed", 5)
    said = " | ".join(b.text for b in out if isinstance(b, B.MarkdownBlock))
    assert "Saved.Done." not in said and "Saved." in said and "Done." in said


def test_what_the_agent_says_after_its_steps_gets_its_own_bullet():
    run, out = run_of([(0, start()), (1, {"type": "agent_token", "message_id": "m1", "data": "Let me look.\n\n"}),
                       (2, {"type": "tool_start", "message_id": "m1", "id": "t1", "name": "file_list", "label": "Listing ."}),
                       (3, {"type": "tool_end", "message_id": "m1", "id": "t1", "name": "file_list", "ok": True,
                            "done_label": "Listed .", "summary": "4 items"}),
                       (4, {"type": "agent_token", "message_id": "m1", "data": "Found it."}),
                       (5, {"type": "agent_done", "message_id": "m1"})])
    md = [b for b in out if isinstance(b, B.MarkdownBlock)]
    assert [b.bullet for b in md] == [True, True]


def test_failed_steps_are_not_counted_as_work_done():
    assert steps_summary([{"name": "file_read", "failed": True}, {"name": "file_read", "failed": True},
                          {"name": "file_list"}]) == "Looked through files · 2 failed"
    assert steps_summary([{"name": "memory_save"}]) == "Saved to memory"


def test_an_answered_question_and_a_denial_are_each_said_once():
    run, out = run_of([
        (0, start()),
        (1, {"type": "tool_start", "message_id": "m1", "id": "c1", "name": "ask_user", "label": "Asking you"}),
        (2, {"type": "approval_request", "message_id": "m1", "id": "q1", "tool_call_id": "c1", "kind": "question",
             "question": "Which year?"}),
        (3, {"type": "approval_resolved", "message_id": "m1", "id": "q1", "tool_call_id": "c1", "decision": "answered",
             "answer": "2026"}),
        (4, {"type": "tool_end", "message_id": "m1", "id": "c1", "name": "ask_user", "ok": True,
             "done_label": "Asked you: Which year?", "summary": "You chose: 2026"}),
        (5, {"type": "tool_start", "message_id": "m1", "id": "c2", "name": "terminal", "label": "Running `rm -rf build`"}),
        (6, {"type": "approval_request", "message_id": "m1", "id": "a2", "tool_call_id": "c2", "tool": "terminal",
             "summary": "Running `rm -rf build`"}),
        (7, {"type": "approval_resolved", "message_id": "m1", "id": "a2", "tool_call_id": "c2", "decision": "deny"}),
        (8, {"type": "tool_end", "message_id": "m1", "id": "c2", "name": "terminal", "ok": False, "outcome": "denied"}),
        (9, {"type": "answer_note", "message_id": "m1", "verification": "blocked",
             "note": "Blocked — authorization: The user denied permission to run terminal. Do not retry it."}),
    ])
    rendered = "\n".join(plain(B.render(b, 100, CAPS)) for b in out)
    assert rendered.count("2026") == 1
    assert rendered.count("rm -rf build") == 1 and "you denied it" in rendered
    assert "Do not retry" not in rendered                              # the model-facing message never shows


def test_an_allowed_step_says_so_on_its_own_line():
    run, out = run_of([(0, start()),
                       (1, {"type": "approval_resolved", "message_id": "m1", "id": "a1", "tool_call_id": "c1",
                            "decision": "allow"}),
                       (2, {"type": "tool_end", "message_id": "m1", "id": "c1", "name": "terminal", "ok": True,
                            "done_label": "Ran `pytest`", "summary": "3 passed"})])
    tools = [b for b in out if isinstance(b, B.ToolLine)]
    assert not [b for b in out if isinstance(b, B.Note)] and tools[0].summary == "3 passed · you allowed it"


def test_a_failure_is_stated_once_and_never_called_answered():
    run, out = run_of([(0, start()),
                       (1, {"type": "agent_done", "message_id": "m1", "success": False, "error": "Provider unavailable (HTTP 500)",
                            "text": "The model request failed: Provider unavailable (HTTP 500)"}),
                       (2, {"type": "final", "success": False})])
    out += run.finish("completed", 9)
    rendered = "\n".join(plain(B.render(b, 100, CAPS)) for b in out)
    assert rendered.count("Provider unavailable") == 1
    assert "Answered in" not in rendered


def test_a_failed_tool_line_reads_as_a_failure_without_repeating_itself(monkeypatch):
    monkeypatch.setenv("HOME", "/home/owner")
    line = B.ToolLine("fail", "Reading parser.py", "Failed: file not found: /home/owner/Documents/parser.py", 0,
                      ["Error: file not found: /home/owner/Documents/parser.py"])
    out = plain(B.render(line, 100, CAPS)).strip("\n").splitlines()
    assert out == ["  " + CAPS.glyphs.fail + " Reading parser.py · file not found: ~/Documents/parser.py"]


def test_search_lines_name_the_sources_the_agent_actually_got():
    output = ("1. datetime — Basic date and time types\n   URL: https://docs.python.org/3/library/datetime.html\n"
              "   date.fromisocalendar …\n2. isoweek · PyPI\n   URL: https://pypi.org/project/isoweek/\n"
              "3. Pendulum\n   URL: https://www.pendulum.eustace.io/docs/\n4. Fourth\n   URL: https://x.org/\n")
    assert search_sources(output) == ["docs.python.org · datetime — Basic date and time types",
                                      "pypi.org · isoweek · PyPI", "pendulum.eustace.io · Pendulum"]
    assert search_sources("No results") == []


def test_an_open_question_is_not_also_announced_by_the_status_line_and_steps():
    async def go(tui):
        from echospeak_cli.tui.state import Run

        tui.run = Run("r1", tui.now(), glyphs=tui.caps.glyphs)
        tui.run.feed(start(), tui.now())
        tui.run.feed({"type": "tool_start", "message_id": "m1", "id": "c1", "name": "ask_user", "label": "Asking you"},
                     tui.now())
        tui.run.feed({"type": "approval_request", "message_id": "m1", "id": "q1", "tool_call_id": "c1",
                      "kind": "question", "question": "Which year?"}, tui.now())
        tui.open_dialog(Dialog(title="Echo is asking", options=[Option("2026", "2026")], kind="question",
                               payload={"id": "q1", "tool_call_id": "c1"}))
        tui._forget_plan()
        assert tui._rows("status") == 0 and tui._rows("steps") == 0
    session(go)


def test_a_turn_whose_step_you_denied_says_so_instead_of_answered():
    run, out = run_of([(0, start()),
                       (1, {"type": "approval_resolved", "message_id": "m1", "id": "a1", "tool_call_id": "c1", "decision": "deny"}),
                       (2, {"type": "tool_end", "message_id": "m1", "id": "c1", "name": "terminal", "ok": False, "outcome": "denied"})])
    rules = [b.text for b in run.finish("completed", 3) if isinstance(b, B.RuleLine)]
    assert rules and rules[0].startswith("Worked for") and rules[0].endswith("1 denied")
