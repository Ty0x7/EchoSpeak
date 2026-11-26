"""A new conversation in the terminal belongs to the terminal's own project (unified mode: no project = Chat only)."""
from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("prompt_toolkit")

from prompt_toolkit.application.current import create_app_session  # noqa: E402
from prompt_toolkit.input import create_pipe_input  # noqa: E402
from prompt_toolkit.output import DummyOutput  # noqa: E402

from test_layout_matrix import make_tui  # noqa: E402


def _run(tui, coro):
    asyncio.run(coro)


def _setup(tui, home: str, current: str):
    tui.home_project_id = home
    tui.core.project_id = current
    tui.note = lambda *a, **k: None

    async def name(pid):
        return f"name-{pid}"

    tui._project_name = name


@pytest.mark.parametrize("command", ["cmd_new", "cmd_clear"])
def test_new_conversation_returns_to_the_home_project_after_a_switch(command):
    with create_pipe_input() as inp, create_app_session(input=inp, output=DummyOutput()):
        tui, _ = make_tui()
        _setup(tui, home="home-pid", current="")       # switched to a session without a project
        old_id = tui.sid

        def create(title, *, project_id):
            assert title == "New Session" and project_id == "home-pid"
            return {"thread_id": "fresh-id"}

        async def refresh_model():
            pass

        tui.core.create_session = create
        tui._refresh_model = refresh_model
        tui.buffer.text = "draft to clear"
        tui.buffer._load_history_task = None
        tui.last_answer = "previous answer"
        _run(tui, getattr(tui, command)([]))
        if command == "cmd_clear":
            assert tui.sid == "fresh-id" and tui.sid != old_id
            assert not tui.buffer.text and not tui.last_answer and tui.welcome is not None
        else:
            assert tui.core.session is None
        assert tui.core.project_id == "home-pid"
        assert tui.project_name == "name-home-pid"


def test_detached_terminal_stays_without_a_project():
    with create_pipe_input() as inp, create_app_session(input=inp, output=DummyOutput()):
        tui, _ = make_tui()
        _setup(tui, home="", current="")               # /project - detached it on purpose
        _run(tui, tui.cmd_new([]))
        assert tui.core.project_id == ""


def test_switched_to_another_project_keeps_it_until_a_new_conversation():
    with create_pipe_input() as inp, create_app_session(input=inp, output=DummyOutput()):
        tui, _ = make_tui()
        _setup(tui, home="home-pid", current="other-pid")
        assert tui.core.project_id == "other-pid"      # switching itself changes nothing here
        _run(tui, tui.cmd_new([]))
        assert tui.core.project_id == "home-pid"
