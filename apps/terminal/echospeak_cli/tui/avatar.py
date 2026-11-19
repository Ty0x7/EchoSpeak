"""Echo's face in the terminal, and its entrance: pure functions of elapsed time, no I/O.

Echo is a square with two tall pill eyes (``apps/web/src/components/EchoFace.tsx``). Here it is a solid
14 × 6 bitmap drawn with quadrant block characters (one terminal cell holds 2 × 2 "pixels"). A cell is
about twice as tall as it is wide, so 14 half-cells by 6 half-rows is about 63 × 60 px at JetBrains Mono
13.5 pt: square, with straight sides and no corner notches (the earlier face's notched top and bottom rows
made its middle row stick out at the sides).

The face fills a fixed canvas of 7 cells × 3 rows, so every frame is exactly that size and the text
beside it never moves. The entrance slides Echo in from the left edge to its resting place in half-cell
steps (about 4.5 px), easing out, then Echo blinks once. ``frame(t)`` after the entrance, and always under
reduced motion, is the resting face.
"""
from __future__ import annotations

CANVAS_W = 14          # half-cells (7 cells)
CANVAS_H = 6           # half-rows (3 rows)
EYES = ((3, 4), (9, 10))       # eye columns: three half-cells from each side, four apart
EYE_ROWS = (2, 3)              # the middle row: 2 half-rows (one cell) tall
SLIDE_MS = 520                 # in from the left
BLINK_AT, BLINK_MS = 820, 90   # one blink once Echo has arrived
ENTRANCE_MS = 1000
HOLD_MS = 0                    # nothing waits on the entrance: the banner is printed when output needs it

# Quadrant characters by (top-left, top-right, bottom-left, bottom-right).
_QUAD = {
    (0, 0, 0, 0): " ", (1, 1, 1, 1): "█", (1, 1, 0, 0): "▀", (0, 0, 1, 1): "▄", (1, 0, 1, 0): "▌", (0, 1, 0, 1): "▐",
    (0, 0, 1, 0): "▖", (0, 0, 0, 1): "▗", (1, 0, 0, 0): "▘", (0, 1, 0, 0): "▝", (1, 0, 1, 1): "▙", (1, 1, 1, 0): "▛",
    (1, 1, 0, 1): "▜", (0, 1, 1, 1): "▟", (1, 0, 0, 1): "▚", (0, 1, 1, 0): "▞",
}


def _face(blink: bool = False) -> list[str]:
    """The square face: solid, with two eyes (a blink keeps only each eye's lower half, like a lid closing)."""
    rows = [["#"] * CANVAS_W for _ in range(CANVAS_H)]
    for y in (EYE_ROWS[1:] if blink else EYE_ROWS):
        for c0, c1 in EYES:
            rows[y][c0] = rows[y][c1] = "."
    return ["".join(r) for r in rows]


def _shifted(rows: list[str], dx: int) -> list[str]:
    """The face moved ``dx`` half-cells (negative: still partly beyond the left edge), clipped to the canvas."""
    out = []
    for row in rows:
        cells = ["."] * CANVAS_W
        for x, ch in enumerate(row):
            if 0 <= x + dx < CANVAS_W and ch == "#":
                cells[x + dx] = "#"
        out.append("".join(cells))
    return out


def _ease_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


REST = _face()


def bitmap(t_ms: float, reduced_motion: bool = False) -> list[str]:
    if reduced_motion or t_ms >= ENTRANCE_MS or t_ms < 0:
        return REST
    if t_ms < SLIDE_MS:
        # Whole half-cell steps: every frame is a real position, so nothing shimmers between two.
        dx = -round(CANVAS_W * (1 - _ease_out(t_ms / SLIDE_MS)))
        return _shifted(REST, dx) if dx else REST
    if BLINK_AT <= t_ms < BLINK_AT + BLINK_MS:
        return _face(blink=True)
    return REST


def cells(bm: list[str]) -> list[str]:
    """A bitmap (rows of '#'/'.', even width and height) as rows of quadrant characters."""
    out = []
    for y in range(0, len(bm), 2):
        top, bottom = bm[y], bm[y + 1] if y + 1 < len(bm) else "." * len(bm[y])
        row = []
        for x in range(0, len(top), 2):
            key = tuple(int(s[i] == "#") for s, i in ((top, x), (top, x + 1), (bottom, x), (bottom, x + 1)))
            row.append(_QUAD[key])   # type: ignore[index]
        out.append("".join(row))
    return out


def frame(t_ms: float, reduced_motion: bool = False) -> tuple[str, str, str]:
    """The three rows of the face at ``t_ms`` into the entrance (7 cells each)."""
    rows = cells(bitmap(t_ms, reduced_motion))
    return rows[0], rows[1], rows[2]


def done(t_ms: float, reduced_motion: bool = False) -> bool:
    return reduced_motion or t_ms >= ENTRANCE_MS


FACE_REST = frame(ENTRANCE_MS)
