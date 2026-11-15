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
    """`◉ Echo is thinking it through… (14s · ↓ 1.2k tokens · esc to stop)`.

    `esc to stop` keeps its place; tokens, then detail, then elapsed are dropped as width runs out.
    Under 40 columns it collapses to `glyph Working… esc`.
    """
    g = caps.glyphs
    if si.needs_ok:
        return [("class:attention", f"{g.still} "), ("class:attention", truncate(si.activity, max(4, width - 2)))]
    style, glyph = ping(now_ms, caps, si.stalled_ms)
    head: Fragments = [(f"class:{style}", glyph + " ")]
    if width < 40:
        return head + [("", "Working" + g.ellipsis + " "), ("class:dim", "esc")]
    activity = si.activity.rstrip("…").rstrip(".") + g.ellipsis
    stop_hint = "stopping" + g.ellipsis if si.stopping else "esc to stop"
    optional = []
    elapsed = fmt_elapsed(si.elapsed_s)
    if si.tokens:
        optional.append(("tokens", f"↓ {fmt_tokens(si.tokens)} tokens" if not caps.ascii else f"{fmt_tokens(si.tokens)} tokens"))
    stall = ""
    if si.stalled_ms >= STALL_BOLD_MS:
        stall = f"still waiting ({int(si.stalled_ms // 1000)}s)"
    elif si.stalled_ms >= STALL_DIM_MS:
        stall = "waiting on the model"
    parts = [("elapsed", elapsed)] + optional

    def build(keep: list[tuple[str, str]], detail: str) -> Fragments:
        body = shimmer(activity, now_ms, caps) if not si.stalled_ms >= STALL_DIM_MS else [("", activity)]
        out = head + body
        sep = f" {g.sep} "
        if detail:
            out += [("class:dim", sep + detail)]
        if stall:
            out += [("class:dim" if si.stalled_ms < STALL_BOLD_MS else "bold", sep + stall)]
        inner = sep.join([t for _k, t in keep] + [stop_hint])
        out += [("class:dim", f" ({inner})")]
        return out

    detail = si.detail
    keep = list(parts)
    candidate = build(keep, detail)
    # Drop tokens, then detail, then elapsed.
    while frag_width(candidate) > width:
        if any(k == "tokens" for k, _ in keep):
            keep = [p for p in keep if p[0] != "tokens"]
        elif detail:
            detail = ""
        elif keep:
            keep = []
        else:
            budget = width - frag_width(head) - width_of(f" ({stop_hint})") - 1
            body = truncate(activity, max(4, budget))
            return head + [("", body), ("class:dim", f" ({stop_hint})")]
        candidate = build(keep, detail)
    return candidate


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
    "autonomous": "autonomous",
    "always": "ask before every action",
    "never": "never ask",
}
MODE_LINE = {
    "autonomous": "autonomous: routine approvals won't interrupt",
    "always": "always ask: every action waits for your OK",
    "never": "never ask: actions run without approval",
}


def footer_lines(fi: FooterInput, width: int, caps: Caps) -> list[Fragments]:
    """Left: model · agent · project · mode (+ context %, held). Right: hints. Plus the mode line."""
    g = caps.glyphs
    mode_glyph = "◇" if not caps.ascii else "o"
    narrow = width < 60
    left: Fragments = []

    def add(text: str, style: str = "class:footer") -> None:
        if not text:
            return
        if left:
            left.append(("class:footer", f" {g.sep} "))
        left.append((style, text))

    add(fi.model)
    if fi.context_pct is not None and fi.context_pct >= 50:
        add(f"context {fi.context_pct}%", "class:footer.bold" if fi.context_pct >= 85 else "class:footer")
    if not narrow:
        add(fi.agent)
        add(fi.project)
        add(f"{mode_glyph} {MODE_LABEL.get(fi.approval_mode, fi.approval_mode)}")
        if fi.held:
            add(f"{g.held} {fi.held} held {g.sep} /approvals", "class:attention")
    if fi.notice:
        right = fi.notice
        right_style = "class:footer.bold"
    elif narrow or fi.quiet:
        right, right_style = "", "class:footer"
    elif fi.running:
        right, right_style = f"esc to stop {g.sep} enter to steer {g.sep} tab to queue", "class:footer"
    else:
        right, right_style = "? for shortcuts", "class:footer"
    # Fit: shrink the left side, never the hint.
    avail = width - 2 - (width_of(right) + 2 if right else 0)
    while left and frag_width(left) > avail:
        # Drop from the end (least important), keeping at least the model.
        if len(left) > 1:
            left = left[:-2]
        else:
            left = [(left[0][0], truncate(left[0][1], max(1, avail)))]
            break
    line: Fragments = [("", "  ")] + left
    if right:
        pad = max(2, width - frag_width(line) - width_of(right))
        line += [("", " " * pad), (right_style, right)]
    lines = [line]
    if fi.approval_mode in MODE_LINE and not narrow:
        text = f"{'◆' if not caps.ascii else '*'} {MODE_LINE[fi.approval_mode]} (shift+tab to cycle)"
        lines.append([("", "  "), ("class:mode", truncate(text, width - 3))])
    return lines


def rule(width: int, title: str, caps: Caps, style: str = "class:rule", title_style: str = "class:rule.title") -> Fragments:
    """`──────── title ─` (title right-aligned, Claude's capture)."""
    ch = caps.glyphs.rule
    if not title or width < 20:
        return [(style, ch * max(0, width))]
    title = truncate(title, max(5, width // 2))
    return [(style, ch * max(1, width - width_of(title) - 3)), (title_style, f" {title} "), (style, ch)]
