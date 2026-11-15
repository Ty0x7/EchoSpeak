"""Edit and diff previews (docs/research/edit-preview.md): diff rows at several widths, NO_COLOR and ASCII,
the scrollback block, the approval layouts, /diff helpers, and the preview viewer's model.

Needs prompt_toolkit and rich (apps/terminal/.venv); skipped without them.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

import pytest

pytest.importorskip("prompt_toolkit")
pytest.importorskip("rich")

from echospeak_cli import discovery  # noqa: E402
from echospeak_cli.client import Client  # noqa: E402
from echospeak_cli.tui import blocks as B  # noqa: E402
from echospeak_cli.tui import diffview as DV  # noqa: E402
from echospeak_cli.tui.motion import width_of  # noqa: E402
from echospeak_cli.tui.preview import Model, read_key_file  # noqa: E402
from echospeak_cli.tui.state import Run  # noqa: E402
from echospeak_cli.tui.theme import ASCII_GLYPHS, Caps, Glyphs, detect, diff_tints  # noqa: E402

ANSI = re.compile(r"\x1b\[[0-9;:]*m")
COLOR = Caps()
MONO = Caps(color=False)
ASCII = Caps(color=False, ascii=True, glyphs=ASCII_GLYPHS)
TINT = Caps(truecolor=True, diff_tint=True, bg_hex="#16242d", fg_hex="#d6e2ee")


def plain(text: str) -> str:
    return ANSI.sub("", text)


def record(**extra) -> dict:
    base = {
        "op": "edit", "path": "/p/src/dates.py", "display_path": "src/dates.py", "language": "python",
        "added": 3, "removed": 1, "status": "applied", "change_id": "chg_1", "tool_call_id": "c1",
        "hunks": [
            {"old_start": 41, "old_lines": 5, "new_start": 41, "new_lines": 7, "lines": [
                [" ", "def iso_week(d):"], [" ", "    y, w, _ = d.isocalendar()"],
                ["-", "    if w == 53: return (y, 52)"], ["+", "    if w == 53 and not _long_year(y):"],
                ["+", "        return (y + 1, 1)"], [" ", "    return (y, w)"]]},
            {"old_start": 88, "old_lines": 1, "new_start": 90, "new_lines": 2, "lines": [
                [" ", "def _long_year(y):"], ["+", '    """ISO years with 53 weeks."""']]},
        ],
    }
    base.update(extra)
    return base


def long_record(n: int = 40, op: str = "edit") -> dict:
    lines = [["+", f"line {i} = {i}"] for i in range(1, n + 1)]
    return record(op=op, added=n, removed=0, hunks=[{"old_start": 0, "old_lines": 0, "new_start": 1, "new_lines": n,
                                                      "lines": lines}])


def block(raw: dict, width: int, caps: Caps = COLOR, verbose: bool = False) -> str:
    return B.render(B.FileDiff(DV.parse(raw), "ok", 100), width, caps, verbose=verbose)


# ── the scrollback block ─────────────────────────────────────────────
@pytest.mark.parametrize("width", [70, 100, 140, 200])
def test_block_fits_every_width_with_numbers_signs_and_separator(width):
    out = block(record(), width)
    lines = plain(out).rstrip("\n").split("\n")
    assert all(width_of(line) <= width for line in lines), [line for line in lines if width_of(line) > width]
    assert lines[0].startswith("  ✓ Edited src/dates.py · +3 −1")
    assert lines[0].rstrip().endswith("0.1s")
    body = "\n".join(lines[1:])
    assert re.search(r"^\s+43  -    if w == 53: return \(y, 52\)$", body, re.M)       # old number on "-"
    assert re.search(r"^\s+43  \+    if w == 53 and not _long_year\(y\):$", body, re.M)  # new number on "+"
    assert re.search(r"^\s+41   def iso_week\(d\):$", body, re.M)                          # context: blank sign
    assert "⋮" in body


def test_narrow_drops_numbers_and_long_lines_wrap_with_a_blank_gutter():
    raw = record(hunks=[{"old_start": 9, "old_lines": 0, "new_start": 9, "new_lines": 1,
                         "lines": [["+", "x = '" + "y" * 150 + "'"]]}], added=1, removed=0)
    narrow = plain(block(raw, 50)).split("\n")
    assert narrow[1].startswith("    +x = '")                     # under 60 columns: sign only
    wide = plain(block(raw, 100)).split("\n")
    assert wide[1].startswith("     9  +x = '")
    assert wide[2].startswith("        ") and wide[2].strip().startswith("y")   # continuation, no number
    assert all(width_of(x) <= 100 for x in wide)


def test_inline_rows_are_capped_and_point_at_ctrl_o():
    out = plain(block(long_record(40), 100)).rstrip("\n").split("\n")
    assert len(out) == 1 + 16 + 1
    assert out[-1].strip() == "… +24 lines · ctrl+o"
    created = plain(block(long_record(40, "create"), 100)).rstrip("\n").split("\n")
    assert created[0].startswith("  ✓ Created src/dates.py · +40")
    assert len(created) == 1 + 8 + 1 and created[-1].strip() == "… +32 lines · ctrl+o"
    deleted = plain(block(record(op="delete", added=0, removed=112), 100)).rstrip("\n").split("\n")
    assert deleted == [deleted[0]] and "Deleted src/dates.py · −112" in deleted[0]
    full = plain(block(long_record(40), 100, verbose=True)).rstrip("\n").split("\n")
    assert len(full) == 41 and "ctrl+o" not in full[-1]


def test_additions_and_deletions_do_not_depend_on_hue():
    mono = block(record(), 100, MONO)
    codes = set(re.findall(r"\x1b\[([0-9;]*)m", mono))
    assert codes <= {"0", "1", "2", "22", ""}, codes            # NO_COLOR: bold and dim only
    rows = mono.split("\n")
    add = next(r for r in rows if "_long_year(y):" in r and "+" in plain(r))
    rem = next(r for r in rows if "return (y, 52)" in r)
    assert "\x1b[1m+" in add                                       # bold +
    assert "\x1b[1m-" in rem and "\x1b[2m    if w == 53: return" in rem   # bold -, dim code
    color = block(record(), 100, COLOR)
    assert "\x1b[1;32m+" in color and "\x1b[1;31m-" in color   # hue only reinforces the sign
    assert "48;2" not in color                                    # no tints without truecolor


def test_truecolor_tints_differ_in_luminance_only():
    add_bg, del_bg = diff_tints(TINT)
    lum = lambda h: sum(int(h[i:i + 2], 16) for i in (1, 3, 5))  # noqa: E731
    assert lum(add_bg) > lum(TINT.bg_hex) > lum(del_bg)
    out = block(record(), 100, TINT)
    assert "48;2;41;55;64" in out and "48;2;14;23;29" in out
    assert diff_tints(Caps(truecolor=True)) == ("", "")          # no theme background: no tint
    env = {"COLORTERM": "truecolor", "ECHO_TUI_BG": "#202020", "HOME": "/nonexistent"}
    assert detect(env).diff_tint and not detect({**env, "ECHO_TUI_DIFF_TINT": "0"}).diff_tint
    assert not detect({"ECHO_TUI_BG": "#202020", "HOME": "/nonexistent"}).diff_tint


def test_ascii_mode_uses_ascii_glyphs():
    out = plain(block(record(), 100, ASCII))
    assert out.startswith("  + Edited src/dates.py (+3 -1)")
    assert "⋮" not in out and "−" not in out and "✓" not in out
    assert re.search(r"^\s+:$", out, re.M)


def test_server_text_is_sanitized_never_markup():
    raw = record(display_path="src/[bold red]x\x1b[31m.py", hunks=[{"old_start": 1, "old_lines": 1, "new_start": 1,
                                                                     "new_lines": 1, "lines": [["+", "a\x1b]52;c;ZZ\x07[link=x]b"]]}])
    out = block(raw, 100)
    assert "\x1b]52" not in out and "\x1b[31m.py" not in out
    assert "[bold red]x" in plain(out) and "[link=x]b" in plain(out)
    assert DV.parse({"hunks": []}) is None and DV.parse("nope") is None


def test_split_view_pairs_rows():
    lines = [plain(x) for x in DV.to_ansi(DV.split(DV.parse(record()), 160, MONO), 160, MONO)]
    pair = next(x for x in lines if "return (y, 52)" in x)
    assert "│" in pair and pair.index("return (y, 52)") < pair.index("│") < pair.index("_long_year(y)")
    lone = next(x for x in lines if "return (y + 1, 1)" in x)
    assert lone.split("│")[0].strip() == ""                       # unmatched addition: blank old side
    assert all(width_of(x) <= 160 for x in lines)


def test_syntax_highlighting_survives_odd_input():
    raw = record(language="python", hunks=[{"old_start": 1, "old_lines": 1, "new_start": 1, "new_lines": 2,
                                            "lines": [["-", 's = """open'], ["+", 's = """open'], ["+", "\tclose\"\"\""]]}])
    out = plain(block(raw, 100))
    assert '    close"""' in out                                    # tabs become 4 spaces
    assert "_long_year(y):" in plain(block(record(language="no-such-lexer"), 100))   # unknown lexer: plain rows


