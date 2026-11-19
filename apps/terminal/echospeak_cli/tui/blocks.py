"""Scrollback blocks: what a finished piece of a run looks like once it is printed (spec §3.6).

Blocks are plain data. ``render(block, width, caps)`` turns one into ANSI text with rich, always with
``markup=False`` and ``Text`` objects, and every server string goes through ``sanitize`` first, so
server text can never become rich markup or a terminal escape.
"""
from __future__ import annotations

import io
import os
import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional, Union

from rich.console import Console, Group, RenderableType
from rich.markdown import Heading, ListItem, Markdown
from rich.segment import Segment
from rich.padding import Padding
from rich.table import Table as RTable
from rich.text import Text
from rich.theme import Theme

from ..render import one_line, sanitize
from .motion import fmt_duration_ms, truncate, width_of
from .theme import Caps, agent_color, rich_styles


# ── block types ──────────────────────────────────────────────────────
@dataclass
class Banner:
    version: str
    agent: str
    agent_title: str
    model: str
    cwd: str
    project: str
    mode_label: str
    tagline: str = ""     # kept for callers; the placeholder already types it, so the banner doesn't repeat it
    resumed: str = ""     # 'Resumed "Fix parser" · 14 messages · last active 2h ago'
    last: str = ""        # 'Last here: "Fix parser" · 14 messages · 2h ago' (a fresh start that could resume)
    face: Optional[tuple[str, str, str]] = None   # an entrance frame (tui/avatar.py); None = Echo at rest


@dataclass
class UserLine:
    text: str
    queued: bool = False


@dataclass
class AgentHeader:
    agent_id: str
    name: str
    title: str = ""
    delegated_by: str = ""


@dataclass
class MarkdownBlock:
    text: str
    bullet: bool = False      # "● " before the first line (single-agent runs)


@dataclass
class ToolLine:
    status: str               # ok | fail | denied | held
    label: str
    summary: str = ""
    duration_ms: float = 0
    preview: list[str] = field(default_factory=list)
    more: int = 0


@dataclass
class ThinkingLine:
    seconds: float
    text: str = ""            # shown only in verbose mode


@dataclass
class Note:
    glyph: str
    text: str
    style: str = "dim"        # role name: dim | bold | accent | attention | error
    indent: int = 0


@dataclass
class RuleLine:
    text: str
    gap: bool = False         # a blank line after it (the end-of-turn separator)


@dataclass
class Outcome:
    glyph: str
    headline: str
    style: str                # accent | bold | error
    check: str = ""           # "≡ Checks pass" line


@dataclass
class TaskBoard:
    tasks: list[dict]


@dataclass
class Table:
    """Command output: rows of cells, first row may be a header. Cells are sanitized here."""
    rows: list[list[str]]
    header: bool = False
    title: str = ""
    styles: list[str] = field(default_factory=list)   # per-row role ("" | dim | accent | bold)


@dataclass
class FileDiff:
    """A finished file edit: header, then numbered hunks (tui/diffview.py)."""
    change: Any                   # diffview.Change
    status: str = "ok"            # ok | fail
    duration_ms: float = 0
    full: bool = False            # the transcript copy: every row the backend sent


@dataclass
class Raw:
    """Pre-rendered ANSI from our own code (never server text)."""
    ansi: str


Block = Union[Banner, UserLine, AgentHeader, MarkdownBlock, ToolLine, ThinkingLine, Note, RuleLine, Outcome,
              TaskBoard, Table, FileDiff, Raw]


# ── rendering ────────────────────────────────────────────────────────
class _LeftHeading(Heading):
    LEVEL_ALIGN = {f"h{i}": "left" for i in range(1, 7)}


class _DotItem(ListItem):
    """Numbered items as "1." (rich prints a bare "1"), wrapped lines hanging under the text."""

    def render_number(self, console, options, number: int, last_number: int):
        number_width = len(str(last_number)) + 3
        lines = console.render_lines(self.elements, options.update(width=options.max_width - number_width), style=self.style)
        number_style = console.get_style("markdown.item.number", default="none")
        numeral = Segment(f"{number}.".rjust(number_width - 1) + " ", number_style)
        padding = Segment(" " * number_width, number_style)
        for i, line in enumerate(lines):
            yield numeral if i == 0 else padding
            yield from line
            yield Segment("\n")


