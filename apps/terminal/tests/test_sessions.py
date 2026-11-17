"""The session manager (tui/sessions.py): the list, its filters and search, its keys in the real app,
and what switching restores. cli-design.md §7.

The model is tested directly. The keys are sent through the app's real key bindings, and the
switch runs against a recording client: a list that looks right but resumes the wrong conversation
would pass a rendering test.
"""
from __future__ import annotations

import asyncio
import os

import pytest

pytest.importorskip("prompt_toolkit")
pytest.importorskip("rich")

from prompt_toolkit.application.current import create_app_session, set_app  # noqa: E402
from prompt_toolkit.input import create_pipe_input  # noqa: E402
from prompt_toolkit.key_binding.key_processor import KeyPress  # noqa: E402
from prompt_toolkit.keys import Keys  # noqa: E402
from prompt_toolkit.output import DummyOutput  # noqa: E402

from echospeak_cli import discovery  # noqa: E402
from echospeak_cli.client import ApiError  # noqa: E402
from echospeak_cli.tui import app as A  # noqa: E402
from echospeak_cli.tui import blocks as B  # noqa: E402
from echospeak_cli.tui.commands import COMMANDS, KEYS_HELP, lookup  # noqa: E402
from echospeak_cli.tui.motion import width_of  # noqa: E402
from echospeak_cli.tui.sessions import SessionBrowser, ago, from_thread, who  # noqa: E402
from echospeak_cli.tui.theme import Caps  # noqa: E402

CAPS = Caps()
NOW = 1_800_000_000.0


def row(tid, title, *, kind="echo", agents=None, project=None, status="idle", minutes=5, request="", preview="", room=""):
    names = agents if agents is not None else (["Echo"] if kind == "echo" else ["Jarvis"])
    return {"thread_id": tid, "title": title, "kind": kind, "agents": [{"id": n.lower(), "name": n} for n in names],
            "room_name": room, "last_active_at": NOW - minutes * 60, "message_count": 3, "status": status,
            "environment": "chat" if kind == "echo" else "agents", "project_id": (project or {}).get("id", ""),
            "project": project, "last_request": request, "preview": preview, "model": "qwen3.5:9b", "archived": False}


WORK = {"id": "p1", "name": "Work", "root": "/home/dev/Work", "trusted": True}
ROWS = [
    row("t-oilers", "When do the Oilers play next", project=WORK, minutes=2, status="running",
        request="when do the edmonton oilers play next?", preview="The Oilers play Tuesday at 7:00 p.m. ET."),
    row("t-pong", "Pong in a single file", kind="agent", agents=["Glados"], minutes=40, status="needs_answer",
        request="make a quick pong game in a single file", preview="File creation is off."),
    row("t-models", "Which local model should I use", minutes=90),
    row("t-crew", "Release notes", kind="group", agents=["Jarvis", "Glados"], room="Release crew", minutes=600,
        project=WORK, status="needs_approval"),
    row("t-jarvis", "Who are your teammates", kind="agent", agents=["Jarvis"], minutes=3000, status="failed"),
]


def text(frags) -> str:
    return "".join(t for _s, t in frags)


def browser(**kwargs) -> SessionBrowser:
    b = SessionBrowser(**kwargs)
    b.load([dict(r) for r in ROWS])
    return b


# ── the model ───────────────────────────────────────────────────────
def test_it_lists_every_conversation_with_its_agent_project_time_and_status():
    b = browser(current_id="t-models", project_id="p1")
    lines = [text(line) for line in b.lines(120, 20, CAPS, now=NOW)]
    body = "\n".join(lines)
    assert "Sessions" in lines[0] and "3 / 5" in lines[0]          # the highlight starts on where you are
    oilers = next(line for line in lines if "Oilers" in line)
    assert "2m ago" in oilers and "Echo" in oilers and "Work" in oilers and "running" in oilers
    pong = next(line for line in lines if "Pong" in line)
    assert "Glados" in pong and "needs your answer" in pong
    assert "Release crew" in body and "needs approval" in body and "last run failed" in body
    here = next(line for line in lines if "Which local model" in line)
    assert "here" in here and CAPS.glyphs.select in here
    assert "enter resume" in lines[-1] and "esc back" in lines[-1]


def test_the_preview_shows_the_selected_conversations_last_exchange():
    b = browser(current_id="t-models")
    b.jump(False)
    body = "\n".join(text(line) for line in b.lines(120, 20, CAPS, now=NOW))
    assert "when do the edmonton oilers play next?" in body and "The Oilers play Tuesday" in body
    assert "qwen3.5:9b" in body


