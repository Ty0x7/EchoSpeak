"""Channel callers: only the owner gets the full toolset, memory, and past chats.

Since 10.0 Discord, Telegram, Twitch, and Twitter all run on the lean runtime.
These pin the guest restrictions that the legacy pipeline used to enforce.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any

import pytest

from agent.adapters import get_adapter
from agent.lean import runtime as lean_runtime
from agent.lean.personas import get_persona_store

_HOST_TOOLS = {"terminal", "file_write", "file_read", "file_delete", "memory_save", "memory_search",
               "chat_search", "delegate_to_agent", "process_start"}


def _session(monkeypatch, role: str, recalls: list[str]) -> lean_runtime.LeanSession:
    monkeypatch.setattr(lean_runtime.LeanSession, "_recall", lambda self, query, limit=8: recalls.append(query) or [])
    return lean_runtime.LeanSession(
        agent=type("Agent", (), {"memory": None})(),
        session_id=f"role-{uuid.uuid4().hex[:8]}",
        request_id="req-role",
        emit=lambda _event: None,
        cancel=threading.Event(),
        source="discord_bot",
        caller_role=role,
    )


@pytest.mark.parametrize("role", ["public", "trusted"])
def test_guests_get_lookup_tools_only_and_no_memory(monkeypatch, role):
    recalls: list[str] = []
    session = _session(monkeypatch, role, recalls)
    turn = session._build_turn(get_persona_store().get("echo"), "what's in my files?", history=[], depth=0)
    names = set(turn.toolbox.names)
    assert names <= set(lean_runtime.GUEST_TOOLS[role])
    assert not names & _HOST_TOOLS
    assert recalls == []  # the owner's memories never reach a guest's prompt


def test_owner_keeps_the_full_toolset(monkeypatch):
    recalls: list[str] = []
    session = _session(monkeypatch, "owner", recalls)
    turn = session._build_turn(get_persona_store().get("echo"), "hello", history=[], depth=0)
    assert "chat_search" in turn.toolbox.names
    assert recalls == ["hello"]


def test_unknown_role_falls_back_to_public(monkeypatch):
    assert _session(monkeypatch, "admin", []).caller_role == "public"


@pytest.mark.parametrize(
    ("source", "info", "expected"),
    [
        ("discord_bot", {"user_id": "1", "access_reason": "open_server"}, "public"),
        ("discord_bot_dm", {"user_id": "2", "access_reason": "trusted_user"}, "trusted"),
        ("discord_bot", {"user_id": "3", "access_reason": "owner_id"}, "owner"),
        ("twitch", None, "public"),
        ("twitter", None, "public"),
        ("web", None, "owner"),
    ],
)
def test_roles_resolve_from_the_channel(source: str, info: Any, expected: str):
    role = get_adapter(source).resolve_role(source, info)
    assert str(getattr(role, "value", role)) == expected


def test_the_agent_is_told_who_it_is_talking_to(monkeypatch):
    session = _session(monkeypatch, "public", [])
    turn = session._build_turn(get_persona_store().get("echo"), "can you read my files?", history=[], depth=0)
    note = turn.system_prompt.split("## Who you're talking to", 1)[1]
    assert "member of the public" in note and "Discord server channel" in note
    assert "no access to your owner's files" in note

    assert "Who you're talking to" not in lean_runtime.caller_note("web", "owner")  # app chats get no note
    assert lean_runtime.caller_note("web", "owner") == ""
    assert "your owner through Telegram" in lean_runtime.caller_note("telegram", "owner")
    assert "someone your owner trusts" in lean_runtime.caller_note("discord_bot_dm", "trusted")


def test_memory_lookups_use_the_request_not_the_channel_wrapper(monkeypatch):
    recalls: list[str] = []
    session = _session(monkeypatch, "owner", recalls)
    wrapped = "Recent #general messages:\n- bob: lol\n\nUser request: what's my sister's name?"
    session._build_turn(get_persona_store().get("echo"), wrapped, history=[], depth=0)
    assert recalls == ["what's my sister's name?"]
    assert lean_runtime.request_text("no wrapper here") == "no wrapper here"
