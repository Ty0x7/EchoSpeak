"""Project collaboration contracts against real stores; no model or external agent calls."""
import importlib.util
import io
import json
from pathlib import Path

import pytest
from PIL import Image
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent import projects, project_workspace as workspace, state, threads, routines, media_library
from agent.lean import artifacts, policy
from agent.lean.toolbox import Toolbox
from config import config


@pytest.fixture
def env(tmp_path, monkeypatch):
    import config as config_module
    monkeypatch.setattr(config_module, "DATA_DIR", tmp_path / "data")
    manager = projects.ProjectManager(tmp_path / "registry")
    monkeypatch.setattr(projects, "_project_manager", manager)
    monkeypatch.setattr(projects.ProjectManager, "read_git_metadata", staticmethod(lambda root: {}))
    root = tmp_path / "workspace"
    root.mkdir()
    item = manager.create_project("WATCHLIST", workspace_root=str(root), metadata={"brief": "Road-system project", "unrelated": 42})
    store = state.StateStore(tmp_path / "state")
    monkeypatch.setattr(state, "get_state_store", lambda: store)
    store.update_thread_state("chat-a", active_project_id=item.id, workspace_root=str(root))
    tm = threads.ThreadManager(tmp_path / "threads.json")
    monkeypatch.setattr(threads, "get_thread_manager", lambda: tm)
    rm = routines.RoutineManager(tmp_path / "routines")
    monkeypatch.setattr(routines, "get_routine_manager", lambda: rm)
    library = media_library.MediaLibraryStore(tmp_path / "media")
    monkeypatch.setattr(media_library, "get_media_library_store", lambda: library)
    monkeypatch.setattr(config, "enable_system_actions", True)
    monkeypatch.setattr(config, "allow_file_write", True)
    (root / "reports").mkdir()
    (root / "reports/roads.md").write_text("# City Roads\n\nChanged the layout.\n\n![Before](before.png)", encoding="utf-8")
    out = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(out, "PNG")
    (root / "reports/before.png").write_bytes(out.getvalue())
    return item, manager, root, store


def manifest(item, **patch):
    return {"schema_version": 1, "project_id": item.id, "id": "city-roads", "revision": 1,
            "title": "City Roads — Layout Improvements", "summary": "Connected the road junctions.", "agent": "claude-code",
            "session_id": "external-session", "status": "completed", "report": "reports/roads.md",
            "media": [{"path": "reports/before.png", "caption": "Before"}], "files_changed": ["roads.gd"],
            "verification": [{"label": "Game scene", "result": "not_run", "evidence": "User will run Godot."}],
            "remaining": ["Check collisions in Godot"], **patch}


def test_import_persistence_idempotency_and_metadata_preservation(env):
    item, manager, root, _ = env
    value = manifest(item)
    first = workspace.import_handoff(item.id, value)
    assert first["changed"] and first["record"]["verification_basis"] == "agent_reported"
    assert workspace.import_handoff(item.id, value)["changed"] is False
    assert len(artifacts.list_all()) == 1
    loaded = projects.ProjectManager(manager.projects_dir).get_project(item.id)
    assert loaded.workspace_records[0]["id"] == "city-roads"
    assert loaded.metadata == {"brief": "Road-system project", "unrelated": 42}
    assert not (root / ".echospeak").exists()  # manual import never configures another agent


def test_catalog_refresh_reuses_lightweight_summaries_and_updates_after_edits(env, monkeypatch):
    item, _, _, _ = env
    row = workspace.import_handoff(item.id, manifest(item))["record"]
    artifacts.list_all()
    original = Path.read_text
    reads = []
    def read(path, *args, **kwargs):
        reads.append(path.name)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", read)
    artifacts.list_all()
    assert row["artifact_id"] + ".json" not in reads
    artifacts.update(row["artifact_id"], content="# Revised", title="Revised report")
    assert artifacts.list_all()[0]["title"] == "Revised report"


def test_revisions_preserve_documents_and_reject_conflicting_claims(env):
    item, _, root, _ = env
    first = workspace.import_handoff(item.id, manifest(item))["record"]
    (root / "reports/roads.md").write_text("# New layout", encoding="utf-8")
    second = workspace.import_handoff(item.id, manifest(item, revision=2, status="blocked"))["record"]
    doc = artifacts.get(first["artifact_id"])
    assert len(doc["versions"]) == 2
    assert "Changed the layout" in artifacts.version(doc, first["artifact_version"])["content"]
    assert second["history"][0]["status"] == "completed"
    assert workspace.import_handoff(item.id, manifest(item, report="gone.md"))["stale"]
    with pytest.raises(ValueError, match="increase revision"):
        workspace.import_handoff(item.id, manifest(item, revision=2, title="Conflicting title"))


