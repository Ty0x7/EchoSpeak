"""find_tools finds a connected app's tools when the query names the app.

Eval X1 (auto-test-fix cycle 1): asked to use "the accounts service" to register, verify and sign in a user,
the model searched find_tools for "accounts service user registration authentication". The app's name was not
part of what find_tools matched, so the accounts tools only scored on stray words like "user" and lost places
to unrelated built-in tools; verify_email and login never loaded, and the model told the owner the service had
no such tools. The find_tools schema itself invites "the app's name" as a query.
"""
from __future__ import annotations

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
