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
