"""Echo TUI: the interactive terminal (prompt_toolkit live region + rich scrollback), spec §3.

Inline, never the alternate screen. Finished output goes into real terminal scrollback; only a small
live region at the bottom (stream tail, running steps, status, pending input, menu or dialog,
composer, footer) is redrawn. Backend calls run in worker threads; the app object is only touched
from the event loop.
"""
from __future__ import annotations

import asyncio
import base64
import io
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from prompt_toolkit.application import Application
from prompt_toolkit.application.run_in_terminal import in_terminal
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition, has_focus
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.history import FileHistory, InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.key_binding.defaults import load_key_bindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.formatted_text import ANSI, to_formatted_text
from prompt_toolkit.layout import (ConditionalContainer, D, FormattedTextControl, HSplit, Layout, VSplit, Window)
from prompt_toolkit.layout.controls import BufferControl
from prompt_toolkit.layout.processors import Processor, Transformation, TransformationInput
from prompt_toolkit.output.color_depth import ColorDepth
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import SearchToolbar

from .. import control, discovery
from ..client import ApiError, Client, ConnectError, follow, new_request_id, submit
from ..render import Renderer, one_line, sanitize
from ..repl import App as Core, Out
from . import avatar as A
from . import blocks as B
from . import diffview as DV
from . import layout as L
from . import motion as M
from .commands import COMMANDS, KEYS_HELP, SHORTCUTS, filter_commands, lookup, visible
from .dialog import Action, Dialog, Option, approval_options, question_options
from .sessions import SessionBrowser, from_thread, plain_recap  # noqa: F401 (plain_recap: re-exported)
from .state import Run
from .theme import Caps

VERSION_FALLBACK = ""
PASTE_CHARS = 1000
PROMPT_W = 3            # "›  ": the composer's prompt column (the scrollback user line uses the same)
PASTE_LINES = 12
CHIP_RE = re.compile(r"\[Pasted [\d,]+ chars · \d+ lines #(\d+)\]")
# Approval with a diff (edit-preview.md §3.3): two columns from this width (and height), else stacked.
TWO_COL_MIN_W = 120
TWO_COL_MIN_H = 20
TWO_COL_LEFT = 44
STACKED_DIFF_ROWS = 10
DIFF_MARK = "__diff__"
# Messages shown when a conversation is resumed: the last exchanges, so you see where it stood.
RECAP_MESSAGES = 6



class _Abandon(Exception):
    pass


@dataclass
class PendingInput:
    kind: str          # steer | queue
    text: str
    ref: str = ""      # client_request_id for queued items


class Placeholder(Processor):
    """Dim placeholder text while the buffer is empty."""

    def __init__(self, text: Callable[[], str]) -> None:
        self.text = text

    def apply_transformation(self, ti: TransformationInput) -> Transformation:
        if ti.document.text == "" and ti.lineno == 0:
            return Transformation(list(ti.fragments) + [("class:placeholder", self.text())])
        return Transformation(ti.fragments)


class ChipHighlighter(Processor):
    """Paste chips read as one token."""

    def apply_transformation(self, ti: TransformationInput) -> Transformation:
        text = "".join(t for _s, t, *_ in ti.fragments)
        if "[Pasted " not in text:
            return Transformation(ti.fragments)
        out: StyleAndTextTuples = []
        pos = 0
        for m in CHIP_RE.finditer(text):
            if m.start() > pos:
                out.append(("", text[pos:m.start()]))
            out.append(("class:chip", m.group(0)))
            pos = m.end()
        if pos < len(text):
            out.append(("", text[pos:]))
        return Transformation(out)


def _home(path: str) -> str:
    home = str(Path.home())
    return "~" + path[len(home):] if path == home or path.startswith(home + os.sep) else path


_WORDS = re.compile(r"(\s+)")


def _wrap(text: str, width: int) -> list[str]:
    """Word-wrap plain text by display width; words longer than a line are broken."""
    width = max(1, width)
    out: list[str] = []
    for raw in text.split("\n"):
        line, used = "", 0
        for word in _WORDS.split(raw):
            if not word:
                continue
            ww = M.width_of(word)
            if used + ww <= width:
                line, used = line + word, used + ww
                continue
            if word.isspace():
                out.append(line.rstrip())
                line, used = "", 0
                continue
            if ww <= width:
                out.append(line.rstrip())
                line, used = word, ww
                continue
            for ch in word:
                cw = max(0, M.width_of(ch))
                if used + cw > width and line:
                    out.append(line.rstrip())
                    line, used = "", 0
                line, used = line + ch, used + cw
        out.append(line.rstrip())
    return out


_INLINE = re.compile(r"(\*\*[^*\n]+\*\*|`[^`\n]+`|(?<![\w*])\*[^*\s][^*\n]*\*(?![\w*]))")
_HEADING = re.compile(r"^(#{1,6})\s+")


def _inline_md(line: str, base: str = "class:tail") -> StyleAndTextTuples:
    """Light inline markdown for the streaming tail: **bold**, `code`, *italic*, # headings.

    Only closed pairs are styled; a half-streamed `**bo` stays literal until its closing marker arrives.
    """
    m = _HEADING.match(line)
    if m:
        return [(base + " bold", line[m.end():])]
    out: StyleAndTextTuples = []
    pos = 0
    for hit in _INLINE.finditer(line):
        if hit.start() > pos:
            out.append((base, line[pos:hit.start()]))
        tok = hit.group(0)
        if tok.startswith("**"):
            out.append((base + " bold", tok[2:-2]))
        elif tok.startswith("`"):
            out.append((base + " class:tail.code", tok[1:-1]))
        else:
            out.append((base + " italic", tok[1:-1]))
        pos = hit.end()
    if pos < len(line):
        out.append((base, line[pos:]))
    return out


def _wrap_frags(frags: StyleAndTextTuples, width: int) -> list[StyleAndTextTuples]:
    """Word-wrap one line of styled fragments by display width (as _wrap, keeping styles)."""
    width = max(1, width)
    lines: list[StyleAndTextTuples] = []
    cur: StyleAndTextTuples = []
    used = 0

    def push() -> None:
        nonlocal cur, used
        while cur and cur[-1][1].isspace():
            cur.pop()
        if cur:
            st, text = cur[-1][0], cur[-1][1].rstrip()
            cur[-1] = (st, text)
        lines.append(cur)
        cur, used = [], 0

    for st, text, *_ in frags:
        for word in _WORDS.split(text):
            if not word:
                continue
            ww = M.width_of(word)
            if used + ww <= width:
                cur.append((st, word))
                used += ww
            elif word.isspace():
                push()
            elif ww <= width:
                push()
                cur.append((st, word))
                used = ww
            else:
                for ch in word:
                    cw = max(0, M.width_of(ch))
                    if used + cw > width and cur:
                        push()
                    cur.append((st, ch))
                    used += cw
    push()
    return lines


def _crop(frags: StyleAndTextTuples, width: int) -> StyleAndTextTuples:
    """Cut a row of fragments to ``width`` display cells (diff rows at very small widths)."""
    out: StyleAndTextTuples = []
    used = 0
    for st, text, *rest in frags:
        room = width - used
        if room <= 0:
            break
        w = M.width_of(text)
        if w <= room:
            out.append((st, text, *rest))
            used += w
            continue
        cut, cw = "", 0
        for ch in text:
            chw = max(0, M.width_of(ch))
            if cw + chw > room:
                break
            cut += ch
            cw += chw
        out.append((st, cut, *rest))
        break
    return out


