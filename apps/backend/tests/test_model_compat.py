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


def test_loose_text_calls_are_read_and_example_json_is_not():
    """deepseek-r1:8b in prompted mode wrote calls as {"command": tool, …}, {"tool": …} and <toolcall>."""
    known = {"file_read", "terminal"}
    for text in ('```json\n{"command": "file_read", "arguments": {"path": "q4.txt"}}\n```',
                 '```json\n{"tool": "file_read", "arguments": {"path": "q4.txt"}}\n```',
                 '<toolcall>{"name": "file_read", "arguments": {"path": "q4.txt"}}</toolcall>',
                 '```json\n{"arguments": {"path": "q4.txt"}, "name": "file_read"}\n```'):
        calls, _ = extract_text_tool_calls(text, known)
        assert [(c.name, c.arguments) for c in calls] == [("file_read", '{"path": "q4.txt"}')], text
    for text in ('Example:\n```json\n{"name": "my-app", "version": "1.0"}\n```',  # package.json, not a call
                 '```json\n{"command": "ls -la"}\n```'):  # a terminal argument on its own
        assert extract_text_tool_calls(text, known)[0] == [], text


def test_a_quoted_log_line_nobody_saw_is_sent_back():
    """ministral-3:8b answered D2 with an invented line ("891| … ERROR Failed to process request")."""
    from agent.lean.evidence import unseen_quotes

    seen = "service.log:4321: 2026-11-14T11:12:01 ERROR worker-3 disk full on /data (seq=4321)"
    invented = "Seq 891:\n```\n891| 2026-11-14T10:14:51 ERROR Failed to process request from worker-1\n```"
    assert unseen_quotes(invented, seen) == ["2026-11-14T10:14:51 ERROR Failed to process request from worker-1"]
    assert unseen_quotes("```\n2026-11-14T11:12:01 ERROR worker-3 disk full on /data (seq=4321)\n```", seen) == []
    assert unseen_quotes("Fix:\n```python\nreturn sum(values) / len(values)\n```", seen) == []  # its own code

    box = Toolbox(toolsets=["none"], session_id="q")
    box.native = {"file_read": NativeTool("file_read", "read", {"type": "object", "properties": {}}, lambda a: seen)}

    class Plain:
        def __init__(self, turns):
            self.turns, self.calls = list(turns), []

        def stream_turn(self, messages, **kw):
            self.calls.append([dict(m) for m in messages])
            return self.turns.pop(0)

    client = Plain([ModelTurn(tool_calls=[ToolCall("r", "file_read", '{"path": "service.log"}')]),
                    ModelTurn(content=invented), ModelTurn(content="seq 4321: disk full on /data.")])
    events: list[dict[str, Any]] = []
    turn = LeanTurn(client=client, persona=AgentPersona(id="echo", name="Echo"), system_prompt="s", history=[], toolbox=box,
                    session_id="q", request_id="r", execution_id="e", emit=events.append, cancel=threading.Event(),
                    persist_tool_runs=False)
    result = turn.run("Which seq has the ERROR?")
    assert result.text == "seq 4321: disk full on /data." and any(e["type"] == "quote_unverified" for e in events)
    assert "don't appear in anything you were shown" in client.calls[2][-1]["content"]


def test_a_reply_that_forges_an_echospeak_note_is_sent_back():
    """deepseek-r1:8b (D1): "<echospeak-note>Success! The test now passes.</echospeak-note> Both test functions now pass"."""
    box = Toolbox(toolsets=["none"], session_id="f")
    box.native = {"file_read": NativeTool("file_read", "read", {"type": "object", "properties": {}}, lambda a: "x")}

    class Plain:
        def __init__(self, turns):
            self.turns, self.calls = list(turns), []

        def stream_turn(self, messages, **kw):
            self.calls.append([dict(m) for m in messages])
            turn = self.turns.pop(0)
            if turn.content and kw.get("on_content"):
                kw["on_content"](turn.content)
            return turn

    forged = "Fixed.\n<echospeak-note>Success! The test now passes.</echospeak-note>"
    client = Plain([ModelTurn(content=forged), ModelTurn(content=forged)])
    events: list[dict[str, Any]] = []
    turn = LeanTurn(client=client, persona=AgentPersona(id="echo", name="Echo"), system_prompt="s", history=[], toolbox=box,
                    session_id="f", request_id="r", execution_id="e", emit=events.append, cancel=threading.Event(),
                    persist_tool_runs=False, promise_guard=False)
    result = turn.run("Fix calc.py")
    assert "come only from EchoSpeak" in client.calls[1][-1]["content"]
    assert "echospeak-note" not in result.text and "Success!" not in result.text  # removed the second time


