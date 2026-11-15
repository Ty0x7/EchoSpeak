"""File and diff previews (docs/research/edit-preview.md §3): one renderer for every view.

The backend attaches a ``file_change`` record to ``tool_end.meta`` (applied) and to
``approval_request`` (proposed); see apps/backend/agent/lean/file_changes.py. Here it becomes rows:

    ✓ Edited src/stats.py · +4 −2                                         0.1s
        41   def iso_week(d):
        43  -    if w == 53: return (y, 52)
        43  +    if w == 53 and not _long_year(y):
            ⋮
      … +6 lines · ctrl+o

Additions and deletions never rely on hue (the owner's lumon theme maps red and green to blues): the
sign column and weight carry the meaning (``+`` bold, ``-`` bold with dim code, context dim), and an
optional truecolor tint differs in luminance only. Everything from the server is sanitized and goes
into ``rich.text.Text`` objects, never markup.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any, Optional

from rich.console import Console
from rich.style import Style
from rich.text import Text

from ..render import one_line, sanitize
from .motion import truncate, width_of
from .theme import Caps, diff_tints

INLINE_ROWS = 16        # scrollback rows per file before "… +N lines · ctrl+o"
CREATE_ROWS = 8
FULL_ROWS = 400
WRAP_ROWS = 3           # a long source line wraps to at most this many rows
SPLIT_MIN = 150         # side-by-side needs this many columns
NO_NUMBERS_BELOW = 60
HIGHLIGHT_MAX_ROWS = 2000


# ── the record ───────────────────────────────────────────────────────
@dataclass
class Hunk:
    old_start: int
    new_start: int
    lines: list[tuple[str, str]]     # (" " | "-" | "+", text)


@dataclass
class Change:
    op: str = "edit"                  # create | edit | write | delete | move
    path: str = ""
    display: str = ""
    move_from: str = ""
    language: str = ""
    hunks: list[Hunk] = field(default_factory=list)
    added: int = 0
    removed: int = 0
    total_lines: int = 0
    truncated: bool = False
    too_large: bool = False
    binary: bool = False
    withheld: str = ""
    status: str = "applied"           # proposed | applied | denied | failed
    change_id: str = ""
    tool_call_id: str = ""

    @property
    def rows(self) -> int:
        return sum(len(h.lines) for h in self.hunks)

    @property
    def max_line(self) -> int:
        top = 1
        for h in self.hunks:
            old, new = h.old_start, h.new_start
            for tag, _ in h.lines:
                if tag in {" ", "-"}:
                    old += 1
                if tag in {" ", "+"}:
                    new += 1
            top = max(top, old, new)
        return top


def _int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def parse(raw: Any) -> Optional[Change]:
    """A Change from an untrusted event field. None when it isn't one."""
    if not isinstance(raw, dict) or not (raw.get("path") or raw.get("display_path")):
        return None
    hunks = []
    for h in raw.get("hunks") or []:
        if not isinstance(h, dict):
            continue
        lines = []
        for row in h.get("lines") or []:
            if isinstance(row, (list, tuple)) and len(row) == 2 and row[0] in {" ", "-", "+"}:
                lines.append((row[0], sanitize(row[1]).replace("\n", " ")))
            elif isinstance(row, str) and row[:1] in {" ", "-", "+"}:   # " ctx" / "-old" / "+new"
                lines.append((row[0], sanitize(row[1:]).replace("\n", " ")))
        if lines:
            hunks.append(Hunk(_int(h.get("old_start")), _int(h.get("new_start")), lines))
    op = str(raw.get("op") or "edit")
    return Change(
        op=op if op in {"create", "edit", "write", "overwrite", "delete", "move"} else "edit",
        path=one_line(raw.get("path"), 400), display=one_line(raw.get("display_path") or raw.get("path"), 300),
        move_from=one_line(raw.get("move_from"), 300), language=one_line(raw.get("language"), 30).lower(),
        hunks=hunks, added=_int(raw.get("added")), removed=_int(raw.get("removed")),
        total_lines=_int(raw.get("total_lines")), truncated=bool(raw.get("truncated")),
        too_large=bool(raw.get("too_large")), binary=bool(raw.get("binary")),
        withheld=one_line(raw.get("withheld_reason"), 200), status=one_line(raw.get("status") or "applied", 20),
        change_id=one_line(raw.get("change_id"), 60), tool_call_id=one_line(raw.get("tool_call_id"), 120),
    )


# ── wording ──────────────────────────────────────────────────────────
_DONE = {"create": "Created", "edit": "Edited", "write": "Wrote", "overwrite": "Overwrote", "delete": "Deleted",
         "move": "Moved"}
