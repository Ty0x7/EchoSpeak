"""Chat's execution paths through the real Lean loop, with a scripted model and fake tools.

The five paths a general-purpose assistant needs (a simple question, current information,
multistep research, a memory-dependent follow-up, a file-grounded explanation), plus the
evidence rules around them: citations checked against what was retrieved, one correction,
and a step budget that still ends in an answer. No model calls, no network.
"""
from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any

import pytest

from agent import state, threads
from agent.lean import chat_run, rooms
from agent.lean import runtime as lean_runtime
from agent.lean.loop import LeanTurn
from agent.lean.personas import AgentPersona
from agent.lean.provider import ModelTurn, ToolCall
from agent.lean.toolbox import NativeTool, Toolbox

DOCS = "https://docs.example.org/release-notes"
BLOG = "https://blog.example.net/review"
FORUM = "https://forum.example.com/thread/9"


class Script:
    """A model that plays back turns and records what it was sent (messages and tools)."""

    def __init__(self, turns: list[ModelTurn]) -> None:
        self.turns = list(turns)
        self.calls: list[dict[str, Any]] = []

    def stream_turn(self, messages, **kwargs):
        self.calls.append({"messages": [dict(m) for m in messages], "tools": list(kwargs.get("tools") or [])})
        turn = self.turns.pop(0)
        if kwargs.get("on_content") and turn.content:
            kwargs["on_content"](turn.content)
        return turn


def _call(name: str, **args: Any) -> ModelTurn:
    import json
    return ModelTurn(tool_calls=[ToolCall(f"c{abs(hash((name, json.dumps(args, sort_keys=True)))) % 10**6}", name, json.dumps(args))])


def _tools(log: list[str]) -> dict[str, NativeTool]:
    def search(args):
        log.append(f"search:{args.get('query')}")
        return (f"1. Release notes - {DOCS}\n   Version 5 ships on 1 Nov.\n2. Review - {BLOG}\n   Hands-on review.\n"
                f"3. Forum thread - {FORUM}\n   Users report a bug.")

    def fetch(args):
        log.append(f"fetch:{args.get('url')}")
        return f"[Source: {args.get('url')}]\nVersion 5 ships on 1 November 2026 with offline mode."

    def read_file(args):
        log.append(f"file:{args.get('path')}")
        return "def add(a, b):\n    return a + b\n"

    schema = {"type": "object", "properties": {}}
    return {
        "web_search": NativeTool(name="web_search", description="search", parameters=schema, func=search, parallel_safe=True),
        "safe_web_fetch": NativeTool(name="safe_web_fetch", description="read a page", parameters=schema, func=fetch, parallel_safe=True),
        "file_read": NativeTool(name="file_read", description="read a file", parameters=schema, func=read_file, parallel_safe=True),
    }


def _chat_turn(script: Script, *, profile: str = "full", earlier=(), user: str = "q") -> tuple[LeanTurn, chat_run.ChatRun, list[str]]:
    """A Chat turn wired the way LeanSession._build_turn wires it."""
    log: list[str] = []
    box = Toolbox(toolsets=["none"], session_id="t")
    box.native = _tools(log)
    run = chat_run.ChatRun(session_id="t", model="fixture-model", provider="lmstudio", profile=profile,
                           earlier_sources=list(earlier), user_message=user)
    turn = LeanTurn(client=script, persona=AgentPersona(id="chat", name="fixture-model"), system_prompt="s", history=[],
                    toolbox=box, session_id="t", request_id="r", execution_id="e", emit=lambda _e: None,
                    cancel=threading.Event(), persist_tool_runs=False)
    turn.tool_observer = run.observe
    turn.final_check = run.check_answer
    turn.max_steps_override = run.budget
    turn.final_grace = True
    turn.grace_prompt = chat_run.GRACE_PROMPT
    return turn, run, log


def _finish(turn: LeanTurn, run: chat_run.ChatRun, message: str) -> tuple[Any, dict[str, Any]]:
    result = turn.run(message)
    steps = max([int(i.get("step") or 0) for i in result.timeline] or [0])
    return result, run.trace(steps=steps, stop_reason=result.stop_reason, usage=result.usage, answer=result.text)


def test_simple_question_is_one_model_call_and_no_tools():
    script = Script([ModelTurn(content="Paris is the capital of France.")])
    turn, run, log = _chat_turn(script)
    result, trace = _finish(turn, run, "What's the capital of France?")
    assert result.text == "Paris is the capital of France."
    assert len(script.calls) == 1 and log == []
    assert trace["path"] == "direct" and trace["calls"] == [] and trace["stop_reason"] == "answered"


