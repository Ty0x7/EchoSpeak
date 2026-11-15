"""The window a local server really serves, and prompts it silently cut (agent/lean/window.py).

Found live (harness evaluation, 15 Nov 2026): Ollama served qwen3.5:9b with a 16,384-token window while
EchoSpeak assumed 32,768, and Ollama answers an over-long prompt with HTTP 200 after keeping only its
first 4 tokens and the newest half of the window. Nothing told the loop.
"""
from __future__ import annotations

import threading
from typing import Any

import pytest

from agent.lean import settings, window
from agent.lean.loop import LeanTurn, _estimate_tokens
from agent.lean.personas import AgentPersona
from agent.lean.provider import Endpoint, ModelTurn, ToolCall
from agent.lean.toolbox import NativeTool, Toolbox

OLLAMA = Endpoint("http://127.0.0.1:11434/v1", "not-needed", "qwen3.5:9b", "ollama", True)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    from config import config

    window.reset()
    monkeypatch.delenv("LEAN_CONTEXT_TOKENS", raising=False)
    monkeypatch.delenv("LEAN_MAX_OUTPUT_TOKENS", raising=False)
    monkeypatch.setattr(config, "lean_context_tokens", 0)
    monkeypatch.setattr(config, "model_capability_profiles", {})
    monkeypatch.setattr(config.local, "context_length", 32768)  # LOCAL_MODEL_CONTEXT's default
    yield
    window.reset()


def serving(monkeypatch, size: int) -> list[tuple[str, str]]:
    asked: list[tuple[str, str]] = []
    monkeypatch.setattr(window, "_fetch_ollama", lambda base, model: asked.append((base, model)) or size)
    return asked


def test_the_served_window_lowers_the_configured_one(monkeypatch):
    asked = serving(monkeypatch, 16384)
    assert settings.context_tokens(OLLAMA) == 16384
    assert asked == [("http://127.0.0.1:11434", "qwen3.5:9b")]
    settings.context_tokens(OLLAMA)
    assert len(asked) == 1  # cached, not asked on every step


def test_a_served_window_never_enlarges_the_configured_one(monkeypatch):
    serving(monkeypatch, 262144)
    assert settings.context_tokens(OLLAMA) == 32768


def test_unknown_servers_and_cloud_endpoints_are_not_asked(monkeypatch):
    asked = serving(monkeypatch, 4096)
    assert settings.context_tokens(Endpoint("http://127.0.0.1:1234/v1", "", "m", "lmstudio", True)) == 32768
    assert settings.context_tokens(Endpoint("http://192.168.1.20:11434/v1", "", "m", "ollama", True)) == 32768  # not loopback
    assert window.served(Endpoint("https://api.example.com/v1", "k", "m", "ollama", False)) == 0
    assert asked == []


def test_a_server_that_cannot_be_asked_changes_nothing(monkeypatch):
    def down(base: str, model: str) -> int:
        raise OSError("connection refused")
    monkeypatch.setattr(window, "_fetch_ollama", down)
    assert settings.context_tokens(OLLAMA) == 32768


def test_cut_prompts_are_recognised(monkeypatch):
    serving(monkeypatch, 16384)
    # What Ollama did live: 43,104 tokens sent, 8,194 kept.
    assert window.truncated(OLLAMA, 43104, 8194) == 16384
    # A prompt somewhat over the window, cut to exactly half of it (the ratio alone would miss this one).
    assert window.truncated(OLLAMA, 13000, 8194) == 16384
    # Normal prompts, small prompts and estimator noise are not cuts.
    assert window.truncated(OLLAMA, 9000, 8400) == 0
    assert window.truncated(OLLAMA, 1500, 400) == 0
    assert window.truncated(OLLAMA, 9000, 0) == 0


def test_an_unknown_window_is_inferred_from_the_cut_and_remembered(monkeypatch):
    serving(monkeypatch, 0)  # not loaded / not Ollama
    assert window.truncated(OLLAMA, 30000, 2050) == 4100
    window.learn(OLLAMA, 4100)
    assert settings.context_tokens(OLLAMA) == 4100


# ── the loop ────────────────────────────────────────────────────────────
class Client:
    """A model server. Unless a turn says what it kept, it counts the prompt as EchoSpeak estimates it."""

    def __init__(self, turns: list[ModelTurn]) -> None:
        self.endpoint = OLLAMA
        self.turns = list(turns)
        self.calls: list[list[dict[str, Any]]] = []

    def stream_turn(self, messages, **kwargs):
        self.calls.append([dict(m) for m in messages])
        turn = self.turns.pop(0)
        if not turn.usage.get("prompt"):
            prompt = _estimate_tokens(messages, kwargs.get("tools") or [])
            turn.usage = {"prompt": prompt, "completion": 5, "total": prompt + 5}
        if turn.content and kwargs.get("on_content"):
            kwargs["on_content"](turn.content)
        return turn


