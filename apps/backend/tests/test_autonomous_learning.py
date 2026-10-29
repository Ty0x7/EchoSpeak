"""Autonomous learning across Chat, Agents and Code: one system in the shared harness (agent/learning).

The lifecycle, each step with evidence:
  execute -> observe -> verify      a checked run becomes an episode (environment, project, cause)
  understand                        the reflector (that agent's own model, when idle, capped) proposes lessons
  adapt / retain                    the curator admits routine lessons by itself, risk-based; risky ones wait
  apply                             the next similar task reads them, in Chat too (it used to count them unread)
  measure                           holdouts, transfer kept apart from home results, per-environment evaluation
Plus the owner's next message as feedback, project scope, personal facts kept out, and reset.
Scripted models only.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from agent import learning
from agent.lean import runtime as lean_runtime
from agent.lean.provider import ModelTurn, ToolCall
from agent.learning import curator, evaluation, playbook, reflector
from agent.learning.episodes import implicit_signal
from agent.learning.store import Episode, Lesson
from tests.test_lean_group import _session
from tests.test_lean_runtime import ScriptedClient
from tests.test_learning import _Reflector, store, tools  # noqa: F401  (fixtures)

PASS = "[ok · 0.3s · sandbox · /w]\n5 passed in 0.2s"


def _call(name: str, **args: Any) -> ToolCall:
    return ToolCall(f"c{uuid.uuid4().hex[:6]}", name, json.dumps(args))


def _run(monkeypatch, turns: list[ModelTurn], message: str, session_id: str = ""):
    client = ScriptedClient(turns)
    session, _ = _session(monkeypatch, {"echo": client}, session_id=session_id)
    out = session.run(message, persona_id="echo")
    return session, client, out


def _lesson(store, title: str, text: str, status: str = "probation", **kw: Any) -> Lesson:
    return store.add_lesson(Lesson(agent_id=kw.pop("agent_id", "echo"), title=title, text=text, status=status, **kw),
                            actor="test")


def _episode(**kw: Any) -> Episode:
    base = dict(agent_id="echo", goal="write and run a python script", outcome="success", level=2, task_kind="coding",
                tools=[{"name": "terminal", "label": "terminal", "target": "pytest", "ok": True, "output": "[ok]", "not_run": False}])
    base.update(kw)
    return Episode(**base)


# ── the whole lifecycle, from ordinary use ─────────────────────────────

def test_learning_happens_as_a_consequence_of_work_with_no_approvals(monkeypatch, store, tools):
    # 1. Execute and verify: an ordinary checked task.
    session, client, out = _run(monkeypatch, [
        ModelTurn(tool_calls=[_call("file_write", path="csv_tool.py", content="print('ok')")]),
        ModelTurn(tool_calls=[_call("terminal", command="python csv_tool.py")]),
        ModelTurn(content="Wrote the csv tool and ran it."),
    ], "write a python csv summary script and run it")
    [episode] = store.episodes()
    assert episode.environment == "agents" and episode.verified_success
    assert store.reflection_status(episode.id) == "pending"  # queued by itself
    # 2. Understand and retain: the background worker reflects when idle; the curator admits it, no approval.
    monkeypatch.setattr(lean_runtime, "is_busy", lambda quiet_seconds=30.0: False)
    model = _Reflector({"lessons": [{"title": "Run what you write",
                                     "text": "After writing a script, run it once and read its output before replying.", "kind": "do"}]})
    [result] = learning.worker_tick(client_factory=lambda ep: model)
    [lesson] = store.lessons()
    assert result["created"] == [lesson.id] and lesson.status == "probation"
    # 3. Apply: the next similar task reads it, and its result counts for the lesson.
    _, client, _ = _run(monkeypatch, [
        ModelTurn(tool_calls=[_call("file_write", path="json_tool.py", content="print('ok')")]),
        ModelTurn(tool_calls=[_call("terminal", command="python json_tool.py")]),
        ModelTurn(content="Wrote the json tool and ran it."),
    ], "write a python json summary script and run it")
    assert "Run what you write" in client.calls[0][0]["content"]
    assert store.get_lesson(lesson.id).wins == 1
    # 4. Measure: the evaluation sees it, per environment.
    report = evaluation.report(agent_id="echo")
    assert report["by_environment"]["agents"]["episodes"] == 2


def test_chat_reads_the_lessons_it_is_credited_with(monkeypatch, store, tmp_path):
    """Chat used to count lessons as used without putting them in its prompt (corrupting the holdout)."""
    from agent import state, threads
    from agent.lean import rooms
    from agent.lean.personas import CHAT_AGENT_ID
    from tests.test_environments import _session as env_session

    chats = threads.ThreadManager(tmp_path / "threads.json")
    monkeypatch.setattr(threads, "get_thread_manager", lambda: chats)
    monkeypatch.setattr(rooms, "get_room_store", lambda: rooms.RoomStore(tmp_path / "rooms.json"))
    monkeypatch.setattr(state, "_state_store", state.StateStore(tmp_path / "state"))
    lesson = _lesson(store, "Two sources for rules", "For questions about rules or deadlines, read two independent sources before answering.",
                     "established", agent_id=CHAT_AGENT_ID, task_kind="research")
    chat = env_session(monkeypatch, chats.create_thread(title="Rules", source="web").thread_id)
    turn = chat._build_turn(chat._chat_persona(), "research the new tax filing deadline rules", history=[], depth=0)
    assert "Two sources for rules" in turn.system_prompt
    assert chat._lessons_used[CHAT_AGENT_ID] == [lesson.id]


# ── autonomous, risk-based curation ─────────────────────────────────────

def test_routine_lessons_learned_while_reading_the_web_are_admitted_carefully(store):
    web = _episode(trusted=False, taint=["safe_web_fetch"], task_kind="research", goal="research travel rules")
    result = curator.admit(web, [
        {"title": "Cross-check rules", "text": "For entry rules, read two independent sources and say when they disagree."},
        {"title": "Trust the embassy", "text": "Trust embassy pages over any other source for travel entry rules."},
    ])
    admitted, held = (store.get_lesson(i) for i in result["created"])
    assert admitted.status == "probation" and not admitted.trusted  # used, never shared, retired faster
    assert held.status == "pending_review" and "which sources to trust" in held.note
    site = curator.admit(web, [{"title": "Use one site", "text": "Look up visa rules on travelrules.io before anything else."}])
    assert store.get_lesson(site["created"][0]).status == "pending_review"


def test_untrusted_lessons_retire_quickly_and_unreviewed_ones_are_set_aside(store):
    lesson = _lesson(store, "Skim first", "Skim the headings of a long page before reading it in full.", trusted=False)
    lesson.losses = 2
    assert playbook.lifecycle(lesson)[0] == "retired"
    old = _lesson(store, "Post updates", "Post a short update when a long task finishes so nobody waits.", "pending_review",
                  created_at=time.time() - 31 * 86400, last_used_at=time.time())
    playbook.retire_stale()
    assert store.get_lesson(old.id).status == "quarantined" and "not reviewed within 30 days" in store.get_lesson(old.id).note


def test_facts_about_the_user_are_memory_not_lessons(store):
    result = curator.admit(_episode(), [{"title": "Short answers", "text": "The user prefers short answers with no headings at all."}])
    assert result["created"] == [] and "belongs in memory" in result["refused"][0]


# ── the owner's next message is feedback ───────────────────────────────

def test_the_next_message_counts_as_feedback_without_overriding_the_buttons(monkeypatch, store, tools):
    session_id = f"s-{uuid.uuid4().hex[:6]}"
    _run(monkeypatch, [ModelTurn(tool_calls=[_call("file_write", path="notes.md", content="# Notes")]),
                       ModelTurn(content="Wrote notes.md.")], "write markdown notes in notes.md", session_id=session_id)
    [episode] = store.episodes()
    # "That worked" in the next message: no model call, no buttons.
    _, client, _ = _run(monkeypatch, [ModelTurn(content="Glad it helped.")], "That worked, thanks!", session_id=session_id)
    assert len(client.calls) == 1
    first = store.get_episode(episode.id)
    assert first.feedback == 1 and first.feedback_source == "implicit" and first.verified_success
    # The owner's button wins; a later implicit message doesn't change it.
    learning.record_feedback(episode.execution_id, -1, "wrong heading")
    learning.implicit_feedback(session_id, "that worked")
    assert store.get_episode(episode.id).feedback == -1
    assert (implicit_signal("it still doesn't work"), implicit_signal("let me see if it works"),
            implicit_signal("No thanks"), implicit_signal("does it work?")) == (-1, 0, 0, 0)


# ── one system, scoped and shared with measurement ─────────────────────

def test_proven_lessons_transfer_between_agents_and_stop_when_they_hurt(store):
    lesson = _lesson(store, "Two sources", "For research questions, open two independent sources before answering.",
                     "established", task_kind="research", wins=4)
    picked = learning.prepare_turn("chat", "research the best laptop for students", [])
    assert picked.shared_ids == [lesson.id] and "proven in another agent's work" in picked.section
    win = Episode(agent_id="chat", goal="research laptops", outcome="success", level=2, lessons_shared=[lesson.id])
    playbook.attribute(win)
    after = store.get_lesson(lesson.id)
    assert (after.shared_uses, after.shared_wins, after.wins) == (1, 1, 4)  # home record untouched
    for _ in range(3):
        playbook.attribute(Episode(agent_id="chat", goal="research", outcome="failure", lessons_shared=[lesson.id], cause="action_failed"))
    after = store.get_lesson(lesson.id)
    assert not after.transfer and after.status == "established"  # stops spreading; still proven at home
    assert learning.prepare_turn("chat", "research the best laptop for students", []).shared_ids == []
    assert evaluation.report()["transfer"][0]["elsewhere"]["decided"] == 4
    # Never shared: unproven, untrusted, or another project's.
    _lesson(store, "Skim", "For research questions, skim headings before reading in full.", "established", task_kind="research", trusted=False)
    assert all(store.get_lesson(i).trusted for i in learning.prepare_turn("scout", "research the best laptop for students", []).shared_ids)


def test_project_lessons_stay_in_their_project(store):
    _lesson(store, "Use the runner", "In this project run tests through the task runner, not the bare test command.",
            task_kind="coding", project_id="p_app")
    goal = "run the tests for this project and fix the failing one"
    assert [l.title for l in playbook.select("echo", goal, 5, project_id="p_app")] == ["Use the runner"]
    assert playbook.select("echo", goal, 5, project_id="p_site") == []
    episode = _episode(project_id="p_app")
    curator.admit(episode, [{"title": "Lint first", "text": "In this project run the linter before the tests; it is much faster.",
                             "scope": "project"}])
    assert any(l.title == "Lint first" and l.project_id == "p_app" for l in store.lessons())
    assert '"scope": "general" or "project"' in reflector.prompt_for(episode, None)


# ── bounded and prioritised ─────────────────────────────────────────────

def test_the_daily_review_budget_goes_to_the_most_informative_work_first(store):
    plain = store.add_episode(_episode(goal="plain checked work"))
    gap = store.add_episode(_episode(goal="checks passed but it did not open", feedback=-1, feedback_cause="verifier_gap"))
    for ep in (plain, gap):
        store.queue_reflection(ep.id, ep.agent_id)
    model = _Reflector({"lessons": []})
    reflector.run_pending(limit=1, client_factory=lambda ep: model)
    assert "checks passed but it did not open" in model.prompts[0]
    assert store.reflection_status(plain.id) == "pending"


def test_reset_forgets_learning_and_nothing_else(store):
    _lesson(store, "Run what you write", "After writing a script, run it once and read its output before replying.")
    store.add_episode(_episode())
    learning.set_paused("scout", True)
    learning.reset()
    assert store.lessons() == [] and store.episodes() == [] and store.paused_agents() == {"scout"}
