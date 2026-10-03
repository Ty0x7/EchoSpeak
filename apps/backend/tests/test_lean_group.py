"""Group chats and handoffs: who answers, in what order, and what stops runaway
handoffs. Uses scripted model turns; no model server needed."""

from __future__ import annotations

import json
import threading
import uuid
from typing import Any

import pytest

from agent.lean import runtime as lean_runtime
from agent.lean.provider import Endpoint, ModelTurn, ToolCall
from agent.lean.rooms import Room
from agent.lean.toolbox import NativeTool
from agent.state import get_state_store
from tests.test_lean_runtime import ScriptedClient


class BarrierClient(ScriptedClient):
    """Waits for every other parallel agent before answering: deadlocks (and
    fails) if the agents were run one after another."""

    def __init__(self, turns: list[ModelTurn], barrier: threading.Barrier) -> None:
        super().__init__(turns)
        self.barrier = barrier

    def stream_turn(self, messages, **kwargs):
        self.barrier.wait(timeout=10)
        return super().stream_turn(messages, **kwargs)


class _AlwaysDone:
    calls: list = []

    def stream_turn(self, messages, **kwargs):
        return ModelTurn(content='{"done": true, "summary": "Answered."}')


def _session(monkeypatch, scripts: dict[str, Any], *, room: Room | None = None, session_id: str = ""):
    monkeypatch.setattr(lean_runtime.LeanSession, "_client_for", lambda self, persona, routing=False: scripts[persona.id])
    monkeypatch.setattr(lean_runtime.LeanSession, "_recall", lambda self, query, limit=8: [])
    # The completion check: scripted per test when it matters, otherwise "done".
    reviewer = scripts.get("_review") or _AlwaysDone()
    monkeypatch.setattr(lean_runtime.LeanSession, "_review_client", lambda self, persona: reviewer)
    events: list[dict[str, Any]] = []
    session = lean_runtime.LeanSession(
        agent=type("Agent", (), {"memory": None})(),
        session_id=session_id or f"group-{uuid.uuid4().hex[:8]}",
        request_id=f"req-{uuid.uuid4().hex[:6]}",
        emit=events.append,
        cancel=threading.Event(),
        source="web",
        room=room,
    )
    return session, events


def _room(session_id: str, agent_ids: list[str]) -> Room:
    return Room(id=f"room_{session_id}", name="Team", kind="group", agent_ids=agent_ids, thread_id=session_id)


def _delegate(call_id: str, agent: str, task: str = "look it up") -> ToolCall:
    return ToolCall(call_id, "delegate_to_agent", '{"agent": "%s", "task": "%s"}' % (agent, task))


def _tool_results(client: ScriptedClient, call: int) -> list[str]:
    return [m.get("content", "") for m in client.calls[call] if m.get("role") == "tool"]


# ── U1: handoff hygiene ─────────────────────────────────────────────────

def test_teammate_cannot_hand_the_task_straight_back(monkeypatch):
    scripts = {
        "echo": ScriptedClient([
            ModelTurn(tool_calls=[_delegate("d1", "Scout")]),
            ModelTurn(content="Thanks, Scout."),
        ]),
        "scout": ScriptedClient([
            ModelTurn(tool_calls=[_delegate("d2", "Echo")]),
            ModelTurn(content="I'll do it myself: found it."),
        ]),
    }
    session, events = _session(monkeypatch, scripts)
    out = session.run("find the thing", persona_id="echo")

    assert any(r.startswith("Error: Echo handed this task to you") for r in _tool_results(scripts["scout"], 1))
    assert [m["agent_id"] for m in out["messages"]] == ["echo", "scout", "echo"]
    assert [e["agent_id"] for e in events if e["type"] == "agent_start"] == ["echo", "scout", "echo"]


