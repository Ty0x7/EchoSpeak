from types import SimpleNamespace

import pytest

from agent.chat_branches import branch_before_prompt
from agent.chat_titles import update_in_background
from agent.lean.provider import Endpoint, reasoning_effort_for, thinking_controls
from agent.lean.rooms import RoomStore
from agent.state import StateStore
from agent.threads import ThreadManager


@pytest.fixture
def stores(tmp_path, monkeypatch):
    state = StateStore(tmp_path / "state")
    threads = ThreadManager(tmp_path / "threads.json")
    rooms = RoomStore(tmp_path / "rooms.json")
    monkeypatch.setattr("agent.state.get_state_store", lambda: state)
    monkeypatch.setattr("agent.threads.get_thread_manager", lambda: threads)
    monkeypatch.setattr("agent.lean.rooms.get_room_store", lambda: rooms)
    return state, threads, rooms


def completed_turn(store, session, text, response):
    scope = store.get_thread_state(session)
    turn = store.create_execution(thread_id=session, query=text, runtime_provider="gemini", model_id="gemini-test",
                                  active_project_id=scope.active_project_id, workspace_id=scope.workspace_id)
    store.add_item(turn_id=turn.id, session_id=session, item_type="assistant_message", status="complete", payload={"text": response})
    store.update_execution(turn.id, status="completed", success=True, response_preview=response)
    return turn


def test_branch_keeps_only_prior_context_and_binding_without_authority(stores):
    state, threads, _ = stores
    source = threads.create_thread(title="Garden plan")
    state.ensure_session_model_binding(source.thread_id, provider_id="gemini", model_id="gemini-test")
    state.update_thread_state(source.thread_id, active_project_id="garden-project", workspace_id="garden-workspace")
    first = completed_turn(state, source.thread_id, "Plan a garden", "Choose sunny beds")
    anchor = completed_turn(state, source.thread_id, "Add herbs", "Use basil")
    completed_turn(state, source.thread_id, "Add mint", "Keep it in a pot")
    state.create_approval(thread_id=source.thread_id, execution_id=first.id, tool="file_write", action_summary="Write the plan")
    state.update_execution(first.id, status="completed", success=True)
    branch = branch_before_prompt(source.thread_id, execution_id=anchor.id)
    timeline = state.session_timeline(branch.thread_id)
    assert [m["text"] for t in timeline["turns"] for m in t["messages"]] == ["Plan a garden", "Choose sunny beds"]
    bound = state.get_thread_state(branch.thread_id)
    assert bound.model_binding.model_id == "gemini-test"
    assert bound.active_project_id == "garden-project"
    assert bound.workspace_id == "garden-workspace"
    assert not bound.pending_approval_id and not bound.active_turn_id
    assert not state.get_pending_approval(branch.thread_id)
    assert len(state.session_timeline(source.thread_id)["turns"]) == 3
    assert branch.message_count == 1
    assert StateStore(state.root).session_timeline(branch.thread_id)["turns"]


def test_branch_rejects_foreign_anchor_before_creating_anything(stores):
    state, threads, _ = stores
    first, second = threads.create_thread(), threads.create_thread()
    foreign = completed_turn(state, second.thread_id, "Private", "Second chat")
    with pytest.raises(ValueError, match="belongs"):
        branch_before_prompt(first.thread_id, execution_id=foreign.id)
    assert len(threads.list_threads()) == 2


def test_branch_preserves_group_members_and_discussion_mode(stores):
    state, _, rooms = stores
    original = rooms.create(name="Team", agent_ids=["echo", "helper"], mode="discussion")
    anchor = completed_turn(state, original.thread_id, "Start", "Hello")
    branch = branch_before_prompt(original.thread_id, execution_id=anchor.id)
    copied = rooms.by_thread(branch.thread_id)
    assert copied.agent_ids == original.agent_ids and copied.mode == "discussion"
    assert not state.session_timeline(branch.thread_id)["turns"]


def test_branch_refuses_incomplete_prior_turn(stores):
    state, threads, _ = stores
    source = threads.create_thread()
    state.create_execution(thread_id=source.thread_id, query="Still running")
    with pytest.raises(ValueError, match="still running"):
        branch_before_prompt(source.thread_id, request_id="not-submitted")
    assert len(threads.list_threads()) == 1