# ── the reducer ──────────────────────────────────────────────────────
def test_tool_end_with_a_file_change_prints_a_diff_block():
    run = Run("r", 0.0, glyphs=Glyphs())
    run.feed({"type": "agent_start", "message_id": "m1", "agent": {"id": "echo", "name": "Echo"}}, 0)
    run.feed({"type": "tool_start", "message_id": "m1", "id": "c1", "name": "file_edit", "label": "Editing a file"}, 0)
    out = run.feed({"type": "tool_end", "message_id": "m1", "id": "c1", "name": "file_edit", "ok": True,
                    "done_label": "Edited a file", "duration_ms": 120, "meta": {"file_change": record()}}, 1)
    diffs = [b for b in out if isinstance(b, B.FileDiff)]
    assert len(diffs) == 1 and not diffs[0].full
    assert [b for b in run.transcript if isinstance(b, B.FileDiff)][0].full
    assert run.changes[0].display == "src/dates.py"
    # A failed tool keeps its plain tool line
    out = run.feed({"type": "tool_end", "id": "c2", "name": "file_edit", "ok": False, "output": "Error: nope",
                    "meta": {"file_change": record()}}, 2)
    assert not any(isinstance(b, B.FileDiff) for b in out)


def test_denying_a_previewed_edit_names_the_file():
    run = Run("r", 0.0, glyphs=Glyphs())
    run.feed({"type": "approval_request", "id": "apr_1", "tool": "file_edit", "summary": "Edit dates.py",
              "tool_call_id": "c1", "file_change": record(status="proposed")}, 0)
    out = run.feed({"type": "approval_resolved", "id": "apr_1", "decision": "deny", "tool_call_id": "c1"}, 1)
    assert [n.text for n in out if isinstance(n, B.Note)] == ["Denied edit · src/dates.py"]
    # the denied tool's own line would only repeat it
    assert run.feed({"type": "tool_end", "id": "c1", "name": "file_edit", "ok": False, "outcome": "denied"}, 2) == []


