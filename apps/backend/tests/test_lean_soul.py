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
    # Evaluation MI1 (19 November): "I've noted your build cache key" with nothing saved; the fact was lost.
    ("I've noted your build cache key: zebra-598.", set(), True),
    ("I've noted your build cache key: zebra-598.", {"memory_save"}, False),
    ("I've noted it in plan.md.", {"file_write"}, False),
    ("Noted that the build failed twice before the fix.", set(), False),
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
        self.records[memory_id] = {"id": memory_id, "text": text, "active": True,
                                   "source_execution_id": _kwargs.get("source_execution_id", "")}
        if not self.lose_writes:
            self._records_path.write_text(json.dumps({"records": self.records}), encoding="utf-8")
        return memory_id

    def count_items(self) -> int:
        return len(self.records)


@pytest.mark.parametrize("lose, expected", [(False, "Saved and verified: I take my coffee black."),
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


@pytest.mark.parametrize("text", [
    "That's the only thing I have saved about you for now.",
    "Here is what I have stored about you.",
    "The facts that I have recorded are your name and preferences.",
])
def test_describing_existing_memory_is_not_a_new_write_claim(text):
    assert unbacked_claim(text, {"memory_search"}) == ""


@pytest.mark.parametrize("text", [
    "I have saved your name.",
    "I've stored that in memory.",
    "That's what I have saved; I have also updated your profile.",
    "Here is what I have stored. I have saved your new address.",
])
def test_memory_lookup_still_does_not_back_a_new_save(text):
    assert unbacked_claim(text, {"memory_search"})


def test_memory_work_is_verified_only_after_readback(monkeypatch, tmp_path, soul_file):
    agent = type("Agent", (_Agent,), {"memory": _FakeMemory(tmp_path)})()
    scripts = {"echo": ScriptedClient([
        ModelTurn(tool_calls=[ToolCall("m1", "memory_save", '{"fact": "The user takes coffee black."}')]),
        ModelTurn(content="I've saved your preference."),
    ])}
    session, events = _session(monkeypatch, scripts, agent=agent)
    result = session.run("Remember that I take my coffee black.")
    assert result["work"]["verification"] == "verified"
    receipt = next(e for e in events if e["type"] == "tool_end" and e["name"] == "memory_save")
    assert receipt["meta"]["memory_verified"] is True
    assert not any(e["type"] == "claim_unverified" for e in events)


@pytest.mark.parametrize("lose", [False, True])
def test_already_remembered_fact_requires_a_durable_readback(monkeypatch, tmp_path, soul_file, lose):
    memory = _FakeMemory(tmp_path, lose_writes=lose)
    memory.add_memory_item("I take my coffee black.")
    memory._records = memory.records
    agent = type("Agent", (_Agent,), {"memory": memory})()
    scripts = {"echo": ScriptedClient([
        ModelTurn(tool_calls=[ToolCall("m1", "memory_save", '{"fact": "The user takes coffee black."}')]),
        ModelTurn(content="Noted."),
    ])}
    session, events = _session(monkeypatch, scripts, agent=agent)
    result = session.run("Remember that I take my coffee black.")
    receipt = next(e for e in events if e["type"] == "tool_end" and e["name"] == "memory_save")
    assert bool(receipt["meta"].get("memory_verified")) is not lose
    assert (result["work"]["verification"] == "verified") is not lose


def test_staged_profile_uses_only_current_facts_and_persists_full_message(monkeypatch, tmp_path):
    from agent.state import get_state_store

    memory = _FakeMemory(tmp_path)
    agent = type("Agent", (_Agent,), {"memory": memory})()
    model = ScriptedClient([ModelTurn(content="I've saved it all.") for _ in range(20)])
    scripts = {"echo": model}
    offer, _ = _session(monkeypatch, scripts, agent=agent)
    assert offer.run("I'm going to give you a list of things to remember. My next prompt will be it.")["success"]
    status, _ = _session(monkeypatch, scripts, agent=agent)
    status.session_id = offer.session_id
    status.run("The backup restored successfully. EchoSpeak release verified. See backup-log.md.")
    assert not memory.records
    profile, events = _session(monkeypatch, scripts, agent=agent)
    profile.session_id = offer.session_id
    message = ("Remember this list:\n1. Personal details\n- My home city is Alder Harbor.\n\n"
               "2. Work preferences\n- My default editor is Juniper Edit.\n\n"
               + "Background for later. " * 205 + "\n- My evening drink is barley tea.")
    result = profile.run(message)
    assert result["success"]
    assert not model.calls
    assert len(memory.records) == 3
    assert "barley tea" in result["response"]
    assert "1 other line" in result["response"]
    assert result["deliverable"] is None
    assert sum(e["type"] == "memory_saved" for e in events) == 3
    store = get_state_store()
    execution = store.get_execution(result["execution_id"])
    assert execution.query == message[:4000]
    assert execution.tools_used == ["memory_save"]
    timeline = store.session_timeline(offer.session_id)
    assert timeline["turns"][-1]["messages"][0]["text"] == message


def test_repeating_a_keyed_memory_reports_readback_instead_of_a_new_save(monkeypatch, tmp_path):
    import json
    from agent.memory import AgentMemory

    def lexical_only(self):
        self.use_faiss = False
        self.embeddings = None
        self.simple_memory = []

    monkeypatch.setattr(AgentMemory, "_select_embeddings", lexical_only)
    memory = AgentMemory(memory_path=str(tmp_path / "memory"))
    agent = type("Agent", (_Agent,), {"memory": memory})()

    def save_turn(call_id):
        return ModelTurn(tool_calls=[ToolCall(call_id, "memory_save", json.dumps({
            "fact": "Home city is Alder Harbor.", "key": "home_city"
        }))])

    first_model = ScriptedClient([save_turn("save-first"), ModelTurn(content="Noted.")])
    first, first_events = _session(monkeypatch, {"echo": first_model}, agent=agent)
    first.run("Remember my home city is Alder Harbor.")
    first_receipt = next(e for e in first_events if e["type"] == "tool_end" and e["name"] == "memory_save")
    assert first_receipt["meta"]["memory_written"] is True
    before = memory._records_path.read_bytes()

    again_model = ScriptedClient([save_turn("save-again")] + [
        ModelTurn(content="I've updated your home city in memory.") for _ in range(3)
    ])
    again, events = _session(monkeypatch, {"echo": again_model}, agent=agent)
    again.run("Remember my home city is Alder Harbor.")
    receipt = next(e for e in events if e["type"] == "tool_end" and e["name"] == "memory_save")
    assert receipt["output"].startswith("Already remembered (verified on disk)")
    assert receipt["meta"]["memory_verified"] is True
    assert receipt["meta"]["memory_written"] is False
    assert not any(e["type"] == "memory_saved" for e in events)
    assert any(e["type"] == "claim_unverified" for e in events)
    assert memory._records_path.read_bytes() == before


@pytest.mark.parametrize("between", ["another-thread", "cancel", "public", "second-status"])
def test_staged_memory_authorization_does_not_spill_into_other_turns(monkeypatch, tmp_path, between):
    memory = _FakeMemory(tmp_path)
    agent = type("Agent", (_Agent,), {"memory": memory})()
    model = ScriptedClient([ModelTurn(content="Understood.") for _ in range(20)])
    scripts = {"echo": model}
    offer, _ = _session(monkeypatch, scripts, agent=agent)
    offer.run("I'll send you a list in my next message. Please remember it.")
    profile, events = _session(monkeypatch, scripts, agent=agent)
    if between != "another-thread":
        profile.session_id = offer.session_id
    if between == "public":
        profile.caller_role = "public"
    elif between == "cancel":
        cancel, _ = _session(monkeypatch, scripts, agent=agent)
        cancel.session_id = offer.session_id
        cancel.run("Cancel that. Don't remember anything from the list.")
    elif between == "second-status":
        for _ in range(2):
            status, _ = _session(monkeypatch, scripts, agent=agent)
            status.session_id = offer.session_id
            status.run("Backup status: copy finished.")
    profile.run("1. Personal details\n- My home city is Alder Harbor.\n- I prefer barley tea.")
    assert not memory.records
    assert not any(e["type"] == "tool_start" and e["name"] == "memory_save" for e in events)


def test_staged_memory_receipt_lists_only_durable_writes(monkeypatch, tmp_path):
    memory = _FakeMemory(tmp_path, lose_writes=True)
    agent = type("Agent", (_Agent,), {"memory": memory})()
    model = ScriptedClient([])
    session, events = _session(monkeypatch, {"echo": model}, agent=agent)
    result = session.run("Remember this list:\n- My home city is Alder Harbor.\n- My evening drink is barley tea.")
    assert "couldn’t verify 2 entries" in result["response"]
    assert "Saved and verified" not in result["response"]
    assert not any(e["type"] == "memory_saved" for e in events)


@pytest.fixture
def lexical_memory(monkeypatch, tmp_path):
    from agent.memory import AgentMemory

    def disable_embeddings(self):
        self.use_faiss = False
        self.simple_memory = []

    monkeypatch.setattr(AgentMemory, "_select_embeddings", disable_embeddings)
    return AgentMemory(memory_path=str(tmp_path / "memory"))


@pytest.mark.parametrize("prompt", [
    "- Home city: Test Valley\n- Editor: Foundry",
    "- Home city: Test Valley\n- Editor: Foundry\n\nDon’t save these.",
    "# Example document\n- Publication: Harbor Gazette\n- Sponsor: River Guild\n\nSummarize this without retaining it.",
    "Remember this list:\n- Home city: Test Valley\n- Editor: Foundry\n\nDo not retain any of this.",
])
def test_staged_list_needs_consent_in_current_message(monkeypatch, lexical_memory, prompt):
    agent = type("Agent", (_Agent,), {"memory": lexical_memory})()
    model = ScriptedClient([ModelTurn(tool_calls=[
        ToolCall("unrequested", "memory_save", '{"fact":"Home city: Test Valley","key":"home_city"}'),
    ]), ModelTurn(content="Understood.")])
    scripts = {"echo": model}
    offer, _ = _session(monkeypatch, scripts, agent=agent)
    offer.run("I'll send you a list in my next message. Please remember it.")
    followup, _ = _session(monkeypatch, scripts, agent=agent)
    followup.session_id = offer.session_id
    followup.run(prompt)
    assert lexical_memory._records == {}
    if "Summarize this without retaining it" in prompt:
        assert any("That preference is settled" in m["content"] for m in model.calls[0] if m["role"] == "user")


@pytest.mark.parametrize("scope,restriction", [
    ("session", "For this chat only."),
    ("project", "For this project only."),
])
def test_structured_memory_scope_is_preserved_on_disk_and_on_recall(
    monkeypatch, lexical_memory, tmp_path, scope, restriction,
):
    import json
    from agent.state import get_state_store

    agent = type("Agent", (_Agent,), {"memory": lexical_memory})()
    session, _ = _session(monkeypatch, {"echo": ScriptedClient([])}, agent=agent)
    session.project_root = str(tmp_path)
    get_state_store().update_thread_state(session.session_id, active_project_id="project-a")
    outcome = session.run(f"Remember this list:\n- Home city: Test Valley\n- Editor: Foundry\n\n{restriction}")
    disk = list(json.loads(lexical_memory._records_path.read_text())["records"].values())
    assert len(disk) == 2
    assert {row["scope"] for row in disk} == {scope}
    assert {row["source_execution_id"] for row in disk} == {outcome["execution_id"]}
    own = lexical_memory.runtime_memory_projection("Home city", session_id=session.session_id, project_id="project-a")
    assert any("Test Valley" in row["content"] for row in own)
    outside = lexical_memory.runtime_memory_projection(
        "Home city", session_id="another-chat", project_id="project-b", project_path=str(tmp_path),
    )
    assert not outside
    another_chat = lexical_memory.runtime_memory_projection(
        "Home city", session_id="another-chat", project_id="project-a", project_path=str(tmp_path),
    )
    assert any("Test Valley" in row["content"] for row in another_chat) == (scope == "project")


@pytest.mark.parametrize("seed_legacy", [None, "", "preference:home_city"])
def test_label_correction_supersedes_in_the_same_scope(monkeypatch, lexical_memory, seed_legacy):
    import json

    agent = type("Agent", (_Agent,), {"memory": lexical_memory})()
    scripts = {"echo": ScriptedClient([])}
    if seed_legacy is not None:
        lexical_memory.add_memory_item("Home city: Alder Harbor.", semantic_key=seed_legacy)
        lexical_memory.add_memory_item("Home city: Slate Town.")
    else:
        original, _ = _session(monkeypatch, scripts, agent=agent)
        original.run("Remember this list:\n- My home city is Alder Harbor.\n- Editor: Foundry")
    update, _ = _session(monkeypatch, scripts, agent=agent)
    outcome = update.run("Remember this list:\n- Home city: Moss Quay\n- Editor: Foundry")
    disk = list(json.loads(lexical_memory._records_path.read_text())["records"].values())
    city = [row for row in disk if "home city" in row["text"].casefold()]
    assert [row["text"] for row in city if row["active"]] == ["Home city: Moss Quay."]
    current = next(row for row in city if row["active"])
    assert current["source_execution_id"] == outcome["execution_id"]
    assert all(row["superseded_by"] == current["id"] for row in city if not row["active"])


def test_unknown_project_scope_and_ambiguous_model_write_fail_closed(monkeypatch, lexical_memory):
    agent = type("Agent", (_Agent,), {"memory": lexical_memory})()
    model = ScriptedClient([ModelTurn(tool_calls=[
        ToolCall("invented", "memory_save", '{"fact":"Editor: Foundry","scope":"account"}'),
    ]), ModelTurn(content="Understood.")])
    session, _ = _session(monkeypatch, {"echo": model}, agent=agent)
    session.run("Remember this list for this project only:\n- Home city: Test Valley\n- Editor: Foundry")
    assert lexical_memory._records == {}


@pytest.mark.parametrize("scope,other_project", [("chat", "project-a"), ("project", "project-b")])
def test_chat_search_cannot_return_the_scoped_source_transcript(
    monkeypatch, lexical_memory, tmp_path, scope, other_project,
):
    from agent.state import get_state_store

    agent = type("Agent", (_Agent,), {"memory": lexical_memory})()
    store = get_state_store()
    source, _ = _session(monkeypatch, {"echo": ScriptedClient([])}, agent=agent)
    source.project_root = str(tmp_path)
    store.update_thread_state(source.session_id, active_project_id="project-a")
    source.run(f"Remember this list for this {scope} only:\n- Preview alias: Frosty Loom\n- Shape: hexagon")

    if scope == "project":
        corroborating = ScriptedClient([ModelTurn(tool_calls=[
            ToolCall("find-own", "chat_search", '{"query":"preview alias"}'),
        ]), ModelTurn(content="The preview alias is Frosty Loom.")])
        inside, own_events = _session(monkeypatch, {"echo": corroborating}, agent=agent)
        inside.project_root = str(tmp_path)
        store.update_thread_state(inside.session_id, active_project_id="project-a")
        own_turn = inside.run("Check past conversation messages for the preview alias.")
        own_result = next(e for e in own_events if e["type"] == "tool_end" and e["name"] == "chat_search")
        assert "Frosty Loom" in own_result["output"]
        assert store.get_execution(own_turn["execution_id"]).metadata["memory_context_id"] == "project-a"

    model = ScriptedClient([ModelTurn(tool_calls=[
        ToolCall("find-source", "chat_search", '{"query":"preview alias"}'),
    ]), ModelTurn(content="Done checking.")])
    outside, events = _session(monkeypatch, {"echo": model}, agent=agent)
    outside.project_root = str(tmp_path)
    store.update_thread_state(outside.session_id, active_project_id=other_project)
    outside.run("Check past conversation messages for the preview alias.")
    result = next(e for e in events if e["type"] == "tool_end" and e["name"] == "chat_search")
    assert "Frosty Loom" not in result["output"], result["output"]


@pytest.mark.parametrize(("side_detail", "correction_lead"), [
    (". I brought a grapefruit seltzer today.", "Small correction. Actually, "),
    ("; the deck crew served grapefruit seltzer at the dock.", "Small correction; "),
])
def test_prose_memory_uses_current_authorized_text_through_a_correction(
    monkeypatch, lexical_memory, side_detail, correction_lead,
):
    import json
    from agent.state import get_state_store

    agent = type("Agent", (_Agent,), {"memory": lexical_memory})()

    def propose(fact, key, call_id):
        return {"echo": ScriptedClient([
            ModelTurn(tool_calls=[ToolCall(call_id, "memory_save", json.dumps({"fact": fact, "key": key}))]),
            ModelTurn(content="Noted."),
        ])}

    first, _ = _session(monkeypatch, propose(
        "Takes the 7:45 AM Marlow ferry on Tuesdays for reading on the ride.", "tuesday_ferry", "first",
    ), agent=agent)
    first_result = first.run(f"Please remember that I take the 7:45 Marlow ferry on Tuesdays{side_detail}")
    update, _ = _session(monkeypatch, propose(
        "User takes the 8:10 AM Marlow ferry on Tuesdays for reading, replacing the 7:45 ferry.",
        "tuesday_ferry", "correction",
    ), agent=agent)
    update.session_id = first.session_id
    changed = update.run(
        f"{correction_lead}my Tuesday Marlow ferry leaves at 8:10 now. Please remember that instead."
    )

    disk = list(json.loads(lexical_memory._records_path.read_text())["records"].values())
    old, new = (next(r for r in disk if r["source_execution_id"] == result["execution_id"])
                for result in (first_result, changed))
    assert old["text"] == "I take the 7:45 Marlow ferry on Tuesdays."
    assert new["text"] == "My Tuesday Marlow ferry leaves at 8:10 now."
    assert not old["active"] and old["superseded_by"] == new["id"]
    assert new["active"] and new["supersedes"] == old["id"]
    fresh = lexical_memory.runtime_memory_projection("Tuesday Marlow ferry", session_id="fresh-other-chat")
    assert [row["content"] for row in fresh] == [new["text"]]
    user_item = next(item for item in get_state_store().list_items(changed["execution_id"])
                     if item.item_type == "user_message")
    assert new["source_item_id"] == user_item.id


@pytest.mark.parametrize("request_text,fact,key", [
    ("Please remember that I take the 7:45 Marlow ferry. My manager is Lucy.",
     "The user's manager is Lucy.", "manager"),
    ("Please remember that I take the 7:45 Marlow ferry, but I have grape soda just for today.",
     "The user always brings grape soda.", "usual_drink"),
    ("Please remember that the 7:45 Marlow ferry takes 10 minutes.",
     "The Marlow ferry takes 10 minutes.", "home_city"),
    ("Remember that I take the ferry at 7:45 a.m. I play the flute.", "The user plays the flute.", "flute"),
    ("Remember that instead.", "The user wants that instead.", "instead"),
    ("I recommend this app. Can you run a check? Please remember that instead.",
     "The user uses Marlow App.", "marlow_app"),
])
def test_prose_memory_rejects_unrelated_or_ambiguous_proposals(monkeypatch, lexical_memory, request_text, fact, key):
    import json

    agent = type("Agent", (_Agent,), {"memory": lexical_memory})()
    lexical_memory.add_memory_item("Home city: Alder Harbor.", thread_id="older", semantic_key="home_city")
    model = ScriptedClient([
        ModelTurn(tool_calls=[ToolCall("proposal", "memory_save", json.dumps({"fact": fact, "key": key}))]),
        ModelTurn(content="Understood."),
        ModelTurn(content="Understood."),
    ])
    session, _ = _session(monkeypatch, {"echo": model}, agent=agent)
    initial = lexical_memory._records_path.read_bytes()
    session.run(request_text)
    assert lexical_memory._records_path.read_bytes() == initial


def test_prose_memory_preserves_project_scope_and_literal_time(monkeypatch, lexical_memory, tmp_path):
    import json
    from agent.state import get_state_store

    agent = type("Agent", (_Agent,), {"memory": lexical_memory})()
    model = ScriptedClient([
        ModelTurn(tool_calls=[ToolCall("project-fact", "memory_save", json.dumps({
            "fact": "Monday builds start at 7:10 a.m. to avoid overruns.", "key": "monday_builds",
        }))]),
        ModelTurn(content="Noted."),
    ])
    session, _ = _session(monkeypatch, {"echo": model}, agent=agent)
    session.project_root = str(tmp_path)
    get_state_store().update_thread_state(session.session_id, active_project_id="project-a")
    session.run("Remember that the Monday builds start at 7:10 a.m. for this project only. I think it's an easy rhythm.")
    rows = list(json.loads(lexical_memory._records_path.read_text())["records"].values())
    assert len(rows) == 1 and rows[0]["text"] == "The Monday builds start at 7:10 a.m."
    assert rows[0]["scope"] == "project" and rows[0]["project_id"] == "project-a"
    assert not lexical_memory.runtime_memory_projection(
        "Monday builds", session_id="other", project_id="project-b", project_path=str(tmp_path),
    )