class EchoMarkdown(Markdown):
    elements = {**Markdown.elements, "heading_open": _LeftHeading, "list_item_open": _DotItem}


def _theme(caps: Caps) -> Theme:
    code = "bold cyan" if caps.color else "bold"
    return Theme({
        "markdown.code": code, "markdown.code_block": "", "markdown.h1": "bold underline", "markdown.h2": "bold",
        "markdown.h3": "bold", "markdown.h4": "bold italic", "markdown.link": "underline",
        "markdown.link_url": "dim underline", "markdown.block_quote": "dim italic", "markdown.hr": "dim",
        "markdown.item.bullet": "bold", "markdown.item.number": "bold",
    })


def console(width: int, caps: Caps, file: Optional[io.StringIO] = None) -> Console:
    return Console(file=file or io.StringIO(), force_terminal=True, width=max(1, width),
                   color_system=("truecolor" if caps.truecolor else "standard") if caps.color else "standard",
                   no_color=not caps.color, markup=False, emoji=False, highlight=False, legacy_windows=False,
                   soft_wrap=False, force_jupyter=False, force_interactive=False, theme=_theme(caps), _environ={})


_TRAILING = re.compile(r"[ \t]+((?:\x1b\[[0-9;]*m)*)$")


def _strip_trailing(text: str) -> str:
    """rich pads every line to the width; trailing blanks wrap badly when the terminal narrows."""
    out = []
    for line in text.split("\n"):
        prev = None
        while prev != line:
            prev, line = line, _TRAILING.sub(r"\1", line)
        out.append(line)
    return "\n".join(out)


def _render(renderable: RenderableType, width: int, caps: Caps) -> str:
    buf = io.StringIO()
    con = console(width, caps, buf)
    con.print(renderable, end="")
    out = _strip_trailing(buf.getvalue())
    return out if out.endswith("\n") else out + "\n"


def _t(text: Any, style: str = "") -> Text:
    """A Text from untrusted server data. Never parsed as markup."""
    return Text(sanitize(text), style=style)


def render(block: Block, width: int, caps: Caps, verbose: bool = False) -> str:
    st = rich_styles(caps)
    g = caps.glyphs
    width = max(1, width)   # the real width: a wider line would be wrapped again by the terminal
    if isinstance(block, Raw):
        return block.ansi
    if isinstance(block, Banner):
        return "\n" + _banner(block, width, caps)
    if isinstance(block, UserLine):
        body = Text(sanitize(block.text).rstrip("\n"))
        if block.queued:
            body.append("  (queued)", style="dim")
        # `›` and two spaces, the composer's prompt column: what you sent stays where you typed it.
        grid = RTable.grid(padding=(0, 2 if width >= 30 else 1, 0, 0))
        grid.add_column(no_wrap=True)
        grid.add_column(overflow="fold")
        grid.add_row(Text(g.prompt, style=st["user"]), body)
        return _render(grid, width, caps) + "\n"
    if isinstance(block, AgentHeader):
        text = Text()
        text.append(f"{g.bullet} ", style=agent_color(block.agent_id, caps))
        text.append(one_line(block.name, 60), style="bold")
        if block.title:
            text.append(" · " + one_line(block.title, 60), style="dim")
        if block.delegated_by:
            text.append(f"  (from {one_line(block.delegated_by, 40)})", style="dim")
        return _render(text, width, caps)
    if isinstance(block, MarkdownBlock):
        return _markdown(block, width, caps)
    if isinstance(block, ToolLine):
        return _tool(block, width, caps, verbose)
    if isinstance(block, FileDiff):
        return _file_diff(block, width, caps, verbose)
    if isinstance(block, ThinkingLine):
        secs = max(1, round(block.seconds))
        text = Text(f"  {g.thought} Thought for {secs}s", style="dim italic")
        out = _render(text, width, caps)
        if verbose and block.text.strip():
            body = Text(sanitize(block.text).strip(), style="dim italic")
            out += _render(Padding(body, (0, 0, 0, 4)), width, caps)
        return out
    if isinstance(block, Note):
        style = st.get(block.style, block.style)
        body = Text(sanitize(block.text), style=style)
        if not block.glyph:
            return _render(Padding(body, (0, 0, 0, block.indent)), width, caps)
        # Hanging indent: wrapped lines align with the text, not the glyph.
        grid = RTable.grid(padding=(0, 1, 0, 0))
        grid.add_column(no_wrap=True)
        grid.add_column(overflow="fold")
        grid.add_row(Text(block.glyph, style=style), body)
        return _render(Padding(grid, (0, 0, 0, block.indent)), width, caps)
    if isinstance(block, RuleLine):
        if width < 10:
            line = Text(g.rule * width, style=st["rule"])
        else:
            label = f" {truncate(one_line(block.text, 300), width - 6)} "
            line = Text(g.rule * 2 + label + g.rule * 2, style=st["rule"])
        return _render(line, width, caps) + ("\n" if block.gap else "")
    if isinstance(block, Outcome):
        text = Text()
        style = st.get(block.style, block.style)
        text.append(f"{block.glyph} ", style=style)
        text.append(sanitize(block.headline), style=style)
        out = "\n" + _render(text, width, caps)
        if block.check:
            out += _render(Text(f"  {g.check} " + sanitize(block.check), style="dim" if block.style in {"accent", ""} else "bold"),
                           width, caps)
        return out
    if isinstance(block, TaskBoard):
        lines = []
        for task in block.tasks:
            if not isinstance(task, dict):
                continue
            status = str(task.get("status") or "")
            if status in {"done", "completed"}:
                glyph, style = g.ok, "dim strike"
            elif status in {"in_progress", "running", "doing"}:
                glyph, style = g.task_doing, st["accent"]
            else:
                glyph, style = g.task_open, ""
            row = Text(f"  {glyph} ", style=style)
            row.append(f"{one_line(task.get('owner') or '?', 30)} {g.handoff} {one_line(task.get('task'), 200)}", style=style)
            lines.append(row)
        return _render(Group(*lines), width, caps) if lines else ""
    if isinstance(block, Table):
        return _table(block, width, caps)
    return ""


