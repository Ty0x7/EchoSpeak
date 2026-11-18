"""Echo TUI pure parts: reducer, spinner math, footer, outcome wording, approval mapping, sanitizing, widths.

Needs prompt_toolkit and rich (apps/terminal/.venv); skipped without them.
"""
from __future__ import annotations

import re

import pytest

pytest.importorskip("prompt_toolkit")
pytest.importorskip("rich")

from echospeak_cli import control  # noqa: E402
from echospeak_cli.tui import blocks as B  # noqa: E402
from echospeak_cli.tui import motion as M  # noqa: E402
from echospeak_cli.tui.commands import COMMANDS, filter_commands, lookup, visible  # noqa: E402
from echospeak_cli.tui.dialog import Dialog, Option, approval_options, question_options  # noqa: E402
from echospeak_cli.tui.mdstream import MdStream  # noqa: E402
from echospeak_cli.tui.state import Run, outcome_blocks, steps_summary  # noqa: E402
from echospeak_cli.tui.theme import ASCII_GLYPHS, Caps, Glyphs, detect  # noqa: E402

ANSI = re.compile(r"\x1b\[[0-9;]*m")
CAPS = Caps()
MONO = Caps(color=False)


def plain(text: str) -> str:
    return ANSI.sub("", text)


def text_of(frags) -> str:
    return "".join(t for _s, t in frags)


def run_of(events, verbose=False):
    run = Run("term-r1", 0.0, glyphs=Glyphs(), verbose=verbose)
    out = []
    for t, evt in events:
        out += run.feed(evt, t)
    return run, out


def start(mid="m1", agent="echo", name="Echo", **extra):
    return {"type": "agent_start", "message_id": mid, "agent": {"id": agent, "name": name, "title": "Personal agent"}, **extra}


# ── reducer ──────────────────────────────────────────────────────────
def test_tokens_commit_whole_markdown_blocks():
    run, out = run_of([(0, start()), (0.1, {"type": "agent_token", "message_id": "m1", "data": "First para"}),
                       (0.2, {"type": "agent_token", "message_id": "m1", "data": "graph.\n\nSecond "})])
    mds = [b for b in out if isinstance(b, B.MarkdownBlock)]
    assert [m.text for m in mds] == ["First paragraph."]
    assert mds[0].bullet is True           # single agent: "● " inline, no header
    assert not any(isinstance(b, B.AgentHeader) for b in out)
    assert run.tail() == "Second "
    rest = run.feed({"type": "agent_done", "message_id": "m1", "text": "First paragraph.\n\nSecond one."}, 1.0)
    assert [b.text.strip() for b in rest if isinstance(b, B.MarkdownBlock)] == ["Second"]
    assert run.tail() == ""


def test_tool_lines_statuses_and_previews():
    events = [(0, start()),
              (1, {"type": "tool_start", "message_id": "m1", "id": "t1", "name": "terminal", "label": "Running `pytest`"}),
              (2, {"type": "tool_end", "message_id": "m1", "id": "t1", "name": "terminal", "ok": True, "outcome": "ok",
                   "done_label": "Ran `pytest`", "summary": "3 passed", "duration_ms": 2300,
                   "output": "[ok · 2.3s · this PC · /p]\na\nb\nc\nd\n3 passed"}),
              (3, {"type": "tool_start", "message_id": "m1", "id": "t2", "name": "web_search", "label": "Searching “x”"}),
              (4, {"type": "tool_end", "message_id": "m1", "id": "t2", "name": "web_search", "ok": False, "outcome": "failed",
                   "summary": "timeout after 15s", "output": "Error: timeout"}),
              (5, {"type": "approval_request", "message_id": "m1", "id": "apr1", "tool_call_id": "t3", "tool": "terminal",
                   "summary": "Running `rm x`"}),
              (6, {"type": "approval_resolved", "message_id": "m1", "id": "apr1", "tool_call_id": "t3", "decision": "deny"}),
              (7, {"type": "tool_end", "message_id": "m1", "id": "t3", "name": "terminal", "ok": False, "outcome": "failed"}),
              (8, {"type": "tool_end", "message_id": "m1", "id": "t4", "name": "file_write", "ok": False, "outcome": "held"})]
    run, out = run_of(events)
    tools = [b for b in out if isinstance(b, B.ToolLine)]
    assert [t.status for t in tools] == ["ok", "fail", "denied", "held"]
    assert tools[0].preview == ["c", "d", "3 passed"] and tools[0].more == 2   # the [ok …] header is dropped
    assert tools[1].preview == ["Error: timeout"]
    assert tools[2].summary == "you denied it" and tools[2].preview == []
    notes = [b.text for b in out if isinstance(b, B.Note)]
    assert "Denied: Running `rm x`" in notes
    # Denied and held steps never ran: only the search and the pytest run count.
    assert steps_summary(run.finished_tools) == "Searched the web · Ran 1 command · 1 failed"