class EchoTUI:
    def __init__(self, client: Client, endpoint: discovery.Endpoint, caps: Caps, *, env=os.environ,
                 verbose: bool = False, agent: str = "", resume: str = "", new: bool = False, project: str = "",
                 continue_last: bool = False) -> None:
        self.client = client
        self.endpoint = endpoint
        self.caps = caps
        self.env = env
        self.core = Core(client, Renderer(color=False), Out(io.StringIO()), interactive=False,
                         source_hint=endpoint.source)
        self.verbose = verbose
        self.start_agent = agent
        self.start_resume = resume
        self.start_new = new
        self.start_project = project
        # A plain start is a clean one (like `claude`): no conversation is reopened or replayed until you
        # choose one. --continue (or `echospeak resume`) picks up where you left off.
        self.start_continue = continue_last
        self.last_here: Optional[dict] = None   # the latest conversation in this folder's project, offered on ←
        # The welcome banner while Echo's entrance plays in the live region; then it is printed to scrollback.
        self.welcome: Optional[B.Banner] = None
        self.welcome_t0 = 0.0
        self._after_flush: list[Callable[[], None]] = []
        self._committing_welcome = False
        self._welcome_sent = False      # committed once (printed or queued); the live copy waits for the flush
        self._welcome_cache: Optional[tuple] = None
        self._last_block: type = type(None)
        self.version = endpoint.version or VERSION_FALLBACK
        self.data_dir = ""
        # backend-derived state
        self.settings: dict = {}
        self.model = ""
        self.provider = ""
        self.approval_mode = "smart"
        self.agents: list[dict] = []
        self.agent_name = "Echo"
        self.agent_title = "Personal agent"
        self.project_name = ""
        self.features: set[str] = set()
        self.commands = COMMANDS
        # live state
        self.run: Optional[Run] = None
        self.run_gen = 0
        self.follow_abandon: Optional[threading.Event] = None
        self.pending: list[PendingInput] = []
        self.dialogs: list[Dialog] = []
        self.dialog: Optional[Dialog] = None
        # The session manager (tui/sessions.py): open in the composer's place, or None.
        self.browser: Optional[SessionBrowser] = None
        self.deferred: list[str] = []
        self.last_answer = ""
        self.session_tokens = {"prompt": 0, "completion": 0, "runs": 0}
        self.context: Optional[tuple[int, int]] = None
        self.held = 0
        self.transcript: list[B.Block] = []
        self.changes: list[DV.Change] = []     # file changes seen in this terminal (fallback for /diff)
        self.preview_pane = ""                  # tmux pane id of the /preview companion
        self.project_root = ""
        self._diff_cache: dict = {}
        self.notice = ("", 0.0)
        self.ctrl_c_at = 0.0
        self._ctrl_c_mode = ""
        self.esc_at = 0.0
        self.menu_index = 0
        self.menu_dismissed = None
        self.show_shortcuts = False
        self.typed = False
        self.chips: dict[str, str] = {}
        self.start_ms = time.monotonic() * 1000
        self.prompted: set[str] = set()
        self.exit_note = ""
        self._held_base = 0
        self._out: list[str] = []
        self._tail = "\n\n"
        self._out_event: Optional[asyncio.Event] = None
        self._events: Optional[asyncio.Queue] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._plan_cache: Optional[tuple[tuple[int, int], dict[str, int]]] = None
        self._tail_cache: Optional[tuple[tuple[str, int], list]] = None   # the tail redraws ~20×/s
        self.reflows = L.terminal_reflows(env)   # does the terminal re-wrap lines on resize?
        self.app = self._build_app()

    # ── small helpers ────────────────────────────────────────────────
    @property
    def sid(self) -> str:
        return self.core.sid

    @property
    def running(self) -> bool:
        return self.run is not None

    def width(self) -> int:
        """The terminal's real width. Every renderer copes with any width; nothing assumes a minimum."""
        try:
            return max(1, self.app.output.get_size().columns)
        except Exception:  # noqa: BLE001
            return max(1, shutil.get_terminal_size((100, 30)).columns)

    def height(self) -> int:
        try:
            return max(1, self.app.output.get_size().rows)
        except Exception:  # noqa: BLE001
            return 30

    def now(self) -> float:
        return time.monotonic()

    def say_notice(self, text: str, seconds: float = 2.5) -> None:
        self.notice = (text, self.now() + seconds)
        self.app.invalidate()

    # Prose wraps at a readable measure on very wide terminals; code, diffs, tables and tool lines
    # use the full width (layout.measure, ECHO_TUI_MEASURE).
    _PROSE = (B.MarkdownBlock, B.Note, B.ThinkingLine, B.UserLine, B.Outcome, B.AgentHeader, B.TaskBoard)

    def emit(self, *blocks: B.Block, transcript: bool = True) -> None:
        if self.welcome is not None and not self._welcome_sent and not self._committing_welcome:
            self._commit_welcome()   # nothing may be printed above the banner
        width = self.width()
        prose = L.measure(width, self.env)
        for b in blocks:
            if b is None:
                continue
            text = B.render(b, prose if isinstance(b, self._PROSE) else width, self.caps, verbose=self.verbose)
            if isinstance(b, (B.UserLine, B.Outcome)) or (isinstance(b, B.AgentHeader)):
                # One blank line before a new turn or a header, never two.
                text = ("" if self._tail.endswith("\n\n") else "\n") + text.lstrip("\n")
            elif isinstance(b, B.MarkdownBlock) and self._last_block in (B.ToolLine, B.FileDiff, B.Note, B.ThinkingLine):
                # What the agent says after its steps is a new paragraph, apart from the step lines.
                text = ("" if self._tail.endswith("\n\n") else "\n") + text.lstrip("\n")
            self._last_block = type(b)
            if transcript and not isinstance(b, B.ToolLine):
                self.transcript.append(b)
            if text:
                self._out.append(text)
                self._tail = (self._tail + text)[-2:]
        if self._out_event is not None:
            self._out_event.set()
        elif self._out:
            sys.stdout.write("".join(self._out))
            sys.stdout.flush()
            self._out.clear()

    def note(self, text: str, style: str = "dim", glyph: str = "") -> None:
        self.emit(B.Note(glyph, text, style, 2))

    async def call(self, fn: Callable, *args, **kwargs) -> Any:
        return await asyncio.to_thread(fn, *args, **kwargs)

    # ── layout ───────────────────────────────────────────────────────
    def _build_app(self) -> Application:
        state_dir = control.state_dir(self.env)
        try:
            state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            history = FileHistory(str(state_dir / "history"))
        except OSError:
            history = InMemoryHistory()
        self.search = SearchToolbar(text_if_not_searching="", ignore_case=True)
        self.buffer = Buffer(multiline=True, history=history, tempfile_suffix=".md",
                             on_text_changed=lambda _b: self._on_text_changed(), name="composer")
        self.dialog_buffer = Buffer(multiline=False, name="dialog-text")
        composer_control = BufferControl(self.buffer, input_processors=[Placeholder(self._placeholder), ChipHighlighter()],
                                         search_buffer_control=self.search.control, focus_on_click=True,
                                         preview_search=True)
        # Every live-region section takes exactly the rows the plan gives it (layout.allocate), so the
        # region always fits the terminal. Text is cut or wrapped by us, never by prompt_toolkit.
        self.composer_window = Window(composer_control, height=lambda: D(min=1, max=max(1, self._rows("input"))),
                                      wrap_lines=True, get_line_prefix=self._line_prefix, dont_extend_height=True)
        self.dialog_window = Window(FormattedTextControl(self._dialog_text, focusable=True, show_cursor=False),
                                    dont_extend_height=True, wrap_lines=False,
                                    width=lambda: D.exact(self._left_w()) if self._two_col() else D())
        self.diff_window = Window(FormattedTextControl(self._diff_pane_text), dont_extend_height=True, wrap_lines=False)
        self.browser_window = Window(FormattedTextControl(self._browser_text, focusable=True, show_cursor=False),
                                     dont_extend_height=True, wrap_lines=False)
        self.dialog_text_window = Window(BufferControl(self.dialog_buffer), height=1,
                                         get_line_prefix=lambda _l, _w: [("class:attention", f" {self._dialog_prompt()} ")])

        def shown(name: str) -> Condition:
            return Condition(lambda: self._rows(name) > 0)

        is_running = Condition(lambda: self.running)
        dialog_open = Condition(lambda: self.dialog is not None)
        text_mode = Condition(lambda: self.dialog is not None and self.dialog.text_mode)
        # A dialog (an approval, a question) always wins: it takes the place the manager had.
        browser_open = Condition(lambda: self.browser is not None and self.dialog is None)

        composer = HSplit([
            ConditionalContainer(Window(FormattedTextControl(self._top_rule), height=1), filter=shown("top_rule")),
            self.composer_window,
            ConditionalContainer(self.search, filter=Condition(self._searching)),
            ConditionalContainer(Window(FormattedTextControl(self._bottom_rule), height=1), filter=shown("bottom_rule")),
        ])
        dialog = HSplit([
            VSplit([self.dialog_window,
                    ConditionalContainer(self.diff_window, filter=Condition(self._two_col))]),
            ConditionalContainer(self.dialog_text_window, filter=text_mode),
            Window(FormattedTextControl(self._dialog_footer), dont_extend_height=True),
        ])
        root = HSplit([
            # Echo's entrance: the banner at the top of the live region, the row it is printed to afterwards.
            ConditionalContainer(Window(FormattedTextControl(self._welcome_text), dont_extend_height=True),
                                 filter=Condition(lambda: self.welcome is not None) & shown("welcome")),
            # Grow above the live conversation, keeping the composer at the terminal's bottom.
            # Preferred height stays zero: inline output and terminal scrollback still work.
            Window(height=D(min=0, preferred=0, weight=1)),
            ConditionalContainer(Window(FormattedTextControl(self._tail_text), dont_extend_height=True),
                                 filter=shown("tail")),
            ConditionalContainer(Window(FormattedTextControl(self._steps_text), dont_extend_height=True),
                                 filter=shown("steps")),
            ConditionalContainer(Window(FormattedTextControl(self._status_text), height=1), filter=shown("status")),
            ConditionalContainer(Window(FormattedTextControl(self._pending_text), dont_extend_height=True),
                                 filter=shown("pending")),
            ConditionalContainer(Window(height=1), filter=shown("spacer")),
            ConditionalContainer(Window(FormattedTextControl(self._menu_text), dont_extend_height=True),
                                 filter=shown("menu")),
            ConditionalContainer(Window(FormattedTextControl(self._shortcuts_text), dont_extend_height=True),
                                 filter=shown("shortcuts")),
            ConditionalContainer(dialog, filter=dialog_open),
            ConditionalContainer(self.browser_window, filter=browser_open),
            ConditionalContainer(composer, filter=~dialog_open & ~browser_open),
            ConditionalContainer(Window(FormattedTextControl(self._footer_text), dont_extend_height=True),
                                 filter=shown("footer")),
        ])
        kb = self._key_bindings(dialog_open, text_mode, is_running, browser_open)
        color_depth = None if self.caps.color else ColorDepth.DEPTH_1_BIT
        app = Application(layout=Layout(root, focused_element=self.composer_window),
                          key_bindings=merge_key_bindings([load_key_bindings(), kb]),
                          style=Style.from_dict(self._style()), full_screen=False, mouse_support=False,
                          erase_when_done=True, color_depth=color_depth, include_default_pygments_style=False)
        app.ttimeoutlen = 0.05
        app.timeoutlen = 0.08
        app.before_render += lambda _a: self._forget_plan()
        app.after_render += lambda _a: self._forget_plan()
        self._fit_live_region(app)
        self._reflow_on_resize(app)
        return app

    @staticmethod
    def _fit_live_region(app: Application) -> None:
        """Keep available terminal height, with spare rows above the live conversation.

        CPR reports the space below the scrollback cursor. Preserve it so a short conversation
        already has its input at the bottom; reserve row zero for the inline resize/scrollback rule.
        """
        renderer = app.renderer
        original = renderer.report_absolute_cursor_row

        def report(row: int) -> None:
            original(row)
            renderer._min_available_height = min(renderer._min_available_height, L.budget(app.output.get_size().rows))

        renderer.report_absolute_cursor_row = report

        def before_render(_app) -> None:
            last = renderer._last_screen
            if last is None:
                return
            size = app.output.get_size()
            renderer._min_available_height = min(renderer._min_available_height, L.budget(size.rows))
            want = min(L.budget(size.rows), max(renderer._min_available_height,
                       app.layout.container.preferred_height(size.columns, size.rows).preferred))
            if want < last.height:
                renderer.erase(leave_alternate_screen=False)

        app.before_render += before_render

    def _reflow_on_resize(self, app: Application) -> None:
        """Erase the old live region correctly when the terminal narrows (layout.reflowed_rows_above).

        prompt_toolkit erases by moving up the number of rows it drew. A terminal that re-wraps lines
        on resize has turned each full-width rule into two or more rows by then, so that erase stops
        short and leaves copies of the rules above the new region. Count the rows the old region takes
        at the new width instead, and erase from there. Widening (or a terminal that only crops) keeps
        prompt_toolkit's own erase.
        """
        renderer = app.renderer
        if self.env.get("TMUX") or str(self.env.get("TERM", "")).startswith(("tmux", "screen")):
            # tmux treats erase-to-bottom at the top-left as a screen clear and archives it.
            # A bottom-anchored region can reach that row after a height shrink. Clear each
            # physical line instead, preserving the cursor and the real conversation history.
            output = app.output

            def erase_lines() -> None:
                rows = max(1, output.get_size().rows)
                output.write_raw("\x1b7\x1b[0K" + "\x1b[1B\r\x1b[0K" * (rows - 1) + "\x1b8")

            output.erase_down = erase_lines

        def on_resize() -> None:
            last = renderer._last_screen
            old = renderer._last_size
            new = app.output.get_size()
            if self.reflows and last is not None and old is not None and new.columns < old.columns:
                lengths = []
                styled = renderer._style_string_has_style   # what prompt_toolkit counts as written
                for y in range(last.height):
                    row = last.data_buffer[y]
                    used = [x for x, ch in row.items()
                            if ch.char != " " or (styled[ch.style] if styled is not None else bool(ch.style.strip()))]
                    lengths.append(min(old.columns, max(used) + 1) if used else 0)
                pos = renderer._cursor_pos
                up = L.reflowed_rows_above(lengths, pos.y, pos.x, new.columns)
                out = app.output
                out.write_raw("\r")
                out.cursor_up(up)
                out.erase_down()
                out.reset_attributes()
                out.enable_autowrap()
                out.flush()
                renderer.reset(leave_alternate_screen=False)
            else:
                renderer.erase(leave_alternate_screen=False)
            self._forget_plan()
            app._request_absolute_cursor_position()
            app._redraw()

        app._on_resize = on_resize   # used by both the SIGWINCH handler and the size poll

    def _style(self) -> dict:
        from .theme import pt_style_dict

        return pt_style_dict(self.caps)

    # ── the height plan ──────────────────────────────────────────────
    def _forget_plan(self) -> None:
        self._plan_cache = None

    def _rows(self, name: str) -> int:
        return self._plan().get(name, 0)

    def _searching(self) -> bool:
        try:
            return self.search.control.buffer.text != "" or self.app.layout.has_focus(self.search.control)
        except Exception:  # noqa: BLE001
            return False

    def _plan(self) -> dict[str, int]:
        """Rows for each live-region section at this size (layout.allocate), computed once per frame.

        Priorities: the input (or a dialog) first, then the status and footer, the menu, what you
        sent while it runs, running steps, the rules, the stream tail, then the decorations.
        """
        w, h = self.width(), self.height()
        if self._plan_cache is not None and self._plan_cache[0] == (w, h):
            return self._plan_cache[1]
        wants: list[L.Want] = []
        if self.welcome is not None:
            n = len(self._welcome_lines())
            wants.append(L.Want("welcome", n, n, 6))   # gives way before the composer, the rules and the footer
        run = self.run
        if run is not None:
            tail = len(self._tail_all())
            wants.append(L.Want("tail", min(8, tail), 1, 6))
            tools = len(self._running_tools())
            if tools:
                wants.append(L.Want("steps", min(3, tools) + (1 if tools > 3 else 0), 1, 4))
            # A single running step already names the activity and shows its elapsed time. An open approval
            # or question says what it waits for in its own title, so the status line doesn't repeat it.
            asking = self.dialog is not None and self.dialog.kind in {"approval", "question"}
            if run.stopping or (not asking and (not tools or run.needs_ok() or len(run.working()) > 1)):
                wants.append(L.Want("status", 1, 1, 1))
        if self.pending:
            wants.append(L.Want("pending", len(self._pending_lines()), 1, 3))
        if run is not None or self.pending:
            wants.append(L.Want("spacer", 1, 0, 9))     # only from leftover rows
        if self.dialog is None and self.browser is not None:
            wants.append(L.Want("sessions", self.browser.natural_rows(h), self.browser.minimum_rows(), 0))
        elif self.dialog is None:
            items = self._menu_items()
            if items:
                wants.append(L.Want("menu", len(items), min(3, len(items)), 2))
            if self.show_shortcuts:
                wants.append(L.Want("shortcuts", len(self._shortcut_rows()), 1, 2))
            wants.append(L.Want("top_rule", 1, 1, 5))
            wants.append(L.Want("input", self._input_rows(), 1, 0))
            if self._searching():
                wants.append(L.Want("search", 1, 1, 1))
            wants.append(L.Want("bottom_rule", 1, 1, 5))
        else:
            natural = self._dialog_natural()
            wants.append(L.Want("dialog", natural, min(natural, self._dialog_minimum()), 0))
        lines = M.footer_lines(self._footer_input(), w, self.caps)
        wants.append(L.Want("footer", 1, 1, 1))
        if len(lines) > 1:
            wants.append(L.Want("mode_line", 1, 0, 7))  # the footer already says the mode
        plan = L.allocate(wants, L.budget(h))
        self._plan_cache = ((w, h), plan)
        return plan

    def _input_rows(self) -> int:
        """Rows the draft needs at this width (the prompt takes PROMPT_W cells), at most 8."""
        w = max(1, self.width() - PROMPT_W)
        text = self.buffer.text
        if not text:
            return 1
        rows = sum(max(1, -(-M.width_of(line) // w)) for line in text.split("\n"))
        return max(1, min(8, rows))

    # ── live region renderers ───────────────────────────────────────
    @staticmethod
    def _join(lines: list[StyleAndTextTuples]) -> StyleAndTextTuples:
        out: StyleAndTextTuples = []
        for i, line in enumerate(lines):
            if i:
                out.append(("", "\n"))
            out += line
        return out

    def _line_prefix(self, lineno: int, wrap_count: int) -> StyleAndTextTuples:
        # `›` and two spaces: the text (and the block cursor on it) sits clear of the prompt glyph.
        # Wrapped and continuation lines hang at the same column.
        if lineno == 0 and wrap_count == 0:
            return [("class:prompt", self.caps.glyphs.prompt), ("", " " * (PROMPT_W - 1))]
        return [("", " " * PROMPT_W)]

    def _placeholder(self) -> str:
        room = max(0, self.width() - PROMPT_W - 1)   # never wraps: a wrapped placeholder would grow the composer
        if self.running:
            text = "Steer Echo, or tab to queue a follow-up" if self.agent_name == "Echo" else \
                f"Steer {self.agent_name}, or tab to queue a follow-up"
            return M.truncate(text, room) or " "
        phrases = self._phrases()
        if self.typed or self.caps.reduced_motion:
            return M.truncate(phrases[0], room) or " "
        return M.truncate(M.typewriter(time.monotonic() * 1000 - self.start_ms, phrases), room) or " "

    def _phrases(self) -> tuple[str, ...]:
        if self.core.agent_id or ((self.core.session or {}).get("environment") == "agents"):
            return M.AGENT_PHRASES
        if self.core.project_id:
            return M.CODE_PHRASES
        return M.PHRASES

    def _title(self) -> str:
        title = one_line((self.core.session or {}).get("title") or "", 60)
        if self.core.agent_id and not title:
            return f"new session with {self.agent_name}"
        return title or ("new session" if not self.sid else "untitled")

    def _top_rule(self) -> StyleAndTextTuples:
        return M.rule(self.width(), self._title(), self.caps)

    def _bottom_rule(self) -> StyleAndTextTuples:
        return M.rule(self.width(), "", self.caps)

    def _tail_indent(self) -> int:
        return 2 if self.width() >= L.MICRO else 0

    def _tail_all(self) -> list[StyleAndTextTuples]:
        """The answer still streaming, wrapped at the prose measure, with light inline markdown so
        it already looks like the block it will be committed as (code fences stay literal)."""
        if not self.run:
            return []
        tail = self.run.tail()
        if not tail.strip():
            return []
        width = max(1, min(self.width(), L.measure(self.width(), self.env)) - 2 - self._tail_indent())
        key = (tail, width)
        if self._tail_cache is not None and self._tail_cache[0] == key:
            return self._tail_cache[1]
        out: list[StyleAndTextTuples] = []
        fence = False
        for line in tail.rstrip("\n").split("\n"):
            if line.lstrip().startswith(("```", "~~~")):
                fence = not fence
                out += [[("class:tail class:dim", part)] for part in _wrap(line, width)]
                continue
            frags = [("class:tail", line)] if fence else _inline_md(line)
            out += _wrap_frags(frags, width)
        self._tail_cache = (key, out)
        return out

    def _tail_lines(self) -> list[StyleAndTextTuples]:
        rows = self._rows("tail")
        return self._tail_all()[-rows:] if rows > 0 else []

    def _tail_text(self) -> StyleAndTextTuples:
        pad = " " * self._tail_indent()
        return self._join([[("", pad)] + line for line in self._tail_lines()])

    def _running_tools(self) -> list:
        """Running steps worth a line: not the one an open approval or question is already asking about."""
        if self.run is None:
            return []
        tools = self.run.running_tools()
        if self.dialog is None or self.dialog.kind not in {"approval", "question"}:
            return tools
        asked = {str((d.payload or {}).get("tool_call_id") or "") for d in [self.dialog, *self.dialogs]} - {""}
        return [t for t in tools if t.id not in asked and t.name != "ask_user"]

    def _steps_text(self) -> StyleAndTextTuples:
        if not self.run:
            return []
        tools = self._running_tools()
        rows = self._rows("steps")
        if not tools or rows <= 0:
            return []
        now = self.now()
        w = self.width()
        g = self.caps.glyphs
        indent = "  " if w >= L.MICRO else ""
        if rows == 1 and len(tools) > 1:
            return [("class:dim", M.truncate(f"{indent}{len(tools)} steps running", w))]
        show = tools[:rows] if len(tools) <= rows else tools[:rows - 1]
        lines: list[StyleAndTextTuples] = []
        for i, t in enumerate(show):
            style, glyph = M.ping((now * 1000) + i * 240, self.caps)
            dur = M.fmt_elapsed(now - t.started)
            label = t.label + (f" {g.sep} {t.detail}" if t.detail else "")
            fixed = len(indent) + 2
            if w - fixed - M.width_of(dur) - 2 >= 8:
                lines.append([("", indent), (f"class:{style}", glyph + " "),
                              ("", M.truncate(label, w - fixed - M.width_of(dur) - 2)), ("class:dim", f"  {dur}")])
            else:
                lines.append([("", indent), (f"class:{style}", glyph + " "), ("", M.truncate(label, max(1, w - fixed)))])
        if len(show) < len(tools):
            lines.append([("class:dim", M.truncate(f"{indent}  +{len(tools) - len(show)} more", w))])
        return self._join(lines)

    def _status_text(self) -> StyleAndTextTuples:
        run = self.run
        if not run:
            return []
        now = self.now()
        stalled = (now - run.last_event) * 1000
        if run.running_tools() or self.dialog is not None or run.needs_ok():
            stalled = 0  # a long tool or a waiting approval is not a stall
        activity = run.activity(now)
        quiet = (now - run.last_specific) * 1000
        flav = M.flavour(now * 1000, quiet, self.caps) if activity.endswith(("is working", "is starting")) else ""
        if flav:
            activity = flav
        si = M.StatusInput(activity=activity, elapsed_s=now - run.started, tokens=run.tokens, stalled_ms=stalled,
                           needs_ok=run.needs_ok() and not run.stopping, stopping=run.stopping)
        return M.status_line(si, now * 1000, self.width(), self.caps)

    def _pending_lines(self, rows: Optional[int] = None) -> list[StyleAndTextTuples]:
        """What you sent while it runs. Cut to ``rows``: newest items first, then a one-line summary."""
        steers = [p for p in self.pending if p.kind == "steer"]
        queued = [p for p in self.pending if p.kind == "queue"]
        w = self.width()
        g = self.caps.glyphs
        ind = "  " if w >= L.MICRO else ""

        def head(text: str) -> StyleAndTextTuples:
            return [("class:dim", M.truncate(ind + text, w))]

        def item(p: PendingInput) -> StyleAndTextTuples:
            lead = f"{ind}{g.steer} "
            return [("class:dim", lead), ("", M.truncate(one_line(p.text, 400), max(1, w - M.width_of(lead))))]

        lines: list[StyleAndTextTuples] = []
        if steers:
            lines.append(head("Sent at the next checkpoint"))
            lines += [item(p) for p in steers[-3:]]
        if queued:
            edit = f" {g.sep} ↑ to edit the last one" if self.running and w >= 56 else ""
            lines.append(head("Queued for after this turn" + edit))
            lines += [item(p) for p in queued[-3:]]
        if rows is None or len(lines) <= rows:
            return lines
        parts = []
        if steers:
            parts.append(f"{len(steers)} to steer")
        if queued:
            parts.append(f"{len(queued)} queued")
        summary = head(f"{g.steer} " + f" {g.sep} ".join(parts))
        newest = [item(p) for p in (steers + queued)[-(rows - 1):]] if rows > 1 else []
        return [summary] + newest

    def _pending_text(self) -> StyleAndTextTuples:
        rows = self._rows("pending")
        return self._join(self._pending_lines(rows)) if rows > 0 else []

    def _menu_token(self) -> Optional[str]:
        text = self.buffer.text
        if not text.startswith("/") or "\n" in text or " " in text:
            return None
        return text

    def _menu_items(self) -> list:
        token = self._menu_token()
        if token is None or token == self.menu_dismissed:
            return []
        return filter_commands(token, self.commands)[:8]

    def _menu_text(self) -> StyleAndTextTuples:
        items = self._menu_items()
        rows = self._rows("menu")
        if not items or rows <= 0:
            return []
        w = self.width()
        sel = self.menu_index % len(items)
        top = max(0, min(sel - rows + 1, len(items) - rows)) if sel >= rows else 0
        indent = "  " if w >= L.MICRO else ""
        name_w = max(M.width_of(c.name) for c in items) + 3
        lines: list[StyleAndTextTuples] = []
        for i, c in enumerate(items[top:top + rows], start=top):
            chosen = i == sel
            mark = f"{self.caps.glyphs.select} " if chosen else "  "
            room = w - len(indent) - M.width_of(mark) - name_w
            name = c.name.ljust(name_w) if room >= 8 else c.name
            desc = M.truncate(c.desc, room) if room >= 8 else ""
            head = M.truncate(indent + mark + name, w)
            style = "class:menu.sel" if chosen else "class:menu.name"
            lines.append([(style, head)] + ([("class:menu.sel" if chosen else "class:menu.desc", desc)] if desc else []))
        return self._join(lines)

    def _shortcut_rows(self) -> list[str]:
        w = self.width()
        cols = 3 if w >= L.WIDE else 2 if w >= L.NARROW else 1
        indent = 2 if w >= L.MICRO else 0
        col_w = max(1, (w - indent) // cols)
        cells = [c for row in SHORTCUTS for c in row]
        rows = [cells[i:i + cols] for i in range(0, len(cells), cols)]
        out = []
        for row in rows:
            text = "".join(M.truncate(c, max(1, col_w - 2)).ljust(col_w) for c in row).rstrip()
            out.append(M.truncate(" " * indent + text, w))
        return out

    def _shortcuts_text(self) -> StyleAndTextTuples:
        rows = self._rows("shortcuts")
        lines = self._shortcut_rows()
        if rows <= 0:
            return []
        if len(lines) > rows:
            ind = "  " if self.width() >= L.MICRO else ""
            hidden = len(lines) - max(0, rows - 1)
            lines = lines[:max(0, rows - 1)] + [M.truncate(f"{ind}… {hidden} more rows (a taller terminal shows all)",
                                                           self.width())]
        return self._join([[("class:dim", line)] for line in lines])

    def _footer_input(self) -> M.FooterInput:
        notice = self.notice[0] if self.notice[1] > self.now() else ""
        ctx = None
        if self.context and self.context[1]:
            ctx = round(100 * self.context[0] / self.context[1])
        agent = self.agent_name if self.core.agent_id else ""
        return M.FooterInput(model=self.model, agent=agent, project=self.project_name, approval_mode=self.approval_mode,
                             context_pct=ctx, held=self.held, running=self.running and self.dialog is None,
                             notice=notice, quiet=self.dialog is not None or self.browser is not None)

    def _footer_text(self) -> StyleAndTextTuples:
        lines = M.footer_lines(self._footer_input(), self.width(), self.caps)
        rows = self._rows("footer") + self._rows("mode_line")
        return self._join(lines[:max(1, rows)])

    def _dialog_prompt(self) -> str:
        d = self.dialog
        return (d.text_prompt if d and d.text_prompt else "›") + ""

    # ── dialogs: fitted to the rows the plan gives them ─────────────
    def _dialog_chrome(self, rows: Optional[int]) -> int:
        """Rows under the options: the text field (when open) and the footer (a blank line + hint, or
        just the hint when space is short)."""
        d = self.dialog
        field = 1 if d is not None and d.text_mode else 0
        if rows is None:
            return field + 2
        # The blank line above the hint is the first thing to go when the dialog doesn't fit.
        return field + (2 if rows >= self._dialog_natural() else 1)

    def _dialog_minimum(self) -> int:
        d = self.dialog
        if d is None:
            return 0
        # What you need to decide without scrolling: the title rule, the first line of the body (the
        # question, or what the action is), every option up to 6, and the hint.
        first = 1 if d.body and d.body[0][0] != DIFF_MARK else 0
        return 1 + first + min(6, max(1, len(d.visible_options()))) + self._dialog_chrome(0)

    def _dialog_natural(self) -> int:
        left = len(self._dialog_lines(None))
        if self._two_col():
            left = max(left, 1 + min(len(self._diff_rows(self.dialog.change, self._diff_w(), 1)), self._diff_page()))
        return left + self._dialog_chrome(None)

    def _dialog_rows(self) -> int:
        """Rows for the dialog's own text (rule, body, options) at this frame."""
        total = self._rows("dialog")
        return max(1, total - self._dialog_chrome(total))

    def _dialog_text(self) -> StyleAndTextTuples:
        if self.dialog is None:
            return []
        return self._join(self._dialog_lines(self._dialog_rows()))

    def _option_line(self, d: Dialog, i: int, opt: Option, w: int) -> StyleAndTextTuples:
        g = self.caps.glyphs
        selected = i == d.selected
        mark = f"{g.select} " if selected else "  "
        num = "" if d.filterable else f"{i + 1}. "
        key = f"({opt.key})" if opt.key else ""
        left = " " + mark + num + opt.label
        if key and M.width_of(left) + 1 + M.width_of(key) > w - 1:
            key = ""   # keys go first when space runs out; the numbers still work
        avail = w - 1 - (M.width_of(key) + 1 if key else 0)
        hint = opt.hint
        if M.width_of(left) > avail:
            left, hint = M.truncate(left, max(1, avail)), ""
        elif hint and M.width_of(left) + 2 + M.width_of(hint) > avail:
            room = avail - M.width_of(left) - 2
            hint = M.truncate(hint, room) if room > 6 else ""
        body_style = "class:sel" if selected else ("class:dim" if opt.style == "dim" else "")
        out: StyleAndTextTuples = [(body_style, left)]
        used = M.width_of(left)
        if hint:
            out.append(("class:dim", "  " + hint))
            used += 2 + M.width_of(hint)
        if key:
            out += [("", " " * max(1, avail - used + 1)), ("class:key", key)]
        return out

    def _dialog_lines(self, rows: Optional[int]) -> list[StyleAndTextTuples]:
        """Rule, body, options. With ``rows``, fitted: options first (scrolled to the selection), then
        the rule, then the body (the diff gives way before the text, the title line is kept)."""
        d = self.dialog
        if d is None:
            return []
        two = self._two_col()
        w = self._left_w() if two else self.width()
        g = self.caps.glyphs
        style = "class:attention.rule" if d.attention else "class:rule"
        rule = M.rule(w, d.title, self.caps, style=style,
                      title_style="class:attention" if d.attention else "class:title")
        if w < 24 and d.title:
            rule = [("class:attention" if d.attention else "class:title", M.truncate(d.title, w))]
        vis = d.visible_options()
        pos = [i for i, _ in vis].index(d.selected) if d.selected in [i for i, _ in vis] else 0
        limit = 12 if rows is None else max(1, rows - (1 if rows >= 3 else 0))
        shown = min(len(vis), 12, limit)
        more = len(vis) - shown
        if more > 0 and shown >= 3:
            shown -= 1          # room for the "… N more" line
        top = max(0, min(pos - shown + 1, len(vis) - shown)) if pos >= shown else 0
        options = [self._option_line(d, i, opt, w) for i, opt in vis[top:top + shown]]
        if len(vis) > shown and shown >= 2:
            how = "↑↓ or type to filter" if d.filterable else "↑↓ to see them"
            options.append([("class:dim", M.truncate(f"   … {len(vis) - shown} more · {how}", w))])
        if d.filterable and d.filter_text:
            options.append([("class:dim", M.truncate(f"   filter: {d.filter_text}", w))])

        def body(diff_rows: Optional[int]) -> list[StyleAndTextTuples]:
            out: list[StyleAndTextTuples] = []
            for st, text in d.body:
                if st == DIFF_MARK:
                    if not two and diff_rows != 0:
                        out += self._stacked_diff(d, w, diff_rows)
                    continue
                for line in _wrap(text, max(1, w - 2)):
                    out.append([(st, " " + line)])
            return out + [[]] if out else out

        if rows is None:
            return [rule] + body(None) + options
        left = rows - len(options)
        out = [rule] if left >= 1 and rows >= 3 else []
        left -= len(out)
        if left > 0 and d.body:
            full = body(None)
            if len(full) > left:
                text_only = len(body(0))
                diff_room = left - text_only
                full = body(diff_room if diff_room >= 3 else 0)
                if len(full) > left:
                    if left == 1:
                        # The title line (the question, "Edit a file") whole on one line beats a hint.
                        st, text = d.body[0]
                        full = [[(st, M.truncate(" " + one_line(text, 400), w))]] if st != DIFF_MARK else full[:1]
                    elif left == 2:
                        full = full[:2]     # the title and the command ("$ pytest …") you are approving
                    else:
                        full = full[:left - 1] + [[("class:dim", M.truncate(" … ctrl+g for everything", w))]]
            out += full
        return out + options[:max(0, rows - len(out))] if len(out) + len(options) > rows else out + options

    # ── approval diff (two columns or stacked) ─────────────────────
    def _two_col(self) -> bool:
        d = self.dialog
        return bool(d is not None and d.change is not None and d.change.hunks and not d.text_mode
                    and self.width() >= TWO_COL_MIN_W and self.height() >= TWO_COL_MIN_H)

    def _left_w(self) -> int:
        """The left column fits the longest option (labels are never cut), 44 by default, at most 64."""
        d = self.dialog
        longest = max((M.width_of(o.label) + 7 + (M.width_of(o.key) + 4 if o.key else 0) for o in (d.options if d else [])),
                      default=0)
        return max(TWO_COL_LEFT, min(64, longest + 2, self.width() - 60))

    def _diff_w(self) -> int:
        return max(20, self.width() - self._left_w()) - 2

    def _ansi_rows(self, lines, width: int) -> list[StyleAndTextTuples]:
        return [_crop(list(to_formatted_text(ANSI(row))), width) for row in DV.to_ansi(lines, width, self.caps)]

    def _diff_rows(self, change: DV.Change, width: int, indent: int) -> list[StyleAndTextTuples]:
        """Every diff row as fragments, cached: the live region redraws up to 20 times a second."""
        width = max(1, width)
        key = (id(change), width, indent)
        hit = self._diff_cache.get(key)
        if hit is None:
            rows, _ = DV.unified(change, width, self.caps, max_rows=None, indent=min(indent, max(0, width // 4)),
                                 hint="")
            hit = self._ansi_rows(rows, width)
            if len(self._diff_cache) > 8:
                self._diff_cache.clear()
            self._diff_cache[key] = hit
        return hit

    def _stacked_page(self, width: int) -> int:
        return 6 if width < L.NARROW else STACKED_DIFF_ROWS

    def _stacked_diff(self, d: Dialog, width: int, rows: Optional[int] = None) -> list[StyleAndTextTuples]:
        """Below 120 columns: at most 10 rows of the diff (6 under 60, fewer when the terminal is
        short), scrolled with pgup/pgdn, plus one line saying where you are."""
        page = self._stacked_page(width) if rows is None else max(1, min(self._stacked_page(width), rows - 1))
        rows_all = self._diff_rows(d.change, width, 4 if width >= L.NARROW else 1)
        top = max(0, min(d.diff_top, max(0, len(rows_all) - page)))
        shown = rows_all[top:top + page]
        rest = len(rows_all) - top - len(shown)
        g = self.caps.glyphs
        if rest > 0 or top:
            where = f"rows {top + 1}–{top + len(shown)} of {len(rows_all)}"
            text = (f"    {g.ellipsis} +{rest} lines {g.sep} {where} {g.sep} pgup/pgdn {g.sep} ctrl+g"
                    if rest > 0 else f"    {where} {g.sep} ctrl+g full diff")
            shown.append([("class:dim", M.truncate(text if width >= L.NARROW else text.strip(), width))])
        return shown

    def _diff_page(self) -> int:
        """Diff rows the right column would like (the plan may give it fewer)."""
        return max(4, self.height() - 8)

    def _scroll_page(self) -> tuple[int, int, int]:
        """(page, width, indent) of the diff on screen now, for PgUp/PgDn."""
        if self._two_col():
            return max(1, self._dialog_rows() - 1), self._diff_w(), 1
        w = self.width()
        return self._stacked_page(w), w, 4 if w >= L.NARROW else 1

    def _diff_pane_text(self) -> StyleAndTextTuples:
        """The right column: '┬─ src/dates.py · +3 −1 ───── rows 1–9 of 14 ─' over '│ ' + diff rows."""
        d = self.dialog
        if d is None or d.change is None or not self._two_col():
            return []
        g = self.caps.glyphs
        width = max(20, self.width() - self._left_w())
        rows = self._diff_rows(d.change, width - 2, 1)
        page = max(1, self._dialog_rows() - 1)
        top = max(0, min(d.diff_top, max(0, len(rows) - page)))
        d.diff_top = top
        shown = rows[top:top + page]
        where = f"rows {top + 1}–{top + len(shown)} of {len(rows)}"
        title = M.truncate(DV.summary_text(d.change, self.caps), max(10, width - M.width_of(where) - 10))
        fill = max(1, width - 3 - M.width_of(title) - 2 - M.width_of(where) - 2)
        top_rule = "┬" if not self.caps.ascii else "+"
        bar = "class:attention.rule" if d.attention else "class:rule"
        out: StyleAndTextTuples = [(bar, top_rule + g.rule + " "), ("class:title", title), (bar, " " + g.rule * fill + " "),
                                   ("class:dim", where), (bar, " " + g.rule)]
        left_lines = len(self._dialog_lines(self._dialog_rows()))
        for i in range(min(page, max(len(shown), left_lines - 1))):
            out += [("", "\n"), (bar, g.vbar + " ")]
            if i < len(shown):
                out += shown[i]
        return out

    def _dialog_footer(self) -> StyleAndTextTuples:
        d = self.dialog
        if d is None:
            return []
        if d.text_mode:
            text = "enter to send · esc " + ("to deny without a note" if d.kind == "approval" else "to go back")
        elif d.footer:
            text = d.footer
        else:
            text = f"{self.caps.glyphs.arrow_up_down} choose · enter confirm · esc cancel"
        line = [("class:dim", M.truncate(" " + text, self.width()))]
        total = self._rows("dialog")
        blank = self._dialog_chrome(total) - (1 if d.text_mode else 0) > 1
        return ([("", "\n")] if blank else []) + line

    # ── key bindings ─────────────────────────────────────────────────
    def _key_bindings(self, dialog_open, text_mode, is_running, browser_open=None) -> KeyBindings:
        kb = KeyBindings()
        composer = has_focus(self.buffer)
        nav = dialog_open & ~text_mode
        empty = Condition(lambda: self.buffer.text == "")
        browsing = browser_open if browser_open is not None else Condition(lambda: False)

        # ── the session manager (tui/sessions.py) ──
        # Left on an empty input has nothing to move over, so it opens the list of conversations.
        # With any text in the input it is the ordinary cursor key.
        @kb.add("left", filter=composer & empty & ~dialog_open)
        def _sessions_open(event) -> None:
            self.open_sessions()

        @kb.add("up", filter=browsing)
        @kb.add("down", filter=browsing)
        @kb.add("pageup", filter=browsing)
        @kb.add("pagedown", filter=browsing)
        @kb.add("home", filter=browsing)
        @kb.add("end", filter=browsing)
        @kb.add("tab", filter=browsing)
        @kb.add("s-tab", filter=browsing)
        @kb.add("enter", filter=browsing)
        @kb.add("right", filter=browsing)
        @kb.add("left", filter=browsing)
        @kb.add("backspace", filter=browsing)
        @kb.add("c-n", filter=browsing)
        @kb.add("escape", filter=browsing, eager=True)
        def _sessions_keys(event) -> None:
            self._browser_key(event.key_sequence[0].key)

        @kb.add(Keys.Any, filter=browsing)
        def _sessions_char(event) -> None:
            if self.browser is not None and event.data and event.data.isprintable():
                self.browser.type(event.data)
                self.app.invalidate()

        @kb.add("enter", filter=composer)
        def _enter(event) -> None:
            items = self._menu_items()
            text = self.buffer.text
            if items and text.startswith("/"):
                # An exact command name wins over the highlighted row.
                typed = lookup(text.strip(), self.commands)
                cmd = typed or items[self.menu_index % len(items)]
                self.buffer.set_document(Document(cmd.name), bypass_readonly=True)
                self.buffer.append_to_history()
                self.buffer.reset()
                self._dispatch(cmd.name, record=text)
                return
            if text.endswith("\\") and not text.endswith("\\\\"):
                self.buffer.delete_before_cursor(1)
                self.buffer.insert_text("\n")
                return
            self._submit()

        @kb.add("c-j", filter=composer)
        @kb.add("escape", "enter", filter=composer)
        def _newline(event) -> None:
            self.buffer.insert_text("\n")

        @kb.add("tab", filter=composer)
        def _tab(event) -> None:
            items = self._menu_items()
            if items:
                cmd = items[self.menu_index % len(items)]
                self.buffer.set_document(Document(cmd.name + " "), bypass_readonly=True)
                return
            if self.running and self.buffer.text.strip():
                text = self._take_input()
                self._spawn(self._queue(text))

        @kb.add("s-tab", filter=~browsing)
        def _cycle(event) -> None:
            if self.dialog is not None:
                return
            self._spawn(self._cycle_mode())

        @kb.add("escape", filter=composer)
        def _escape(event) -> None:
            now = self.now()
            if self._menu_items():
                self.menu_dismissed = self.buffer.text
                return
            if self.show_shortcuts:
                self.show_shortcuts = False
                return
            if self.running:
                self._spawn(self._stop())
                return
            if now - self.esc_at < 0.6 and self.buffer.text:
                self.buffer.append_to_history()
                self.buffer.reset()
                self.say_notice("draft cleared (saved to history)")
            self.esc_at = now

        @kb.add("c-c")
        def _ctrl_c(event) -> None:
            now = self.now()
            # "Twice" only counts presses with the same meaning: a press that stopped a run does not
            # arm "exit", so stop-then-quick-second-press never quits the app by surprise.
            mode = "run" if self.running else "idle"
            twice = now - self.ctrl_c_at < 2.0 and self._ctrl_c_mode == mode
            self.ctrl_c_at = now
            self._ctrl_c_mode = mode
            if self.dialog is not None:
                self._close_dialog(Action("cancel", self.dialog.escape_value if self.dialog.kind != "approval" else None))
                return
            if self.browser is not None:
                self.close_sessions()
                return
            if self.running:
                if twice:
                    self._detach()
                else:
                    self._spawn(self._stop())
                    self.say_notice("stopping · ctrl+c again to detach", 2.0)
                return
            if self.buffer.text:
                self.buffer.reset()
                return
            if twice:
                event.app.exit()
            else:
                self.say_notice("press ctrl+c again to exit", 2.0)

        @kb.add("c-d", filter=composer & empty)
        def _ctrl_d(event) -> None:
            if self.running:
                self._detach()
            else:
                event.app.exit()

        @kb.add("up", filter=composer)
        def _up(event) -> None:
            items = self._menu_items()
            if items:
                self.menu_index = (self.menu_index - 1) % len(items)
                return
            if self.running and not self.buffer.text and any(p.kind == "queue" for p in self.pending):
                self._spawn(self._pull_back())
                return
            self.buffer.auto_up(count=event.arg)

        @kb.add("down", filter=composer)
        def _down(event) -> None:
            items = self._menu_items()
            if items:
                self.menu_index = (self.menu_index + 1) % len(items)
                return
            self.buffer.auto_down(count=event.arg)

        @kb.add("?", filter=composer & empty)
        def _question(event) -> None:
            self.show_shortcuts = not self.show_shortcuts

        @kb.add("c-o")
        def _ctrl_o(event) -> None:
            self._spawn(self._pager())

        @kb.add("c-g", filter=composer)
        def _editor(event) -> None:
            self.buffer.open_in_editor(validate_and_handle=False)

        @kb.add("c-g", filter=dialog_open)
        def _full_args(event) -> None:
            if self.dialog and self.dialog.full_text:
                self._spawn(self._page_text(self.dialog.full_text, end=self.dialog.change is None))

        @kb.add("c-l")
        def _redraw(event) -> None:
            event.app.renderer.erase()
            event.app.invalidate()

        @kb.add("c-z")
        def _suspend(event) -> None:
            event.app.suspend_to_background()

        @kb.add(Keys.BracketedPaste, filter=composer)
        def _paste(event) -> None:
            data = event.data.replace("\r\n", "\n").replace("\r", "\n")
            lines = data.rstrip("\n").count("\n") + 1
            if len(data) > PASTE_CHARS or lines > PASTE_LINES:
                n = str(len(self.chips) + 1)
                self.chips[n] = data
                self.buffer.insert_text(f"[Pasted {len(data):,} chars · {lines} lines #{n}]")
            else:
                self.buffer.insert_text(data)

        # dialog navigation
        @kb.add("up", filter=nav)
        @kb.add("down", filter=nav)
        @kb.add("enter", filter=nav)
        @kb.add("escape", filter=nav, eager=True)
        def _dialog_keys(event) -> None:
            key = {"up": "up", "down": "down", "c-m": "enter", "escape": "escape"}.get(event.key_sequence[0].key,
                                                                                      event.key_sequence[0].key)
            self._dialog_key(key)

        @kb.add("pageup", filter=nav)
        @kb.add("pagedown", filter=nav)
        def _dialog_scroll(event) -> None:
            d = self.dialog
            if d is None or d.change is None:
                return
            page, width, indent = self._scroll_page()
            total = len(self._diff_rows(d.change, width, indent))
            step = max(1, page - 2)
            d.scroll_diff(step if event.key_sequence[0].key == Keys.PageDown else -step, total, page)
            self.app.invalidate()

        @kb.add("backspace", filter=nav)
        def _dialog_backspace(event) -> None:
            if self.dialog and self.dialog.filterable and self.dialog.filter_text:
                self.dialog.filter_text = self.dialog.filter_text[:-1]
                self._refilter()

        @kb.add(Keys.Any, filter=nav)
        def _dialog_char(event) -> None:
            data = event.data
            d = self.dialog
            if d is None or not data or not data.isprintable():
                return
            if d.filterable:
                d.filter_text += data
                self._refilter()
                return
            self._dialog_key(data)

        @kb.add("enter", filter=text_mode)
        def _dialog_text_enter(event) -> None:
            if self.dialog:
                text = self.dialog_buffer.text
                self.dialog_buffer.reset()
                self._close_dialog(self.dialog.submit_text(text))

        @kb.add("escape", filter=text_mode, eager=True)
        def _dialog_text_escape(event) -> None:
            if self.dialog:
                self.dialog_buffer.reset()
                self._close_dialog(self.dialog.cancel_text())

        return kb

    def _refilter(self) -> None:
        d = self.dialog
        if d is None:
            return
        vis = [i for i, _ in d.visible_options()]
        if vis and d.selected not in vis:
            d.selected = vis[0]

    def _dialog_key(self, key: str) -> None:
        d = self.dialog
        if d is None:
            return
        action = d.key(key, self.now())
        if d.text_mode:
            self.dialog_buffer.reset()
            self.app.layout.focus(self.dialog_text_window)
            return
        self._close_dialog(action)

    def _on_text_changed(self) -> None:
        if self.buffer.text:
            self.typed = True
            self.show_shortcuts = False
        token = self._menu_token()
        if token != self.menu_dismissed:
            self.menu_dismissed = None
        self.menu_index = 0 if token is None else self.menu_index

    # ── the session manager ──────────────────────────────────────────
    def open_sessions(self) -> None:
        """Show every conversation in the composer's place (← on an empty input, /sessions)."""
        if self.dialog is not None:
            return
        self.browser = SessionBrowser(current_id=self.sid or "", project_id=self.core.project_id or "",
                                      prefer_id=str((self.last_here or {}).get("thread_id") or ""))
        self.show_shortcuts = False
        try:
            self.app.layout.focus(self.browser_window)
        except ValueError:
            pass
        self.app.invalidate()
        self._spawn(self._load_sessions())

    def close_sessions(self) -> None:
        """Back to the conversation you were in, untouched."""
        if self.browser is None:
            return
        self.browser = None
        if self.dialog is None:
            try:
                self.app.layout.focus(self.composer_window)
            except ValueError:
                pass
        self.app.invalidate()

    async def _load_sessions(self) -> None:
        browser = self.browser
        if browser is None:
            return
        try:
            try:
                rows = (await self.call(self.client.get, "/sessions/overview", limit=200) or {}).get("items") or []
            except ApiError as exc:
                if exc.status not in {404, 405}:
                    raise
                # An older backend: the plain thread list, without agents, projects or status.
                rows = [from_thread(t) for t in await self.call(self.client.threads, 200) if not t.get("archived")]
        except (ApiError, ConnectError) as exc:
            if self.browser is browser:
                browser.loading, browser.error = False, f"could not load conversations: {one_line(str(exc), 120)}"
                self.app.invalidate()
            return
        if self.browser is browser:
            browser.load(rows)
            self.app.invalidate()

    def _browser_text(self) -> StyleAndTextTuples:
        if self.browser is None:
            return []
        return self._join(self.browser.lines(self.width(), max(1, self._rows("sessions")), self.caps))

    def _browser_key(self, key) -> None:
        browser = self.browser
        if browser is None:
            return
        page = max(1, self._rows("sessions") - 6)
        on_page = browser.mode == "page"
        move = browser.move_page if on_page else browser.move
        if key == Keys.Up:
            move(-1)
        elif key == Keys.Down:
            move(1)
        elif key == Keys.PageUp:
            move(-page)
        elif key == Keys.PageDown:
            move(page)
        elif key == Keys.Home:
            (browser.jump_page if on_page else browser.jump)(False)
        elif key == Keys.End:
            (browser.jump_page if on_page else browser.jump)(True)
        elif key == Keys.Tab:
            browser.cycle_scope(1)
        elif key == Keys.BackTab:
            browser.cycle_scope(-1)
        elif key == Keys.Backspace:
            browser.backspace()
        elif key == Keys.Left:
            # ← again: the full-screen session browser (tui/sessionpage.py), on the alternate screen.
            self._spawn(self._open_page())
            return
        elif key == Keys.Escape:
            # Esc clears a search first, then goes back one level: page → picker → your conversation.
            if not browser.clear_query():
                if on_page:
                    browser.mode = "picker"
                else:
                    self.close_sessions()
                    return
        elif key == Keys.Right:
            if on_page:
                browser.mode = "picker"
            else:
                self.close_sessions()
                return
        elif key == Keys.ControlN:
            self.close_sessions()
            self._spawn(self.cmd_new([]))
            return
        elif key in {Keys.Enter, Keys.ControlM}:
            row = browser.chosen()
            if row is None:
                return
            self.close_sessions()
            if row.get("thread_id") == self.sid:
                self.say_notice("you are already in this conversation")
            else:
                self._spawn(self._resume_row(row))
            return
        self._forget_plan()    # the page and the picker want different heights
        self.app.invalidate()

    async def _open_page(self) -> None:
        """The full-screen browser in place of the picker; back returns to the picker as it was."""
        browser = self.browser
        if browser is None or self.dialog is not None:
            return
        from . import sessionpage

        self.browser = None            # the picker steps aside while the page has the screen
        self._forget_plan()
        try:
            async with in_terminal():
                action, row = await sessionpage.run(browser, self.caps, self.app.style)
        except Exception:  # noqa: BLE001 - never lose the conversation over the browser
            action, row = "back", None
        browser.mode = "picker"
        if action == "back" and self.dialog is None:
            self.browser = browser
            try:
                self.app.layout.focus(self.browser_window)
            except ValueError:
                pass
        else:
            try:
                self.app.layout.focus(self.composer_window)
            except ValueError:
                pass
            if action == "resume" and row is not None:
                if row.get("thread_id") == self.sid:
                    self.say_notice("you are already in this conversation")
                else:
                    await self._resume_row(row)
            elif action == "new":
                await self.cmd_new([])
        self._forget_plan()
        self.app.invalidate()

    async def _resume_row(self, row: dict) -> None:
        # The thread record is what the rest of the client holds; the overview row only chose it.
        try:
            session = await self.call(self.client.thread, str(row.get("thread_id")))
        except ApiError:
            session = row
        await self._switch(session)

    # ── dialogs ──────────────────────────────────────────────────────
    def open_dialog(self, d: Dialog) -> None:
        self.dialogs.append(d)
        if self.dialog is None:
            self._show_next_dialog()

    def _show_next_dialog(self) -> None:
        if self.dialog is not None or not self.dialogs:
            return
        self.dialog = self.dialogs.pop(0)
        self.dialog.opened_at = self.now()
        self.show_shortcuts = False
        self.browser = None   # something needs a decision: the list gives way
        try:
            self.app.layout.focus(self.dialog_window)
        except ValueError:
            pass
        self.app.invalidate()

    def _close_dialog(self, action: Action) -> None:
        d = self.dialog
        if d is None or action.kind == "none":
            return
        self.dialog = None
        try:
            self.app.layout.focus(self.composer_window)
        except ValueError:
            pass
        if d.on_result is not None:
            result = d.on_result(action)
            if asyncio.iscoroutine(result):
                self._spawn(result)
        self._show_next_dialog()
        self.app.invalidate()

    def drop_dialogs(self, pred: Callable[[Dialog], bool]) -> None:
        self.dialogs = [d for d in self.dialogs if not pred(d)]
        if self.dialog is not None and pred(self.dialog):
            self.dialog = None
            try:
                self.app.layout.focus(self.composer_window)
            except ValueError:
                pass
            self._show_next_dialog()

    def ask(self, title: str, options: list[Option], *, body: Optional[list[tuple[str, str]]] = None,
            attention: bool = False, footer: str = "", selected: int = 0, filterable: bool = False,
            kind: str = "picker", text_prompt: str = "") -> "asyncio.Future":
        """Open a dialog and await the Action."""
        fut = asyncio.get_running_loop().create_future()

        def done(action: Action) -> None:
            if not fut.done():
                fut.set_result(action)

        self.open_dialog(Dialog(title=title, options=options, body=body or [], attention=attention, footer=footer,
                                selected=selected, filterable=filterable, kind=kind, on_result=done,
                                text_prompt=text_prompt))
        return fut

    # ── input ────────────────────────────────────────────────────────
    def _take_input(self) -> str:
        text = self.buffer.text
        self.buffer.append_to_history()
        self.buffer.reset()
        return self._expand_chips(text)

    def _expand_chips(self, text: str) -> str:
        def sub(m: re.Match) -> str:
            return self.chips.get(m.group(1), m.group(0))
        return CHIP_RE.sub(sub, text)

    def _submit(self) -> None:
        raw = self.buffer.text
        if not raw.strip():
            return
        if raw.startswith("/") and not raw.startswith("//"):
            self.buffer.append_to_history()
            self.buffer.reset()
            self._dispatch(raw.strip(), record=raw)
            return
        text = self._take_input()
        display = raw[1:] if raw.startswith("//") else raw
        if raw.startswith("//"):
            text = text[1:]
        if self.running:
            self._spawn(self._steer(text))
        else:
            self._spawn(self.send(text, display=display))

    def _spawn(self, coro) -> None:
        task = asyncio.ensure_future(coro)

        def done(t: asyncio.Task) -> None:
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                self._report(exc)

        task.add_done_callback(done)

    def _rediscover(self) -> bool:
        """After a lost connection: is the backend back on another port? If so, use it. True when switched.

        A restarted backend publishes a new endpoint file (port and key). The terminal used to keep the old
        address until it was restarted too. Only an address found in that file is followed: one given with
        --url or ECHOSPEAK_URL is the owner's choice.
        """
        if self.endpoint.source in {"flag", "env"}:
            return False
        try:
            found = discovery.discover(env=self.env)
        except Exception:  # noqa: BLE001
            return False
        if found.source != "endpoint-file" or (found.url == self.client.base_url and found.key == self.client.key):
            return False
        try:
            self.client.retarget(found.url, found.key)
        except ValueError:
            return False
        self.endpoint = found
        return True

    def _report(self, exc: BaseException) -> None:
        if isinstance(exc, ConnectError) and self._rediscover():
            self.emit(B.Note(self.caps.glyphs.ok, "the backend restarted; reconnected to it. Send that again.", "dim", 2))
            return
        if isinstance(exc, ApiError):
            self.emit(B.Note(self.caps.glyphs.fail, one_line(exc.detail, 300), "error", 2))
        elif isinstance(exc, ConnectError):
            self.emit(B.Note(self.caps.glyphs.fail, f"cannot reach the backend: {one_line(exc, 200)}", "error", 2))
        elif isinstance(exc, LookupError):
            self.emit(B.Note(self.caps.glyphs.fail, one_line(exc, 300), "error", 2))
        else:
            self.emit(B.Note(self.caps.glyphs.fail, f"{type(exc).__name__}: {one_line(exc, 300)}", "error", 2))

    # ── runs ─────────────────────────────────────────────────────────
    async def send(self, text: str, request_id: str = "", queued: bool = False, display: str = "") -> None:
        if self.running:
            await self._steer(text)
            return
        # Paste chips stay chips in scrollback; the backend gets the full text.
        self.emit(B.UserLine(display or text, queued=queued))
        try:
            sid = await self.call(self.core.ensure_session, CHIP_RE.sub("(pasted text)", display) if display else text)
        except (ApiError, ConnectError) as exc:
            self._report(exc)
            return
        rid = request_id or new_request_id()
        # Mark busy before the network round trip so a second Enter steers instead of racing a 409.
        self._begin_run(rid)
        try:
            first = await self.call(submit, self.client, sid, text, rid, self.core.agent_id)
        except (ApiError, ConnectError) as exc:
            self.run = None
            self._report(exc)
            return
        self._follow(rid, first)
        await self._refresh_title()

    def _begin_run(self, rid: str) -> None:
        self.run_gen += 1
        session_agent = self.core.agent_id or ""
        self.run = Run(rid, self.now(), glyphs=self.caps.glyphs, session_agent=session_agent, verbose=self.verbose)
        self._held_base = self.held
        self.app.invalidate()

    def _follow(self, rid: str, first=None, after: int = 0) -> None:
        gen = self.run_gen
        abandon = threading.Event()
        self.follow_abandon = abandon
        loop = self.loop
        sid = self.sid
        events = self._events

        def put(item) -> None:
            loop.call_soon_threadsafe(events.put_nowait, (gen, item))

        cursor = [after]

        def on_event(evt: dict) -> None:
            if abandon.is_set():
                raise _Abandon()
            try:
                cursor[0] = max(cursor[0], int(evt.get("_replay_seq") or 0))
            except (TypeError, ValueError):
                pass
            put(("event", evt))

        def worker() -> None:
            stream = first
            dead = 0
            while not abandon.is_set():
                try:
                    status, _cursor = follow(self.client, sid, rid, after=cursor[0], first=stream, on_event=on_event,
                                             sleep=lambda s: abandon.wait(s))
                    put(("done", status))
                    return
                except _Abandon:
                    return
                except ConnectError as exc:
                    # A long tool or a waiting approval can leave the stream idle; that is not a lost run.
                    stream = None
                    try:
                        self.client.health()
                        dead = 0
                    except (ConnectError, ApiError):
                        if self._rediscover():
                            # The backend restarted on another port; the run's journal is in the same data folder.
                            put(("notice", "the backend restarted; reconnected"))
                            dead = 0
                            continue
                        dead += 1
                        put(("notice", f"connection lost ({one_line(exc, 80)}); retrying"))
                        if dead >= 5:
                            put(("fail", exc))
                            return
                        abandon.wait(3.0)
                except Exception as exc:  # noqa: BLE001
                    put(("fail", exc))
                    return

        threading.Thread(target=worker, name="echo-follow", daemon=True).start()

    async def _consume(self) -> None:
        assert self._events is not None
        while True:
            gen, (kind, payload) = await self._events.get()
            if gen != self.run_gen or self.run is None:
                continue
            run = self.run
            now = self.now()
            if kind == "event":
                etype = str(payload.get("type") or "")
                blocks = run.feed(payload, now)
                # The run keeps its own full-detail transcript; it joins the session's when the run ends.
                self.emit(*blocks, transcript=False)
                if etype == "token_usage" and run.context:
                    self.context = run.context
                    try:
                        self.session_tokens["prompt"] += int(payload.get("prompt") or 0)
                        self.session_tokens["completion"] += int(payload.get("completion") or 0)
                    except (TypeError, ValueError):
                        pass
                elif etype in {"approval_request", "approval_pending"}:
                    await self._on_approval(payload, verified=not payload.get("_recovered"))
                elif etype == "approval_resolved":
                    aid = str(payload.get("id") or "")
                    self.drop_dialogs(lambda d, aid=aid: d.kind in {"approval", "question"}
                                      and str((d.payload or {}).get("id")) == aid)
                elif etype == "approval_held":
                    self.held = getattr(self, "_held_base", 0) + run.held
                elif etype == "steer_applied":
                    text = str(payload.get("text") or "")
                    for i, p in enumerate(self.pending):
                        if p.kind == "steer" and (p.text.strip() == text.strip() or i == 0):
                            self.pending.pop(i)
                            break
                elif etype == "agent_start":
                    self.app.invalidate()
            elif kind == "notice":
                self.emit(B.Note("·", one_line(payload, 200), "dim", 2))
            elif kind in {"done", "fail"}:
                status = payload if kind == "done" else "failed"
                if kind == "fail":
                    self.emit(B.Note(self.caps.glyphs.fail, f"lost the run: {one_line(payload, 300)}", "error", 0))
                await self._end_run(status)
            self.app.invalidate()

    async def _end_run(self, status: str) -> None:
        run = self.run
        if run is None:
            return
        self.emit(*run.finish(status, self.now()), transcript=False)
        self.transcript.extend(run.transcript)
        self.changes.extend(run.changes)
        if run.final_text:
            self.last_answer = run.final_text
        self.session_tokens["runs"] += 1
        self.run = None
        self.follow_abandon = None
        self.drop_dialogs(lambda d: d.kind in {"approval", "question"})
        steers = [p for p in self.pending if p.kind == "steer"]
        self.pending = [p for p in self.pending if p.kind != "steer"]
        if steers:
            # The backend moves unread steers to the durable queue when the turn ends.
            self.note("Your note arrived after the task finished; it runs next as a follow-up.")
        await self._settings_hint(run)
        await self._refresh_title()
        await self._sync_queue()
        await self._sync_held()
        if status == "completed":
            await self._run_queued()
        elif self.pending:
            self.note("Queued follow-ups are paused after a stop; they run after your next message.")
        await self._run_deferred()

    async def _sync_queue(self) -> None:
        if not self.sid:
            return
        try:
            res = await self.call(self.client.get, "/query/queue", thread_id=self.sid)
        except (ApiError, ConnectError):
            return
        items = (res or {}).get("queue") or []
        self.pending = [p for p in self.pending if p.kind == "steer"] + [
            PendingInput("queue", str(i.get("message") or ""), str(i.get("client_request_id") or "")) for i in items]

    async def _sync_held(self) -> None:
        """Approvals waiting from earlier work (held in autonomous mode, or from a restart)."""
        if not self.sid:
            return
        try:
            items = await self.call(self.client.approvals, self.sid)
        except (ApiError, ConnectError):
            return
        self.held = sum(1 for a in items if a.get("recovered"))

    async def _run_queued(self) -> None:
        if self.running or not self.sid:
            return
        try:
            claimed = await self.call(self.client.request, "POST", "/query/queue/claim", None, {"thread_id": self.sid})
        except (ApiError, ConnectError):
            return
        item = (claimed or {}).get("item")
        if not (claimed or {}).get("claimed") or not item:
            return
        ref = str(item.get("client_request_id") or "")
        self.pending = [p for p in self.pending if p.ref != ref]
        await self.send(str(item.get("message") or ""), request_id=ref if re.match(r"^[A-Za-z0-9._:-]{8,128}$", ref) else "",
                        queued=True)

    async def _run_deferred(self) -> None:
        while self.deferred and not self.running:
            text = self.deferred.pop(0)
            await self._run_command(text)

    async def _steer(self, text: str) -> None:
        if not self.running or not self.sid:
            await self.send(text)
            return
        try:
            await self.call(self.client.steer, self.sid, text)
            self.pending.append(PendingInput("steer", text))
        except ApiError as exc:
            if exc.status not in {400, 409}:
                raise
            await self._queue(text)
        self.app.invalidate()

    async def _queue(self, text: str) -> None:
        if not self.sid:
            await self.send(text)
            return
        res = await self.call(self.client.queue, self.sid, text)
        self.pending.append(PendingInput("queue", text, str((res or {}).get("client_request_id") or "")))
        self.app.invalidate()

    async def _pull_back(self) -> None:
        queued = [p for p in self.pending if p.kind == "queue"]
        if not queued:
            return
        last = queued[-1]
        try:
            await self.call(self.client.request, "DELETE", "/query/queue/" + last.ref, None, {"thread_id": self.sid})
        except ApiError as exc:
            self.say_notice(one_line(exc.detail, 80))
            return
        self.pending.remove(last)
        self.buffer.set_document(Document(last.text, len(last.text)))

    async def _stop(self) -> None:
        run = self.run
        if run is None:
            return
        run.stopping = True
        self.app.invalidate()
        try:
            res = await self.call(self.client.cancel, run.request_id, self.sid)
            if not res.get("cancelled"):
                self.note(f"not running anymore ({one_line(res.get('reason', ''), 80)})")
        except (ApiError, ConnectError) as exc:
            run.stopping = False
            self._report(exc)

    def _detach(self) -> None:
        run = self.run
        if run is None:
            return
        if self.follow_abandon:
            self.follow_abandon.set()
        self.run_gen += 1
        self.emit(*[b for b in run.finish("detached", self.now()) if isinstance(b, (B.MarkdownBlock, B.ThinkingLine))],
                  transcript=False)
        self.transcript.extend(b for b in run.transcript if not isinstance(b, (B.Outcome, B.RuleLine)))
        self.changes.extend(run.changes)
        self.emit(B.Note(self.caps.glyphs.handoff, f"Detached. The task keeps running on the backend · "
                                                   f"echospeak attach {self.sid[:8]} or /resume", "dim", 0))
        self.run = None
        self.drop_dialogs(lambda d: d.kind in {"approval", "question"})
        self.app.invalidate()

    # ── approvals ────────────────────────────────────────────────────
    async def _on_approval(self, evt: dict, verified: bool) -> None:
        aid = str(evt.get("id") or "")
        if not aid or aid in self.prompted:
            return
        if not verified:
            try:
                live = {str(a.get("id")): a for a in await self.call(self.client.approvals, self.sid)}
            except (ApiError, ConnectError):
                live = {}
            if aid not in live:
                return
            evt = {**evt, **live[aid]}
        self.prompted.add(aid)
        self.open_dialog(self._approval_dialog(evt))

    def _agent_label(self, evt: dict) -> str:
        if self.run:
            m = self.run.msgs.get(str(evt.get("message_id") or ""))
            if m:
                return m.name
        return self.agent_name or "Echo"

    def _approval_dialog(self, evt: dict) -> Dialog:
        g = self.caps.glyphs
        agent = self._agent_label(evt)
        is_q = evt.get("kind") == "question" or bool(evt.get("question"))
        body: list[tuple[str, str]] = []
        waiting = 1 + sum(1 for d in self.dialogs if d.kind in {"approval", "question"})
        count = f"  ·  1 of {waiting}" if waiting > 1 else ""
        full = "\n".join(B.args_text(evt.get("args"))) if evt.get("args") else ""
        if is_q:
            body.append(("class:title", one_line(evt.get("question") or evt.get("summary"), 600) + count))
            opts = question_options(evt)
            d = Dialog(title=f"{agent} is asking", options=opts, body=body, kind="question", payload=evt,
                       selected=-1, footer=f"{g.arrow_up_down} choose · enter confirm · esc to leave it for later",
                       full_text=full)
        else:
            args = evt.get("args") if isinstance(evt.get("args"), dict) else {}
            change = DV.parse(evt.get("file_change"))
            command = args.get("command") or args.get("cmd")
            if change is not None:
                # A file tool: the proposed change itself instead of its JSON arguments (edit-preview.md §3.3).
                body.append(("class:title", DV.ask_title(change) + count))
                body.append(("bold", DV.where(change, self.caps)))
                tail = "".join(t for t, _s in DV.counts(change, self.caps))
                note = DV.withheld_note(change)
                if note and not change.hunks:
                    body.append(("class:dim", f"Diff not shown: {note}. Approve once only if you expected it."))
                elif tail:
                    body.append(("class:dim", tail + (f" {g.sep} preview cut at {DV.FULL_ROWS} lines" if change.truncated else "")))
                if change.hunks:
                    body.append((DIFF_MARK, ""))
                full = self._change_text(change, "Proposed change") + ("\n\nArguments\n" + full if full else "")
            else:
                summary = one_line(evt.get("summary") or evt.get("tool"), 300)
                if command and one_line(command, 300)[:40] in summary:
                    # "Running `rm -rf build`" above "$ rm -rf build" says the command twice.
                    summary = "Run this command"
                body.append(("class:title", summary + count))
            if change is not None:
                pass   # the diff replaces the arguments (ctrl+g still shows them)
            elif command:
                for line in sanitize(command).splitlines()[:6] or [""]:
                    body.append(("", f"$ {line}"))
                where = args.get("cwd") or args.get("workdir")
                if where:
                    body.append(("class:dim", f"in {_home(one_line(where, 200))}"))
            elif args:
                lines = B.args_text(args)
                for line in lines[:12]:
                    body.append(("class:dim", line))
                if len(lines) > 12:
                    body.append(("class:dim", f"… {len(lines) - 12} more lines · ctrl+g"))
            if len(str(evt.get("reason") or "").split()) > 2:   # "action tool" says nothing
                body.append(("class:dim", "Why: " + one_line(evt.get("reason"), 400)))
            if evt.get("recovered"):
                body.append(("class:dim", "Waiting from earlier work: only this exact action can be allowed, and the "
                                          "work can resume once you answer."))
            footer = f"y/n or {g.arrow_up_down} then enter · esc: tell {agent} what to do instead"
            if change is not None and change.hunks:
                footer = (f"y/n or {g.arrow_up_down} then enter {g.sep} pgup/pgdn diff {g.sep} ctrl+g full diff "
                          f"{g.sep} esc: tell {agent} instead")
            elif full:
                footer += " · ctrl+g full arguments"
            d = Dialog(title=f"{agent} needs your OK", options=approval_options(evt, agent), body=body, kind="approval",
                       payload=evt, selected=-1, escape_value="__steer__", full_text=full, footer=footer,
                       change=change)
        d.text_prompt = f"Tell {agent} what to do instead:" if not is_q else "Your answer:"

        async def decided(action: Action) -> None:
            await self._decide(evt, action, is_q)

        d.on_result = decided
        return d

    def _change_text(self, change: DV.Change, title: str = "") -> str:
        """A whole change as ANSI text for the pager: header, then every row the backend sent."""
        width = self.width()
        out = B.render(B.FileDiff(change, "ok", 0, full=True), width, self.caps, verbose=True)
        if title:
            out = B.render(B.Note("", title, "bold", 0), width, self.caps) + out
        return out

    async def _decide(self, evt: dict, action: Action, is_q: bool) -> None:
        aid = str(evt.get("id") or "")
        if action.kind == "cancel":
            self.note(("Left the question" if is_q else "Left the approval") + " waiting · /approvals answers it")
            self.prompted.discard(aid)
            return
        if is_q:
            answer = action.text if action.kind == "text" else action.value
            if not answer:
                return
            await self._send_decision(aid, str(answer))
            return
        value = action.value
        if value == "steer_deny":
            try:
                # Shown when the run reads it ("↳ You added: …"), from the backend's steer_applied event.
                await self.call(self.client.steer, self.sid, action.text)
            except (ApiError, ConnectError) as exc:
                self._report(exc)
            value = "deny"
        if value not in {"allow", "always", "persist", "deny"}:
            return
        await self._send_decision(aid, value)

    async def _send_decision(self, aid: str, decision: str) -> None:
        try:
            res = await self.call(self.client.decide, aid, decision)
        except ApiError as exc:
            if exc.status == 404:
                self.note("That approval was already answered elsewhere.")
                return
            self._report(exc)
            self.prompted.discard(aid)
            return
        if res.get("resume_work_id"):
            if decision == "allow":
                self._spawn(self._offer_resume(str(res["resume_work_id"])))
            else:
                self.note("Recorded. The paused work stays paused.")
        if not self.running:
            # Answered outside a followed run (e.g. /approvals): say so, the run's own stream won't.
            word = {"allow": "Allowed once", "always": "Allowed for this chat", "persist": "Always allowed",
                    "deny": "Denied"}.get(decision, f"Answered: {one_line(decision, 100)}")
            self.emit(B.Note(self.caps.glyphs.ok if decision != "deny" else self.caps.glyphs.denied, word,
                             "dim" if decision != "deny" else "bold", 2))
            await self._sync_held()

    async def _offer_resume(self, work_id: str) -> None:
        """A held or recovered approval was allowed: the paused work can continue now (POST /lean/work/{id})."""
        act = await self.ask("Resume the paused work now?", [Option("Yes, resume it now", True, "y"),
                                                             Option("Later (it stays paused)", False, "n")],
                             body=[("", f"Work {one_line(work_id, 60)} was waiting for this approval.")],
                             selected=-1, kind="confirm", footer="y/n or ↑↓ then enter · esc later")
        if not (act.kind == "choose" and act.value is True):
            self.note("The work stays paused; it resumes from the app's Activity view or the next time you allow it.")
            return
        works = (await self.call(self.client.get, "/lean/work", session_id=self.sid) or {}).get("works") or []
        row = next((w for w in works if w.get("id") == work_id), None)
        if row is None:
            self.note("That work is no longer waiting.")
            return
        self.note("Resuming the paused work… (it runs on the backend; the answer appears here when it is done)")
        slow = Client(self.client.base_url, self.client.key, timeout=1800)
        try:
            res = await self.call(slow.post, f"/lean/work/{work_id}", {"action": "resume", "revision": int(row.get("revision") or 0),
                                                                     "session_id": str(row.get("session_id") or self.sid)})
        except (ApiError, ConnectError) as exc:
            self._report(exc)
            return
        text = sanitize((res or {}).get("response") or "")
        if text.strip():
            self.emit(B.MarkdownBlock(text, bullet=True))
        ok = bool((res or {}).get("success"))
        self.note("The resumed work ended" + ("" if ok else " with an error") + "; the reply above is what it reported.",
                  "bold", self.caps.glyphs.ok if ok else self.caps.glyphs.fail)
        await self._sync_held()

    # ── slash commands ───────────────────────────────────────────────
    def _dispatch(self, text: str, record: str = "") -> None:
        name = text.split()[0].lower() if text.split() else ""
        cmd = lookup(name, self.commands)
        if cmd is None:
            self.note(f"unknown command {one_line(name, 40)} · /help lists them")
            return
        if self.running and not cmd.live:
            self.deferred.append(text)
            self.say_notice(f"{cmd.name} runs after this turn", 3)
            return
        self._spawn(self._run_command(text))

    async def _run_command(self, text: str) -> None:
        try:
            parts = shlex.split(text)
        except ValueError:
            parts = text.split()
        name, args = parts[0].lower(), parts[1:]
        cmd = lookup(name, self.commands)
        handler = getattr(self, "cmd_" + (cmd.name[1:] if cmd else ""), None)
        if handler is None:
            self.note(f"unknown command {one_line(name, 40)}")
            return
        # Echo the command into scrollback (as Claude Code does), so its output never looks spontaneous.
        self.emit(B.UserLine(text))
        try:
            await handler(args)
        except (ApiError, ConnectError, LookupError) as exc:
            self._report(exc)

    async def cmd_help(self, args) -> None:
        rows = [[c.name + (" " + c.args if c.args else ""), c.desc + ("" if c.live else "")] for c in self.commands]
        self.emit(B.Table(rows, title="Commands"))
        self.emit(B.Table([[k, v] for k, v in KEYS_HELP], title="Keys"))
        self.emit(B.Note("", "During a task, /stop /status /usage /approvals /transcript /diff /preview /verbose /copy /help /detach /quit "
                             "run at once; other commands wait for the turn to end. Start a message with // to send a "
                             "literal slash.", "dim", 2))

    async def cmd_quit(self, args) -> None:
        self.app.exit()

    async def cmd_clear(self, args) -> None:
        self.core.session = None
        self.context = None
        self._out.append("\x1b[2J\x1b[H")
        if self._out_event:
            self._out_event.set()
        await asyncio.sleep(0.06)
        self.emit(self._banner())

    async def cmd_new(self, args) -> None:
        if "--agent" in args and args.index("--agent") + 1 < len(args):
            await self._set_agent(args[args.index("--agent") + 1], announce=False)
        self.core.session = None
        self.context = None
        who = self.agent_name
        self.emit(B.RuleLine(f"New session with {who}" if self.core.agent_id else "New session"))
        self.app.invalidate()

    async def cmd_verbose(self, args) -> None:
        self.verbose = not self.verbose
        if self.run:
            self.run.verbose = self.verbose
        self.note(f"verbose {'on: full tool output and thinking' if self.verbose else 'off'}")

    async def cmd_copy(self, args) -> None:
        text = self.last_answer
        if not text:
            self.note("nothing to copy yet")
            return
        payload = base64.b64encode(text.encode("utf-8")).decode("ascii")
        self._out.append(f"\x1b]52;c;{payload}\x07")
        if self._out_event:
            self._out_event.set()
        self.note(f"copied the last answer ({len(text):,} chars) · in tmux this needs `set -g set-clipboard on`")

    async def cmd_transcript(self, args) -> None:
        await self._pager()

    # ── /diff and /preview (edit-preview.md §3.4, §3.5) ──────────────
    def _session_changes(self) -> list[DV.Change]:
        return list(self.changes) + (list(self.run.changes) if self.run else [])

    async def cmd_diff(self, args) -> None:
        want = (args[0].lower() if args else "")
        root = await self.call(self._git_root) if want != "session" else ""
        if want == "git" and not root:
            self.note("not a git repository here · /diff session shows this session's edits")
            return
        if root:
            text = await self.call(git_diff_text, root, self.width())
            if text.strip():
                await self._page_text(text, end=False)
                return
            if want == "git":
                self.note(f"no uncommitted changes in {_home(root)}")
                return
        text = await self._session_diff_text()
        if not text:
            self.note("no file changes in this session yet" + (f" (and none uncommitted in {_home(root)})" if root else ""))
            return
        await self._page_text(text, end=False)

    def _git_root(self) -> str:
        for base in (self.project_root, os.getcwd()):
            if base and Path(base).is_dir():
                root = git_toplevel(base)
                if root:
                    return root
        return ""

    async def _session_diff_text(self) -> str:
        """This session's recorded changes: from the run journal when it answers, else what this terminal saw."""
        from .preview import Model, collect

        model: Optional[Model] = None
        if self.sid:
            try:
                model = await self.call(collect, self.client, self.sid)
            except (ApiError, ConnectError):
                model = None
        changes = [e.change for e in sorted(model.entries, key=lambda e: e.order) if e.status == "applied"] if model else []
        if self.run:
            have = {x.change_id for x in changes}
            changes += [c for c in self.run.changes if c.change_id not in have]
        if not changes:
            changes = self._session_changes()
        if not changes:
            return ""
        return session_diff_text(changes, self.width(), self.caps)

    async def cmd_preview(self, args) -> None:
        if not self.env.get("TMUX") or not shutil.which("tmux"):
            self.note("/preview opens a live diff pane beside this one inside tmux; showing /diff in the pager instead")
            await self.cmd_diff([])
            return
        if self.preview_pane and await self.call(tmux_pane_alive, self.preview_pane):
            await self.call(tmux, "kill-pane", "-t", self.preview_pane)
            self.preview_pane = ""
            self.note("preview pane closed")
            return
        if not self.sid:
            # Opening the preview first is natural; start the session now so the pane has one to follow.
            await self.call(self.core.ensure_session, "New session")
            await self._refresh_title()
        here = self.env.get("TMUX_PANE", "")
        width = await self.call(tmux_pane_width, here)
        if width and width < PREVIEW_MIN_COLS:
            self.note(f"Widen to {PREVIEW_MIN_COLS} columns for the preview pane (this one has {width}), or use /diff")
            return
        command, env = preview_command(self.sid, self.endpoint, self.client, self.env)
        try:
            pane = await self.call(tmux_split, here, command, env)
        except (OSError, subprocess.SubprocessError) as exc:
            self.note(f"tmux could not open the pane ({one_line(exc, 120)}) · /diff shows the changes in the pager")
            return
        self.preview_pane = pane
        self.note("preview pane open on the right · it follows this session's edits · /preview again closes it")

    async def cmd_stop(self, args) -> None:
        if self.running:
            await self._stop()
            return
        if self.sid:
            runs = [r for r in await self.call(self.client.runs, self.sid) if r.get("status") == "running"]
            if runs:
                res = await self.call(self.client.cancel, runs[0]["id"], self.sid)
                self.note("stopping…" if res.get("cancelled") else "nothing to stop")
                return
        self.note("nothing is running in this session")

    async def cmd_detach(self, args) -> None:
        if self.running:
            self._detach()
        else:
            self.note("nothing to detach from")

    async def cmd_status(self, args) -> None:
        c = self.client
        health = await self.call(c.health)
        rows = [["backend", f"{c.base_url} · v{one_line(health.get('version'), 20)} · found via {self.endpoint.source}"]]
        if self.data_dir:
            rows.append(["data", _home(self.data_dir)])
        rows.append(["model", self._model_line()])
        rows.append(["agent", f"{self.agent_name} · {self.agent_title}" if self.agent_title else self.agent_name])
        rows.append(["session", f"{self.sid[:8]} · {self._title()}" if self.sid else "new (starts with your next message)"])
        rows.append(["project", self.project_name or "none"])
        persona = next((a for a in self.agents if a.get("id") == (self.core.agent_id or "echo")), {})
        toolsets = persona.get("toolsets")
        if isinstance(toolsets, list) and toolsets:
            rows.append(["configured toolsets", ", ".join(one_line(t, 32) for t in toolsets)])
        rows.append(["approvals", control.APPROVAL_MODES.get(self.approval_mode, self.approval_mode)])
        try:
            terminal = await self.call(c.get, "/lean/terminal") or {}
            if terminal.get("resolved") in {"host", "docker", "bwrap"}:
                rows.append(["command environment", terminal["resolved"] + " (when terminal access is allowed)"])
        except (ApiError, ConnectError):
            pass
        mode = str(self.settings.get("assistant_mode") or "")
        if mode:
            rows.append(["assistant", mode])
        try:
            stop = await self.call(c.get, "/lean/stop-status") or {}
            if stop.get("paused"):
                rows.append(["STOPPED", one_line(stop.get("reason"), 80)])
        except ApiError:
            pass
        waiting = await self.call(c.approvals)
        rows.append(["waiting", f"{len(waiting)} approval(s)" + (" · /approvals" if waiting else "")])
        if self.running:
            rows.append(["running", f"this session · {self.run.request_id}"])
        self.emit(B.Table(rows, title="Status"))

    async def cmd_usage(self, args) -> None:
        t = self.session_tokens
        rows = [["this terminal", f"{t['runs']} run(s) · {M.fmt_tokens(t['prompt'])} prompt · "
                                  f"{M.fmt_tokens(t['completion'])} completion tokens"]]
        if self.context:
            prompt, limit = self.context
            rows.append(["context", f"{round(100 * prompt / limit)}% used ({M.fmt_tokens(prompt)} of {M.fmt_tokens(limit)}) · "
                                    f"{100 - round(100 * prompt / limit)}% left"])
        else:
            rows.append(["context", "not measured yet in this session"])
        self.emit(B.Table(rows, title="Usage"))

    async def cmd_sessions(self, args) -> None:
        self.open_sessions()

    async def cmd_resume(self, args) -> None:
        if args:
            session = await self.call(self.core.find_session, args[0])
            await self._switch(session)
            return
        self.open_sessions()

    async def cmd_agents(self, args) -> None:
        await self._load_agents()
        opts = [Option("Echo", "-", hint="Personal agent · the default")]
        for a in self.agents:
            if a.get("id") in {"echo"}:
                continue
            model = a.get("model") or {}
            m = "/".join(x for x in (model.get("provider"), model.get("model_id")) if x) or "session model"
            opts.append(Option(one_line(a.get("name"), 30), str(a.get("id")), hint=f"{one_line(a.get('title'), 30)} · {m}"))
        current = next((i for i, o in enumerate(opts) if o.value == (self.core.agent_id or "-")), 0)
        action = await self.ask("Agents · talk to one", opts, selected=current,
                                footer=f"{self.caps.glyphs.arrow_up_down} choose · enter to talk to it · esc cancel")
        if action.kind == "choose":
            await self._set_agent(action.value)

    async def cmd_agent(self, args) -> None:
        if not args:
            self.note(f"talking to {self.agent_name}" + (f" · {self.agent_title}" if self.agent_title else "")
                      + " · /agents to choose")
            return
        await self._set_agent(args[0])

    async def _set_agent(self, ref: str, announce: bool = True) -> None:
        await self._load_agents()
        ref_l = ref.lower().lstrip("@")
        if ref_l in {"-", "none", "echo", "chat", "default"}:
            changed = bool(self.core.agent_id)
            self.core.agent_id = ""
            self.agent_name, self.agent_title = "Echo", "Personal agent"
            if changed and (self.core.session or {}).get("environment") == "agents":
                self.core.session = None
            self._sync_default_identity()
            if announce:
                self.note(f"talking to {self.agent_name}" + (" · your next message starts a new session" if changed and not self.sid else ""))
            self.app.invalidate()
            return
        hit = next((a for a in self.agents if str(a.get("id")).lower() == ref_l or str(a.get("name")).lower() == ref_l), None)
        if hit is None:
            names = ", ".join(one_line(a.get("name"), 20) for a in self.agents)
            raise LookupError(f"no agent named {ref!r} (agents: {names})")
        self.core.agent_id = str(hit.get("id"))
        self.agent_name = one_line(hit.get("name"), 30)
        self.agent_title = one_line(hit.get("title"), 30)
        # An agent answers in its own direct session, as the app's Agents view does.
        self.core.session = None
        self.context = None
        if announce:
            self.emit(B.AgentHeader(self.core.agent_id, self.agent_name, self.agent_title))
            self.note(f"Your next message starts a direct session with {self.agent_name} · /agent - for Echo")
        self.app.invalidate()

    async def cmd_project(self, args) -> None:
        if args and args[0] == "-":
            if self.sid:
                await self.call(self.client.request, "POST", "/projects/deactivate", None, {"thread_id": self.sid})
            self.core.project_id, self.project_name = "", ""
            self.note("project detached from this session")
            self.app.invalidate()
            return
        ref = args[0] if args else os.getcwd()
        path = Path(ref).expanduser()
        if not (os.sep in ref or ref.startswith((".", "~")) or path.is_dir()):
            project = await self.call(self.client.get, "/projects/" + ref)
            await self._activate_project(project)
            return
        target = str(path.resolve())
        existing = await self.call(self.core.project_for_path, target, False)
        if existing:
            project = await self.call(self.client.get, "/projects/" + existing)
            await self._activate_project(project)
            return
        body = [("", _home(target)),
                ("class:dim", "Echo will be able to read, edit and run commands in this folder, still within your "
                              "permission switches and approvals. Only attach folders you trust.")]
        action = await self.ask("Trust this folder?", [Option("No, cancel", False, "n"), Option("Yes, I trust this folder", True, "y")],
                                body=body, attention=True, selected=-1, kind="confirm",
                                footer="y/n or ↑↓ then enter · esc cancel")
        if action.kind != "choose" or action.value is not True:
            self.note("not attached")
            return
        await self.call(control.forget_folder_decline, target, self.env)
        project = await self.call(self.client.post, "/projects/attach-folder",
                                  {"path": target, "trust_state": "trusted", "session_id": self.sid})
        await self._activate_project(project, activated=bool(self.sid))

    async def _activate_project(self, project: dict, activated: bool = False) -> None:
        pid = str(project.get("id") or "")
        if self.sid and not activated:
            await self.call(self.client.request, "POST", f"/projects/{pid}/activate", None, {"thread_id": self.sid})
        self.core.project_id = pid
        self.project_name = one_line(project.get("name") or "", 40)
        self.project_root = str(project.get("workspace_root") or "")
        trust = str(project.get("trust_state") or "")
        extra = "" if trust == "trusted" else " · untrusted: Echo can't edit or run anything there"
        self.note(f"project {self.project_name} ({_home(one_line(project.get('workspace_root'), 120))}){extra}"
                  + ("" if self.sid else " · applies from your next message"))
        self.app.invalidate()

    async def cmd_mode(self, args) -> None:
        current = str(self.settings.get("assistant_mode") or "unified")
        if args:
            choice = args[0].lower()
            if choice not in control.ASSISTANT_MODES:
                raise LookupError("modes: unified, environments")
        else:
            opts = [Option(k, k, hint=v + (" (current)" if k == current else "")) for k, v in control.ASSISTANT_MODES.items()]
            action = await self.ask("Assistant mode (global: the app uses it too)", opts,
                                    selected=list(control.ASSISTANT_MODES).index(current) if current in control.ASSISTANT_MODES else 0)
            if action.kind != "choose":
                return
            choice = action.value
        if choice == current:
            self.note(f"assistant mode is already {choice}")
            return
        await self.call(control.put_setting, self.client, "assistant_mode", choice)
        await self._load_settings()
        self.note(f"assistant mode: {choice} · {control.ASSISTANT_MODES[choice]}")

    # ── settings and permissions (cli-design.md §10) ──────────────────
    async def cmd_settings(self, args) -> None:
        """One place for what the app's Settings changes most: each section opens, and Esc comes back here."""
        selected = 0
        while True:
            await self._load_settings()
            await self._load_agents()
            s = self.settings
            allowed = sum(1 for k, _l, _d in control.PERMISSIONS if control.switch_state(s, k) == "on")
            model = self.model or control.current_model(s)[1] or "not chosen"
            privacy = str(s.get("privacy_mode") or "standard")
            search = str(s.get("web_search_provider") or "auto")
            mode = str(s.get("assistant_mode") or "unified")
            sections = [
                ("permissions", "Permissions", f"approval: {self.approval_mode} · {allowed} of {len(control.PERMISSIONS)} "
                                               "action switches on"),
                ("agents", "Agents and their tools", "what " + (", ".join(one_line(x.get("name"), 20) for x in self.agents
                                                                   if x.get("name")) or "each agent") + " may use"),
                ("model", "Model", model),
                ("privacy", "Privacy", f"{privacy}: {control.PRIVACY_MODES.get(privacy, '')}"),
                ("search", "Web search", f"{search}: {control.SEARCH_PROVIDERS.get(search, '')}"),
                ("mode", "Assistant mode", mode),
            ]
            opts = [Option(label, sid, hint=hint) for sid, label, hint in sections]
            action = await self.ask("Settings", opts, selected=selected,
                                    body=[("class:dim", "Global: the desktop app and every agent use these.")],
                                    footer=f"{self.caps.glyphs.arrow_up_down} choose · enter open · esc close")
            if action.kind != "choose":
                return
            selected = next(i for i, (sid, _l, _h) in enumerate(sections) if sid == action.value)
            if action.value == "permissions":
                await self.cmd_permissions([])
            elif action.value == "agents":
                await self.cmd_tools([])
            elif action.value == "model":
                await self.cmd_model([])
            elif action.value == "privacy":
                await self._pick_setting("privacy_mode", "Privacy", control.PRIVACY_MODES, privacy)
            elif action.value == "search":
                await self._pick_setting("web_search_provider", "Web search", control.SEARCH_PROVIDERS, search)
            elif action.value == "mode":
                await self.cmd_mode([])

    async def _pick_setting(self, key: str, title: str, choices: dict, current: str) -> None:
        names = list(choices)
        opts = [Option(name, name, hint=desc + (" · current" if name == current else "")) for name, desc in choices.items()]
        action = await self.ask(title, opts, selected=names.index(current) if current in names else 0,
                                footer=f"{self.caps.glyphs.arrow_up_down} choose · enter use it · esc back")
        if action.kind != "choose" or action.value == current:
            return
        await self.call(control.put_setting, self.client, key, action.value)
        await self._load_settings()
        self.note(f"{title.lower()}: {action.value}", "bold", self.caps.glyphs.ok)

    def _switch_row(self, key: str, label: str, desc: str) -> Option:
        state = control.switch_state(self.settings, key)
        mark = {"on": "[on] ", "off": "[off]", "blocked": "[on] "}[state]
        hint = desc if state != "blocked" else f"blocked: {control.switch_label(control.MASTER)} is off"
        return Option(f"{mark} {label}", ("switch", key), hint=hint,
                      style="attention" if state == "blocked" else "" if state == "on" else "dim")

    async def cmd_permissions(self, args) -> None:
        """The approval mode and the action switches. The list stays open, so several can change in a row.

        ``/permissions <mode>`` sets the approval mode; ``/permissions <switch> on|off`` a switch ("files", "terminal").
        """
        if args and args[0].lower() in control.APPROVAL_MODES and len(args) == 1:
            await self._set_approval_mode(args[0].lower(), confirm=True)
            return
        if args:
            key = control.switch_key(" ".join(a for a in args if a.lower() not in {"on", "off"}))
            want = {"on": True, "off": False}.get(args[-1].lower())
            if key is None or want is None:
                raise LookupError("usage: /permissions [smart|autonomous|always|never] · "
                                  "/permissions <files|terminal|actions|apps|browser|desktop|self> on|off")
            await self._load_settings()
            await self._change_switch(key, want)
            return
        selected = 0
        while True:
            await self._load_settings()
            opts = [Option(f"Approval mode: {self.approval_mode}", ("mode", None),
                           hint=control.APPROVAL_MODES.get(self.approval_mode, ""))]
            opts += [self._switch_row(key, label, desc) for key, label, desc in control.PERMISSIONS]
            action = await self.ask("Permissions", opts, selected=selected,
                                    body=[("class:dim", "Global: the desktop app and every agent use these. "
                                                        "Agents still ask before risky actions.")],
                                    footer=f"{self.caps.glyphs.arrow_up_down} choose · enter to change (asks first) · "
                                           "esc back")
            if action.kind != "choose":
                return
            kind, key = action.value
            selected = next(i for i, o in enumerate(opts) if o.value == action.value)
            if kind == "mode":
                modes = list(control.APPROVAL_MODES)
                mopts = [Option(m, m, hint=control.APPROVAL_MODES[m]) for m in modes]
                act = await self.ask("Approval mode", mopts,
                                     selected=modes.index(self.approval_mode) if self.approval_mode in modes else 0)
                if act.kind == "choose":
                    await self._set_approval_mode(act.value, confirm=True)
                continue
            await self._change_switch(key, control.switch_state(self.settings, key) == "off" or
                                      (control.switch_state(self.settings, key) == "blocked"))

    async def _change_switch(self, key: str, on: bool) -> None:
        """Turn a switch on (with the master switch it needs, after asking) or off (at once: turning off is safe)."""
        label = control.switch_label(key)
        desc = next((d for k, _l, d in control.PERMISSIONS if k == key), "")
        if not on:
            if not self.settings.get(key):
                self.note(f"{label} is already off")
                return
            await self.call(control.put_setting_many, self.client, {key: False})
            await self._load_settings()
            extra = " · every action on this computer is off now" if key == control.MASTER else ""
            self.note(f"{label}: off{extra}", "bold")
            return
        plan = control.enable_plan(self.settings, key)
        if not plan:
            self.note(f"{label} is already on")
            return
        body = [("", f"{label}: {desc}.")]
        if len(plan) > 1:
            body.append(("", f"It needs {control.switch_label(control.MASTER)}, which is off, so both will be turned on."))
        body.append(("class:dim", "Global: the desktop app and every agent use it. Agents still ask before risky actions."))
        act = await self.ask(f"Turn on {label}?", [Option("No, keep it off", False, "n"), Option("Yes, turn it on", True, "y")],
                             body=body, attention=True, selected=-1, kind="confirm",
                             footer="y/n or ↑↓ then enter · esc cancel")
        if not (act.kind == "choose" and act.value is True):
            self.note("unchanged")
            return
        await self.call(control.put_setting_many, self.client, {k: True for k in plan})
        await self._load_settings()
        self.note(" · ".join(f"{control.switch_label(k)}: on" for k in plan), "bold", self.caps.glyphs.ok)

    async def cmd_tools(self, args) -> None:
        """What an agent may use: its tool groups, toggled one at a time (PATCH /lean/agents/{id})."""
        await self._load_agents()
        agents = [a for a in self.agents if a.get("id")]
        if not agents:
            raise LookupError("no agents to configure")
        if args:
            ref = " ".join(args).lower().lstrip("@")
            agent = next((a for a in agents if str(a.get("id")).lower() == ref or str(a.get("name")).lower() == ref), None)
            if agent is None:
                raise LookupError(f"no agent named {' '.join(args)!r} (agents: "
                                  f"{', '.join(one_line(a.get('name'), 20) for a in agents)})")
            await self._agent_tools(str(agent["id"]))
            return
        selected = 0
        while True:
            await self._load_agents()
            agents = [a for a in self.agents if a.get("id")]
            opts = [Option(one_line(a.get("name"), 30), str(a.get("id")),
                           hint=f"{one_line(a.get('title'), 30)} · "
                                + ", ".join(control.TOOLSET_INFO.get(t, t).split(" and ")[0].split(",")[0]
                                            for t in a.get("toolsets") or []))
                    for a in agents]
            action = await self.ask("Agents and their tools", opts, selected=selected,
                                    footer=f"{self.caps.glyphs.arrow_up_down} choose · enter to change its tools · esc back")
            if action.kind != "choose":
                return
            selected = next(i for i, o in enumerate(opts) if o.value == action.value)
            await self._agent_tools(action.value)

    async def _agent_tools(self, agent_id: str) -> None:
        selected = 0
        while True:
            await self._load_agents()
            await self._load_settings()
            agent = next((a for a in self.agents if str(a.get("id")) == agent_id), None)
            if agent is None:
                return
            try:
                available = [str(t.get("id")) for t in (await self.call(self.client.get, "/lean/toolsets") or {})
                             .get("toolsets") or [] if t.get("id")]
            except ApiError:
                available = list(control.TOOLSET_ORDER)
            have = [str(t) for t in agent.get("toolsets") or []]
            rows = control.toolset_rows(have, available, self.settings)
            opts = [Option(f"{'[on] ' if r['on'] else '[off]'} {r['label']}", r["id"],
                           hint=(f"also needs: {r['needs']}" if r["needs"] and r["on"] else ""),
                           style="" if r["on"] else "dim") for r in rows]
            name = one_line(agent.get("name"), 30)
            action = await self.ask(f"{name}'s tools", opts, selected=selected,
                                    body=[("class:dim", f"What {name} may use. The global switches in /permissions still "
                                                        "apply to every agent.")],
                                    footer=f"{self.caps.glyphs.arrow_up_down} choose · enter to turn on or off · esc back")
            if action.kind != "choose":
                return
            tid = action.value
            selected = next(i for i, o in enumerate(opts) if o.value == tid)
            turning_on = tid not in have
            label = control.TOOLSET_INFO.get(tid, tid)
            if turning_on and tid in control.TOOLSET_POWERFUL:
                needs = next((r["needs"] for r in rows if r["id"] == tid), "")
                body = [("", f"{name} could use: {label}.")]
                if needs:
                    body.append(("", f"It also needs {needs} in /permissions before anything runs."))
                body.append(("class:dim", f"{name} still asks before risky actions."))
                act = await self.ask(f"Let {name} use {label.lower()}?",
                                     [Option("No", False, "n"), Option("Yes", True, "y")],
                                     body=body, attention=True, selected=-1, kind="confirm",
                                     footer="y/n or ↑↓ then enter · esc cancel")
                if not (act.kind == "choose" and act.value is True):
                    continue
            new = have + [tid] if turning_on else [t for t in have if t != tid]
            await self.call(self.client.request, "PATCH", f"/lean/agents/{agent_id}", {"toolsets": new})
            self.note(f"{name}: {label} {'on' if turning_on else 'off'}", "bold", self.caps.glyphs.ok)

    async def _settings_hint(self, run) -> None:
        """After a run: one line when the answer or a step says a permission blocked it and that switch is off."""
        texts = [run.final_text] + [t.summary for t in run.transcript if isinstance(t, B.ToolLine) and t.status != "ok"]
        texts += [b.text for b in run.transcript if isinstance(b, B.Note)]
        if not any(texts):
            return
        try:
            settings = await self.call(control.settings_of, self.client)
        except (ApiError, ConnectError):
            return
        hint = control.blocked_hint(texts, settings)
        if hint:
            self.emit(B.Note("!", hint, "attention", 0))   # "!" as other notes that need you; ⚙ draws as a wide emoji

    async def _set_approval_mode(self, mode: str, confirm: bool) -> None:
        if mode == self.approval_mode:
            self.note(f"approval mode is already {mode}")
            return
        if mode == "never":
            body = [("", control.APPROVAL_MODES["never"] + "."),
                    ("class:dim", "Agents will act without asking you, everywhere (the app too). Type never to confirm.")]
            act = await self.ask("Turn approvals off?", [Option("Type never to confirm", "never", text_entry=True),
                                                        Option("Cancel", None, "n")],
                                 body=body, attention=True, selected=0, kind="confirm", text_prompt="Type never:")
            if not (act.kind == "text" and act.text.strip().lower() == "never"):
                self.note("approval mode unchanged")
                return
        elif confirm:
            act = await self.ask(f"Change the approval mode to {mode}?",
                                 [Option("No", False, "n"), Option("Yes, change it", True, "y")],
                                 body=[("", control.APPROVAL_MODES[mode] + "."),
                                       ("class:dim", "Global: the app uses it too.")],
                                 attention=mode != "always", selected=-1, kind="confirm",
                                 footer="y/n or ↑↓ then enter · esc cancel")
            if not (act.kind == "choose" and act.value is True):
                self.note("approval mode unchanged")
                return
        await self.call(control.put_setting, self.client, "lean_approval_mode", mode)
        await self._load_settings()
        self.say_notice(f"approval mode: {self.approval_mode}", 2.5)

    async def _cycle_mode(self) -> None:
        mode = control.next_approval_mode(self.approval_mode)
        try:
            await self.call(control.put_setting, self.client, "lean_approval_mode", mode)
        except (ApiError, ConnectError) as exc:
            self._report(exc)
            return
        await self._load_settings()
        self.say_notice(f"approval mode: {control.APPROVAL_MODES.get(self.approval_mode, self.approval_mode).split(':')[0]}", 2.0)

    async def cmd_approvals(self, args) -> None:
        items = await self.call(self.client.approvals, self.sid) if self.sid else await self.call(self.client.approvals)
        if not items and self.sid:
            items = await self.call(self.client.approvals)
        if not items:
            self.note("no approvals waiting")
            return
        for item in items:
            self.prompted.discard(str(item.get("id")))
            await self._on_approval(item, verified=True)

    async def cmd_memory(self, args) -> None:
        query = " ".join(args).strip().lower()
        params = {"limit": 200}
        if self.sid:
            params["thread_id"] = self.sid
        res = await self.call(self.client.get, "/memory", **params)
        items = (res or {}).get("items") or []
        if query:
            words = query.split()
            items = [i for i in items if all(w in str(i.get("text") or "").lower() for w in words)]
        if not items:
            self.note("nothing remembered yet" if not query else f"nothing in memory matches {query!r}")
            return
        rows = [["type", "remembered", "when"]]
        for i in items[:25]:
            rows.append([one_line(i.get("memory_type") or i.get("scope") or "", 12), one_line(i.get("text"), 160),
                         one_line(i.get("timestamp") or "", 10)[:10]])
        title = f"Memory · {len(items)} item(s)" + (f" matching {query!r}" if query else "")
        self.emit(B.Table(rows, header=True, title=title))

    async def cmd_model(self, args) -> None:
        await self._load_settings()
        models, base = await self._model_choices()
        if args:
            name = args[0]
            if models and name not in [m.name for m in models]:
                raise LookupError(f"{name} is not installed (available: {', '.join(m.name for m in models)})")
            await self._apply_model(name, base)
            return
        if not models:
            self.note("no models found; is Ollama running? (ollama list)")
            return
        current = self.model
        opts = [Option(m.name, m.name, hint=(m.hint or "") + (" · current" if m.name == current else ""),
                       style="dim" if m.fits is False else "") for m in models]
        sel = next((i for i, m in enumerate(models) if m.name == current), 0)
        action = await self.ask("Choose a model", opts, selected=sel,
                                footer=f"{self.caps.glyphs.arrow_up_down} choose · enter select · esc cancel")
        if action.kind == "choose":
            await self._apply_model(action.value, base)

    async def _model_choices(self) -> tuple[list[control.ModelInfo], str]:
        provider, _model = control.current_model(self.settings)
        base = control.ollama_base(self.settings)
        if provider in {"", "ollama"}:
            try:
                models = await self.call(control.ollama_models, base)
                vram = await self.call(control.gpu_vram_mib)
                return control.with_hints(models, vram), base
            except (OSError, ValueError):
                pass
        res = await self.call(self.client.get, "/provider/models")
        return [control.ModelInfo(one_line(m, 120)) for m in (res or {}).get("models") or []], str((res or {}).get("base_url") or "")

    async def _apply_model(self, name: str, base: str) -> None:
        provider, _ = control.current_model(self.settings)
        opts = [Option("For this chat and as the default for new chats", "both")] if self.sid else []
        opts.append(Option("As the default for new chats" if self.sid else "Use it (default for new chats)", "default"))
        if self.sid:
            opts.append(Option("For this chat only", "session"))
        action = await self.ask(f"Use {name}", opts, selected=0,
                                footer=f"{self.caps.glyphs.arrow_up_down} choose · enter confirm · esc cancel")
        if action.kind != "choose":
            return
        scope = action.value
        if scope in {"both", "default"}:
            if provider in {"", "ollama"}:
                await self.call(control.save_default_model, self.client, name, base,
                                int((self.settings.get("local") or {}).get("context_length") or control.DEFAULT_CONTEXT))
            else:
                await self.call(control.put_setting, self.client, "local", {"model_name": name})
            await self.call(control.confirm_model, self.data_dir, f"ollama:{name}", self.env)
        if scope in {"both", "session"} and self.sid:
            res = await self.call(control.switch_session_model, self.client, self.sid, provider if provider not in {"", "cloud"} else "ollama", name)
            if res.get("cancelled_turns"):
                self.note("the running task was stopped to switch models")
        await self._load_settings()
        await self._refresh_model()
        where = {"both": "this chat and new chats", "default": "new chats", "session": "this chat"}[scope]
        self.note(f"model: {name} for {where}", "bold")

    # ── session switching ───────────────────────────────────────────
    async def _switch(self, session: dict) -> None:
        if self.running:
            self._detach()
        self.core.session = session
        self.core.project_id = str(session.get("project_id") or "")
        self.project_name = await self._project_name(self.core.project_id)
        # What belonged to the conversation you left stays with it: its context meter, its token
        # count, its file changes and its queued follow-ups are not this one's.
        self.context = None
        self.session_tokens = {"prompt": 0, "completion": 0, "runs": 0}
        self.changes = []
        self.last_answer = ""
        self.pending = []
        self.held = 0
        await self._sync_agent_from_session(session)
        await self._refresh_model()
        who = f" {self.caps.glyphs.sep} {self.agent_name}" if self.core.agent_id else ""
        where = f" {self.caps.glyphs.sep} {self.project_name}" if self.project_name else ""
        self.emit(B.RuleLine(f"Resumed {one_line(session.get('title') or '(untitled)', 60)}{who}{where}"))
        await self._show_recent()
        await self._catch_up()

    async def _sync_agent_from_session(self, session: dict) -> None:
        if session.get("environment") != "agents":
            if self.core.agent_id:
                self.core.agent_id, self.agent_name, self.agent_title = "", "Echo", "Personal agent"
            return
        try:
            rooms = (await self.call(self.client.get, "/lean/rooms") or {}).get("items") or []
        except ApiError:
            return
        room = next((r for r in rooms if r.get("thread_id") == session.get("thread_id")), None)
        if room and room.get("kind") == "direct" and room.get("agent_ids"):
            await self._load_agents()
            aid = str(room["agent_ids"][0])
            hit = next((a for a in self.agents if a.get("id") == aid), None)
            self.core.agent_id = aid
            self.agent_name = one_line((hit or {}).get("name") or aid, 30)
            self.agent_title = one_line((hit or {}).get("title") or "", 30)

    async def _show_recent(self) -> None:
        try:
            hist = (await self.call(self.client.get, "/history", thread_id=self.sid) or {}).get("history") or []
        except (ApiError, ConnectError):
            return
        said = [h for h in hist if isinstance(h, str) and h.startswith(("Human: ", "Assistant: "))]
        tail = said[-RECAP_MESSAGES:]
        if len(said) > len(tail):
            n = len(said) - len(tail)
            self.emit(B.Note(self.caps.glyphs.vdots, f"{n} earlier message{'s' if n != 1 else ''}", "dim", 0))
        for h in tail:
            if h.startswith("Human: "):
                self.emit(B.Note(self.caps.glyphs.prompt + " ", one_line(h[7:], 300), "dim", 0))   # "›  ", as when typed
            elif h.startswith("Assistant: "):
                self.emit(B.Note(self.caps.glyphs.bullet, one_line(plain_recap(h[11:]), 300), "dim", 0))
        if tail:
            self.emit(B.Raw("\n"), transcript=False)

    async def _catch_up(self) -> None:
        if not self.sid:
            return
        runs = [r for r in await self.call(self.client.runs, self.sid) if r.get("status") == "running"]
        if runs:
            self.note(f"following the running task {one_line(runs[0]['id'], 60)}")
            self._begin_run(str(runs[0]["id"]))
            self._follow(str(runs[0]["id"]))
            return
        items = await self.call(self.client.approvals, self.sid)
        live = [i for i in items if not i.get("recovered")]
        for item in live:
            await self._on_approval(item, verified=True)
        self.held = len(items) - len(live)
        await self._sync_queue()

    async def _refresh_title(self) -> None:
        if not self.sid:
            return
        try:
            self.core.session = await self.call(self.client.thread, self.sid)
        except (ApiError, ConnectError):
            pass
        self.app.invalidate()

    # ── backend state ────────────────────────────────────────────────
    async def _load_settings(self) -> None:
        self.settings = await self.call(control.settings_of, self.client)
        mode = str(self.settings.get("lean_approval_mode") or "smart").lower()
        self.approval_mode = mode if mode in control.APPROVAL_MODES else "smart"
        self._sync_default_identity()
        self.app.invalidate()

    async def _load_agents(self) -> None:
        try:
            self.agents = (await self.call(self.client.get, "/lean/agents") or {}).get("items") or []
        except ApiError:
            self.agents = []

    async def _refresh_model(self) -> None:
        provider, model = control.current_model(self.settings)
        try:
            info = await self.call(self.client.get, "/provider", session_id=self.sid or "default") if self.sid else None
        except (ApiError, ConnectError):
            info = None
        if info:
            self.provider, self.model = str(info.get("provider") or ""), str(info.get("model") or "")
        else:
            self.provider, self.model = provider, model
        self._sync_default_identity()
        self.app.invalidate()

    def _sync_default_identity(self) -> None:
        if self.core.agent_id or (self.core.session or {}).get("environment") == "agents":
            return
        if self.settings.get("assistant_mode", "unified") == "environments":
            self.agent_name = one_line((self.model or "Assistant").rsplit("/", 1)[-1], 60)
            self.agent_title = "Code" if (self.core.session or {}).get("environment") == "code" else "Chat"
        else:
            self.agent_name, self.agent_title = "Echo", "Personal agent"

    def _model_line(self) -> str:
        provider, default = control.current_model(self.settings)
        text = f"{self.model or default or 'not chosen'}"
        if self.provider:
            text += f" ({self.provider})"
        if default and self.model and default != self.model:
            text += f" · default for new chats: {default}"
        return text

    async def _project_name(self, pid: str) -> str:
        if not pid:
            return ""
        try:
            return one_line((await self.call(self.client.get, "/projects/" + pid) or {}).get("name"), 40)
        except (ApiError, ConnectError):
            return ""

    async def _probe(self) -> None:
        checks = {"agents": ("/lean/agents", {}), "projects": ("/projects", {}), "settings": ("/settings", {}),
                  "memory": ("/memory", {"limit": 1}), "models": ("/provider/models", {})}
        for name, (path, params) in checks.items():
            try:
                await self.call(self.client.get, path, **params)
                self.features.add(name)
            except (ApiError, ConnectError):
                pass
        self.commands = visible(COMMANDS, self.features)

    # ── Echo's entrance (tui/avatar.py) ──────────────────────────────
    def _animate_welcome(self) -> bool:
        """The entrance plays only where it can look right; everywhere else the banner is printed at once."""
        w, h = self.width(), self.height()
        return (not self.caps.reduced_motion and not self.caps.ascii and w >= L.NARROW and h >= 14
                and str(self.env.get("TERM", "")) != "dumb")

    def _welcome_lines(self) -> list[StyleAndTextTuples]:
        """The banner as it will be printed, with the face at this moment of the entrance."""
        b = self.welcome
        if b is None:
            return []
        t = (self.now() - self.welcome_t0) * 1000 if self.welcome_t0 else 0.0
        face = A.frame(t, self.caps.reduced_motion)
        key = (face, self.width(), id(b))
        if self._welcome_cache is not None and self._welcome_cache[0] == key:
            return self._welcome_cache[1]
        b.face = face
        text = B.render(b, self.width(), self.caps).rstrip("\n")
        b.face = None
        frags = to_formatted_text(ANSI(text))
        lines: list[StyleAndTextTuples] = [[]]
        for style, chunk, *_ in frags:
            parts = chunk.split("\n")
            for i, part in enumerate(parts):
                if i:
                    lines.append([])
                if part:
                    lines[-1].append((style, part))
        self._welcome_cache = (key, lines)
        return lines

    def _welcome_text(self) -> StyleAndTextTuples:
        if self.welcome is not None and not self.welcome_t0:
            self.welcome_t0 = self.now()   # the clock starts on the first frame actually drawn
        rows = self._rows("welcome")
        return self._join(self._welcome_lines()[:rows]) if rows > 0 else []

    def _commit_welcome(self) -> None:
        """Print the banner (Echo at rest) into scrollback where the entrance played, then drop the live copy.

        The live copy stays until the flusher has written the banner, inside the same terminal hand-off, so
        there is never a frame without it (no flicker) and never two of it (no duplicate)."""
        banner = self.welcome
        if banner is None or self._committing_welcome or self._welcome_sent:
            return
        # Once only: several outputs before the flush (a resume prints a rule and its recap at once) each used to
        # commit it again, and the banner was printed between every one of them.
        self._welcome_sent = True
        self._committing_welcome = True
        try:
            self.emit(banner)
        finally:
            self._committing_welcome = False
        if self._out_event is None:
            self.welcome = None
            return

        def drop() -> None:
            if self.welcome is banner:
                self.welcome = None
                self._forget_plan()

        self._after_flush.append(drop)

    async def _welcome_runner(self) -> None:
        """Redraw the entrance at ~30 fps while it plays, then leave Echo at rest in the live region.

        The banner is not committed when the entrance ends: committing hands the terminal back to print it,
        which erases and redraws the whole live region (the flash at the end of the first entrance). It is
        committed by the next output (``emit``), or at exit, both of which redraw anyway; or at once if the
        terminal gets too short to show it.
        """
        while self.welcome is not None:
            elapsed = (self.now() - self.welcome_t0) * 1000 if self.welcome_t0 else 0.0
            if self.welcome_t0 and self._rows("welcome") <= 0:
                self._commit_welcome()
                return
            playing = not (self.welcome_t0 and A.done(elapsed, self.caps.reduced_motion))
            if playing:
                self.app.invalidate()
            await asyncio.sleep(0.033 if playing else 0.25)

    # ── startup ──────────────────────────────────────────────────────
    def _banner(self) -> B.Banner:
        cwd = _home(os.getcwd())
        model = self.model or control.current_model(self.settings)[1] or "no model yet"
        prov = f" ({self.provider})" if self.provider and self.provider not in {"cloud"} else ""
        return B.Banner(version=self.version, agent=self.agent_name, agent_title=self.agent_title, model=model + prov,
                        cwd=cwd, project=self.project_name,
                        mode_label=M.MODE_LABEL.get(self.approval_mode, self.approval_mode),
                        tagline=self._phrases()[0])

    async def _prepare(self) -> Optional[dict]:
        """Everything the banner needs. Runs before the live region exists, so the banner is printed
        straight to the terminal and the region starts below it."""
        await self._load_settings()
        await self._probe()
        await self._load_agents()
        found = discovery.read_endpoint_file(discovery.runtime_dirs(self.env)[0] / "endpoint.json") or {}
        self.data_dir = str(found.get("data_dir") or discovery.data_dir(self.env))
        if self.start_agent:
            await self._set_agent(self.start_agent, announce=False)
        session = None
        last = None
        if self.start_resume:
            session = await self.call(self.core.find_session, self.start_resume)
        elif not self.start_new:
            # A clean start: the latest conversation in this folder's project is offered, not reopened.
            try:
                last = await self.call(self.core.default_session, os.getcwd())
            except (ApiError, ConnectError):
                last = None
            if self.start_continue:
                session, last = last, None
        if self.start_project:
            project = self.start_project
            pid = await self.call(self.core.project_for_path, project, False) if (os.sep in project or Path(project).is_dir()) \
                else project
            if pid:
                self.core.project_id = pid
        elif not session:
            pid = await self.call(self.core.project_for_path, os.getcwd(), False)
            self.core.project_id = pid or ""
        if session:
            self.core.session = session
            self.core.project_id = str(session.get("project_id") or self.core.project_id)
            await self._sync_agent_from_session(session)
        self.project_name = await self._project_name(self.core.project_id)
        await self._refresh_model()
        self.last_here = last
        banner = self._banner()
        sep = f" {self.caps.glyphs.sep} "
        if session:
            banner.resumed = sep.join([f'Resumed “{one_line(session.get("title") or "(untitled)", 50)}”',
                                       _plural_msgs(session.get("message_count")),
                                       f'last active {_ago(session.get("last_active_at"))}', "/new for a fresh one"])
        elif last:
            arrow = "<-" if self.caps.ascii else "←"
            banner.last = sep.join([f'Last here: “{one_line(last.get("title") or "(untitled)", 50)}”',
                                    _plural_msgs(last.get("message_count")), _ago(last.get("last_active_at")),
                                    f"{arrow} to pick it up"])
        if self._animate_welcome():
            self.welcome = banner   # drawn in the live region while Echo arrives, then printed (_commit_welcome)
        else:
            self.emit(banner)
        return session

    async def _after_start(self) -> None:
        try:
            await self._model_setup()
            await self._folder_setup()
            if self.sid:
                await self._show_recent()
                await self._catch_up()
        except (ApiError, ConnectError, LookupError) as exc:
            self._report(exc)

    async def _folder_setup(self) -> None:
        """Work in the folder Echo was started in, like Claude Code and Codex, but only once the owner trusts it.

        Without a project the backend's file tools fall back to its default root, which in a source
        checkout is EchoSpeak itself; the terminal should mean "here".
        """
        if self.core.project_id or self.start_project:
            return
        cwd = os.getcwd()
        existing = await self.call(self.core.project_for_path, cwd, False)
        if existing:
            project = await self.call(self.client.get, "/projects/" + existing)
            if str(project.get("trust_state") or "") == "trusted":
                await self._activate_project(project)
            return
        if not control.folder_offer(cwd, self.env):
            return
        body = [("", _home(str(Path(cwd).resolve()))),
                ("class:dim", "Echo can read, edit and run commands in this folder, still within your permission "
                              "switches and approvals. Say no to just chat; /project changes this later.")]
        action = await self.ask("Work in this folder?", [Option("Yes, I trust this folder", True, "y"),
                                                         Option("No, just chat", False, "n")],
                                body=body, attention=True, selected=-1, kind="confirm",
                                footer="y/n or ↑↓ then enter · esc decide later")
        if action.kind != "choose":
            self.note("not working in this folder yet · /project when you want to")
            return
        if action.value is not True:
            await self.call(control.decline_folder, cwd, self.env)
            self.note("just chatting · /project to work in this folder later")
            return
        await self.call(control.forget_folder_decline, cwd, self.env)
        project = await self.call(self.client.post, "/projects/attach-folder",
                                  {"path": str(Path(cwd).resolve()), "trust_state": "trusted",
                                   **({"session_id": self.sid} if self.sid else {})})
        await self._activate_project(project, activated=bool(self.sid))

    async def _model_setup(self) -> None:
        provider, model = control.current_model(self.settings)
        if provider not in {"", "ollama"}:
            return
        base = control.ollama_base(self.settings)
        try:
            models = await self.call(control.ollama_models, base)
        except (OSError, ValueError):
            self.note("Ollama is not reachable at " + base + "; start it, then use /model", "bold", "!")
            return
        confirmed = control.confirmed_model(self.data_dir, self.env)
        reason = control.model_setup_reason(self.settings, [m.name for m in models], confirmed)
        if not reason:
            return
        if not models:
            self.note("No models are installed in Ollama. Install one (ollama pull qwen3.5:9b), then use /model.", "bold", "!")
            return
        vram = await self.call(control.gpu_vram_mib)
        control.with_hints(models, vram)
        opts = [Option(m.name, m.name, hint=(m.hint or "") + (" · configured" if m.name == model else ""),
                       style="dim" if m.fits is False else "") for m in models]
        sel = next((i for i, m in enumerate(models) if m.name == model and m.fits is not False),
                   next((i for i, m in enumerate(models) if m.fits), 0))
        ctx = int(self.env.get("ECHOSPEAK_CONTEXT_LENGTH") or control.DEFAULT_CONTEXT)
        body = [("", reason), ("class:dim", f"Saved for EchoSpeak (the app too) with a {ctx:,}-token context window. "
                                           "Change it later with /model.")]
        action = await self.ask("Choose Echo's model", opts, body=body, selected=sel, attention=True, kind="picker",
                                footer=f"{self.caps.glyphs.arrow_up_down} choose · enter use it · esc decide later")
        if action.kind != "choose":
            self.note("No model confirmed yet · /model when you're ready", "bold", "!")
            return
        await self.call(control.save_default_model, self.client, action.value, base, ctx)
        await self.call(control.confirm_model, self.data_dir, f"ollama:{action.value}", self.env)
        if self.sid:
            try:
                await self.call(control.switch_session_model, self.client, self.sid, "ollama", action.value)
            except ApiError as exc:
                self._report(exc)
        await self._load_settings()
        await self._refresh_model()
        self.note(f"model: {action.value} · context {ctx:,} tokens", "bold", self.caps.glyphs.ok)

    # ── output pump and ticker ───────────────────────────────────────
    async def _flusher(self) -> None:
        assert self._out_event is not None
        while True:
            await self._out_event.wait()
            await asyncio.sleep(0.05)   # coalesce: at most ~20 flushes a second
            self._out_event.clear()
            if not self._out:
                continue
            data = "".join(self._out)
            self._out.clear()
            async with in_terminal():
                out = self.app.output
                out.write_raw(data)
                out.flush()
                hooks, self._after_flush = self._after_flush, []
                for hook in hooks:
                    hook()

    async def _ticker(self) -> None:
        while True:
            if self.run is not None:
                interval = 1.0 if self.caps.reduced_motion else 0.05
                self.app.invalidate()
            elif not self.typed and not self.caps.reduced_motion and not self.buffer.text and self.dialog is None:
                interval = 0.05
                self.app.invalidate()
            else:
                interval = 0.25
                if self.notice[0] and self.notice[1] < self.now():
                    self.notice = ("", 0.0)
                    self.app.invalidate()
            await asyncio.sleep(interval)

    async def _pager(self) -> None:
        width = self.width()
        parts = [B.render(b, width, self.caps, verbose=True) for b in self.transcript]
        if self.run:
            parts += [B.render(b, width, self.caps, verbose=True) for b in self.run.transcript]
            if self.run.tail():
                parts.append(self.run.tail())
        await self._page_text("".join(parts) or "(nothing in this session yet)\n")

    async def _page_text(self, text: str, end: bool = True) -> None:
        pager = shutil.which("less")
        fd, path = tempfile.mkstemp(prefix="echospeak-", suffix=".txt")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            async with in_terminal():
                if pager:
                    subprocess.call([pager, "-R", "+G", path] if end else [pager, "-R", path])
                else:
                    sys.stdout.write(text)
                    sys.stdout.flush()
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    # ── main ─────────────────────────────────────────────────────────
    async def run_async(self) -> int:
        self.loop = asyncio.get_running_loop()
        self._events = asyncio.Queue()
        await self._prepare()
        self._out_event = asyncio.Event()

        def pre_run() -> None:
            # Created inside the running app so get_app() (and in_terminal) find it; prompt_toolkit
            # cancels them when the app exits.
            for coro in (self._flusher(), self._ticker(), self._consume(), self._after_start(), self._welcome_runner()):
                self.app.create_background_task(coro)

        try:
            with patch_stdout(raw=True):
                await self.app.run_async(pre_run=pre_run)
        finally:
            if self.follow_abandon:
                self.follow_abandon.set()
        if self.welcome is not None and not self._welcome_sent:
            # Never printed (nothing else was): the live region is gone now, so the banner goes where it stood.
            self._out.insert(0, B.render(self.welcome, self.width(), self.caps))
            self.welcome = None
        tail = "".join(self._out)
        if tail:
            sys.stdout.write(tail)
        if self.running:
            print(f"The task keeps running on the backend · echospeak attach {self.sid[:8]}")
        elif self.sid:
            print(f"\x1b[2mSession {self.sid[:8]} · resume with: echospeak resume {self.sid[:8]}\x1b[0m"
                  if self.caps.color else f"Session {self.sid[:8]} · resume with: echospeak resume {self.sid[:8]}")
        return 0



# ── /diff text, read-only git, and tmux (only on /preview) ─────────
PREVIEW_MIN_COLS = 140
PREVIEW_ENV = ("HOME", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "COLORTERM", "NO_COLOR", "XDG_RUNTIME_DIR", "XDG_DATA_HOME",
               "ECHOSPEAK_DATA_DIR", "ECHOSPEAK_RUNTIME_DIR", "ECHOSPEAK_URL", "ECHOSPEAK_BACKEND_DIR",
               "ECHO_TUI_ASCII", "ECHO_TUI_ACCENT", "ECHO_TUI_BG", "ECHO_TUI_FG", "ECHO_TUI_DIFF_TINT", "ECHO_REDUCED_MOTION")


def session_diff_text(changes: list, width: int, caps: Caps) -> str:
    """The file list with net counts, then every change in full, oldest first."""
    files: dict[str, list[int]] = {}
    for c in changes:
        row = files.setdefault(c.display, [0, 0])
        row[0] += c.added
        row[1] += c.removed
    g = caps.glyphs
    rows = [[name, f"+{a}", f"{g.minus}{r}"] for name, (a, r) in files.items()]
    out = B.render(B.Table(rows, title=f"Changes {g.sep} this session {g.sep} {len(files)} file"
                                       f"{'s' if len(files) != 1 else ''}"), width, caps)
    for c in changes:
        out += "\n" + B.render(B.FileDiff(c, "ok", 0, full=True), width, caps, verbose=True)
    return out


def _git(root: str, *args: str, timeout: float = 15) -> str:
    try:
        res = subprocess.run(["git", "-C", root, *args], capture_output=True, timeout=timeout,
                             env={**os.environ, "GIT_PAGER": "cat", "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"})
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if res.returncode not in (0, 1):
        return ""
    return res.stdout.decode("utf-8", "replace")


def git_toplevel(path: str) -> str:
    if not shutil.which("git"):
        return ""
    return _git(path, "rev-parse", "--show-toplevel", timeout=5).strip()


def git_diff_text(root: str, width: int, max_untracked: int = 50) -> str:
    """Uncommitted changes plus untracked files (as Codex's /diff does), coloured by git. File contents are
    untrusted, so everything except git's own colour codes goes through sanitize."""
    color = "--color=always"
    tracked = _git(root, "diff", color, "HEAD") or _git(root, "diff", color)
    names = [n for n in _git(root, "ls-files", "--others", "--exclude-standard", "-z").split("\0") if n][:max_untracked]
    parts = [tracked] if tracked.strip() else []
    for name in names:
        parts.append(_git(root, "diff", color, "--no-index", "--", "/dev/null", name))
    text = "".join(parts)
    if not text.strip():
        return ""
    head = f"Uncommitted changes in {_home(root)}" + (f" · {len(names)} untracked file(s) included" if names else "")
    return head + "\n\n" + _sgr_only(text)


_SGR = re.compile(r"\x1b\[[0-9;]*m")


def _sgr_only(text: str) -> str:
    out, pos = [], 0
    for m in _SGR.finditer(text):
        out.append(sanitize(text[pos:m.start()]))
        out.append(m.group(0))
        pos = m.end()
    out.append(sanitize(text[pos:]))
    return "".join(out)


def tmux(*args: str) -> str:
    res = subprocess.run(["tmux", *args], capture_output=True, timeout=10, check=True)
    return res.stdout.decode("utf-8", "replace").strip()


def tmux_pane_alive(pane: str) -> bool:
    try:
        return pane in tmux("list-panes", "-a", "-F", "#{pane_id}").split()
    except (OSError, subprocess.SubprocessError):
        return False


def tmux_pane_width(pane: str) -> int:
    try:
        return int(tmux("display-message", "-p", *(["-t", pane] if pane else []), "#{pane_width}") or 0)
    except (OSError, subprocess.SubprocessError, ValueError):
        return 0


def preview_command(sid: str, endpoint: discovery.Endpoint, client: Client, env) -> tuple[list[str], dict[str, str]]:
    """argv and environment for `echospeak preview --follow <sid>` in a new pane.

    tmux runs pane commands in its server's environment, so the discovery variables are passed with -e.
    The API key is never on a command line or in the pane's environment: when it didn't come from the
    endpoint file (which the viewer finds itself), it goes in a private 0600 file the viewer deletes.
    """
    import json

    root = str(Path(__file__).resolve().parents[2])
    argv = [sys.executable, "-m", "echospeak_cli", "preview", "--follow", sid]
    passed = {k: str(env[k]) for k in PREVIEW_ENV if env.get(k)}
    passed["PYTHONPATH"] = root
    passed["ECHOSPEAK_NO_VENV"] = "1"
    if endpoint.source != "endpoint-file":
        argv += ["--url", client.base_url]
        if client.key:
            folder = tempfile.mkdtemp(prefix="echospeak-preview-")
            path = os.path.join(folder, "endpoint.json")
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"url": client.base_url, "key": client.key}, fh)
            argv += ["--key-file", path]
    return argv, passed


def tmux_split(target: str, argv: list[str], env: dict[str, str]) -> str:
    """The viewer in a right-hand split of ``target`` (this pane); focus stays here. Returns the pane id."""
    args = ["split-window", "-h", "-d", "-l", "40%", "-P", "-F", "#{pane_id}"]
    if target:
        args += ["-t", target]
    for key, value in env.items():
        args += ["-e", f"{key}={value}"]
    args.append(" ".join(shlex.quote(a) for a in argv))
    return tmux(*args)


def _plural_msgs(n) -> str:
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        n = 0
    return f"{n} message" + ("" if n == 1 else "s")


def _ago(ts) -> str:
    try:
        sec = max(0, time.time() - float(ts))
    except (TypeError, ValueError):
        return ""
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if sec >= size:
            return f"{int(sec // size)}{unit} ago"
    return "just now"


def run_tui(client: Client, endpoint: discovery.Endpoint, caps: Caps, **kwargs) -> int:
    tui = EchoTUI(client, endpoint, caps, **kwargs)
    return asyncio.run(tui.run_async())
