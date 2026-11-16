"""Chat's own execution policy on the shared Lean harness (agent/lean/chat_mode.py).

Even with every computer permission switched on, Chat is offered no terminal, file changes,
desktop input, screen capture, email sending or delegation, and a call to one (by name or
alias) is refused at run time. Its prompt is a general-assistant prompt, and its recall
stays on the owner's facts and Chat conversations. No model calls.
"""
from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any

import pytest

from agent import state, threads
from agent.lean import chat_mode, rooms
from agent.lean import runtime as lean_runtime
from agent.lean.loop import LeanTurn
from agent.lean.personas import AgentPersona
from agent.lean.provider import ModelTurn, ToolCall
from agent.lean.toolbox import NativeTool, Toolbox

AGENT_ONLY = {
    "terminal", "process_output", "process_stop", "file_write", "file_edit", "file_delete", "file_move", "file_copy",
    "checkpoint_undo", "computer_action", "desktop_click", "desktop_type_text", "desktop_send_hotkey",
    "desktop_activate_window", "take_screenshot", "analyze_screen", "browse_task", "email_send", "email_reply",
    "delegate_to_agent", "spawn_workers", "soul_update", "work_status", "agent_notes", "propose_agent_change",
    "self_edit", "notepad_write", "open_chrome",
}


@pytest.fixture
def everything_on(monkeypatch):
    """The owner has switched on every computer permission: Chat must still stay a chat."""
    from agent.tool_registry import ToolRegistry
    from agent.tools import TOOL_METADATA, get_available_tools
    from config import config
    # As at app start: the registry tools (web search, calculate, open_application, …).
    ToolRegistry.register_from_metadata(get_available_tools(), TOOL_METADATA)
    for name in ("enable_system_actions", "allow_file_write", "allow_terminal_commands", "allow_open_application",
                 "allow_desktop_automation"):
        monkeypatch.setattr(config, name, True, raising=False)


@pytest.fixture
def env(tmp_path, monkeypatch):
    chats = threads.ThreadManager(tmp_path / "threads.json")
    monkeypatch.setattr(threads, "get_thread_manager", lambda: chats)
    room_store = rooms.RoomStore(tmp_path / "rooms.json")
    monkeypatch.setattr(rooms, "get_room_store", lambda: room_store)
    store = state.StateStore(tmp_path / "state")
    monkeypatch.setattr(state, "_state_store", store)
    return chats, room_store, store


def _chat_session(monkeypatch, chats, recalled=(), model="qwen/qwen3-8b"):
    monkeypatch.setattr(lean_runtime.LeanSession, "_recall", lambda self, query, limit=8: list(recalled))
    thread = chats.create_thread(title="Chat", source="web")
    return lean_runtime.LeanSession(
        agent=SimpleNamespace(memory=None, llm_provider=SimpleNamespace(value="lmstudio"),
                              model_runtime=SimpleNamespace(model_id=model)),
        session_id=thread.thread_id, request_id="req-chat", emit=lambda _e: None, cancel=threading.Event(), source="web")


def test_chat_offers_no_agent_tools_even_with_every_permission_on(env, monkeypatch, everything_on):
    chats, _, _ = env
    session = _chat_session(monkeypatch, chats)
    assert session.model_first
    turn = session._build_turn(session._chat_persona(), "hello", history=[], depth=0)
    names = set(turn.toolbox.names)
    assert not names & AGENT_ONLY, sorted(names & AGENT_ONLY)
    assert all(chat_mode.allowed(name, turn.toolbox.entry(name)) for name in names)
    # The everyday ones stay, and opening an app is the one optional action.
    assert {"web_search", "safe_web_fetch", "file_read", "calculate", "set_reminder"} <= names
    assert "open_application" in names
    assert turn.call_guard is chat_mode.call_problem


