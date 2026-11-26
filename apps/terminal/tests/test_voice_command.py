"""/voice: read Echo's replies aloud through the backend's shared speaker; on/off persists."""
from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("prompt_toolkit")

from prompt_toolkit.application.current import create_app_session  # noqa: E402
from prompt_toolkit.input import create_pipe_input  # noqa: E402
from prompt_toolkit.output import DummyOutput  # noqa: E402

from test_layout_matrix import make_tui  # noqa: E402


def _run(tui, args):
    calls, notes = [], []
    tui.note = lambda text, *a, **k: notes.append(text)

    def request(method, path, body=None, params=None):
        calls.append((method, path, body))
        return {"status": "queued"}
    tui.client.request = request
    tui.client.post = lambda path, body=None, **k: request("POST", path, body)
    tui.client.get = lambda path, **k: {"settings": {"voice_read_aloud_terminal": False}}

    async def go():
        await tui.cmd_voice(args)
    asyncio.run(go())
    return calls, notes


@pytest.mark.parametrize("args,expected", [(["on"], True), (["off"], False)])
def test_on_and_off_persist_with_list_args(args, expected):
    with create_pipe_input() as inp, create_app_session(input=inp, output=DummyOutput()):
        tui, _ = make_tui()
        calls, notes = _run(tui, args)
        assert ("PUT", "/settings", {"voice_read_aloud_terminal": expected}) in calls
        assert ("reads replies aloud" if expected else "silent") in notes[-1]


def test_say_reads_the_last_answer_through_the_shared_speaker():
    with create_pipe_input() as inp, create_app_session(input=inp, output=DummyOutput()):
        tui, _ = make_tui()
        tui.last_answer = "Done. Three tests pass."
        calls, _ = _run(tui, ["say"])
        assert calls[-1][1] == "/media-runtime/voice/speak" and calls[-1][2]["channel"] == "echo"


def test_no_args_reports_status():
    with create_pipe_input() as inp, create_app_session(input=inp, output=DummyOutput()):
        tui, _ = make_tui()
        _, notes = _run(tui, [])
        assert notes[-1].startswith("read aloud is off")
