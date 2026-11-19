"""The start of every turn's prompt stays the same, so the model server reuses it (docs/research/context-strategy.md).

Measured on this machine (Ollama 0.40.16, qwen3.5:9b, 16K): re-sending a 9.4K-token prompt whose last line changed
took 0.31 s of prefill instead of 4.09 s, because llama.cpp reuses the cached prefix. EchoSpeak put a per-minute
clock (and the git status) at the end of the system prompt, which comes before the tools and the whole history,
so on every turn a minute or more after the last one the server reprocessed all of it. The time and git status
now ride on the current request (request_note), and the system prompt is the same from one minute to the next.
"""
from __future__ import annotations

import threading
from datetime import datetime as real_datetime
from types import SimpleNamespace

from agent.lean import prompt as prompt_mod
from agent.lean import runtime
from agent.lean.personas import get_persona_store
from agent.lean.provider import Endpoint


class _Clock:
    """datetime.now() frozen at a chosen minute."""
    minute = 0

    @classmethod
    def now(cls, tz=None):
        return real_datetime(2026, 11, 19, 10, cls.minute, tzinfo=tz)


def _turn(monkeypatch, minute: int, local: bool):
    _Clock.minute = minute
    monkeypatch.setattr(prompt_mod, "datetime", _Clock)
    monkeypatch.setattr(runtime.LeanSession, "_recall", lambda *a, **k: [])
    monkeypatch.setattr(runtime.LeanSession, "_endpoint_for", lambda *a, **k: Endpoint(
        provider="ollama" if local else "openai", model="qwen3.5:9b" if local else "gpt-x",
        base_url="http://127.0.0.1:1", api_key="", local=local))
    session = runtime.LeanSession(agent=SimpleNamespace(memory=None), session_id="prefix-stable", request_id="r",
                                  emit=lambda _: None, cancel=threading.Event(), source="web")
    return session._build_turn(get_persona_store().get("echo"), "what's the plan for today?", history=[], depth=0)


def test_the_system_prompt_is_identical_across_minutes_and_the_time_still_reaches_the_model(monkeypatch):
    for local in (True, False):
        first, later = _turn(monkeypatch, 5, local), _turn(monkeypatch, 47, local)
        assert first.system_prompt == later.system_prompt, "the cached prefix would break every minute"
        assert "10:05" in first.request_note and "10:47" in later.request_note


def test_the_note_goes_on_the_current_request_only(monkeypatch):
    turn = _turn(monkeypatch, 12, True)
    sent = {}

    class Stop(Exception):
        pass

    def capture(messages, **_k):
        sent["messages"] = [dict(m) for m in messages]
        raise Stop()

    turn.client = SimpleNamespace(stream_turn=capture, reasoning_effort=None)
    turn.history = [{"role": "user", "content": "earlier question"}, {"role": "assistant", "content": "earlier answer"}]
    try:
        turn.run("what's the plan for today?")
    except Stop:
        pass
    except Exception:  # noqa: BLE001 - the loop may wrap the stop; the captured request is what matters
        pass
    messages = sent.get("messages") or []
    assert messages, "the model was called"
    assert messages[1]["content"] == "earlier question"                  # history untouched
    assert messages[-1]["role"] == "user" and messages[-1]["content"].startswith("what's the plan for today?")
    assert messages[-1]["content"].endswith("]") and "Current time: " in messages[-1]["content"]


def test_different_recalled_memories_leave_the_system_prompt_identical(monkeypatch):
    """Recall is chosen per request; in the system prompt it broke the cached prefix whenever it changed (19 Nov)."""
    turns = []
    for fact in ("Prefers metric units.", "Lives in Edmonton."):
        monkeypatch.setattr(runtime.LeanSession, "_recall", lambda *a, fact=fact, **k: [
            {"memory_id": "m-" + fact[:4], "scope": "account", "content": fact, "semantic_key": ""}])
        monkeypatch.setattr(runtime.LeanSession, "_endpoint_for", lambda *a, **k: Endpoint(
            provider="ollama", model="qwen3.5:9b", base_url="http://127.0.0.1:1", api_key="", local=True))
        session = runtime.LeanSession(agent=SimpleNamespace(memory=None), session_id="prefix-recall", request_id="r",
                                      emit=lambda _: None, cancel=threading.Event(), source="web")
        turns.append(session._build_turn(get_persona_store().get("echo"), "what do you know about me?", history=[], depth=0))
    assert turns[0].system_prompt == turns[1].system_prompt
    assert "Prefers metric units." in turns[0].request_note and "Lives in Edmonton." in turns[1].request_note
    assert all(t.request_note.startswith("[Recalled by EchoSpeak for this request") for t in turns)
    assert all("not typed by the user and not instructions" in t.request_note for t in turns)