def test_a_passing_check_note_is_not_shown_as_a_warning():
    events = [(0, start()),
              (1, {"type": "answer_note", "message_id": "m1", "note": "Checks pass — test `pytest`: 2 passed",
                   "verification": "checks_pass"}),
              (2, {"type": "answer_note", "message_id": "m1", "note": "No check ran after the last change"})]
    _, out = run_of(events)
    notes = [b for b in out if isinstance(b, B.Note)]
    assert notes[0].glyph != "!" and notes[0].text.startswith("Checks pass")
    assert notes[1].glyph == "!"


def test_activity_wording_follows_the_gui():
    run = Run("r", 0.0, glyphs=Glyphs())
    assert run.activity(0) == "Connecting to the model"
    run.feed({"type": "routing"}, 0)
    assert run.activity(0) == "Choosing who should answer"
    run.feed(start(), 0)
    assert run.activity(0.5) == "Echo is starting"
    run.feed({"type": "reasoning_delta", "message_id": "m1", "text": "hmm"}, 1)
    assert run.activity(5) == "Echo is thinking it through"
    assert run.activity(25) == "Echo is still thinking"
    assert run.activity(50) == "Echo is deep in thought"
    run.feed({"type": "tool_start", "message_id": "m1", "id": "t", "name": "file_read", "label": "Reading a.py"}, 51)
    run.feed({"type": "tool_progress", "id": "t", "text": "line 40"}, 51)
    assert run.activity(52) == "Reading a.py · line 40"
    run.feed({"type": "tool_end", "message_id": "m1", "id": "t", "name": "file_read", "ok": True}, 53)
    assert run.activity(53) == "Echo: Reading through it"
    run.feed({"type": "approval_request", "message_id": "m1", "id": "a1", "tool": "terminal"}, 54)
    assert run.activity(54) == "Echo needs your OK" and run.needs_ok()
    run.feed({"type": "approval_resolved", "message_id": "m1", "id": "a1", "decision": "allow"}, 55)
    assert not run.needs_ok()
    run.feed(start("m2", "scout", "Jarvis"), 56)
    assert run.activity(56) == "Echo and Jarvis are working"


def test_handoff_header_and_context():
    run, out = run_of([(0, start()), (1, start("m2", "scout", "Jarvis", delegated_by={"name": "Echo"})),
                       (2, {"type": "token_usage", "message_id": "m2", "prompt": 9000, "completion": 120, "context_limit": 16000})])
    heads = [b for b in out if isinstance(b, B.AgentHeader)]
    assert [h.name for h in heads] == ["Jarvis"] and heads[0].delegated_by == "Echo"
    run.feed({"type": "agent_done", "message_id": "m1", "text": ""}, 3)
    assert run.activity(3) == "Echo → Jarvis · Jarvis is starting"
    assert run.context_pct() == 56 and run.tokens == 120


def test_direct_session_with_an_agent_gets_a_header():
    run = Run("r", 0.0, glyphs=Glyphs(), session_agent="scout")
    out = run.feed(start("m1", "scout", "Jarvis"), 0)
    assert isinstance(out[0], B.AgentHeader)