def test_interrupted_index_write_reuses_artifact_version(env, monkeypatch):
    item, manager, _, _ = env
    save = manager.save_workspace
    monkeypatch.setattr(manager, "save_workspace", lambda *a, **kw: (_ for _ in ()).throw(OSError("Interrupted")))
    with pytest.raises(OSError):
        workspace.import_handoff(item.id, manifest(item))
    monkeypatch.setattr(manager, "save_workspace", save)
    workspace.import_handoff(item.id, manifest(item))
    assert len(artifacts.list_all()) == 1
    assert artifacts.list_all()[0]["versions"] == 1


@pytest.mark.parametrize("path", ["../outside.md", "/etc/passwd", "C:\\private.txt", "\\\\host\\share", "reports/../../outside.txt", ".git/config", ".env", ".env.production", ".ssh/id_rsa", "reports/private.pem", "reports/settings.secrets.json", "reports/x:y.md"])
def test_preview_and_import_reject_private_or_out_of_scope_paths(env, path):
    item, _, root, _ = env
    with pytest.raises((ValueError, OSError)):
        workspace.safe_path(root, path, exists=False)
    with pytest.raises((ValueError, OSError)):
        workspace.import_handoff(item.id, manifest(item, files_changed=[path]))
    assert workspace.project(item.id).workspace_records == []


def test_symlink_escape_rejected_even_when_target_is_inside(env):
    item, _, root, _ = env
    try:
        (root / "alias.md").symlink_to(root / "reports/roads.md")
    except OSError:
        pytest.skip("Symlink creation needs Windows developer mode")
    with pytest.raises(ValueError, match="Linked"):
        workspace.read_file(item, "alias.md")


def test_project_identity_and_associations_are_checked(env):
    item, _, _, _ = env
    with pytest.raises(ValueError, match="different project"):
        workspace.import_handoff(item.id, manifest(item, project_id="foreign"))
    with pytest.raises(ValueError, match="session"):
        workspace.import_handoff(item.id, manifest(item, echo_session_id="foreign"))
    foreign = artifacts.create(title="Foreign", kind="markdown", content="private", session_id="foreign")
    with pytest.raises(ValueError, match="artifact"):
        workspace.import_handoff(item.id, manifest(item, artifact_ids=[foreign["id"]]))
    assert foreign["id"] not in {a["id"] for a in workspace.overview(item.id)["artifacts"]}


def test_sync_keeps_last_good_report_when_another_file_is_partial(env):
    item, _, root, _ = env
    assert workspace.sync(item.id)["enabled"] is False
    workspace.prepare_sharing(item.id)
    (root / ".echospeak/handoffs/roads.json").write_text(json.dumps(manifest(item)), encoding="utf-8")
    (root / ".echospeak/handoffs/partial.json").write_text('{"id":', encoding="utf-8")
    result = workspace.sync(item.id)
    assert result["imported"] == 1 and len(result["errors"]) == 1
    assert workspace.sync(item.id)["imported"] == 0
    assert len(workspace.project(item.id).workspace_records) == 1


def test_sharing_does_not_overwrite_a_foreign_binding(env):
    item, _, root, _ = env
    (root / ".echospeak").mkdir()
    binding = root / ".echospeak/project.json"
    binding.write_text('{"project_id":"foreign"}', encoding="utf-8")
    with pytest.raises(ValueError, match="different project"):
        workspace.prepare_sharing(item.id)
    assert "foreign" in binding.read_text()


def test_export_respects_write_permission_and_preserves_independent_agents(env, monkeypatch):
    item, _, root, _ = env
    monkeypatch.setattr(config, "allow_file_write", False)
    with pytest.raises(PermissionError):
        workspace.export_context(item.id)
    assert not (root / ".echospeak").exists()
    monkeypatch.setattr(config, "allow_file_write", True)
    workspace.import_handoff(item.id, manifest(item))
    result = workspace.export_context(item.id)
    exported = json.loads((root / result["path"]).read_text(encoding="utf-8"))
    assert workspace.project(item.id).workspace_sharing_enabled is False
    assert exported["records"][0]["verification_basis"] == "agent_reported"
    assert "workspace_root" not in exported["project"]
    assert "Changed the layout" in exported["documents"][0]["content"]
    assert not (root / "AGENTS.md").exists() and not (root / ".claude").exists()


