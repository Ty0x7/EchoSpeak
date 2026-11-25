"""The responsive live region: width tiers, the height budget, and resize reflow (cli-design.md §6).

Pure functions only; ``app.py`` asks them what fits.

- **Width tiers** replace scattered thresholds. Every renderer degrades step by step inside a tier
  instead of jumping at one magic width.
- **Height budget.** The live region gets at most ``rows - 1`` rows (it must never start at row 0:
  tmux copies the screen into history when it is erased from the top-left corner). Sections ask for
  a natural and a minimum height; ``allocate`` gives rows by priority, so the composer and a dialog's
  options are always on screen and the stream tail gives way first.
- **Resize reflow.** Terminals (kitty, ghostty, foot, alacritty, wezterm, VTE, tmux) re-wrap lines
  when the window narrows. The old live region then takes more rows than prompt_toolkit drew, and
  its erase-by-row-count leaves copies of the rules behind. ``reflowed_rows_above`` counts the rows
  the old region really occupies above the cursor at the new width.
"""
from __future__ import annotations

import math
import re
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass

from prompt_toolkit.utils import get_cwidth

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
    """Rows the live region may use: everything but one, so it never starts at row 0."""
    return max(1, rows - 1)


class LiveRegionFit:
    """Where the inline live region's top row sits, so it grows downward and stays at the bottom once there.

    ``below`` is the number of rows from the region's top row to the terminal's bottom. A cursor
    report sets it (``reported``). A render taller than that scrolled the terminal, and the region now
    ends on the bottom row, so ``rendered`` raises it to the rendered height. While the region is
    shorter than ``below`` it has free rows under it and takes only what its content needs
    (``floor`` = 0). Once it reaches the bottom it keeps every row down to it (``floor`` = ``below``),
    so whatever shrinks inside it leaves the composer on the bottom row instead of leaving blank rows
    beneath it. ``stale`` forgets the position when the region is about to move (output printed above
    it, a resize) until the next report.

    ``borrowed`` counts committed rows directly above the region's natural top that it is drawn over
    for the moment (``borrow``); ``give_back`` returns them once they have been written back.
    """

    def __init__(self) -> None:
        self.below: int | None = None
        self.borrowed = 0

    @property
    def known(self) -> bool:
        return self.below is not None

    def reported(self, rows_below_cursor: int) -> None:
        self.below = max(1, rows_below_cursor)

    def stale(self) -> None:
        self.below = None

    def rendered(self, height: int) -> None:
        if self.below is not None and height > self.below:
            self.below = height

    def floor(self, height: int, budget_rows: int) -> int:
        """Rows the region must keep: all of them down to the bottom once it reaches it, else none."""
        if self.below is None:
            return 0
        bottom = min(self.below, budget_rows)
        return bottom if height >= bottom else 0

    def borrow(self, rows: int) -> None:
        self.borrowed += rows
        if self.below is not None:
            self.below += rows

    def give_back(self, rows: int) -> None:
        rows = min(rows, self.borrowed)
        self.borrowed -= rows
        if self.below is not None:
            self.below = max(1, self.below - rows)


_ANSI = re.compile(r"\x1b\[[0-9;?:<=>]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][0-9A-Za-z]|\x1b[=>78]")


class CommittedRows:
    """The last rows the TUI printed above the live region, exactly as written, one string per screen row.

    Fed with every chunk the output pump writes (``feed``). A chunk splits into screen rows at each
    newline as long as every line fits the terminal's width and carries no cursor movement; anything
    else (a line that wraps, a screen clear, a cursor move) makes the rows before it unknowable, so the
    record starts over from there. ``last(n)`` returns the last ``n`` complete rows, or ``None`` when
    fewer are known; ``restore`` turns rows into the bytes that draw them back from the cursor's row.
    """

    def __init__(self, keep: int = 400) -> None:
        self.rows: deque[str] = deque(maxlen=keep)
        self.partial = ""

    def reset(self) -> None:
        self.rows.clear()
        self.partial = ""

    @staticmethod
    def _plain_row(line: str, columns: int) -> bool:
        for match in _ANSI.finditer(line):
            seq = match.group(0)
            if seq.startswith("\x1b[") and seq[-1] != "m":
                return False          # cursor movement, erase, scroll: not a plain row
        visible = _ANSI.sub("", line)
        if "\r" in visible or "\t" in visible or "\b" in visible:
            return False
        return get_cwidth(visible) <= columns

    def feed(self, data: str, columns: int) -> None:
        if not data:
            return
        parts = (self.partial + data).split("\n")
        self.partial = parts.pop()
        for line in parts:
            if self._plain_row(line, columns):
                self.rows.append(line)
            else:
                self.rows.clear()
        if self.partial and not self._plain_row(self.partial, columns):
            self.rows.clear()
            self.partial = ""

    def last(self, n: int) -> list[str] | None:
        if n <= 0:
            return []
        if self.partial or n > len(self.rows):
            return None
        return list(self.rows)[-n:]

    @staticmethod
    def restore(rows: list[str]) -> str:
        """Draw ``rows`` from the cursor's row down, clearing each first; the cursor ends below them."""
        return "".join("\r\x1b[0m\x1b[2K" + row + "\x1b[0m\r\n" for row in rows)


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


# ── resize ───────────────────────────────────────────────────────────
def rows_after_reflow(length: int, width: int) -> int:
    """Terminal rows a line of ``length`` cells takes once re-wrapped at ``width``."""
    if width <= 0:
        return 1
    return max(1, math.ceil(length / width))


def reflowed_rows_above(lengths: list[int], cursor_row: int, cursor_col: int, new_width: int) -> int:
    """Rows between the top of the old live region and the cursor after a narrowing reflow.

    ``lengths`` are the drawn cell counts of each region row (trailing blanks are never written);
    the cursor sits at (cursor_row, cursor_col) in region coordinates.
    """
    above = sum(rows_after_reflow(n, new_width) for n in lengths[:max(0, cursor_row)])
    if new_width > 0 and cursor_col > 0:
        above += cursor_col // new_width   # the cursor stays on its cell, which moved down with the wrap
    return above


def terminal_reflows(env: Mapping[str, str]) -> bool:
    """Does this terminal re-wrap lines on resize? Almost all do; xterm and the Linux console do not.

    ECHO_TUI_REFLOW=0/1 overrides the guess.
    """
    forced = str(env.get("ECHO_TUI_REFLOW", "")).strip().lower()
    if forced in {"0", "false", "no", "off"}:
        return False
    if forced in {"1", "true", "yes", "on"}:
        return True
    term = str(env.get("TERM", "")).lower()
    if term == "linux" or term.startswith("vt"):
        return False
    if env.get("TMUX") or term.startswith(("tmux", "screen")):
        return True          # tmux reflows its panes whatever the outer terminal does
    if term.startswith("xterm") and term not in {"xterm-kitty", "xterm-ghostty"}:
        # Plain "xterm*" is also what most modern terminals claim; they identify themselves elsewhere.
        markers = ("VTE_VERSION", "KONSOLE_VERSION", "WT_SESSION", "TERM_PROGRAM", "KITTY_WINDOW_ID",
                   "GHOSTTY_RESOURCES_DIR", "ALACRITTY_WINDOW_ID", "ALACRITTY_SOCKET", "WEZTERM_PANE")
        return any(env.get(m) for m in markers)
    return True
