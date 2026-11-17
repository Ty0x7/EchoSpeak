"""The session manager: browse every conversation and switch to one (cli-design.md §7).

Opened with the left arrow on an empty input (or /sessions). It takes the composer's place in the
live region, like a dialog, and returns to the conversation you were in with → or Esc.

The interaction follows Codex CLI's resume picker (0.169: a list ordered by activity, filters
chosen with the keyboard, type to search, Enter resumes, a position counter), adapted to
EchoSpeak: a conversation here belongs to an agent and may have a project, a running task or a
question waiting, so each row says so, and the filters are about who you were talking to.

This module is the model and its drawing: rows in, lines out, no I/O. ``app.py`` loads the rows
from ``GET /sessions/overview`` (falling back to ``/threads`` on an older backend), sends the
keys here, and performs the switch.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from ..render import one_line
from . import layout as L
from . import motion as M
from .theme import Caps

Fragments = list[tuple[str, str]]

SCOPE_LABEL = {"all": "All", "echo": "Echo", "agents": "Agents", "project": "This project"}
STATUS_LABEL = {
    "running": "running",
    "needs_answer": "needs your answer",
    "needs_approval": "needs approval",
    "failed": "last run failed",
}
STATUS_SHORT = {"running": "running", "needs_answer": "answer?", "needs_approval": "approve?", "failed": "failed"}
# Rows the frame needs beyond the list: the rule, the filter line and the key hint.
CHROME = 3
PREVIEW_ROWS = 3        # a blank line and two lines of the selected conversation
MIN_LIST = 3


def ago(ts, now: Optional[float] = None) -> str:
    try:
        sec = max(0.0, (time.time() if now is None else now) - float(ts))
    except (TypeError, ValueError):
        return ""
    if not ts:
        return ""
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if sec >= size:
            return f"{int(sec // size)}{unit} ago"
    return "just now"


def from_thread(thread: dict) -> dict:
    """A row from the older ``/threads`` list: what it can say, with the rest left blank."""
    environment = str(thread.get("environment") or "chat")
    return {**thread, "kind": "agent" if environment == "agents" else "echo",
            "agents": [] if environment == "agents" else [{"id": "echo", "name": "Echo"}],
            "project": None, "status": "idle", "preview": "", "last_request": "", "model": "", "room_name": ""}


def who(row: dict) -> str:
    """Who answers in this conversation: "Echo", "Jarvis", or "Jarvis, Glados" for a group."""
    if row.get("room_name"):
        return one_line(row["room_name"], 30)
    names = [one_line(a.get("name"), 20) for a in row.get("agents") or [] if a.get("name")]
    return ", ".join(names) or ("Agent" if row.get("kind") != "echo" else "Echo")


def project_name(row: dict) -> str:
    return one_line((row.get("project") or {}).get("name") or "", 30)


@dataclass
class SessionBrowser:
    rows: list[dict] = field(default_factory=list)
    current_id: str = ""            # the conversation you came from
    project_id: str = ""            # its project, for the "This project" filter
    scope: str = "all"
    query: str = ""
    selected: int = 0               # index into visible()
    top: int = 0                    # first list row shown
    loading: bool = True
    error: str = ""

    # ── what is listed ────────────────────────────────────────────────
    def scopes(self) -> tuple[str, ...]:
        """The filters that would show something different: no "This project" without a project."""
        return ("all", "echo", "agents") + (("project",) if self.project_id else ())

    def _in_scope(self, row: dict) -> bool:
        if self.scope == "echo":
            return row.get("kind") == "echo"
        if self.scope == "agents":
            return row.get("kind") in {"agent", "group"}
        if self.scope == "project":
            return bool(self.project_id) and str(row.get("project_id") or "") == self.project_id
        return True

    def _matches(self, row: dict) -> bool:
        if not self.query:
            return True
        members = " ".join(str(a.get("name") or "") for a in row.get("agents") or [])   # a group is found by who is in it
        hay = " ".join([str(row.get("title") or ""), who(row), members, project_name(row), str(row.get("last_request") or ""),
                        str(row.get("preview") or ""), STATUS_LABEL.get(str(row.get("status")), "")]).casefold()
        return all(word in hay for word in self.query.casefold().split())

    def _named(self, row: dict) -> bool:
        """The search matches what the conversation is called: its title, who answers, its project."""
        members = " ".join(str(a.get("name") or "") for a in row.get("agents") or [])
        hay = " ".join([str(row.get("title") or ""), who(row), members, project_name(row)]).casefold()
        return all(word in hay for word in self.query.casefold().split())

    def visible(self) -> list[dict]:
        rows = [row for row in self.rows if self._in_scope(row) and self._matches(row)]
        if self.query:
            # A conversation called what you typed comes before one that only mentions it in its last reply
            # (stable: each group keeps its activity order).
            rows.sort(key=lambda row: not self._named(row))
        return rows

    def chosen(self) -> Optional[dict]:
        rows = self.visible()
        return rows[self.selected] if 0 <= self.selected < len(rows) else None

    # ── loading ───────────────────────────────────────────────────────
    def load(self, rows: list[dict]) -> None:
        """New rows from the backend; the highlight stays on the same conversation when it still exists."""
        keep = (self.chosen() or {}).get("thread_id") if not self.loading else self.current_id
        self.rows, self.loading, self.error = list(rows), False, ""
        self._select_id(keep or self.current_id)

    def _select_id(self, thread_id: str) -> None:
        rows = self.visible()
        self.selected = next((i for i, row in enumerate(rows) if row.get("thread_id") == thread_id), 0)
        self.top = 0

    # ── keys ──────────────────────────────────────────────────────────
    def move(self, delta: int) -> None:
        count = len(self.visible())
        if count:
            self.selected = max(0, min(count - 1, self.selected + delta))

    def jump(self, end: bool) -> None:
        self.selected = max(0, len(self.visible()) - 1) if end else 0

    def cycle_scope(self, step: int = 1) -> None:
        scopes = self.scopes()
        keep = (self.chosen() or {}).get("thread_id")
        self.scope = scopes[(scopes.index(self.scope) + step) % len(scopes)] if self.scope in scopes else "all"
        self._select_id(keep or "")

    def type(self, text: str) -> None:
        self.query += text
        self.selected, self.top = 0, 0

    def backspace(self) -> bool:
        if not self.query:
            return False
        self.query = self.query[:-1]
        self.selected, self.top = 0, 0
        return True

    def clear_query(self) -> bool:
        """Esc's first press when a search is typed; False means there was nothing to clear."""
        if not self.query:
            return False
        self.query, self.selected, self.top = "", 0, 0
        return True

    # ── drawing ───────────────────────────────────────────────────────
    def natural_rows(self, height: int) -> int:
        """Rows the manager would like at this terminal height: the whole list, up to the screen."""
        return max(CHROME + 1, min(L.budget(height) - 1, CHROME + PREVIEW_ROWS + max(MIN_LIST, len(self.visible()))))

    @staticmethod
    def minimum_rows() -> int:
        return CHROME + 1

    def _window(self, count: int, room: int) -> int:
        """Scroll so the highlight is on screen; returns the first row to show."""
        room = max(1, room)
        if self.selected < self.top:
            self.top = self.selected
        elif self.selected >= self.top + room:
            self.top = self.selected - room + 1
        self.top = max(0, min(self.top, max(0, count - room)))
        return self.top

    def lines(self, width: int, rows: int, caps: Caps, now: Optional[float] = None) -> list[Fragments]:
        """The manager drawn into at most ``rows`` lines of at most ``width`` cells."""
        width, rows = max(1, width), max(1, rows)
        visible = self.visible()
        count = len(visible)
        position = f"{self.selected + 1 if count else 0} / {count}"
        out: list[Fragments] = []
        title = f"Sessions {caps.glyphs.sep} {position}" if width >= L.MICRO else position
        if rows >= 3:
            out.append(M.rule(width, title, caps, title_style="class:title"))
        if rows >= 4:
            out.append(self._filter_line(width, caps))
        hint = self._hint(width, caps) if rows >= 2 else None
        room = rows - len(out) - (1 if hint is not None else 0)
        preview = self._preview(width, caps) if room >= MIN_LIST + PREVIEW_ROWS and width >= L.NARROW else []
        room -= len(preview)
        if self.loading and not self.rows:
            out.append([("class:dim", M.truncate("  loading conversations…", width))])
        elif self.error and not self.rows:
            out.append([("class:error", M.truncate(f"  {self.error}", width))])
        elif not count:
            what = f"no conversations match “{self.query}”" if self.query else "no conversations here yet"
            out.append([("class:dim", M.truncate(f"  {what}", width))])
        else:
            first = self._window(count, room)
            for index in range(first, min(count, first + max(1, room))):
                out.append(self._row(visible[index], index == self.selected, width, caps, now))
        out += preview
        if hint is not None:
            out.append(hint)
        return out[:rows]

    def _filter_line(self, width: int, caps: Caps) -> Fragments:
        sep = f" {caps.glyphs.sep} "
        left: Fragments = [("", " ")]
        for index, scope in enumerate(self.scopes()):
            if index:
                left.append(("class:dim", sep))
            left.append(("class:sel" if scope == self.scope else "class:dim", SCOPE_LABEL[scope]))
        search = f"search: {self.query}" if self.query else "type to search"
        style = "" if self.query else "class:dim"
        if M.frag_width(left) + 2 + M.width_of(search) > width:
            # Not both: what you typed matters more than the list of filters.
            if self.query:
                return [("", M.truncate(" " + search, width))]
            current: Fragments = [("", " "), ("class:sel", SCOPE_LABEL.get(self.scope, "All"))]
            return current if M.frag_width(current) <= width else [("class:sel", M.truncate(SCOPE_LABEL.get(self.scope, "All"), width))]
        pad = width - M.frag_width(left) - M.width_of(search) - 1
        return left + [("", " " * max(2, pad)), (style, search)]

    def _row(self, row: dict, selected: bool, width: int, caps: Caps, now: Optional[float]) -> Fragments:
        g = caps.glyphs
        mark = f" {g.select} " if selected else "   "
        current = row.get("thread_id") == self.current_id and bool(self.current_id)
        status = str(row.get("status") or "idle")
        tier = L.tier(width)
        when = ago(row.get("last_active_at"), now)
        title = one_line(row.get("title") or "(untitled)", 120)
        agent, project = who(row), project_name(row)
        state = "here" if current else (STATUS_LABEL if tier == "wide" else STATUS_SHORT).get(status, "")
        base = "class:sel" if selected else ""
        dim = "class:sel" if selected else "class:dim"
        alert = "class:attention" if status in {"needs_answer", "needs_approval"} else "class:error" if status == "failed" else dim
        if tier == "micro":
            flag = "!" if status in {"needs_answer", "needs_approval"} else "*" if current else ""
            return [(base, M.truncate(mark.rstrip(" ") + " " + title, max(1, width - len(flag)))), (alert, flag)]
        when_w, agent_w = 9, (14 if tier != "narrow" else 10)
        project_w = 16 if tier == "wide" else 12 if tier == "regular" else 0
        state_w = max(M.width_of(s) for s in (STATUS_LABEL if tier == "wide" else STATUS_SHORT).values()) + 1
        if tier == "narrow":
            state_w = 9
        fixed = M.width_of(mark) + when_w + agent_w + (project_w + 1 if project_w else 0) + state_w + 2
        title_w = width - fixed
        if title_w < 12:
            project_w, fixed = 0, fixed - (project_w + 1 if project_w else 0)
            title_w = width - fixed
        if title_w < 8:
            return [(base, M.truncate(mark + title, width))]
        out: Fragments = [(base, mark), (dim, _cell(when, when_w)), (base, _cell(title, title_w)), ("", " "),
                          (base if row.get("kind") != "echo" else dim, _cell(agent, agent_w))]
        if project_w:
            out += [("", " "), (dim, _cell(project, project_w))]
        out += [("", " "), ("class:sel" if current and selected else "class:accent" if current else alert, _cell(state, state_w))]
        return out

    def _preview(self, width: int, caps: Caps) -> list[Fragments]:
        """The selected conversation's last exchange, so you know what you are returning to."""
        row = self.chosen()
        if row is None:
            return []
        asked, said = one_line(row.get("last_request"), 400), one_line(row.get("preview"), 400)
        if not asked and not said:
            return []
        g = caps.glyphs
        lines: list[Fragments] = [[]]
        lines.append([("class:dim", M.truncate(f"   {g.prompt} {asked}" if asked else "", width))])
        model = f"  ({one_line(row.get('model'), 30)})" if row.get("model") and width >= L.WIDE else ""
        lines.append([("class:dim", M.truncate(f"   {g.bullet} {said}" if said else "", max(1, width - M.width_of(model)))),
                      ("class:dim", model)])
        return lines

    def _hint(self, width: int, caps: Caps) -> Fragments:
        g = caps.glyphs
        sep = f" {g.sep} "
        clear = "esc clear search" if self.query else "→ or esc back"
        forms = [
            sep.join([f"{g.arrow_up_down} browse", "enter resume", "tab filter", "type to search", clear, "ctrl+n new"]),
            sep.join([f"{g.arrow_up_down} browse", "enter resume", "tab filter", clear]),
            sep.join(["enter resume", "esc back"]),
            "enter" + sep + "esc",
        ]
        text = next((form for form in forms if M.width_of(form) + 1 <= width), "")
        return [("class:dim", " " + text if text else "")]


def _cell(text: str, width: int) -> str:
    """``text`` cut and padded to exactly ``width`` cells."""
    text = M.truncate(text, max(1, width))
    return text + " " * max(0, width - M.width_of(text))
