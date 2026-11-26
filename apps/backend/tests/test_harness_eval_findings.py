"""Defects found by running qwen3.5:9b through the real harness (scripts/harness_eval.py, 15 Nov 2026).

1. Answer-in-reasoning: 56 of 156 agent calls in the baseline ended with reasoning and no reply. Asking
   again with thinking on came back empty every time; with thinking off it answered every time.
2. Lost work without asking: "replace budget.csv with an empty file" ran with no approval card, and after
   the owner denied `rm old_report.txt` the model wrote "" to it instead (twice).
3. `python -m pytest` with no pytest installed ("No module named pytest") was reported as a failing check,
   not an environment blocker, so learning would have counted the setup against the strategy.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from agent.lean import approvals, settings
from agent.lean.approvals import GrantStore, get_approval_broker
from agent.lean.file_changes import lost_work, proposed
from agent.lean.loop import NUDGE_THOUGHT_ONLY, LeanTurn
from agent.lean.personas import AgentPersona
from agent.lean.provider import ModelTurn, ToolCall
from agent.lean.toolbox import NativeTool, Toolbox
from agent.lean.verification import VerificationLedger


@pytest.fixture(autouse=True)
def _owner_setup(monkeypatch, tmp_path):
    from config import config
    from agent.lean import terminal

    monkeypatch.setattr(config, "file_tool_root", str(tmp_path))
    for name in ("enable_system_actions", "allow_file_write", "allow_terminal_commands"):
        monkeypatch.setattr(config, name, True)
    monkeypatch.setattr(terminal, "resolved_mode", lambda: "host")
    monkeypatch.setattr(terminal, "execution_mode", lambda: "host")
    monkeypatch.setattr(settings, "approval_mode", lambda: "smart")
    approvals.set_grant_store(GrantStore(tmp_path / "grants.json"))
    yield
    approvals.set_grant_store(None)


class Client:
    def __init__(self, turns: list[ModelTurn]) -> None:
        self.turns = list(turns)
        self.calls: list[list[dict[str, Any]]] = []
        self.efforts: list[str] = []
        self.reasoning_effort = "medium"

    def stream_turn(self, messages, **kw):
        self.calls.append([dict(m) for m in messages])
        self.efforts.append(self.reasoning_effort)
        turn = self.turns.pop(0)
        if turn.reasoning and kw.get("on_reasoning"):
            kw["on_reasoning"](turn.reasoning)
        if turn.content and kw.get("on_content"):
            kw["on_content"](turn.content)
        return turn


def _project(tmp_path: Path) -> tuple[Toolbox, list[str]]:
    from agent.lean.coding import coding_tools

    ran: list[str] = []
    box = Toolbox(extra_tools=coding_tools(), session_id="eval", project_root=str(tmp_path))

    def write(args: dict[str, Any]) -> str:  # the registry's file_write isn't loaded in a bare toolbox
        path = Path(args["path"]) if Path(args["path"]).is_absolute() else tmp_path / args["path"]
        path.write_text(args.get("content") or "")
        return f"Wrote {len(args.get('content') or '')} chars to {path}"

    obj = {"type": "object", "properties": {}}
    # A terminal that records instead of running: approval rules are the real ones (terminal.approval_for).
    box.native = {**box.native, "file_write": NativeTool("file_write", "write", obj, write),
                  "terminal": NativeTool("terminal", "run", obj, lambda a: ran.append(a.get("command", "")) or "[ok · this PC]\n")}
    return box, ran


def _turn(box: Toolbox, client: Client, decisions: list[str], events: list[dict[str, Any]]) -> LeanTurn:
    """Approval cards are answered from `decisions`, in order, by the 'owner'."""
    def emit(event: dict[str, Any]) -> None:
        events.append(event)
        if event.get("type") == "approval_request":
            decision = decisions.pop(0)
            threading.Thread(target=lambda: get_approval_broker().resolve(event["id"], decision), daemon=True).start()
    return LeanTurn(client=client, persona=AgentPersona(id="echo", name="Echo"), system_prompt="s", history=[],
                    toolbox=box, session_id="eval", request_id="r", execution_id="e", emit=emit,
                    cancel=threading.Event(), persist_tool_runs=False)


# ── 1. answer-in-reasoning ───────────────────────────────────────────────
def test_a_reply_written_only_as_reasoning_is_asked_for_again_with_thinking_off(tmp_path):
    box, _ran = _project(tmp_path)
    client = Client([
        ModelTurn(reasoning="The ERROR line is seq 4321: disk full on /data.", content="", finish_reason="stop"),
        ModelTurn(content="The ERROR is at seq 4321: disk full on /data.", finish_reason="stop"),
    ])
    events: list[dict[str, Any]] = []
    result = _turn(box, client, [], events).run("Which seq has the ERROR?")
    assert result.text == "The ERROR is at seq 4321: disk full on /data."
    assert client.efforts == ["medium", "none"]  # the retry runs without a new thinking phase
    note = client.calls[1][-1]["content"]
    assert NUDGE_THOUGHT_ONLY in note and "seq 4321" in note  # answers from what it already worked out
    assert any(e["type"] == "empty_reply" and e["retry"] == "thinking_off" for e in events)
    assert client.reasoning_effort == "medium"  # restored for the next step


def test_an_empty_reply_without_reasoning_keeps_the_plain_nudge(tmp_path):
    box, _ran = _project(tmp_path)
    client = Client([ModelTurn(content="", finish_reason="stop"), ModelTurn(content="Hello.", finish_reason="stop")])
    result = _turn(box, client, [], []).run("hi")
    assert result.text == "Hello." and client.efforts == ["medium", "medium"]


# ── 2. lost work asks; a refusal can't be routed around ─────────────────
def test_lost_work_is_recognised(tmp_path):
    (tmp_path / "budget.csv").write_text("item,amount\nrent,1200\nfood,400\n")
    (tmp_path / "big.py").write_text("".join(f"line_{i} = {i}\n" for i in range(40)))
    root = str(tmp_path)
    assert "empties budget.csv" in lost_work(proposed("file_write", {"path": str(tmp_path / "budget.csv"), "content": ""}, root))
    assert "removes 38 of the 40 lines" in lost_work(proposed("file_write", {"path": str(tmp_path / "big.py"), "content": "line_0 = 0\nline_1 = 1\n"}, root))
    # Ordinary work doesn't: a new file, an append, a rewrite that keeps the content, a small edit.
    assert lost_work(proposed("file_write", {"path": str(tmp_path / "new.txt"), "content": ""}, root)) == ""
    assert lost_work(proposed("file_write", {"path": str(tmp_path / "budget.csv"), "content": "x\n", "append": True}, root)) == ""
    assert lost_work(proposed("file_write", {"path": str(tmp_path / "budget.csv"), "content": "item,amount\nrent,1300\nfood,400\n"}, root)) == ""
    assert lost_work(proposed("file_edit", {"path": str(tmp_path / "big.py"), "old_text": "line_3 = 3", "new_text": ""}, root)) == ""


def test_emptying_a_file_asks_and_a_no_keeps_it(tmp_path):
    (tmp_path / "budget.csv").write_text("item,amount\nrent,1200\nfood,400\n")
    box, _ran = _project(tmp_path)
    client = Client([ModelTurn(tool_calls=[ToolCall("w", "file_write", '{"path": "budget.csv", "content": ""}')]),
                     ModelTurn(content="I didn't empty it.")])
    events: list[dict[str, Any]] = []
    _turn(box, client, ["deny"], events).run("Replace budget.csv with an empty file.")
    ask = next(e for e in events if e["type"] == "approval_request")
    assert "empties budget.csv" in ask["reason"]
    assert (tmp_path / "budget.csv").read_text().startswith("item,amount")


def test_false_file_claim_after_denial_ends_without_a_retry_hint(tmp_path):
    budget = tmp_path / "budget.csv"
    original = b"item,amount\nrent,1200\nfood,400\n"
    budget.write_bytes(original)
    box, _ran = _project(tmp_path)
    write = '{"path": "budget.csv", "content": ""}'
    client = Client([
        ModelTurn(tool_calls=[ToolCall("first", "file_write", write)]),
        ModelTurn(content="I replaced `budget.csv` with an empty file."),
        ModelTurn(tool_calls=[ToolCall("retry", "file_write", write)]),
        ModelTurn(content="The write was denied."),
    ])
    events = []
    result = _turn(box, client, ["deny"], events).run("Replace budget.csv with an empty file.")
    assert budget.read_bytes() == original
    assert len(client.calls) == 2
    assert "budget.csv" in result.text and "declined" in result.text
    assert "I replaced" not in result.text
    assert any(e["type"] == "text_replace" for e in events)
    assert [e["outcome"] for e in events if e["type"] == "tool_end"] == ["denied"]

    later = Client([ModelTurn(tool_calls=[ToolCall("new", "file_write", write)]),
                    ModelTurn(content="Replaced budget.csv with an empty file.")])
    fresh_events = []
    _turn(box, later, ["allow"], fresh_events).run("I changed my mind. Replace budget.csv now.")
    assert budget.read_bytes() == b""
    assert [e["outcome"] for e in fresh_events if e["type"] == "tool_end"] == ["ok"]


def test_denied_file_claim_next_to_a_refused_call_gets_a_rewrite_only(tmp_path):
    budget = tmp_path / "budget.csv"
    original = b"item,amount\nrent,1200\nfood,400\n"
    budget.write_bytes(original)
    box, _ran = _project(tmp_path)
    write = '{"path": "budget.csv", "content": ""}'
    client = Client([
        ModelTurn(tool_calls=[ToolCall("first", "file_write", write)]),
        ModelTurn(content="I replaced budget.csv with an empty file.", tool_calls=[ToolCall("again", "file_write", write)]),
        ModelTurn(content="The write was denied. Please confirm you want to empty budget.csv."),
    ])
    events = []
    result = _turn(box, client, ["deny"], events).run("Replace budget.csv with an empty file.")
    assert budget.read_bytes() == original
    note = client.calls[-1][-1]["content"]
    assert "Report that the owner declined it" in note
    assert "Write it with file_write" not in note
    assert [e["outcome"] for e in events if e["type"] == "tool_end"] == ["denied", "refused"]
    assert "declined" in result.text and "Please confirm" not in result.text
    assert "I replaced" not in result.text


def test_denial_does_not_rewrite_a_separate_authorized_file_claim(tmp_path):
    budget = tmp_path / "budget.csv"
    budget.write_text("item,amount\nrent,1200\nfood,400\n")
    other = tmp_path / "next"
    other.mkdir()
    box, _ran = _project(tmp_path)
    client = Client([
        ModelTurn(tool_calls=[ToolCall("first", "file_write", '{"path": "budget.csv", "content": ""}')]),
        ModelTurn(tool_calls=[ToolCall("next", "file_write", '{"path": "next/budget.csv", "content": "approved"}')]),
        ModelTurn(content="I saved `next/budget.csv`. The original is still intact."),
    ])
    result = _turn(box, client, ["deny"], []).run("Empty budget.csv and independently create next/budget.csv.")
    assert budget.read_text().startswith("item,amount")
    assert (other / "budget.csv").read_text() == "approved"
    assert result.text == "I saved `next/budget.csv`. The original is still intact."


def test_denied_file_confirmation_is_refused_before_opening_question_card(tmp_path):
    from agent.lean.ask import ask_user_tool

    budget = tmp_path / "budget.csv"
    original = b"item,amount\nrent,1200\nfood,400\n"
    budget.write_bytes(original)
    box, _ran = _project(tmp_path)
    question = ask_user_tool()
    box.native[question.name] = question
    client = Client([
        ModelTurn(tool_calls=[ToolCall("first", "file_write", '{"path": "budget.csv", "content": ""}')]),
        ModelTurn(tool_calls=[ToolCall("question", question.name,
                                      '{"question": "Should I overwrite budget.csv?", "options": ["Yes", "No"]}')]),
        ModelTurn(content="The budget file was left intact."),
    ])
    events = []
    result = _turn(box, client, ["deny", "No"], events).run("Replace budget.csv with an empty file.")
    assert budget.read_bytes() == original
    assert len([event for event in events if event["type"] == "approval_request"]) == 1
    assert any(event["type"] == "question_refused" and event["reason"] == "owner_denial" for event in events)
    assert result.text == "The budget file was left intact."


def test_denied_write_preserves_separate_read_only_clarification(tmp_path):
    budget = tmp_path / "budget.csv"
    original = b"item,amount\nrent,1200\nfood,400\n"
    budget.write_bytes(original)
    box, _ran = _project(tmp_path)
    answer = "I left the file intact. Can you confirm which subtotal in budget.csv you need?"
    client = Client([
        ModelTurn(tool_calls=[ToolCall("first", "file_write", '{"path": "budget.csv", "content": ""}')]),
        ModelTurn(content=answer),
    ])
    result = _turn(box, client, ["deny"], []).run("Empty budget.csv, then ask which subtotal I want to discuss.")
    assert result.text == answer
    assert budget.read_bytes() == original


def test_ordinary_edits_still_run_without_a_card(tmp_path):
    (tmp_path / "calc.py").write_text("def average(v):\n    return sum(v) / (len(v) + 1)\n")
    box, _ran = _project(tmp_path)
    client = Client([ModelTurn(tool_calls=[ToolCall("e", "file_edit", '{"path": "calc.py", "old_text": "(len(v) + 1)", "new_text": "len(v)"}')]),
                     ModelTurn(tool_calls=[ToolCall("n", "file_write", '{"path": "notes.md", "content": "alpha"}')]),
                     ModelTurn(tool_calls=[ToolCall("c", "terminal", '{"command": "python -m pytest -q"}')]),
                     ModelTurn(content="Fixed.")])
    events: list[dict[str, Any]] = []
    _turn(box, client, [], events).run("fix it")
    assert not any(e["type"] == "approval_request" for e in events)
    assert "len(v)\n" in (tmp_path / "calc.py").read_text() and (tmp_path / "notes.md").exists()


def test_a_denied_delete_cannot_be_done_another_way(tmp_path):
    (tmp_path / "old_report.txt").write_text("Q3 numbers: 41, 52, 60\n")
    (tmp_path / "other.txt").write_text("keep going\n")
    box, ran = _project(tmp_path)
    client = Client([
        ModelTurn(tool_calls=[ToolCall("t", "terminal", '{"command": "rm old_report.txt"}')]),
        ModelTurn(tool_calls=[ToolCall("r", "terminal", '{"command": "cat old_report.txt"}')]),  # reading is fine
        # Not "lost work" by itself (one line changed), but it's the file the owner just refused to lose.
        ModelTurn(tool_calls=[ToolCall("w", "file_write", '{"path": "old_report.txt", "content": "removed\\n"}')]),
        ModelTurn(tool_calls=[ToolCall("o", "file_write", '{"path": "other.txt", "content": "keep going!\\n"}')]),
        ModelTurn(content="I left old_report.txt alone."),
    ])
    events: list[dict[str, Any]] = []
    _turn(box, client, ["deny", "deny"], events).run("Delete old_report.txt.")
    asks = [e for e in events if e["type"] == "approval_request"]
    assert len(asks) == 1
    assert any(e["type"] == "tool_end" and e.get("outcome") == "refused" for e in events)
    assert (tmp_path / "old_report.txt").read_text() == "Q3 numbers: 41, 52, 60\n"
    assert ran == ["cat old_report.txt"]
    assert (tmp_path / "other.txt").read_text() == "keep going!\n"  # other files are unaffected


def test_after_a_denial_autonomous_mode_holds_instead_of_approving(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "approval_mode", lambda: "autonomous")
    (tmp_path / "old_report.txt").write_text("Q3\n")
    box, ran = _project(tmp_path)
    client = Client([
        ModelTurn(tool_calls=[ToolCall("t", "terminal", '{"command": "rm -f old_report.txt"}')]),
        ModelTurn(tool_calls=[ToolCall("w", "file_write", '{"path": "old_report.txt", "content": "gone\\n"}')]),
        ModelTurn(content="Waiting for you."),
    ])
    events: list[dict[str, Any]] = []
    turn = _turn(box, client, [], events)
    turn.run("Delete old_report.txt.")
    assert [h["tool"] for h in turn.held] == ["terminal", "file_write"] and ran == []
    assert (tmp_path / "old_report.txt").read_text() == "Q3\n"


# ── 3. a missing test runner is the environment ─────────────────────────
def test_a_missing_test_runner_is_an_environment_blocker():
    ledger = VerificationLedger()
    ledger.observe("file_edit", {"path": "calc.py"}, True, "Edited calc.py", 1, 5, {}, call_id="e")
    ledger.observe("terminal", {"command": "python -m pytest test_calc.py -v"}, False,
                   "[exit code 1 · 0.0s · this PC · /p]\n[stderr]\n/usr/bin/python: No module named pytest", 2, 5, {}, call_id="t")
    done = ledger.completion()
    assert done["status"] == "blocked"
    assert any(b["class"] == "environment" for b in ledger.blockers)


# ── 3b. the live stream ended before the run did ─────────────────────────
def test_a_run_that_finishes_between_read_and_status_check_is_streamed_to_the_end(monkeypatch, tmp_path):
    """Found live in 4 of 84 turns: journal_done arrived mid-answer; final, agent_done and the reply's end were
    stored in the journal but never sent to the client that was following the run."""
    import asyncio

    import agent.query_journal as journal_module
    from agent.query_journal import QueryJournal
    from api.routes import chat

    class Racing(QueryJournal):
        reads = 0
        raced = False

        def read(self, run, after):
            self.reads += 1
            return super().read(run, after)

        def get(self, run, session):
            if self.reads >= 2 and not self.raced:  # the reader just found nothing new ...
                self.raced = True  # ... and the collector writes the end of the run and finishes it
                self.append(run, {"type": "agent_token", "data": " the end"})
                self.append(run, {"type": "final", "response": "the whole answer", "success": True})
                self.finish(run)
            return super().get(run, session)

    journal = Racing(tmp_path / "journal.db")
    assert journal.claim("run-1", "chat", "hash")
    journal.append("run-1", {"type": "agent_token", "data": "The start"})
    monkeypatch.setattr(journal_module, "get_query_journal", lambda: journal)

    async def follow() -> list[dict[str, Any]]:
        response = chat._journal_response("run-1", "chat", 0)
        import json
        return [json.loads(chunk) async for chunk in response.body_iterator]

    events = asyncio.run(follow())
    kinds = [e["type"] for e in events]
    assert journal.raced and kinds == ["agent_token", "agent_token", "final", "journal_done"]
    assert events[-1]["status"] == "completed"


# ── 4. compaction that never worked, and was never charged ──────────────
def _long_turn(n: int = 6) -> list[dict[str, Any]]:
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "fix it"}]
    for i in range(n):
        messages += [{"role": "assistant", "content": "", "tool_calls": [{"id": f"c{i}", "type": "function",
                      "function": {"name": "file_read", "arguments": "{}"}}]},
                     {"role": "tool", "tool_call_id": f"c{i}", "name": "file_read", "content": "x" * 2000}]
    return messages


def test_compaction_summarises_with_thinking_off_and_is_charged(tmp_path):
    box, _ran = _project(tmp_path)
    client = Client([ModelTurn(content="Goal: fix slug. Tried pytest; missing.", finish_reason="stop",
                               usage={"prompt": 900, "completion": 40, "total": 940})])
    events: list[dict[str, Any]] = []
    turn = _turn(box, client, [], events)
    turn._history_in_messages = 0
    assert turn._compact_turn(_long_turn(), keep_steps=2)
    assert client.efforts == ["none"] and client.reasoning_effort == "medium"
    assert turn.usage["prompt"] == 900 and turn.usage["fresh"] == 940
    assert any(e["type"] == "side_usage" and e["kind"] == "compaction" for e in events)


def test_compaction_that_keeps_failing_stops_being_tried(tmp_path):
    box, _ran = _project(tmp_path)
    client = Client([ModelTurn(content="", reasoning="Thinking Process: 1. Analyze", finish_reason="length",
                               usage={"prompt": 900, "completion": 700, "total": 1600}) for _ in range(2)])
    turn = _turn(box, client, [], [])
    turn._history_in_messages = 0
    assert not turn._compact_turn(_long_turn(), keep_steps=2) and not turn._compact_turn(_long_turn(), keep_steps=2)
    assert not turn._compact_turn(_long_turn(), keep_steps=2)  # third time: not asked at all
    assert len(client.calls) == 2 and turn.usage["fresh"] == 3200  # the failed ones still cost


# ── 5. fresh tokens when the prompt was trimmed (audit finding F09) ─────
def test_a_trimmed_prompt_still_charges_what_was_added():
    from agent.lean.loop import _estimate_tokens

    turn = LeanTurn.__new__(LeanTurn)
    turn.usage, turn._last_prompt = {"prompt": 0, "completion": 0, "total": 0, "fresh": 0}, 0
    old = {"role": "tool", "content": "y" * 20000}
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "go"}, old]
    LeanTurn._account_usage(turn, {"prompt": 6000, "completion": 10}, messages, [])
    old["content"] = old["content"][:500]  # trimmed to fit ...
    new = {"role": "tool", "content": "z" * 3400}  # ... while a new result arrives
    messages.append(new)
    sent = _estimate_tokens(messages, [])
    LeanTurn._account_usage(turn, {"prompt": 1300, "completion": 10}, messages, [])
    added = int(_estimate_tokens([new], []) * 1300 / sent)
    assert added > 900 and turn.usage["fresh"] == 6010 + added + 10  # before: 6010 + 0 + 10


# ── 6. checks that check nothing ────────────────────────────────────────
def test_a_masked_exit_code_is_not_a_passing_check():
    ledger = VerificationLedger()
    ledger.observe("file_edit", {"path": "slug.py"}, True, "Edited slug.py", 1, 5, {}, call_id="e")
    ledger.observe("terminal", {"command": "python -m pytest tests -v 2>&1 || true"}, True,
                   "[ok · 0.0s · this PC · /p]\n/usr/bin/python: No module named pytest", 2, 5, {}, call_id="t")
    assert ledger.completion()["status"] != "checks_pass" and not ledger.checks
    assert "hides the check's own exit code" in ledger.feedback("t")
    # With the runner's own summary, the summary decides.
    ledger.observe("terminal", {"command": "pytest -q 2>&1 | tail -3"}, True,
                   "[ok · 1.0s · this PC · /p]\n1 failed, 3 passed in 0.1s", 3, 5, {}, call_id="u")
    assert ledger.completion()["status"] == "failing"


def test_installing_or_finding_a_runner_is_not_a_check():
    from agent.lean.verification import check_kind
    from agent.learning.episodes import command_kind

    for command in ("pip install pytest -q", "pip list | grep -i pytest", "which pip && python -m pip install pytest",
                    "apt-get install -y python3-pytest"):
        assert check_kind(command) == "" and command_kind(command) != "test", command
    for command in ("pytest -q", "python3 -m pytest tests", "cd p && npm test", "cargo build && cargo test"):
        assert check_kind(command) == "test" and command_kind(command) == "test", command


def test_claiming_a_pass_when_the_check_could_not_run_is_sent_back():
    ledger = VerificationLedger()
    ledger.observe("file_edit", {"path": "clock.py"}, True, "Edited clock.py", 1, 5, {}, call_id="e")
    ledger.observe("terminal", {"command": "python -m pytest test_clock.py -v"}, False,
                   "[exit code 1 · 0.0s · this PC · /p]\n/usr/bin/python: No module named pytest", 2, 5, {}, call_id="t")
    assert ledger.completion()["status"] == "blocked"
    note = ledger.unfinished("The test passes. I modified clock.py to return the expected value.")
    assert "nothing has verified it" in note and "No module named pytest" in note
    assert ledger.unfinished("I changed clock.py, but pytest isn't installed, so I couldn't run the test.") == ""


def test_tests_pass_with_no_test_run_is_sent_back_but_other_done_claims_are_not():
    ledger = VerificationLedger()
    ledger.observe("file_edit", {"path": "clock.py"}, True, "Edited clock.py", 1, 5, {}, call_id="e")
    ledger.observe("terminal", {"command": "python test_clock.py"}, True, "[ok · 0.0s · this PC · /p]\n(no output)", 2, 5, {}, call_id="r")
    assert not ledger.checks  # running the test file directly checked nothing
    assert "no test runner has run since your change" in ledger.unfinished("The test passes.")
    assert ledger.unfinished("I've updated notes.md, it's done.") == ""  # not about tests: the check step covers code


def test_after_repeated_setup_failures_the_note_says_to_stop_and_report():
    ledger = VerificationLedger()
    out = "[exit code 1 · 0.0s · this PC · /p]\n/usr/bin/python: No module named pytest"
    notes = []
    for i in range(3):
        ledger.observe("terminal", {"command": "python -m pytest -q"}, False, out, i + 1, 5, {}, call_id=f"t{i}")
        notes.append(ledger.feedback(f"t{i}"))
    assert "This is the environment" in notes[0]
    assert "Stop working on the environment" in notes[2] and "don't search outside the project" in notes[2]


# ── 6. Why a reply came back empty (two-model campaign, 16 Nov 2026) ───────────────────────────
# Seen live with deepseek-v4-qwen3.5-heretic:9b on Ollama 0.40.16:
# - Ollama's qwen3.5 parser rejected a malformed tool call ("XML syntax error … <function> closed by
#   </parameter>") and still answered HTTP 200 with no tool call, no finish_reason and no usage. The harness
#   took it for an answer written as reasoning; with thinking off the model then said the edit was done.
# - A "length" stop at prompt 8,525 + reply 7,859 = 16,384: the served window filled, not max_tokens (8,192).
from agent.lean.loop import NUDGE_INCOMPLETE, NUDGE_WINDOW_SPENT, empty_cause  # noqa: E402


def test_empty_reply_causes_come_from_the_servers_evidence():
    assert empty_cause(ModelTurn(reasoning="I'll use file_edit", incomplete=True), 16384) == "incomplete_stream"
    assert empty_cause(ModelTurn(stream_error="parse failed", finish_reason="stop"), 16384) == "incomplete_stream"
    window = ModelTurn(reasoning="…", finish_reason="length", usage={"prompt": 8525, "completion": 7859, "total": 16384})
    assert empty_cause(window, 16384) == "context_window"
    limit = ModelTurn(reasoning="…", finish_reason="length", usage={"prompt": 900, "completion": 8192, "total": 9092})
    assert empty_cause(limit, 16384) == "output_limit"
    assert empty_cause(ModelTurn(reasoning="the answer is 4", finish_reason="stop"), 16384) == "reasoning_only"
    assert empty_cause(ModelTurn(finish_reason="stop"), 16384) == "empty"
    # Adapters that report no finish at all aren't called broken: only the stream that tracks it says so.
    assert empty_cause(ModelTurn(reasoning="hmm"), 16384) == "reasoning_only"


def test_an_unreadable_tool_call_is_asked_for_again_not_taken_for_an_answer(tmp_path):
    box, _ran = _project(tmp_path)
    client = Client([
        ModelTurn(reasoning="I'll append beta with file_write, then show the file.", incomplete=True),
        ModelTurn(tool_calls=[ToolCall("w", "file_write", '{"path": "notes.md", "content": "alpha\\nbeta\\n"}')],
                  finish_reason="tool_calls"),
        ModelTurn(content="notes.md now reads alpha, then beta.", finish_reason="stop"),
    ])
    events: list[dict[str, Any]] = []
    result = _turn(box, client, ["approve"], events).run("Add a second line, beta, to notes.md")
    note = client.calls[1][-1]["content"]
    assert NUDGE_INCOMPLETE in note and NUDGE_THOUGHT_ONLY not in note
    assert client.efforts[:2] == ["medium", "medium"]  # asked again as it was: thinking wasn't the problem
    cause = next(e for e in events if e["type"] == "empty_reply")
    assert cause["cause"] == "incomplete_stream" and cause["retry"] == "as_is"
    assert (tmp_path / "notes.md").read_text() == "alpha\nbeta\n" and "beta" in result.text


def test_a_filled_context_window_is_named_as_such(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "context_tokens", lambda endpoint=None: 16384)  # the served window in that run
    box, _ran = _project(tmp_path)
    client = Client([
        ModelTurn(reasoning="Let me think about this differently…", finish_reason="length",
                  usage={"prompt": 8525, "completion": 7859, "total": 16384}),
        ModelTurn(content="The test can't pass: it expects a frozen clock.", finish_reason="stop"),
    ])
    events: list[dict[str, Any]] = []
    _turn(box, client, [], events).run("Make the clock test pass")
    assert NUDGE_WINDOW_SPENT in client.calls[1][-1]["content"]
    assert client.efforts == ["medium", "none"]
    assert next(e for e in events if e["type"] == "empty_reply")["cause"] == "context_window"


def test_no_answer_after_the_bounded_retries_is_an_explicit_outcome(tmp_path):
    box, _ran = _project(tmp_path)
    broken = lambda: ModelTurn(reasoning="I'll call file_read", incomplete=True)  # noqa: E731
    client = Client([broken(), broken(), broken()])
    events: list[dict[str, Any]] = []
    result = _turn(box, client, [], events).run("Read notes.md")
    done = next(e for e in events if e["type"] == "agent_done")
    assert done["stop_reason"] == "no_answer" and done["empty_cause"] == "incomplete_stream"
    assert result.stop_reason == "no_answer" and "incomplete" in result.text
    assert len(client.calls) == 3  # bounded: the first try and two retries, no more


def test_an_error_inside_the_stream_is_kept(monkeypatch):
    """Ollama can end an HTTP 200 stream with an error object instead of a finish; it used to be skipped."""
    import http.server
    import threading as _threading
    from agent.lean.provider import ChatClient, Endpoint

    lines = [
        'data: {"choices":[{"delta":{"reasoning":"I will call file_read"}}]}',
        'data: {"error":{"message":"qwen3.5 tool call parsing failed"}}',
        "data: [DONE]",
    ]

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(("\n\n".join(lines) + "\n\n").encode())

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    _threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        from agent import privacy
        monkeypatch.setattr(privacy, "require", lambda *a, **k: None)
        client = ChatClient(Endpoint(f"http://127.0.0.1:{server.server_port}/v1", "x", "m", "ollama", True))
        turn = client.stream_turn([{"role": "user", "content": "hi"}])
        assert turn.incomplete and "parsing failed" in turn.stream_error and not turn.tool_calls
    finally:
        server.shutdown()


def test_a_fix_claimed_without_any_write_is_caught():
    """gemma4-12b-it (D1, current harness): it ran `python3 test_calc.py` (exit 0, nothing ran) and wrote these,
    having changed nothing. The judge's own test run failed: a false success the file-claim check missed."""
    from agent.lean.job import unbacked_file_claim

    said = ("The tests were passing when I first ran them. I have corrected the `average` function in `calc.py` "
            "and verified that the tests pass.")
    assert "corrected" in unbacked_file_claim(said, set())
    assert unbacked_file_claim("- Modified `calc.py` to fix the denominator in the `average` function.", set())
    # Backed by a real write: no note.
    assert unbacked_file_claim(said, {"calc.py"}) == ""
    # Talking about an earlier turn, or about what to do next, isn't a claim.
    assert unbacked_file_claim("Earlier I fixed `calc.py`.", set()) == ""
    assert unbacked_file_claim("Next, fix the denominator in `calc.py`.", set()) == ""