def test_thinking_collapses_and_cancel_noise_is_dropped():
    run, out = run_of([(0, start()), (0, {"type": "reasoning_delta", "message_id": "m1", "text": "plan"}),
                       (8, {"type": "agent_token", "message_id": "m1", "data": "Hi"})])
    thought = [b for b in out if isinstance(b, B.ThinkingLine)]
    assert len(thought) == 1 and thought[0].seconds == 8 and thought[0].text == "plan"
    out = run.feed({"type": "agent_done", "message_id": "m1", "text": "Stopped.", "success": False, "error": "cancelled"}, 9)
    assert not any(isinstance(b, B.Note) and "cancel" in b.text for b in out)


def test_malformed_events_never_raise():
    run = Run("r", 0.0, glyphs=Glyphs())
    for evt in ({"type": "tool_end", "duration_ms": "x"}, {"type": "agent_start", "agent": "nope"}, {}, {"type": "token_usage", "prompt": "?"}):
        run.feed(evt, 0)


# ── outcome wording ─────────────────────────────────────────────────
def g():
    return Glyphs()


def test_outcome_says_done_only_when_verified():
    done = {"status": "done", "summary": "Fixed it."}
    v = outcome_blocks(outcome=done, work={"status": "completed", "verification": "checks pass"}, journal="completed",
                       success=True, error="", caps_glyphs=g())
    assert v[0].glyph == "◆" and v[0].style == "accent" and v[0].headline == "Done: Fixed it." and v[0].check == "Checks pass"
    nv = outcome_blocks(outcome=done, work={"status": "completed", "verification": "observed"}, journal="completed",
                        success=True, error="", caps_glyphs=g())
    assert nv[0].glyph == "◇" and nv[0].headline.startswith("Done (not verified)") and "no check" in nv[0].check
    w = outcome_blocks(outcome=None, work={"status": "completed", "verification": "observed"}, journal="completed",
                       success=True, error="", caps_glyphs=g())
    assert w[0].headline == "Finished (not verified)"
    ok = outcome_blocks(outcome=None, work={"status": "completed", "verification": "verified"}, journal="completed",
                        success=True, error="", caps_glyphs=g())
    assert ok[0].headline == "Done · Verified" and ok[0].style == "accent"
    assert outcome_blocks(outcome=None, work=None, journal="completed", success=True, error="", caps_glyphs=g()) == []


def test_outcome_stopped_failed_denied():
    s = outcome_blocks(outcome=None, work=None, journal="cancelled", success=None, error="", caps_glyphs=g())
    assert s[0].headline == "Stopped: you stopped it"
    s = outcome_blocks(outcome={"status": "stopped", "reason": "budget"}, work=None, journal="completed", success=True,
                       error="", caps_glyphs=g())
    assert s[0].headline == "Stopped: budget"
    f = outcome_blocks(outcome=None, work=None, journal="failed", success=False, error="boom", caps_glyphs=g())
    assert f[0].headline == "The run failed: boom" and f[0].style == "error"
    d = outcome_blocks(outcome=None, work=None, journal="completed", success=False, error="", caps_glyphs=g(), denied=True)
    assert d[0].headline == "Stopped: you denied a step it needed"


def test_finish_adds_separator():
    run, _ = run_of([(0, start()), (1, {"type": "tool_end", "message_id": "m1", "id": "t", "name": "file_read", "ok": True})])
    out = run.finish("completed", 42)
    assert isinstance(out[-1], B.RuleLine) and out[-1].text == "Worked for 42s · Read 1 file"
    run2, _ = run_of([(0, start()), (0, {"type": "agent_token", "message_id": "m1", "data": "hi"})])
    assert run2.finish("completed", 3)[-1].text == "Answered in 3s"


