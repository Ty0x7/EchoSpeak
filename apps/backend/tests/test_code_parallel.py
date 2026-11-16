"""Inbox, MCP and observability regressions using real stores and local peers only."""
import asyncio
import base64
import io
import json
import os
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from agent import code_control as control, code_drafts as drafts, code_inbox, projects, state, threads
from agent.lean import rooms
from api.routes.echo_code import router
from config import config


@pytest.fixture
def env(tmp_path, monkeypatch):
    pm = projects.ProjectManager(tmp_path / "projects")
    root = tmp_path / "repo"
    root.mkdir()
    p = pm.create_project("Fixture", workspace_root=str(root), trust_state="trusted")
    other_root = tmp_path / "other"
    other_root.mkdir()
    other = pm.create_project("Other", workspace_root=str(other_root), trust_state="trusted")
    tm, store = threads.ThreadManager(tmp_path / "threads.json"), state.StateStore(tmp_path / "state")
    monkeypatch.setattr(projects, "_project_manager", pm)
    monkeypatch.setattr(threads, "get_thread_manager", lambda: tm)
    monkeypatch.setattr(state, "_state_store", store)
    monkeypatch.setattr(rooms, "get_room_store", lambda: room_store)
    room_store = rooms.RoomStore(tmp_path / "rooms.json")
    grants = control.Grants(tmp_path / "clients.json")
    monkeypatch.setattr(control, "grants", lambda: grants)
    monkeypatch.setattr(drafts, "_path", lambda: tmp_path / "drafts.json")
    from agent import code_prs
    monkeypatch.setattr(code_prs, "_path", lambda: tmp_path / "prs.json")
    monkeypatch.setattr(config, "allow_file_write", True)
    monkeypatch.setattr(config, "privacy_mode", "standard")
    from agent.lean import stop
    monkeypatch.setattr(stop, "paused", lambda: False)
    t = tm.create_thread(surface="code", title="Fix parser")
    store.update_thread_state(t.thread_id, active_project_id=p.id, workspace_root=str(root))
    return SimpleNamespace(pm=pm, project=p, other=other, root=root, tm=tm, store=store, tid=t.thread_id, grants=grants, rooms=room_store)


def test_timed_snooze_wakes_without_execution_or_writes(env, monkeypatch):
    turn = env.store.create_execution(thread_id=env.tid, active_project_id=env.project.id)
    env.store.update_execution(turn.id, status="completed", success=True)
    env.store.update_thread_state(env.tid, active_turn_id="", current_execution_id="")
    monkeypatch.setattr(code_inbox.time, "time", lambda: 1000)
    snapshot = env.store.code_inbox_snapshot(env.tid)
    env.store.update_code_inbox(env.tid, "snoozed", snapshot["activity"], snooze_seconds=900)
    assert env.store.code_inbox_snapshot(env.tid)["snoozed_until"] == 1900
    monkeypatch.setattr(code_inbox.time, "time", lambda: 1901)
    awake = env.store.code_inbox_snapshot(env.tid)
    assert awake["state"] == "ready_to_review" and awake["woke"]
    assert len(env.store.list_executions(env.tid)) == 1


def test_grants_are_hashed_read_only_scoped_expiring_and_revocable(env, monkeypatch):
    client = env.grants.create("Read client", [env.project.id])
    token = client["token"]
    assert token not in env.grants.path.read_text() and token not in str(env.grants.list())
    assert control.invoke(token, "projects", {})["projects"] == [{"id": env.project.id, "name": "Fixture"}]
    with pytest.raises(PermissionError):
        control.invoke(token, "send", {"id": env.tid, "message": "run", "request_id": "request-1"})
    other_thread = env.tm.create_thread(surface="code")
    env.store.update_thread_state(other_thread.thread_id, active_project_id=env.other.id)
    with pytest.raises(PermissionError):
        control.invoke(token, "status", {"id": other_thread.thread_id})
    assert len(control.invoke(token, "threads", {})["threads"]) == 1
    env.grants.revoke(client["client"]["id"])
    with pytest.raises(PermissionError):
        control.invoke(token, "threads", {})
    expired = env.grants.create("Expiring", [env.project.id])
    monkeypatch.setattr(control.time, "time", lambda: expired["client"]["expires_at"] + 1)
    with pytest.raises(PermissionError):
        control.invoke(expired["token"], "projects", {})


