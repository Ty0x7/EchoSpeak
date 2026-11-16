"""`echospeak preview --follow <session>`: a read-only change viewer for a tmux companion pane.

``/preview`` in the TUI opens it beside the conversation (``tmux split-window -h``); it can also run
on its own. It follows the session's run journal (``GET /query/runs?thread_id=`` and
``/query/runs/{id}/events``) for ``file_change`` records: applied ones on ``tool_end.meta``, proposed
ones on ``approval_request``. This pane's scrollback is disposable, so the viewer may use the
alternate screen; the conversation pane keeps true scrollback.

It never approves, denies or changes anything. Keys: j/k or ↑/↓ scroll, pgup/pgdn page, n/p next or
previous file, f follow the newest edit again, s split/unified, q quit.
"""
from __future__ import annotations

import asyncio
import os
import queue
import threading
from dataclasses import dataclass
from typing import Callable

from rich.text import Text

from ..client import ApiError, Client, ConnectError, follow
from ..render import one_line
from . import diffview as DV
from .theme import Caps

MAX_FILE_ROWS = 5


# ── the change feed (journal → events) ───────────────────────────────
@dataclass
class Entry:
    change: DV.Change
    turn: int
    status: str                 # proposed | applied | denied
    approval_id: str = ""
    order: int = 0


@dataclass
class FileRow:
    display: str
    added: int = 0
    removed: int = 0
    turn: int = 0
    proposed: bool = False
    latest: int = 0