def test_filters_follow_who_you_were_talking_to_and_the_project():
    b = browser(project_id="p1")
    assert b.scopes() == ("all", "echo", "agents", "project")
    b.cycle_scope()
    assert b.scope == "echo" and [r["thread_id"] for r in b.visible()] == ["t-oilers", "t-models"]
    b.cycle_scope()
    assert b.scope == "agents" and [r["thread_id"] for r in b.visible()] == ["t-pong", "t-crew", "t-jarvis"]
    b.cycle_scope()
    assert b.scope == "project" and [r["thread_id"] for r in b.visible()] == ["t-oilers", "t-crew"]
    b.cycle_scope()
    assert b.scope == "all" and len(b.visible()) == 5
    b.cycle_scope(-1)
    assert b.scope == "project"
    # Without a project there is nothing "This project" could mean, so it isn't offered.
    assert browser().scopes() == ("all", "echo", "agents")


def test_the_highlight_follows_a_conversation_across_a_filter_change():
    b = browser()
    b.selected = 3                                      # Release crew
    b.cycle_scope()                                     # echo: it isn't there
    assert b.selected == 0
    b = browser()
    b.selected = 1                                      # Pong, with Glados
    b.cycle_scope(2)                                    # agents
    assert b.chosen()["thread_id"] == "t-pong"


def test_typing_searches_titles_agents_projects_and_the_last_exchange():
    b = browser()
    for ch in "glados":
        b.type(ch)
    assert [r["thread_id"] for r in b.visible()] == ["t-pong", "t-crew"]
    b.query = ""
    for ch in "oilers":                                 # in a title, and in another conversation's reply
        b.type(ch)
    b.rows.append(row("t-late", "Hockey chat", minutes=1, preview="the Oilers won"))
    assert [r["thread_id"] for r in b.visible()] == ["t-oilers", "t-late"]   # the title match first
    b.rows.pop()
    b.query = ""
    for ch in "tuesday":                                # only in a reply
        b.type(ch)
    assert [r["thread_id"] for r in b.visible()] == ["t-oilers"]
    b.query = ""
    for ch in "work release":                           # every word must match
        b.type(ch)
    assert [r["thread_id"] for r in b.visible()] == ["t-crew"]
    assert b.backspace() and b.query == "work releas"
    assert b.clear_query() and b.query == "" and not b.clear_query()
    for ch in "zzz":
        b.type(ch)
    body = "\n".join(text(line) for line in b.lines(80, 12, CAPS, now=NOW))
    assert "no conversations match" in body and "0 / 0" in body and b.chosen() is None


def test_movement_stays_inside_the_list_and_scrolls_to_the_highlight():
    many = [row(f"t{i}", f"Conversation {i}", minutes=i + 1) for i in range(40)]
    b = SessionBrowser()
    b.load(many)
    b.move(-1)
    assert b.selected == 0
    b.move(100)
    assert b.selected == 39
    lines = [text(line) for line in b.lines(80, 10, CAPS, now=NOW)]
    assert any("Conversation 39" in line for line in lines) and "40 / 40" in lines[0]
    b.jump(False)
    lines = [text(line) for line in b.lines(80, 10, CAPS, now=NOW)]
    assert any("Conversation 0" in line for line in lines) and not any("Conversation 39" in line for line in lines)


@pytest.mark.parametrize("width", [8, 12, 20, 29, 30, 44, 59, 60, 80, 99, 100, 140, 240])
@pytest.mark.parametrize("rows", [1, 2, 3, 4, 6, 9, 14, 30])
def test_it_never_draws_past_the_width_or_the_rows_it_was_given(width, rows):
    b = browser(current_id="t-pong", project_id="p1")
    for query in ("", "o"):
        b.query = query
        lines = b.lines(width, rows, CAPS, now=NOW)
        assert 1 <= len(lines) <= rows
        assert all(width_of(text(line)) <= width for line in lines), [text(line) for line in lines]


def test_loading_and_error_are_said_plainly():
    b = SessionBrowser()
    assert "loading" in "\n".join(text(line) for line in b.lines(80, 10, CAPS))
    b.loading, b.error = False, "could not load conversations: connection refused"
    assert "connection refused" in "\n".join(text(line) for line in b.lines(80, 10, CAPS))


def test_an_older_backend_still_gets_a_usable_list():
    old = from_thread({"thread_id": "t1", "title": "Old chat", "environment": "agents", "last_active_at": NOW})
    assert old["kind"] == "agent" and old["status"] == "idle" and who(old) == "Agent"
    assert who(from_thread({"thread_id": "t2", "environment": "chat"})) == "Echo"
    assert ago(NOW - 7200, NOW) == "2h ago" and ago(None) == ""