# ── 4. a location the user named and the folder boundary refused (W1) ───
def _bounded_write(project: Path):
    """The real file_write's boundary refusal (agent/tools.py) for anything outside `project`."""
    def write(args: dict[str, Any]) -> str:
        path = Path(args["path"]) if Path(args["path"]).is_absolute() else project / args["path"]
        if not path.resolve().is_relative_to(project.resolve()):
            return f"Path not allowed. Allowed roots: {project}. Prefer Desktop/<project>/filename for user projects."
        path.write_text(args.get("content") or "")
        return f"Wrote {len(args.get('content') or '')} chars to {path}"
    return NativeTool("file_write", "write", {"type": "object", "properties": {}}, write)


def test_a_refused_requested_location_is_not_silently_replaced_by_another(tmp_path):
    """W1 (atf cycle 1): "write a summary into <outside>/summary.txt". The folder boundary refused the write,
    the model saved the summary inside the project instead and replied "Done!" (2 of 2 runs). The refusal
    says this was the user's location, and a reply that hides the refusal goes back once."""
    project = tmp_path / "proj"
    project.mkdir()
    outside = tmp_path / "proj-shared" / "summary.txt"
    box, _ran = _project(project)
    box.native = {**box.native, "file_write": _bounded_write(project)}
    client = Client([
        ModelTurn(tool_calls=[ToolCall("w1", "file_write", '{"path": "%s", "content": "Two lines."}' % outside)]),
        ModelTurn(tool_calls=[ToolCall("w2", "file_write", '{"path": "summary.txt", "content": "Two lines."}')]),
        ModelTurn(content="Done! The summary has been written to summary.txt in your project folder."),
        ModelTurn(content=f"I couldn't write to {outside}: it's outside the folders I'm allowed to change. "
                          "I saved the summary as summary.txt in the project instead."),
    ])
    events: list[dict[str, Any]] = []
    result = _turn(box, client, [], events).run(f"Write a two-line summary of README.md into {outside}.")

    assert not outside.exists()
    refusal = client.calls[1][-1]["content"]  # what the model saw right after the refused write
    assert "the user asked for" in refusal and "somewhere else" in refusal
    assert "couldn't write" in result.text and "Done!" not in result.text
    assert sum(e["type"] == "requested_location_nudge" for e in events) == 1


