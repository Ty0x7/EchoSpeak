"""Deterministic Echo Code boundaries. No real model, CLI, account, or user data."""
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from agent import echo_code, projects, state, threads
from api.routes.echo_code import router
from config import config


@pytest.fixture
def env(tmp_path, monkeypatch):
    manager = projects.ProjectManager(tmp_path / "projects")
    monkeypatch.setattr(projects, "_project_manager", manager)
    monkeypatch.setattr(projects.ProjectManager, "read_git_metadata", staticmethod(lambda root: {}))
    root = tmp_path / "workspace"
    root.mkdir()
    item = manager.create_project("Compiler", workspace_root=str(root), trust_state="trusted")
    tm = threads.ThreadManager(tmp_path / "threads.json")
    store = state.StateStore(tmp_path / "state")
    monkeypatch.setattr(threads, "get_thread_manager", lambda: tm)
    monkeypatch.setattr(state, "get_state_store", lambda: store)
    monkeypatch.setattr(config, "privacy_mode", "standard")
    monkeypatch.setattr(echo_code.shutil, "which", lambda command: f"/installed/{command}")
    from agent.lean import git_context
    monkeypatch.setattr(git_context, "summary", lambda root: "git: branch main · clean")
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), item, manager, root, tm, store


def test_catalog_is_passive_and_never_claims_authentication(env, monkeypatch):
    client, *_ = env
    def no_process(*args, **kwargs):
        pytest.fail("Catalog must not launch any subprocess")
    monkeypatch.setattr(subprocess, "run", no_process)
    monkeypatch.setattr(subprocess, "Popen", no_process)
    data = client.get("/echo-code/runtimes").json()
    assert data["schema_version"] == 1
    assert data["runtimes"][0]["id"] == "echo"
    for row in data["runtimes"][1:]:
        assert row["authentication"] == "unverified"
        assert not row["can_start"] and not row["implemented"]
    assert next(row for row in data["runtimes"] if row["id"] == "codex")["installed"] is True
    assert "installed/" not in json.dumps(data)


@pytest.mark.parametrize("mode", ["private", "offline"])
def test_private_and_offline_fail_closed_for_external_processes(env, monkeypatch, mode):
    client, *_ = env
    monkeypatch.setattr(config, "privacy_mode", mode)
    rows = client.get("/echo-code/runtimes").json()["runtimes"]
    assert rows[0]["can_start"]
    assert all(not row["can_start"] and any("Private/Offline" in reason for reason in row["blockers"]) for row in rows[1:])


def test_missing_cli_is_not_an_auth_failure(env, monkeypatch):
    client, *_ = env
    monkeypatch.setattr(echo_code.shutil, "which", lambda _: None)
    codex = client.get("/echo-code/runtimes").json()["runtimes"][1]
    assert codex["installed"] is False and codex["authentication"] == "unverified"


def start(client, item, runtime="echo", key="creation-key-1"):
    return client.post(f"/echo-code/projects/{item.id}/sessions", json={"runtime_id": runtime, "idempotency_key": key})


def test_native_session_uses_existing_stores_and_survives_reload(env, monkeypatch):
    client, item, _, root, tm, store = env
    # Creation must not initialize Echo's heavy model/memory stack.
    import api.deps
    monkeypatch.setattr(api.deps, "get_agent", lambda *_: pytest.fail("No agent initialization during session creation"))
    before = config.local.provider
    response = start(client, item)
    assert response.status_code == 200
    row = response.json()
    assert (row["surface"], row["agent_runtime"], row["source"]) == ("code", "echo", "web")
    scope = store.get_thread_state(row["thread_id"])
    assert scope.active_project_id == item.id
    assert scope.workspace_root == scope.project_path == str(root.resolve())
    assert config.local.provider == before
    restored = threads.ThreadManager(tm._path).get_thread(row["thread_id"])
    assert restored.surface == "code" and restored.agent_runtime == "echo"
    overview = client.get(f"/echo-code/projects/{item.id}").json()
    assert overview["sessions"][0]["thread_id"] == row["thread_id"]
    assert overview["git_summary"] == "git: branch main · clean"


def test_transport_retries_and_concurrent_creation_return_one_session(env):
    _, item, _, _, tm, _ = env
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda _: echo_code.create_session(item.id, runtime_id="echo", idempotency_key="one-request"), range(8)))
    assert len({row["thread_id"] for row in rows}) == len(tm.list_threads()) == 1


@pytest.mark.parametrize("runtime", ["codex", "claude_code", "acp"])
def test_unimplemented_runtime_never_falls_back_to_native(env, runtime):
    client, item, _, _, tm, store = env
    response = start(client, item, runtime)
    assert response.status_code == 409 and "not implemented" in response.json()["detail"]
    assert not tm.list_threads() and not store.list_thread_states()


def test_unknown_runtime_is_rejected_before_mutation(env):
    client, item, _, _, tm, _ = env
    assert start(client, item, "arbitrary-command").status_code == 422
    assert not tm.list_threads()