def test_handoff_brief_carries_the_users_message_and_marks_who_handed_off(monkeypatch):
    scripts = {
        "echo": ScriptedClient([ModelTurn(tool_calls=[_delegate("d1", "Scout", "check the score")]), ModelTurn(content="Done.")]),
        "scout": ScriptedClient([ModelTurn(content="3-1.")]),
    }
    session, events = _session(monkeypatch, scripts)
    session.run("what was the score last night?", persona_id="echo")

    brief = scripts["scout"].calls[0][-1]["content"]
    assert "check the score" in brief and "what was the score last night?" in brief
    scout_start = next(e for e in events if e["type"] == "agent_start" and e["agent_id"] == "scout")
    assert scout_start["delegated_by"] == {"id": "echo", "name": "Echo"}


def test_handoffs_per_message_are_capped(monkeypatch):
    monkeypatch.setattr(lean_runtime, "MAX_HANDOFFS_PER_MESSAGE", 1)
    scripts = {
        "echo": ScriptedClient([
            ModelTurn(tool_calls=[_delegate("d1", "Scout")]),
            ModelTurn(tool_calls=[_delegate("d2", "Forge")]),
            ModelTurn(content="Wrapped up myself."),
        ]),
        "scout": ScriptedClient([ModelTurn(content="Scout's part.")]),
        "forge": ScriptedClient([]),  # must never run
    }
    session, _ = _session(monkeypatch, scripts)
    out = session.run("do both parts", persona_id="echo")

    assert scripts["forge"].calls == []
    assert any("already used 1 handoffs" in r for r in _tool_results(scripts["echo"], 2))
    assert out["response"] == "Wrapped up myself."


# ── U2: sticky routing ──────────────────────────────────────────────────

def test_router_keeps_the_last_speaker_for_follow_ups(monkeypatch):
    session_id = f"sticky-{uuid.uuid4().hex[:6]}"
    room = _room(session_id, ["echo", "scout", "forge"])
    scripts = {
        "echo": ScriptedClient([ModelTurn(content="hmm, not sure")]),  # the routing call: no usable answer
        "scout": ScriptedClient([]),
        "forge": ScriptedClient([ModelTurn(content="Built it."), ModelTurn(content="Yes, it also runs on Linux.")]),
    }
    first, _ = _session(monkeypatch, scripts, room=room, session_id=session_id)
    first.run("@Forge build the script")
    second, _ = _session(monkeypatch, scripts, room=room, session_id=session_id)
    out = second.run("does it work on linux too?")

    assert [m["agent_id"] for m in out["messages"]] == ["forge"]
    routing_prompt = scripts["echo"].calls[0][0]["content"]
    assert "The last agent to reply was Glados" in routing_prompt


# ── U3/U4: fan-out, merge, and turn order ───────────────────────────────

def test_named_agents_answer_in_parallel_then_the_lead_merges(monkeypatch):
    session_id = f"fanout-{uuid.uuid4().hex[:6]}"
    room = _room(session_id, ["echo", "scout", "forge"])
    barrier = threading.Barrier(2)
    scripts = {
        "scout": BarrierClient([ModelTurn(content="Option A is faster.")], barrier),
        "forge": BarrierClient([ModelTurn(content="Option B is simpler.")], barrier),
        "echo": ScriptedClient([ModelTurn(content="Both work; pick A for speed, B for simplicity.")]),
    }
    session, events = _session(monkeypatch, scripts, room=room, session_id=session_id)
    out = session.run("@Scout @Forge which option should I use?")

    starts = [e for e in events if e["type"] == "agent_start"]
    assert [e["agent_id"] for e in starts] == ["scout", "forge", "echo"]
    assert starts[2].get("role") == "merge"
    assert len({e["message_id"] for e in starts}) == 3
    # Independent: neither saw the other's answer.
    assert not any("Option B" in str(m.get("content")) for m in scripts["scout"].calls[0])
    assert not any("Option A" in str(m.get("content")) for m in scripts["forge"].calls[0])
    # The merge saw both.
    merge_context = " ".join(str(m.get("content")) for m in scripts["echo"].calls[0])
    assert "Option A" in merge_context and "Option B" in merge_context
    assert [m["agent_id"] for m in out["messages"]] == ["scout", "forge", "echo"]

    saved = [m for t in get_state_store().session_timeline(session_id)["turns"] for m in t["messages"] if m["role"] == "assistant"]
    assert [m["message_id"] for m in saved] == [e["message_id"] for e in starts]
    assert saved[2]["agent_role"] == "merge"