def _banner(b: Banner, width: int, caps: Caps) -> str:
    st = rich_styles(caps)
    g = caps.glyphs
    if width < 60:
        text = Text()
        text.append(f"{g.bullet} ", style=st["accent"])
        text.append("EchoSpeak", style="bold")
        text.append(f" v{b.version}" if b.version else "", style="dim")
        text.append(f" · {one_line(b.agent, 30)} · {one_line(b.model, 40)}", style="")
        out = _render(text, width, caps)
        for note in (b.resumed, b.last):
            if note:
                out += _render(Text(sanitize(note), style="dim"), width, caps)
        return out + "\n"
    lines = banner_rows(b, width, caps)
    out = _render(Group(*lines), width, caps)
    notes = [x for x in (b.resumed, b.last) if x]
    if notes:
        # Aligned with the banner's text column, so it reads as part of the header.
        indent = " " * (width_of(lines[0].plain.split("  ", 1)[0]) + 2)
        out += "\n" + "".join(_render(Text(indent + sanitize(x), style="dim"), width, caps) for x in notes)
    return out + "\n"


def banner_rows(b: Banner, width: int, caps: Caps) -> list[Text]:
    """The three banner rows: Echo's face, then name and version, agent and model, folder and mode.

    The face is a fixed 8-cell canvas (tui/avatar.py) in every frame, so the text beside it never moves
    while it animates. ASCII terminals get the ASCII face.
    """
    from . import avatar

    g = caps.glyphs
    face = (b.face or avatar.FACE_REST) if not caps.ascii else g.face
    pad = "  "
    l1 = Text()
    l1.append(face[0], style="bold")
    l1.append(pad + "EchoSpeak", style="bold")
    l1.append(f" v{one_line(b.version, 20)}" if b.version else "", style="dim")
    l2 = Text()
    l2.append(face[1], style="bold")
    l2.append(pad + one_line(b.agent, 30), style="bold")
    rest = f" {g.sep} ".join(x for x in (one_line(b.agent_title, 30), one_line(b.model, 50)) if x)
    l2.append(f" {g.sep} {rest}" if rest else "", style="")
    l3 = Text()
    l3.append(face[2], style="bold")
    l3.append(pad + f" {g.sep} ".join(x for x in (one_line(b.cwd, 60), one_line(b.project, 40) and f"project {one_line(b.project, 40)}",
                                            b.mode_label) if x), style="dim")
    for row in (l1, l2, l3):
        row.truncate(width, overflow="ellipsis")
    return [l1, l2, l3]