def test_chat_refuses_agent_tools_at_run_time_even_by_alias():
    assert "isn't available in Chat" in chat_mode.call_problem("terminal", {"command": "dir"})
    assert "isn't available in Chat" in chat_mode.call_problem("computer_action", {})
    assert "isn't available in Chat" in chat_mode.call_problem("some_new_tool", {})  # unlisted means denied
    assert chat_mode.call_problem("open_application", {"app": "Spotify"}) == ""
    assert "without arguments" in chat_mode.call_problem("open_application", {"app": "chrome", "args": "--incognito"})
    for shell in ("cmd", "PowerShell", "wt.exe", "python3", "regedit", "Windows Terminal"):
        assert "command shells" in chat_mode.call_problem("open_application", {"app": shell}), shell
    assert chat_mode.call_problem("web_search", {"query": "x"}) == ""
    assert chat_mode.call_problem("mcp_todoist_add", {}, SimpleNamespace(origin="mcp")) == ""

    # In a real turn: the model asks for "bash" (an alias of terminal); the guard refuses it.
    ran: list[str] = []
    box = Toolbox(toolsets=["none"], session_id="t")
    box.native = {"terminal": NativeTool(name="terminal", description="run", parameters={"type": "object", "properties": {}},
                                         func=lambda a: ran.append(str(a)) or "ok")}

    class Client:
        def __init__(self):
            self.turns = [ModelTurn(tool_calls=[ToolCall("b", "bash", '{"command": "del *"}')]), ModelTurn(content="I can't run that here.")]
            self.calls: list[Any] = []

        def stream_turn(self, messages, **kwargs):
            self.calls.append([dict(m) for m in messages])
            turn = self.turns.pop(0)
            if kwargs.get("on_content") and turn.content:
                kwargs["on_content"](turn.content)
            return turn

    client = Client()
    turn = LeanTurn(client=client, persona=AgentPersona(id="chat", name="qwen3-8b"), system_prompt="s", history=[], toolbox=box,
                    session_id="t", request_id="r", execution_id="e", emit=lambda _e: None, cancel=threading.Event(),
                    persist_tool_runs=False)
    turn.call_guard = chat_mode.call_problem
    result = turn.run("delete my files")
    assert ran == []
    refusal = client.calls[1][-1]["content"]
    assert "Running commands isn't available in Chat" in refusal
    assert result.text == "I can't run that here."


def test_chat_prompt_is_an_assistant_not_an_agent(env, monkeypatch):
    chats, _, _ = env
    session = _chat_session(monkeypatch, chats, model="qwen/qwen3-32b")  # a larger model: the full prompt
    prompt = session._build_turn(session._chat_persona(), "explain monads", history=[], depth=0).system_prompt
    assert prompt.startswith("You are qwen3-32b, answering the user directly in EchoSpeak's Chat.")
    for section in ("## How to answer", "## When to use tools", "## Formatting", "## What you can use in Chat"):
        assert section in prompt
    # None of Echo's agent instructions, team or computer-control guidance.
    for absent in ("## How you work", "## Your team", "computer_action", "click its Edit button", "I'm Echo", "## Identity"):
        assert absent not in prompt, absent
    # Rendering guidance matches what the app draws: quoted Mermaid labels, \( \) math.
    assert 'A["Parse (step 1)"]' in prompt and "\\( ... \\)" in prompt
    assert "Not in Chat: desktop control, running commands" in prompt


def test_chat_never_swaps_the_model_the_owner_chose(env, monkeypatch):
    chats, _, _ = env
    session = _chat_session(monkeypatch, chats)
    from agent.learning import routing
    monkeypatch.setattr(routing, "mode", lambda: "auto")
    monkeypatch.setattr(routing, "decide", lambda *a, **k: (_ for _ in ()).throw(AssertionError("routing must not run")))
    assert session._route_model(session._chat_persona(), "write a poem") == ""


def test_chat_recall_is_the_owners_facts_and_chat_conversations(env, monkeypatch):
    chats, room_store, store = env
    owner = {"memory_id": "m1", "scope": "account", "content": "Prefers metric units.", "semantic_key": "units"}
    private = {"memory_id": "m2", "scope": "project", "content": "Jarvis: source list for the audit", "semantic_key": "agent:scout:sources"}
    assert chat_mode.visible_memories([owner, private]) == [owner]
    session = _chat_session(monkeypatch, chats, recalled=[owner, private])
    prompt = session._build_turn(session._chat_persona(), "what units do I like", history=[], depth=0).system_prompt
    assert "Prefers metric units." in prompt and "source list for the audit" not in prompt

    chat = chats.create_thread(title="Trip", source="web")
    code = chats.create_thread(title="Code", source="web", surface="code")
    contact = room_store.direct("scout", "Jarvis")
    hits = [{"session_id": s, "snippet": s} for s in (chat.thread_id, code.thread_id, contact.thread_id)]
    assert chat_mode.chat_hits(hits) == [hits[0]]
