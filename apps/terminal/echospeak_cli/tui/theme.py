"""Terminal capabilities and the colour roles (spec §3.10, §3.11). Pure: everything comes from ``env``.

ANSI palette by default, so whatever theme the terminal runs (Omarchy re-themes it live) applies.
Meaning is carried by glyph and weight first; hue is only ever a second signal.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

ECHO_ACCENT = "#4c72df"
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


@dataclass(frozen=True)
class Glyphs:
    ping: tuple[str, ...] = ("·", "•", "●", "◉", "◎", "○", "◌", "◌")
    still: str = "●"
    prompt: str = "›"
    select: str = "❯"
    ok: str = "✓"
    fail: str = "✗"
    denied: str = "⊘"
    held: str = "⏸"
    elbow: str = "⎿"
    thought: str = "∴"
    done: str = "◆"
    unverified: str = "◇"
    stopped: str = "■"
    steer: str = "↳"
    handoff: str = "→"
    revised: str = "↺"
    check: str = "≡"
    rule: str = "─"
    bullet: str = "●"
    task_open: str = "○"
    task_doing: str = "◉"
    attention: str = "⠿"
    ellipsis: str = "…"
    arrow_up_down: str = "↑↓"
    sep: str = "·"
    face: tuple[str, str, str] = (" ▗▄▄▄▄▄▖ ", " ▐█ █ █▌ ", " ▝▀▀▀▀▀▘ ")
    minus: str = "−"
    vdots: str = "⋮"
    corner: str = "└"
    vbar: str = "│"
    move: str = "→"


ASCII_GLYPHS = Glyphs(
    ping=(".", "o", "O", "0", "O", "o", ".", "."), still="*", prompt=">", select=">", ok="+", fail="x",
    denied="-", held="=", elbow="|", thought="~", done="*", unverified="o", stopped="#", steer=">",
    handoff="->", revised="~", check="=", rule="-", bullet="*", task_open="o", task_doing="*", attention=">",
    ellipsis="...", arrow_up_down="up/down", sep="-", face=(" +-----+ ", " | o o | ", " +-----+ "),
    minus="-", vdots=":", corner="`", vbar="|", move="->",
)


@dataclass(frozen=True)
class Caps:
    color: bool = True             # False under NO_COLOR: bold/dim/reverse and glyphs remain
    ascii: bool = False            # ECHO_TUI_ASCII=1 or a non-UTF-8 locale
    reduced_motion: bool = False   # ECHO_REDUCED_MOTION=1 or --no-anim
    truecolor: bool = False
    accent_hex: str = ""           # set only when a truecolor accent was asked for
    plain_verbs: bool = False      # ECHO_TUI_VERBS=plain: no flavour verbs
    glyphs: Glyphs = field(default_factory=Glyphs)
    # Diff rows: background tints that differ in luminance, not hue (edit-preview.md §3.2). Truecolor only,
    # derived from the theme background (ECHO_TUI_BG or Omarchy colors.toml); ECHO_TUI_DIFF_TINT=0 turns them off.
    bg_hex: str = ""
    fg_hex: str = ""
    diff_tint: bool = False


def _utf8(env: Mapping[str, str]) -> bool:
    for name in ("LC_ALL", "LC_CTYPE", "LANG"):
        value = env.get(name, "")
        if value:
            return "utf-8" in value.lower() or "utf8" in value.lower()
    return True  # unset: assume a modern UTF-8 terminal


def _omarchy_color(env: Mapping[str, str], key: str) -> str:
    home = Path(env.get("HOME") or Path.home())
    for path in (home / ".local/state/omarchy/current/theme/colors.toml", home / ".config/omarchy/current/theme/colors.toml"):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        match = re.search(r'^\s*' + re.escape(key) + r'\s*=\s*"(#[0-9a-fA-F]{6})"', text, re.M)
        if match:
            return match.group(1)
    return ""


def _omarchy_accent(env: Mapping[str, str]) -> str:
    return _omarchy_color(env, "accent")


def detect(env: Mapping[str, str] = os.environ, *, no_anim: bool = False, ascii_only: Optional[bool] = None) -> Caps:
    color = not env.get("NO_COLOR") and env.get("TERM", "") != "dumb"
    ascii_mode = ascii_only if ascii_only is not None else (env.get("ECHO_TUI_ASCII", "") in {"1", "true", "yes"}
                                                         or not _utf8(env))
    reduced = no_anim or env.get("ECHO_REDUCED_MOTION", "") in {"1", "true", "yes"}
    truecolor = color and env.get("COLORTERM", "").lower() in {"truecolor", "24bit"}
    accent = ""
    wanted = env.get("ECHO_TUI_ACCENT", "").strip()
    if truecolor and wanted:
        if wanted == "echo":
            accent = ECHO_ACCENT
        elif wanted == "auto":
            accent = _omarchy_accent(env)
        elif _HEX.match(wanted):
            accent = wanted
    bg = fg = ""
    tint = truecolor and env.get("ECHO_TUI_DIFF_TINT", "auto").strip().lower() not in {"0", "off", "no", "false"}
    if tint:
        bg = env.get("ECHO_TUI_BG", "").strip()
        bg = bg if _HEX.match(bg) else _omarchy_color(env, "background")
        fg = env.get("ECHO_TUI_FG", "").strip()
        fg = fg if _HEX.match(fg) else (_omarchy_color(env, "foreground") or ("#d6d6d6" if bg else ""))
        tint = bool(bg)
    return Caps(color=color, ascii=ascii_mode, reduced_motion=reduced, truecolor=truecolor, accent_hex=accent,
                plain_verbs=env.get("ECHO_TUI_VERBS", "") == "plain", glyphs=ASCII_GLYPHS if ascii_mode else Glyphs(),
                bg_hex=bg, fg_hex=fg, diff_tint=tint)


# ── roles ────────────────────────────────────────────────────────────
def rich_styles(caps: Caps) -> dict[str, str]:
    """Rich style strings per role, for scrollback blocks."""
    if not caps.color:
        return {"dim": "dim", "accent": "bold", "attention": "bold", "error": "bold", "success": "",
                "rule": "dim", "agent.echo": "bold", "agent.scout": "bold", "agent.forge": "bold",
                "agent.other": "bold", "user": "bold dim", "code": ""}
    accent = caps.accent_hex or "blue"
    return {"dim": "dim", "accent": f"bold {accent}", "attention": "bold yellow", "error": "bold red",
            "success": "green", "rule": "bright_black", "agent.echo": "bold", "agent.scout": "bold blue",
            "agent.forge": "bold magenta", "agent.other": "bold", "user": "bold dim", "code": ""}


def pt_style_dict(caps: Caps) -> dict[str, str]:
    """prompt_toolkit classes for the live region."""
    accent = (caps.accent_hex or "ansiblue") if caps.color else ""
    if not caps.color:
        return {
            "rule": "dim", "rule.title": "dim", "prompt": "bold", "prompt.dim": "dim", "placeholder": "dim",
            "dim": "dim", "accent": "bold", "attention": "bold", "attention.rule": "bold", "error": "bold",
            "footer": "dim", "footer.bold": "bold", "menu.sel": "bold reverse", "menu.desc": "dim", "menu.name": "",
            "status.glyph": "bold", "status.glyph.dim": "dim", "shimmer.0": "dim", "shimmer.1": "", "shimmer.2": "bold",
            "tail": "", "tail.code": "bold", "chip": "reverse", "mode": "bold", "sel": "bold reverse", "key": "dim",
            "title": "bold", "search": "reverse", "bottom-toolbar": "noreverse",
            "agent.echo": "bold", "agent.scout": "bold", "agent.forge": "bold", "agent.other": "bold",
        }
    return {
        "rule": "ansibrightblack", "rule.title": "ansibrightblack", "prompt": f"bold {accent}", "prompt.dim": "dim",
        "placeholder": "dim", "dim": "dim", "accent": f"bold {accent}", "attention": "bold ansiyellow",
        "attention.rule": "bold ansiyellow", "error": "bold ansired", "footer": "dim", "footer.bold": "bold",
        "menu.sel": f"bold {accent}", "menu.desc": "dim", "menu.name": "", "status.glyph": f"bold {accent}",
        "status.glyph.dim": "dim", "shimmer.0": "dim", "shimmer.1": "", "shimmer.2": "bold", "tail": "",
        "tail.code": "bold ansicyan",   # matches markdown.code in scrollback, so committing doesn't change the look
        "chip": f"reverse {accent}", "mode": accent, "sel": f"bold {accent}", "key": "dim", "title": "bold",
        "search": "reverse", "bottom-toolbar": "noreverse",
        # Who answers, as in scrollback (rich_styles): Echo plain, Jarvis blue, Glados magenta.
        "agent.echo": "bold", "agent.scout": "bold ansiblue", "agent.forge": "bold ansimagenta", "agent.other": "bold",
    }


def agent_role(agent_id: str) -> str:
    """Echo = default fg bold; Jarvis (scout) blue; Glados (forge) magenta; others a stable hash."""
    if agent_id in {"echo", "chat", "code", ""}:
        return "agent.echo"
    if agent_id == "scout":
        return "agent.scout"
    if agent_id == "forge":
        return "agent.forge"
    return "agent.other"


_OTHER = ("cyan", "green", "yellow", "blue")


def agent_color(agent_id: str, caps: Caps) -> str:
    role = agent_role(agent_id)
    if role != "agent.other" or not caps.color:
        return rich_styles(caps)[role]
    total = sum(ord(c) for c in agent_id)
    return "bold " + _OTHER[total % len(_OTHER)]


def mix(a: str, b: str, t: float) -> str:
    """#rrggbb blended from a toward b by t (0..1)."""
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(ca, cb))


def diff_tints(caps: Caps) -> tuple[str, str]:
    """(added bg, removed bg): lighter and darker than the theme background, so they read on any hue."""
    if not (caps.diff_tint and caps.color and caps.truecolor and _HEX.match(caps.bg_hex or "")):
        return "", ""
    fg = caps.fg_hex if _HEX.match(caps.fg_hex or "") else "#ffffff"
    return mix(caps.bg_hex, fg, 0.10), mix(caps.bg_hex, "#000000", 0.35)
