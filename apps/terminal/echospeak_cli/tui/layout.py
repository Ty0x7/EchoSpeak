"""Width tiers and row allocation for the terminal viewport."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

# Width tiers (columns).
MICRO = 30      # below: only the essentials, no titles or hints
NARROW = 60     # below: one-line banner, compact footer, keys hidden when they don't fit
WIDE = 100      # from here: three-column shortcuts, full footer

# Prose (answers, notes) wraps at a readable measure on very wide terminals; code, diffs, tables and
# the live region still use the full width. ECHO_TUI_MEASURE=0 turns it off.
DEFAULT_MEASURE = 100


def tier(width: int) -> str:
    if width < MICRO:
        return "micro"
    if width < NARROW:
        return "narrow"
    if width < WIDE:
        return "regular"
    return "wide"


def budget(rows: int) -> int:
    """Leave one row for the conversation when the terminal has room."""
    return max(1, rows - 1)


def measure(width: int, env: Mapping[str, str]) -> int:
    raw = str(env.get("ECHO_TUI_MEASURE", "")).strip()
    try:
        cap = int(raw) if raw else DEFAULT_MEASURE
    except ValueError:
        cap = DEFAULT_MEASURE
    return width if cap <= 0 else min(width, max(40, cap))


@dataclass(frozen=True)
class Want:
    name: str
    natural: int        # rows it would like
    minimum: int        # fewest useful rows (it is hidden rather than given less); 0 = only from leftovers
    priority: int       # 0 is kept first; 0 also takes whatever is left when its minimum doesn't fit


def allocate(wants: list[Want], rows: int) -> dict[str, int]:
    """Rows per section: minimums by priority, then growth toward natural heights by priority.

    A section with minimum 0 (a blank spacer, the mode line) gets rows only from what is left after
    every other section has grown to its natural height.
    """
    alloc = {w.name: 0 for w in wants}
    left = max(0, rows)
    order = sorted(wants, key=lambda w: w.priority)   # stable: ties keep layout order
    for w in order:
        need = min(w.minimum, w.natural)
        if need <= 0:
            continue
        if need <= left:
            alloc[w.name] = need
            left -= need
        elif w.priority == 0 and left > 0:
            alloc[w.name] = left
            left = 0
    for optional in (False, True):
        for w in order:
            if left <= 0:
                break
            if (w.minimum <= 0) != optional or (not optional and alloc[w.name] <= 0):
                continue
            extra = min(w.natural - alloc[w.name], left)
            if extra > 0:
                alloc[w.name] += extra
                left -= extra
    return alloc
