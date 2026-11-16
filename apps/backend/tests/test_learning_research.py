"""11.11.7: the weaknesses the research audit confirmed in 11.11.6's learning, each with a regression test.

- transfer between agents had no control group (now: random holdout of transferable lessons)
- lessons were credited on work of a different kind than they were about
- the review queue sorted only its oldest rows, and valuable older experiences aged out
- implicit feedback: a casual "thanks" counted as proof, every agent in a group got it, and
  "that's wrong" anywhere in a message counted
- lessons were injected into small talk, with no size budget
- control mode recorded nothing, so learning-on vs off couldn't be compared
- twins admitted apart were never merged; routine covered successes were reviewed again
Web-learned lessons waiting for confirmation are in test_autonomous_learning.py.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

from agent import learning
from agent.learning import evaluation, playbook, reflector
from agent.learning.episodes import implicit_signal
from agent.learning.store import Episode, ExperienceStore, Lesson
from tests.test_learning import _Reflector, store  # noqa: F401  (fixture)


def _lesson(store, title: str, text: str, status: str = "probation", **kw: Any) -> Lesson:
    return store.add_lesson(Lesson(agent_id=kw.pop("agent_id", "echo"), title=title, text=text, status=status, **kw),
                            actor="test")


def _episode(**kw: Any) -> Episode:
    base = dict(agent_id="echo", goal="write and run a python script", outcome="success", level=2, task_kind="coding",
                tools=[{"name": "terminal", "label": "terminal", "target": "pytest", "ok": True, "output": "[ok]", "not_run": False}])
    base.update(kw)
    return Episode(**base)


def test_transfer_has_a_control_group_and_stops_when_it_does_worse_than_it(store):
    lesson = _lesson(store, "Two sources", "For research questions, open two independent sources before answering.",
                     "established", task_kind="research", wins=5)
    for won in (True, True, True):  # held back from Chat: those tasks went well anyway
        playbook.attribute(Episode(agent_id="chat", goal="g", outcome="success" if won else "failure", level=2,
                                   task_kind="research", lessons_shared_withheld=[lesson.id]))
    for won in (False, False):
        playbook.attribute(Episode(agent_id="chat", goal="g", outcome="success" if won else "failure", level=2,
                                   task_kind="research", lessons_shared=[lesson.id], cause="action_failed"))
    after = store.get_lesson(lesson.id)
    assert (after.shared_held_wins, after.shared_losses) == (3, 2) and not after.transfer
    assert "did better without it" in [e for e in store.lesson_events(lesson.id) if e["action"] == "transfer:stopped"][0]["reason"]
    assert after.status == "established" and after.wins == 5  # its home record is untouched
    # prepare_turn holds transferable lessons back at the transfer rate.
    other = _lesson(store, "Skim then read", "For research questions, skim headings first, then read the relevant part.",
                    "established", task_kind="research")
    import random

    used, held = playbook.holdout([other], rng=random.Random(1), rate=1.0)
    assert used == [] and held == [other]


def test_a_lesson_is_only_credited_on_the_kind_of_work_it_is_about(store):
    coding = _lesson(store, "Run what you write", "After writing a script, run it once and read its output before replying.",
                     task_kind="coding")
    episode = _episode(task_kind="research", lessons_used=[coding.id])  # the request turned out to be research
    playbook.attribute(episode)
    after = store.get_lesson(coding.id)
    assert (after.uses, after.wins, after.losses) == (0, 0, 0) and episode.lessons_used == []


def test_the_review_queue_reaches_valuable_experiences_however_many_routine_ones_wait(store):
    for i in range(30):
        routine = store.add_episode(_episode(goal=f"routine task {i}"))
        store.queue_reflection(routine.id, routine.agent_id, reflector.priority(routine))
    gap = store.add_episode(_episode(goal="checks passed but the export does not open", feedback=-1, feedback_cause="verifier_gap"))
    store.queue_reflection(gap.id, gap.agent_id, reflector.priority(gap))
    model = _Reflector({"lessons": []})
    reflector.run_pending(limit=1, client_factory=lambda ep: model)
    assert "export does not open" in model.prompts[0]


def test_valuable_experiences_wait_longer_for_a_review_than_routine_ones(store):
    old = time.time() - 20 * 86400
    recovered = store.add_episode(_episode(goal="recovered from a failing test", verification={"recovered": True}))
    routine = store.add_episode(_episode(goal="plain checked work"))
    for ep in (recovered, routine):
        store.queue_reflection(ep.id, ep.agent_id, reflector.priority(ep))
    with store._lock, store._conn:
        store._conn.execute("UPDATE reflections SET created_at = ?", (old,))
    model = _Reflector({"lessons": []})
    reflector.run_pending(limit=5, client_factory=lambda ep: model)
    assert len(model.prompts) == 1 and "recovered from a failing test" in model.prompts[0]
    assert store.reflection_status(routine.id) == "skipped"


def test_an_existing_database_gets_the_priority_column(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE reflections (episode_id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, status TEXT NOT NULL,"
                     " created_at REAL NOT NULL, finished_at REAL NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,"
                     " note TEXT NOT NULL DEFAULT '')")
    opened = ExperienceStore(path)
    opened.queue_reflection("ep_x", "echo", 1)
    assert opened.pending_reflections()[0]["priority"] == 1
    opened.close()


def test_a_routine_success_already_covered_by_its_lessons_is_not_reviewed_again():
    covered = _episode(cause="effective", lessons_used=["ls_1"], attribution="win")
    assert "already covered" in reflector.skip_reason(covered)
    assert reflector.skip_reason(_episode(cause="effective", lessons_used=["ls_1"], attribution="win",
                                          verification={"recovered": True})) == ""


def test_implicit_feedback_goes_to_the_lead_only_and_only_at_the_start_of_a_message(store):
    now = time.time()
    lead = store.add_episode(_episode(session_id="s", execution_id="ex", role="lead", created_at=now))
    worker = store.add_episode(_episode(agent_id="scout", session_id="s", execution_id="ex", role="solo", created_at=now))
    learning.implicit_feedback("s", "That's wrong, the deadline moved to March.")
    assert store.get_episode(lead.id).feedback == -1 and store.get_episode(worker.id).feedback == 0
    # Two agents and no lead (a discussion): nobody is blamed for an ambiguous "that's wrong".
    store.add_episode(_episode(session_id="d", execution_id="ex2", role="solo", created_at=now))
    store.add_episode(_episode(agent_id="scout", session_id="d", execution_id="ex2", role="solo", created_at=now))
    assert learning.implicit_feedback("d", "that's wrong") == []
    assert implicit_signal("Thanks! My friend read the report and said they think that's wrong of the council.") == 0


def test_no_lessons_for_small_talk_and_a_size_budget_for_the_rest(store):
    for i in range(6):
        _lesson(store, f"Research rule {i}", "For research questions about prices, open two independent sources and "
                "compare the dates they were published before answering. " + "Keep notes. " * 8,
                "established", task_kind="research")
    assert learning.prepare_turn("echo", "hi there", []).section == ""
    full = learning.prepare_turn("echo", "research current laptop prices and compare sources", [])
    compact = learning.prepare_turn("echo", "research current laptop prices and compare sources", [], limit=2)
    assert full.lesson_ids and full.chars <= playbook.FULL_CHARS + len(playbook.HEADER) + 200
    assert len(compact.lesson_ids) < len(full.lesson_ids) or compact.chars < full.chars


def test_twins_admitted_apart_are_consolidated_without_a_model_call(store):
    proven = _lesson(store, "Run what you write", "After writing a script, run it once and read its output before replying.",
                     "established", wins=4, source_episodes=["ep_a"])
    twin = _lesson(store, "Run your scripts", "After writing a script, run it once and read the output before you reply.",
                   source_episodes=["ep_b"])
    assert playbook.consolidate() == 1
    assert store.get_lesson(twin.id).status == "retired"
    assert store.get_lesson(proven.id).source_episodes == ["ep_a", "ep_b"]


def test_learning_on_and_control_are_compared_with_their_cost(store):
    for mode, outcome, chars in (("on", "success", 400), ("on", "success", 380), ("control", "failure", 410)):
        store.add_episode(_episode(learning_mode=mode, outcome=outcome, verification={"status": "checks_pass" if outcome == "success" else "failing"},
                                   lesson_chars=chars))
    report = evaluation.report(agent_id="echo")
    assert report["by_mode"]["on"]["verified_completion"] == 1.0
    assert report["by_mode"]["control"]["verified_completion"] == 0.0
    assert report["by_mode"]["on"]["lesson_chars_per_task"] == 390.0