def test_scope_drift_and_chat_are_refused(env, monkeypatch):
    token = env.grants.create("Control", [env.project.id], control=True)["token"]
    chat = env.tm.create_thread(title="Private chat")
    with pytest.raises(PermissionError):
        control.invoke(token, "status", {"id": chat.thread_id})
    from agent import project_workspace
    monkeypatch.setattr(project_workspace, "project", lambda identity: SimpleNamespace(id=identity, trust_state="untrusted", workspace_root=str(env.root)))
    with pytest.raises(PermissionError):
        control.invoke(token, "projects", {})


def test_agent_conversations_are_separately_granted(env):
    token = env.grants.create("Echo only", [], ["echo"])["token"]
    direct = env.rooms.direct("echo", "Echo")
    other = env.rooms.direct("scout", "Scout")
    assert control.invoke(token, "status", {"id": direct.thread_id})["id"] == direct.thread_id
    with pytest.raises(PermissionError):
        control.invoke(token, "status", {"id": other.thread_id})
    with pytest.raises(PermissionError):
        control.invoke(token, "status", {"id": env.tid})


def test_public_control_route_requires_own_grant_and_physical_loopback(env):
    app = FastAPI(); app.include_router(router)
    token = env.grants.create("Reader", [env.project.id])["token"]
    local = TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1234))
    path = "/echo-code/control/invoke"
    payload = {"action": "projects"}
    assert local.post(path, json=payload).status_code == 403
    assert local.post(path, json=payload, headers={"Authorization": "Bearer owner-api-key"}).status_code == 403
    assert local.post(path, json=payload, headers={"Authorization": "Bearer " + token}).status_code == 200
    assert local.post(path, json=payload, headers={"Authorization": "Bearer " + token, "Origin": "http://127.0.0.1"}).status_code == 403
    remote = TestClient(app, base_url="http://127.0.0.1", client=("192.0.2.10", 1234))
    assert remote.post(path, json=payload, headers={"Authorization": "Bearer " + token, "X-Forwarded-For": "127.0.0.1"}).status_code == 403


def test_stash_images_validate_and_remain_project_scoped(env):
    from PIL import Image
    stream = io.BytesIO(); Image.new("RGB", (8, 8)).save(stream, format="PNG")
    data = base64.b64encode(stream.getvalue()).decode()
    row = drafts.save(env.project.id, "Check this layout", [data])
    assert row["images"][0] in row["text"]
    assert (env.root / row["images"][0]).read_bytes() == stream.getvalue()
    assert drafts.restore(row["id"])["text"] == row["text"]
    with pytest.raises(Exception):
        drafts.save(env.project.id, "bad", [base64.b64encode(b"<script>bad</script>").decode()])
    drafts.remove(row["id"])
    assert not drafts.list_drafts() and (env.root / row["images"][0]).exists()


@pytest.mark.parametrize("desktop_entry", [False, True])
def test_real_stdio_mcp_lists_and_calls_scoped_backend(env, desktop_entry):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    token = env.grants.create("Fixture MCP", [env.project.id])["token"]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            try:
                result = control.invoke(self.headers.get("Authorization", "")[7:], payload["action"], payload.get("arguments", {}))
                status = 200
            except PermissionError:
                result, status = {"error": "denied"}, 403
            raw = json.dumps(result).encode()
            self.send_response(status); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=http.serve_forever, daemon=True); worker.start()
    async def run():
        args = [str(Path(__file__).parents[2] / "desktop/backend/echospeak_backend.py"), "--control-mcp"] if desktop_entry else ["-m", "agent.control_mcp"]
        params = StdioServerParameters(command=sys.executable, args=args, cwd=str(Path(__file__).parents[1]),
                env={**os.environ, "ECHOSPEAK_CONTROL_URL": f"http://127.0.0.1:{http.server_port}", "ECHOSPEAK_CONTROL_TOKEN": token})
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                names = {t.name for t in (await session.list_tools()).tools}
                assert {"echo_projects", "echo_threads", "echo_thread_send", "echo_thread_stop"} <= names
                result = await session.call_tool("echo_projects", {})
                assert not result.is_error and "Fixture" in str(result.content)
                denied = await session.call_tool("echo_thread_send", {"id": env.tid, "message": "run", "request_id": "fixture-1"})
                assert denied.is_error
    try:
        asyncio.run(run())
    finally:
        http.shutdown(); http.server_close(); worker.join(2)


