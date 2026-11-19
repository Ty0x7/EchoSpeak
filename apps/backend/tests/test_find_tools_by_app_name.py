"""find_tools finds a connected app's tools when the query names the app.

Eval X1 (auto-test-fix cycle 1): asked to use "the accounts service" to register, verify and sign in a user,
the model searched find_tools for "accounts service user registration authentication". The app's name was not
part of what find_tools matched, so the accounts tools only scored on stray words like "user" and lost places
to unrelated built-in tools; verify_email and login never loaded, and the model told the owner the service had
no such tools. The find_tools schema itself invites "the app's name" as a query.
"""
from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from agent import semantic
from agent.lean.toolbox import CHAT_FOCUS, Toolbox
from agent.tool_registry import ToolRegistry

ACCOUNTS = {
    "register_user": "Create an account. Returns a temporary password and an email verification code.",
    "verify_email": "Confirm a user's email address with the code from register_user.",
    "login": "Sign in. Only verified users can sign in.",
    "reset_password": "Issue a new temporary password for an existing user.",
    "list_users": "Usernames, and whether each is verified.",
    "make_admin": "Give a user administrator rights.",
    "delete_user": "Delete an account permanently.",
}


@pytest.fixture
def box(monkeypatch):
    entries = dict(ToolRegistry.get_all())
    for short, doc in ACCOUNTS.items():
        name = f"mcp__accounts__{short}"
        entries[name] = SimpleNamespace(name=name, origin="mcp", mcp_server="accounts", available=True,
                                        policy_flags=(), description=doc, func=None, is_action=True,
                                        input_schema={"type": "object", "properties": {}})
    monkeypatch.setattr(ToolRegistry, "get_all", classmethod(lambda cls: dict(entries)))
    monkeypatch.setattr(ToolRegistry, "get", classmethod(lambda cls, name: entries.get(name)))
    monkeypatch.setattr(ToolRegistry, "get_names", classmethod(lambda cls: list(entries)))
    monkeypatch.setattr(semantic, "similarities", lambda query, texts: None)  # keywords only
    toolbox = Toolbox()
    toolbox.focus(CHAT_FOCUS)  # a local model in ordinary chat: most tools wait behind find_tools
    return toolbox


@pytest.mark.parametrize("query", [
    "accounts service user registration authentication",  # the model's query in X1 r2
    "accounts service",                                     # the model's query in X1 r1
    "accounts",
])
def test_naming_the_app_loads_its_tools_needed_for_the_task(box, query):
    out = box.run("find_tools", {"query": query}).output
    for needed in ("register_user", "verify_email", "login"):
        assert f"mcp__accounts__{needed}" in out, out
        assert f"mcp__accounts__{needed}" not in box.deferred


def test_an_unrelated_query_does_not_pull_in_the_app(box):
    out = box.run("find_tools", {"query": "web search"}).output
    assert "mcp__accounts__" not in out


def test_focus_names_the_connected_apps_it_holds_back(box):
    """Eval X1 (auto-test-fix cycle 2): with the accounts app's 7 tools held behind find_tools, the focus note gave
    six alphabetical examples (agent_sessions, cancel_reminder, ...) and never named the app, so the model told the
    owner it had no accounts tools without ever calling find_tools (r2), or went looking in folders (r1)."""
    note = " ".join(box.notes)
    # Named as an app, not merely as part of a tool name an alphabetical example list happened to reach.
    assert re.search(r"\baccounts\b", note), note
    assert all(f"mcp__accounts__{short}" in box.deferred for short in ACCOUNTS)  # still held: the note only names it


def test_focus_without_connected_apps_adds_no_app_line(monkeypatch):
    monkeypatch.setattr(ToolRegistry, "get_all", classmethod(lambda cls: {}))
    monkeypatch.setattr(ToolRegistry, "get", classmethod(lambda cls, name: None))
    toolbox = Toolbox(toolsets=["none"], extra_tools=[], session_id="t")
    toolbox.native["web_search"] = SimpleNamespace(name="web_search", always=False, hidden=False)
    toolbox.focus(set())
    assert "Connected apps" not in " ".join(toolbox.notes)


def _fresh_box(request: str) -> Toolbox:
    """The fixture's registry, focused for a request (the runtime passes the owner's message as named_in)."""
    toolbox = Toolbox()
    toolbox.focus(CHAT_FOCUS, named_in=request)
    return toolbox


def test_a_request_that_names_the_app_keeps_its_tools_listed(box):
    """Clean XH1 comparison: told "Connected apps: accounts", qwen3.5:9b still searched the files in 7 of 8 runs."""
    focused = _fresh_box("In the accounts service, reset the password for user omar_t.")
    assert all(f"mcp__accounts__{short}" not in focused.deferred for short in ACCOUNTS)
    assert "mcp__accounts__reset_password" in focused.names


def test_a_request_that_does_not_name_the_app_keeps_it_held(box):
    focused = _fresh_box("What's the weather like in Lisbon?")
    assert all(f"mcp__accounts__{short}" in focused.deferred for short in ACCOUNTS)
