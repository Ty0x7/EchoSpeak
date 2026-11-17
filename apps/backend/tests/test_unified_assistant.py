"""ASSISTANT_MODE=unified: one assistant whose capability profile follows the conversation's project.

Real ThreadManager, RoomStore and StateStore in a temp folder; no model calls.
The explicit legacy mode is covered by tests/test_environments.py.
"""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from agent import code_sessions, echo_code, environments, state, threads
from agent.lean import rooms
from agent.lean import runtime as lean_runtime
from config import config

_TEAM_TOOLS = {"delegate_to_agent", "soul_update", "spawn_workers", "work_status", "agent_notes", "propose_agent_change"}
_ENGINEERING = {"terminal", "file_edit", "file_write"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    chats = threads.ThreadManager(tmp_path / "threads.json")
    monkeypatch.setattr(threads, "get_thread_manager", lambda: chats)
    room_store = rooms.RoomStore(tmp_path / "rooms.json")
    monkeypatch.setattr(rooms, "get_room_store", lambda: room_store)
    store = state.StateStore(tmp_path / "state")
    monkeypatch.setattr(state, "_state_store", store)
    folder = tmp_path / "app"
    folder.mkdir()
    import agent.projects
    manager = agent.projects.ProjectManager(tmp_path / "projects")
    trusted = manager.create_project(name="app", workspace_root=str(folder), trust_state="trusted")
    untrusted = manager.create_project(name="other", workspace_root=str(folder), trust_state="untrusted")
    monkeypatch.setattr(agent.projects, "get_project_manager", lambda: manager)
    _IDS.update(p_trusted=trusted.id, p_untrusted=untrusted.id)
    monkeypatch.setattr(config, "assistant_mode", "unified", raising=False)
    # The owner's permission switches still decide; turn the engineering ones on for this test.
    monkeypatch.setattr(config, "enable_system_actions", True, raising=False)
    monkeypatch.setattr(config, "allow_terminal_commands", True, raising=False)
    monkeypatch.setattr(config, "allow_file_write", True, raising=False)
    return chats, room_store, store


def _session(monkeypatch, session_id: str, source: str = "web") -> lean_runtime.LeanSession:
    monkeypatch.setattr(lean_runtime.LeanSession, "_recall", lambda self, query, limit=8: [])
    return lean_runtime.LeanSession(
        agent=SimpleNamespace(memory=None, llm_provider=SimpleNamespace(value="lmstudio"),
                              model_runtime=SimpleNamespace(model_id="qwen/qwen3-8b")),
        session_id=session_id, request_id="req-unified", emit=lambda _event: None, cancel=threading.Event(),
        source=source, room=rooms.get_room_store().by_thread(session_id))


_IDS: dict[str, str] = {}


def _attach(store, session_id: str, project_id: str) -> None:
    store.update_thread_state(session_id, active_project_id=_IDS.get(project_id, project_id))


def test_unified_is_the_default_and_legacy_mode_remains_explicit(monkeypatch):
    monkeypatch.setattr(config, "assistant_mode", "environments", raising=False)
    assert not environments.unified()
    monkeypatch.setattr(config, "assistant_mode", "nonsense", raising=False)
    assert environments.assistant_mode() == "unified"
    monkeypatch.delattr(config, "assistant_mode")
    assert environments.unified()
    monkeypatch.setattr(config, "assistant_mode", " Unified ", raising=False)
    assert environments.unified()


def test_the_profile_follows_the_trusted_project_not_the_tab(env):
    chats, _, store = env
    chat = chats.create_thread(title="Ideas", source="web")
    code = chats.create_thread(title="Code · app", source="web", surface="code")
    # Without a project both converse, including a session opened from the old Code tab.
    assert environments.turn_environment(chat.thread_id, source="web") == "chat"
    assert environments.turn_environment(code.thread_id, source="web") == "chat"
    # A trusted project makes the next turn an engineering turn, wherever it was started.
    _attach(store, chat.thread_id, "p_trusted")
    assert environments.turn_environment(chat.thread_id, source="web") == "code"
    # An untrusted or missing project never grants engineering tools.
    _attach(store, chat.thread_id, "p_untrusted")
    assert environments.turn_environment(chat.thread_id, source="web") == "chat"
    _attach(store, chat.thread_id, "p_gone")
    assert environments.turn_environment(chat.thread_id, source="web") == "chat"
    assert environments.model_first(chat.thread_id, source="web")


def test_agents_routines_guests_and_resumed_work_are_unchanged(env, monkeypatch):
    chats, room_store, store = env
    contact = room_store.direct("scout", "Jarvis")
    _attach(store, contact.thread_id, "p_trusted")
    assert environments.turn_environment(contact.thread_id, source="web") == "agents"
    chat = chats.create_thread(title="Ideas", source="web")
    _attach(store, chat.thread_id, "p_trusted")
    for source in ("routine", "heartbeat", "discord_bot"):
        assert environments.turn_environment(chat.thread_id, source=source) == "agents"
    assert environments.turn_environment(chat.thread_id, source="web", caller_role="trusted") == "agents"
    assert environments.turn_environment(chat.thread_id, source="web", work_id="work_1") == "agents"


def test_echo_answers_and_engineering_tools_appear_only_with_a_trusted_project(env, monkeypatch):
    chats, _, store = env
    chat = chats.create_thread(title="Ideas", source="web")

    talking = _session(monkeypatch, chat.thread_id)
    persona = talking._chat_persona()
    assert talking.model_first and persona.name == "Echo"
    turn = talking._build_turn(persona, "What should I read next?", history=[], depth=0)
    assert not set(turn.toolbox.names) & (_ENGINEERING | _TEAM_TOOLS)
    assert "You are Echo" in turn.system_prompt and "qwen3-8b" in turn.system_prompt
    assert "not Echo" not in turn.system_prompt

    _attach(store, chat.thread_id, "p_trusted")
    working = _session(monkeypatch, chat.thread_id)
    assert working.code_first and not working.model_first
    persona = working._code_persona()
    assert persona.name == "Echo"
    turn = working._build_turn(persona, "Fix the failing test", history=[], depth=0)
    names = set(turn.toolbox.names)
    assert "file_edit" in names and not names & _TEAM_TOOLS
    assert "You are Echo" in turn.system_prompt


def test_no_external_harness_is_offered_or_started(env, tmp_path):
    catalog = echo_code.runtime_catalog()
    assert [row["id"] for row in catalog["runtimes"]] == ["echo"]
    manager = code_sessions.Sessions(tmp_path / "external")
    with pytest.raises(PermissionError, match="no longer starts"):
        manager.admit_external()
    # Echo's own workbench (terminal, Git review, scripts) shares the supervisor's executor and its
    # plain gate. Unified mode must not close it: only Private/Offline and Stop everything do.
    manager.admit()


def test_no_sign_in_nags_for_tools_echo_no_longer_hosts(env, monkeypatch):
    from agent import attention, code_accounts
    signed_out = {"installed": True, "signed_in": False, "plan": "pro", "subscription_usage": {"observed_at": 1}}
    monkeypatch.setattr(code_accounts, "remembered", lambda runtime: dict(signed_out))
    assert attention._coding_agents() == []
    monkeypatch.setattr(config, "assistant_mode", "environments", raising=False)
    assert len(attention._coding_agents()) == 2  # the default mode still reminds the owner


@pytest.mark.parametrize("trusted", [False, True])
def test_unified_echo_uses_the_same_soul_without_granting_extra_tools(env, monkeypatch, trusted):
    chats, _, store = env
    chat = chats.create_thread(title="Identity", source="web")
    if trusted:
        _attach(store, chat.thread_id, "p_trusted")
    session = _session(monkeypatch, chat.thread_id)
    monkeypatch.setattr(session, "_soul", lambda: "Speak calmly and call the owner Captain.")
    monkeypatch.setattr(session, "_endpoint_label", lambda: "qwen3.5:9b")
    persona = session._code_persona() if trusted else session._chat_persona()
    turn = session._build_turn(persona, "Who are you?", history=[], depth=0)
    assert "Speak calmly and call the owner Captain." in turn.system_prompt
    assert "You are Echo" in turn.system_prompt
    assert not set(turn.toolbox.names) & _TEAM_TOOLS
    if not trusted:
        assert not set(turn.toolbox.names) & _ENGINEERING


def test_compact_chat_lists_approved_skills(monkeypatch):
    from agent.lean import chat_mode
    from agent.lean.personas import chat_persona
    monkeypatch.setattr(config, "assistant_mode", "unified")
    prompt = chat_mode.build_prompt(persona=chat_persona(), model="qwen3.5:9b", profile="compact",
        skills="## Skills\n- planning: Plan a project. Use read_skill first.")
    assert "planning" in prompt and "read_skill" in prompt
