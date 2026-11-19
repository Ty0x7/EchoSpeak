"""The full-screen session browser: ← twice on an empty input (cli-design.md §11).

The first ← opens the quick picker in the composer's place (sessions.py). The second opens this: a
dedicated page on the terminal's alternate screen, like Codex's resume picker (0.170, studied in its real
terminal UI): a title, the filters, a search line, a dense list, a position rule and key hints. Codex shows a
session's details inline with ^e; here they are always in a pane on the right: who answers, the project
and its folder, the state, when it was created and last active, the model, and the last exchange. Below 96
columns the details move under the list. Leaving restores the conversation and its scrollback untouched.

``render(browser, width, height, caps, now)`` is pure (rows in, lines out); ``run(...)`` is the full-screen
prompt_toolkit app around it, opened from the main app inside ``in_terminal``.
"""
from __future__ import annotations

import os
import time
from typing import Any, Optional

from ..render import one_line
from . import motion as M
from .sessions import (SCOPE_LABEL, STATUS_LABEL, SessionBrowser, agent_class, ago, plain_recap, project_name,
                       who)
from .theme import Caps

Fragments = list[tuple[str, str]]
TWO_PANES = 96          # from this width the details sit beside the list
HEAD, FOOT = 4, 3       # title, filters, search, blank · rule, two hint lines


def _cell(text: str, width: int) -> str:
    if width <= 0:
        return ""
    text = M.truncate(text, width)
    return text + " " * max(0, width - M.width_of(text))


def _fit(frags: Fragments, width: int) -> Fragments:
    """Cut a line to ``width`` cells and pad it to exactly that."""
    out: Fragments = []
    used = 0
    for style, text in frags:
        if used >= width:
            break
        room = width - used
        if M.width_of(text) > room:
            text = M.truncate(text, room, "…" if room > 1 else "")
        out.append((style, text))
        used += M.width_of(text)
    if used < width:
        out.append(("", " " * (width - used)))
    return out


def _wrap(text: str, width: int, limit: int) -> list[str]:
    words, lines, line = str(text or "").split(), [], ""
    for word in words:
        if M.width_of(word) > width:
            word = M.truncate(word, width)
        candidate = f"{line} {word}".strip()
        if M.width_of(candidate) <= width:
            line = candidate
            continue
        lines.append(line)
        line = word
        if len(lines) >= limit:
            break
    if line and len(lines) < limit:
        lines.append(line)
    if len(lines) == limit and len(" ".join(lines)) < len(" ".join(words)):
        lines[-1] = M.truncate(lines[-1] + " …", width)
    return lines


def _when(ts: Any, now: float) -> str:
    try:
        t = float(ts)
    except (TypeError, ValueError):
        return ""
    if not t:
        return ""
    stamp = time.strftime("%b %d %H:%M", time.localtime(t)).replace(" 0", " ")
    return f"{ago(t, now)} · {stamp}"


def _home(path: str) -> str:
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if home and path.startswith(home) else path