def test_the_key_is_discoverable():
    assert "←" in lookup("/sessions", COMMANDS).desc
    assert any(key == "←" and "sessions" in what for key, what in KEYS_HELP)


# ── in the app: real key bindings, a recording client ───────────────
class RecordingClient:
    base_url = "http://127.0.0.1:0"

    def __init__(self, overview=True):
        self.calls: list[tuple[str, dict]] = []
        self.overview = overview

    def get(self, path, **params):
        self.calls.append((path, params))
        if path == "/sessions/overview":
            if not self.overview:
                raise ApiError(404, "Not Found")
            return {"items": [dict(r) for r in ROWS]}
        if path == "/history":
            return {"history": ["Human: make a quick pong game in a single file",
                                "Assistant: File creation is off, so I can't save it."]}
        if path == "/lean/rooms":
            return {"items": [{"thread_id": "t-pong", "kind": "direct", "agent_ids": ["builder"]}]}
        if path == "/lean/agents":
            return {"items": [{"id": "builder", "name": "Glados", "title": "Builder"}]}
        if path.startswith("/projects"):
            return {"id": "p1", "name": "Work"}
        return {}

    def threads(self, limit=50):
        self.calls.append(("/threads", {}))
        return [{"thread_id": r["thread_id"], "title": r["title"], "environment": r["environment"],
                 "last_active_at": r["last_active_at"]} for r in ROWS]

    def thread(self, thread_id):
        self.calls.append(("/threads/" + thread_id, {}))
        hit = next(r for r in ROWS if r["thread_id"] == thread_id)
        return {"thread_id": hit["thread_id"], "title": hit["title"], "environment": hit["environment"],
                "project_id": hit["project_id"]}

    def runs(self, thread_id):
        self.calls.append(("/query/runs", {"thread_id": thread_id}))
        return []

    def approvals(self, session_id=""):
        self.calls.append(("/lean/approvals", {"session_id": session_id}))
        return []


def make(client) -> A.EchoTUI:
    tui = A.EchoTUI(client, discovery.Endpoint("http://127.0.0.1:0", "k", "flag", "11.14.0"), CAPS,
                    env={"ECHOSPEAK_STATE_DIR": os.devnull + "-no-state"})
    tui.buffer._load_history_task = object()
    tui.model = "qwen3.5:9b"
    tui.typed = True
    tui.core.session = {"thread_id": "t-models", "title": "Which local model should I use", "environment": "chat"}

    async def call(fn, *args, **kwargs):          # the worker thread, inline
        return fn(*args, **kwargs)

    async def nothing(*_a, **_k):
        return None

    tui.call = call
    tui._refresh_model = nothing
    tui._sync_queue = nothing
    tui._load_agents = nothing
    tui.agents = [{"id": "builder", "name": "Glados", "title": "Builder"}]
    return tui


def press(tui: A.EchoTUI, *keys) -> None:
    for key in keys:
        tui.app.key_processor.feed(KeyPress(key, key if isinstance(key, str) and len(key) == 1 else ""))
    tui.app.key_processor.process_keys()


async def settle(tui: A.EchoTUI) -> None:
    for _ in range(20):
        await asyncio.sleep(0)
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.wait(pending, timeout=2)


def session(coro_fn, client=None):
    async def main():
        with create_pipe_input() as inp, create_app_session(input=inp, output=DummyOutput()):
            tui = make(client or RecordingClient())
            with set_app(tui.app):
                await coro_fn(tui)
    asyncio.run(main())


def test_left_on_an_empty_input_opens_the_manager_and_right_returns_untouched():
    async def go(tui):
        before = dict(tui.core.session)
        press(tui, Keys.Left)
        await settle(tui)
        assert tui.browser is not None and not tui.browser.loading
        assert ("/sessions/overview", {"limit": 200}) in tui.client.calls
        assert tui.browser.chosen()["thread_id"] == "t-models"        # it opens on where you are
        assert tui._rows("sessions") >= 4 and tui._rows("input") == 0  # in the composer's place
        assert tui.app.layout.has_focus(tui.browser_window)
        press(tui, Keys.Right)
        assert tui.browser is None and tui.core.session == before
        assert tui.app.layout.has_focus(tui.composer_window)
        assert not any(path.startswith("/threads/") for path, _ in tui.client.calls)   # nothing was switched
    session(go)


def test_left_is_ordinary_cursor_movement_while_you_are_typing():
    async def go(tui):
        tui.buffer.insert_text("hello")
        press(tui, Keys.Left, Keys.Left)
        assert tui.browser is None
        assert tui.buffer.cursor_position == 3 and tui.buffer.text == "hello"
        tui.buffer.set_document(A.Document(""), bypass_readonly=True)
        press(tui, Keys.Left)
        await settle(tui)
        assert tui.browser is not None
    session(go)