# ── spinner, shimmer, numbers ───────────────────────────────────────
def test_ping_is_time_based():
    assert [M.ping_frame(t) for t in (0, 119, 120, 959, 960, 1080)] == [0, 0, 1, 7, 0, 1]
    assert M.ping(0, CAPS)[1] == "·" and M.ping(360, CAPS)[1] == "◉"
    assert M.ping(360, CAPS, stalled_ms=11_000)[0] == "status.glyph.dim"
    assert M.ping(360, Caps(reduced_motion=True)) == ("status.glyph", "●")
    assert M.ping(360, Caps(glyphs=ASCII_GLYPHS))[1] == "0"


def test_shimmer_band_moves_and_stays_in_bounds():
    a = M.shimmer_levels(40, 0)
    b = M.shimmer_levels(40, 900)
    assert len(a) == 40 and set(a) <= {0, 1, 2} and a != b
    peak = b.index(2)
    assert 10 < peak < 30                      # half-way through the 1.8 s sweep: near the middle
    assert M.shimmer("abc", 0, Caps(reduced_motion=True)) == [("", "abc")]


def test_typewriter_uses_gui_timings():
    p = ("ab",)
    assert M.typewriter(0, p) == "a"
    assert M.typewriter(34, p) == "ab"
    assert M.typewriter(68 + 2199, p) == "ab"           # holding
    assert M.typewriter(68 + 2200, p) == "a"            # deleting at 18 ms/char
    assert M.typewriter(68 + 2200 + 36, p) == ""        # gap
    assert M.typewriter(68 + 2200 + 36 + 350, p) == "a"  # next cycle


def test_elapsed_and_tokens():
    assert [M.fmt_elapsed(s) for s in (0, 59, 65, 3723)] == ["0s", "59s", "1m 05s", "1h 02m 03s"]
    assert [M.fmt_tokens(n) for n in (999, 1200, 15000, 250000, 2_500_000)] == ["999", "1.2k", "15k", "250k", "2.5M"]


def test_status_line_drops_optional_parts_by_width():
    si = M.StatusInput(activity="Echo is thinking it through", elapsed_s=14, tokens=1200, detail="", stalled_ms=0)
    wide = text_of(M.status_line(si, 0, 120, CAPS))
    assert wide.endswith("(14s · ↓ 1.2k tokens · esc to stop)") and "Echo is thinking it through…" in wide
    mid = text_of(M.status_line(si, 0, 52, CAPS))
    assert "tokens" not in mid and mid.endswith("(14s · esc to stop)")
    small = text_of(M.status_line(si, 0, 44, CAPS))
    assert small.endswith("(esc to stop)") and "14s" not in small
    # Narrower: the real activity stays (cut), the stop hint shortens; never a generic "Working…".
    tiny = text_of(M.status_line(si, 0, 30, CAPS))
    assert tiny.startswith("· Echo is thinking") and tiny.endswith("(esc)") and M.width_of(tiny) <= 30
    for width in range(1, 130):
        assert M.frag_width(M.status_line(si, 0, width, CAPS)) <= width
    stall = text_of(M.status_line(M.StatusInput("Echo is starting", 40, stalled_ms=31_000), 0, 120, CAPS))
    assert "still waiting (31s)" in stall
    ok = text_of(M.status_line(M.StatusInput("Echo needs your OK", 3, needs_ok=True), 0, 120, CAPS))
    assert ok == "● Echo needs your OK"