def test_retry_unsent_request_keeps_saved_history(stores):
    state, threads, _ = stores
    source = threads.create_thread(title="Saved chat")
    completed_turn(state, source.thread_id, "Prior prompt", "Prior answer")
    branch = branch_before_prompt(source.thread_id, request_id="client-request-that-never-arrived")
    assert [m["text"] for t in state.session_timeline(branch.thread_id)["turns"] for m in t["messages"]] == ["Prior prompt", "Prior answer"]


def test_topic_title_is_semantic_and_manual_rename_wins(stores):
    _, threads, _ = stores
    prompt = "Hi, could you please research which herbs grow on a shady balcony?"
    source = threads.create_thread(title="New Session")
    threads.record_user_message(source.thread_id, prompt)
    closed = []
    class Client:
        def stream_turn(self, messages, **kwargs):
            assert prompt in messages[-1]["content"]
            return SimpleNamespace(content="Herbs for Shady Balconies")
        def close(self): closed.append(True)
    worker = update_in_background(source.thread_id, prompt, Client)
    worker.join(3)
    assert threads.get_thread(source.thread_id).title == "Herbs for Shady Balconies"
    assert closed
    source2 = threads.create_thread(title="New Session")
    threads.record_user_message(source2.thread_id, prompt)
    class RenameClient(Client):
        def stream_turn(self, *args, **kwargs):
            threads.update_thread(source2.thread_id, title="My garden")
            return SimpleNamespace(content="Auto title")
    worker = update_in_background(source2.thread_id, prompt, RenameClient)
    worker.join(3)
    assert threads.get_thread(source2.thread_id).title == "My garden"


def test_second_prompt_cannot_take_over_first_title(stores):
    _, threads, _ = stores
    source = threads.create_thread(title="New Session")
    threads.record_user_message(source.thread_id, "First subject")
    threads.record_user_message(source.thread_id, "A completely different topic")
    assert update_in_background(source.thread_id, "A completely different topic", lambda: pytest.fail("Shouldn't call a model")) is None


@pytest.mark.parametrize("provider,model,local,supported,toggle", [
    ("openai", "gpt-4o-mini", False, False, False),
    ("openai", "gpt-5.2", False, True, True),
    ("gemini", "gemini-3.5-flash", False, True, False),
    ("gemini", "gemini-2.5-flash", False, True, True),
    ("gemini", "gemini-3.5-flash-live", False, False, False),
    ("anthropic", "claude-sonnet", False, False, False),
    ("xai", "grok", False, False, False),
    ("lmstudio", "google/gemma-3-12b", True, False, False),
    ("lmstudio", "google/gemma-4-e4b", True, True, True),
    ("lmstudio", "qwen3-8b", True, True, True),
    ("lmstudio", "deepseek-r1", True, True, False),
])
def test_controls_reflect_model_and_actual_adapter(provider, model, local, supported, toggle):
    caps = thinking_controls(provider, model, local)
    assert caps["supported"] is supported and caps["toggle"] is toggle


def test_unsupported_local_models_get_no_reasoning_parameter():
    endpoint = Endpoint("http://localhost/v1", "", "gemma-3-12b", "lmstudio", True)
    assert reasoning_effort_for(endpoint, True, "high") == ""
    assert reasoning_effort_for(Endpoint("", "", "gpt-5.2", "openai", False), False, "medium") == "none"


def test_local_thinking_override_matches_controls_and_request(monkeypatch):
    from config import config
    monkeypatch.setattr(config, "model_capability_profiles", {"lmstudio:custom-reasoner": {"thinking_supported": True, "thinking_toggle_supported": False}})
    endpoint = Endpoint("http://localhost/v1", "", "custom-reasoner", "lmstudio", True)
    assert reasoning_effort_for(endpoint, True, "high") == "high"
    assert reasoning_effort_for(endpoint, False, "high") == "low"
    assert reasoning_effort_for(Endpoint("", "", "deepseek-r1", "lmstudio", True), False, "medium") == "low"