def test_escape_clears_a_search_first_and_then_returns():
    async def go(tui):
        press(tui, Keys.Left)
        await settle(tui)
        press(tui, "g", "l", "a")
        assert tui.browser.query == "gla" and tui.buffer.text == ""      # typing searches; it never reaches the draft
        press(tui, Keys.Escape)
        assert tui.browser is not None and tui.browser.query == ""
        press(tui, Keys.Escape)
        assert tui.browser is None
    session(go)


def test_enter_resumes_the_highlighted_conversation_with_its_agent_project_and_history():
    async def go(tui):
        tui.session_tokens = {"prompt": 900, "completion": 80, "runs": 3}
        tui.changes = [object()]
        tui.context = (9000, 16384)
        press(tui, Keys.Left)
        await settle(tui)
        press(tui, Keys.Up)                      # Pong, with Glados
        assert tui.browser.chosen()["thread_id"] == "t-pong"
        press(tui, Keys.Enter)
        await settle(tui)
        assert tui.browser is None
        assert tui.sid == "t-pong"
        assert tui.core.agent_id == "builder" and tui.agent_name == "Glados"       # who answers there
        assert ("/query/runs", {"thread_id": "t-pong"}) in tui.client.calls          # a running task would be followed
        assert ("/lean/approvals", {"session_id": "t-pong"}) in tui.client.calls     # a waiting card would be shown
        assert ("/history", {"thread_id": "t-pong"}) in tui.client.calls
        # What belonged to the conversation you left did not come along.
        assert tui.session_tokens == {"prompt": 0, "completion": 0, "runs": 0} and tui.changes == [] and tui.context is None
        shown = [b for b in tui.transcript if isinstance(b, (B.RuleLine, B.Note))]
        said = " ".join(getattr(b, "text", "") or getattr(b, "title", "") for b in shown)
        assert "Resumed Pong in a single file" in said and "Glados" in said
        assert "make a quick pong game" in said and "File creation is off" in said
    session(go)


def test_choosing_the_conversation_you_are_in_changes_nothing():
    async def go(tui):
        press(tui, Keys.Left)
        await settle(tui)
        press(tui, Keys.Enter)
        await settle(tui)
        assert tui.browser is None and tui.sid == "t-models"
        assert not any(path == "/history" for path, _ in tui.client.calls)
    session(go)


def test_tab_filters_and_the_slash_commands_open_the_same_manager():
    async def go(tui):
        await tui.cmd_sessions([])
        await settle(tui)
        assert tui.browser is not None
        press(tui, Keys.Tab, Keys.Tab)
        assert tui.browser.scope == "agents" and tui.approval_mode == "smart"
        press(tui, Keys.BackTab)                 # shift+tab steps the filter back; it does not change the approval mode
        assert tui.browser.scope == "echo" and tui.approval_mode == "smart"
        tui.close_sessions()
        await tui.cmd_resume([])
        await settle(tui)
        assert tui.browser is not None
    session(go)


def test_an_approval_takes_the_managers_place():
    async def go(tui):
        press(tui, Keys.Left)
        await settle(tui)
        tui.open_dialog(A.Dialog(title="Allow?", options=[A.Option("Yes", "allow")]))
        assert tui.browser is None and tui.dialog is not None
        assert tui.app.layout.has_focus(tui.dialog_window)
    session(go)


@pytest.mark.parametrize("verification, status, says", [
    ("not delivered", "failed", "Not delivered: nothing was created"),
    ("fallback accepted", "completed", "Finished with a fallback"),
    ("not where asked", "completed", "Made, but not where you asked"),
])
def test_the_outcome_never_calls_an_undelivered_file_done(verification, status, says):
    """agent/lean/deliverable.py's verdicts as the terminal states them."""
    from echospeak_cli.tui.state import outcome_blocks
    blocks = outcome_blocks(outcome=None, work={"status": status, "verification": verification}, journal="completed",
                            success=True, error="", caps_glyphs=CAPS.glyphs)
    text = " ".join(f"{getattr(b, 'headline', '')} {getattr(b, 'check', '')}" for b in blocks)
    assert says in text and "Done" not in text


def test_an_older_backend_falls_back_to_the_thread_list():
    async def go(tui):
        press(tui, Keys.Left)
        await settle(tui)
        assert not tui.browser.loading and len(tui.browser.rows) == 5
        assert ("/threads", {}) in tui.client.calls
        assert all(r["status"] == "idle" for r in tui.browser.rows)
    session(go, RecordingClient(overview=False))