# ── the list ─────────────────────────────────────────────────────────
def _list_lines(b: SessionBrowser, width: int, rows: int, caps: Caps, now: float) -> list[Fragments]:
    g = caps.glyphs
    visible = b.visible()
    if not visible:
        what = ("loading conversations…" if b.loading and not b.rows else b.error if b.error and not b.rows else
                f"no conversations match “{b.query}”" if b.query else "no conversations yet")
        return [_fit([("class:dim", "  " + what)], width)] + [[("", " " * width)]] * (rows - 1)
    agent_w = min(12, max(M.width_of(who(r)) for r in visible))
    age_w = max(M.width_of(ago(r.get("last_active_at"), now)) for r in visible)
    title_w = max(4, width - 4 - 2 - agent_w - 2 - age_w - 1)
    body: list[tuple[Fragments, Optional[int]]] = []
    for gi, (name, idx) in enumerate(b.groups(now)):
        if gi:
            body.append(([("", " " * width)], None))
        style = "class:attention" if name == "Needs you" else "class:dim"
        body.append((_fit([("", " "), (style, name)], width), None))
        for i in idx:
            row = visible[i]
            sel = i == b.selected
            status = str(row.get("status") or "")
            glyph, gstyle = {"needs_answer": (g.attention, "class:attention"), "needs_approval": (g.attention, "class:attention"),
                             "running": (g.task_doing, "class:accent"), "failed": (g.fail, "class:error")
                             }.get(status, (g.bullet, agent_class(row)))
            base = "class:sel" if sel else ""
            line: Fragments = [(base, f" {g.select} " if sel else "   "), (gstyle, glyph + " "),
                               (base, _cell(one_line(row.get("title") or "(untitled)", 200), title_w)), ("", "  "),
                               (base if sel else agent_class(row), _cell(who(row), agent_w)), ("", "  "),
                               ("class:sel" if sel else "class:dim", ago(row.get("last_active_at"), now).rjust(age_w))]
            body.append((_fit(line, width), i))
    sel_at = next((n for n, (_l, i) in enumerate(body) if i == b.selected), 0)
    top = 0
    if len(body) > rows:
        top = max(0, min(sel_at - rows // 3, len(body) - rows))
        if sel_at > 0 and body[sel_at - 1][1] is None:     # keep the group header above the first row of a group
            top = min(top, sel_at - 1)
    shown = [line for line, _i in body[top:top + rows]]
    return shown + [[("", " " * width)]] * (rows - len(shown))


# ── the details ──────────────────────────────────────────────────────
def _detail_lines(b: SessionBrowser, width: int, rows: int, caps: Caps, now: float) -> list[Fragments]:
    g = caps.glyphs
    row = b.chosen()
    blank: Fragments = [("", " " * width)]
    if row is None or rows <= 0:
        return [blank] * max(0, rows)
    if rows <= 6:
        # Short: the title, one line of facts, then the last exchange.
        status = str(row.get("status") or "idle")
        facts = f" {g.sep} ".join(x for x in (who(row), project_name(row),
                                              STATUS_LABEL.get(status, "") if status not in {"", "idle"} else "",
                                              ago(row.get("last_active_at"), now)) if x)
        short = [_fit([("", " "), ("class:title", one_line(row.get("title") or "(untitled)", 200))], width),
                 _fit([("class:dim", " " + facts)], width)]
        asked, said = one_line(row.get("last_request"), 300), plain_recap(one_line(row.get("preview"), 300))
        if asked:
            short.append(_fit([("class:dim", f" {g.prompt}  "), ("", asked)], width))
        if said:
            short.append(_fit([(agent_class(row), f" {g.bullet}  "), ("class:dim", said)], width))
        return (short + [blank] * rows)[:rows]
    out: list[Fragments] = []
    for i, line in enumerate(_wrap(one_line(row.get("title") or "(untitled)", 300), width - 1, 2)):
        out.append(_fit([("", " "), ("class:title", line)], width))
    here = bool(b.current_id) and row.get("thread_id") == b.current_id
    if here:
        out.append(_fit([("", " "), ("class:accent", "you are in this conversation")], width))
    out.append(blank)
    key_w = 10
    agents = row.get("agents") or []
    agent = who(row) + (f" · {', '.join(one_line(a.get('name'), 20) for a in agents)}"
                        if row.get("room_name") and agents else "")
    project = row.get("project") or {}
    status = str(row.get("status") or "idle")
    facts = [
        ("Agent", agent, agent_class(row)),
        ("Project", " · ".join(x for x in (project_name(row), _home(str(project.get("root") or ""))) if x) or "none", ""),
        ("Status", STATUS_LABEL.get(status, "idle" if status in {"", "idle"} else status),
         "class:attention" if status in {"needs_answer", "needs_approval"} else "class:error" if status == "failed" else ""),
        ("Updated", _when(row.get("last_active_at"), now), ""),
        ("Created", _when(row.get("created_at"), now), ""),
        ("Messages", str(row.get("message_count") or 0), ""),
        ("Model", one_line(row.get("model") or "", 60), ""),
        ("Session", str(row.get("thread_id") or "")[:8], "class:dim"),
    ]
    for key, value, style in facts:
        if value:
            out.append(_fit([("class:dim", " " + key.ljust(key_w)), (style, value)], width))
    asked, said = one_line(row.get("last_request"), 600), plain_recap(one_line(row.get("preview"), 1200))
    if asked or said:
        out.append(blank)
        out.append(_fit([("class:dim", " Last exchange")], width))
        room = max(1, rows - len(out))
        ask_lines = _wrap(asked, width - 4, max(1, min(3, room // 3))) if asked else []
        for i, line in enumerate(ask_lines):
            out.append(_fit([("class:dim", f" {g.prompt}  " if i == 0 else "    "), ("", line)], width))
        room = max(0, rows - len(out))
        for i, line in enumerate(_wrap(said, width - 4, room) if said else []):
            out.append(_fit([(agent_class(row) if i == 0 else "", f" {g.bullet}  " if i == 0 else "    "),
                             ("class:dim", line)], width))
    return (out + [blank] * rows)[:rows]


# ── the page ─────────────────────────────────────────────────────────
def _hints(width: int, b: SessionBrowser, caps: Caps) -> list[Fragments]:
    g = caps.glyphs
    back = "esc clear search" if b.query else "esc back"
    forms = [
        [f"enter resume   ctrl+n new   {back}   {g.arrow_up_down} browse   pgup/pgdn page",
         "tab filter   type to search   ctrl+c close sessions"],
        [f"enter resume   ctrl+n new   {back}", f"{g.arrow_up_down} browse   tab filter   type to search"],
        ["enter resume   esc back", f"{g.arrow_up_down} browse   tab filter"],
        ["enter · esc", ""],
    ]
    pick = next((f for f in forms if all(M.width_of(x) + 1 <= width for x in f)), ["", ""])
    return [_fit([("class:dim", " " + x if x else "")], width) for x in pick]


def render(b: SessionBrowser, width: int, height: int, caps: Caps, now: Optional[float] = None) -> list[Fragments]:
    """The whole page: exactly ``height`` lines of exactly ``width`` cells."""
    now = time.time() if now is None else now
    width, height = max(1, width), max(1, height)
    g = caps.glyphs
    count = len(b.visible())
    agents = sorted({who(r) for r in b.rows})
    right = f"{len(b.rows)} conversations" + (f" {g.sep} {len(agents)} agents" if len(agents) > 1 else "")
    title: Fragments = [("", " "), ("class:title", "Sessions"), ("class:dim", f"  {g.sep}  pick up any conversation")]
    pad = width - M.frag_width(title) - M.width_of(right) - 1
    head = [_fit(title + ([("", " " * pad), ("class:dim", right)] if pad >= 2 else []), width)]
    filt: Fragments = [("class:dim", " Filter: ")]
    for scope in b.scopes():
        filt += [("", "  "), ("class:sel" if scope == b.scope else "class:dim", SCOPE_LABEL[scope])]
    sort = [("class:dim", "     Sort: most recent")] if M.frag_width(filt) + 22 <= width else []
    head.append(_fit(filt + sort, width))
    head.append(_fit([("", " Search: "), ("", b.query), ("class:accent", "▏")] if b.query else
                     [("class:dim", " Type to search")], width))
    head.append([("", " " * width)])
    if height < HEAD + FOOT + 3:
        # A tiny terminal: the list alone.
        return (_list_lines(b, width, height, caps, now))[:height]
    body_h = height - HEAD - FOOT
    if width >= TWO_PANES:
        left_w = max(44, min(72, int(width * 0.46)))
        right_w = width - left_w - 3
        left = _list_lines(b, left_w, body_h, caps, now)
        detail = _detail_lines(b, right_w, body_h, caps, now)
        body = [l + [("class:rule", " │ ")] + r for l, r in zip(left, detail)]
    else:
        # The list takes what it needs (at least 3 rows); the details fill the rest when 3 or more rows are left.
        groups = b.groups(now)
        needed = sum(1 + len(idx) for _n, idx in groups) + max(0, len(groups) - 1) if groups else 1
        list_h = max(min(3, body_h), min(needed, body_h - 4))
        detail_h = body_h - list_h - 1
        if detail_h < 3:
            list_h, detail_h = body_h, 0
        body = _list_lines(b, width, list_h, caps, now)
        if detail_h:
            body.append([("class:rule", g.rule * width)])
            body += _detail_lines(b, width, detail_h, caps, now)
    pos = f" {b.selected + 1 if count else 0} / {count} "
    rule = [("class:rule", g.rule * max(0, width - M.width_of(pos) - 1)), ("class:dim", pos), ("class:rule", g.rule)]
    foot = [_fit(rule, width)] + _hints(width, b, caps)
    return (head + body + foot)[:height]


# ── the app ──────────────────────────────────────────────────────────
async def run(browser: SessionBrowser, caps: Caps, style: Any, now: Any = time.time) -> tuple[str, Optional[dict]]:
    """The page on the alternate screen until a choice: ("resume", row) · ("new", None) · ("back", None) · ("close", None)."""
    from prompt_toolkit.application import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.layout import Layout, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.output.color_depth import ColorDepth

    holder: dict[str, Any] = {}

    def text():
        app = holder["app"]
        size = app.output.get_size()
        lines = render(browser, size.columns, size.rows, caps, now())
        out: Fragments = []
        for i, line in enumerate(lines):
            if i:
                out.append(("", "\n"))
            out += line
        return out

    kb = KeyBindings()

    def page() -> int:
        return max(1, holder["app"].output.get_size().rows - HEAD - FOOT - 2)

    @kb.add("up")
    @kb.add("c-p")
    def _up(e):
        browser.move_page(-1, now())

    @kb.add("down")
    def _down(e):
        browser.move_page(1, now())

    @kb.add("pageup")
    def _pgup(e):
        browser.move_page(-page(), now())

    @kb.add("pagedown")
    def _pgdn(e):
        browser.move_page(page(), now())

    @kb.add("home")
    def _home_key(e):
        browser.jump_page(False, now())

    @kb.add("end")
    def _end(e):
        browser.jump_page(True, now())

    @kb.add("tab")
    def _tab(e):
        browser.cycle_scope(1)

    @kb.add("s-tab")
    def _stab(e):
        browser.cycle_scope(-1)

    @kb.add("backspace")
    def _bs(e):
        browser.backspace()

    @kb.add("enter")
    def _enter(e):
        row = browser.chosen()
        if row is not None:
            e.app.exit(result=("resume", row))

    @kb.add("c-n")
    def _new(e):
        e.app.exit(result=("new", None))

    @kb.add("escape", eager=True)
    def _esc(e):
        if not browser.clear_query():
            e.app.exit(result=("back", None))

    @kb.add("right")
    def _right(e):
        e.app.exit(result=("back", None))

    @kb.add("left")
    def _left(e):
        pass        # nowhere deeper than this page

    @kb.add("c-c")
    @kb.add("c-d")
    def _close(e):
        e.app.exit(result=("close", None))

    @kb.add(Keys.Any)
    def _type(e):
        if e.data and e.data.isprintable():
            browser.type(e.data)

    app = Application(layout=Layout(Window(FormattedTextControl(text, show_cursor=False))), key_bindings=kb,
                      full_screen=True, style=style, mouse_support=False, include_default_pygments_style=False,
                      color_depth=None if caps.color else ColorDepth.DEPTH_1_BIT)
    app.ttimeoutlen = 0.05
    app.timeoutlen = 0.08
    holder["app"] = app
    browser.mode = "page"
    if browser.selected not in browser.page_order(now()):
        browser.jump_page(False, now())
    result = await app.run_async()
    return result or ("back", None)
