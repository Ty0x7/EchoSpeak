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
    assert len(asks) == 2
    assert "refused an action on old_report.txt" in asks[1]["reason"] and asks[1]["grantable"] is False
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
    """Found live in 4 of 78 turns: journal_done arrived mid-answer; final, agent_done and the reply's end were
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


def test_after_repeated_setup_failures_the_note_says_to_stop_and_report():
    ledger = VerificationLedger()
    out = "[exit code 1 · 0.0s · this PC · /p]\n/usr/bin/python: No module named pytest"
    notes = []
    for i in range(3):
        ledger.observe("terminal", {"command": "python -m pytest -q"}, False, out, i + 1, 5, {}, call_id=f"t{i}")
        notes.append(ledger.feedback(f"t{i}"))
    assert "This is the environment" in notes[0]
    assert "Stop working on the environment" in notes[2] and "don't search outside the project" in notes[2]