def test_footer_composition():
    fi = M.FooterInput(model="qwen3.5:9b", agent="Jarvis", project="echospeak", approval_mode="smart", context_pct=64)
    line = text_of(M.footer_lines(fi, 120, CAPS)[0])
    assert "qwen3.5:9b · context 64% · Jarvis · echospeak · ◇ ask for risky actions" in line
    assert line.rstrip().endswith("? for shortcuts")
    bold = M.footer_lines(M.FooterInput(model="m", context_pct=90), 120, CAPS)[0]
    assert ("class:footer.bold", "context 90%") in bold
    # Context is shown whenever it is known, not only past 50 %.
    assert "context 40%" in text_of(M.footer_lines(M.FooterInput(model="m", context_pct=40), 120, CAPS)[0])
    running = text_of(M.footer_lines(M.FooterInput(model="m", running=True), 120, CAPS)[0])
    assert running.rstrip().endswith("esc to stop · enter to steer · tab to queue")
    auto = M.footer_lines(M.FooterInput(model="m", approval_mode="autonomous", held=2), 120, CAPS)
    assert len(auto) == 2 and "shift+tab to cycle" in text_of(auto[1]) and "⏸ 2 held" in text_of(auto[0])
    # Narrow: items shorten one at a time and keep their order; nothing jumps or disappears all at once.
    narrow = text_of(M.footer_lines(M.FooterInput(model="m", agent="Jarvis", approval_mode="autonomous",
                                                  context_pct=70), 50, CAPS)[0])
    assert narrow.strip().startswith("m · context 70% · Jarvis · ◇ auto")
    for width in range(1, 201):
        for line in M.footer_lines(fi, width, CAPS):
            assert M.frag_width(line) <= width
        first = text_of(M.footer_lines(fi, width, CAPS)[0])
        if width >= 12:
            assert "64%" in first, (width, first)        # context survives at every usable width
        if width >= 16:
            assert first.strip().startswith("qwen"), (width, first)


def test_footer_degrades_one_step_at_a_time():
    fi = M.FooterInput(model="qwen3.5:9b", agent="Jarvis", project="echospeak", approval_mode="smart", context_pct=64,
                       held=2)
    widths = {}
    for width in range(12, 140):
        widths[width] = M.frag_width(M.footer_lines(fi, width, CAPS)[0])
    # Going one column narrower never removes more than one item's worth of text (no cliffs).
    texts = [text_of(M.footer_lines(fi, w, CAPS)[0]).split() for w in range(12, 140)]
    for a, b in zip(texts, texts[1:]):
        assert len(b) - len(a) <= 6
    held = text_of(M.footer_lines(fi, 40, CAPS)[0])
    assert "⏸" in held and "64%" in held


def test_rule_fits_width():
    for w in (20, 70, 120):
        assert M.frag_width(M.rule(w, "Fix the parser", CAPS)) == w
    assert text_of(M.rule(30, "", CAPS)) == "─" * 30


# ── approvals and dialogs ───────────────────────────────────────────
def test_approval_options_match_backend_decisions():
    full = {"tool": "terminal", "grantable": True, "persistable": True, "scope_label": "Run safe commands"}
    assert [o.value for o in approval_options(full)] == ["allow", "always", "persist", "deny", "steer_deny"]
    assert [o.key for o in approval_options(full)] == ["y", "c", "a", "n", "esc"]
    assert "terminal commands for this chat" in approval_options(full)[1].label
    assert [o.value for o in approval_options({"grantable": False, "persistable": True})] == ["allow", "deny", "steer_deny"]
    recovered = {"recovered": True, "grantable": True, "persistable": True, "scope_label": "x"}
    assert [o.value for o in approval_options(recovered)] == ["allow", "deny", "steer_deny"]
    assert approval_options({}, agent="Jarvis")[-1].label == "No, and tell Jarvis what to do instead"
    # A read-only folder grant names the folder (backend agent/lean/file_access.py).
    read = {"tool": "read:/home/dev/Work", "grantable": True, "persistable": True,
            "scope_label": "Read files in /home/dev/Work (read-only)"}
    labels = [o.label for o in approval_options(read)]
    assert labels[1] == "Yes, and allow reading /home/dev/Work for this chat"
    assert "(read-only)" in labels[2]


def test_dialog_never_defaults_to_allow_and_guards_typeahead():
    d = Dialog("Echo needs your OK", approval_options({"grantable": True}), selected=-1, opened_at=10.0,
               escape_value="__steer__")
    assert d.key("y", 10.1).kind == "none"          # typed before the dialog could be read
    assert d.key("enter", 10.5).kind == "none"      # nothing is highlighted: Enter does nothing
    act = d.key("y", 10.5)
    assert (act.kind, act.value) == ("choose", "allow")
    d2 = Dialog("t", approval_options({"grantable": True}), selected=-1, opened_at=0.0, escape_value="__steer__")
    d2.key("down", 1)
    assert d2.selected == 0
    d2.key("up", 1)
    assert d2.selected == len(d2.options) - 1
    act = d2.key("3", 1)                            # allow, always, deny, tell: 3 is "No"
    assert (act.kind, act.value) == ("choose", "deny")