class Model:
    """Everything the viewer shows, fed one journal event at a time. Pure: no I/O, no clock."""

    def __init__(self) -> None:
        self.entries: list[Entry] = []
        self.turns: dict[str, int] = {}
        self.seen: set[tuple[str, int]] = set()
        self.selected = ""             # display path of the selected file
        self.following = True          # selection moves to the newest edit
        self.split = False
        self.scroll = 0
        self.notice = ""
        self.title = ""
        self.status = "connecting"
        self._order = 0

    def turn_of(self, run_id: str) -> int:
        if run_id not in self.turns:
            self.turns[run_id] = len(self.turns) + 1
        return self.turns[run_id]

    def feed(self, run_id: str, evt: dict) -> bool:
        """Apply one event; True when something visible changed."""
        if not isinstance(evt, dict):
            return False
        try:
            seq = int(evt.get("_replay_seq") or 0)
        except (TypeError, ValueError):
            seq = 0
        if seq and (run_id, seq) in self.seen:
            return False
        if seq:
            self.seen.add((run_id, seq))
        kind = str(evt.get("type") or "")
        if kind == "_run":                   # the feed names runs oldest first, so turns number in order
            self.turn_of(run_id)
            return False
        if kind == "approval_request":
            change = DV.parse(evt.get("file_change"))
            if change is None:
                return False
            change.status = "proposed"
            self._add(Entry(change, self.turn_of(run_id), "proposed", str(evt.get("id") or "")), seq)
            return True
        if kind == "approval_resolved":
            aid = str(evt.get("id") or "")
            decision = str(evt.get("decision") or "")
            hit = False
            for e in list(self.entries):
                if e.approval_id == aid and e.status == "proposed":
                    hit = True
                    if decision in {"allow", "always", "persist"}:
                        self.entries.remove(e)     # the applied change arrives with tool_end
                    else:
                        e.status = "denied"
            return hit
        if kind == "tool_end":
            meta = evt.get("meta") if isinstance(evt.get("meta"), dict) else {}
            change = DV.parse(meta.get("file_change"))
            if change is None or not evt.get("ok"):
                return False
            self.entries = [e for e in self.entries if not (e.status == "proposed" and change.tool_call_id
                                                             and e.change.tool_call_id == change.tool_call_id)]
            self._add(Entry(change, self.turn_of(run_id), "applied"), seq)
            return True
        return False

    def _add(self, entry: Entry, seq: int = 0) -> None:
        # Runs are read in parallel: order by (turn, event seq), not by arrival.
        self._order += 1
        entry.order = entry.turn * 1_000_000 + (seq or self._order)
        newest = all(entry.order >= e.order for e in self.entries)
        self.entries.append(entry)
        if (self.following and newest) or not self.selected:
            if self.selected != entry.change.display:
                self.scroll = 0
            self.selected = entry.change.display

    # ── queries ──────────────────────────────────────────────────────
    def files(self) -> list[FileRow]:
        rows: dict[str, FileRow] = {}
        for e in self.entries:
            row = rows.setdefault(e.change.display, FileRow(e.change.display))
            if e.status == "applied":
                row.added += e.change.added
                row.removed += e.change.removed
            row.turn = max(row.turn, e.turn)
            row.latest = max(row.latest, e.order)
            row.proposed = e.status == "proposed" if e.order >= row.latest else row.proposed
        applied = {e.change.display for e in self.entries if e.status == "applied"}
        for row in rows.values():
            if row.display not in applied:   # only a proposal so far: show what it would change
                latest = max((e for e in self.entries if e.change.display == row.display), key=lambda e: e.order)
                row.added, row.removed = latest.change.added, latest.change.removed
        return sorted(rows.values(), key=lambda r: -r.latest)

    def totals(self) -> tuple[int, int, int]:
        """(files, lines added, lines removed); proposals count as files but not as changed lines."""
        applied = [e.change for e in self.entries if e.status == "applied"]
        return len(self.files()), sum(c.added for c in applied), sum(c.removed for c in applied)

    def current(self) -> list[Entry]:
        """The selected file's changes, newest first."""
        return sorted((e for e in self.entries if e.change.display == self.selected), key=lambda e: -e.order)

    # ── keys ─────────────────────────────────────────────────────────
    def move_file(self, delta: int) -> None:
        files = self.files()
        if not files:
            return
        names = [f.display for f in files]
        i = names.index(self.selected) if self.selected in names else 0
        self.selected = names[(i + delta) % len(names)]
        self.following = False
        self.scroll = 0

    def follow_newest(self) -> None:
        self.following = True
        files = self.files()
        if files:
            self.selected = files[0].display
        self.scroll = 0

    def toggle_split(self, width: int) -> None:
        if not self.split and width < DV.SPLIT_MIN:
            self.notice = f"split needs {DV.SPLIT_MIN} columns (this pane has {width}); staying unified"
            return
        self.split = not self.split
        self.notice = ""

    # ── rendering ────────────────────────────────────────────────────
    def body_lines(self, width: int, caps: Caps) -> list[Text]:
        out: list[Text] = []
        g = caps.glyphs
        for n, e in enumerate(self.current()):
            if n:
                out.append(Text(""))
            head = Text(" ")
            tag = {"proposed": "PROPOSED", "denied": "DENIED"}.get(e.status, "")
            if tag:
                head.append(tag + " ", style="bold reverse" if e.status == "proposed" else "bold")
            head.append(DV.proposed_label(e.change) if e.status != "applied" else DV.verb(e.change), style="bold")
            tail = DV.counts(e.change, caps)
            if tail:
                head.append(f" {g.sep} ", style="dim")
                for t, s in tail:
                    head.append(t, style=s)
            head.append(f" {g.sep} T{e.turn}", style="dim")
            note = DV.withheld_note(e.change)
            if note and not e.change.hunks:
                head.append(f" {g.sep} {note}", style="dim")
            out.append(head)
            if not e.change.hunks:
                continue
            if self.split and width >= DV.SPLIT_MIN:
                out += DV.split(e.change, width - 1, caps)
            else:
                rows, _ = DV.unified(e.change, width - 1, caps, max_rows=None, indent=1, hint="")
                out += rows
        return out

    def screen(self, width: int, height: int, caps: Caps) -> list[Text]:
        """Exactly ``height`` rows: header, file list, rule, the selected file's diff (scrolled), footer."""
        g = caps.glyphs
        width = max(20, width)
        top: list[Text] = []
        head = Text(" echospeak preview", style="bold")
        head.append(f" {g.sep} {DV.truncate(one_line(self.title, 200), max(10, width - 42))}" if self.title else "", style="")
        head.append(f" {g.sep} {self.status}" + (" · following" if self.following else ""), style="dim")
        top.append(head)
        n, added, removed = self.totals()
        summary = Text(" Changes · this session", style="bold")
        if n:
            summary.append(f"   {n} file{'s' if n != 1 else ''} {g.sep} ", style="dim")
            summary.append(f"+{added}", style="bold")
            summary.append(f" {g.minus}{removed}", style="dim")
        top.append(summary)
        files = self.files()
        names = [f.display for f in files]
        sel = names.index(self.selected) if self.selected in names else 0
        first = max(0, min(sel - MAX_FILE_ROWS + 1, len(files) - MAX_FILE_ROWS)) if sel >= MAX_FILE_ROWS else 0
        name_w = max(10, min(width - 28, max((DV.width_of(x) for x in names), default=10)))
        for i, f in enumerate(files[first:first + MAX_FILE_ROWS], start=first):
            mark = g.select if i == sel else " "
            row = Text(f" {mark} ", style="bold" if i == sel else "")
            row.append(DV.truncate(f.display, name_w).ljust(name_w), style="bold" if i == sel else "")
            row.append(f"  +{f.added}".rjust(7), style="bold")
            row.append(f" {g.minus}{f.removed}".ljust(6), style="dim")
            row.append(f" T{f.turn}", style="dim")
            if f.proposed:
                row.append("  PROPOSED", style="bold")
            top.append(row)
        if len(files) > MAX_FILE_ROWS:
            top.append(Text(f"   … {len(files)} files · n/p to move", style="dim"))
        if not files:
            top.append(Text("   No file changes yet. Edits Echo makes in this session appear here.", style="dim"))
        top.append(Text(g.rule * width, style="dim"))
        if files:
            sel_change = self.current()[0].change if self.current() else None
            title = Text(" ")
            if sel_change is not None:
                title.append(DV.where(sel_change, caps), style="bold")
            mode = "split" if (self.split and width >= DV.SPLIT_MIN) else "unified"
            pad = max(1, width - title.cell_len - len(mode) - 1)
            title.append(" " * pad + mode, style="dim")
            top.append(title)
        footer_text = self.notice or f"j/k scroll {g.sep} n/p file {g.sep} f follow {g.sep} s split {g.sep} q quit"
        footer = Text(" " + DV.truncate(footer_text, width - 2), style="dim")
        key = (self.selected, width, self.split, tuple((e.order, e.status) for e in self.entries))
        if getattr(self, "_body_key", None) != key:
            self._body_key, self._body = key, self.body_lines(width, caps)   # re-lexed only when it changes
        body = self._body
        room = max(1, height - len(top) - 1)
        self.scroll = max(0, min(self.scroll, max(0, len(body) - room)))
        shown = body[self.scroll:self.scroll + room]
        shown += [Text("")] * (room - len(shown))
        return (top + shown + [footer])[:height]