# ── the approval dialog ──────────────────────────────────────────────
class _FakeClient:
    base_url = "http://127.0.0.1:9"
    key = "k" * 32


@pytest.fixture
def tui(monkeypatch, tmp_path):
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    from echospeak_cli.tui.app import EchoTUI

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        app = EchoTUI(_FakeClient(), discovery.Endpoint("http://127.0.0.1:9", "k", "endpoint-file"), MONO,
                      env={"HOME": str(tmp_path), "XDG_STATE_HOME": str(tmp_path)})
        yield app


def _approval(**extra) -> dict:
    return {"type": "approval_request", "id": "apr_1", "tool": "file_edit", "summary": "Edit src/dates.py",
            "reason": "action tool", "args": {"path": "src/dates.py", "old_text": "a", "new_text": "b"},
            "grantable": True, "file_change": record(status="proposed"), **extra}


def _frag_text(frags) -> str:
    return "".join(t for _s, t, *_ in frags)


def test_wide_approval_shows_options_left_and_the_diff_right(tui, monkeypatch):
    monkeypatch.setattr(tui, "width", lambda: 140)
    monkeypatch.setattr(tui, "height", lambda: 40)
    tui.dialog = tui._approval_dialog(_approval())
    assert tui._two_col()
    left = _frag_text(tui._dialog_text()).split("\n")
    right = _frag_text(tui._diff_pane_text()).split("\n")
    lw = tui._left_w()
    assert all(width_of(x) <= lw for x in left)
    assert "Edit a file" in left[1] and "src/dates.py" in left[2]
    labels = [o.label for o in tui.dialog.options]
    assert labels == ["Yes, allow once", "Yes, and allow file changes for this chat", "No",
                      "No, and tell Echo what to do instead"]
    assert any("Yes, and allow file changes for this chat" in x for x in left)   # labels are never cut
    assert right[0].startswith("┬─ src/dates.py +3 −1") and right[0].rstrip().endswith("rows 1–9 of 9 ─")
    assert all(x.startswith("│ ") for x in right[1:])
    assert any("43  -    if w == 53" in x for x in right)
    assert not any("old_text" in x for x in left)                    # the diff replaces the JSON arguments
    assert "pgup/pgdn diff" in tui.dialog.footer and "ctrl+g full diff" in tui.dialog.footer
    assert all(width_of(x) <= 140 - lw for x in right)