def test_tell_echo_instead_then_deny():
    d = Dialog("t", approval_options({}), selected=-1, opened_at=0.0, escape_value="__steer__")
    assert d.key("escape", 0.0).kind == "none" and d.text_mode       # Esc works even inside the guard
    act = d.submit_text("use pytest -x instead")
    assert (act.kind, act.value, act.text) == ("choose", "steer_deny", "use pytest -x instead")
    d.key("escape", 1)
    assert d.submit_text("   ").value == "deny"                       # empty: a plain deny
    d.key("escape", 1)
    assert d.cancel_text().value == "deny"                            # second Esc: a plain deny


def test_question_options():
    opts = question_options({"kind": "question", "options": ["A", "B"], "allow_other": True})
    assert [o.value for o in opts] == ["A", "B", None] and opts[-1].text_entry
    assert question_options({"kind": "question"})[0].text_entry


def test_filterable_picker():
    d = Dialog("Resume", [Option(x, x) for x in ("Fix parser", "Lighthouse story", "Parse dates")], filterable=True,
               opened_at=0)
    d.filter_text = "pars"
    assert [o.label for _i, o in d.visible_options()] == ["Fix parser", "Parse dates"]


# ── sanitizing server text ──────────────────────────────────────────
EVIL = "[bold red]pwn[/] [link=https://evil.example]click[/link] \x1b[31mred\x1b[0m \x1b]52;c;ZXZpbA==\x07 ‮abc"


@pytest.mark.parametrize("block", [
    B.MarkdownBlock(EVIL, bullet=True), B.Note("!", EVIL, "bold", 2), B.AgentHeader("x", EVIL, EVIL),
    B.ToolLine("ok", EVIL, EVIL, 10, [EVIL]), B.UserLine(EVIL), B.Outcome("◆", EVIL, "accent", EVIL),
    B.Table([[EVIL, EVIL]]), B.RuleLine(EVIL), B.ThinkingLine(3, EVIL),
])
def test_server_text_is_never_markup_or_escapes(block):
    out = B.render(block, 200, CAPS, verbose=True)
    shown = plain(out)
    assert "\x1b]" not in out and "\x07" not in out and "‮" not in out
    assert not re.search(r"\x1b\[31m", out)              # the server's SGR never survives
    flat = re.sub(r"\s+", "", shown)
    assert "[boldred]pwn[/]" in flat                      # rich markup stays literal text
    assert "[link=https://evil.example]click[/link]" in flat


def test_markup_lookalikes_stay_literal_in_markdown():
    shown = plain(B.render(B.MarkdownBlock("Use [bold]x[/bold] and [red]y[/]"), 80, CAPS))
    assert "[bold]x[/bold]" in shown and "[red]y[/]" in shown


# ── widths and fallbacks ────────────────────────────────────────────
def test_banner_width_fallback():
    banner = B.Banner("11.13", "Echo", "Personal agent", "qwen3.5:9b (ollama)", "~/w", "proj", "ask for risky actions",
                      "Build a feature, fix a bug, or explore your code.")
    wide = plain(B.render(banner, 100, CAPS))
    assert "▗▄▄▄▄▄▖" in wide and "EchoSpeak  v11.13 · terminal" in wide and "project proj" in wide
    narrow = plain(B.render(banner, 50, CAPS))
    assert narrow.strip().splitlines()[0] == "● EchoSpeak v11.13 · Echo · qwen3.5:9b (ollama)"


def test_tool_line_truncates_and_rendered_lines_fit():
    tl = B.ToolLine("ok", "Ran `" + "x" * 200 + "`", "summary", 1500, ["y" * 300])
    for width in (40, 70, 120):
        for line in plain(B.render(tl, width, CAPS)).splitlines():
            assert M.width_of(line) <= width
    assert plain(B.render(tl, 70, CAPS)).splitlines()[0].endswith("1.5s")