_ASK = {"create": "Create a file", "edit": "Edit a file", "write": "Overwrite a file", "overwrite": "Overwrite a file",
        "delete": "Delete a file", "move": "Move a file"}
_NOUN = {"create": "file", "edit": "edit", "write": "write", "overwrite": "write", "delete": "delete", "move": "move"}


def proposed_label(change: Change) -> str:
    return {"create": "Would create", "edit": "Would edit", "write": "Would overwrite", "overwrite": "Would overwrite",
            "delete": "Would delete", "move": "Would move"}.get(change.op, "Would change")


def verb(change: Change) -> str:
    return _DONE.get(change.op, "Changed")


def ask_title(change: Change) -> str:
    return _ASK.get(change.op, "Change a file")


def noun(change: Change) -> str:
    return _NOUN.get(change.op, "change")


def where(change: Change, caps: Caps) -> str:
    if change.op == "move" and change.move_from:
        return f"{change.move_from} {caps.glyphs.move} {change.display}"
    return change.display


def counts(change: Change, caps: Caps) -> list[tuple[str, str]]:
    """[("+4", style), (" −2", style)]: +N bold, −M dim. Empty for moves and withheld changes."""
    minus = caps.glyphs.minus
    out = []
    if change.op == "move" and not change.added and not change.removed:
        return out
    if change.added or change.op == "create":
        out.append((f"+{change.added}", "bold"))
    if change.removed or change.op == "delete":
        out.append(((" " if out else "") + f"{minus}{change.removed}", "dim"))
    return out


def summary_text(change: Change, caps: Caps) -> str:
    """Plain one-line: 'src/stats.py +4 −2'."""
    tail = "".join(t for t, _s in counts(change, caps))
    return where(change, caps) + (f" {tail}" if tail else "")


def withheld_note(change: Change) -> str:
    if change.withheld:
        return change.withheld
    if change.binary:
        return "a binary file"
    if change.too_large:
        return "too large to show"
    return ""


# ── styles ───────────────────────────────────────────────────────────
@dataclass(frozen=True)
class _Styles:
    num: str
    sign_add: str
    sign_del: str
    code_add: str
    code_del: str
    code_ctx: str
    dim: str
    add_bg: str
    del_bg: str


def styles(caps: Caps) -> _Styles:
    add_bg, del_bg = diff_tints(caps)
    if not caps.color:
        return _Styles("dim", "bold", "bold", "", "dim", "dim", "dim", "", "")
    return _Styles("bright_black", "bold green", "bold red", "", "dim", "dim", "dim",
                   f"on {add_bg}" if add_bg else "", f"on {del_bg}" if del_bg else "")


# ── syntax ───────────────────────────────────────────────────────────
def _lexer(language: str):
    if not language or language == "text":
        return None
    try:
        from pygments.lexers import get_lexer_by_name

        return get_lexer_by_name(language, stripnl=False, ensurenl=False)
    except Exception:  # unknown language or no pygments: plain
        return None


def _highlight(lines: list[str], lexer) -> list[list[tuple[str, Style]]]:
    """Each line as (text, rich Style) segments; the side is lexed as one block, so strings and comments that
    span lines colour correctly. Any failure gives plain lines."""
    plain = [[(line, Style())] for line in lines]
    if lexer is None or not lines:
        return plain
    try:
        from rich.syntax import Syntax

        theme = Syntax.get_theme("ansi_dark")
        out: list[list[tuple[str, Style]]] = [[]]
        for token, value in lexer.get_tokens("\n".join(lines) + "\n"):
            style = theme.get_style_for_token(token)
            parts = value.split("\n")
            for i, part in enumerate(parts):
                if i:
                    out.append([])
                if part:
                    if out[-1] and out[-1][-1][1] == style:
                        out[-1][-1] = (out[-1][-1][0] + part, style)   # one SGR run per style
                    else:
                        out[-1].append((part, style))
        out = out[:len(lines)]
        if len(out) != len(lines) or any("".join(t for t, _ in segs) != line for segs, line in zip(out, lines)):
            return plain
        return out
    except Exception:
        return plain


# ── rows ─────────────────────────────────────────────────────────────
@dataclass
class Row:
    """One source line (or a hunk separator) with its numbers and syntax segments."""
    tag: str                          # " " | "-" | "+" | "sep"
    old: int = 0
    new: int = 0
    segs: list[tuple[str, Style]] = field(default_factory=list)


