"""Verification-driven engineering (agent/lean/verification.py) and evidence-based learning attribution.

Scenarios from the brief (Part E), each with a deterministic check:
 1. a simple task finishes without extra iteration or model calls
 2. a failed test gives the agent structured feedback (expected vs actual, failing tests, next action)
 3. a measured optimisation is held to its target and to correctness
 4. several failed approaches, then a verified solution
 5. an environment blocker is recognised as the setup's fault, not the code's
 6. a strategy that stopped improving is called out, and a regression points back to the best result
 7. an apparent success that independent verification contradicts
 8. interrupted work resumes from the saved objective, baseline and best result
 9. a lesson that helps is measured against its holdout
10. irrelevant or harmful lessons: blockers never count against them, negative transfer is detected
Plus reward hacking (tests edited after they failed), owner feedback that reclassifies, Chat staying light,
and parsing real runner output. Scripted models only: the loop and runtime are real.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from agent import learning
from agent.lean import runtime as lean_runtime
from agent.lean import verification
from agent.lean.provider import ModelTurn, ToolCall
from agent.lean.toolbox import NativeTool
from agent.lean.verification import VerificationLedger, counts, exit_code, failing_names, measure
from agent.learning import evaluation, playbook, profiles
from agent.learning.episodes import classify_cause, feedback_cause, grade
from agent.learning.store import Episode, Lesson
from tests.test_lean_group import _session
from tests.test_lean_runtime import ScriptedClient
from tests.test_learning import store  # noqa: F401  (fixture)

OK = "[ok · 0.4s · sandbox · /w]\n"
FAIL = "[exit code 1 · 0.4s · sandbox · /w]\n"
PYTEST_FAIL = (FAIL + "FAILED tests/test_dates.py::test_leap_year - AssertionError: assert 28 == 29\n"
               "E       assert 28 == 29\n1 failed, 4 passed in 0.21s")
PYTEST_PASS = OK + "5 passed in 0.18s"
_PARAMS = {"type": "object", "properties": {k: {"type": "string"} for k in ("path", "content", "command", "old_text", "new_text")}}


def _tool(name: str, func) -> NativeTool:
    return NativeTool(name=name, description=name, parameters=_PARAMS, func=func, always=True)


@pytest.fixture()
def bench(monkeypatch, tmp_path):
    """Echo-native tools whose terminal plays back scripted command output, in order."""
    from agent.lean import evidence
    from agent.lean import terminal as terminal_module
    from config import config

    monkeypatch.setattr(terminal_module, "resolved_mode", lambda: "host")
    monkeypatch.setattr(terminal_module, "execution_mode", lambda: "host")
    monkeypatch.setattr(evidence, "_resolve", lambda raw: (tmp_path / str(raw)) if str(raw or "").strip() else None)
    outputs: list[str] = []

    def write(args: dict[str, Any]) -> str:
        target = tmp_path / str(args.get("path"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(args.get("content") or "x"), encoding="utf-8")
        return f"Wrote {args.get('path')}"

    class FakeTerminal:
        def __init__(self, root: str = "") -> None:
            pass

        def describe(self) -> str:
            return "terminal is a test double."

        def tools(self) -> list[NativeTool]:
            return [_tool("terminal", lambda args: outputs.pop(0))]

    monkeypatch.setattr(lean_runtime, "coding_tools", lambda: [_tool("file_write", write), _tool("file_edit", write)])
    monkeypatch.setattr(lean_runtime, "Terminal", FakeTerminal)
    monkeypatch.setattr(config, "enable_system_actions", True)
    monkeypatch.setattr(config, "allow_terminal_commands", True)
    return outputs


def _call(name: str, **args: Any) -> ToolCall:
    return ToolCall(f"c{uuid.uuid4().hex[:6]}", name, json.dumps(args))


def _run(monkeypatch, turns: list[ModelTurn], message: str):
    client = ScriptedClient(turns)
    session, events = _session(monkeypatch, {"echo": client})
    out = session.run(message, persona_id="echo")
    return session, client, events, out


def _last_tool_result(client: ScriptedClient, call: int) -> str:
    return [m.get("content", "") for m in client.calls[call] if m.get("role") == "tool"][-1]


def _notes(events: list[dict[str, Any]]) -> list[str]:
    return [e["note"] for e in events if e.get("type") == "answer_note"]


# ── scenario 1: simple work stays simple ────────────────────────────────

def test_a_simple_task_finishes_without_extra_iteration(monkeypatch, store, bench):
    bench.append(PYTEST_PASS)
    session, client, events, out = _run(monkeypatch, [
        ModelTurn(tool_calls=[_call("file_write", path="dates.py", content="def leap(y): ...")]),
        ModelTurn(tool_calls=[_call("terminal", command="pytest -q")]),
        ModelTurn(content="Added leap() in dates.py; pytest passes (5 passed)."),
    ], "add a leap-year helper to dates.py")

    assert not client.turns and len(client.calls) == 3  # no extra model call anywhere
    assert not [e for e in events if e.get("type") == "verification_nudge"]
    assert not [e for e in events if e.get("type") == "text_replace" and not e.get("text")]  # nothing erased
    assert "This check passes" in _last_tool_result(client, 2)
    assert _notes(events) == ["Checks pass — test `pytest -q`: 0 failed, 5 passed, exit 0 · 1 attempt."]
    assert "set_objective" in [t["function"]["name"] for t in client.tools[0]]
    [episode] = store.episodes()
    assert episode.verification["status"] == "checks_pass" and episode.cause == "effective" and episode.verified_success
    assert out["work"]["verification"] == "checks pass"


# ── scenario 2: a failed test gives the agent something to act on ──────

def test_a_failed_test_returns_expected_vs_actual_and_the_next_action(monkeypatch, store, bench):
    bench.extend([PYTEST_FAIL, PYTEST_PASS])
    session, client, events, out = _run(monkeypatch, [
        ModelTurn(tool_calls=[_call("terminal", command="pytest -q tests/test_dates.py")]),
        ModelTurn(tool_calls=[_call("file_write", path="dates.py", content="def leap(y): return y % 4 == 0")]),
        ModelTurn(tool_calls=[_call("terminal", command="pytest tests/test_dates.py -q")]),
        ModelTurn(content="Fixed leap years in dates.py; the date tests pass."),
    ], "fix the leap year bug in dates.py")

    failed = _last_tool_result(client, 1)
    assert "<echospeak-note>Verification, before any change: test `pytest -q tests/test_dates.py`: 1 failed, 4 passed, exit 1." in failed
    assert "Failing: tests/test_dates.py::test_leap_year." in failed
    assert "First error: assert 28 == 29" in failed
    assert "don't change the tests" in failed
    passed = _last_tool_result(client, 3)
    assert "Verification, attempt 1" in passed and "improved on the best result so far" in passed
    assert "Baseline: test `pytest -q tests/test_dates.py`: 1 failed, 4 passed, exit 1." in passed
    [episode] = store.episodes()
    assert episode.verification["recovered"] and episode.verification["started_failing"]
    assert episode.cause == "effective"


# ── scenario 3: an optimisation is held to its target and to correctness ─

def test_an_optimisation_reaches_its_target_without_breaking_correctness():
    ledger = VerificationLedger()
    ledger.declare({"objective": "make parsing faster", "criteria": ["mean under 100 ms", "tests still pass"],
                    "check": "python bench.py", "metric": {"name": "mean", "direction": "lower", "target": 100}})
    run = _runner(ledger)
    run("pytest -q", PYTEST_PASS)
    assert "the baseline" in run("python bench.py", OK + "mean: 180.0 ms")
    ledger.observe("file_edit", {"path": "parse.py"}, True, "Edited", 3)
    note = run("python bench.py", OK + "mean: 120.0 ms")
    assert "improved on the best result so far" in note and "Target not met: mean is 120" in note
    assert ledger.completion()["status"] == "partial"
    ledger.observe("file_edit", {"path": "parse.py"}, True, "Edited", 5)
    run("python bench.py", OK + "mean: 90.0 ms")
    run("pytest -q", PYTEST_FAIL)  # faster, but now wrong
    done = ledger.completion()
    assert done["status"] == "partial" and "still fails" in done["detail"]
    ledger.observe("file_edit", {"path": "parse.py"}, True, "Edited", 8)
    run("pytest -q", PYTEST_PASS)
    run("python bench.py", OK + "mean: 95.0 ms")
    assert ledger.completion()["status"] == "verified"
    assert ledger.summary_note().startswith("Verified — bench `python bench.py`: exit 0, 95 ms")
    # hyperfine's mean, in whatever unit it prints, is compared in ms
    assert measure("Time (mean ± σ):     1.250 s ±  0.010 s") == (1250.0, "ms")


# ── scenario 4: several failed approaches, then a verified fix ──────────

def test_failed_approaches_then_success_is_measured_as_recovery():
    ledger = VerificationLedger()
    ledger.declare({"objective": "fix the date parser", "criteria": ["all date tests pass"], "check": "pytest -q"})
    run = _runner(ledger)
    run("pytest -q", FAIL + "3 failed, 2 passed in 0.3s")
    for step, (result, expect) in enumerate([("3 failed, 2 passed", "the same as the best"),
                                             ("2 failed, 3 passed", "improved"),
                                             ("5 passed", "improved")], start=10):
        ledger.observe("file_edit", {"path": "dates.py"}, True, "Edited", step)
        prefix = OK if result == "5 passed" else FAIL
        assert expect in run("pytest -q", prefix + f"{result} in 0.3s")
    done = ledger.completion()
    assert done["status"] == "verified" and done["attempts"] == 3 and done["recovered"] and done["regressions"] == 0


# ── scenario 5: the environment, not the code ─────────────────────────

def test_a_missing_dependency_is_an_environment_blocker_not_a_code_failure():
    ledger = VerificationLedger()
    note = _runner(ledger)("pytest -q", "[exit code 2 · 0.3s · sandbox · /w]\nImportError while importing test module\n"
                                       "E   ModuleNotFoundError: No module named 'requests'\n1 error in 0.05s")
    assert "This is the environment, not your code (No module named 'requests')" in note
    done = ledger.completion()
    assert done["status"] == "blocked" and done["bottleneck"] == "environment"
    assert ledger.summary_note().startswith("Blocked — environment: No module named 'requests'")
    # A module of the project's own that's gone missing is the code's problem.
    own = VerificationLedger()
    _runner(own)("pytest -q", "[exit code 2 · 0.3s · sandbox · /w]\nE   ModuleNotFoundError: No module named 'app.utils'\n1 error in 0.05s")
    assert own.completion()["status"] == "failing"
    # A command that isn't installed, outside any check.
    shell = VerificationLedger()
    shell.observe("terminal", {"command": "poetry install"}, True,
                  "[exit code 1 · 0.1s · this PC · /w]\n'poetry' is not recognized as an internal or external command", 1)
    assert shell.completion()["status"] == "blocked"


def test_an_environment_blocker_never_counts_against_a_lesson(store):
    lesson = store.add_lesson(Lesson(agent_id="echo", title="Reproduce first", text="Run the failing test first."), actor="test")
    episode = Episode(agent_id="echo", goal="fix it", outcome="failure", lessons_used=[lesson.id], cause="environment")
    playbook.attribute(episode)
    after = store.get_lesson(lesson.id)
    assert (after.wins, after.losses, after.uses) == (0, 0, 1) and not episode.failed
    assert profiles._verdict(episode) == ""


# ── scenario 6: stalls and regressions ──────────────────────────────────

def test_a_strategy_that_stopped_improving_is_called_out_and_regressions_point_back_to_the_best():
    ledger = VerificationLedger()
    run = _runner(ledger)
    run("pytest -q", FAIL + "1 failed, 4 passed in 0.2s")
    notes = []
    for step in range(3):
        ledger.observe("file_edit", {"path": "dates.py"}, True, "Edited", 10 + step)
        notes.append(run("pytest -q", FAIL + "1 failed, 4 passed in 0.2s"))
    assert "3 attempts haven't improved on the best result" in notes[-1] and "Don't try more variations" in notes[-1]
    assert ledger.completion()["bottleneck"] == "implementation"
    ledger.observe("file_edit", {"path": "dates.py"}, True, "Edited", 20)
    ledger.observe("file_edit", {"path": "util.py"}, True, "Edited", 21)
    worse = run("pytest -q", FAIL + "3 failed, 2 passed in 0.2s")
    assert "worse than the best result" in worse
    assert ledger.completion()["regressions"] == 1
    # The best verified result is never hidden behind a worse final one.
    assert "best was step" in ledger.summary_note()


def test_a_regression_names_how_many_changes_back_the_best_result_is():
    ledger = VerificationLedger()
    run = _runner(ledger)
    run("pytest -q", FAIL + "2 failed, 3 passed in 0.2s")
    ledger.observe("file_edit", {"path": "a.py"}, True, "Edited", 3)
    ledger.observe("file_edit", {"path": "b.py"}, True, "Edited", 4)
    note = run("pytest -q", FAIL + "4 failed, 1 passed in 0.2s")
    assert "Keep the best result" in note and "2 file changes since then" in note and "checkpoint_undo" in note


# ── scenario 7: apparent success that verification contradicts ─────────

def test_claiming_success_over_a_failing_check_is_sent_back_once_then_noted(monkeypatch, store, bench):
    bench.append(PYTEST_FAIL)
    session, client, events, out = _run(monkeypatch, [
        ModelTurn(tool_calls=[_call("file_write", path="dates.py", content="def leap(y): return y % 4 == 0")]),
        ModelTurn(tool_calls=[_call("terminal", command="pytest -q")]),
        ModelTurn(content="Done: all tests pass now."),
        ModelTurn(content="Done: all tests pass now."),  # insists
    ], "fix the leap year bug")

    assert not client.turns and len(client.calls) == 4  # one correction, never a loop
    nudges = [e for e in events if e.get("type") == "verification_nudge"]
    assert len(nudges) == 1 and "the last check failed" in nudges[0]["note"]
    assert "<echospeak-note>Your reply says the work is done" in client.calls[3][-1]["content"]  # an app note, not the user
    assert _notes(events)[-1].startswith("Not verified: the last check failed — test `pytest -q`: 1 failed, 4 passed, exit 1")
    [episode] = store.episodes()
    assert episode.cause == "false_success" and episode.failed and not episode.verified_success
    assert profiles.profile("echo")["false_success"] == 1


def test_editing_tests_after_they_failed_does_not_count_as_verification():
    ledger = VerificationLedger()
    ledger.declare({"objective": "fix the login bug", "criteria": ["login works"]})
    run = _runner(ledger)
    run("pytest -q", FAIL + "1 failed, 4 passed in 0.2s")
    ledger.observe("file_edit", {"path": "tests/test_login.py"}, True, "Edited", 3)
    note = run("pytest -q", PYTEST_PASS)
    assert "Test files were changed after they failed (tests/test_login.py)" in note
    done = ledger.completion()
    assert done["status"] == "unverified" and done["tamper_after_fail"] == ["tests/test_login.py"]
    level, reasons, _ = grade("fix the login bug", [], outcome="success", claim_failed=False, verification=done)
    assert level == 0  # nothing ran in this synthetic record; the cap applies on top of whatever ran
    assert classify_cause(outcome="success", stops=set(), tools=[], verification=done, text="All tests pass.", level=2) == "false_success"
    # When the task is about the tests, changing them is the work.
    asked = VerificationLedger()
    asked.declare({"objective": "add tests for the login flow", "criteria": ["login is tested"]})
    run = _runner(asked)
    run("pytest -q", FAIL + "1 failed in 0.1s")
    asked.observe("file_write", {"path": "tests/test_login.py"}, True, "Wrote", 3)
    run("pytest -q", OK + "1 passed in 0.1s")
    assert asked.completion()["status"] == "verified"


# ── scenario 8: interrupted work resumes from what was measured ────────

def test_interrupted_work_resumes_from_the_saved_objective_baseline_and_best(monkeypatch, store, bench):
    bench.extend([PYTEST_FAIL])
    session, client, events, out = _run(monkeypatch, [
        ModelTurn(tool_calls=[_call("set_objective", objective="fix leap years", check="pytest -q",
                                    criteria=["2024 is a leap year"])]),
        ModelTurn(tool_calls=[_call("terminal", command="pytest -q")]),
        ModelTurn(content="The leap-year test still fails; I'll continue next time."),
    ], "fix the leap year bug")
    saved = out["work"]["evidence"]
    assert out["work"]["verification"] == "not verified: the last check failed"
    restored = verification.restore(saved)
    assert restored is not None and restored.resumed and restored.objective == "fix leap years"
    assert len(restored.checks) == 1 and restored.check_command == "pytest -q"
    brief = verification.resume_brief(restored)
    assert "Objective: fix leap years" in brief and "Baseline: test `pytest -q`: 1 failed, 4 passed, exit 1" in brief
    assert "re-running the check" in brief
    # The next attempt is measured against the earlier baseline, not from scratch.
    restored.observe("file_edit", {"path": "dates.py"}, True, "Edited", 1)
    note = _runner(restored)("pytest -q", PYTEST_PASS)
    assert "improved on the best result so far" in note
    assert restored.completion()["status"] == "verified" and restored.completion()["recovered"]


# ── scenarios 9 and 10: lessons measured against their holdouts ────────

def test_a_lesson_is_judged_against_its_holdout_not_by_how_often_it_was_used():
    helps = Lesson(agent_id="echo", title="Reproduce first", text="x", wins=6, losses=2, held_wins=2, held_losses=4)
    harms = Lesson(agent_id="echo", title="Rewrite the module", text="x", wins=1, losses=5, held_wins=4, held_losses=2)
    unknown = Lesson(agent_id="echo", title="New", text="x", wins=3, losses=0, held_wins=1, held_losses=0)
    assert evaluation.lesson_effect(helps)["verdict"] == "helps"
    assert evaluation.lesson_effect(harms)["verdict"] == "negative transfer"
    assert evaluation.lesson_effect(unknown)["verdict"] == "not enough holdout data"
    assert playbook.lifecycle(harms)[0] == "retired"


def test_a_verified_success_credits_the_lesson_and_a_misunderstanding_takes_the_blame_back(store):
    lesson = store.add_lesson(Lesson(agent_id="echo", title="Reproduce first", text="Run the failing test first."), actor="test")
    win = store.add_episode(Episode(agent_id="echo", goal="fix it", outcome="success", level=2, execution_id="ex-win",
                                    lessons_used=[lesson.id], cause="effective", verification={"status": "checks_pass"}))
    playbook.attribute(win)
    assert store.get_lesson(lesson.id).wins == 1
    loss = store.add_episode(Episode(agent_id="echo", goal="fix the export", outcome="failure", level=1, execution_id="ex-loss",
                                     lessons_used=[lesson.id], cause="action_failed"))
    playbook.attribute(loss)
    store.update_episode(loss)
    assert store.get_lesson(lesson.id).losses == 1
    # The owner explains it was the wrong request, not a failed strategy: the loss is taken back, no reflection.
    [changed] = learning.record_feedback("ex-loss", -1, "You misunderstood - I wanted the CSV in the other project")
    assert changed.feedback_cause == "misunderstood" and not changed.failed
    assert store.get_lesson(lesson.id).losses == 0
    assert store.reflection_status(changed.id) != "pending"


def test_feedback_that_the_checks_missed_something_marks_a_verifier_gap(store):
    episode = store.add_episode(Episode(agent_id="echo", goal="export the report", outcome="success", level=2,
                                        execution_id="ex-1", cause="effective", verification={"status": "checks_pass"}))
    [changed] = learning.record_feedback("ex-1", -1, "The file was generated, but it doesn't open in Excel")
    assert changed.feedback_cause == "verifier_gap" and changed.failed and not changed.verified_success
    assert store.reflection_status(changed.id) == "pending"  # worth learning a better check from
    [cleared] = learning.record_feedback("ex-1", 0)
    assert cleared.feedback_cause == "" and cleared.effective_cause == "effective"
    assert feedback_cause(episode, -1, "my machine was offline") == "environment"
    assert feedback_cause(Episode(agent_id="echo", goal="g", outcome="failure"), -1, "") == "owner_failed"


def test_evaluation_reports_rates_from_evidence():
    def ep(status: str, **kw: Any) -> Episode:
        return Episode(agent_id="echo", goal="g", outcome=kw.pop("outcome", "success"), verification={"status": status, **kw.pop("v", {})},
                       tokens=kw.pop("tokens", 1000), **kw)
    rows = [ep("checks_pass", cause="effective", v={"started_failing": True, "recovered": True, "attempts": 2}),
            ep("verified", cause="effective", v={"attempts": 1}),
            ep("failing", cause="false_success"),
            ep("blocked", outcome="failure", cause="environment", v={"bottleneck": "environment"}),
            ep("failing", outcome="stopped", cause="stalled", v={"bottleneck": "implementation", "started_failing": True})]
    report = evaluation.summarize(rows)
    assert report["measured"] == 5 and report["verified_completion"] == 0.4
    assert report["false_success"] == round(1 / 3, 3)  # of the 3 that ended claiming success
    assert report["recovery"] == 0.5 and report["stall"] == 0.2
    assert report["blockers"] == {"environment": 1, "implementation": 1} and report["not_attributable"] == 1
    assert report["attempts_to_verified"] == 1.5 and report["tokens_per_verified"] == 1000.0
    split = evaluation.with_and_without_lessons([
        Episode(agent_id="echo", goal="g", outcome="success", level=2, lessons_used=["l1"]),
        Episode(agent_id="echo", goal="g", outcome="failure", lessons_withheld=["l1"]),
        Episode(agent_id="echo", goal="g", outcome="failure", lessons_used=["l1"], cause="environment")])  # not counted
    assert split["with"] == {"wins": 1, "decided": 1, "rate": 1.0} and split["held_back"]["rate"] == 0.0


def test_causes_come_from_evidence():
    def cause(outcome: str, verification: dict[str, Any] | None = None, tools: list[dict[str, Any]] | None = None,
              stops: set[str] | None = None, text: str = "", level: int = 1) -> str:
        return classify_cause(outcome=outcome, stops=stops or set(), tools=tools or [], verification=verification or {},
                              text=text, level=level)

    tool = lambda name, output, ok=False: {"name": name, "label": name, "ok": ok, "output": output, "not_run": False}  # noqa: E731
    assert cause("success", {"status": "checks_pass"}) == "effective"
    assert cause("success", {"status": "failing"}, text="I couldn't get the last test passing.") == "failed_check"
    assert cause("success", {"status": "blocked", "bottleneck": "authorization"}) == "authorization"
    assert cause("failure", tools=[tool("terminal", "[exit code 1 · …]\nbash: cargo: command not found")]) == "environment"
    assert cause("failure", tools=[tool("web_search", "Error: search provider timed out")]) == "tool_failure"
    assert cause("failure", tools=[tool("terminal", "[exit code 1 · …]\nTraceback: KeyError")]) == "action_failed"
    misses = [tool("file_find", "No matches for 'config.yaml'")] * 3
    assert cause("stopped", tools=misses) == "information_gap"
    assert cause("stopped", {"status": "failing", "bottleneck": "implementation"}) == "stalled"
    assert cause("error") == "model_error"
    assert cause("failure", stops={"unverified_claim"}) == "false_success"


# ── boundaries: Chat stays light; external harnesses untouched ─────────

def test_chat_gets_no_verification_workflow_but_echo_native_does(monkeypatch, tmp_path):
    from agent import state, threads
    from agent.lean import rooms
    from config import config
    from tests.test_environments import _session as env_session

    chats = threads.ThreadManager(tmp_path / "threads.json")
    monkeypatch.setattr(threads, "get_thread_manager", lambda: chats)
    room_store = rooms.RoomStore(tmp_path / "rooms.json")
    monkeypatch.setattr(rooms, "get_room_store", lambda: room_store)
    monkeypatch.setattr(state, "_state_store", state.StateStore(tmp_path / "state"))
    monkeypatch.setattr(config, "enable_system_actions", True)
    monkeypatch.setattr(config, "allow_terminal_commands", True)

    chat = env_session(monkeypatch, chats.create_thread(title="Trip ideas", source="web").thread_id)
    turn = chat._build_turn(chat._chat_persona(), "Plan a weekend in Lisbon", history=[], depth=0)
    assert turn.verifier is None and "set_objective" not in turn.toolbox.names
    agent = env_session(monkeypatch, room_store.direct("echo", "Echo").thread_id)
    turn = agent._build_turn(agent.personas.get("echo"), "fix the failing tests", history=[], depth=0)
    assert isinstance(turn.verifier, VerificationLedger) and "set_objective" in turn.toolbox.names
    assert "call set_objective with acceptance criteria" in turn.system_prompt


def test_set_objective_is_inside_the_grant_of_work_that_exists_before_the_turn(monkeypatch, store, bench):
    # Routines create their work record before the turn is built; the grant is frozen from the toolbox then.
    client = ScriptedClient([
        ModelTurn(tool_calls=[_call("set_objective", objective="keep the nightly export green", criteria=["export tests pass"],
                                    check="pytest -q tests/test_export.py")]),
        ModelTurn(content="Objective set; nothing changed yet."),
    ])
    session, events = _session(monkeypatch, {"echo": client})
    session.source = "routine"
    out = session.run("check the nightly export", persona_id="echo")
    result = _last_tool_result(client, 1)
    assert result.startswith("Objective recorded with 1 acceptance criterion"), result
    assert "set_objective" in out["work"]["tools"]


def test_feedback_never_adds_a_model_call_or_touches_a_turn_without_checks():
    ledger = VerificationLedger()
    ledger.observe("web_search", {"query": "x"}, True, "1. result", 1)
    ledger.observe("terminal", {"command": "git status"}, True, OK + "clean", 2, call_id="c1")
    assert not ledger.active() and ledger.feedback("c1") == "" and ledger.summary_note() == ""
    assert ledger.unfinished("All tests pass!") == ""  # nothing measured, nothing to contradict


# ── reading what runners actually print ────────────────────────────────

def test_runner_output_is_parsed_into_counts_names_and_exit_codes():
    assert exit_code(OK + "x") == 0 and exit_code(FAIL + "x") == 1
    assert exit_code("[exited with code 3 within 3s · sandbox · /w]\nx") == 3
    assert exit_code("[still running after 30s · process p1 · sandbox · /w]") is None
    assert counts("===== 2 failed, 10 passed, 1 error in 1.20s =====") == (10, 2, 1)
    assert counts("Tests:       1 failed, 4 passed, 5 total") == (4, 1, 0)
    assert counts(" Tests  2 failed | 8 passed (10)") == (8, 2, 0)
    assert counts("test result: FAILED. 3 passed; 1 failed; 0 ignored") == (3, 1, 0)
    assert counts("Ran 6 tests in 0.01s\n\nFAILED (failures=1, errors=1)") == (4, 1, 1)
    assert counts("--- FAIL: TestParse (0.00s)\n--- PASS: TestOther (0.00s)") == (1, 1, 0)
    assert counts("Found 3 errors in 2 files.") == (0, 3, 0)
    assert counts("✖ 5 problems (3 errors, 2 warnings)") == (0, 3, 0)
    assert counts("no output") == (-1, -1, -1)
    assert failing_names("FAILED tests/a.py::test_x - assert\n  ✕ adds numbers (3 ms)\n--- FAIL: TestGo") == \
        ["tests/a.py::test_x", "adds numbers", "TestGo"]
    ledger = VerificationLedger()
    ledger.observe("process_output", {"process_id": "p1"}, True,
                   "Process p1 is finished with exit code 1: npm test\nRecent output:\nTests:       2 failed, 3 passed, 5 total", 4)
    assert ledger.checks[0].kind == "test" and ledger.checks[0].failed == 2 and ledger.checks[0].exit_code == 1
    assert verification.check_kind("pytest -q") == "test" and verification.check_kind("npx tsc --noEmit") == "typecheck"
    assert verification.check_kind("npm run build") == "build" and verification.check_kind("git status") == ""
    empty = VerificationLedger()
    _runner(empty)("pytest -q", "[exit code 5 · 0.1s · sandbox · /w]\nno tests ran in 0.01s")
    assert empty.completion()["bottleneck"] == "verification"


def _runner(ledger: VerificationLedger):
    """Run a command's output through the ledger the way the loop does; returns the feedback note."""
    state = {"step": 100}

    def run(command: str, output: str) -> str:
        state["step"] += 1
        call_id = f"call{state['step']}"
        ledger.observe("terminal", {"command": command}, True, output, state["step"], call_id=call_id)
        return ledger.feedback(call_id)
    return run