def test_no_color_keeps_weight_and_glyphs():
    out = B.render(B.Outcome("◆", "Done: x", "accent", "Checks pass"), 80, MONO)
    codes = set(re.findall(r"\x1b\[([0-9;]*)m", out))
    assert codes and all(set(c.split(";")) <= {"0", "1", "2", "3", ""} for c in codes)
    assert "◆ Done: x" in plain(out)


def test_rich_padding_is_stripped():
    out = B.render(B.Note("·", "short", "dim", 2), 120, CAPS)
    assert all(not plain(line).endswith(" ") for line in out.splitlines())


def test_ascii_mode_glyphs():
    caps = Caps(glyphs=ASCII_GLYPHS, ascii=True)
    out = plain(B.render(B.ToolLine("fail", "Ran x", "", 0, ["boom"]), 80, caps))
    assert out.startswith("  x Ran x") and "|  boom" in out
    assert all(ord(ch) < 128 for ch in text_of(M.status_line(M.StatusInput("Echo is working", 2), 0, 80, caps)))


# ── markdown stream ─────────────────────────────────────────────────
def test_mdstream_commits_at_blank_lines_outside_fences():
    s = MdStream()
    assert s.feed("# Title\n") == []
    assert s.feed("\nBody") == ["# Title"]
    assert s.feed("\n```py\nx = 1\n\ny = 2\n") == []          # blank line inside a fence: still open
    assert s.in_fence()
    assert s.feed("```\n") == ["Body\n```py\nx = 1\n\ny = 2\n```"]
    assert s.feed("tail") == [] and s.tail() == "tail"
    assert s.flush() == ["tail"] and s.tail() == ""


# ── commands ────────────────────────────────────────────────────────
def test_command_filter_prefix_then_substring():
    names = [c.name for c in filter_commands("/re", COMMANDS)]
    assert names[0] == "/resume"
    assert "/permissions" not in names[:1]
    assert [c.name for c in filter_commands("/sto", COMMANDS)][0] == "/stop"
    assert "/copy" in [c.name for c in filter_commands("/clipboard", COMMANDS)]
    assert lookup("/exit").name == "/quit"


def test_menu_shows_only_what_the_backend_supports():
    shown = {c.name for c in visible(COMMANDS, {"agents", "settings"})}
    assert "/agents" in shown and "/permissions" in shown and "/memory" not in shown and "/model" not in shown
    assert "/help" in shown and "/status" in shown


# ── control (models, modes) ─────────────────────────────────────────
def test_shift_tab_cycle_never_reaches_never():
    seen, mode = [], "smart"
    for _ in range(6):
        mode = control.next_approval_mode(mode)
        seen.append(mode)
    assert seen == ["autonomous", "always", "smart", "autonomous", "always", "smart"]
    assert control.next_approval_mode("never") == "smart"


def test_gpu_fit_hint():
    gib = 1024 ** 3
    assert control.fit_hint(int(6.1 * gib), 8192) == (True, "6.1 GB · fits your GPU")
    assert control.fit_hint(int(16.5 * gib), 8192) == (False, "16.5 GB · slow: spills to RAM")
    assert control.fit_hint(int(6.1 * gib), 0) == (None, "6.1 GB")


def test_model_setup_reason():
    s = {"use_local_models": True, "local": {"provider": "ollama", "model_name": "qwen3.5:9b"}}
    installed = ["qwen3.5:9b", "qwen3.8:27b"]
    assert control.model_setup_reason(s, installed, "ollama:qwen3.5:9b") == ""
    assert "chosen automatically" in control.model_setup_reason(s, installed, "")
    assert "not installed" in control.model_setup_reason({**s, "local": {"provider": "ollama", "model_name": "gone"}}, installed, "")
    assert "No model" in control.model_setup_reason({**s, "local": {"provider": "ollama", "model_name": ""}}, installed, "")
    assert control.model_setup_reason({"use_local_models": False}, installed, "") == ""
    assert control.model_setup_reason({**s, "local": {"provider": "lmstudio", "model_name": "x"}}, installed, "") == ""