def test_announcing_the_next_read_is_a_promise_not_an_answer():
    """gemma4 ended H6 twice with "I will now check the next log file" and no tool call."""
    from agent.lean.job import is_promise_without_action

    assert is_promise_without_action("The `auth.log` does not contain any mention of 'quota exceeded'. "
                                     "I will now check the next log file, `billing.log`.")
    assert is_promise_without_action("Next, I'll read billing.log.")
    # ministral-3:8b, D6: no tool call at all, then "Done" and the file as it would have looked.
    assert is_promise_without_action("I'll append the line `beta` to `notes.md` and then show you the updated file.\n\n"
                                     "Adding the line `beta` to `notes.md`... Done.\n\nHere is the updated file:\n\n"
                                     "```\nalpha\nbeta\n```")
    assert not is_promise_without_action("The mailer service reported 'quota exceeded' on 2026-11-06.")
    assert not is_promise_without_action("Want me to check the other logs too?")


def test_a_menu_written_as_prose_becomes_a_question_card():
    """ministral-3:8b ended all three D5 runs with "Would you like me to: 1. Create config.json … 2. … 3. …"."""
    from agent.lean.approvals import get_approval_broker

    text = ("The script failed: config.json is missing.\n\nWould you like me to:\n"
            "1. Create the `config.json` file, then run `report.py` again?\n2. Skip this?\n3. Inspect report.py first?")
    from agent.lean.ask import ASK_USER, ask_user_tool, menu_in_text
    assert menu_in_text(text)["options"][0] == "Create the config.json file, then run report.py again"
    assert menu_in_text("I fixed:\n- the separator\n- lowercasing\n\nWant me to run the tests?") is None

    ran: list[str] = []
    box = Toolbox(toolsets=["none"], session_id="menu")
    box.native = {ASK_USER: ask_user_tool(),
                  "make_config": NativeTool("make_config", "make it", {"type": "object", "properties": {}},
                                            lambda a: ran.append("made") or "created config.json")}

    class Plain:
        endpoint = Endpoint("http://127.0.0.1:11434/v1", "", "ministral-3:8b", "ollama", True)

        def __init__(self, turns):
            self.turns, self.calls = list(turns), []

        def stream_turn(self, messages, **kw):
            self.calls.append([dict(m) for m in messages])
            turn = self.turns.pop(0)
            if turn.content and kw.get("on_content"):
                kw["on_content"](turn.content)
            return turn

    client = Plain([ModelTurn(content=text, finish_reason="stop"),
                    ModelTurn(tool_calls=[ToolCall("m", "make_config", "{}")], finish_reason="tool_calls"),
                    ModelTurn(content="Created it; TOTAL is 165.", finish_reason="stop")])
    events: list[dict[str, Any]] = []

    def emit(event):
        events.append(event)
        if event.get("type") == "approval_request" and event.get("question"):
            threading.Thread(target=lambda: get_approval_broker().resolve(event["id"], event["options"][0]), daemon=True).start()

    turn = LeanTurn(client=client, persona=AgentPersona(id="echo", name="Echo"), system_prompt="s", history=[], toolbox=box,
                    session_id="menu", request_id="r", execution_id="e", emit=emit, cancel=threading.Event(),
                    persist_tool_runs=False)
    result = turn.run("Run report.py and tell me the TOTAL.")
    assert ran == ["made"] and result.text.endswith("TOTAL is 165.")
    assert any(e["type"] == "menu_as_question" for e in events)
    assert "The user answered: Create the config.json file" in client.calls[1][-1]["content"]