def test_html_stays_sandboxed_and_only_inlines_allowed_images(env):
    item, _, root, _ = env
    (root / "reports/roads.html").write_text('<h1>Roads</h1><img src="before.png"><img src="../../private.png"><script>fetch("https://example.com")</script>', encoding="utf-8")
    row = workspace.import_handoff(item.id, manifest(item, report="reports/roads.html"))["record"]
    page = artifacts.frame_html(artifacts.get(row["artifact_id"]))
    assert "data:image/png;base64," in page
    assert "connect-src 'none'" in page and "frame-src 'none'" in page
    assert "allow-same-origin" not in artifacts.FRAME_CSP
    assert workspace.read_file(item, "reports/before.png", media=True)[1] == "image/png"


def test_project_tool_is_scoped_and_participates_in_existing_trust_rules(env):
    item, _, root, _ = env
    workspace.import_handoff(item.id, manifest(item))
    box = Toolbox(toolsets=["core"], extra_tools=workspace.tools(), session_id="chat-a", project_root=str(root))
    result = box.run("project_workspace", {"record_id": "city-roads"})
    assert result.ok and "Check collisions" in result.output
    assert policy.is_untrusted_source("project_workspace")
    assert policy.evaluate("memory_save", {"text": "claim"}, tainted_by=["project_workspace"], secrets=[]).action == "ask"
    other = Toolbox(toolsets=["core"], extra_tools=workspace.tools(), session_id="foreign", project_root=str(root))
    assert not other.run("project_workspace", {}).ok
    assert "project_workspace" not in Toolbox(toolsets=["web"], extra_tools=workspace.tools()).names


def test_reported_checks_do_not_become_independent_learning_evidence():
    from agent.learning.episodes import tool_rows, grade
    rows = tool_rows([{"kind": "tool", "name": name, "status": "done", "output": '{"verification": [{"result": "passed"}]}' }
                      for name in ["project_workspace", "save_project_report"]])
    assert rows == []
    level, _, _ = grade("Check the roads", rows, outcome="success", claim_failed=False)
    assert level == 0


def test_echo_can_save_a_named_report_without_exporting_or_starting_agents(env):
    item, _, root, _ = env
    box = Toolbox(toolsets=["core"], extra_tools=workspace.tools(), session_id="chat-a", project_root=str(root))
    result = box.run("save_project_report", {"id": "echo-review", "title": "Road Review", "summary": "Review completed", "status": "completed", "remaining": ["User testing"]})
    assert result.ok
    row = workspace.project(item.id).workspace_records[0]
    assert row["agent"] == "echo" and row["echo_session_id"] == "chat-a"
    assert row["verification"] == []
    assert not (root / ".echospeak").exists()


def test_project_tool_reads_long_reports_without_silent_truncation(env):
    item, _, root, _ = env
    (root / "reports/roads.md").write_text("a" * 24000 + "The unresolved collision check", encoding="utf-8")
    workspace.import_handoff(item.id, manifest(item))
    box = Toolbox(toolsets=["core"], extra_tools=workspace.tools(), session_id="chat-a", project_root=str(root))
    first = json.loads(box.run("project_workspace", {"record_id": "city-roads"}).output)
    assert first["next_offset"] == 24000
    next_page = json.loads(box.run("project_workspace", {"record_id": "city-roads", "offset": first["next_offset"]}).output)
    assert next_page["document"] == "The unresolved collision check" and next_page["next_offset"] is None


def test_existing_artifacts_and_activity_use_real_project_associations(env):
    item, _, _, store = env
    old = artifacts.create(title="Legacy document", kind="markdown", content="Existing", session_id="chat-a")
    store.create_execution(thread_id="chat-a", session_id="chat-a", query="Review roads", project_id=item.id, active_project_id=item.id)
    data = workspace.overview(item.id)
    assert old["id"] in {a["id"] for a in data["artifacts"]}
    assert data["activity"][0]["objective"] == "Review roads"