def test_agents_on_different_models_take_turns_and_converge(monkeypatch):
    session_id = f"mixed-{uuid.uuid4().hex[:6]}"
    room = _room(session_id, ["echo", "scout", "forge"])
    models = {"scout": "model-a", "forge": "model-b", "echo": "model-a"}
    monkeypatch.setattr(
        lean_runtime.LeanSession, "_endpoint_for",
        lambda self, persona: Endpoint(base_url="http://127.0.0.1:1234/v1", api_key="", model=models[persona.id], provider="lmstudio", local=True),
    )
    scripts = {
        "scout": ScriptedClient([ModelTurn(content="Option A.")]),
        "forge": ScriptedClient([ModelTurn(content="Agree, A.")]),
        "echo": ScriptedClient([]),  # no merge after taking turns
    }
    session, events = _session(monkeypatch, scripts, room=room, session_id=session_id)
    out = session.run("@Scout @Forge which option?")

    assert [m["agent_id"] for m in out["messages"]] == ["scout", "forge"]
    assert scripts["echo"].calls == []
    forge_context = " ".join(str(m.get("content")) for m in scripts["forge"].calls[0])
    assert "Option A." in forge_context and "If you agree" in forge_context


def test_parallel_agents_are_told_to_answer_only_for_themselves(monkeypatch):
    session_id = f"self-only-{uuid.uuid4().hex[:6]}"
    room = _room(session_id, ["echo", "scout", "forge"])
    scripts = {
        "scout": ScriptedClient([ModelTurn(content="A.")]),
        "forge": ScriptedClient([ModelTurn(content="B.")]),
        "echo": ScriptedClient([ModelTurn(content="Summary.")]),
    }
    session, _ = _session(monkeypatch, scripts, room=room, session_id=session_id)
    session.run("@Scout @Forge thoughts?")
    scout_prompt = scripts["scout"].calls[0][-1]["content"]
    assert "Jarvis, answer for yourself only. Glados will answer separately" in scout_prompt


def test_builtin_teammates_are_renamed_once_and_ids_still_resolve(tmp_path):
    import json

    from agent.lean.personas import PersonaStore

    path = tmp_path / "agents.json"
    old = [
        {"id": "echo", "name": "Echo", "builtin": True},
        {"id": "scout", "name": "Scout", "avatar": "S", "soul": "You are Scout, a meticulous researcher."},
        {"id": "forge", "name": "Echo Code", "avatar": "F", "soul": "Custom instructions the user wrote."},
    ]
    path.write_text(json.dumps({"version": 1, "agents": old}), encoding="utf-8")
    store = PersonaStore(path)
    scout, forge = store.get("scout"), store.get("forge")
    assert (scout.name, scout.avatar) == ("Jarvis", "J") and scout.soul.startswith("You are Jarvis,")
    assert (forge.name, forge.avatar) == ("Glados", "G") and forge.soul == "Custom instructions the user wrote."
    # Old @mentions by id still find them.
    assert store.find_by_name("@scout").id == "scout" and store.find_by_name("Glados").id == "forge"
    # Runs once: a later rename by the user sticks.
    store.update("scout", {"name": "Friday"})
    assert PersonaStore(path).get("scout").name == "Friday"
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 2


# ── work together (stored as mode "discussion") ─────────────────────────

def _discussion_room(session_id: str, cap: int = 30) -> Room:
    room = _room(session_id, ["echo", "scout", "forge"])
    return room.model_copy(update={"mode": "discussion", "max_messages": cap})


def _names(tools: list[dict[str, Any]]) -> set[str]:
    return {t["function"]["name"] for t in tools}