def test_narrow_approval_stacks_a_capped_diff(tui, monkeypatch):
    monkeypatch.setattr(tui, "width", lambda: 90)
    monkeypatch.setattr(tui, "height", lambda: 40)
    evt = _approval(file_change=long_record(30))
    tui.dialog = tui._approval_dialog(evt)
    assert not tui._two_col()
    text = _frag_text(tui._dialog_text()).split("\n")
    diff_rows = [x for x in text if re.match(r"^\s+\d+  \+line", x)]
    assert len(diff_rows) == 10
    assert any("+20 lines" in x and "ctrl+g" in x for x in text)
    tui.dialog.scroll_diff(8, 30, 10)
    text = _frag_text(tui._dialog_text())
    assert "line 9 = 9" in text and "line 1 = 1" not in text
    # the guard and the options are the existing ones
    assert tui.dialog.guard == 0.3 and tui.dialog.selected == -1


def test_withheld_diff_says_so(tui, monkeypatch):
    monkeypatch.setattr(tui, "width", lambda: 140)
    monkeypatch.setattr(tui, "height", lambda: 40)
    tui.dialog = tui._approval_dialog(_approval(file_change=record(hunks=[], binary=True, withheld_reason="a binary file")))
    assert not tui._two_col()
    assert "Diff not shown: a binary file" in _frag_text(tui._dialog_text())


# ── /diff and /preview helpers ───────────────────────────────────────
@pytest.mark.skipif(not shutil.which("git"), reason="needs git")
def test_git_diff_includes_untracked_files_and_strips_escapes(tmp_path):
    from echospeak_cli.tui.app import git_diff_text, git_toplevel

    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@x"}
    run = lambda *a: subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True, env=env)  # noqa: E731
    run("init", "-q")
    (tmp_path / "a.txt").write_text("one\n")
    run("add", "a.txt")
    run("commit", "-qm", "init")
    (tmp_path / "a.txt").write_text("two\n")
    (tmp_path / "new.txt").write_text("evil \x1b]52;c;QQ==\x07 text\n")
    assert git_toplevel(str(tmp_path)) == str(tmp_path.resolve())
    text = git_diff_text(str(tmp_path), 100)
    assert "+two" in plain(text) and "-one" in plain(text) and "new.txt" in text
    assert "1 untracked file(s) included" in text
    assert "\x1b]52" not in text and "evil" in text


def test_preview_command_never_puts_the_key_on_the_command_line(tmp_path, monkeypatch):
    from echospeak_cli.tui.app import preview_command

    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    client = Client("http://127.0.0.1:8123", "secret-key-123456789")
    argv, env = preview_command("sess-1", discovery.Endpoint(client.base_url, client.key, "env"), client,
                                {"ECHOSPEAK_KEY": "secret-key-123456789", "ECHOSPEAK_DATA_DIR": "/d", "HOME": "/h"})
    assert "secret-key-123456789" not in " ".join(argv) + json.dumps(env)
    assert "ECHOSPEAK_KEY" not in env and env["ECHOSPEAK_DATA_DIR"] == "/d"
    assert argv[argv.index("--follow") + 1] == "sess-1"
    path = argv[argv.index("--key-file") + 1]
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    assert read_key_file(path) == ("http://127.0.0.1:8123", "secret-key-123456789")
    assert not os.path.exists(path)                                 # read once, then gone
    argv, _ = preview_command("s", discovery.Endpoint("u", "k", "endpoint-file"), client, {})
    assert "--key-file" not in argv and "--url" not in argv          # the viewer finds the endpoint file itself