def test_current_information_searches_reads_and_cites_what_it_read():
    script = Script([
        _call("web_search", query="product X release date 2026"),
        _call("safe_web_fetch", url=DOCS),
        ModelTurn(content=f"It ships on 1 November 2026, with offline mode ([release notes]({DOCS}))."),
    ])
    turn, run, log = _chat_turn(script)
    result, trace = _finish(turn, run, "When does product X version 5 come out?")
    assert log == ["search:product X release date 2026", f"fetch:{DOCS}"]
    assert DOCS in result.text and trace["path"] == "search"
    assert trace["evidence"]["pages_read"] == [chat_run.normalize_url(DOCS)]
    assert trace["citations"]["read"] == [chat_run.normalize_url(DOCS)] and trace["citations"]["unknown"] == []
    assert trace["corrections"] == []


def test_research_with_a_made_up_link_gets_one_correction_then_a_grounded_answer():
    invented = "https://made-up.example/report"
    script = Script([
        _call("web_search", query="product X review"),
        _call("web_search", query="product X known bugs"),
        _call("safe_web_fetch", url=BLOG),
        _call("safe_web_fetch", url=FORUM),
        ModelTurn(content=f"Reviews are positive ([analysis]({invented})), but users report a bug ([forum]({FORUM}))."),
        ModelTurn(content=f"Reviews are positive ([review]({BLOG})), but users report a bug ([forum]({FORUM}))."),
    ])
    turn, run, _log = _chat_turn(script)
    result, trace = _finish(turn, run, "Is product X worth buying?")
    correction = script.calls[5]["messages"][-1]["content"]
    assert "weren't in any search result or page you retrieved" in correction and "made-up.example" in correction
    assert invented not in result.text and BLOG in result.text
    assert trace["path"] == "research" and trace["corrections"] == ["unknown_links"]
    assert trace["citations"]["unknown"] == [] and len(trace["citations"]["read"]) == 2


def test_research_that_cites_nothing_it_read_is_asked_once_to_cite():
    long_answer = "Version 5 adds offline mode and ships in November. " * 12
    script = Script([
        _call("safe_web_fetch", url=DOCS),
        ModelTurn(content=long_answer),
        ModelTurn(content=long_answer + f"([release notes]({DOCS}))"),
    ])
    turn, run, _log = _chat_turn(script)
    result, trace = _finish(turn, run, "Summarise the release")
    assert "cites none of them" in script.calls[2]["messages"][-1]["content"]
    assert trace["corrections"] == ["uncited"] and DOCS in result.text


def test_out_of_budget_still_answers_from_what_was_found():
    script = Script([_call("web_search", query=f"angle {i}") for i in range(6)]
                    + [ModelTurn(content="From what I found: it ships 1 November 2026 (not confirmed by a second source).")])
    turn, run, log = _chat_turn(script, profile="compact")
    assert run.budget == 6
    result, trace = _finish(turn, run, "Everything about product X")
    assert len(log) == 6 and result.stop_reason == "max_steps"
    assert script.calls[-1]["tools"] == []  # the final answer is written without tools
    assert "research budget is used up" in script.calls[-1]["messages"][-1]["content"]
    assert result.text.startswith("From what I found") and "ran out of steps" not in result.text


def test_file_grounded_explanation_reads_the_file_first():
    script = Script([_call("file_read", path="src/math.py"),
                     ModelTurn(content="`add` in src/math.py returns the sum of its two arguments.")])
    turn, run, log = _chat_turn(script)
    result, trace = _finish(turn, run, "What does src/math.py do?")
    assert log == ["file:src/math.py"] and trace["path"] == "files"
    assert trace["evidence"]["files"] == ["src/math.py"] and trace["corrections"] == []


def test_follow_up_can_cite_pages_read_earlier_in_the_chat_without_searching_again():
    earlier = [{"id": "src_1", "url": DOCS, "title": "Release notes"}]
    script = Script([ModelTurn(content=f"As the release notes said, offline mode is included ([notes]({DOCS})).")])
    turn, run, log = _chat_turn(script, earlier=earlier)
    result, trace = _finish(turn, run, "Does it include offline mode?")
    assert log == [] and trace["citations"]["read"] == [chat_run.normalize_url(DOCS)] and trace["corrections"] == []


def test_links_in_an_answer_that_did_no_research_are_left_alone_but_traced():
    run = chat_run.ChatRun(session_id="t")
    assert run.check_answer("See [Python docs](https://docs.python.org/3/).") == ""
    assert run.cite_audit("See [Python docs](https://docs.python.org/3/).")["unknown"] == ["https://docs.python.org/3"]


