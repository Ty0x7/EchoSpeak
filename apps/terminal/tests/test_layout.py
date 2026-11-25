"""layout.py: width tiers, the height allocator, and the resize reflow arithmetic."""
from __future__ import annotations

from echospeak_cli.tui import layout as L


def test_tiers_and_budget():
    assert [L.tier(w) for w in (10, 29, 30, 59, 60, 99, 100, 300)] == \
        ["micro", "micro", "narrow", "narrow", "regular", "regular", "wide", "wide"]
    assert L.budget(1) == 1 and L.budget(2) == 1 and L.budget(24) == 23


def test_allocate_keeps_priorities_and_never_exceeds_budget():
    wants = [L.Want("tail", 8, 1, 6), L.Want("status", 1, 1, 1), L.Want("spacer", 1, 0, 9),
             L.Want("input", 3, 1, 0), L.Want("footer", 1, 1, 1)]
    for rows in range(20):
        alloc = L.allocate(wants, rows)
        assert sum(alloc.values()) <= rows
    assert L.allocate(wants, 1) == {"tail": 0, "status": 0, "spacer": 0, "input": 1, "footer": 0}
    assert L.allocate(wants, 3) == {"tail": 0, "status": 1, "spacer": 0, "input": 1, "footer": 1}
    # Minimums first (the tail gets a line), then growth by priority (the input before the tail).
    assert L.allocate(wants, 6) == {"tail": 1, "status": 1, "spacer": 0, "input": 3, "footer": 1}
    # The spacer only gets leftovers.
    assert L.allocate(wants, 13)["spacer"] == 0 and L.allocate(wants, 14)["spacer"] == 1


def test_allocate_priority_zero_takes_what_is_left():
    alloc = L.allocate([L.Want("dialog", 12, 9, 0), L.Want("footer", 1, 1, 1)], 5)
    assert alloc == {"dialog": 5, "footer": 0}


def test_reflow_counts_rewrapped_rows():
    # An 80-column region: rule (80), prompt line (40, cursor at col 12), rule (80), footer (70).
    lengths = [80, 40, 80, 70]
    assert L.reflowed_rows_above(lengths, 1, 12, 80) == 1            # no reflow
    assert L.reflowed_rows_above(lengths, 1, 12, 40) == 2            # the top rule became 2 rows
    assert L.reflowed_rows_above(lengths, 1, 12, 30) == 3            # 80/30 → 3 rows
    assert L.reflowed_rows_above(lengths, 1, 35, 30) == 4            # the cursor's own line wrapped too
    assert L.reflowed_rows_above([0, 0, 50], 2, 0, 20) == 2          # blank rows still take one row
    assert L.rows_after_reflow(0, 10) == 1 and L.rows_after_reflow(21, 10) == 3


def test_terminal_reflow_guess():
    assert L.terminal_reflows({"TERM": "xterm-ghostty"})
    assert L.terminal_reflows({"TERM": "tmux-256color", "TMUX": "/tmp/x"})
    assert L.terminal_reflows({"TERM": "foot"}) and L.terminal_reflows({"TERM": "alacritty"})
    assert L.terminal_reflows({"TERM": "xterm-256color", "VTE_VERSION": "7600"})
    assert not L.terminal_reflows({"TERM": "xterm-256color"})        # plain xterm crops
    assert not L.terminal_reflows({"TERM": "linux"})
    assert not L.terminal_reflows({"TERM": "xterm-ghostty", "ECHO_TUI_REFLOW": "0"})
    assert L.terminal_reflows({"TERM": "linux", "ECHO_TUI_REFLOW": "1"})


# ── live region fit (top-anchored growth, bottom pin, borrowed rows) ──────────
def test_fit_unpinned_while_rows_remain_below():
    fit = L.LiveRegionFit()
    assert fit.floor(5, 23) == 0          # unknown position: no floor
    fit.reported(20)
    assert fit.floor(5, 23) == 0          # 15 free rows under the region
    fit.rendered(5)
    assert fit.below == 20


def test_fit_pins_once_the_region_reaches_the_bottom():
    fit = L.LiveRegionFit()
    fit.reported(4)
    fit.rendered(12)                      # a taller render scrolled the terminal
    assert fit.below == 12
    assert fit.floor(12, 31) == 12        # keeps every row down to the bottom when content shrinks
    fit.stale()
    assert not fit.known and fit.floor(12, 31) == 0


def test_fit_borrow_and_give_back_move_the_top():
    fit = L.LiveRegionFit()
    fit.reported(4)
    fit.borrow(8)
    assert (fit.below, fit.borrowed) == (12, 8)
    fit.give_back(5)
    assert (fit.below, fit.borrowed) == (7, 3)
    fit.give_back(10)
    assert (fit.below, fit.borrowed) == (4, 0)


def test_committed_rows_keep_plain_rows_exactly():
    rows = L.CommittedRows()
    rows.feed("\x1b[1mhello\x1b[0m\nworld\n", 20)
    rows.feed("\x1b]8;;https://x\x1b\\link\x1b]8;;\x1b\\\n", 20)
    assert rows.last(3) == ["\x1b[1mhello\x1b[0m", "world", "\x1b]8;;https://x\x1b\\link\x1b]8;;\x1b\\"]
    assert rows.last(4) is None
    assert rows.last(0) == []


def test_committed_rows_forget_what_they_cannot_place():
    rows = L.CommittedRows()
    rows.feed("a\nb\n", 10)
    rows.feed("x" * 11 + "\n", 10)        # wraps: the rows above it no longer line up
    assert rows.last(1) is None
    rows.feed("c\n\x1b[2J\x1b[Hd\ne\n", 10)
    assert rows.last(1) == ["e"] and rows.last(2) is None   # the clearing row itself is dropped too
    rows.feed("half", 10)                 # the cursor is mid-row: nothing can be drawn back yet
    assert rows.last(1) is None
    rows.feed(" done\n", 10)
    assert rows.last(1) == ["half done"]


def test_committed_rows_restore_clears_each_row_first():
    out = L.CommittedRows.restore(["one", "two"])
    assert out == "\r\x1b[0m\x1b[2Kone\x1b[0m\r\n\r\x1b[0m\x1b[2Ktwo\x1b[0m\r\n"