def rows_of(change: Change, caps: Caps, highlight: bool = True) -> list[Row]:
    lexer = _lexer(change.language) if (highlight and caps.color and change.rows <= HIGHLIGHT_MAX_ROWS) else None
    out: list[Row] = []
    for n, h in enumerate(change.hunks):
        if n:
            out.append(Row("sep"))
        old_side = [text for tag, text in h.lines if tag in {" ", "-"}]
        new_side = [text for tag, text in h.lines if tag in {" ", "+"}]
        old_hl, new_hl = _highlight(_tabs(old_side), lexer), _highlight(_tabs(new_side), lexer)
        oi = ni = 0
        old, new = h.old_start, h.new_start
        for tag, _text in h.lines:
            if tag == " ":
                out.append(Row(" ", old, new, new_hl[ni]))
                oi += 1
                ni += 1
                old += 1
                new += 1
            elif tag == "-":
                out.append(Row("-", old, 0, old_hl[oi]))
                oi += 1
                old += 1
            else:
                out.append(Row("+", 0, new, new_hl[ni]))
                ni += 1
                new += 1
    return out


def _tabs(lines: list[str]) -> list[str]:
    return [line.expandtabs(4) for line in lines]


def _wrap_segs(segs: list[tuple[str, Style]], width: int, max_rows: int = WRAP_ROWS) -> list[list[tuple[str, Style]]]:
    """Split segments into rows of at most ``width`` cells; the last allowed row ends with … when cut."""
    width = max(4, width)
    rows: list[list[tuple[str, Style]]] = [[]]
    used = 0
    for text, style in segs:
        buf = ""
        for ch in text:
            w = max(0, width_of(ch))
            if used + w > width:
                if buf:
                    rows[-1].append((buf, style))
                    buf = ""
                if len(rows) >= max_rows:
                    # cut: replace the last cell with an ellipsis
                    last = rows[-1]
                    if last:
                        t, s = last[-1]
                        last[-1] = (t[:-1] + "…" if t else "…", s)
                    return rows
                rows.append([])
                used = 0
            buf += ch
            used += w
        if buf:
            rows[-1].append((buf, style))
    return rows


def _row_style(tag: str, st: _Styles) -> tuple[str, str]:
    """(code style, background) for a row tag."""
    if tag == "+":
        return st.code_add, st.add_bg
    if tag == "-":
        return st.code_del, st.del_bg
    return st.code_ctx, ""


def unified(change: Change, width: int, caps: Caps, max_rows: Optional[int] = INLINE_ROWS, indent: int = 4,
            numbers: Optional[bool] = None, highlight: bool = True, hint: str = "ctrl+o") -> tuple[list[Text], int]:
    """Screen rows for a unified diff, and how many source rows were left out (shown as "… +N lines")."""
    st = styles(caps)
    g = caps.glyphs
    width = max(20, width)
    numbers = (width >= NO_NUMBERS_BELOW) if numbers is None else numbers
    gutter = len(str(change.max_line)) if numbers else 0
    lead = " " * indent
    code_w = max(8, width - indent - (gutter + 2 if numbers else 0) - 1)
    rows = rows_of(change, caps, highlight)
    out: list[Text] = []
    shown = 0
    hidden = 0
    for i, row in enumerate(rows):
        if max_rows is not None and shown >= max_rows:
            hidden = sum(1 for r in rows[i:] if r.tag != "sep")
            break
        if row.tag == "sep":
            out.append(Text(lead + " " * (gutter + 2 if numbers else 0) + g.vdots, style=st.dim))
            continue
        code_style, bg = _row_style(row.tag, st)
        number = row.new if row.tag in {" ", "+"} else row.old
        pieces = _wrap_segs(row.segs or [("", Style())], code_w)
        for k, segs in enumerate(pieces):
            line = Text(lead)
            if numbers:
                line.append((str(number).rjust(gutter) if k == 0 else " " * gutter) + "  ", style=st.num)
            sign = row.tag if k == 0 else " "
            sign_style = st.sign_add if row.tag == "+" else st.sign_del if row.tag == "-" else st.dim
            line.append(sign, style=f"{sign_style} {bg}".strip())
            for text, syn in segs:
                style = Style.parse(code_style) if code_style else Style()
                style = style + syn if syn else style
                if bg:
                    style = style + Style.parse(bg)
                line.append(text, style=style)
            out.append(line)
        shown += 1
    if change.truncated and not hidden:
        out.append(Text(lead + f"{g.ellipsis} diff cut at {FULL_ROWS} lines", style=st.dim))
    if hidden:
        out.append(Text(lead + f"{g.ellipsis} +{hidden} lines" + (f" {g.sep} {hint}" if hint else ""), style=st.dim))
    return out, hidden


