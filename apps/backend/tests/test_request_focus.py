"""Tools found from the request itself, for focused local models (owner session f75dae98, 18 November 2026).

Asked "please look at codex and claude sessions and summerize what they did", qwen3.5:9b never saw
agent_sessions (focus defers it behind find_tools; it didn't call find_tools). It guessed two read_output
IDs, then made 76 more calls walking ~/Work and summarised the wrong thing. The tool's own description
says to use it for exactly that request. A strong match between the request and a deferred tool now
lists that tool up front; ordinary requests list nothing extra, so the prompt doesn't grow.
"""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from agent.lean import runtime
from agent.lean.personas import get_persona_store
from agent.lean.provider import Endpoint
from agent.lean.toolbox import CHAT_FOCUS


def _sent(monkeypatch, message: str) -> tuple[set[str], set[str]]:
    monkeypatch.setattr(runtime.LeanSession, "_recall", lambda *a, **k: [])
    monkeypatch.setattr(runtime.LeanSession, "_endpoint_for", lambda *a, **k: Endpoint(
        provider="ollama", model="qwen3.5:9b", base_url="http://127.0.0.1:1", api_key="", local=True))
    session = runtime.LeanSession(agent=SimpleNamespace(memory=None), session_id="focus-request", request_id="r",
                                  emit=lambda _: None, cancel=threading.Event(), source="web")
    box = session._build_turn(get_persona_store().get("echo"), message, history=[], depth=0).toolbox
    always = {n for n, t in box.native.items() if t.always or t.hidden}
    return {s["function"]["name"] for s in box.schemas()}, CHAT_FOCUS | always | {"find_tools"}


def test_the_owners_sessions_request_lists_agent_sessions(monkeypatch):
    sent, _base = _sent(monkeypatch, "please look at codex and claude sessions and summerize what they did")
    assert "agent_sessions" in sent


@pytest.mark.parametrize("message", [
    "What's the capital of Peru?",
    "lets crate a pong video game here in /work and launch it after ur done on a website",
    "what is ASMR and do research into it",
    "hello",
    "read the file README.md",
    # Evaluation prompts that matched on common words before distinctiveness was required:
    "Ask Glados to create hello.txt in the project containing exactly: hi from glados. Then check it.",
    "Rename compute_total to order_total everywhere in the project. test_checkout.py already uses it.",
    "Sign Noor Halloran up on our user system (username noor_h, email noor@example.test), confirm it worked.",
])
def test_ordinary_requests_list_nothing_extra(monkeypatch, message):
    sent, base = _sent(monkeypatch, message)
    assert sent <= base, sorted(sent - base)


def test_suggest_is_strong_matches_only_and_at_most_two():
    from agent.lean.toolbox import NativeTool, Toolbox

    tools = [NativeTool(n, d, {"type": "object", "properties": {}}, lambda a: "ok") for n, d in (
        ("agent_sessions", "Read the actual session logs of Claude Code and Codex: what the agent did."),
        ("stock_history", "Price history of a stock or fund."),
        ("image_search", "Find pictures on the web."),
        ("video_search", "Find videos on the web."),
    )]
    box = Toolbox(toolsets=["none"], extra_tools=tools)
    box.native = {t.name: t for t in tools}
    box.focus(set())
    assert box.suggest("look at codex and claude sessions") == ["agent_sessions"]
    assert box.suggest("hello there") == []
    assert len(box.suggest("search images videos stock history sessions codex")) <= 2


def test_a_builder_asked_to_remember_something_is_offered_memory_save(monkeypatch):
    """Evaluation MI1 (19 November): the builder (forge) had memory_save deferred and agent_notes listed, so
    "Remember that my build cache key is …" replaced its private project notes with the fact (2 of 2 trials)."""
    monkeypatch.setattr(runtime.LeanSession, "_recall", lambda *a, **k: [])
    monkeypatch.setattr(runtime.LeanSession, "_endpoint_for", lambda *a, **k: Endpoint(
        provider="ollama", model="qwen3.5:9b", base_url="http://127.0.0.1:1", api_key="", local=True))

    def sent(message: str) -> set[str]:
        session = runtime.LeanSession(agent=SimpleNamespace(memory=SimpleNamespace()), session_id="focus-remember",
                                      request_id="r", emit=lambda _: None, cancel=threading.Event(), source="web")
        box = session._build_turn(get_persona_store().get("forge"), message, history=[], depth=0).toolbox
        return {s["function"]["name"] for s in box.schemas()}

    assert "memory_save" in sent("Remember that my build cache key is zebra-164.")
    assert "memory_save" in sent("Please remember: the staging host is vega-12")
    assert "memory_save" not in sent("Fix the failing test in parser.py")
