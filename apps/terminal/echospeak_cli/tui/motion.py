"""Time-based animation and the one-line chrome (status line, footer). Pure functions of (state, now, width).

Every frame derives from a monotonic clock in milliseconds; nothing counts ticks, so a slow redraw
never makes the animation drift or speed up.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from wcwidth import wcswidth

from .theme import Caps

Fragments = list[tuple[str, str]]

PING_MS = 120
SHIMMER_PERIOD_MS = 1800
STALL_DIM_MS = 10_000
STALL_BOLD_MS = 30_000
FLAVOURS = ("Listening", "Tuning in", "Sounding it out", "Picking up the signal", "Reverberating")
FLAVOUR_AFTER_MS = 2000
FLAVOUR_HOLD_MS = 4000

# Typewriter timings from the GUI's WelcomeTypewriter.
TYPE_MS = 34
HOLD_MS = 2200
DELETE_MS = 18
GAP_MS = 350

PHRASES = ("Ask a question, search the web, or explore an idea.",
           "Write, research, and turn a thought into something useful.",
           "Drop in a file or folder and work through it together.")
AGENT_PHRASES = ("Give your agent a task, or just catch up.",
                 "Your agents remember, follow up, and check in with you.",
                 "Hand off research, building, or anything you repeat.")
CODE_PHRASES = ("Build a feature, fix a bug, or explore your code.",
                "Review changes and work through ideas with your agent.",
                "Keep your project, tools, and progress in one workspace.")


def width_of(text: str) -> int:
    w = wcswidth(text)
    return w if w >= 0 else len(text)


def frag_width(frags: Fragments) -> int:
    return sum(width_of(t) for _s, t in frags)


def truncate(text: str, limit: int, ellipsis: str = "…") -> str:
    if limit <= 0:
        return ""
    if width_of(text) <= limit:
        return text
    out, used = [], 0
    for ch in text:
        w = max(0, width_of(ch))
        if used + w > limit - width_of(ellipsis):
            break
        out.append(ch)
        used += w
    return "".join(out) + ellipsis


# ── spinner ──────────────────────────────────────────────────────────
def ping_frame(now_ms: float) -> int:
    return int(now_ms // PING_MS) % 8


def ping(now_ms: float, caps: Caps, stalled_ms: float = 0) -> tuple[str, str]:
    """(style class, glyph) for the Echo ping: a ripple that grows and fades."""
    if caps.reduced_motion:
        return ("status.glyph", caps.glyphs.still)
    i = ping_frame(now_ms)
    glyph = caps.glyphs.ping[i]
    if stalled_ms >= STALL_DIM_MS or i == 7:
        return ("status.glyph.dim", glyph)
    return ("status.glyph" if i < 4 else "accent" if caps.color else "", glyph)


def shimmer_levels(length: int, now_ms: float) -> list[int]:
    """0 dim / 1 normal / 2 bold per cell. The band centre crosses len + 2h cells every 1.8 s."""
    if length <= 0:
        return []
    h = max(3.0, 0.1 * length)
    span = length + 2 * h
    centre = (now_ms % SHIMMER_PERIOD_MS) / SHIMMER_PERIOD_MS * span - h
    out = []
    for i in range(length):
        d = abs(i - centre)
        t = 0.5 * (1 + math.cos(math.pi * d / h)) if d < h else 0.0
        out.append(0 if t < 0.2 else 1 if t < 0.6 else 2)
    return out


def shimmer(text: str, now_ms: float, caps: Caps) -> Fragments:
    if caps.reduced_motion or not text:
        return [("", text)]
    levels = shimmer_levels(len(text), now_ms)
    frags: Fragments = []
    for ch, lvl in zip(text, levels):
        style = f"class:shimmer.{lvl}"
        if frags and frags[-1][0] == style:
            frags[-1] = (style, frags[-1][1] + ch)
        else:
            frags.append((style, ch))
    return frags


def typewriter(now_ms: float, phrases: tuple[str, ...]) -> str:
    """The placeholder as the GUI types it: 34 ms/char, hold 2.2 s, delete 18 ms/char, 350 ms gap."""
    if not phrases:
        return ""
    cycles = [len(p) * TYPE_MS + HOLD_MS + len(p) * DELETE_MS + GAP_MS for p in phrases]
    t = now_ms % sum(cycles)
    for phrase, cycle in zip(phrases, cycles):
        if t >= cycle:
            t -= cycle
            continue
        typing = len(phrase) * TYPE_MS
        if t < typing:
            return phrase[: int(t // TYPE_MS) + 1]
        t -= typing
        if t < HOLD_MS:
            return phrase
        t -= HOLD_MS
        deleting = len(phrase) * DELETE_MS
        if t < deleting:
            return phrase[: max(0, len(phrase) - int(t // DELETE_MS) - 1)]
        return ""
    return ""


# ── numbers ──────────────────────────────────────────────────────────
def fmt_elapsed(seconds: float) -> str:
    """0s, 59s, 1m 05s, 1h 02m 03s (Codex fmt_elapsed_compact)."""
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {(s % 3600) // 60:02d}m {s % 60:02d}s"


def fmt_tokens(n: int) -> str:
    if n < 1000:
        return str(n)
    if n < 100_000:
        return f"{n / 1000:.1f}k".replace(".0k", "k")
    if n < 1_000_000:
        return f"{n // 1000}k"
    return f"{n / 1_000_000:.1f}M"


def fmt_duration_ms(ms: float) -> str:
    try:
        ms = float(ms or 0)
    except (TypeError, ValueError):
        return ""
    if ms <= 0:
        return ""
    if ms < 1000:
        return f"{ms / 1000:.1f}s" if ms >= 50 else "0.0s"
    if ms < 60_000:
        return f"{ms / 1000:.1f}s"
    return fmt_elapsed(ms / 1000)


# ── activity wording ────────────────────────────────────────────────
def thinking_words(name: str, seconds: float) -> str:
    if seconds >= 45:
        return f"{name} is deep in thought"
    if seconds >= 20:
        return f"{name} is still thinking"
    return f"{name} is thinking it through"


def flavour(now_ms: float, quiet_ms: float, caps: Caps) -> str:
    """An Echo-flavoured verb when nothing more specific has been known for 2 s (each holds 4 s)."""
    if caps.plain_verbs or quiet_ms < FLAVOUR_AFTER_MS:
        return ""
    return FLAVOURS[int((quiet_ms - FLAVOUR_AFTER_MS) // FLAVOUR_HOLD_MS) % len(FLAVOURS)]


# ── status line ──────────────────────────────────────────────────────
@dataclass
class StatusInput:
    activity: str
    elapsed_s: float
    tokens: int = 0
    stalled_ms: float = 0
    needs_ok: bool = False
    detail: str = ""
    stopping: bool = False


def status_line(si: StatusInput, now_ms: float, width: int, caps: Caps) -> Fragments:
    """`◉ Echo is thinking it through… (14s · ↓ 1.2k tokens · esc to stop)`, degraded one step at a time.

    The ladder, richest first: drop tokens → drop the detail → shorten the stop hint to `esc` → drop
    the stall note → drop elapsed → cut the activity (never below a few letters) → glyph and `esc`.
    The glyph is always first and the stop hint, when shown, always last, so nothing jumps around.
    """
    g = caps.glyphs
    width = max(1, width)
    if si.needs_ok:
        return [("class:attention", f"{g.still} "), ("class:attention", truncate(si.activity, max(1, width - 2)))]
    style, glyph = ping(now_ms, caps, si.stalled_ms)
    head: Fragments = [(f"class:{style}", glyph + " ")]
    if width < 4:
        return [(f"class:{style}", glyph)]
    activity = si.activity.rstrip("…").rstrip(".") + g.ellipsis
    stalled = si.stalled_ms >= STALL_DIM_MS
    tokens = ""
    if si.tokens:
        tokens = f"↓ {fmt_tokens(si.tokens)} tokens" if not caps.ascii else f"{fmt_tokens(si.tokens)} tokens"
    stall = ""
    if si.stalled_ms >= STALL_BOLD_MS:
        stall = f"still waiting ({int(si.stalled_ms // 1000)}s)"
    elif stalled:
        stall = "waiting on the model"
    elapsed = fmt_elapsed(si.elapsed_s)
    sep = f" {g.sep} "

    def build(act: str, detail: str, stall_txt: str, parts: list[str], stop: str) -> Fragments:
        body = shimmer(act, now_ms, caps) if not stalled else [("", act)]
        out = head + body
        if detail:
            out += [("class:dim", sep + detail)]
        if stall_txt:
            out += [("class:dim" if si.stalled_ms < STALL_BOLD_MS else "bold", sep + stall_txt)]
        inner = sep.join([p for p in parts if p] + ([stop] if stop else []))
        if inner:
            out += [("class:dim", f" ({inner})")]
        return out

    stop_full = "stopping" + g.ellipsis if si.stopping else "esc to stop"
    stop_short = "stopping" if si.stopping else "esc"
    ladder = [
        (si.detail, stall, [elapsed, tokens], stop_full),
        (si.detail, stall, [elapsed], stop_full),
        ("", stall, [elapsed], stop_full),
        ("", stall, [], stop_full),
        ("", stall, [elapsed], stop_short),
        ("", "", [elapsed], stop_short),
        ("", "", [], stop_short),
    ]
    for detail, stall_txt, parts, stop in ladder:
        candidate = build(activity, detail, stall_txt, parts, stop)
        if frag_width(candidate) <= width:
            return candidate
    # Cut the activity, keeping `(esc)` while at least a few letters of it still fit.
    for stop in (stop_short, ""):
        tail = width_of(f" ({stop})") if stop else 0
        room = width - frag_width(head) - tail
        if room >= 6 or not stop:
            act = truncate(activity, max(1, room), g.ellipsis if room > width_of(g.ellipsis) + 1 else "")
            return head + [("", act)] + ([("class:dim", f" ({stop})")] if stop else [])
    return head


# ── footer ───────────────────────────────────────────────────────────
@dataclass
class FooterInput:
    model: str = ""
    agent: str = ""
    project: str = ""
    approval_mode: str = "smart"
    context_pct: Optional[int] = None
    held: int = 0
    running: bool = False
    notice: str = ""            # transient one-liner ("press ctrl+c again to exit")
    quiet: bool = False         # a dialog has the keys: no hints on the right
    hints: list[str] = field(default_factory=list)


MODE_LABEL = {
    "smart": "ask for risky actions",
    "autonomous": "work independently",
    "always": "ask first",
    "never": "don't ask",
}
MODE_SHORT = {"smart": "ask if risky", "autonomous": "indep.", "always": "ask first", "never": "don't ask"}
MODE_LINE = {
    "autonomous": "work independently: routine steps won't interrupt",
    "always": "ask first: every action waits for your OK",
    "never": "don't ask: actions run without approval",
}
MODE_LINE_MIN_W = 30
CONTEXT_BOLD = 85
LEFT_ORDER = ("model", "context", "agent", "project", "mode", "held")

# The order in which the footer gives things up as the terminal narrows: (item, level). Level 0 is
# the full form; each step moves one item to a shorter form, and an empty form means hidden.
# Context and held approvals are shortened last and never hidden; the model is cut, never dropped.
FOOTER_LADDER = (
    ("hints", 1), ("project", 1), ("agent", 1), ("mode", 1), ("project", 2), ("agent", 2), ("held", 1),
    ("hints", 2), ("context", 1), ("model", 1), ("mode", 2), ("model", 2), ("context", 2), ("held", 2),
    ("mode", 3), ("model", 3),
)


def _footer_forms(fi: FooterInput, caps: Caps) -> dict[str, list[tuple[str, str]]]:
    """Every item's forms, longest first: [(style, text), ...]; an empty text means hidden."""
    g = caps.glyphs
    mode_glyph = "◇" if not caps.ascii else "o"
    forms: dict[str, list[tuple[str, str]]] = {}
    model = fi.model or ""
    forms["model"] = [("class:footer", model), ("class:footer", truncate(model, 16)),
                      ("class:footer", truncate(model, 10)), ("class:footer", truncate(model, 6))]
    if fi.context_pct is not None:
        pct = max(0, min(100, int(fi.context_pct)))
        st = "class:footer.bold" if pct >= CONTEXT_BOLD else "class:footer"
        forms["context"] = [(st, f"context {pct}%"), (st, f"ctx {pct}%"), (st, f"{pct}%")]
    if fi.held:
        forms["held"] = [("class:attention", f"{g.held} {fi.held} held {g.sep} /approvals"),
                         ("class:attention", f"{g.held} {fi.held} held"), ("class:attention", f"{g.held}{fi.held}")]
    if fi.agent:
        forms["agent"] = [("class:footer", fi.agent), ("class:footer", truncate(fi.agent, 12)), ("class:footer", "")]
    if fi.project:
        forms["project"] = [("class:footer", fi.project), ("class:footer", truncate(fi.project, 14)),
                            ("class:footer", "")]
    label = MODE_LABEL.get(fi.approval_mode, fi.approval_mode)
    short = MODE_SHORT.get(fi.approval_mode, fi.approval_mode)
    forms["mode"] = [("class:footer", f"{mode_glyph} {label}"), ("class:footer", f"{mode_glyph} {short}"),
                     ("class:footer", mode_glyph), ("class:footer", "")]
    if fi.notice:
        forms["hints"] = [("class:footer.bold", fi.notice)] * 3
    elif fi.quiet:
        forms["hints"] = [("class:footer", "")] * 3
    elif fi.running:
        forms["hints"] = [("class:footer", f"esc to stop {g.sep} enter to steer {g.sep} tab to queue"),
                          ("class:footer", f"esc stop {g.sep} tab queue"), ("class:footer", "")]
    else:
        # The session list is one key away on an empty input; say so where the eye rests. On a narrow terminal it's the
        # sessions key that stays: "?" is announced at the start, the session list isn't found by chance.
        forms["hints"] = [("class:footer", f"← sessions {g.sep} ? for shortcuts"), ("class:footer", "← sessions"),
                          ("class:footer", "")]
    return forms


