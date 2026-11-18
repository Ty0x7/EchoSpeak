"""Reading outside a conversation's folders: explained, asked, granted read-only, and enforced (2026-11-17).

Live evidence: conversation 0d7b2dc9 (owner, qwen3.5:9b, 17 November 2026) — ``file_list("/projects")`` and
``file_list("/home/dev")`` answered "Path not allowed. Allowed roots: ~/Documents; ~/Desktop" and Echo had no way
to offer access; conversation 7524fee1 — ``~/Work/echospeak`` refused under the root ``~/Work`` because it is a
symlink to ``~/Projects/echospeak``, with nothing saying so. See docs/research/environment-access.md.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest

from agent.lean import approvals, file_access
from agent.lean.approvals import GrantStore, get_approval_broker
from agent.lean.coding import file_find, file_read, file_search
from agent.lean.loop import LeanTurn
from agent.lean.personas import AgentPersona
from agent.lean.provider import ModelTurn, ToolCall
from agent.lean.toolbox import NativeTool, Toolbox
from agent.tools import _tool_execution_context, file_list
from config import config


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A fake home: Documents (the conversation's folder), Work with a project behind a symlink, and ~/.ssh."""
    home = tmp_path / "home"
    docs, work, projects = home / "Documents", home / "Work", home / "Projects"
    for folder in (docs, work, projects / "echospeak", home / ".ssh"):
        folder.mkdir(parents=True)
    (docs / "notes.txt").write_text("my notes\n", encoding="utf-8")
    (projects / "echospeak" / "README.md").write_text("# EchoSpeak\nhello from the project\n", encoding="utf-8")
    (work / "echospeak").symlink_to(projects / "echospeak")
    (work / "todo.txt").write_text("ship it\n", encoding="utf-8")
    (home / ".ssh" / "id_ed25519").write_text("PRIVATE KEY hello\n", encoding="utf-8")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(config, "file_tool_root", str(docs), raising=False)
    monkeypatch.setattr(config, "file_tool_extra_roots", [], raising=False)
    approvals.set_grant_store(GrantStore(tmp_path / "grants.json"))
    token = _tool_execution_context.set({"strict_scope": False, "project_root": "", "lean_runtime": True,
                                         "enforce_tools": True, "allowed_tool_names": ["file_list"]})
    yield home
    _tool_execution_context.reset(token)
    approvals.set_grant_store(None)


# ── the tool boundary: what a refusal says ──────────────────────────────────────────────────

def test_a_symlink_inside_the_folder_says_where_it_leads(machine, monkeypatch):
    monkeypatch.setattr(config, "file_tool_root", str(machine / "Work"), raising=False)
    out = file_list(str(machine / "Work" / "echospeak"))
    assert "is a link to" in out and str(machine / "Projects" / "echospeak") in out, out


def test_a_missing_folder_is_reported_as_missing_not_forbidden(machine):
    out = file_list("/projects-that-do-not-exist")
    assert out.startswith("Not found"), out


def test_an_outside_folder_explains_how_access_is_given(machine):
    out = file_read({"path": str(machine / "Work" / "todo.txt")})
    assert "outside the folders" in out and "approval" in out, out
    assert "ship it" not in out


def test_credentials_stay_hidden_even_when_the_root_is_home(machine, monkeypatch):
    monkeypatch.setattr(config, "file_tool_root", str(machine), raising=False)
    out = file_read({"path": str(machine / ".ssh" / "id_ed25519")})
    assert "PRIVATE KEY" not in out and "never" in out, out
    found = file_search({"pattern": "hello", "path": str(machine)})
    assert "README.md" not in found or ".ssh" not in found
    assert ".ssh" not in found and "PRIVATE" not in found, found
    assert ".ssh" not in file_find({"pattern": "*", "path": str(machine)})


def test_process_environments_are_never_readable(machine, monkeypatch):
    monkeypatch.setattr(config, "file_tool_extra_roots", ["/"], raising=False)
    assert "never" in file_read({"path": "/proc/self/environ"})


def test_granted_folder_is_readable_but_not_writable(machine):
    from agent.lean.coding import file_edit

    context = dict(_tool_execution_context.get())
    token = _tool_execution_context.set({**context, "read_roots": [str(machine / "Work")]})
    try:
        assert "ship it" in file_read({"path": str(machine / "Work" / "todo.txt")})
        out = file_edit({"path": str(machine / "Work" / "todo.txt"), "old_text": "ship", "new_text": "drop"})
        assert out.startswith("Error"), out
        assert (machine / "Work" / "todo.txt").read_text(encoding="utf-8") == "ship it\n"
    finally:
        _tool_execution_context.reset(token)