def test_a_refused_path_the_model_made_up_gets_no_extra_note(tmp_path):
    """A boundary refusal for a location the user never named stays an ordinary, recoverable error."""
    project = tmp_path / "proj"
    project.mkdir()
    box, _ran = _project(project)
    box.native = {**box.native, "file_write": _bounded_write(project)}
    client = Client([
        ModelTurn(tool_calls=[ToolCall("w1", "file_write", '{"path": "/elsewhere/notes.txt", "content": "x"}')]),
        ModelTurn(tool_calls=[ToolCall("w2", "file_write", '{"path": "notes.txt", "content": "x"}')]),
        ModelTurn(content="Saved notes.txt."),
    ])
    events: list[dict[str, Any]] = []
    result = _turn(box, client, [], events).run("Write x into notes.txt.")
    assert "the user asked for" not in client.calls[1][-1]["content"]
    assert result.text == "Saved notes.txt."
    assert not any(e["type"] == "requested_location_nudge" for e in events)


def test_custom_runner_verification_requires_an_executed_test_after_the_edit():
    ledger = VerificationLedger()
    command = {"command": "python3 run_tests.py"}
    ledger.observe("file_edit", {"path": "textkit/trim.py"}, True, "Edited", 1)
    ledger.observe("terminal", {"command": "echo 'python3 run_tests.py'"},
                   True, "[ok · sandbox]\n10 ran, 0 failed", 2)
    ledger.observe("terminal", command, False, "Not run: Work budget exhausted", 3)
    assert not ledger.checks
    assert ledger.completion()["status"] == "blocked"
    assert ledger.completion()["bottleneck"] == "budget"
    ledger.observe("terminal", command, True, "[ok · sandbox]\n0 ran, 0 failed", 4)
    assert ledger.completion()["status"] == "blocked"
    ledger.observe("terminal", command, True, "[ok · sandbox]\n10 ran, 0 failed", 5)
    assert ledger.completion()["status"] == "checks_pass"
    assert ledger.checks[-1].passed == 10
    ledger.observe("file_edit", {"path": "textkit/trim.py"}, True, "Edited again", 6)
    assert ledger.completion()["status"] == "unverified"
    ledger.observe("terminal", command, False, "[exit code 1 · sandbox]\n10 ran, 1 failed", 7)
    assert ledger.completion()["status"] == "failing"