def test_usage_is_allowlisted_and_diagnostics_exclude_content(env):
    from agent.code_usage import record
    from agent.code_diagnostics import snapshot
    saved = []
    session = SimpleNamespace(row={}, save=lambda: saved.append(True))
    record(session, {"usage": {"input_tokens": 42, "output_tokens": 8, "secret": "do not export", "total_tokens": float("nan")}, "total_cost_usd": 0.02})
    assert session.row["reported_usage"]["tokens"] == {"input_tokens": 42, "output_tokens": 8}
    assert session.row["reported_usage"]["reported_cost_usd"] == .02
    report = snapshot(SimpleNamespace(sessions={"a": SimpleNamespace(row={"runtime_id": "codex", "state": "stopped", "prompt": "PRIVATE"}, task=None, approvals={})}))
    assert "PRIVATE" not in str(report) and "secret" not in str(session.row)
    assert "backend_rss_bytes" in report["resources"] or "unavailable" in report["resources"]


def test_background_reuses_query_and_drains_followups_without_duplicate_send(env, monkeypatch):
    from agent import code_tasks
    import api.routes.chat
    entered, release = threading.Event(), threading.Event()
    calls = []
    async def scripted(request):
        calls.append(request.message)
        run = env.store.create_execution(thread_id=env.tid, request_id=request.client_request_id, active_project_id=env.project.id)
        if len(calls) == 1:
            entered.set(); assert release.wait(5)
        env.store.update_execution(run.id, status="completed", success=True)
        env.store.update_thread_state(env.tid, current_execution_id="", active_turn_id="")
        return SimpleNamespace(success=True)
    monkeypatch.setattr(api.routes.chat, "query", scripted)
    first = code_tasks.submit(env.tid, "first", "request-first")
    assert first["status"] == "accepted" and entered.wait(5)
    assert code_tasks.submit(env.tid, "second", "request-second")["status"] == "queued"
    assert code_tasks.submit(env.tid, "second", "request-second")["status"] == "queued"
    with pytest.raises(ValueError):
        code_tasks.submit(env.tid, "different", "request-first")
    release.set()
    deadline = time.monotonic() + 5
    while code_tasks.active(env.tid) and time.monotonic() < deadline:
        time.sleep(.02)
    assert calls == ["first", "second"] and not code_tasks.active(env.tid)


def test_background_cannot_start_a_second_native_writer_in_the_same_project(env, monkeypatch):
    from agent import code_tasks
    first = env.store.create_execution(thread_id=env.tid, active_project_id=env.project.id)
    other = env.tm.create_thread(surface="code")
    env.store.update_thread_state(other.thread_id, active_project_id=env.project.id)
    with pytest.raises(ValueError, match="Another native"):
        code_tasks.submit(other.thread_id, "edit", "request-other")
    env.store.update_execution(first.id, status="canceled")


@pytest.mark.parametrize("change", ["none", "dirty", "head", "owner_snooze", "new_turn"])
def test_pr_settlement_respects_linked_identity_and_new_owner_activity(env, monkeypatch, tmp_path, change):
    from agent import code_prs, workbench
    turn = env.store.create_execution(thread_id=env.tid, active_project_id=env.project.id)
    env.store.update_execution(turn.id, status="completed", success=True)
    env.store.update_thread_state(env.tid, active_turn_id="", current_execution_id="")
    before = env.store.code_inbox_snapshot(env.tid)
    state_pr = {"merged": False, "during_read": False}
    class Bench:
        def __init__(self, supervisor):
            pass
        def context(self, pid, external=""):
            return {"project_id": pid, "root": str(env.root)}
        async def read_git(self, context, *argv):
            if argv[0] == "branch": return "codex/fixture"
            if argv[0] == "status": return " M changed.py" if change == "dirty" and state_pr["merged"] else ""
            return "new-head" if change == "head" and state_pr["merged"] else "original-head"
        async def pull_requests(self, context):
            if state_pr["merged"] and not state_pr["during_read"]:
                state_pr["during_read"] = True
                if change == "owner_snooze": env.store.update_code_inbox(env.tid, "snoozed", before["activity"], snooze_seconds=3600)
                if change == "new_turn": env.store.create_execution(thread_id=env.tid, active_project_id=env.project.id)
            return {"repository":"fixture/repo","items":[{"number":7,"headRefName":"codex/fixture","url":"https://github.com/fixture/repo/pull/7","state":"MERGED" if state_pr["merged"] else "OPEN"}]}
    monkeypatch.setattr(workbench, "Workbench", Bench)
    service = SimpleNamespace()
    asyncio.run(code_prs.link(service, env.tid, False, 7, before["activity"]))
    state_pr["merged"] = True
    result = asyncio.run(code_prs.sync(service))
    after = env.store.code_inbox_snapshot(env.tid)
    assert (after["state"] == "settled") == (change == "none")
    assert bool(result["settled"]) == (change == "none")