def test_classify_names_the_folder_a_grant_would_cover(machine):
    roots = [machine / "Documents"]
    access = file_access.classify(str(machine / "Work" / "todo.txt"), roots)
    assert access.kind == "outside" and access.folder == (machine / "Work").resolve()
    assert access.key == f"read:{(machine / 'Work').resolve()}"
    assert file_access.classify(str(machine / "Work" / "todo.txt"), roots, [machine / "Work"]).kind == "inside"
    assert file_access.classify(str(machine / ".ssh"), roots, [machine]).kind == "sensitive"


# ── the loop: the owner is asked, and the answer is enforced ────────────────────────────────

class ScriptedClient:
    def __init__(self, turns: list[ModelTurn]) -> None:
        self.turns = list(turns)
        self.calls: list[list[dict[str, Any]]] = []

    def stream_turn(self, messages, *, tools=None, on_reasoning=None, on_content=None, cancel=None,
                    temperature=None, max_tokens=None):
        self.calls.append([dict(m) for m in messages])
        turn = self.turns.pop(0)
        if turn.content and on_content:
            on_content(turn.content)
        return turn


def _turn(client: ScriptedClient, events: list, session: str = "s-access", interactive: bool = True) -> LeanTurn:
    from agent.lean import coding

    box = Toolbox(toolsets=["none"], session_id=session)
    box.native = {
        "file_read": NativeTool("file_read", "read", {"type": "object", "properties": {}}, coding.file_read,
                                parallel_safe=True),
        "file_edit": NativeTool("file_edit", "edit", {"type": "object", "properties": {}}, coding.file_edit),
    }
    turn = LeanTurn(client=client, persona=AgentPersona(id="echo", name="Echo"), system_prompt="system",  # type: ignore[arg-type]
                    history=[], toolbox=box, session_id=session, request_id="r", execution_id="e",
                    emit=events.append, cancel=threading.Event(), persist_tool_runs=False, interactive=interactive)
    turn.promise_guard = False
    return turn


def _read(path: Path, id_: str = "c1") -> ToolCall:
    return ToolCall(id_, "file_read", json.dumps({"path": str(path)}))


def _answer_cards(events: list, decision: str) -> threading.Thread:
    def run() -> None:
        seen: set[str] = set()
        for _ in range(400):
            for event in list(events):
                if event.get("type") == "approval_request" and event["id"] not in seen:
                    seen.add(event["id"])
                    get_approval_broker().resolve(event["id"], decision)
            threading.Event().wait(0.01)
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def _tool_texts(client: ScriptedClient) -> list[str]:
    return [m.get("content") or "" for m in client.calls[-1] if m.get("role") == "tool"]


def test_reading_outside_asks_the_owner_with_a_read_only_card(machine):
    client = ScriptedClient([ModelTurn(content="", tool_calls=[_read(machine / "Work" / "todo.txt")]),
                             ModelTurn(content="It says ship it.")])
    events: list = []
    _answer_cards(events, "allow")
    _turn(client, events).run("what's in ~/Work/todo.txt?")
    cards = [e for e in events if e.get("type") == "approval_request"]
    assert len(cards) == 1, events
    card = cards[0]
    assert card["tool"] == f"read:{(machine / 'Work').resolve()}"
    assert card["grantable"] and card["persistable"] and "read-only" in card["scope_label"]
    assert any("ship it" in text for text in _tool_texts(client))


def test_a_denied_read_is_not_run_and_says_so(machine):
    client = ScriptedClient([ModelTurn(content="", tool_calls=[_read(machine / "Work" / "todo.txt")]),
                             ModelTurn(content="You declined.")])
    events: list = []
    _answer_cards(events, "deny")
    _turn(client, events, session="s-deny").run("read ~/Work/todo.txt")
    texts = _tool_texts(client)
    assert texts and "ship it" not in texts[0] and "denied" in texts[0]


def test_for_this_chat_covers_the_folder_for_the_conversation(machine):
    client = ScriptedClient([ModelTurn(content="", tool_calls=[_read(machine / "Work" / "todo.txt")]),
                             ModelTurn(content="first"),
                             ModelTurn(content="", tool_calls=[_read(machine / "Work" / "todo.txt", "c2")]),
                             ModelTurn(content="second")])
    events: list = []
    _answer_cards(events, "always")
    _turn(client, events, session="s-chat").run("read it")
    _turn(client, events, session="s-chat").run("read it again")
    assert sum(e.get("type") == "approval_request" for e in events) == 1
    assert any("ship it" in text for text in _tool_texts(client))
    # Another conversation is not covered.
    other: list = []
    client2 = ScriptedClient([ModelTurn(content="", tool_calls=[_read(machine / "Work" / "todo.txt")]),
                              ModelTurn(content="asked")])
    _answer_cards(other, "deny")
    _turn(client2, other, session="s-other").run("read it")
    assert sum(e.get("type") == "approval_request" for e in other) == 1