_FENCE_LINE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")


def hard_breaks(text: str) -> str:
    """Keep the model's single line breaks (poems, addresses, step lists) outside code fences."""
    lines = text.split("\n")
    out, fence = [], ""
    for i, line in enumerate(lines):
        m = _FENCE_LINE.match(line)
        if fence:
            if m and m.group(1)[0] == fence[0]:
                fence = ""
            out.append(line)
            continue
        if m:
            fence = m.group(1)
            out.append(line)
            continue
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if line.strip() and nxt.strip() and not line.endswith("  ") and not _FENCE_LINE.match(nxt):
            line += "  "
        out.append(line)
    return "\n".join(out)


def _markdown(b: MarkdownBlock, width: int, caps: Caps) -> str:
    text = hard_breaks(sanitize(b.text).strip("\n"))
    if not text.strip():
        return ""
    md = EchoMarkdown(text, code_theme="ansi_dark", hyperlinks=False, inline_code_theme="ansi_dark")
    body = _render(Padding(md, (0, 0, 0, 2)), width, caps)
    lines = body.split("\n")
    # rich pads lists and code blocks with blank lines; blocks are spaced by us instead.
    blank = re.compile(r"^(?:\x1b\[[0-9;]*m|\s)*$")
    while lines and blank.match(lines[0]):
        lines.pop(0)
    while lines and blank.match(lines[-1]):
        lines.pop()
    lines = [line for i, line in enumerate(lines)
             if not (blank.match(line) and i > 0 and blank.match(lines[i - 1]))]
    if b.bullet and lines:
        st = rich_styles(caps)
        # Replace the 2-space indent on the first non-empty line with the bullet.
        bullet = Text(caps.glyphs.bullet, style=st["agent.echo"])
        bullet_ansi = _render(bullet, width, caps).rstrip("\n") + " "
        for i, line in enumerate(lines):
            if line.strip():
                lines[i] = bullet_ansi + _strip_indent(line, 2)
                break
    return "\n".join(lines) + "\n\n" if lines else ""


def _strip_indent(ansi_line: str, n: int) -> str:
    """Remove n leading spaces from a rendered line that may start with SGR codes."""
    i = 0
    prefix = ""
    while i < len(ansi_line) and ansi_line[i] == "\x1b":
        j = ansi_line.find("m", i)
        if j < 0:
            break
        prefix += ansi_line[i:j + 1]
        i = j + 1
    rest = ansi_line[i:]
    removed = 0
    while removed < n and rest.startswith(" "):
        rest = rest[1:]
        removed += 1
    return prefix + rest


def _tool(b: ToolLine, width: int, caps: Caps, verbose: bool) -> str:
    g = caps.glyphs
    st = rich_styles(caps)
    glyph = {"ok": g.ok, "fail": g.fail, "denied": g.denied, "held": g.held}.get(b.status, g.ok)
    # A finished step is quiet (all dim). A step that failed, was denied or is held is not: its glyph carries
    # the meaning (✗ ⊘ ⏸, readable in any theme), its label is normal weight, its detail stays dim.
    glyph_style = {"ok": "dim", "fail": st["error"], "denied": "bold", "held": st["attention"]}.get(b.status, "")
    label_style = "dim" if b.status == "ok" else ""
    detail_style = st["attention"] if b.status == "held" else "dim"
    dur = fmt_duration_ms(b.duration_ms)
    label = home_short(one_line(b.label, 300))
    summary = home_short(one_line(b.summary, 200))
    if b.status == "fail":
        summary = re.sub(r"^(?:failed|error)\s*:\s*", "", summary, flags=re.I)   # ✗ already says it failed
    sep = f" {g.sep} "
    pad = "  " if width >= 30 else ""
    head = f"{pad}{glyph} "
    room = width - width_of(head) - (width_of(dur) + 2 if dur else 0)
    if dur and room < 12:
        dur, room = "", width - width_of(head)   # the duration goes before the label is cut short
    # Detail is dropped first, then the label is cut.
    label = truncate(label, max(1, room))
    detail = truncate(sep + summary, max(0, room - width_of(label))) if summary else ""
    if detail and width_of(detail) <= width_of(sep) + 1:
        detail = ""
    line = Text()                       # no base style: each span keeps its own (a base style would tint them all)
    line.append(head, style=glyph_style)
    line.append(label, style=label_style)
    if detail:
        line.append(detail, style=detail_style)
    if dur:
        line.append(" " * max(2, room - width_of(label) - width_of(detail) + 2) + dur, style="dim")
    lines = [line]
    seen = _plain_key(summary)
    preview = [row for row in b.preview if not (seen and _plain_key(row) and _plain_key(row) in seen)]
    if preview:
        first, rest = (f"    {g.elbow}  ", "       ") if width >= 30 else (f"{g.elbow} ", "  ")
        for i, row in enumerate(preview):
            lead = first if i == 0 else rest
            lines.append(Text(lead + truncate(home_short(sanitize(row)), max(1, width - width_of(lead) - 1)), style="dim"))
        if b.more:
            more = f"{g.ellipsis} +{b.more} lines (ctrl+o for details)"
            lines.append(Text(rest + truncate(more, max(1, width - width_of(rest))), style="dim"))
    for t in lines:
        t.no_wrap, t.overflow = True, "crop"
    return _render(Group(*lines), width, caps)


