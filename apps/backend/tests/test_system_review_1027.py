"""Findings from the 2026-10-27 system review (docs/research/system-review-2026-10-27.md).

Each test states the *intended* behavior and is marked xfail(strict=True) while the finding
is open: the suite stays green today, and a fix turns the test into an unexpected pass that
fails the run until its marker is removed. Nothing here calls a model or a network.
"""
from __future__ import annotations

import re
import threading
from pathlib import Path

import pytest

from agent.lean.job import needs_action
from agent.lean.loop import LeanTurn
from agent.lean.personas import AgentPersona
from agent.lean.provider import ModelTurn, ToolCall
from agent.lean.toolbox import NativeTool, Toolbox, _FAILURE_PREFIXES
from agent.state import StateStore
from tests.test_capabilities import _Scripted

BACKEND = Path(__file__).resolve().parents[1]
open_finding = lambda finding: pytest.mark.xfail(strict=True, reason=f"open review finding {finding}")


def _native(name, func):
    return NativeTool(name, name, {"type": "object", "properties": {}}, func)


# ── R1: a rejected argument is recorded as an uncertain effect ──────────
# Fixed in 11.10.8 (was open review finding R1).
def test_an_argument_error_is_not_an_uncertain_effect():
    box = Toolbox(toolsets=["none"], session_id="r1")

    def rejects(args):
        raise ValueError("Coordinate outside screenshot; capture again instead of guessing")

    box.native = {"computer_action": _native("computer_action", rejects)}
    result = box.run("computer_action", {})
    assert not result.ok and not result.uncertain  # nothing ran, so nothing is uncertain


# ── R2: an uncertain action in one chat crashes every later turn that needs the resource ──
# Fixed in 11.10.8 (was open review finding R2).
def test_a_held_resource_is_a_tool_result_not_a_crashed_turn(tmp_path, monkeypatch):
    from agent.coordination import work_scope
    from agent.lean import policy

    ledger = StateStore(tmp_path).coordination
    held = ledger.claim(ledger.create(objective="old chat", agent_id="echo", tools=["computer_action"])["id"])
    ledger.action_result(ledger.action_intent(held, "x", "computer_action", {}, read_only=False),
                         ok=False, outcome="failed", uncertain=True)
    ctx = ledger.claim(ledger.create(objective="new chat", agent_id="echo", tools=["computer_action"])["id"])
    monkeypatch.setattr("agent.lean.loop.tool_needs_approval", lambda entry, name, args: (False, ""))
    monkeypatch.setattr(policy, "evaluate", lambda *a, **k: policy.Decision("allow", "", ""))
    box = Toolbox(toolsets=["none"], session_id="r2")
    box.native = {"computer_action": _native("computer_action", lambda a: "Executed 1 actions.")}
    client = _Scripted([ModelTurn(tool_calls=[ToolCall("c1", "computer_action", "{}")]),
                        ModelTurn(content="The screen is busy with an unresolved action.")])
    with work_scope(ctx):
        turn = LeanTurn(client=client, persona=AgentPersona(id="echo", name="Echo"), system_prompt="s", history=[],
                        toolbox=box, session_id="r2", request_id="r", execution_id="e", emit=lambda e: None,
                        cancel=threading.Event(), persist_tool_runs=False)
        result = turn.run("click play")  # today: PermissionError escapes and fails the whole turn
    rows = [item for item in turn.timeline if item.get("kind") == "tool"]
    assert result.text and rows and rows[0]["status"] == "failed" and "held" in rows[0]["output"].lower()


# ── R3: work blocks its own next write while its dev server runs ────────
# Fixed in 11.10.8 (was open review finding R3).
def test_a_running_background_process_does_not_block_its_own_work(tmp_path):
    ledger = StateStore(tmp_path).coordination
    ctx = ledger.claim(ledger.create(objective="start the dev server, then edit App.tsx", agent_id="echo",
                                     project_id="proj", tools=["terminal", "file_edit"])["id"])
    started = ledger.action_intent(ctx, "t1", "terminal", {"command": "npm run dev"}, read_only=False)
    ledger.action_result(started, ok=True, outcome="ok", effects=[{"kind": "process", "running": "p1"}], uncertain=False)
    ledger.action_intent(ctx, "t2", "file_edit", {"path": "App.tsx"}, read_only=False)


