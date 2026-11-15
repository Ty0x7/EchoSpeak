"""Model compatibility found by running five local models through the real harness (five-model evaluation).

deepseek-r1:8b: Ollama's chat template for it never renders the tool definitions, so the model was told
about tools it couldn't see and invented a script's output. The harness now notices from the server's own
prompt count and gives such a model its tools as text.
"""
from __future__ import annotations

import threading
from typing import Any

import pytest

from agent.lean import tool_prompting, window
from agent.lean.loop import LeanTurn, _estimate_tokens
from agent.lean.personas import AgentPersona
from agent.lean.provider import Endpoint, ModelTurn, ToolCall, extract_text_tool_calls
from agent.lean.toolbox import NativeTool, Toolbox

DEEPSEEK = Endpoint("http://127.0.0.1:11434/v1", "not-needed", "deepseek-r1:8b", "ollama", True)
SCHEMA = {"type": "object", "properties": {"command": {"type": "string", "description": "the command " * 30}},
          "required": ["command"]}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    tool_prompting.reset()
    window.reset()
    monkeypatch.setattr(window, "_fetch_ollama", lambda base, model: 16384)
    yield
    tool_prompting.reset()
    window.reset()


def test_definitions_the_server_never_counted_were_not_rendered():
    # deepseek-r1:8b, live: ~4,600 estimated with tools, ~2,900 without, 2,699 counted.
    assert tool_prompting.observe(DEEPSEEK, 4600, 2900, 2699) is True
    assert tool_prompting.needed(DEEPSEEK) and tool_prompting.describe(DEEPSEEK) == "prompted"


def test_rendered_definitions_are_left_alone():
    qwen = Endpoint("http://127.0.0.1:11434/v1", "", "qwen3.5:9b", "ollama", True)
    # qwen3.5:9b, live: the server counts more than the estimate with tools (0.85-1.3x of it).
    assert tool_prompting.observe(qwen, 4600, 2900, 5542) is False
    assert tool_prompting.known(qwen) and not tool_prompting.needed(qwen)
    # Too few definitions to measure: no verdict either way.
    other = Endpoint("http://127.0.0.1:11434/v1", "", "tiny", "ollama", True)
    assert tool_prompting.observe(other, 3000, 2900, 2000) is False and not tool_prompting.known(other)


def test_the_catalog_names_every_tool_and_the_one_way_to_call_it():
    tools = [{"type": "function", "function": {"name": "terminal", "description": "Run a command. More text.", "parameters": SCHEMA}}]
    text = tool_prompting.catalog(tools)
    assert "- terminal(command*): Run a command." in text and "<tool_call>" in text
    messages = [{"role": "system", "content": "RULES"}, {"role": "user", "content": "go"}]
    sent = tool_prompting.with_catalog(messages, tools)
    assert sent[0]["content"].startswith("RULES") and "## Calling tools" in sent[0]["content"]
    assert messages[0]["content"] == "RULES"  # the stored history is untouched


def test_deepseek_native_calls_are_read():
    text = ("I'll run it.<｜tool▁calls▁begin｜><｜tool▁call▁begin｜>function<｜tool▁sep｜>terminal\n```json\n"
            '{"command": "python report.py"}\n```<｜tool▁call▁end｜><｜tool▁calls▁end｜>')
    calls, cleaned = extract_text_tool_calls(text, {"terminal"})
    assert [(c.name, c.arguments) for c in calls] == [("terminal", '{"command": "python report.py"}')]
    assert cleaned == "I'll run it."


def test_announcing_the_next_read_is_a_promise_not_an_answer():
    """gemma4 ended H6 twice with "I will now check the next log file" and no tool call."""
    from agent.lean.job import is_promise_without_action

    assert is_promise_without_action("The `auth.log` does not contain any mention of 'quota exceeded'. "
                                     "I will now check the next log file, `billing.log`.")
    assert is_promise_without_action("Next, I'll read billing.log.")
    assert not is_promise_without_action("The mailer service reported 'quota exceeded' on 2026-11-06.")
    assert not is_promise_without_action("Want me to check the other logs too?")


def test_appending_to_a_file_adds_a_line():
    """gemma4 (2 of 3 runs) and qwen3.5 (once): "alpha" then append "beta" gave "alphabeta"."""
    from agent.tools import append_text

    assert append_text("alpha", "beta") == ("alpha\nbeta", True)
    assert append_text("alpha\n", "beta") == ("alpha\nbeta", False)
    assert append_text("alpha", "\nbeta") == ("alpha\nbeta", False)
    assert append_text("", "beta") == ("beta", False)


def test_the_approval_preview_shows_the_line_that_will_be_written(tmp_path):
    from agent.lean.file_changes import proposed

    (tmp_path / "notes.md").write_text("alpha")
    change = proposed("file_write", {"path": str(tmp_path / "notes.md"), "content": "beta", "append": True}, str(tmp_path))
    added = [text for hunk in change["hunks"] for tag, text in hunk["lines"] if tag == "+"]
    removed = [text for hunk in change["hunks"] for tag, text in hunk["lines"] if tag == "-"]
    assert added == ["beta"] and removed == [] and change["total_lines"] == 2  # before: -alpha +alphabeta


class Client:
    """A server whose template drops tool definitions: it counts only the messages."""

    def __init__(self, turns: list[ModelTurn]) -> None:
        self.endpoint = DEEPSEEK
        self.turns = list(turns)
        self.calls: list[list[dict[str, Any]]] = []

    def stream_turn(self, messages, **kwargs):
        self.calls.append([dict(m) for m in messages])
        turn = self.turns.pop(0)
        prompt = _estimate_tokens(messages, [])  # the definitions in `tools` aren't rendered
        turn.usage = {"prompt": prompt, "completion": 20, "total": prompt + 20}
        if turn.content and kwargs.get("on_content"):
            kwargs["on_content"](turn.content)
        return turn


def test_a_reply_made_without_seeing_the_tools_is_discarded_and_asked_again_with_them():
    ran: list[str] = []
    box = Toolbox(toolsets=["none"], session_id="t")
    box.native = {"run_report": NativeTool("run_report", "Run a report. " + "Details. " * 200, SCHEMA,
                                         lambda a: ran.append(a["command"]) or "[ok · this PC]\nTOTAL 165")}
    client = Client([
        ModelTurn(content="The script printed: Total: 4,285.75", finish_reason="stop"),  # invented
        ModelTurn(content='<tool_call>{"name": "run_report", "arguments": {"command": "python report.py"}}</tool_call>',
                  finish_reason="stop"),
        ModelTurn(content="It printed TOTAL 165.", finish_reason="stop"),
    ])
    events: list[dict[str, Any]] = []
    turn = LeanTurn(client=client, persona=AgentPersona(id="echo", name="Echo"), system_prompt="RULES " * 400, history=[],
                    toolbox=box, session_id="t", request_id="r", execution_id="e", emit=events.append,
                    cancel=threading.Event(), persist_tool_runs=False)
    result = turn.run("Run report.py and tell me the TOTAL.")
    assert ran == ["python report.py"]
    assert result.text == "It printed TOTAL 165." and "4,285.75" not in result.text
    assert any(e["type"] == "tools_prompted" for e in events)
    assert "## Calling tools" not in client.calls[0][0]["content"]
    assert "## Calling tools" in client.calls[1][0]["content"] and "## Calling tools" in client.calls[2][0]["content"]
    assert not any(e["type"] == "context_truncated" for e in events)  # a smaller count is not a cut