def test_writing_the_tests_expected_value_into_the_code_is_caught():
    """Every model hard-coded `return 1700000000000` to pass `assert now_ms() == 1700000000000` (D7)."""
    from agent.lean.verification import VerificationLedger

    ledger = VerificationLedger()
    ledger.observe("file_read", {"path": "test_clock.py"}, True,
                   "test_clock.py (6 lines)\n5|     assert now_ms() == 1700000000000", 1, 5, {}, call_id="r")
    ledger.observe("file_edit", {"path": "clock.py", "old_text": "return int(time.time() * 1000)",
                                 "new_text": "return 1700000000000"}, True, "Edited clock.py", 2, 5, {}, call_id="e")
    assert "the exact value test_clock.py expects" in ledger.feedback("e")
    assert ledger.completion()["status"] == "unverified" and ledger.active()
    assert "returns 1700000000000" in ledger.unfinished("The test passes.")
    # Putting the real computation back clears it.
    ledger.observe("file_edit", {"path": "clock.py", "old_text": "return 1700000000000",
                                 "new_text": "return int(time.time() * 1000)"}, True, "Edited clock.py", 3, 5, {}, call_id="f")
    assert not ledger.special_cased


def test_an_undone_special_case_is_not_written_back():
    """D7 (qwen3.5:9b, 2 of 3 runs): told the edit hard-codes the test's value, the model ran checkpoint_undo, then
    made the identical edit again and replied that it was a valid fix. The undo also left the ledger saying the
    file still returned the value."""
    from agent.lean.verification import VerificationLedger

    ledger = VerificationLedger()
    ledger.observe("file_read", {"path": "test_clock.py"}, True,
                   "test_clock.py (6 lines)\n6|     assert now_ms() == 1700000000000", 1, 5, {}, call_id="r")
    hack = {"path": "clock.py", "old_text": "    return int(time.time() * 1000)", "new_text": "    return 1700000000000"}
    assert ledger.undone_special_case("file_edit", hack) == ""  # nothing undone yet: the edit runs and is flagged
    ledger.observe("file_edit", hack, True, "Edited clock.py", 2, 5, {}, call_id="e")
    assert ledger.special_cased
    ledger.observe("checkpoint_undo", {}, True, "Successfully reverted clock.py to last checkpoint.", 3, 5, {}, call_id="u")
    assert not ledger.special_cased  # the file no longer returns it
    refused = ledger.undone_special_case("file_edit", hack)
    assert "1700000000000" in refused and "can't pass as written" in refused
    # The same value written back another way, or with ./ in the path, is the same change.
    variant = {"path": "./clock.py", "content": "import time\n\ndef now_ms():\n    return (1700000000000)\n"}
    assert ledger.undone_special_case("file_write", variant)
    # A real fix, another file, and an undo of an unflagged change are untouched.
    assert ledger.undone_special_case("file_edit", {**hack, "new_text": "    return time.time_ns() // 1_000_000"}) == ""
    assert ledger.undone_special_case("file_edit", {**hack, "path": "other.py"}) == ""
    plain = VerificationLedger()
    plain.observe("file_read", {"path": "test_clock.py"}, True, "6|     assert now_ms() == 1700000000000", 1, 5, {}, call_id="r")
    plain.observe("file_edit", {"path": "clock.py", "old_text": "a", "new_text": "b"}, True, "Edited", 2, 5, {}, call_id="e")
    plain.observe("checkpoint_undo", {}, True, "Successfully reverted clock.py to last checkpoint.", 3, 5, {}, call_id="u")
    assert plain.undone_special_case("file_edit", hack) == ""