# ── the preview viewer model ─────────────────────────────────────────
def test_viewer_follows_proposed_then_applied_changes():
    m = Model()
    assert m.feed("r1", {"type": "approval_request", "id": "apr_1", "_replay_seq": 3,
                         "file_change": record(status="proposed")})
    assert m.files()[0].proposed and m.selected == "src/dates.py"
    assert (m.files()[0].added, m.files()[0].removed) == (3, 1)          # a proposal shows what it would change
    assert m.totals() == (1, 0, 0)                                        # but is not counted as applied
    assert not m.feed("r1", {"type": "approval_request", "id": "apr_1", "_replay_seq": 3,
                             "file_change": record(status="proposed")})     # replayed: ignored
    m.feed("r1", {"type": "approval_resolved", "id": "apr_1", "decision": "allow", "_replay_seq": 4})
    assert m.entries == []
    m.feed("r1", {"type": "tool_end", "ok": True, "_replay_seq": 5, "meta": {"file_change": record()}})
    other = record(display_path="tests/test_dates.py", path="/p/tests/test_dates.py", added=6, removed=2,
                   change_id="chg_2", tool_call_id="c2")
    m.feed("r2", {"type": "tool_end", "ok": True, "_replay_seq": 1, "meta": {"file_change": other}})
    files = m.files()
    assert [f.display for f in files] == ["tests/test_dates.py", "src/dates.py"]   # newest first
    assert (files[0].turn, files[1].turn) == (2, 1)
    assert m.selected == "tests/test_dates.py" and m.totals() == (2, 9, 3)
    m.move_file(1)
    assert m.selected == "src/dates.py" and not m.following
    m.feed("r2", {"type": "tool_end", "ok": True, "_replay_seq": 2, "meta": {"file_change": {**other, "change_id": "c3"}}})
    assert m.selected == "src/dates.py"                              # moved by hand: stays put
    m.follow_newest()
    assert m.selected == "tests/test_dates.py"


def test_viewer_marks_denied_proposals_and_renders_exact_height():
    m = Model()
    m.feed("r1", {"type": "approval_request", "id": "apr_9", "file_change": record(status="proposed")})
    m.feed("r1", {"type": "approval_resolved", "id": "apr_9", "decision": "deny"})
    assert m.entries[0].status == "denied"
    for width, height in ((80, 24), (60, 10), (200, 50)):
        rows = m.screen(width, height, MONO)
        assert len(rows) == height
        assert all(r.cell_len <= width for r in rows)
    text = "\n".join(r.plain for r in m.screen(80, 30, MONO))
    assert "DENIED" in text and "Would edit" in text and "unified" in text


def test_viewer_split_needs_150_columns():
    m = Model()
    m.feed("r1", {"type": "tool_end", "ok": True, "meta": {"file_change": record()}})
    m.toggle_split(80)
    assert not m.split and "150" in m.notice
    m.toggle_split(160)
    assert m.split
    text = "\n".join(r.plain for r in m.screen(160, 30, MONO))
    assert "│" in text and "split" in text
    text = "\n".join(r.plain for r in m.screen(100, 30, MONO))
    assert "unified" in text                                         # a narrower pane falls back


def test_viewer_orders_turns_by_run_not_by_arrival():
    m = Model()
    m.feed("old", {"type": "_run"})
    m.feed("new", {"type": "_run"})
    late = record(display_path="b.py", path="/p/b.py", change_id="c2")
    m.feed("new", {"type": "tool_end", "ok": True, "_replay_seq": 4, "meta": {"file_change": late}})
    m.feed("old", {"type": "tool_end", "ok": True, "_replay_seq": 9, "meta": {"file_change": record()}})
    assert [f.display for f in m.files()] == ["b.py", "src/dates.py"]
    assert m.selected == "b.py" and m.files()[0].turn == 2