def test_work_together_answers_a_question_without_inventing_tasks(monkeypatch):
    """A plain question: one round of views, then the lead answers. No tasks, no wrap-up."""
    session_id = f"disc-{uuid.uuid4().hex[:6]}"
    scripts = {
        "scout": ScriptedClient([ModelTurn(content="Option A is faster.")]),
        "echo": ScriptedClient([ModelTurn(content="Agreed, A fits here."), ModelTurn(content="Go with A.")]),
        "forge": ScriptedClient([ModelTurn(content="A, unless you need B's plugins.")]),
    }
    session, events = _session(monkeypatch, scripts, room=_discussion_room(session_id), session_id=session_id)
    out = session.run("@Jarvis which option should I pick?")

    assert [m["agent_id"] for m in out["messages"]] == ["scout", "echo", "forge", "echo"]
    assert out["response"] == "Go with A." and out["outcome"]["status"] == "done"
    assert session.job.subtasks == [] and not any(e["type"] == "task_board" for e in events)
    # The second speaker saw the first one's message.
    assert any("Option A is faster." in str(m.get("content")) for m in scripts["echo"].calls[0])
    # Planning turns can look things up but not change anything; the lead's decision can assign work.
    assert not _names(scripts["echo"].tools[0]) & {"file_write", "terminal", "assign_tasks"}
    assert "assign_tasks" in _names(scripts["echo"].tools[1])


def test_work_together_stops_at_its_turn_budget(monkeypatch):
    """Progress every round, never verified done: the turn budget ends it with a visible reason."""
    session_id = f"disc-cap-{uuid.uuid4().hex[:6]}"
    written: list[str] = []
    monkeypatch.setattr(lean_runtime, "coding_tools", lambda: [NativeTool(
        name="file_write", description="write", parameters={"type": "object", "properties": {}},
        func=lambda args: written.append("x") or "Wrote it.",
    )])
    work = [turn for i in range(20) for turn in (
        ModelTurn(tool_calls=[ToolCall(f"w{i}", "file_write", "{}")]),
        ModelTurn(tool_calls=[ToolCall(f"c{i}", "complete_task", '{"summary": "Wrote part %d."}' % i)]),
    )]
    not_done = {"done": False, "reason": "more parts are needed", "next": "Glados", "instruction": "write the next part"}
    scripts = {
        "echo": ScriptedClient([
            ModelTurn(content="Glados should write it."),
            ModelTurn(tool_calls=[ToolCall("a1", "assign_tasks", '{"tasks": [{"owner": "Glados", "task": "write part 1"}]}')]),
            ModelTurn(content="Glados is on it."),
        ]),
        "scout": ScriptedClient([ModelTurn(content="Fine by me.")]),
        "forge": ScriptedClient([ModelTurn(content="Ready.")] + work),
        "_review": type("R", (), {"stream_turn": lambda self, m, **k: ModelTurn(content=json.dumps(not_done))})(),
    }
    session, _ = _session(monkeypatch, scripts, room=_discussion_room(session_id, cap=15), session_id=session_id)
    out = session.run("@Echo write the whole thing")

    assert out["outcome"]["status"] == "stopped"
    assert "budget of 15 agent turns" in out["outcome"]["reason"] and "more parts are needed" in out["outcome"]["reason"]
    assert len(out["messages"]) == 15 and len(written) == 11  # 3 views + the decision + 11 task turns


def test_room_mode_and_cap_are_validated():
    from agent.lean import rooms

    assert rooms._clean_mode("Discussion") == "discussion" and rooms._clean_mode("chaos") == "reply"
    assert rooms._clean_cap(15) == 15 and rooms._clean_cap(100) == 60 and rooms._clean_cap("x") == 30
    # An old "stop after 6 messages" cap is not a turn budget: it gets the default.
    assert rooms._clean_cap(6) == 30 and rooms._clean_cap(12) == 30


def test_plain_language_everyone_addresses_the_whole_group():
    from agent.lean.rooms import mentioned_agents

    team = [type("P", (), {"name": n, "id": n.lower()})() for n in ("Echo", "Jarvis", "Glados")]
    for message in ("Each of you: one tip for focus.", "What do you all think?", "Everyone, quick vote", "both of you weigh in"):
        assert [p.name for p in mentioned_agents(message, team)] == ["Echo", "Jarvis", "Glados"], message
    assert mentioned_agents("What do you think?", team) == []
    assert [p.name for p in mentioned_agents("@Jarvis, and each of you", team)] == ["Jarvis"]  # explicit names win
    assert mentioned_agents("each of you", team[:1]) == []  # one-to-one chats are unaffected
