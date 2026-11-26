"""layout.py: width tiers, and the height allocator."""
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