def _turn(client: Client, history: list[dict[str, Any]]) -> tuple[LeanTurn, list[str], list[dict[str, Any]]]:
    ran: list[str] = []
    events: list[dict[str, Any]] = []
    box = Toolbox(toolsets=["none"], session_id="t")
    box.native = {"file_list": NativeTool(name="file_list", description="list", parameters={"type": "object", "properties": {}},
                                          func=lambda a: ran.append("file_list") or "a.txt")}
    turn = LeanTurn(client=client, persona=AgentPersona(id="echo", name="Echo"), system_prompt="SYSTEM RULES", history=history,
                    toolbox=box, session_id="t", request_id="r", execution_id="e", emit=events.append,
                    cancel=threading.Event(), persist_tool_runs=False)
    return turn, ran, events


def _long_history(tokens: int) -> list[dict[str, Any]]:
    rows = []
    while _estimate_tokens(rows, []) < tokens:
        rows += [{"role": "user", "content": "Earlier question. " + "word " * 1200},
                 {"role": "assistant", "content": "Earlier answer. " + "word " * 1200}]
    return rows


def test_a_reply_to_a_cut_prompt_is_discarded_and_the_step_is_asked_again(monkeypatch):
    serving(monkeypatch, 0)  # the server's window isn't known yet: only the cut itself reveals it
    history = _long_history(20000)  # fits the assumed 32k budget, not the real 16k window
    client = Client([
        # Made from a prompt that lost its system prompt and request: must not run.
        ModelTurn(content="Deleting everything as asked.", tool_calls=[ToolCall("x", "file_list", "{}")],
                  finish_reason="tool_calls", usage={"prompt": 8194, "completion": 30, "total": 8224}),
        ModelTurn(content="Here is the answer.", finish_reason="stop"),
    ])
    turn, ran, events = _turn(client, history)
    result = turn.run("What is in the project?")
    assert ran == []  # the cut reply's tool call never ran
    assert result.text == "Here is the answer." and "Deleting everything" not in result.text
    cut = next(e for e in events if e["type"] == "context_truncated")
    assert cut["kept"] == 8194 and cut["window"] == 16388
    assert len(client.calls) == 2
    retried = client.calls[1]
    assert retried[0]["content"] == "SYSTEM RULES" and retried[-1]["content"] == "What is in the project?"
    assert _estimate_tokens(retried, []) < 16388 * 0.6  # trimmed to the window the server really has
    assert settings.context_tokens(OLLAMA) == 16388  # remembered for the next steps
    assert result.usage["prompt"] == 8194 + _estimate_tokens(retried, turn.toolbox.schemas())  # the wasted call still cost


def test_a_known_window_is_respected_before_the_first_call(monkeypatch):
    serving(monkeypatch, 16384)
    client = Client([ModelTurn(content="Fine.", finish_reason="stop")])
    turn, _ran, events = _turn(client, _long_history(20000))
    turn.run("Hello?")
    sent = client.calls[0]
    assert _estimate_tokens(sent, []) <= 0.8 * 16384 and sent[-1]["content"] == "Hello?"
    assert not any(e["type"] == "context_truncated" for e in events)
    assert next(e for e in events if e["type"] == "token_usage")["context_limit"] == 16384


def test_a_16k_window_keeps_room_for_the_work_but_still_fits(monkeypatch):
    """A 16k window used to lose a quarter of itself to the reply reserve (4,096): with the real window known,
    the loop compacted every other step and re-read what it had just trimmed (D8: 67 calls)."""
    serving(monkeypatch, 16384)
    history = _long_history(10500)
    client = Client([ModelTurn(content="Fine.", finish_reason="stop")])
    turn, _ran, _events = _turn(client, history)
    turn.run("Hello?")
    assert len(client.calls[0]) == len(history) + 2  # nothing trimmed: 10.5k fits a 16k window with room to reply
    serving(monkeypatch, 16384)
    window.reset()
    client = Client([ModelTurn(content="Fine.", finish_reason="stop")])
    turn, _ran, _events = _turn(client, _long_history(13000))
    turn.run("Hello?")
    assert _estimate_tokens(client.calls[0], []) <= 16384 - 2048  # but more than the window allows is trimmed


def test_a_prompt_that_cannot_fit_ends_the_turn_honestly(monkeypatch):
    serving(monkeypatch, 0)
    client = Client([ModelTurn(content="?", finish_reason="stop", usage={"prompt": 2050, "completion": 1, "total": 2051})
                     for _ in range(3)])
    turn, ran, _events = _turn(client, [])
    turn.system_prompt = "RULES " * 40000  # nothing left to trim: every attempt is cut again
    result = turn.run("Hello?")
    assert not result.success and "context window" in result.text and "?" != result.text
    assert len(client.calls) == 3 and ran == []  # two retries, then an honest error, never the cut reply
