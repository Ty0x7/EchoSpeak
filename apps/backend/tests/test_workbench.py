"""Workbench integration in temporary repositories; no account or network use."""
import asyncio
import json
import os
from pathlib import Path
import time

import pytest

from agent import workbench as wb
from tests.test_code_sessions import setup
_REAL_WHICH = wb.shutil.which


@pytest.fixture(autouse=True)
def real_workspace_tools(setup, monkeypatch):
    # The Codex fixture replaces CLI discovery; these tests need real Git.
    monkeypatch.setattr(wb.shutil, "which", _REAL_WHICH)


def service(setup):
    supervisor, item, root = setup
    workbench = wb.Workbench(supervisor)
    return supervisor, item, root, workbench, workbench.context(item.id)


def test_summary_tree_file_and_private_paths(setup):
    owner, item, root, workbench, context = service(setup)
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("print('fixture')")
    (root / ".env").write_text("PRIVATE_FIXTURE=do-not-preview")
    (root / "untracked.txt").write_text("test")
    summary = owner.call(workbench.summary(context))
    assert summary["git"]["is_git"] and summary["git"]["head"]
    assert any(row["path"] == "untracked.txt" for row in summary["git"]["changes"])
    assert all(row["path"] != ".env" for row in summary["git"]["changes"])
    tree = owner.call(workbench.files(context))
    assert any(row["path"] == "src" and row["directory"] for row in tree["items"])
    assert all(not row["name"].startswith(".") for row in tree["items"])
    assert "fixture" in owner.call(workbench.file(context, "src/app.py"))["text"]
    for path in ["../outside", ".git/config", ".env"]:
        with pytest.raises((ValueError, OSError)):
            owner.call(workbench.file(context, path))


def test_commit_only_reviewed_files_and_reject_changed_review(setup):
    owner, item, root, workbench, context = service(setup)
    (root / "hello.txt").write_text("selected change")
    (root / "other.txt").write_text("leave uncommitted")
    owner.call(workbench.read_git(context, "add", "--", "other.txt"))
    review = owner.call(workbench.review(context, "commit", ["hello.txt"], "Fixture commit", ""))
    (root / "hello.txt").write_text("newer edit")
    with pytest.raises(ValueError, match="moved"):
        owner.call(workbench.apply(context, review["review_id"]))
    review = owner.call(workbench.review(context, "commit", ["hello.txt"], "Fixture commit", ""))
    result = owner.call(workbench.apply(context, review["review_id"]))
    assert result["summary"]["git"]["head"] != review["head"]
    assert owner.call(workbench.read_git(context, "show", "HEAD:hello.txt")) == "newer edit"
    assert any(row["path"] == "other.txt" for row in result["summary"]["git"]["changes"])
    assert "other.txt" in owner.call(workbench.read_git(context, "diff", "--cached", "--name-only"))
    with pytest.raises(ValueError, match="expired"):
        owner.call(workbench.apply(context, review["review_id"]))


def test_untracked_commit_and_branch_review(setup):
    owner, item, root, workbench, context = service(setup)
    (root / "new.txt").write_text("new file")
    review = owner.call(workbench.review(context, "commit", ["new.txt"], "Add fixture file", ""))
    owner.call(workbench.apply(context, review["review_id"]))
    assert owner.call(workbench.read_git(context, "show", "HEAD:new.txt")) == "new file"
    review = owner.call(workbench.review(context, "create_branch", [], "", "codex/fixture"))
    assert owner.call(workbench.apply(context, review["review_id"]))["summary"]["git"]["branch"] == "codex/fixture"


@pytest.mark.skipif(os.name != "nt", reason="Real Windows ConPTY test")
def test_real_conpty_input_resize_multiple_terminals_and_stop(setup):
    owner, item, root, workbench, context = service(setup)
    first = owner.call(workbench.terminal_start(context, 24, 90))
    second = owner.call(workbench.terminal_start(context, 24, 90))
    owner.call(workbench.terminal(context, first["id"], "write", text="Write-Output ('WB-' + 'PTY-OK')\r"))
    owner.call(workbench.terminal(context, first["id"], "resize", rows=30, cols=100))
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        snapshot = owner.call(workbench.terminal(context, first["id"], "poll"))
        if "WB-PTY-OK" in "".join(row["text"] for row in snapshot["output"]):
            break
        time.sleep(.05)
    else:
        pytest.fail("ConPTY did not return the fixture command output")
    owner.call(owner.stop_all("Fixture stop"))
    import psutil
    for pid in (first["pid"], second["pid"]):
        try:
            psutil.Process(pid).wait(timeout=5)
        except psutil.NoSuchProcess:
            pass
    assert owner.call(workbench.terminal(context, first["id"], "poll"))["state"] == "stopped"
    assert owner.call(workbench.terminal(context, second["id"], "poll"))["state"] == "stopped"
    assert not workbench.groups and not owner.owned_tools


def test_agent_worktree_identity_and_terminal_writer_conflict(setup):
    owner, item, root, workbench, source = service(setup)
    row = owner.call(owner.create(item.id, "codex", "fixture-workbench", workspace_mode="worktree"))
    context = workbench.context(item.id, row["id"])
    assert context["root"] != source["root"]
    job = wb.OwnedJob(workbench, context)
    job.is_terminal = True
    async def check():
        workbench.acquire(job)
        with pytest.raises(ValueError, match="terminals"):
            await owner.connect(row["id"])
        await job.stop()
    owner.call(check())
    assert not owner.tool_loans