@pytest.mark.parametrize("trust", ["untrusted", "unknown"])
def test_session_requires_explicit_project_trust(env, trust):
    client, item, _, _, tm, _ = env
    item.trust_state = trust
    assert start(client, item).status_code == 403
    assert not tm.list_threads()
    data = client.get(f"/echo-code/projects/{item.id}").json()
    assert not data["can_start_native"]


def test_missing_archived_or_detached_project_cannot_create_sessions(env):
    client, item, _, root, tm, _ = env
    assert client.post("/echo-code/projects/missing/sessions", json={"runtime_id": "echo", "idempotency_key": "missing-project"}).status_code == 404
    item.archived = True
    assert start(client, item).status_code == 404
    item.archived = False
    item.workspace_root = ""
    assert start(client, item).status_code == 400
    item.workspace_root = str(root / "missing")
    assert start(client, item).status_code == 400
    assert not tm.list_threads()


def test_idempotency_conflict_does_not_rebind_a_session(env):
    client, item, manager, root, tm, store = env
    first = start(client, item).json()
    other = manager.create_project("Other", workspace_root=str(root), trust_state="trusted")
    assert start(client, other).status_code == 409
    assert len(tm.list_threads()) == 1
    assert store.get_thread_state(first["thread_id"]).active_project_id == item.id
    # A later explicit change must also survive a transport replay.
    store.update_thread_state(first["thread_id"], active_project_id=other.id)
    assert start(client, item).status_code == 409
    assert store.get_thread_state(first["thread_id"]).active_project_id == other.id


def test_workspace_listing_excludes_chat_other_projects_and_archived_sessions(env):
    client, item, _, _, tm, store = env
    code = start(client, item).json()
    chat = tm.create_thread("Ordinary chat")
    store.update_thread_state(chat.thread_id, active_project_id=item.id)
    other = tm.create_thread("Other Code", surface="code")
    store.update_thread_state(other.thread_id, active_project_id="another-project")
    assert [row["thread_id"] for row in client.get(f"/echo-code/projects/{item.id}").json()["sessions"]] == [code["thread_id"]]
    tm.update_thread(code["thread_id"], archived=True)
    assert not client.get(f"/echo-code/projects/{item.id}").json()["sessions"]


def test_creation_replay_does_not_undo_an_explicit_project_detach(env):
    client, item, _, _, tm, store = env
    row = start(client, item).json()
    store.update_thread_state(row["thread_id"], active_project_id="", workspace_root="", project_path="")
    response = start(client, item)
    assert response.status_code == 409 and "detached" in response.json()["detail"]
    assert store.get_thread_state(row["thread_id"]).active_project_id == ""
    assert len(tm.list_threads()) == 1


def test_old_thread_payloads_and_legacy_creation_keys_stay_compatible(env):
    _, _, _, _, tm, _ = env
    legacy = threads.ThreadInfo.from_dict({"thread_id": "old", "title": "Before Code"})
    assert legacy.surface == "chat" and legacy.agent_runtime == "echo"
    a = tm.create_thread("Legacy", idempotency_key="legacy-request")
    b = tm.create_thread("Legacy", idempotency_key="legacy-request")
    assert a.thread_id == b.thread_id
    with pytest.raises(ValueError, match="different surface"):
        tm.create_thread("Legacy", idempotency_key="legacy-request", surface="code")
    with pytest.raises(ValueError, match="native Echo"):
        tm.create_thread("External", surface="code", agent_runtime="codex")


def test_revising_a_native_coding_prompt_keeps_its_surface(env):
    _, item, _, _, tm, store = env
    from agent.chat_branches import branch_before_prompt
    source = echo_code.create_session(item.id, runtime_id="echo", idempotency_key="code-to-branch")
    execution = store.create_execution(thread_id=source["thread_id"], query="Original prompt", active_project_id=item.id, record_user_message=False)
    store.update_execution(execution.id, status="completed", success=True)
    target = branch_before_prompt(source["thread_id"], execution_id=execution.id)
    assert target.surface == "code" and target.agent_runtime == "echo"
    assert store.get_thread_state(target.thread_id).active_project_id == item.id


def test_new_routes_inherit_api_auth_and_foreign_origin_guards(env, monkeypatch):
    from api.server import app
    monkeypatch.setattr(config, "api_auth_enabled", True)
    monkeypatch.setattr(config, "api_auth_localhost_bypass", False)
    monkeypatch.setattr(config, "api_auth_key", "test-echo-code-key")
    client = TestClient(app)
    assert client.get("/echo-code/runtimes").status_code == 401
    assert client.get("/echo-code/runtimes", headers={"x-echospeak-key": "test-echo-code-key"}).status_code == 200
    assert client.post("/echo-code/projects/any/sessions", headers={"x-echospeak-key": "test-echo-code-key", "origin": "https://untrusted.example"}, json={"runtime_id": "echo", "idempotency_key": "foreign-origin"}).status_code == 403
