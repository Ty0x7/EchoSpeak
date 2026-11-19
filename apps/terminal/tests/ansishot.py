"""Terminal screenshots: a tmux ``capture-pane -e`` screen -> HTML -> PNG (headless Chromium).

The screen is the real terminal state (tmux is the terminal emulator); this only paints it, the way
ghostty would: the owner's font, the theme's 16-colour palette, faint text at half opacity, block
elements drawn as exact cell quadrants (ghostty draws them as sprites, not font glyphs), wide
characters in two cells and the cursor where tmux says it is.

    python ansishot.py screen.ansi out.png [--cursor X,Y] [--palette owner|standard]
"""
from __future__ import annotations

import argparse
import html
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from wcwidth import wcwidth

FONT = "JetBrainsMono Nerd Font"
FONT_PX = 15
CELL_W = 9                       # JetBrains Mono advance (600/1000 em) at 15 px
LINE = 20 / 15                   # a 20 px cell: integer metrics, no seams between block cells
PAD_X, PAD_Y = 16, 14            # the owner's ghostty window padding

STANDARD = {"bg": "#1e1e2e", "fg": "#cdd6f4", "cursor": "#f5e0dc", "palette": [
    "#45475a", "#f38ba8", "#a6e3a1", "#f9e2af", "#89b4fa", "#f5c2e7", "#94e2d5", "#bac2de",
    "#585b70", "#f38ba8", "#a6e3a1", "#f9e2af", "#89b4fa", "#f5c2e7", "#94e2d5", "#a6adc8"]}


def owner_palette() -> dict:
    path = Path.home() / ".local/state/omarchy/current/theme/ghostty.conf"
    try:
        text = path.read_text()
    except OSError:
        return STANDARD
    pal = list(STANDARD["palette"])
    for m in re.finditer(r"^palette\s*=\s*(\d+)=(#[0-9a-fA-F]{6})", text, re.M):
        if int(m.group(1)) < 16:
            pal[int(m.group(1))] = m.group(2)

    def get(key, default):
        m = re.search(rf"^{key}\s*=\s*(#[0-9a-fA-F]{{6}})", text, re.M)
        return m.group(1) if m else default

    return {"bg": get("background", STANDARD["bg"]), "fg": get("foreground", STANDARD["fg"]),
            "cursor": get("cursor-color", STANDARD["cursor"]), "palette": pal}