def test_scripts_save_does_not_execute_and_usage_is_not_fabricated(setup):
    owner, item, root, workbench, context = service(setup)
    rows = owner.call(workbench.script_save(context, "Dev", "npm run dev"))
    assert rows[0]["command"] == "npm run dev"
    assert not owner.owned_tools
    assert not owner.call(workbench.usage(context))["runtimes"]


@pytest.mark.parametrize("mode", ["private", "offline"])
def test_private_offline_block_execution_but_allow_safe_inspection(setup, monkeypatch, mode):
    from config import config
    owner, item, root, workbench, context = service(setup)
    monkeypatch.setattr(config, "privacy_mode", mode)
    assert owner.call(workbench.files(context))["items"]
    with pytest.raises(PermissionError):
        owner.call(workbench.terminal_start(context, 24, 80))
    with pytest.raises(PermissionError):
        owner.call(workbench.review(context, "push", [], "", ""))


def test_review_belongs_to_exact_context_and_push_remote_is_constrained(setup):
    owner, item, root, workbench, context = service(setup)
    for remote, expected in [("git@github.com:owner/repo.git", "owner/repo"), ("https://github.com/owner/repo.git", "owner/repo"),
                             ("https://user:secret@github.com/owner/repo", ""), ("https://other.test/owner/repo", "")]:
        assert workbench.github_repo(remote) == expected


def test_agent_activity_and_session_credentials_stay_in_their_scope(setup):
    owner, item, root, workbench, source = service(setup)
    row = owner.call(owner.create(item.id, "codex", "fixture-workbench-activity", workspace_mode="worktree"))
    context = workbench.context(item.id, row["id"])
    session = owner.sessions[row["id"]]
    session.row["native_id"] = "owned-native"
    session.redaction_values = ["fixture-runtime-secret-123456789"]
    session.emit("item/started", {"threadId": "owned-native", "item": {"id": "search-1", "type": "webSearch", "query": "fixture"}})
    session.emit("item/completed", {"threadId": "foreign-native", "item": {"id": "foreign", "type": "commandExecution"}})
    result = owner.call(workbench.activity(context))
    assert len(result["items"]) == 1 and result["items"][0]["research"]
    file = Path(context["root"]) / "hello.txt"
    file.write_text(session.redaction_values[0])
    owner.workbench = workbench
    preview = owner.call(wb.dispatch(owner, "file", item.id, row["id"], path="hello.txt"))
    assert session.redaction_values[0] not in preview["text"]
    with pytest.raises(PermissionError, match="credential"):
        owner.call(workbench.review(context, "commit", ["hello.txt"], "Fixture secret", ""))


@pytest.mark.skipif(os.name != "nt", reason="Real Windows ConPTY test")
def test_worker_cannot_import_project_agent_package(setup):
    owner, item, root, workbench, context = service(setup)
    package = root / "agent"
    package.mkdir()
    (package / "__init__.py").write_text("from pathlib import Path\nPath('wrong-module-imported').write_text('unsafe')\n")
    terminal = owner.call(workbench.terminal_start(context, 24, 80))
    assert not (root / "wrong-module-imported").exists()
    owner.call(workbench.terminal(context, terminal["id"], "stop"))


@pytest.mark.parametrize("origin,key", [("https://untrusted.invalid", "fixture-key"), ("http://localhost:5174", "wrong-key")])
def test_terminal_socket_rejects_untrusted_origin_and_key(setup, monkeypatch, origin, key):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    from api.routes.workbench import router
    from config import config
    monkeypatch.setattr(config, "api_auth_enabled", True)
    monkeypatch.setattr(config, "api_auth_localhost_bypass", False)
    monkeypatch.setattr(config, "api_auth_key", "fixture-key")
    app = FastAPI(); app.include_router(router)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        with pytest.raises(WebSocketDisconnect) as error:
            with client.websocket_connect("/workbench/fixture/terminals/fixture/socket", headers={"origin": origin},
                                          subprotocols=["echospeak", "echospeak-auth-" + key]):
                pass
    assert error.value.code == 1008


@pytest.mark.skipif(os.name != "nt", reason="Real Windows ConPTY socket test")
def test_terminal_socket_streams_scoped_io_and_disconnect_keeps_process(setup, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.routes.workbench import router
    from agent import code_sessions as cs
    from config import config
    owner, item, root, workbench, context = service(setup)
    owner.workbench = workbench
    monkeypatch.setattr(cs, "_instance", owner)
    monkeypatch.setattr(config, "api_auth_enabled", True)
    monkeypatch.setattr(config, "api_auth_localhost_bypass", False)
    monkeypatch.setattr(config, "api_auth_key", "fixture-key")
    terminal = owner.call(workbench.terminal_start(context, 24, 80))
    app = FastAPI(); app.include_router(router)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        with client.websocket_connect(f"/workbench/{item.id}/terminals/{terminal['id']}/socket",
                                      headers={"origin": "http://localhost:5174"},
                                      subprotocols=["echospeak", "echospeak-auth-fixture-key"]) as socket:
            assert socket.accepted_subprotocol == "echospeak"
            socket.send_json({"operation": "resize", "rows": 28, "cols": 92})
            # Caller-supplied project fields cannot retarget the route's owned terminal.
            socket.send_json({"operation": "write", "text": "Write-Output 'SOCKET-PROOF'\r", "project_id": "other"})
            output = ""
            for _ in range(30):
                result = socket.receive_json()
                assert result["project_id"] == item.id
                output += "".join(row["text"] for row in result["output"])
                if "SOCKET-PROOF" in output:
                    break
            assert "SOCKET-PROOF" in output
        assert owner.call(workbench.terminal(context, terminal["id"], "poll"))["state"] == "running"
    owner.call(workbench.terminal(context, terminal["id"], "stop"))