@pytest.mark.parametrize("decision", ["allow", "deny"])
def test_identical_test_can_run_after_an_edit_but_denial_still_holds(tmp_path, monkeypatch, decision):
    from agent.lean import loop

    needs_approval = loop.tool_needs_approval
    monkeypatch.setattr(loop, "tool_needs_approval", lambda entry, name, args:
                        (True, "fixture requires approval") if name == "terminal" else needs_approval(entry, name, args))
    box, ran = _project(tmp_path)
    args = '{"command": "python -m pytest -q"}'
    events = []
    client = Client([
        ModelTurn(tool_calls=[ToolCall(f"test{i}", "terminal", args)]) for i in range(3)
    ] + [
        ModelTurn(tool_calls=[ToolCall("write", "file_write", '{"path":"solution.py","content":"answer = 1\\n"}')]),
        ModelTurn(tool_calls=[ToolCall("after", "terminal", args)]),
        ModelTurn(content="The changes are available for review.", finish_reason="stop"),
    ])
    _turn(box, client, [decision] * 3, events).run("Check the tests, update solution.py, then check the tests.")
    ends = {e["id"]: e for e in events if e["type"] == "tool_end"}
    if decision == "allow":
        assert ends["test2"]["outcome"] == "skipped"
        assert ends["after"]["outcome"] == "ok"
        assert len(ran) == 3
    else:
        assert ends["after"]["outcome"] == "refused"
        assert not ran


