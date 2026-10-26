"""Real Git + owned local protocol processes. No real agent/account/model calls."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from agent import code_sessions as cs, code_runtimes as adapters, code_worktree as wt, projects
from agent.code_process import RpcProcess
from config import config

FIXTURE = Path(__file__).parent / "fixtures" / "code_rpc_agent.py"


def wait_for(service, session_id, predicate, seconds=12):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = service.call(service.poll(session_id, 0))
        if predicate(value):
            return value
        time.sleep(.05)
    pytest.fail("Protocol fixture timed out: " + json.dumps(value))


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root = tmp_path / "source"
    root.mkdir()
    wt.git(root, "init")
    wt.git(root, "config", "user.name", "Fixture")
    wt.git(root, "config", "user.email", "fixture@example.invalid")
    (root / "hello.txt").write_text("original", encoding="utf-8")
    wt.git(root, "add", "hello.txt")
    wt.git(root, "commit", "-m", "Fixture baseline")
    manager = projects.ProjectManager(tmp_path / "projects")
    item = manager.create_project("Fixture", workspace_root=str(root), trust_state="trusted")
    monkeypatch.setattr(projects, "_project_manager", manager)
    monkeypatch.setattr(config, "privacy_mode", "standard")
    from agent.lean import stop
    monkeypatch.setattr(stop, "paused", lambda: False)
    service = cs.Sessions(tmp_path / "sessions")
    monkeypatch.setattr(adapters.shutil, "which", lambda _: sys.executable)
    yield service, item, root
    service.call(service.shutdown())
    service.loop.call_soon_threadsafe(service.loop.stop)
    service.thread.join(timeout=5)


def fake(monkeypatch, mode):
    monkeypatch.setattr(adapters, "RpcProcess", lambda argv, cwd, env, handler: RpcProcess(
        [sys.executable, str(FIXTURE), mode], cwd, env, handler))


def create(service, item, runtime="codex", key="one", **kwargs):
    return service.call(service.create(item.id, runtime, key, **kwargs))


def test_creation_retry_isolation_and_dirty_source(setup):
    service, item, root = setup
    (root / "hello.txt").write_text("owner's newer edit")
    with pytest.raises(ValueError, match="local changes"):
        create(service, item)
    first = create(service, item, acknowledge_dirty=True)
    assert create(service, item, acknowledge_dirty=True)["id"] == first["id"]
    second = create(service, item, key="two", acknowledge_dirty=True)
    assert first["worktree"] != second["worktree"]
    assert Path(first["worktree"]).joinpath("hello.txt").read_text() == "original"
    assert (root / "hello.txt").read_text() == "owner's newer edit"
    assert "creation_context" not in first


@pytest.mark.parametrize("runtime,mode", [("codex", "codex"), ("acp", "acp")])
def test_stream_approval_and_rollback(setup, monkeypatch, runtime, mode):
    service, item, root = setup
    fake(monkeypatch, mode)
    row = create(service, item, runtime, command=[sys.executable, str(FIXTURE), mode] if runtime == "acp" else None)
    sid = row["id"]
    service.call(service.connect(sid))
    service.call(service.prompt(sid, "Fixture task"))
    waiting = wait_for(service, sid, lambda row: bool(row["approvals"]))
    with pytest.raises(ValueError, match="idle"):
        service.call(service.prompt(sid, "second task"))
    assert Path(row["worktree"]).joinpath("hello.txt").read_text() == "original"
    service.call(service.decide(sid, waiting["approvals"][0]["id"], True))
    done = wait_for(service, sid, lambda row: row["state"] == "completed" and row["rollback_available"])
    assert any("reply" in json.dumps(event) for event in done["events"])
    assert done["model"] == ("fixture-model" if runtime == "codex" else "fixture-acp-model")
    assert Path(row["worktree"]).joinpath("hello.txt").read_text() == "agent edit"
    assert (root / "hello.txt").read_text() == "original"
    assert "agent edit" in service.call(service.inspect(sid))["diff"]
    with pytest.raises(ValueError, match="Stop"):
        service.call(service.rollback(sid))
    service.call(service.stop(sid))
    service.call(service.rollback(sid))
    assert Path(row["worktree"]).joinpath("hello.txt").read_text() == "original"
    assert list(Path(row["worktree"]).parent.glob("rollback-backup-*.json"))
    with pytest.raises(ValueError, match="history"):
        service.call(service.connect(sid))


def test_claude_reported_model_is_retained_and_redacted(setup):
    service, item, _ = setup
    row = create(service, item, runtime="claude_code")
    session = service.sessions[row["id"]]
    adapter = adapters.Claude(session)
    service.call(adapter.event("sdk/event", {"kind": "SystemMessage", "data": {"data": {"model": "fixture-claude-model"}}}))
    assert session.public()["model"] == "fixture-claude-model"
    session.api_key = "fixture-secret-model-key-12345"
    service.call(adapter.event("sdk/event", {"kind": "AssistantMessage", "data": {"model": session.api_key}}))
    assert session.api_key not in (session.directory / "session.json").read_text()


def test_acp_second_turn_retains_reported_model_without_new_session(setup, monkeypatch):
    service, item, _ = setup
    fake(monkeypatch, "acp")
    row = create(service, item, "acp", command=[sys.executable, str(FIXTURE), "acp"])
    service.call(service.connect(row["id"]))
    for text in ["first", "second"]:
        service.call(service.prompt(row["id"], text))
        waiting = wait_for(service, row["id"], lambda value: bool(value["approvals"]))
        service.call(service.decide(row["id"], waiting["approvals"][0]["id"], False))
        done = wait_for(service, row["id"], lambda value: value["state"] == "completed" and value["rollback_available"])
        assert done["model"] == "fixture-acp-model"
        assert done["models"][0]["model"] == "fixture-acp-model"


def test_rollback_preserves_newer_edits_and_index(setup):
    service, item, root = setup
    row = create(service, item)
    tree = Path(row["worktree"])
    before = wt.snapshot(tree)
    (tree / "hello.txt").write_text("agent")
    wt.git(tree, "add", "hello.txt")
    after = wt.snapshot(tree)
    checkpoint = tree.parent / "checkpoint.json"
    wt.atomic_json(checkpoint, {"before": before, "after": wt.fingerprint(after)})
    (tree / "hello.txt").write_text("owner")
    with pytest.raises(ValueError, match="newer edits"):
        wt.rollback(tree, checkpoint)
    (tree / "hello.txt").write_text("agent")
    wt.rollback(tree, checkpoint)
    assert not wt.git(tree, "status", "--porcelain")


@pytest.mark.parametrize("mode", ["private", "offline"])
def test_privacy_stops_approval_and_blocks_launch(setup, monkeypatch, mode):
    service, item, _ = setup
    fake(monkeypatch, "codex")
    sid = create(service, item)["id"]
    service.call(service.connect(sid))
    service.call(service.prompt(sid, "Wait at approval"))
    wait_for(service, sid, lambda row: bool(row["approvals"]))
    monkeypatch.setattr(cs, "_instance", service)
    with cs.privacy_transition():
        assert not service.sessions[sid].adapter
        monkeypatch.setattr(config, "privacy_mode", mode)
    with pytest.raises(PermissionError, match="Private/Offline"):
        service.call(service.connect(sid))
    assert not service.call(service.poll(sid, 0))["approvals"]


def test_child_process_job_cleanup(setup, monkeypatch):
    service, item, _ = setup
    fake(monkeypatch, "child")
    sid = create(service, item)["id"]
    service.call(service.connect(sid))
    service.call(service.prompt(sid, "Spawn fixture child"))
    tree = service.sessions[sid].root
    deadline = time.monotonic() + 10
    while not (tree / "child.pid").exists() and time.monotonic() < deadline:
        time.sleep(.05)
    pid = int((tree / "child.pid").read_text())
    service.call(service.stop(sid))
    if os.name == "nt":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            ctypes.windll.kernel32.CloseHandle(handle)
            assert code.value != 259  # STILL_ACTIVE


def test_unknown_requests_and_acp_capabilities_fail_closed(setup):
    service, item, _ = setup
    session = service.sessions[create(service, item)["id"]]
    codex = adapters.Codex(session)
    with pytest.raises(ValueError):
        service.call(codex.event("item/tool/call", {"name": "arbitrary"}, True))
    acp = adapters.Acp(session)
    with pytest.raises(ValueError):
        service.call(acp.event("fs/write_text_file", {"sessionId": "foreign"}, True))


def test_project_rebinding_and_writer_lease(setup):
    service, item, root = setup
    sid = create(service, item)["id"]
    session = service.sessions[sid]
    session.acquire()
    contender = cs.Session(service, dict(session.row))
    with pytest.raises(ValueError, match="Another"):
        contender.acquire()
    item.trust_state = "untrusted"
    with pytest.raises(PermissionError, match="trust"):
        service.call(service.poll(sid, 0))


def test_runtime_crash_is_failed_without_replay(setup, monkeypatch):
    service, item, _ = setup
    fake(monkeypatch, "crash")
    sid = create(service, item)["id"]
    service.call(service.connect(sid))
    service.call(service.prompt(sid, "Fixture crash"))
    value = wait_for(service, sid, lambda row: row["state"] == "failed")
    assert not value["connected"]
    assert not value["approvals"]


def test_deny_does_not_change_files_and_pending_decisions_expire(setup, monkeypatch):
    service, item, _ = setup
    fake(monkeypatch, "codex")
    sid = create(service, item)["id"]
    service.call(service.connect(sid))
    service.call(service.prompt(sid, "Fixture denial"))
    waiting = wait_for(service, sid, lambda row: bool(row["approvals"]))
    rid = waiting["approvals"][0]["id"]
    service.call(service.decide(sid, rid, False))
    wait_for(service, sid, lambda row: row["state"] == "completed")
    assert (service.sessions[sid].root / "hello.txt").read_text() == "original"
    with pytest.raises(ValueError, match="expired"):
        service.call(service.decide(sid, rid, True))


def test_credentials_are_not_persisted_or_replayed(setup):
    service, item, _ = setup
    sid = create(service, item, runtime="claude_code")["id"]
    session = service.sessions[sid]
    session.api_key = "fixture-secret-never-expose"
    session.emit("sdk/event", {"text": session.api_key})
    assert session.api_key not in json.dumps(session.public())
    assert session.api_key not in (session.directory / "session.json").read_text()


def test_restart_keeps_native_identity_but_never_starts_runtime(setup):
    service, item, _ = setup
    sid = create(service, item)["id"]
    session = service.sessions[sid]
    session.row.update(native_id="fixture-native", state="running")
    session.save()
    async def reload():
        service.sessions.clear()
        await service._load()
    service.call(reload())
    restored = service.call(service.poll(sid, 0))
    assert restored["state"] == "stopped" and restored["native_id"] == "fixture-native"
    assert not restored["connected"]


def test_provisioning_never_runs_checkout_filter_or_hook(setup):
    service, item, root = setup
    # A required, failing smudge filter would make normal checkout fail. Raw blob
    # provisioning must preserve the committed data without invoking it.
    (root / ".gitattributes").write_text("hello.txt filter=fixture\n")
    wt.git(root, "add", ".gitattributes")
    wt.git(root, "commit", "-m", "Fixture filter")
    wt.git(root, "config", "filter.fixture.smudge", "this-command-must-never-execute")
    wt.git(root, "config", "filter.fixture.required", "true")
    row = create(service, item)
    assert (Path(row["worktree"]) / "hello.txt").read_text() == "original"


def test_failed_privacy_save_keeps_admission_closed(setup, monkeypatch):
    service, item, _ = setup
    monkeypatch.setattr(cs, "_instance", service)
    monkeypatch.setattr(cs, "_privacy_failed", False)
    with pytest.raises(OSError):
        with cs.privacy_transition():
            raise OSError("Fixture settings write failed")
    with pytest.raises(PermissionError):
        create(service, item)
    with cs.privacy_transition():
        pass
    assert create(service, item)["state"] == "stopped"


def test_official_claude_sdk_options_and_every_tool_gate(monkeypatch):
    sdk = pytest.importorskip("claude_agent_sdk")
    from agent.code_claude_worker import Worker
    captured = {}
    class FakeClient:
        def __init__(self, options):
            captured["options"] = options
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def query(self, text): pass
        async def receive_response(self):
            from claude_agent_sdk import ResultMessage
            yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                                num_turns=1, session_id="fixture-claude", total_cost_usd=0, usage={}, result="fixture")
    monkeypatch.setattr(sdk, "ClaudeSDKClient", FakeClient)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fixture-sdk-key")
    worker = Worker()
    monkeypatch.setattr(worker, "send", lambda value: None)
    async def approve(tool, inputs): return tool == "Read"
    monkeypatch.setattr(worker, "approval", approve)
    async def check():
        result = await worker.prompt({"text": "Fixture", "resume": "native-claude"})
        options = captured["options"]
        assert options.resume == "native-claude" and options.permission_mode == "default"
        assert options.setting_sources == [] and options.strict_mcp_config
        assert not options.allowed_tools and options.mcp_servers == {}
        gate = options.hooks["PreToolUse"][0].hooks[0]
        allowed = await gate({"tool_name": "Read", "tool_input": {}}, "one", {})
        denied = await gate({"tool_name": "Bash", "tool_input": {}}, "two", {})
        assert allowed["hookSpecificOutput"]["permissionDecision"] == "allow"
        assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert result["session_id"] == "fixture-claude" and not result["is_error"]
    asyncio.run(check())


def test_external_api_requires_acknowledgement_and_never_creates_native_thread(setup, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.routes.echo_code import router
    service, item, _ = setup
    monkeypatch.setattr(cs, "_instance", service)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    url = f"/echo-code/projects/{item.id}/external-sessions"
    body = {"runtime_id": "codex", "idempotency_key": "fixture-request-1"}
    assert client.post(url, json=body).status_code == 400
    assert not service.sessions
    body["acknowledge_execution"] = True
    response = client.post(url, json=body)
    assert response.status_code == 200
    assert "thread_id" not in response.json()
    assert client.post(url, json=body).json()["id"] == response.json()["id"]
    monkeypatch.setattr(config, "privacy_mode", "private")
    body["idempotency_key"] = "fixture-request-2"
    assert client.post(url, json=body).status_code == 403


def test_codex_foreign_thread_or_turn_cannot_approve_or_complete(setup):
    service, item, _ = setup
    session = service.sessions[create(service, item)["id"]]
    session.row["native_id"] = "owned-thread"
    session.turn_id = "owned-turn"
    adapter = adapters.Codex(session)
    decision = service.call(adapter.event("item/commandExecution/requestApproval", {
        "threadId": "other-thread", "turnId": "owned-turn"}, True))
    assert decision == {"decision": "decline"}
    service.call(adapter.event("turn/completed", {"threadId": "owned-thread", "turn": {
        "id": "other-turn", "status": "completed"}}))
    assert not session.turn_finished.is_set()


def test_inherited_git_environment_cannot_redirect_workspace(setup, monkeypatch):
    service, item, root = setup
    row = create(service, item)
    monkeypatch.setenv("GIT_DIR", str(root / "not-the-repository"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(root / "owner-index-do-not-touch"))
    tree = Path(row["worktree"])
    assert wt.git(tree, "rev-parse", "HEAD").decode().strip() == row["base_head"]
    assert wt.snapshot(tree)["files"]["hello.txt"]
    assert not (root / "owner-index-do-not-touch").exists()


def test_withdrawn_trust_stops_live_runtime_and_stop_remains_available(setup, monkeypatch):
    service, item, _ = setup
    fake(monkeypatch, "codex")
    sid = create(service, item)["id"]
    service.call(service.connect(sid))
    item.trust_state = "untrusted"
    with pytest.raises(PermissionError):
        service.call(service.poll(sid, 0))
    assert not service.sessions[sid].adapter
    assert service.call(service.stop(sid))["state"] == "stopped"


@pytest.mark.parametrize("name", [".GIT", "sub/.git/config", "git~1", "CON.txt", "file.", "file ", "a\\b", "../escape", "a:stream"])
def test_raw_blob_paths_cannot_replace_git_binding_or_use_windows_aliases(tmp_path, name):
    with pytest.raises(ValueError):
        wt.path(tmp_path, name)