# ── journal reader ───────────────────────────────────────────────────
class Feed:
    """Reads every run of a session from the journal, then keeps following the running ones."""

    def __init__(self, client: Client, session_id: str, sink: Callable[[str, dict], None]) -> None:
        self.client = client
        self.sid = session_id
        self.sink = sink
        self.started: set[str] = set()
        self.stop = threading.Event()

    def poll(self) -> None:
        runs = self.client.runs(self.sid)
        for run in sorted(runs, key=lambda r: float(r.get("updated") or 0)):
            rid = str(run.get("id") or "")
            if not rid or rid in self.started:
                continue
            self.started.add(rid)
            self.sink(rid, {"type": "_run"})
            threading.Thread(target=self._follow, args=(rid,), name=f"preview-{rid[:8]}", daemon=True).start()

    def _follow(self, rid: str) -> None:
        def on_event(evt: dict) -> None:
            if self.stop.is_set():
                raise _Stop()
            self.sink(rid, evt)

        try:
            follow(self.client, self.sid, rid, on_event=on_event, sleep=lambda s: self.stop.wait(s))
        except _Stop:
            return
        except (ApiError, ConnectError):
            self.started.discard(rid)   # try again on the next poll


class _Stop(Exception):
    pass


def collect(client: Client, session_id: str, skip_running: bool = True) -> Model:
    """Every change recorded in a session's journal, read once (``/diff``). Running runs are skipped."""
    model = Model()
    for run in sorted(client.runs(session_id), key=lambda r: float(r.get("updated") or 0)):
        rid = str(run.get("id") or "")
        if not rid:
            continue
        model.turn_of(rid)
        if skip_running and run.get("status") == "running":
            continue
        try:
            follow(client, session_id, rid, on_event=lambda e, rid=rid: model.feed(rid, e), retries=1)
        except (ApiError, ConnectError):
            continue
    return model