# ── R4: ordinary questions become durable work records ──────────────────
@open_finding("R4")
@pytest.mark.parametrize("question", ["What should I make for dinner?", "How do I update my resume?",
                                      "Can you add up 12 and 30?"])
def test_ordinary_questions_do_not_count_as_action_requests(question):
    assert not needs_action(question)


# ── R5: legacy tool failures recorded as successes ──────────────────────
# Fixed in 11.10.8 (was open review finding R5).
@pytest.mark.parametrize("text", [
    "Email send failed: SMTP authentication error",
    "Discord bot client not available.",
    "Path not found.",
    "Playwright is not available. Please install playwright and run 'playwright install'.",
    "[WEATHER_LIVE] ok=false error=missing_location",
    "❌ Failed to create issue: 401 Bad credentials",  # every skill tool's failure marker
])
def test_a_tool_that_reports_failure_is_recorded_as_failed(text):
    box = Toolbox(toolsets=["none"], session_id="r5")
    box.native = {"probe": _native("probe", lambda a, t=text: t)}
    assert not box.run("probe", {}).ok


# ── R6: GitHub, Calendar and Notion skills can't import in the installed app ──
@open_finding("R6")
def test_skill_libraries_are_bundled_into_the_installer():
    spec = (BACKEND.parent / "desktop" / "backend" / "echospeak_backend.spec").read_text(encoding="utf-8")
    for module in ("googleapiclient", "google_auth_oauthlib", "github", "notion_client"):
        assert re.search(rf"['\"]{module}['\"]", spec), module


# ── R7: settings that save but change nothing ──────────────────────────
@open_finding("R7")
def test_your_name_and_remember_conversations_are_read_somewhere():
    sources = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in (BACKEND / "agent").rglob("*.py"))
    assert "user_display_name" in sources, "Settings › General › Your name is saved but no agent reads it"
    assert "session_memory_enabled" in sources, "Settings › Memory › Remember conversations has no effect"


# ── R8: a plain group question costs an extra completion-judge model call ──
@open_finding("R8")
def test_a_group_question_without_tools_or_tasks_skips_the_model_judge(monkeypatch):
    from types import SimpleNamespace

    from agent.lean import runtime
    from agent.lean.job import Job

    session = runtime.LeanSession.__new__(runtime.LeanSession)
    session.job = Job(goal="What's everyone's favourite movie?", max_rounds=3, token_budget=0)
    session.job.record_text("Jarvis", "Mine is Arrival.", 10)
    session.cancel = threading.Event()
    session.room = SimpleNamespace(kind="group", agent_ids=["scout", "forge"], mode="reply")
    monkeypatch.setattr(runtime.LeanSession, "_is_group", lambda self: True)
    monkeypatch.setattr(runtime.LeanSession, "_members", lambda self: [AgentPersona(id="scout", name="Jarvis"),
                                                                        AgentPersona(id="forge", name="Glados")])

    calls = []

    class Judge:
        def stream_turn(self, *a, **k):
            calls.append(1)
            return ModelTurn(content='{"done": true, "summary": "answered"}')

    monkeypatch.setattr(runtime.LeanSession, "_review_client", lambda self, persona: Judge())
    assert runtime.LeanSession._judge(session, AgentPersona(id="echo", name="Echo"))["done"] is True
    assert calls == [], "the completion judge called a model for a plain question with no tools or tasks"


# ── guards that hold today (regressions, not open findings) ─────────────
def test_the_failure_prefix_list_still_covers_the_desktop_fixes():
    for text in ("control not found", "click failed", "window not found"):
        assert text.startswith(_FAILURE_PREFIXES)


def test_an_unknown_effect_still_blocks_a_blind_retry_of_the_same_resource(tmp_path):
    """The fix for R1-R3 must keep this: a write whose outcome is truly unknown is not repeated blindly."""
    ledger = StateStore(tmp_path).coordination
    ctx = ledger.claim(ledger.create(objective="write notes", agent_id="echo", project_id="p", tools=["file_write"])["id"])
    ledger.action_intent(ctx, "w1", "file_write", {"path": "notes.md"}, read_only=False)
    restarted = StateStore(tmp_path).coordination  # crash after intent: reconcile marks it unknown
    other = restarted.claim(restarted.create(objective="other", agent_id="echo", project_id="p", tools=["file_write"])["id"])
    with pytest.raises(PermissionError):
        restarted.action_intent(other, "w2", "file_write", {"path": "notes.md"}, read_only=False)