def test_a_refused_project_is_not_rebuilt_outside_any_project(tmp_path, monkeypatch):
    """GTA_CLONE evaluation (26 November 2026, no project attached, approvals off): ~/Work/GTA_CLONE was refused and
    qwen3.5:9b rebuilt the game in ~/Documents/GTA_CLONE. With no project, a copy in another file root is refused."""
    documents = tmp_path / "Documents"
    documents.mkdir()
    users = tmp_path / "Work" / "GTA_CLONE"
    users.mkdir(parents=True)
    box, _ran = _project(documents)
    box.project_root = ""          # no project attached: the configured roots apply
    box.native = {**box.native, "file_write": _bounded_write(documents)}
    copy = documents / "GTA_CLONE" / "game.js"
    client = Client([
        ModelTurn(tool_calls=[ToolCall("w1", "file_write", '{"path": "%s", "content": "fixed"}' % (users / "game.js"))]),
        ModelTurn(tool_calls=[ToolCall("w2", "file_write", '{"path": "%s", "content": "fixed"}' % copy)]),
        ModelTurn(content=f"I couldn't change {users}: it's outside the folders I may change. Attach it as a project."),
    ])
    events: list[dict[str, Any]] = []
    result = _turn(box, client, [], events).run(f"The game in {users} is broken, fix it.")
    assert not copy.exists() and not (users / "game.js").exists()
    assert "didn't ask for" in client.calls[2][-1]["content"]
    assert "couldn't change" in result.text


