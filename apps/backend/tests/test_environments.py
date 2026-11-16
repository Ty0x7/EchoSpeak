"""Chat, Agents and Code: one rule decides where a conversation shows and who answers in it.

Chat is model-first: the chosen model answers in Echo's harness without Echo's persona,
team or delegation. Agents are persistent contacts. Code keeps its own runtimes.
No model calls; real ThreadManager, RoomStore and StateStore in a temp folder.
"""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from agent import echo_code, environments, state, threads
from agent.lean import rooms
from agent.lean import runtime as lean_runtime
from agent.lean.personas import CHAT_AGENT_ID, get_persona_store

_TEAM_TOOLS = {"delegate_to_agent", "soul_update", "spawn_workers", "work_status", "agent_notes", "propose_agent_change"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    chats = threads.ThreadManager(tmp_path / "threads.json")
    monkeypatch.setattr(threads, "get_thread_manager", lambda: chats)
    room_store = rooms.RoomStore(tmp_path / "rooms.json")
    monkeypatch.setattr(rooms, "get_room_store", lambda: room_store)
    store = state.StateStore(tmp_path / "state")
    monkeypatch.setattr(state, "_state_store", store)
    return chats, room_store, store


def _session(monkeypatch, session_id: str, source: str = "web", **kwargs) -> lean_runtime.LeanSession:
    monkeypatch.setattr(lean_runtime.LeanSession, "_recall", lambda self, query, limit=8: [])
    return lean_runtime.LeanSession(
        agent=SimpleNamespace(memory=None, llm_provider=SimpleNamespace(value="lmstudio"),
                              model_runtime=SimpleNamespace(model_id="qwen/qwen3-8b")),
        session_id=session_id, request_id="req-env", emit=lambda _event: None, cancel=threading.Event(),
        source=source, room=rooms.get_room_store().by_thread(session_id), **kwargs)


def test_every_conversation_has_exactly_one_environment(env):
    chats, room_store, _ = env
    chat = chats.create_thread(title="Trip ideas", source="web")
    code = chats.create_thread(title="Code · app", source="web", surface="code")
    contact = room_store.direct("scout", "Jarvis")
    group = room_store.create(name="Team", agent_ids=["scout", "forge"], kind="group")
    routine = chats.create_thread(title="Routine · inbox", source="agent")
    discord = chats.create_thread(title="Discord DM", source="discord")
    assert environments.environment_of(chat.thread_id) == "chat"
    assert environments.environment_of(code.thread_id) == "code"
    assert environments.environment_of(contact.thread_id) == "agents"
    assert environments.environment_of(group.thread_id) == "agents"
    assert environments.environment_of(routine.thread_id) == "agents"  # an agent started it
    assert environments.environment_of(discord.thread_id) == "agents"
    # Unknown ids keep Echo, as before; nothing is migrated.
    assert environments.environment_of("not-a-thread") == "agents"
    # An agent's contact conversation is one conversation, reused.
    assert room_store.direct("scout", "Jarvis").thread_id == contact.thread_id


def test_chat_is_the_model_not_echo(env, monkeypatch):
    chats, _, _ = env
    chat = chats.create_thread(title="Trip ideas", source="web")
    session = _session(monkeypatch, chat.thread_id)
    assert session.model_first
    persona = session._chat_persona()
    assert persona.id == CHAT_AGENT_ID and persona.name == "qwen3-8b"
    turn = session._build_turn(persona, "Plan a weekend in Lisbon", history=[], depth=0)
    names = set(turn.toolbox.names)
    # Ordinary tools stay: search, files, memory, past chats.
    assert {"memory_search", "chat_search"} & names or "web_search" in names
    # No team, delegation, workers, agent work tools or persona editing in Chat.
    assert not names & _TEAM_TOOLS
    prompt = turn.system_prompt
    assert "not Echo" in prompt and "qwen3-8b" in prompt
    assert "## Your team" not in prompt and "delegate_to_agent" not in prompt  # the roster is described (app facts), never offered as a team
    # Chat isn't a contact: the roster never lists it, but its messages still have a name.
    assert CHAT_AGENT_ID not in {agent.id for agent in get_persona_store().list()}
    assert get_persona_store().get(CHAT_AGENT_ID).id == CHAT_AGENT_ID


def test_agents_and_code_keep_their_own_harness(env, monkeypatch):
    chats, room_store, _ = env
    contact = room_store.direct("echo", "Echo")
    assert not _session(monkeypatch, contact.thread_id).model_first  # Echo answers as Echo
    code = chats.create_thread(title="Code · app", source="web", surface="code")
    assert not _session(monkeypatch, code.thread_id).model_first
    chat = chats.create_thread(title="Trip ideas", source="web")
    # Routines, check-ins and channels in any conversation stay with their agent.
    for source in ("routine", "heartbeat", "discord_bot"):
        assert not _session(monkeypatch, chat.thread_id, source=source).model_first
    # Resuming an agent's durable work is never Chat.
    assert not environments.model_first(chat.thread_id, source="web", work_id="work_1")
    assert not environments.model_first(chat.thread_id, source="web", caller_role="trusted")


def test_code_sidebar_groups_sessions_and_moves_native_ones(env, monkeypatch, tmp_path):
    chats, _, store = env
    folders = {name: tmp_path / name for name in ("app", "site")}
    for folder in folders.values():
        folder.mkdir()
    projects = {name: SimpleNamespace(id=f"p_{name}", name=name, workspace_root=str(path), archived=False,
                                      trust_state="trusted") for name, path in folders.items()}
    manager = SimpleNamespace(list_projects=lambda: list(projects.values()),
                              get_project=lambda pid: next((p for p in projects.values() if p.id == pid), None))
    import agent.projects
    monkeypatch.setattr(agent.projects, "get_project_manager", lambda: manager)

    native = chats.create_thread(title="Code · app", source="web", surface="code")
    store.update_thread_state(native.thread_id, active_project_id="p_app", workspace_root=str(folders["app"]))
    orphan = chats.create_thread(title="Code · old", source="web", surface="code")
    store.update_thread_state(orphan.thread_id, active_project_id="p_gone")
    chats.create_thread(title="Not code", source="web")

    view = echo_code.overview()
    by_id = {project["id"]: [row["thread_id"] for row in project["sessions"]] for project in view["projects"]}
    assert by_id == {"p_app": [native.thread_id], "p_site": []}
    assert [row["thread_id"] for row in view["unorganized"]] == [orphan.thread_id]

    # Drag an unorganized session into a project: its next turn works in that folder.
    moved = echo_code.move_session(orphan.thread_id, "p_site")
    assert moved["project_id"] == "p_site"
    scope = store.get_thread_state(orphan.thread_id)
    assert scope.active_project_id == "p_site" and scope.workspace_root == str(folders["site"].resolve())
    assert not echo_code.overview()["unorganized"]

    # A session mid-turn doesn't move; a Chat conversation is never a coding session.
    store.update_thread_state(native.thread_id, current_execution_id="exec_1")
    with pytest.raises(echo_code.RuntimeUnavailable):
        echo_code.move_session(native.thread_id, "p_site")
    with pytest.raises(KeyError):
        echo_code.move_session(chats.create_thread(title="Chat", source="web").thread_id, "p_site")
    projects["site"].trust_state = "untrusted"
    store.update_thread_state(native.thread_id, current_execution_id="")
    with pytest.raises(PermissionError):
        echo_code.move_session(native.thread_id, "p_site")
