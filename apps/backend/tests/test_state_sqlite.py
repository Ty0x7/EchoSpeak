"""State store on SQLite: one-time JSON import, reload, and chat search."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.state import StateStore


@pytest.fixture(autouse=True)
def _multi_writer(monkeypatch):
    monkeypatch.setenv("ECHOSPEAK_ALLOW_MULTI_WRITER", "1")


def test_json_records_are_imported_once_and_kept_as_a_copy(tmp_path: Path):
    root = tmp_path / "state"
    root.mkdir()
    legacy = {"s1": {"thread_id": "s1", "objective": "from json"}}
    (root / "thread_state.json").write_text(json.dumps(legacy), encoding="utf-8")

    store = StateStore(root)
    assert store.get_thread_state("s1").objective == "from json"
    assert (root / "state.db").exists()
    assert (root / "thread_state.json").exists()

    # Later edits to the JSON file are ignored: SQLite is now the source of truth.
    (root / "thread_state.json").write_text(json.dumps({"s1": {"thread_id": "s1", "objective": "stale"}}), encoding="utf-8")
    store.update_thread_state("s1", objective="from sqlite")
    assert StateStore(root).get_thread_state("s1").objective == "from sqlite"


def test_executions_and_messages_survive_a_restart(tmp_path: Path):
    root = tmp_path / "state"
    store = StateStore(root)
    turn = store.create_execution(thread_id="chat-a", query="plan the garden beds")
    store.add_item(turn_id=turn.id, item_type="assistant_message", status="complete", session_id="chat-a",
                   payload={"text": "Tomatoes go in the sunny bed.", "agent_name": "Echo"})

    reloaded = StateStore(root)
    timeline = reloaded.session_timeline("chat-a")
    assert timeline["count"] == 1
    texts = [m.get("text") or m.get("content") for m in timeline["turns"][0]["messages"]]
    assert any("garden" in str(t) for t in texts)


def test_search_finds_past_messages_by_word_and_prefix(tmp_path: Path):
    store = StateStore(tmp_path / "state")
    one = store.create_execution(thread_id="chat-a", query="How do I deploy the website?")
    store.add_item(turn_id=one.id, item_type="assistant_message", status="complete", session_id="chat-a",
                   payload={"text": "Push to main and the deploy workflow publishes it.", "agent_name": "Echo"})
    two = store.create_execution(thread_id="chat-b", query="Best soup for a cold day?")
    store.add_item(turn_id=two.id, item_type="assistant_message", status="complete", session_id="chat-b",
                   payload={"text": "Chicken noodle, always.", "agent_name": "Jarvis"})

    hits = store.search_messages("deplo")
    assert {hit["session_id"] for hit in hits} == {"chat-a"}
    assert any("[deploy]" in hit["snippet"] for hit in hits)

    soup = store.search_messages("noodle")
    assert soup and soup[0]["session_id"] == "chat-b" and soup[0]["agent"] == "Jarvis" and soup[0]["role"] == "assistant"

    assert store.search_messages("deploy", session_id="chat-b") == []
    assert store.search_messages("  ") == []
    # Search syntax characters in the query never raise.
    assert isinstance(store.search_messages('deploy" OR (x'), list)
    # And the index is rebuilt from disk on restart.
    assert StateStore(tmp_path / "state").search_messages("noodle")
