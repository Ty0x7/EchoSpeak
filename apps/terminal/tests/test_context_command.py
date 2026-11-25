"""/context: choose the window; it is saved and the served one is reported, never assumed (workstream 1)."""
from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("prompt_toolkit")

from prompt_toolkit.application.current import create_app_session  # noqa: E402
from prompt_toolkit.input import create_pipe_input  # noqa: E402
from prompt_toolkit.output import DummyOutput  # noqa: E402

from echospeak_cli import control  # noqa: E402
from test_layout_matrix import make_tui  # noqa: E402

INFO = {"model": "qwen3.5:9b", "configured": 16384, "served": 16384, "default": 32768,
        "choices": [{"tokens": 16384, "label": "16K", "note": "smaller GPUs", "default": False},
                    {"tokens": 32768, "label": "32K", "note": "the standard default", "default": True},
                    {"tokens": 65536, "label": "64K", "note": "coding", "default": False}]}


def _run(tui, args, monkeypatch, info=INFO):
    saved, notes = [], []
    monkeypatch.setattr(control, "context_windows", lambda client: info)
    monkeypatch.setattr(control, "save_context_length", lambda client, tokens: saved.append(tokens) or {})
    tui.note = lambda text, *a, **k: notes.append(text)

    async def nothing():
        return None
    tui._load_settings = nothing

    async def go():
        tui._out_event = None
        await tui.cmd_context(args)
    asyncio.run(go())
    return saved, notes


def test_choosing_by_name_saves_it(monkeypatch):
    with create_pipe_input() as inp, create_app_session(input=inp, output=DummyOutput()):
        tui, _ = make_tui()
        saved, notes = _run(tui, ["64k"], monkeypatch)
        assert saved == [65536] and "64K" in notes[-1] and "served" in notes[-1]


def test_a_window_the_model_does_not_offer_is_refused(monkeypatch):
    with create_pipe_input() as inp, create_app_session(input=inp, output=DummyOutput()):
        tui, _ = make_tui()
        with pytest.raises(LookupError):
            _run(tui, ["128k"], monkeypatch)


def test_providers_with_their_own_window_say_so(monkeypatch):
    with create_pipe_input() as inp, create_app_session(input=inp, output=DummyOutput()):
        tui, _ = make_tui()
        with pytest.raises(LookupError, match="own window"):
            _run(tui, [], monkeypatch, info={"choices": [], "note": "This provider sets its own window."})


def test_the_standard_default_is_32k():
    assert control.DEFAULT_CONTEXT == 32768


def test_the_picker_says_how_much_runs_on_the_cpu(monkeypatch):
    with create_pipe_input() as inp, create_app_session(input=inp, output=DummyOutput()):
        tui, _ = make_tui()
        shown = {}

        async def ask(title, opts, body=None, selected=0, **kw):
            shown["body"] = " ".join(t for _s, t in body or [])
            class A:
                kind = "cancel"
            return A()
        tui.ask = ask
        info = dict(INFO, state="served", served=32768, configured=32768,
                    loaded=[{"name": "echospeak/qwen3.5-9b:32k", "context": 32768, "gpu_share": 0.79}])
        _run(tui, [], monkeypatch, info=info)
        assert "serving 32K" in shown["body"] and "79% of it is on the GPU" in shown["body"]