def test_echo_report_can_preserve_its_own_screenshot_but_not_another_chat(env, monkeypatch):
    from agent.lean import observations
    item, _, root, _ = env
    monkeypatch.setattr(observations, "root", lambda: root.parent / "observations")
    own = observations.save(Image.new("RGB", (10, 10), "white"), "chat-a", "Road screenshot")
    foreign = observations.save(Image.new("RGB", (10, 10), "black"), "foreign", "Private screenshot")
    box = Toolbox(toolsets=["core"], extra_tools=workspace.tools(), session_id="chat-a", project_root=str(root))
    args = {"id": "screen-review", "title": "Road screenshot review", "summary": "Screenshot preserved for user review", "observation_ids": [foreign["id"]]}
    assert not box.run("save_project_report", args).ok
    assert not (root / ".echospeak").exists()
    assert box.run("save_project_report", {**args, "observation_ids": [own["id"]]}).ok
    record = workspace.project(item.id).workspace_records[0]
    assert (root / record["media"][0]["path"]).read_bytes() == observations.load(own["id"], "chat-a")[1]


def test_routes_validate_imports_and_media_ranges(env):
    from api.routes.project_workspace import router
    app = FastAPI()
    app.include_router(router)
    item, _, _, _ = env
    client = TestClient(app)
    base = f"/projects/{item.id}/workspace"
    assert client.post(base + "/imports", json=manifest(item)).status_code == 200
    assert client.post(base + "/imports", json=manifest(item, command="execute this")).status_code == 422
    assert client.get(base + "/file", params={"path": "../private.md"}).status_code == 400
    response = client.get(base + "/media", params={"path": "reports/before.png"}, headers={"Range": "bytes=0-7"})
    assert response.status_code == 206 and response.content == b"\x89PNG\r\n\x1a\n"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert client.get(base + "/media", params={"path": "reports/before.png"}, headers={"Range": "bytes=999999-"}).status_code == 416


def test_project_routes_keep_the_existing_api_auth_boundary(env, monkeypatch):
    from api.server import app
    item, _, _, _ = env
    monkeypatch.setattr(config, "api_auth_enabled", True)
    monkeypatch.setattr(config, "api_auth_localhost_bypass", False)
    monkeypatch.setattr(config, "api_auth_key", "workspace-test-key")
    client = TestClient(app, base_url="http://127.0.0.1")
    url = f"/projects/{item.id}/workspace"
    assert client.get(url).status_code == 401
    assert client.get(url, headers={"x-echospeak-key": "workspace-test-key"}).status_code == 200


def hook_module():
    path = Path(__file__).resolve().parents[3] / "scripts/project_handoff.py"
    spec = importlib.util.spec_from_file_location("project_handoff", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_hook_is_a_receipt_not_a_completion_claim(env, monkeypatch):
    item, _, root, _ = env
    workspace.prepare_sharing(item.id)
    module = hook_module()
    event = {"hook_event_name": "Stop", "session_id": "claude-session", "turn_id": "turn-a", "cwd": str(root), "last_assistant_message": "SECRET", "transcript_path": "private"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    assert module.main(["--project", str(root), "--agent", "claude-code", "--hook"]) == 0
    paths = list((root / ".echospeak/events").glob("*.json"))
    receipt = json.loads(paths[0].read_text())
    assert receipt["status"] == "needs_review" and "SECRET" not in paths[0].read_text()
    assert not list((root / ".echospeak/handoffs").glob("*.json"))
    assert workspace.project(item.id).workspace_records == []


def test_publisher_and_importer_work_together_without_a_model(env):
    item, _, root, _ = env
    workspace.prepare_sharing(item.id)
    source = root / "reports/manifest.json"
    source.write_text(json.dumps(manifest(item)), encoding="utf-8")
    module = hook_module()
    argv = ["--project", str(root), "--manifest", "reports/manifest.json"]
    assert module.main(argv) == 0 and module.main(argv) == 0
    assert len(list((root / ".echospeak/handoffs").glob("*.json"))) == 1
    assert workspace.sync(item.id)["imported"] == 1
    source.write_text(json.dumps(manifest(item, revision=2, status="interrupted")), encoding="utf-8")
    assert module.main(argv) == 0
    assert workspace.sync(item.id)["imported"] == 1
    assert workspace.project(item.id).workspace_records[0]["status"] == "interrupted"