def test_prefs_round_trip(tmp_path):
    env = {"ECHOSPEAK_STATE_DIR": str(tmp_path / "st")}
    assert control.confirmed_model("/d", env) == ""
    control.confirm_model("/d", "ollama:qwen3.5:9b", env)
    assert control.confirmed_model("/d", env) == "ollama:qwen3.5:9b"
    assert control.confirmed_model("/other", env) == ""


# ── theme ───────────────────────────────────────────────────────────
def test_detect_environment():
    assert detect({"NO_COLOR": "1", "LANG": "en_US.UTF-8"}).color is False
    assert detect({"LANG": "C"}).ascii is True and detect({"LANG": "C"}).glyphs.prompt == ">"
    assert detect({"ECHO_REDUCED_MOTION": "1"}).reduced_motion is True
    assert detect({}, no_anim=True).reduced_motion is True
    assert detect({"COLORTERM": "truecolor", "ECHO_TUI_ACCENT": "echo"}).accent_hex == "#4c72df"
    assert detect({"ECHO_TUI_ACCENT": "echo"}).accent_hex == ""          # not without truecolor
    assert detect({"ECHO_TUI_VERBS": "plain"}).plain_verbs is True


def test_flavour_verbs():
    assert M.flavour(0, 1500, CAPS) == ""
    assert M.flavour(0, 2000, CAPS) == "Listening"
    assert M.flavour(0, 6000, CAPS) == "Tuning in"
    assert M.flavour(0, 9000, Caps(plain_verbs=True)) == ""


def test_folder_offer_skips_home_root_and_remembered_declines(tmp_path):
    from echospeak_cli import control

    home = tmp_path / "home"
    project = home / "code" / "app"
    project.mkdir(parents=True)
    env = {"HOME": str(home), "ECHOSPEAK_STATE_DIR": str(tmp_path / "state")}
    assert control.folder_offer(str(project), env)
    assert not control.folder_offer(str(home), env)          # too broad to offer
    assert not control.folder_offer("/", env)
    assert not control.folder_offer(str(tmp_path / "missing"), env)
    control.decline_folder(str(project), env)
    assert not control.folder_offer(str(project), env)       # asked once; "no" is remembered
    control.forget_folder_decline(str(project), env)
    assert control.folder_offer(str(project), env)


def test_resume_recap_is_plain_text_not_markdown():
    from echospeak_cli.tui.app import plain_recap

    md = "# How unittest works\n\nPython's `unittest` is **elegant**.\n\n## Discovery\n- finds `test_*.py`\n1. see [docs](https://x.y)"
    assert plain_recap(md) == "How unittest works Python's unittest is elegant. Discovery finds test_*.py see docs"


def test_several_edits_to_one_file_count_as_one_changed_file():
    tools = [{"name": "file_edit", "path": "/p/stats.py"}, {"name": "file_edit", "path": "/p/stats.py"},
             {"name": "file_write", "path": "/p/test_stats.py"}, {"name": "terminal"}]
    assert steps_summary(tools) == "Changed 2 files · Ran 1 command"


def test_memory_receipt_replaces_intermediate_saved_event():
    run, out = run_of([(0, start()),
        (1, {"type": "tool_start", "message_id": "m1", "id": "mem", "name": "memory_save", "label": "Saving to memory"}),
        (2, {"type": "memory_saved", "memory_count": 1}),
        (3, {"type": "tool_end", "message_id": "m1", "id": "mem", "name": "memory_save", "ok": True,
             "done_label": "Saved to memory", "summary": "Saved", "meta": {"memory_verified": True}})])
    receipts = [b for b in out if isinstance(b, B.ToolLine)]
    assert len(receipts) == 1 and receipts[0].summary == "verified on disk"
    assert not any(isinstance(b, B.Note) and b.text == "Saved to memory" for b in out)
    assert run.feed({"type": "memory_saved"}, 4)  # old backend, standalone event