def xterm256(n: int, pal: list[str]) -> str:
    if n < 16:
        return pal[n]
    if n < 232:
        n -= 16
        steps = [0, 95, 135, 175, 215, 255]
        return "#%02x%02x%02x" % (steps[n // 36], steps[(n // 6) % 6], steps[n % 6])
    v = 8 + (n - 232) * 10
    return "#%02x%02x%02x" % (v, v, v)


def mix(a: str, b: str, t: float) -> str:
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(ca, cb))


# Block elements as quadrants (TL, TR, BL, BR) or vertical/horizontal eighths.
QUAD = {"▀": "1100", "▄": "0011", "█": "1111", "▌": "1010", "▐": "0101", "▖": "0010", "▗": "0001", "▘": "1000",
        "▝": "0100", "▙": "1011", "▛": "1110", "▜": "1101", "▟": "0111", "▚": "1001", "▞": "0110"}
LOWER = {"▁": 1, "▂": 2, "▃": 3, "▅": 5, "▆": 6, "▇": 7}
LEFT = {"▏": 1, "▎": 2, "▍": 3, "▋": 5, "▊": 6, "▉": 7}
UPPER = {"▔": 1}

SGR = re.compile(r"\x1b\[([0-9;:]*)m")
OTHER_ESC = re.compile(r"\x1b(?:\][^\x07\x1b]*(?:\x07|\x1b\\)|\[[0-9;?]*[A-Za-z]|[()][A-Za-z0-9])")


class Style:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.fg = self.bg = None
        self.bold = self.dim = self.italic = self.under = self.reverse = self.strike = False

    def apply(self, params: str) -> None:
        parts = [p for p in re.split(r"[;]", params)] if params else ["0"]
        i = 0
        while i < len(parts):
            p = parts[i]
            if ":" in p:          # 38:2::r:g:b or 38:5:n
                sub = [x for x in p.split(":")]
                if sub[0] in {"38", "48"}:
                    nums = [x for x in sub[1:] if x != ""]
                    col = None
                    if nums and nums[0] == "5" and len(nums) > 1:
                        col = ("idx", int(nums[1]))
                    elif nums and nums[0] == "2" and len(nums) >= 4:
                        col = ("rgb", "#%02x%02x%02x" % tuple(int(x) for x in nums[-3:]))
                    if sub[0] == "38":
                        self.fg = col
                    else:
                        self.bg = col
                elif sub[0] == "4":
                    self.under = sub[1:] != ["0"]
                i += 1
                continue
            n = int(p or 0)
            if n == 0:
                self.reset()
            elif n == 1:
                self.bold = True
            elif n == 2:
                self.dim = True
            elif n == 3:
                self.italic = True
            elif n == 4:
                self.under = True
            elif n == 7:
                self.reverse = True
            elif n == 9:
                self.strike = True
            elif n == 22:
                self.bold = self.dim = False
            elif n == 23:
                self.italic = False
            elif n == 24:
                self.under = False
            elif n == 27:
                self.reverse = False
            elif n == 29:
                self.strike = False
            elif 30 <= n <= 37:
                self.fg = ("idx", n - 30)
            elif n == 39:
                self.fg = None
            elif 40 <= n <= 47:
                self.bg = ("idx", n - 40)
            elif n == 49:
                self.bg = None
            elif 90 <= n <= 97:
                self.fg = ("idx", n - 90 + 8)
            elif 100 <= n <= 107:
                self.bg = ("idx", n - 100 + 8)
            elif n in (38, 48) and i + 1 < len(parts):
                mode = parts[i + 1]
                col = None
                if mode == "5" and i + 2 < len(parts):
                    col = ("idx", int(parts[i + 2]))
                    i += 2
                elif mode == "2" and i + 4 < len(parts):
                    col = ("rgb", "#%02x%02x%02x" % (int(parts[i + 2]), int(parts[i + 3]), int(parts[i + 4])))
                    i += 4
                if n == 38:
                    self.fg = col
                else:
                    self.bg = col
            i += 1

    def colors(self, theme: dict) -> tuple[str, str]:
        def res(c, default):
            if c is None:
                return default
            if c[0] == "idx":
                idx = c[1]
                if self.bold and idx < 8 and False:   # ghostty: bold-is-bright off
                    idx += 8
                return xterm256(idx, theme["palette"])
            return c[1]

        fg, bg = res(self.fg, theme["fg"]), res(self.bg, theme["bg"])
        if self.reverse:
            fg, bg = bg, fg
        if self.dim:
            fg = mix(bg, fg, 0.5)
        return fg, bg


def parse(text: str, theme: dict) -> list[list[tuple[str, str, str, dict]]]:
    """Rows of cells: (char, fg, bg, flags). A wide char is one cell followed by a '' filler."""
    rows = []
    st = Style()
    for raw in text.rstrip("\n").split("\n"):
        row = []
        pos = 0
        raw = OTHER_ESC.sub(lambda m: m.group(0) if m.group(0).endswith("m") and m.group(0).startswith("\x1b[") else "", raw)
        for m in list(SGR.finditer(raw)) + [None]:
            end = m.start() if m else len(raw)
            for ch in raw[pos:end]:
                if ch in "\r":
                    continue
                fg, bg = st.colors(theme)
                flags = {"b": st.bold, "i": st.italic, "u": st.under, "s": st.strike}
                w = wcwidth(ch)
                if w < 0:
                    continue
                if w == 0 and row:
                    c = row[-1]
                    row[-1] = (c[0] + ch, c[1], c[2], c[3])
                    continue
                row.append((ch, fg, bg, flags))
                if w == 2:
                    row.append(("", fg, bg, flags))
            if m:
                st.apply(m.group(1))
                pos = m.end()
        rows.append(row)
    return rows


def block_css(ch: str, fg: str, bg: str) -> str:
    layers = []
    if ch in QUAD:
        q = QUAD[ch]
        for k, (x, y) in enumerate(((0, 0), (100, 0), (0, 100), (100, 100))):
            if q[k] == "1":
                layers.append(f"linear-gradient({fg},{fg}) {x}% {y}% / 50.5% 50.5% no-repeat")
    elif ch in LOWER:
        layers.append(f"linear-gradient({fg},{fg}) 0 100% / 100% {LOWER[ch] * 12.5}% no-repeat")
    elif ch in LEFT:
        layers.append(f"linear-gradient({fg},{fg}) 0 0 / {LEFT[ch] * 12.5}% 100% no-repeat")
    elif ch in UPPER:
        layers.append(f"linear-gradient({fg},{fg}) 0 0 / 100% {UPPER[ch] * 12.5}% no-repeat")
    layers.append(bg)
    return "background:" + ",".join(layers)


def to_html(text: str, cols: int, rows_n: int, theme: dict, cursor: tuple[int, int] | None, title: str = "") -> str:
    rows = parse(text, theme)
    rows += [[] for _ in range(max(0, rows_n - len(rows)))]
    out = []
    for y, row in enumerate(rows[:rows_n]):
        cells = row + [(" ", theme["fg"], theme["bg"], {})] * max(0, cols - len(row))
        line = []
        for x, (ch, fg, bg, fl) in enumerate(cells[:cols]):
            if ch == "":
                continue
            wide = x + 1 < len(cells) and cells[x + 1][0] == "" and ch != ""
            is_cursor = cursor is not None and cursor == (x, y)
            if is_cursor:
                fg, bg = theme["bg"], theme["cursor"]
            css = []
            special = ch in QUAD or ch in LOWER or ch in LEFT or ch in UPPER
            if special:
                css.append(block_css(ch, fg, bg))
                glyph = "&nbsp;"
            else:
                css.append(f"color:{fg}")
                if bg != theme["bg"]:
                    css.append(f"background:{bg}")
                glyph = html.escape(ch) if ch != " " else "&nbsp;"
            if fl.get("b"):
                css.append("font-weight:700")
            if fl.get("i"):
                css.append("font-style:italic")
            deco = [d for d, on in (("underline", fl.get("u")), ("line-through", fl.get("s"))) if on]
            if deco:
                css.append("text-decoration:" + " ".join(deco))
            cls = "c w" if wide else "c"
            line.append(f'<span class="{cls}" style="{";".join(css)}">{glyph}</span>')
        out.append('<div class="r">' + "".join(line) + "</div>")
    w = round(cols * CELL_W + 2 * PAD_X)
    h = round(rows_n * FONT_PX * LINE + 2 * PAD_Y)
    cap = f'<div class="cap">{html.escape(title)}</div>' if title else ""
    return f"""<!doctype html><meta charset="utf-8"><style>
html,body{{margin:0;background:{theme['bg']};}}
.t{{width:{w}px;height:{h}px;box-sizing:border-box;padding:{PAD_Y}px {PAD_X}px;font-family:'{FONT}',monospace;
font-size:{FONT_PX}px;line-height:{FONT_PX * LINE}px;color:{theme['fg']};font-variant-ligatures:none;
font-feature-settings:"liga" 0,"calt" 0;}}
.r{{height:{FONT_PX * LINE}px;white-space:pre;display:flex;}}
.c{{display:inline-block;width:{CELL_W}px;height:{FONT_PX * LINE}px;overflow:visible;text-align:left;}}
.w{{width:{2 * CELL_W}px;}}
.cap{{position:fixed;right:8px;bottom:2px;font:10px sans-serif;color:#888;}}
</style><div class="t">{''.join(out)}</div>{cap}""", (w, h)


def shoot(html_text: str, size: tuple[int, int], png: Path, scale: float = 2.0) -> None:
    chromium = shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome")
    if not chromium:
        raise RuntimeError("no chromium for screenshots")
    with tempfile.TemporaryDirectory(prefix="ansishot-") as tmp:
        page = Path(tmp) / "s.html"
        page.write_text(html_text, encoding="utf-8")
        subprocess.run([chromium, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                        f"--user-data-dir={tmp}/profile", f"--force-device-scale-factor={scale}",
                        f"--window-size={size[0]},{size[1]}", f"--screenshot={png}", page.as_uri()],
                       check=True, capture_output=True, timeout=60)


def render_png(ansi: str, png: Path, cols: int, rows: int, cursor: tuple[int, int] | None = None,
               palette: str = "owner", title: str = "", scale: float = 0, keep_html: bool = True) -> Path:
    theme = owner_palette() if palette == "owner" else STANDARD
    page, size = to_html(ansi, cols, rows, theme, cursor, title)
    png.parent.mkdir(parents=True, exist_ok=True)
    if keep_html:
        (png.with_suffix(".html")).write_text(page, encoding="utf-8")
    # 2x for reading closely; tall scrollback captures at 1x (Chromium's memory), and 1x for repo media.
    shoot(page, size, png, scale=scale or (1.0 if rows > 60 else 2.0))
    return png


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ansi")
    ap.add_argument("png")
    ap.add_argument("--size", default="")
    ap.add_argument("--cursor", default="")
    ap.add_argument("--palette", default="owner")
    a = ap.parse_args()
    text = Path(a.ansi).read_text(encoding="utf-8")
    if a.size:
        cols, rows = (int(x) for x in a.size.split("x"))
    else:
        lines = text.rstrip("\n").split("\n")
        cols, rows = 100, len(lines)
    cur = tuple(int(x) for x in a.cursor.split(",")) if a.cursor else None
    render_png(text, Path(a.png), cols, rows, cur, a.palette)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