# ── the full-screen viewer ───────────────────────────────────────────
def run_viewer(client: Client, session_id: str, caps: Caps, poll_s: float = 1.0) -> int:
    from prompt_toolkit.application import Application
    from prompt_toolkit.formatted_text import ANSI, to_formatted_text
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import FormattedTextControl, Layout, Window
    from prompt_toolkit.output.color_depth import ColorDepth

    model = Model()
    inbox: "queue.Queue[tuple[str, dict]]" = queue.Queue()
    feed = Feed(client, session_id, lambda rid, evt: inbox.put((rid, evt)))
    try:
        thread = client.thread(session_id)
        model.title = str((thread or {}).get("title") or session_id[:8])
    except (ApiError, ConnectError):
        model.title = session_id[:8]

    def size() -> tuple[int, int]:
        try:
            s = app.output.get_size()
            return s.columns, s.rows
        except Exception:  # noqa: BLE001
            return 80, 24

    def text():
        w, h = size()
        lines = DV.to_ansi(model.screen(w, h, caps), w, caps)
        frags = []
        for i, line in enumerate(lines):
            frags += to_formatted_text(ANSI(line))
            if i < len(lines) - 1:
                frags.append(("", "\n"))
        return frags

    kb = KeyBindings()

    def page() -> int:
        return max(1, size()[1] - 12)

    @kb.add("q")
    @kb.add("c-c")
    def _quit(event) -> None:
        event.app.exit(result=0)

    @kb.add("j")
    @kb.add("down")
    def _down(event) -> None:
        model.scroll += 1

    @kb.add("k")
    @kb.add("up")
    def _up(event) -> None:
        model.scroll = max(0, model.scroll - 1)

    @kb.add("pagedown")
    @kb.add(" ")
    def _pgdn(event) -> None:
        model.scroll += page()

    @kb.add("pageup")
    def _pgup(event) -> None:
        model.scroll = max(0, model.scroll - page())

    @kb.add("g")
    def _top(event) -> None:
        model.scroll = 0

    @kb.add("G")
    def _bottom(event) -> None:
        model.scroll = 10 ** 6

    @kb.add("n")
    @kb.add("tab")
    def _next(event) -> None:
        model.move_file(1)

    @kb.add("p")
    @kb.add("s-tab")
    def _prev(event) -> None:
        model.move_file(-1)

    @kb.add("f")
    def _follow(event) -> None:
        model.follow_newest()

    @kb.add("s")
    def _split(event) -> None:
        model.toggle_split(size()[0])

    app = Application(layout=Layout(Window(FormattedTextControl(text), wrap_lines=False)), key_bindings=kb,
                      full_screen=True, mouse_support=False,
                      color_depth=None if caps.color else ColorDepth.DEPTH_1_BIT)

    async def pump() -> None:
        last_poll = 0.0
        loop = asyncio.get_running_loop()
        while True:
            now = loop.time()
            if now - last_poll >= poll_s:
                last_poll = now
                try:
                    await asyncio.to_thread(feed.poll)
                    model.status = "live"
                except (ApiError, ConnectError) as exc:
                    model.status = f"backend unreachable ({one_line(exc, 40)}); retrying"
            changed = False
            while True:
                try:
                    rid, evt = inbox.get_nowait()
                except queue.Empty:
                    break
                changed = model.feed(rid, evt) or changed
            app.invalidate()
            await asyncio.sleep(0.2)

    def pre_run() -> None:
        app.create_background_task(pump())

    try:
        return int(app.run(pre_run=pre_run) or 0)
    finally:
        feed.stop.set()


def read_key_file(path: str) -> tuple[str, str]:
    """(url, key) handed over by /preview in a private file, deleted once read."""
    import json

    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return "", ""
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    if not isinstance(data, dict):
        return "", ""
    return str(data.get("url") or ""), str(data.get("key") or "")