def test_bridge_refuses_nonlocal_and_redirect_origins(monkeypatch):
    from agent.control_mcp import call
    monkeypatch.setenv("ECHOSPEAK_CONTROL_TOKEN", "fixture-not-owner")
    monkeypatch.setenv("ECHOSPEAK_CONTROL_URL", "https://example.invalid")
    with pytest.raises(ValueError, match="local"):
        call("projects", {})


def test_bulk_triage_reports_each_result_without_hiding_failures(env):
    app = FastAPI(); app.include_router(router)
    client = TestClient(app)
    snapshot = env.store.code_inbox_snapshot(env.tid)
    result = client.post("/echo-code/inbox/batch", json={"action":"snoozed","snooze_seconds":900,"targets":[
        {"id":env.tid,"activity":snapshot["activity"]},{"id":"missing","activity":"0"*32}]}).json()
    assert [r["ok"] for r in result["results"]] == [True, False]


def test_followup_to_an_existing_interactive_turn_uses_the_same_queue(env, monkeypatch):
    from agent import code_tasks
    import api.routes.chat
    existing = env.store.create_execution(thread_id=env.tid, active_project_id=env.project.id)
    calls = []
    async def scripted(request):
        calls.append(request.message)
        return SimpleNamespace(success=True)
    monkeypatch.setattr(api.routes.chat, "query", scripted)
    assert code_tasks.submit(env.tid, "followup", "request-existing")["status"] == "queued"
    assert not calls
    env.store.update_execution(existing.id, status="completed")
    env.store.update_thread_state(env.tid, active_turn_id="", current_execution_id="")
    deadline=time.monotonic()+5
    while code_tasks.active(env.tid) and time.monotonic()<deadline:
        time.sleep(.02)
    assert calls == ["followup"]


def test_stop_during_background_admission_is_retained(env, monkeypatch):
    from agent import code_tasks
    import api.routes.chat
    entered, release = threading.Event(), threading.Event()
    cancelled=[]
    async def scripted(request):
        entered.set();assert release.wait(5)
        cancelled.append(code_tasks.was_cancelled(request.client_request_id))
        return SimpleNamespace(success=False)
    monkeypatch.setattr(api.routes.chat,"query",scripted)
    code_tasks.submit(env.tid,"cancel this","request-cancel")
    assert entered.wait(5)
    code_tasks.cancel(env.tid);release.set()
    deadline=time.monotonic()+5
    while code_tasks.active(env.tid) and time.monotonic()<deadline:
        time.sleep(.02)
    assert cancelled == [True]


def test_control_creates_and_sends_only_in_its_granted_project(env, monkeypatch):
    from agent import code_tasks
    import api.routes.chat
    token = env.grants.create("Writer", [env.project.id], control=True)["token"]
    args = {"project_id": env.project.id, "request_id": "create-controlled"}
    row = control.invoke(token, "create", args)
    assert control.invoke(token, "create", args)["thread_id"] == row["thread_id"]
    with pytest.raises(PermissionError):
        control.invoke(token, "create", {"project_id": env.other.id, "request_id": "create-forbidden"})
    entered, release = threading.Event(), threading.Event()
    observed = []
    async def scripted(request):
        observed.append((request.thread_id, request.message))
        entered.set(); assert release.wait(5)
        return SimpleNamespace(success=True)
    monkeypatch.setattr(api.routes.chat, "query", scripted)
    send = {"id": row["thread_id"], "message": "Inspect the parser", "request_id": "send-controlled"}
    try:
        assert control.invoke(token, "send", send)["status"] == "accepted"
        assert entered.wait(5)
        control.invoke(token, "send", send)
        control.invoke(token, "stop", {"id": row["thread_id"]})
        assert code_tasks.was_cancelled(send["request_id"])
        assert env.store.queue_paused(row["thread_id"])
    finally:
        release.set()
        deadline = time.monotonic() + 5
        while code_tasks.active(row["thread_id"]) and time.monotonic() < deadline:
            time.sleep(.02)
    assert observed == [(row["thread_id"], "Inspect the parser")]


def test_bridge_never_forwards_a_grant_across_a_redirect(monkeypatch):
    from agent.control_mcp import call
    hits = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/redirect-target")
            self.end_headers()
        def do_GET(self):
            hits.append(self.headers.get("Authorization"))
            self.send_response(200); self.end_headers()
    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=http.serve_forever, daemon=True); worker.start()
    monkeypatch.setenv("ECHOSPEAK_CONTROL_URL", f"http://127.0.0.1:{http.server_port}")
    monkeypatch.setenv("ECHOSPEAK_CONTROL_TOKEN", "fixture-secret")
    try:
        with pytest.raises(ValueError, match="redirects"):
            call("projects", {})
        assert not hits
    finally:
        http.shutdown(); http.server_close(); worker.join(2)
