"""11.5 ask and recover: search failures say what to do next, agents can ask the
user a question with choices (ask_user), and the prompt says to try a fallback,
then ask, instead of ending on an apology."""
from __future__ import annotations

import threading
from typing import Any

import pytest

from agent.lean import ask, steps
from agent.lean.approvals import get_approval_broker
from agent.lean.loop import LeanTurn
from agent.lean.personas import AgentPersona
from agent.lean.provider import ModelTurn, ToolCall
from agent.lean.toolbox import NativeTool, Toolbox, describe_call
from agent.web_search_providers import SearchProviderResult, describe_search_failure, format_hits_for_tool


class ScriptedClient:
    def __init__(self, turns: list[ModelTurn]) -> None:
        self.turns = list(turns)
        self.calls: list[list[dict[str, Any]]] = []

    def stream_turn(self, messages, *, tools=None, on_reasoning=None, on_content=None, cancel=None, temperature=None, max_tokens=None):
        self.calls.append([dict(m) for m in messages])
        turn = self.turns.pop(0)
        if turn.content and on_content:
            on_content(turn.content)
        return turn


def _turn(client: ScriptedClient, events: list[dict[str, Any]]) -> LeanTurn:
    box = Toolbox(toolsets=["none"], session_id="t")
    box.native = {ask.ASK_USER: ask.ask_user_tool()}
    return LeanTurn(
        client=client,  # type: ignore[arg-type]
        persona=AgentPersona(id="echo", name="Echo"),
        system_prompt="system",
        history=[],
        toolbox=box,
        session_id="t",
        request_id="r",
        execution_id="e",
        emit=events.append,
        cancel=threading.Event(),
        persist_tool_runs=False,
    )


# ── 1. search failures are actionable ─────────────────────────────────

def test_missing_search_package_says_what_to_do_instead_of_install():
    text = describe_search_failure(["DuckDuckGo package not installed (ddgs / duckduckgo_search)"])
    assert text.startswith("Web search failed.")
    assert "Settings › Web search" in text and "safe_web_fetch" in text and "ask_user" in text
    assert "Don't tell the user to install" in text


@pytest.mark.parametrize("error, phrase", [
    ("RatelimitException: 202 Ratelimit", "rate-limiting"),
    ("ConnectTimeout: timed out", "couldn't reach the internet"),
    ("something odd", "Every search provider failed"),
])
def test_failure_causes_are_named(error, phrase):
    assert phrase in describe_search_failure([error])


def test_vague_query_error_passes_through_unchanged():
    vague = "Search query is too vague; provide a concrete subject, entity, or question."
    assert describe_search_failure([vague]) == vague


def test_tool_output_and_step_summary_for_a_failed_search(monkeypatch):
    import agent.web_search_providers as providers
    from agent.tools import web_search

    failed = SearchProviderResult(provider="duckduckgo", errors=["DuckDuckGo package not installed (ddgs / duckduckgo_search)"])
    assert format_hits_for_tool(failed).startswith("Web search failed.")
    monkeypatch.setattr(providers, "run_web_search", lambda *a, **k: failed)
    output = web_search.invoke({"query": "latest AI news"})
    assert output.startswith("Web search failed.") and "ask_user" in output
    assert steps.summarize("web_search", {}, True, output, {"results": 0}) == "Search isn't working"
    assert steps.summarize("web_search", {}, True, "No search results found.", {"results": 0}) == "No results"


# ── 2. ask_user ───────────────────────────────────────────────────────

def test_ask_user_arguments_are_checked():
    assert ask.parse({"question": "", "options": ["a", "b"]})[3].startswith("Error")
    assert ask.parse({"question": "Which?", "options": ["only one"]})[3].startswith("Error")
    question, options, allow_other, problem = ask.parse({"question": " Which  one? ", "options": ["A", "a", "B", "C", "D", "E"]})
    assert (question, options, allow_other, problem) == ("Which one?", ["A", "B", "C", "D"], True, "")
    assert ask.parse({"question": "Q", "options": "Yes | No"})[1] == ["Yes", "No"]


def test_question_answers_are_validated():
    broker = get_approval_broker()
    closed = broker.open_question(session_id="s", request_id="r", question="Go?", options=["Yes", "No"], allow_other=False)
    with pytest.raises(ValueError):
        broker.resolve(closed.id, "Maybe later")
    assert broker.resolve(closed.id, "yes").answer == "yes"
    open_ = broker.open_question(session_id="s", request_id="r", question="Go?", options=["Yes", "No"], allow_other=True)
    resolved = broker.resolve(open_.id, "  try   reuters instead ")
    assert (resolved.decision, resolved.answer) == ("answered", "try reuters instead")
    with pytest.raises(ValueError):
        broker.resolve("apr_missing", "maybe")  # approvals still only take allow / deny / always


def test_the_agent_asks_and_gets_the_users_answer():
    client = ScriptedClient([
        ModelTurn(content="Search isn't working here.", tool_calls=[ToolCall(
            "q1", "ask_user", '{"question": "How should I get the news?", "options": ["Read a news site", "Set up a search key", "Skip it"]}')]),
        ModelTurn(content="Okay, reading a news site."),
    ])
    events: list[dict[str, Any]] = []
    turn = _turn(client, events)

    def answer_when_asked(event):
        events.append(event)
        if event["type"] == "approval_request":
            threading.Timer(0.05, lambda: get_approval_broker().resolve(event["id"], "Read a news site")).start()

    turn._emit = answer_when_asked
    result = turn.run("latest AI news")
    assert result.text.endswith("Okay, reading a news site.")
    request = next(e for e in events if e["type"] == "approval_request")
    assert request["kind"] == "question" and request["options"] == ["Read a news site", "Set up a search key", "Skip it"]
    assert client.calls[1][-1]["content"] == "The user answered: Read a news site"
    resolved = next(e for e in events if e["type"] == "approval_resolved")
    assert (resolved["decision"], resolved["answer"]) == ("answered", "Read a news site")
    end = next(e for e in events if e["type"] == "tool_end")
    assert end["done_label"].startswith("Asked you") and end["summary"] == "You chose: Read a news site"
    saved = next(item for item in turn.timeline if item.get("kind") == "approval")
    assert saved["question"] == "How should I get the news?" and saved["answer"] == "Read a news site"


def test_channels_without_cards_are_told_to_ask_in_text():
    client = ScriptedClient([
        ModelTurn(tool_calls=[ToolCall("q1", "ask_user", '{"question": "Which?", "options": ["A", "B"]}')]),
        ModelTurn(content="Which do you want, A or B?"),
    ])
    turn = _turn(client, [])
    turn.interactive = False
    turn.run("go")
    assert "Ask the user in plain text" in client.calls[1][-1]["content"]


def test_ask_user_reads_well_in_the_timeline():
    assert describe_call("ask_user", {"question": "How should I get the news?"}) == "Asking you: How should I get the news?"
    assert steps.done_label("Asking you: How should I get the news?") == "Asked you: How should I get the news?"


# ── 3. the prompt rule ────────────────────────────────────────────────

def test_prompt_says_try_a_fallback_then_ask():
    from agent.lean.prompt import WORKING_RULES

    assert "try one other route" in WORKING_RULES and "call ask_user with 2-4 concrete options" in WORKING_RULES
    assert "Never end on just an apology" in WORKING_RULES