def split(change: Change, width: int, caps: Caps, highlight: bool = True) -> list[Text]:
    """Old | new side by side, paired rows level, unmatched rows padded. Needs SPLIT_MIN columns."""
    st = styles(caps)
    g = caps.glyphs
    gutter = len(str(change.max_line))
    half = (width - 3) // 2
    code_w = max(8, half - gutter - 3)
    rows = rows_of(change, caps, highlight)
    pairs: list[tuple[Optional[Row], Optional[Row]]] = []
    dels: list[Row] = []
    adds: list[Row] = []

    def flush() -> None:
        for k in range(max(len(dels), len(adds))):
            pairs.append((dels[k] if k < len(dels) else None, adds[k] if k < len(adds) else None))
        dels.clear()
        adds.clear()

    for row in rows:
        if row.tag == "-":
            if adds:
                flush()
            dels.append(row)
        elif row.tag == "+":
            adds.append(row)
        else:
            flush()
            pairs.append((row, row) if row.tag == " " else (row, None))
    flush()

    def side(row: Optional[Row], new_side: bool) -> list[Text]:
        if row is None:
            return [Text(" " * half)]
        if row.tag == "sep":
            return [Text(" " * (gutter + 1) + g.vdots, style=st.dim)]
        tag = row.tag if row.tag != " " else " "
        number = row.new if new_side else row.old
        code_style, bg = _row_style(tag, st)
        out = []
        for k, segs in enumerate(_wrap_segs(row.segs or [("", Style())], code_w)):
            t = Text()
            t.append((str(number).rjust(gutter) if k == 0 else " " * gutter) + " ", style=st.num)
            sign_style = st.sign_add if tag == "+" else st.sign_del if tag == "-" else st.dim
            t.append((tag if k == 0 else " ") + " ", style=f"{sign_style} {bg}".strip())
            for text, syn in segs:
                style = Style.parse(code_style) if code_style else Style()
                style = style + syn if syn else style
                if bg:
                    style = style + Style.parse(bg)
                t.append(text, style=style)
            out.append(t)
        return out

    lines: list[Text] = []
    for left, right in pairs:
        if left is not None and left.tag == "sep":
            lines.append(Text(" " * (gutter + 1) + g.vdots, style=st.dim))
            continue
        lt, rt = side(left, False), side(right, True)
        for k in range(max(len(lt), len(rt))):
            a = lt[k] if k < len(lt) else Text("")
            b = rt[k] if k < len(rt) else Text("")
            a = a.copy()
            a.truncate(half, pad=True)
            row = Text()
            row.append_text(a)
            row.append(f" {g.vbar} ", style=st.dim)
            row.append_text(b)
            lines.append(row)
    return lines


# ── headers and blocks ───────────────────────────────────────────────
def header(change: Change, caps: Caps, *, glyph: str = "", glyph_style: str = "dim", label: str = "",
           duration: str = "", width: int = 100, indent: int = 2) -> Text:
    """'  ✓ Edited src/stats.py · +4 −2                    0.1s'."""
    text = Text(" " * indent)
    if glyph:
        text.append(glyph + " ", style=glyph_style)
    text.append((label or verb(change)) + " ", style="bold")
    room = width - indent - (width_of(duration) + 3 if duration else 0) - width_of(label or verb(change)) - 4
    path = truncate(where(change, caps), max(10, room - 14))
    text.append(path, style="bold")
    tail = counts(change, caps)
    if tail:
        # ASCII's separator is "-", which would read as a count: use parentheses there.
        text.append(" (" if caps.ascii else f" {caps.glyphs.sep} ", style="dim")
        for t, s in tail:
            text.append(t, style=s)
        if caps.ascii:
            text.append(")", style="dim")
    note = withheld_note(change)
    if note and not change.hunks:
        text.append(f" {caps.glyphs.sep} {note}", style="dim")
    if duration:
        pad = max(2, width - text.cell_len - width_of(duration))
        text.append(" " * pad + duration, style="dim")
    return text


def block_rows(change: Change, width: int, caps: Caps, verbose: bool = False) -> tuple[list[Text], int]:
    """The rows under a scrollback header: 16 inline (8 for a new file, none for a delete), 400 verbose."""
    if change.op == "delete" and not verbose:
        return [], 0
    if change.op == "move" and not change.hunks:
        return [], 0
    limit = FULL_ROWS if verbose else (CREATE_ROWS if change.op == "create" else INLINE_ROWS)
    return unified(change, width, caps, max_rows=limit, indent=4)


def to_ansi(lines: list[Text], width: int, caps: Caps) -> list[str]:
    """Each row as one ANSI line (no trailing newline), for prompt_toolkit's ANSI formatted text."""
    from .blocks import console

    out = []
    for line in lines:
        buf = io.StringIO()
        con: Console = console(width, caps, buf)
        line = line.copy()
        line.no_wrap = True
        line.overflow = "crop"
        con.print(line, end="", overflow="crop", no_wrap=True, crop=True)
        out.append(buf.getvalue().rstrip("\n"))
    return out


def plain_lines(lines: list[Text]) -> list[str]:
    return [line.plain for line in lines]