def footer_lines(fi: FooterInput, width: int, caps: Caps) -> list[Fragments]:
    """`  model · context 64% · agent · project · ◇ mode · ⏸ 2 held        ? for shortcuts`.

    Items keep their order at every width. As space runs out they shorten one at a time following
    FOOTER_LADDER, so the line changes gradually instead of losing half its content at one width.
    A notice ("press ctrl+c again to exit") takes the hint's place and is cut, never dropped.
    """
    g = caps.glyphs
    width = max(1, width)
    forms = _footer_forms(fi, caps)
    level = {k: 0 for k in forms}
    indent = 2 if width >= 24 else 1 if width >= 8 else 0
    sep = f" {g.sep} " if width >= 24 else " "

    def compose() -> tuple[Fragments, tuple[str, str]]:
        left: Fragments = []
        for k in LEFT_ORDER:
            if k not in forms:
                continue
            st, text = forms[k][min(level[k], len(forms[k]) - 1)]
            if not text:
                continue
            if left:
                left.append(("class:footer", sep))
            left.append((st, text))
        return left, forms["hints"][min(level["hints"], 2)]

    def total(left: Fragments, right: tuple[str, str]) -> int:
        return indent + frag_width(left) + (2 + width_of(right[1]) if right[1] else 0)

    left, right = compose()
    for item, lvl in FOOTER_LADDER:
        if total(left, right) <= width:
            break
        if item in level and level[item] < lvl:
            level[item] = lvl
            left, right = compose()
    if fi.notice and total(left, right) > width:
        # The notice outranks everything but the model: cut it to the room that is left.
        if width - indent - frag_width(left) - 2 < 12 and left:
            st, text = left[0]
            left = [(st, truncate(text, max(1, min(width_of(text), width - indent - 14))))]
        right = (right[0], truncate(right[1], max(1, width - indent - frag_width(left) - 2)))
    elif total(left, right) > width:
        right = (right[0], "")
    if total(left, right) > width and left:
        # Last resort: the model alone, cut to fit.
        st, text = left[0]
        left = [(st, truncate(text, max(1, width - indent - (2 + width_of(right[1]) if right[1] else 0))))]
    line: Fragments = [("", " " * indent)] + left
    if right[1]:
        gap = 2 if left else 0
        pad = max(gap, width - frag_width(line) - width_of(right[1]))
        line += [("", " " * pad), right]
    lines = [line]
    if fi.approval_mode in MODE_LINE and width >= MODE_LINE_MIN_W:
        star = "◆" if not caps.ascii else "*"
        full = f"{star} {MODE_LINE[fi.approval_mode]} (shift+tab to cycle)"
        if width_of(full) + indent > width:
            full = f"{star} {MODE_LINE[fi.approval_mode]}"
        lines.append([("", " " * indent), ("class:mode", truncate(full, max(1, width - indent - 1)))])
    return lines


def rule(width: int, title: str, caps: Caps, style: str = "class:rule", title_style: str = "class:rule.title") -> Fragments:
    """`──────── title ─` (title right-aligned, Claude's capture). The title is cut to fit, or left out
    below 24 columns, so the rule is always exactly ``width`` cells."""
    ch = caps.glyphs.rule
    width = max(0, width)
    if not title or width < 24:
        return [(style, ch * width)]
    title = truncate(title, max(5, min(width // 2, width - 8)))
    return [(style, ch * max(1, width - width_of(title) - 3)), (title_style, f" {title} "), (style, ch)]