def test_model_profiles_and_url_matching():
    assert chat_run.profile_for("qwen/qwen3-4b", local=True) == "compact"
    assert chat_run.profile_for("google/gemma-3n-e4b", local=True) == "compact"
    assert chat_run.profile_for("llama-3.1-70b-instruct", local=True) == "full"
    assert chat_run.profile_for("my-custom-model", local=True) == "full"  # size unknown: full
    assert chat_run.profile_for("gpt-5.5", local=False) == "full"
    assert chat_run.normalize_url("https://www.Example.com/a/?utm_source=x&id=2#top") == "https://example.com/a?id=2"
    assert chat_run.links_in(f"[a]({DOCS}) and {BLOG}.") == [DOCS, BLOG]


# ── Through the real session: profile, prompt order, earlier sources, memory, saved trace ──

@pytest.fixture
def env(tmp_path, monkeypatch):
    chats = threads.ThreadManager(tmp_path / "threads.json")
    monkeypatch.setattr(threads, "get_thread_manager", lambda: chats)
    room_store = rooms.RoomStore(tmp_path / "rooms.json")
    monkeypatch.setattr(rooms, "get_room_store", lambda: room_store)
    store = state.StateStore(tmp_path / "state")
    monkeypatch.setattr(state, "_state_store", store)
    from agent import research_notebook
    notebook = research_notebook.ResearchNotebook(tmp_path / "research")
    monkeypatch.setattr(research_notebook, "ResearchNotebook", lambda root=None: notebook)
    return chats, notebook


def _session(monkeypatch, chats, model: str, recalled=()):
    monkeypatch.setattr(lean_runtime.LeanSession, "_recall", lambda self, query, limit=8: list(recalled))
    thread = chats.create_thread(title="Chat", source="web")
    return lean_runtime.LeanSession(
        agent=SimpleNamespace(memory=None, llm_provider=SimpleNamespace(value="lmstudio"), model_runtime=SimpleNamespace(model_id=model)),
        session_id=thread.thread_id, request_id="req", emit=lambda _e: None, cancel=threading.Event(), source="web")


def test_a_small_local_model_gets_the_compact_contract_and_fewer_tools(env, monkeypatch):
    chats, _ = env
    small = _session(monkeypatch, chats, "qwen/qwen3-4b")
    turn = small._build_turn(small._chat_persona(), "hi", history=[], depth=0)
    assert small._chat_run.profile == "compact" and turn.max_steps_override == 6
    assert "## When to use tools" not in turn.system_prompt and "## How to answer" in turn.system_prompt
    large = _session(monkeypatch, chats, "qwen/qwen3-32b")
    big_turn = large._build_turn(large._chat_persona(), "hi", history=[], depth=0)
    assert large._chat_run.profile == "full" and big_turn.max_steps_override == 10
    assert len(turn.system_prompt) < len(big_turn.system_prompt) * 0.7


def test_prompt_is_ordered_for_caching_and_carries_memory_and_earlier_sources(env, monkeypatch):
    chats, notebook = env
    session = _session(monkeypatch, chats, "qwen/qwen3-32b",
                       recalled=[{"memory_id": "m1", "scope": "account", "content": "Prefers metric units.", "semantic_key": "units"}])
    notebook.retain(session.session_id, url=DOCS, title="Release notes", text="Version 5 ships…", inspected=True)
    notebook.retain(session.session_id, url=BLOG, title="Search snippet only", text="…", inspected=False)
    prompt = session._build_turn(session._chat_persona(), "and how heavy is it?", history=[], depth=0).system_prompt
    parts = prompt.split("\n\n## ")
    assert parts[-1].startswith("Current time")  # the per-minute clock is last: the prefix stays cacheable
    assert "Prefers metric units." in prompt
    assert "## Pages read earlier in this chat" in prompt and DOCS in prompt and BLOG not in prompt
    context = session._chat_run.context
    assert context["sections"]["clock"] > 0 and context["prompt_chars"] == len(prompt)
    assert prompt.index("## How to answer") < prompt.index("## What you can use in Chat") < prompt.index("## Pages read earlier")


def test_the_run_trace_is_saved_on_the_execution(env, monkeypatch):
    chats, _ = env
    session = _session(monkeypatch, chats, "qwen/qwen3-32b")
    monkeypatch.setattr(lean_runtime.LeanSession, "_client_for", lambda self, persona, routing=False: Script([ModelTurn(content="Hello!")]))
    result = session.run("hello")
    assert result["response"] == "Hello!"
    execution = state.get_state_store().get_execution(result["execution_id"])
    trace = execution.metadata["chat_run"]
    assert trace["path"] == "direct" and trace["profile"] == "full" and trace["stop_reason"] == "answered"
    assert trace["context"]["prompt_chars"] > 0 and "rules" in trace["context"]["sections"]
    assert "Hello" not in str(trace)  # refs and sizes only: no prompt or reply text
