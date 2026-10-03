"""The soul is really written, read back, and survives into the next session.

The reported bug: the agent said it saved or updated its Soul, nothing was
written, and it acted the part for the rest of the session. Now soul_update
writes atomically and reads back through the loader the next chat uses, a
failed write is reported as failed, and a reply that claims a change no tool
made is challenged. Scripted model turns; no model server needed.
"""

from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Any

import pytest

from agent.core import EchoSpeakAgent
from agent.lean import policy, runtime as lean_runtime, soul
from agent.lean.approvals import get_approval_broker, tool_needs_approval
from agent.lean.job import unbacked_claim
from agent.lean.personas import PersonaStore
from agent.lean.provider import ModelTurn, ToolCall
from config import config
from tests.test_lean_runtime import ScriptedClient

START = "# Echo\n\nI'm Echo, a personal agent.\n"


class _Agent:
    """The real SOUL.md loader (path resolution, cache, size limit) on a bare object."""

    memory = None
    _load_soul = EchoSpeakAgent._load_soul


@pytest.fixture
def soul_file(tmp_path, monkeypatch):
    path = tmp_path / "SOUL.md"
    path.write_text(START, encoding="utf-8")
    monkeypatch.setattr(config.soul, "path", str(path))
    monkeypatch.setattr(config.soul, "enabled", True)
    return path


def _session(monkeypatch, scripts: dict[str, ScriptedClient], *, agent: Any = None, approve: str = "allow",
             personas: PersonaStore | None = None):
    monkeypatch.setattr(lean_runtime.LeanSession, "_client_for", lambda self, persona, routing=False: scripts[persona.id])
    monkeypatch.setattr(lean_runtime.LeanSession, "_recall", lambda self, query, limit=8: [])
    events: list[dict[str, Any]] = []

    def emit(event: dict[str, Any]) -> None:
        events.append(event)
        if event["type"] == "approval_request" and approve:
            threading.Timer(0.02, lambda: get_approval_broker().resolve(event["id"], approve)).start()

    session = lean_runtime.LeanSession(
        agent=agent or _Agent(), session_id=f"soul-{uuid.uuid4().hex[:8]}", request_id=f"req-{uuid.uuid4().hex[:6]}",
        emit=emit, cancel=threading.Event(), source="web",
    )
    if personas is not None:
        session.personas = personas
    return session, events


def _soul_call(call_id: str, action: str, text: str = "", old_text: str = "") -> ModelTurn:
    args = {"action": action, "text": text, "old_text": old_text}
    import json

    return ModelTurn(tool_calls=[ToolCall(call_id, "soul_update", json.dumps(args))])


def _tool_results(client: ScriptedClient, call: int) -> list[str]:
    return [m.get("content", "") for m in client.calls[call] if m.get("role") == "tool"]


# ── the write path ──────────────────────────────────────────────────────

def test_soul_update_persists_and_the_next_session_sees_it(monkeypatch, soul_file):
    scripts = {"echo": ScriptedClient([
        _soul_call("s1", "add", "Keep answers under three sentences unless asked for more."),
        ModelTurn(content="Done: I've updated my soul to keep answers short."),
    ])}
    session, events = _session(monkeypatch, scripts)
    out = session.run("From now on keep your answers short. Put that in your soul.")

    result = _tool_results(scripts["echo"], 1)[0]
    assert result.startswith("Saved and verified")
    assert "Keep answers under three sentences" in soul_file.read_text(encoding="utf-8")
    assert soul_file.read_text(encoding="utf-8").startswith(START.rstrip())  # nothing else lost
    assert out["response"] == "Done: I've updated my soul to keep answers short."  # backed: no nudge
    assert any(e["type"] == "approval_request" and "Add to my soul" in e.get("summary", "") for e in events)
    assert any(e["type"] == "soul_updated" for e in events)

    # A brand-new session (fresh agent, so no in-memory cache) builds its prompt from disk.
    fresh = {"echo": ScriptedClient([ModelTurn(content="Hi.")])}
    later, _ = _session(monkeypatch, fresh, agent=_Agent())
    later.run("hello")
    system_prompt = fresh["echo"].calls[0][0]["content"]
    assert "Keep answers under three sentences unless asked for more." in system_prompt


