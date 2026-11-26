"""The shared desktop and terminal API must delete durable conversation data."""
import json
import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent import memory, state, threads
from agent.lean import rooms, summaries, tool_outputs
from agent.query_journal import get_query_journal
from agent.research_notebook import ResearchNotebook
from agent.vector_store import VectorStore
from api import deps
from api.routes import chat, lean, sessions


def test_deletion_removes_chat_and_search_data_after_restart(tmp_path, monkeypatch):
    registry = threads.ThreadManager(tmp_path / "threads.json")
    store = state.StateStore(tmp_path / "state")
    room_store = rooms.RoomStore(tmp_path / "rooms.json")
    monkeypatch.setattr(threads, "_thread_manager", registry)
    monkeypatch.setattr(rooms, "_STORE", room_store)
    monkeypatch.setattr(state, "_state_store", store)
    monkeypatch.setattr(memory.AgentMemory, "_select_embeddings", lambda self: setattr(self, "use_faiss", False))
    source = memory.AgentMemory(str(tmp_path / "memory"))
    monkeypatch.setattr(memory, "get_agent_memory", lambda: source)
    monkeypatch.setattr(summaries, "_path", lambda sid: tmp_path / f"summary-{sid}.json")
    monkeypatch.setattr(tool_outputs, "root", lambda: tmp_path / "outputs")
    app = FastAPI()
    app.include_router(sessions.router)
    app.include_router(chat.router)
    app.include_router(lean.router)
    client = TestClient(app)

    conversation = client.post("/threads", json={"title": "Synthetic deletion case"}).json()["thread_id"]
    other = client.post("/threads", json={"title": "Keep this chat"}).json()["thread_id"]
    assert registry.get_thread(conversation)
    run = "synthetic-delete-request"
    journal = get_query_journal()
    book = ResearchNotebook()
    journal.claim(run, conversation, "fingerprint")
    journal.append(run, {"type": "token", "text": "purgekeyword"})
    journal.finish(run)
    book.notes(conversation, "purgekeyword")
    book.notes(other, "keep the sibling")
    execution = store.create_execution(thread_id=conversation, query="purgekeyword")
    store.update_execution(execution.id, status="completed")
    store.add_item(turn_id=execution.id, item_type="assistant_message", status="complete",
                   payload={"text": "purgekeyword"}, session_id=conversation)
    store.add_item(turn_id="other", item_type="assistant_message", status="complete",
                   payload={"text": "purgekeyword"}, session_id=other)
    tool = store.create_tool_run(turn_id=execution.id, tool_name="synthetic", session_id=conversation)
    output = tool_outputs.save(conversation, "synthetic", "purgekeyword", untrusted=False)
    sibling_output = tool_outputs.save(other, "synthetic", "keep output", untrusted=True)
    summaries._save(conversation, {"summary": "purgekeyword"})
    store.enqueue_turn(conversation, message="pending", client_request_id="pending")
    session_memory = source.add_memory_item("The test-only archive is green.", scope="session", thread_id=conversation)
    account_memory = source.add_memory_item("The account archive is blue.", scope="account", thread_id=conversation)
    project_memory = source.add_memory_item("The project archive is orange.", scope="project", project_id="project", project_path=str(tmp_path), thread_id=conversation)
    assert session_memory and account_memory and project_memory

    class Embeddings:
        def embed_documents(self, rows):
            return [[float(i), 1.0] for i, _ in enumerate(rows)]

    index = VectorStore.from_texts(["delete", "keep account", "keep project"], Embeddings(),
                                  ids=[session_memory, account_memory, project_memory])
    index.save_local(source.memory_root)
    external = tmp_path / "external-agent-history.jsonl"
    external.write_text("independent source")
    assert len(store.search_messages("purgekeyword")) >= 2

    cancel = threading.Event()
    deps._register_query_cancellation(run, conversation, cancel)
    try:
        assert client.delete(f"/threads/{conversation}").status_code == 409
        assert client.get(f"/threads/{conversation}").status_code == 200
    finally:
        deps._release_query_cancellation(run, cancel)

    assert client.delete(f"/threads/{conversation}").status_code == 200
    assert client.delete(f"/threads/{conversation}").status_code == 200
    assert client.get("/history", params={"thread_id": conversation}).status_code == 404
    assert client.get(f"/threads/{conversation}/state").status_code == 404
    assert client.post("/query/stream", json={"thread_id": conversation, "message": "stale"}).status_code == 404
    assert client.post("/query/queue", json={"thread_id": conversation, "message": "stale"}).status_code == 404
    assert client.get(f"/executions/{execution.id}").status_code == 404
    rows = client.get("/sessions/overview").json()["items"]
    assert conversation not in {row["thread_id"] for row in rows}
    assert store.list_tool_runs_for_session(conversation) == []
    assert store.list_queued_turns(conversation) == []
    assert tool.id not in store._tool_runs
    assert journal.list(conversation) == []
    assert book.notes(conversation) == "" and book.notes(other) == "keep the sibling"
    assert not (tool_outputs.root() / f"{output}.txt").exists()
    assert tool_outputs.load(sibling_output, other)[1] == "keep output"
    assert not summaries._path(conversation).exists()

    restarted = state.StateStore(store.root)
    assert restarted.session_deleted(conversation)
    assert conversation not in {row.thread_id for row in restarted.list_thread_states()}
    assert {hit["session_id"] for hit in restarted.search_messages("purgekeyword")} == {other}
    assert not threads.ThreadManager(tmp_path / "threads.json").get_thread(conversation)
    assert set(VectorStore.load_local(source.memory_root, None).ids) == {account_memory, project_memory}
    source.rebuild_faiss_from_canonical()
    on_disk = json.loads(source._records_path.read_text())["records"]
    assert set(on_disk) == {account_memory, project_memory}
    assert external.read_text() == "independent source"

    room = room_store.create(name="Synthetic direct", agent_ids=["test"], kind="direct")
    assert client.delete(f"/lean/rooms/{room.id}").status_code == 200
    assert client.get(f"/threads/{room.thread_id}").status_code == 404
    assert room_store.get(room.id) is None
