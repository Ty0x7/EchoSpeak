"""What Echo Companion says, from the owner's live feedback: accurate, natural, and quiet when there's nothing to say."""

from __future__ import annotations

import time

import pytest

from agent.companion import narrate, text
from agent.companion.digest import analyse
from agent.companion.observe import CompanionStore, normalise


@pytest.fixture(autouse=True)
def no_name(monkeypatch):
    monkeypatch.setattr(narrate, "owner_name", lambda: "")


def _f(kind="turn_completed", body="Done, renamed it.", minutes=0.5, interacting=False, agent="claude-code", task="", meta=None):
    now = time.time()
    event = {"id": "e", "agent": agent, "session_id": "s", "kind": kind, "at": now, "text": body, "task": task,
             "project": "Work", "turn_started_at": now - minutes * 60, "meta": meta or {}}
    return narrate.facts(event, analyse(body), interacting=interacting, now=now)


REPORT = "## Summary\nFixed the config loader in the harness.\n\n## Verification\n- 52 passed, 0 failed.\n\n## Root cause\n- The harness read the wrong file.\n"


@pytest.mark.parametrize("case,expected", [
    (dict(kind="waiting_input", body="Claude is waiting for your input"), ""),            # idle prompts: never
    (dict(body="Done, renamed it.", interacting=True), ""),                              # the owner is reading it
    (dict(body=REPORT, interacting=True, minutes=1), ""),                                 # short job in their session
    (dict(body=REPORT, interacting=True, minutes=12, task="fix the loader"), "long_job_done"),  # they stepped away
    (dict(body=REPORT, interacting=False), "report"),                                     # background report
    (dict(body="Which do you prefer, A or B?", interacting=False), "needs_you"),
    (dict(kind="needs_permission", body="Bash: rm -rf build/", interacting=True), ""),    # they can see the prompt
    (dict(kind="needs_permission", body="Bash: rm -rf build/", interacting=False), "needs_you"),
    (dict(body="Done.", agent="glados", meta={"measured": "checks_pass", "agent_name": "Glados"}), "report"),
])
def test_speaks_only_when_it_is_worth_it(case, expected):
    assert narrate.worth_saying(_f(**case)) == expected


def test_stale_events_stay_silent():
    f = _f(body=REPORT)
    f["age_seconds"] = narrate.STALE + 1
    assert narrate.worth_saying(f) == ""


def test_system_notifications_are_not_the_owners_request(tmp_path):
    store = CompanionStore(tmp_path / "o.sqlite3", tmp_path / "spool")
    base = {"session_id": "s1", "cwd": "/w/app", "hook_event_name": "UserPromptSubmit"}
    store.add(normalise({"agent": "claude-code", "received_at": 1.0, "event": {**base, "prompt_id": "1",
        "prompt": "<pasted_content id='x'># Repair the backend connection\n\nPlease fix it.</pasted_content>"}}))
    store.add(normalise({"agent": "claude-code", "received_at": 2.0, "event": {**base, "prompt_id": "2", "cwd": "/w/app/web",
        "prompt": "<task-notification><task-id>b1</task-id></task-notification>"}}))
    store.add(normalise({"agent": "claude-code", "received_at": 3.0, "event": {**base, "prompt_id": "3", "prompt": "continue"}}))
    [row] = store.sessions(now=4.0)
    assert row["task"] == "Repair the backend connection"   # kept through "continue" and the notification
    assert row["project"] == "app"                           # fixed at session start, not the agent's later cd
    assert "<" not in row["last_prompt"]


@pytest.mark.parametrize("prompt,task", [
    ("Excellent progress. I approve proceeding with the companion install.", "the companion install"),
    ("Please fix the flaky eval test", "fix the flaky eval test"),
    ("continue", ""), ("thanks!", ""), ("<task-notification>x</task-notification>", ""),
])
def test_task_titles(prompt, task):
    assert text.task_title(prompt) == task


def test_natural_grammar_and_attribution():
    line = narrate.compose(_f(body=REPORT, task="restore the backend and install the build"), "report")
    assert line.startswith("Claude just wrapped up restoring the backend and installing the build.")
    assert "It says the tests pass." in line and "Something worth knowing" in line
    assert narrate.gerund("the install") == "the install" and narrate.gerund("run the evals") == "running the evals"
    perm = narrate.compose(_f(kind="needs_permission", body="Bash: rm -rf build/"), "needs_you")
    assert perm.startswith("Claude needs your permission to run a shell command")
    assert "Codex" in text.lower_first("Codex still needs a test")


def test_name_only_from_memory_and_not_every_time(monkeypatch):
    monkeypatch.setattr(narrate, "owner_name", lambda: "Sam")
    assert narrate.compose(_f(body=REPORT), "report", greet=True).startswith("Hey Sam, ")
    assert "Sam" not in narrate.compose(_f(body=REPORT), "report", greet=False)
    monkeypatch.setattr(narrate, "owner_name", lambda: "")
    assert narrate.compose(_f(body=REPORT), "report", greet=True).startswith("Claude")


def test_owner_name_comes_from_profile_memory(monkeypatch):
    class Memory:
        def personal_memory_summary(self):
            return [{"text": "User's name is Sam.", "type": "profile"}]
    import agent.memory as memory_module
    monkeypatch.setattr(memory_module, "get_agent_memory", lambda: Memory())
    monkeypatch.setattr(text.config if hasattr(text, "config") else __import__("config").config, "user_display_name", "", raising=False)
    text._name.update(at=0.0, value="")
    assert text.owner_name() == "Sam"
    text._name.update(at=0.0, value="")


@pytest.mark.parametrize("rewrite,ok", [
    ("Codex finished the loader; it says 52 tests pass.", True),
    ("Codex finished the loader; it says 60 tests pass.", False),                     # a number not in the facts
    ("Codex finished the loader and Jarvis checked it; it says 52 tests pass.", False),  # an agent not in the facts
    ("Codex finished; he says 52 tests pass.", False),                                 # gendered agent
    ("Codex finished; want me to merge it? It says 52 tests pass.", False),            # companion offering to act
    ("Codex finished the loader and 52 tests pass.", False),                           # dropped "says"
])
def test_guard_keeps_rewrites_faithful(rewrite, ok):
    f = _f(body=REPORT, agent="codex", task="fix the loader")
    grounded = "Codex just wrapped up fixing the loader. It says the tests pass."
    assert narrate.guard(f, grounded, rewrite)[0] is ok