def home_short(text: str) -> str:
    """Your home folder as ~ in what the terminal shows (paths in tool lines and errors)."""
    home = os.path.expanduser("~").rstrip("/")
    if not home or home == "~" or len(home) < 2:
        return text
    return re.sub(re.escape(home) + r"(?=/|\b)", "~", text)


def _plain_key(text: str) -> str:
    """A line reduced for "does this repeat that?": no Error/Failed prefix, case or spacing."""
    text = re.sub(r"^\s*(?:error|failed)\s*:\s*", "", home_short(str(text or "")), flags=re.I)
    return " ".join(text.lower().split())


def _file_diff(b: FileDiff, width: int, caps: Caps, verbose: bool) -> str:
    from . import diffview as DV

    g = caps.glyphs
    st = rich_styles(caps)
    ok = b.status == "ok"
    proposed = getattr(b.change, "status", "") == "proposed"
    head = DV.header(b.change, caps, glyph=(g.attention if proposed else g.ok if ok else g.fail),
                     glyph_style="dim" if ok else st["error"], label=DV.proposed_label(b.change) if proposed else "",
                     duration=fmt_duration_ms(b.duration_ms), width=width)
    rows, _hidden = DV.block_rows(b.change, width, caps, verbose=verbose or b.full)
    lines = [head] + rows
    for line in lines:
        line.no_wrap = True
        line.overflow = "crop"
    return _render(Group(*lines), width, caps)


def _table(b: Table, width: int, caps: Caps) -> str:
    st = rich_styles(caps)
    if not b.rows:
        return ""
    cols = max(len(r) for r in b.rows)
    widths = [0] * cols
    clean = [[one_line(c, 200) for c in r] + [""] * (cols - len(r)) for r in b.rows]
    for r in clean:
        for i, c in enumerate(r):
            widths[i] = max(widths[i], width_of(c))
    # Shrink the widest column until it fits.
    total = lambda: sum(widths) + 2 * (cols - 1) + 2  # noqa: E731
    while total() > width and max(widths) > 8:
        widths[widths.index(max(widths))] -= 1
    out = []
    if b.title:
        out.append(Text(""))
        out.append(Text(sanitize(b.title), style="bold"))
    for n, r in enumerate(clean):
        role = (b.styles[n] if n < len(b.styles) else "") or ("dim" if b.header and n == 0 else "")
        style = st.get(role, role)
        line = Text("  ")
        for i, c in enumerate(r):
            cell = truncate(c, widths[i])
            line.append(cell + (" " * (widths[i] - width_of(cell)) + "  " if i < cols - 1 else ""), style=style)
        out.append(line)
    return _render(Group(*out), width, caps)


def args_text(args: Any, limit: Optional[int] = None) -> list[str]:
    """Approval arguments as JSON lines: control bytes stay visible as \\u escapes."""
    try:
        dumped = json.dumps(args, indent=2, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        dumped = str(args)
    lines = [sanitize(x) for x in dumped.splitlines()]
    return lines if limit is None else lines[:limit]
