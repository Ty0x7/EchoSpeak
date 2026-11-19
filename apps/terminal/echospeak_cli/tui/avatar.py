"""Echo's face in the terminal, and its entrance: pure functions of elapsed time, no I/O.

Echo is a rounded square with two tall pill eyes (``apps/web/src/components/EchoFace.tsx``). Here it is
a 14 × 6 bitmap drawn with quadrant block characters, so one terminal cell holds 2 × 2 "pixels".
A cell is about twice as tall as it is wide, so 14 half-cells by 6 half-rows comes out square
(about 63 × 60 px at JetBrains Mono 13.5 pt), where the old 7 × 2 face was wide and flat.

The face sits in a fixed canvas of 8 cells × 3 rows (one spare half-cell each side, room for the
landing squash). Every frame is exactly that size, so the animation never moves the text beside
it and never changes the height of the banner. The entrance takes ENTRANCE_MS: Echo rises into
the canvas, lands with a squash, settles, glances left and right, and blinks. ``frame(t)`` after
the entrance (and always under reduced motion) is the resting face.
"""
from __future__ import annotations

CANVAS_W = 16          # half-cells (8 cells)
CANVAS_H = 6           # half-rows (3 rows)
FACE = (
    ".############.",
    "##############",
    "###..####..###",
    "###..####..###",
    "##############",
    ".############.",
)
EYES = ((3, 4), (9, 10))       # eye columns in FACE coordinates
ENTRANCE_MS = 1350
HOLD_MS = 250            # Echo rests this long before the banner is committed to scrollback

# Quadrant characters by (top-left, top-right, bottom-left, bottom-right).
_QUAD = {
    (0, 0, 0, 0): " ", (1, 1, 1, 1): "█", (1, 1, 0, 0): "▀", (0, 0, 1, 1): "▄", (1, 0, 1, 0): "▌", (0, 1, 0, 1): "▐",
    (0, 0, 1, 0): "▖", (0, 0, 0, 1): "▗", (1, 0, 0, 0): "▘", (0, 1, 0, 0): "▝", (1, 0, 1, 1): "▙", (1, 1, 1, 0): "▛",
    (1, 1, 0, 1): "▜", (0, 1, 1, 1): "▟", (1, 0, 0, 1): "▚", (0, 1, 1, 0): "▞",
}


def _face(eye_dx: int = 0, eye_dy: int = 0, eye_h: int = 2) -> list[str]:
    """The face bitmap with its eyes moved (a glance) or shortened (a blink)."""
    rows = [list(r) for r in FACE]
    for r in rows:
        for c0, c1 in EYES:
            r[c0] = r[c1] = "#"
    top = 2 + eye_dy + (2 - eye_h)       # a blink keeps the eye's bottom edge, like a lid closing
    for y in range(top, top + eye_h):
        if 0 <= y < len(rows):
            for c0, c1 in EYES:
                for c in (c0 + eye_dx, c1 + eye_dx):
                    if 0 <= c < len(rows[y]):
                        rows[y][c] = "."
    return ["".join(r) for r in rows]


def _fit(rows: list[str], dy: int, dx: int = 1) -> list[str]:
    """Place a bitmap in the canvas, ``dy`` half-rows down and ``dx`` half-cells right; the rest is clipped."""
    canvas = [["."] * CANVAS_W for _ in range(CANVAS_H)]
    for y, row in enumerate(rows):
        cy = y + dy
        if not 0 <= cy < CANVAS_H:
            continue
        off = dx if len(row) < CANVAS_W else 0
        for x, ch in enumerate(row):
            cx = x + off
            if 0 <= cx < CANVAS_W and ch == "#":
                canvas[cy][cx] = "#"
    return ["".join(r) for r in canvas]


def _squashed() -> list[str]:
    """The landing frame: the face one half-row shorter and a half-cell wider each side."""
    # Eyes squeeze to one half-row, in the same columns as at rest (face column + the canvas's 1 offset).
    eyes = "".join("." if (x - 1) in (3, 4, 9, 10) else "#" for x in range(CANVAS_W))
    rows = ["." + "#" * 14 + ".", "#" * 16, eyes, "#" * 16, "." + "#" * 14 + "."]
    return ["." * CANVAS_W] + rows


# The entrance as (until_ms, bitmap). Time-based: a slow redraw skips frames, it never slows Echo down.
def _timeline() -> list[tuple[int, list[str]]]:
    rest = _fit(_face(), 0)
    return [
        (70, _fit(_face(), 5)),            # the top edge appears
        (140, _fit(_face(), 4)),           # rising: the forehead (no half-cut eyes)
        (240, _fit(_face(), 1)),           # most of the face, eyes whole (a half-cut eye reads as legs)
        (340, _squashed()),                # lands: squash
        (430, _fit(_face(eye_dy=-1), 0)),  # stretches back up, eyes perk
        (620, rest),
        (800, _fit(_face(eye_dx=-1), 0)),  # glances left
        (980, _fit(_face(eye_dx=1), 0)),   # and right
        (1150, rest),
        (1230, _fit(_face(eye_h=1), 0)),   # blink
        (ENTRANCE_MS, rest),
    ]


_TIMELINE = _timeline()
REST = _TIMELINE[-1][1]


def bitmap(t_ms: float, reduced_motion: bool = False) -> list[str]:
    if reduced_motion or t_ms >= ENTRANCE_MS or t_ms < 0:
        return REST
    for until, bm in _TIMELINE:
        if t_ms < until:
            return bm
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
    """The three rows of the face at ``t_ms`` into the entrance (8 cells each)."""
    rows = cells(bitmap(t_ms, reduced_motion))
    return rows[0], rows[1], rows[2]


def done(t_ms: float, reduced_motion: bool = False) -> bool:
    return reduced_motion or t_ms >= ENTRANCE_MS


FACE_REST = frame(ENTRANCE_MS)