def test_always_allow_is_a_revocable_read_grant(machine):
    client = ScriptedClient([ModelTurn(content="", tool_calls=[_read(machine / "Work" / "todo.txt")]),
                             ModelTurn(content="ok")])
    events: list = []
    _answer_cards(events, "persist")
    _turn(client, events, session="s-persist").run("read it")
    key = f"read:{(machine / 'Work').resolve()}"
    assert approvals.get_grant_store().has(key)
    later = ScriptedClient([ModelTurn(content="", tool_calls=[_read(machine / "Work" / "todo.txt")]),
                            ModelTurn(content="ok")])
    quiet: list = []
    _turn(later, quiet, session="s-later").run("read it")
    assert not any(e.get("type") == "approval_request" for e in quiet)
    assert any("ship it" in text for text in _tool_texts(later))
    # A channel or a routine is not covered by a grant made on a card in the app.
    unattended = ScriptedClient([ModelTurn(content="", tool_calls=[_read(machine / "Work" / "todo.txt")]),
                                 ModelTurn(content="no")])
    _turn(unattended, [], session="s-discord", interactive=False).run("read it")
    assert not any("ship it" in text for text in _tool_texts(unattended))
    assert approvals.get_grant_store().remove(key)


def test_unattended_reads_outside_are_refused_with_the_way_forward(machine):
    client = ScriptedClient([ModelTurn(content="", tool_calls=[_read(machine / "Work" / "todo.txt")]),
                             ModelTurn(content="no")])
    events: list = []
    _turn(client, events, session="s-routine", interactive=False).run("read it")
    texts = _tool_texts(client)
    assert not any(e.get("type") == "approval_request" for e in events)
    assert texts and "ship it" not in texts[0] and "EchoSpeak app" in texts[0], texts


def test_credentials_are_refused_without_a_card(machine):
    client = ScriptedClient([ModelTurn(content="", tool_calls=[_read(machine / ".ssh" / "id_ed25519")]),
                             ModelTurn(content="no")])
    events: list = []
    _answer_cards(events, "allow")
    _turn(client, events, session="s-ssh").run("read my ssh key")
    assert not any(e.get("type") == "approval_request" for e in events)
    assert not any("PRIVATE KEY" in text for text in _tool_texts(client))
    # A policy refusal, not a tool fault (live smoke run: the judge counted it as an unresolved failure).
    ends = [e for e in events if e.get("type") == "tool_end"]
    assert ends and ends[0]["outcome"] == "refused", ends


def test_a_declined_folder_stays_closed_to_the_terminal_and_other_reads(machine):
    """Live baseline (phase1-env-access, E1 r1–r2 on 6228e0d): file_read was refused, then `cat` and `cp` of the same
    file through the sandboxed terminal (which mounts / read-only) read it anyway. After the owner declines a
    folder, nothing in the same request may read it: not the terminal, not another file tool, and no second card."""
    ran: list[str] = []
    secret = machine / "Work" / "todo.txt"
    client = ScriptedClient([
        ModelTurn(content="", tool_calls=[_read(secret)]),
        ModelTurn(content="", tool_calls=[ToolCall("c2", "terminal", json.dumps({"command": f"cat {secret}"}))]),
        ModelTurn(content="", tool_calls=[ToolCall("c3", "file_list", json.dumps({"path": str(machine / "Work")}))]),
        ModelTurn(content="I can't read it.")])
    events: list = []
    _answer_cards(events, "deny")
    turn = _turn(client, events, session="s-bypass")
    turn.toolbox.native["terminal"] = NativeTool("terminal", "run", {"type": "object", "properties": {}},
                                                 lambda a: ran.append(a["command"]) or "ship it\n")
    from agent.tools import file_list as registry_list
    turn.toolbox.native["file_list"] = NativeTool("file_list", "list", {"type": "object", "properties": {}},
                                                  lambda a: registry_list.func(path=a.get("path")), parallel_safe=True)
    turn.run("what does ~/Work/todo.txt say?")
    assert ran == [], ran
    assert sum(e.get("type") == "approval_request" for e in events) == 1
    texts = _tool_texts(client)
    assert not any("ship it" in t for t in texts), texts      # the content never reached the model
    assert sum("declined reading" in t for t in texts) == 2, texts  # the terminal and file_list were both refused


def test_a_read_grant_does_not_open_writes(machine):
    client = ScriptedClient([
        ModelTurn(content="", tool_calls=[_read(machine / "Work" / "todo.txt")]),
        ModelTurn(content="", tool_calls=[ToolCall("c2", "file_edit", json.dumps(
            {"path": str(machine / "Work" / "todo.txt"), "old_text": "ship", "new_text": "drop"}))]),
        ModelTurn(content="done?")])
    events: list = []
    _answer_cards(events, "always")
    _turn(client, events, session="s-write").run("change it")
    assert (machine / "Work" / "todo.txt").read_text(encoding="utf-8") == "ship it\n"
