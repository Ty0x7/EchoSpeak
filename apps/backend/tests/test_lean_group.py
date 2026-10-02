"""Group chats and handoffs: who answers, in what order, and what stops runaway
handoffs. Uses scripted model turns; no model server needed."""

from __future__ import annotations

import threading
import uuid
from typing import Any

import pytest

from agent.lean import runtime as lean_runtime
from agent.lean.provider import Endpoint, ModelTurn, ToolCall
from agent.lean.rooms import Room
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


def _session(monkeypatch, scripts: dict[str, Any], *, room: Room | None = None, session_id: str = ""):
    monkeypatch.setattr(lean_runtime.LeanSession, "_client_for", lambda self, persona, routing=False: scripts[persona.id])
    monkeypatch.setattr(lean_runtime.LeanSession, "_recall", lambda self, query, limit=8: [])
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
    assert "The last agent to reply was Forge" in routing_prompt


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