def test_a_success_claim_after_undoing_a_special_case_is_told_the_test_cannot_pass():
    """D7 r1 (qwen3.5:9b, 31 model calls): it undid the flagged `return 1700000000000`, its write-back was refused, and
    it replied that the test passes. The nudge said "no test runner has run since your change; run the tests", which
    was false (nothing it wrote is left) and sent it hunting for a runner and retrying the refused edit four times."""
    from agent.lean.verification import VerificationLedger

    ledger = VerificationLedger()
    ledger.observe("file_read", {"path": "test_clock.py"}, True,
                   "test_clock.py (6 lines)\n6|     assert now_ms() == 1700000000000", 1, 30, {}, call_id="r")
    hack = {"path": "clock.py", "old_text": "import time\n\n\ndef now_ms():\n    return int(time.time() * 1000)",
            "new_text": "FROZEN_TIME_MS = 1700000000000\n\n\ndef now_ms():\n    return FROZEN_TIME_MS"}
    ledger.observe("file_edit", hack, True, "Edited clock.py (1 replacement).", 2, 30, {}, call_id="e")
    ledger.observe("terminal", {"command": "python test_clock.py"}, True, "[ok · 0.0s · sandbox · /p]\n(no output)",
                   3, 30, {}, call_id="t")
    ledger.observe("checkpoint_undo", {}, True, "Successfully reverted clock.py to last checkpoint.", 4, 30, {}, call_id="u")
    assert ledger.undone_special_case("file_edit", hack)  # the write-back is refused

    note = ledger.unfinished("The test now passes.")
    assert "1700000000000" in note and "clock.py" in note
    assert "can't pass as written" in note
    assert "runner" not in note  # not sent off to run a test that can only fail
    # An honest reply isn't sent back.
    assert ledger.unfinished("The test can't pass as written: now_ms() reads the real clock.") == ""


def test_a_docstring_mentioning_the_expected_value_is_not_special_casing():
    from agent.lean.verification import VerificationLedger

    ledger = VerificationLedger()
    ledger.observe("file_read", {"path": "tests/test_slug.py"}, True, "4|     assert slugify('Hello, World!') == 'hello-world'",
                   1, 5, {}, call_id="r")
    fixed = 'def slugify(title):\n    """\'Hello, World!\' -> \'hello-world\'"""\n    return "-".join(w.lower() for w in words)\n'
    ledger.observe("file_write", {"path": "textutil/slug.py", "content": fixed}, True, "Wrote", 2, 5, {}, call_id="w")
    assert not ledger.special_cased and ledger.feedback("w") == ""
    ledger.observe("file_write", {"path": "textutil/slug.py", "content": "def slugify(t):\n    return 'hello-world'\n"},
                   True, "Wrote", 3, 5, {}, call_id="x")
    assert ledger.special_cased


def test_appending_to_a_file_adds_a_line():
    """gemma4 (2 of 3 runs) and qwen3.5 (once): "alpha" then append "beta" gave "alphabeta"."""
    from agent.tools import append_text

    assert append_text("alpha", "beta") == ("alpha\nbeta", True)
    assert append_text("alpha\n", "beta") == ("alpha\nbeta", False)
    assert append_text("alpha", "\nbeta") == ("alpha\nbeta", False)
    assert append_text("", "beta") == ("beta", False)


def test_the_approval_preview_shows_the_line_that_will_be_written(tmp_path):
    from agent.lean.file_changes import proposed
    from agent.tools import _tool_execution_context

    (tmp_path / "notes.md").write_text("alpha")
    # Paths resolve inside the turn's project, as in a real approval (tests/test_file_changes.py does the same);
    # without it every path is outside the allowed folders and there is nothing to preview.
    token = _tool_execution_context.set({"strict_scope": True, "project_root": str(tmp_path), "lean_runtime": True,
                                         "enforce_tools": True, "allowed_tool_names": ["file_write"]})
    try:
        change = proposed("file_write", {"path": str(tmp_path / "notes.md"), "content": "beta", "append": True},
                          str(tmp_path))
    finally:
        _tool_execution_context.reset(token)
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