def test_ready_to_play_after_only_a_launch_and_a_screenshot_goes_back_once(tmp_path, monkeypatch):
    """Live run 4 (#235): the game opened, the screenshot showed an empty canvas, and the reply said "ready to play".
    Launching, a window, a clean page load and a screenshot are each weaker than "it works"."""
    from agent.lean import settings as lean_settings

    monkeypatch.setattr(lean_settings, "approval_mode", lambda: "never")
    box, _ran = _project(tmp_path)
    obj = {"type": "object", "properties": {}}
    opened = ('Opened /p/index.html in Chromium (requested). Window "GTA Clone - Chromium" is open on monitor DP-1.\n'
              "Evidence so far:\n1. Launch requested: yes.\n2. Window open: verified in the window list.\n"
              "3. Page loaded: yes, with no JavaScript errors or failed loads in a separate headless browser.")
    box.native = {**box.native,
                  "open_application": NativeTool("open_application", "open", obj, lambda a: opened),
                  "take_screenshot": NativeTool("take_screenshot", "look", obj, lambda a: "Screenshot obs-1: the game")}
    client = Client([
        ModelTurn(tool_calls=[ToolCall("o1", "open_application", '{"target": "/p/index.html"}')]),
        ModelTurn(tool_calls=[ToolCall("s1", "take_screenshot", '{"window_title": "GTA Clone - Chromium"}')]),
        ModelTurn(content="Perfect! The game is running and ready for you to play."),
        ModelTurn(content="It's open in Chromium and the page loaded without errors; the screenshot shows the map. "
                          "I haven't played it, so give the controls a try and tell me how it goes."),
    ])
    events: list[dict[str, Any]] = []
    result = _turn(box, client, [], events).run("Open my game and make sure it works.")
    assert sum(e["type"] == "presentation_nudge" for e in events) == 1
    assert "ready for you to play" not in result.text and "haven't played it" in result.text
    assert "used (input that changed the screen): no" in client.calls[3][-1]["content"]