def test_a_failed_write_is_reported_honestly_and_the_false_claim_is_caught(monkeypatch, soul_file):
    def broken_write(path: Path, text: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(soul, "write_atomic", broken_write)
    scripts = {"echo": ScriptedClient([
        _soul_call("s1", "add", "Always answer in French."),
        # The model ignores the failure and claims success...
        ModelTurn(content="I've updated my soul: I'll answer in French from now on."),
        # ...is challenged, and tells the truth.
        ModelTurn(content="Sorry, I couldn't save that to my soul: the disk is full."),
    ])}
    session, events = _session(monkeypatch, scripts)
    out = session.run("Update your soul: always answer in French.")

    assert _tool_results(scripts["echo"], 1)[0].startswith("Failed: the soul could not be saved (disk full)")
    assert soul_file.read_text(encoding="utf-8") == START  # unchanged
    assert any(e["type"] == "claim_nudge" for e in events)
    assert "no tool call in this turn did that" in scripts["echo"].calls[2][-1]["content"]
    assert out["response"] == "Sorry, I couldn't save that to my soul: the disk is full."
    assert not any(e["type"] == "soul_updated" for e in events)


def test_a_write_that_does_not_read_back_is_a_failure(monkeypatch, soul_file):
    class StaleAgent(_Agent):
        def _load_soul(self) -> str:  # a loader still serving the old text
            return START

    scripts = {"echo": ScriptedClient([_soul_call("s1", "add", "Be brief."), ModelTurn(content="That didn't save.")])}
    session, _ = _session(monkeypatch, scripts, agent=StaleAgent())
    session.run("put 'be brief' in your soul")
    assert _tool_results(scripts["echo"], 1)[0].startswith("Failed: the soul was written but did not read back the same")


def test_a_claim_with_no_tool_call_at_all_is_challenged_then_flagged(monkeypatch, soul_file):
    scripts = {"echo": ScriptedClient([
        ModelTurn(content="Got it, I've saved that to my soul."),
        ModelTurn(content="Done, it's saved in my soul now."),  # still no tool call
    ])}
    session, events = _session(monkeypatch, scripts)
    out = session.run("remember to be concise, save it to your soul")

    assert [e["type"] for e in events].count("claim_nudge") == 1
    flagged = [e for e in events if e["type"] == "claim_unverified"]
    assert flagged and "Not verified" in flagged[0]["note"]
    # The false text from the first attempt was taken back on screen.
    assert any(e["type"] == "text_replace" and e["text"] == "" for e in events)
    assert out["response"] == "Done, it's saved in my soul now."
    assert soul_file.read_text(encoding="utf-8") == START


def test_a_teammate_edits_its_own_soul_in_the_persona_store(monkeypatch, tmp_path, soul_file):
    store = PersonaStore(tmp_path / "agents.json")
    scripts = {"forge": ScriptedClient([
        _soul_call("s1", "replace", "never placeholder stubs, and always run the tests", "never placeholder stubs"),
        ModelTurn(content="Updated."),
    ])}
    session, _ = _session(monkeypatch, scripts, personas=store)
    session.run("Glados, always run the tests: change your soul", persona_id="forge")

    assert _tool_results(scripts["forge"], 1)[0].startswith("Saved and verified")
    assert "always run the tests" in PersonaStore(tmp_path / "agents.json").get("forge").soul
    assert soul_file.read_text(encoding="utf-8") == START  # Echo's soul untouched


# ── edits, gating, claims ───────────────────────────────────────────────

def test_apply_edit_add_replace_remove_and_errors():
    body, err = soul.apply_edit(START, "add", "Be brief.")
    assert not err and body.rstrip().endswith(f"{soul.ADDED_HEADING}\n\n- Be brief.")
    body2, _ = soul.apply_edit(body, "add", "No emoji.")
    assert body2.count(soul.ADDED_HEADING) == 1 and body2.rstrip().endswith("- Be brief.\n- No emoji.")
    swapped, _ = soul.apply_edit(body2, "replace", "Be very brief.", "Be brief.")
    assert "- Be very brief." in swapped and "- Be brief." not in swapped
    removed, _ = soul.apply_edit(swapped, "remove", old_text="No emoji.")
    assert "No emoji" not in removed and "\n-\n" not in removed
    assert soul.apply_edit(START, "remove", old_text="not there")[1].startswith("those words aren't")
    assert soul.apply_edit(START, "add", "")[1].startswith("give the new instruction")
    assert soul.apply_edit(body2, "add", "Be brief.")[1] == "your soul already says that"


def test_over_the_size_limit_nothing_is_written():
    writes: list[str] = []
    ok, message, _ = soul.update_soul(action="add", text="x" * 50, old_text="", read=lambda: START,
                                      write=writes.append, read_back=lambda: "", max_chars=40)
    assert not ok and "over its limit" in message and writes == []


def test_soul_changes_ask_first_and_are_rule_of_two_gated(monkeypatch):
    from agent.lean import settings

    monkeypatch.setattr(settings, "approval_mode", lambda: "smart")
    assert tool_needs_approval(None, "soul_update", {"action": "add"})[0] is True
    monkeypatch.setattr(settings, "approval_mode", lambda: "never")
    assert tool_needs_approval(None, "soul_update", {"action": "add"})[0] is False
    # After reading the web, a soul write needs a person; where nobody can approve, it's refused.
    assert policy.evaluate("soul_update", {}, tainted_by=["web_search"], secrets=[]).action == "ask"
    assert policy.evaluate("soul_update", {}, tainted_by=["web_search"], interactive=False, secrets=[]).action == "deny"


@pytest.mark.parametrize("text, succeeded, flagged", [
    ("Got it, I've saved that to memory.", set(), True),
    ("Got it, I've saved that to memory.", {"memory_save"}, False),
    ("I've updated my soul.", {"memory_save"}, True),
    ("Sure, I'll remember that you take your coffee black.", set(), True),
    ("From now on, I'll keep it brief.", set(), True),
    ("The file has been written to hello.py.", {"file_read"}, True),
    ("The file has been written to hello.py.", {"file_write"}, False),
    ("I've written it and marked it done.", {"complete_task"}, True),
    ("I created that file earlier; it's in Documents.", set(), False),
    ("Done, it's saved in my soul now.", set(), True),
    ("The forecast is updated every hour.", set(), False),
    ("Paris is the capital of France.", set(), False),
    ("I have checked three sources and they agree.", {"web_search"}, False),
])
def test_unbacked_claims(text, succeeded, flagged):
    assert bool(unbacked_claim(text, succeeded)) is flagged


# ── memory_save reads back too ──────────────────────────────────────────

class _FakeMemory:
    """Writes records.json like the real store; can be told to lose the write."""

    def __init__(self, root: Path, lose_writes: bool = False) -> None:
        self._records_path = root / "records.json"
        self.lose_writes = lose_writes
        self.records: dict[str, Any] = {}

    def add_memory_item(self, text: str, **_kwargs: Any) -> str:
        import json

        memory_id = f"m{len(self.records) + 1}"
        self.records[memory_id] = {"id": memory_id, "text": text, "active": True}
        if not self.lose_writes:
            self._records_path.write_text(json.dumps({"records": self.records}), encoding="utf-8")
        return memory_id

    def count_items(self) -> int:
        return len(self.records)


@pytest.mark.parametrize("lose, expected", [(False, "Saved and verified: The user takes coffee black."),
                                            (True, "Failed: the memory store reported a save, but it isn't on disk")])
def test_memory_save_is_verified_on_disk(monkeypatch, tmp_path, soul_file, lose, expected):
    agent = type("Agent", (_Agent,), {"memory": _FakeMemory(tmp_path, lose_writes=lose)})()
    scripts = {"echo": ScriptedClient([
        ModelTurn(tool_calls=[ToolCall("m1", "memory_save", '{"fact": "The user takes coffee black."}')]),
        ModelTurn(content="Noted."),
    ])}
    session, _ = _session(monkeypatch, scripts, agent=agent)
    session.run("I take my coffee black, remember that")
    assert _tool_results(scripts["echo"], 1)[0].startswith(expected)
