"""Project file discovery contracts against real stores; no model or external agent calls."""
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


def client_for(item):
    from api.routes.project_workspace import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), f"/projects/{item.id}/workspace"


def paired_files(root):
    (root / "docs").mkdir(exist_ok=True)
    (root / "artifacts/media").mkdir(parents=True, exist_ok=True)
    (root / "docs/roads.md").write_text("# Roads\n\nTechnical decisions", encoding="utf-8")
    (root / "artifacts/media/after.png").write_bytes((root / "reports/before.png").read_bytes())
    (root / "artifacts/roads.html").write_text('<html><head><title>Roads &amp; junctions</title></head><body><img src="media/after.png"><p>Actual result</p></body></html>', encoding="utf-8")


def test_discovery_pairs_actual_files_without_writes_or_artifact_copies(env, monkeypatch):
    item, _, root, _ = env
    paired_files(root)
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    monkeypatch.setattr(config, "allow_file_write", False)
    data = workspace.overview(item.id)
    row = next(row for row in data["documents"] if row["path"] == "artifacts/roads.html")
    assert row["title"] == "Roads & junctions" and row["markdown"] == "docs/roads.md"
    assert row["thumbnail"] == "artifacts/media/after.png"
    assert "docs/roads.md" not in {r["path"] for r in data["documents"]}
    assert not data["artifacts"] and not data["records"]
    assert before == {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert not (root / ".echospeak").exists()


def test_refresh_detects_edits_additions_deletions_and_media_changes(env):
    item, _, root, _ = env
    paired_files(root)
    first = workspace.discover(item)
    (root / "artifacts/roads.html").write_text("<title>Updated roads</title>", encoding="utf-8")
    updated = workspace.discover(item)
    assert first["file_revision"] != updated["file_revision"]
    assert next(r for r in updated["documents"] if r["path"] == "artifacts/roads.html")["title"] == "Updated roads"
    (root / "artifacts/media/after.png").write_bytes(b"updated image")
    media = workspace.discover(item)
    assert media["file_revision"] != updated["file_revision"]
    (root / "artifacts/roads.html").unlink()
    final = workspace.discover(item)
    assert final["file_revision"] != media["file_revision"]
    assert "docs/roads.md" in {r["path"] for r in final["documents"]}


@pytest.mark.parametrize("path", ["../outside.md", "/etc/passwd", "C:\\private.txt", "\\\\host\\share", "reports/../../outside.txt", ".git/config", ".env", ".env.production", ".ssh/id_rsa", "reports/private.pem", "reports/settings.secrets.json", "reports/x:y.md", ".agents/skills/foo/SKILL.md"])
def test_preview_rejects_private_or_out_of_scope_paths(env, path):
    _, _, root, _ = env
    with pytest.raises((ValueError, OSError)):
        workspace.safe_path(root, path, exists=False)


def test_discovery_ignores_generated_private_and_agent_configuration(env):
    item, _, root, _ = env
    for folder in [".git", ".claude", ".agents", ".echospeak", ".test-state", "node_modules", "target", "build"]:
        (root / folder).mkdir()
        (root / folder / "private.html").write_text("secret", encoding="utf-8")
    (root / ".env.md").write_text("secret", encoding="utf-8")
    assert [r["path"] for r in workspace.discover(item)["documents"]] == ["reports/roads.md"]


@pytest.mark.parametrize("path", [".git./secret.md", ".git /secret.md", "reports/CON.md", ".azure/token.md", "reports/file.md "])
def test_windows_aliases_cannot_bypass_private_path_rules(env, path):
    _, _, root, _ = env
    with pytest.raises(ValueError): workspace.safe_path(root, path, exists=False)


def test_symlink_escape_rejected_even_when_target_is_inside(env):
    item, _, root, _ = env
    try: (root / "alias.md").symlink_to(root / "reports/roads.md")
    except OSError: pytest.skip("Symlink creation needs Windows developer mode")
    with pytest.raises(ValueError, match="Linked"):
        workspace.read_file(item, "alias.md")
    assert "alias.md" not in {r["path"] for r in workspace.discover(item)["documents"]}


def test_scan_is_bounded_and_files_remain_browsable(env, monkeypatch):
    item, _, root, _ = env
    for i in range(10): (root / f"page-{i}.html").write_text("<p>Work</p>", encoding="utf-8")
    monkeypatch.setattr(workspace, "SCAN_ENTRIES", 4)
    result = workspace.discover(item)
    assert result["scan_limited"] and len(result["documents"]) <= 4
    assert len(workspace.files(item)) > 4


def test_nested_reports_and_notes_are_prioritized_before_large_source_trees(env, monkeypatch):
    item, _, root, _ = env
    for folder in ("aaa-source", "artifacts/releases", "docs/releases"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    for i in range(60):
        (root / f"aaa-source/file-{i}.md").write_text("source docs", encoding="utf-8")
    for i in range(3):
        (root / f"artifacts/releases/v1.0.{i}.html").write_text("<title>Version</title>", encoding="utf-8")
        (root / f"docs/releases/v1.0.{i}.md").write_text("# Version", encoding="utf-8")
    monkeypatch.setattr(workspace, "SCAN_ENTRIES", 30)
    monkeypatch.setattr(workspace, "SCAN_SECONDS", 10)
    result = workspace.discover(item)
    rows = {row["path"]: row for row in result["documents"]}
    assert result["scan_limited"]
    for i in range(3):
        assert rows[f"artifacts/releases/v1.0.{i}.html"]["markdown"] == f"docs/releases/v1.0.{i}.md"


def test_metadata_scan_does_not_follow_windows_reparse_points(env, monkeypatch):
    from types import SimpleNamespace
    item, _, root, _ = env
    (root / "linked").mkdir()
    (root / "linked/private.html").write_text("must not be discovered", encoding="utf-8")
    original = workspace.os.scandir
    class Entry:
        def __init__(self, entry): self.entry = entry
        def __getattr__(self, name): return getattr(self.entry, name)
        def stat(self, **kwargs):
            if self.entry.name == "linked": return SimpleNamespace(st_file_attributes=0x400)
            return self.entry.stat(**kwargs)
    class Listing:
        def __init__(self, folder): self.listing = original(folder)
        def __enter__(self): return (Entry(entry) for entry in self.listing)
        def __exit__(self, *args): self.listing.close()
    monkeypatch.setattr(workspace.os, "scandir", lambda folder: Listing(folder))
    assert not any(row["path"].startswith("linked/") for row in workspace.discover(item)["documents"])


def test_missing_or_unattached_root_preserves_native_resources(env):
    item, manager, root, _ = env
    native = artifacts.create(title="Native", kind="markdown", content="Kept", session_id="chat-a")
    root.rename(root.with_name("moved-project"))
    data = workspace.overview(item.id)
    assert data["scan_error"] and not data["documents"]
    assert native["id"] in {a["id"] for a in data["artifacts"]}


@pytest.mark.parametrize("link,expected", [("media/a.png", "artifacts/media/a.png"), ("../docs/a.md", "docs/a.md"), ("../../secret.png", None), ("%2e%2e/%2e%2e/secret.png", None), ("file:///secret", None), ("https://example.com/a.png", None), ("//host/share", None), ("C:/private", None)])
def test_local_urls_are_project_relative(link, expected):
    assert workspace.local_reference(link, "artifacts/roads.html") == expected


def test_html_reuses_existing_csp_with_bounded_local_media(env, monkeypatch):
    item, _, root, _ = env
    paired_files(root)
    page = workspace.document_html(item, "artifacts/roads.html", '<img src="media/after.png" srcset="https://bad/x 2x"><img src="../../private.png"><video controls poster="media/after.png"><source src="media/movie.webm"></video><script>fetch("https://example.com")</script>')
    assert "data:image/png;base64," in page and "srcset=" not in page
    assert "connect-src 'none'" in page and "frame-src 'none'" in page
    assert "allow-same-origin" not in artifacts.FRAME_CSP and "file://" not in page
    monkeypatch.setattr(workspace, "INLINE_BUDGET", 1)
    page = workspace.document_html(item, "artifacts/roads.html", '<img src="media/after.png">')
    assert "data:image" not in page and 'src=""' in page


def test_project_reports_cannot_navigate_to_external_pages(env):
    item, _, _, _ = env
    page = workspace.document_html(item, "reports/roads.html", '<meta http-equiv="refresh" content="0;url=https://bad"><a href="https://bad">Link</a><a href="#evidence">Evidence</a>')
    assert 'http-equiv="refresh"' not in page
    assert 'href="https://bad"' not in page
    assert 'href="#evidence"' in page


def test_read_file_and_routes_keep_ranges_validation_and_no_write_protocol(env):
    item, _, root, _ = env
    paired_files(root)
    client, base = client_for(item)
    assert client.get(base).json()["documents"]
    assert client.get(base + "/file", params={"path": "../private.md"}).status_code == 400
    assert "data:image/png" in client.get(base + "/file", params={"path": "artifacts/roads.html"}).json()["html_preview"]
    for route in ["imports", "sync", "sharing", "export"]:
        assert client.post(base + "/" + route, json={}).status_code == 404
    response = client.get(base + "/media", params={"path": "reports/before.png"}, headers={"Range": "bytes=0-7"})
    assert response.status_code == 206 and response.content == b"\x89PNG\r\n\x1a\n"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert client.get(base + "/media", params={"path": "reports/before.png"}, headers={"Range": "bytes=999999-"}).status_code == 416


def test_legacy_records_versions_and_media_are_preserved_without_duplicate_cards(env):
    item, manager, root, _ = env
    doc = artifacts.create(title="Earlier roads", kind="html", content='<h1>Earlier</h1><img src="before.png">', project_id=item.id, workspace_record_id="old-roads")
    doc["workspace_bases"] = {"1": "reports/roads.html"}
    artifacts._save(doc)
    row = {"id": "old-roads", "title": "Earlier roads", "artifact_id": doc["id"], "artifact_version": 1, "imported_at": 1, "history": []}
    manager.save_workspace(item.id, [row], sharing=True)
    data = workspace.overview(item.id)
    assert data["records"][0]["id"] == "old-roads"
    assert doc["id"] not in {a["id"] for a in data["artifacts"]}
    assert manager.get_project(item.id).workspace_sharing_enabled is True
    assert manager.get_project(item.id).metadata["unrelated"] == 42
    assert "data:image/png" in artifacts.frame_html(artifacts.get(doc["id"]))
    client, base = client_for(item)
    assert client.get(base + "/records/old-roads").status_code == 200
    loaded = projects.ProjectManager(manager.projects_dir).get_project(item.id)
    assert loaded.workspace_records == [row]


def test_project_tool_reads_current_technical_docs_with_scope_trust_and_pagination(env):
    item, _, root, _ = env
    (root / "reports/roads.md").write_text("a" * 24000 + "The unresolved collision check", encoding="utf-8")
    box = Toolbox(toolsets=["core"], extra_tools=workspace.tools(), session_id="chat-a", project_root=str(root))
    result = box.run("project_workspace", {})
    assert result.ok and "reports/roads.md" in result.output
    first = json.loads(box.run("project_workspace", {"path": "reports/roads.md"}).output)
    assert first["next_offset"] == 24000
    final = json.loads(box.run("project_workspace", {"path": "reports/roads.md", "offset": 24000}).output)
    assert final["document"] == "The unresolved collision check" and final["next_offset"] is None
    assert policy.is_untrusted_source("project_workspace")
    assert policy.evaluate("memory_save", {"text": "claim"}, tainted_by=["project_workspace"], secrets=[]).action == "ask"
    other = Toolbox(toolsets=["core"], extra_tools=workspace.tools(), session_id="other", project_root=str(root))
    assert not other.run("project_workspace", {}).ok
    assert "save_project_report" not in box.names
    assert "project_workspace" not in Toolbox(toolsets=["web"], extra_tools=workspace.tools()).names


def test_screenshot_export_preserves_own_original_only_with_write_permission(env, monkeypatch):
    from agent.lean import observations
    _, _, root, _ = env
    monkeypatch.setattr(observations, "root", lambda: root.parent / "observations")
    own = observations.save(Image.new("RGB", (10, 10), "white"), "chat-a", "Road screenshot")
    foreign = observations.save(Image.new("RGB", (10, 10), "black"), "foreign", "Private screenshot")
    box = Toolbox(toolsets=["core"], extra_tools=workspace.tools(), session_id="chat-a", project_root=str(root))
    assert not box.run("save_project_image", {"observation_id": foreign["id"]}).ok
    assert not (root / "artifacts").exists()
    monkeypatch.setattr(config, "allow_file_write", False)
    assert not box.run("save_project_image", {"observation_id": own["id"]}).ok
    monkeypatch.setattr(config, "allow_file_write", True)
    result = box.run("save_project_image", {"observation_id": own["id"]})
    assert result.ok
    path = root / json.loads(result.output)["path"]
    assert path.read_bytes() == observations.load(own["id"], "chat-a")[1]
    assert not (root / ".echospeak").exists()


def test_workspace_does_not_query_activity_or_delete_execution_history(env, monkeypatch):
    item, _, _, store = env
    store.create_execution(thread_id="chat-a", session_id="chat-a", query="Review roads", project_id=item.id, active_project_id=item.id)
    history = store.list_executions("chat-a", limit=8)
    monkeypatch.setattr(store, "list_executions", lambda *a, **k: pytest.fail("Workspace must not query activity"))
    assert "activity" not in workspace.overview(item.id)
    assert history[0].query == "Review roads"


def test_report_revision_changes_only_with_its_html_or_local_media(env):
    item, _, root, _ = env
    paired_files(root)
    def report():
        return next(r for r in workspace.discover(item)["documents"] if r["path"] == "artifacts/roads.html")
    first = report()["revision"]
    (root / "unrelated.py").write_text("# Unrelated", encoding="utf-8")
    (root / "docs/roads.md").write_text("Updated technical notes", encoding="utf-8")
    assert report()["revision"] == first
    (root / "artifacts/media/after.png").write_bytes(b"Changed image")
    second = report()["revision"]
    assert second != first
    (root / "artifacts/roads.html").write_text("<title>Changed report</title>", encoding="utf-8")
    assert report()["revision"] != second


def test_project_identity_update_keeps_files_metadata_and_survives_reload(env):
    item, manager, root, _ = env
    from api.routes.projects import router
    import base64
    app = FastAPI(); app.include_router(router)
    client = TestClient(app)
    original = (root / "reports/before.png").read_bytes()
    logo = "data:image/png;base64," + base64.b64encode(original).decode("ascii")
    response = client.put(f"/projects/{item.id}", json={"name": "  Road studio  ", "logo_data_url": logo})
    assert response.status_code == 200
    saved = response.json()
    assert saved["name"] == "Road studio" and saved["logo_data_url"].startswith("data:image/png;base64,")
    assert saved["workspace_root"] == item.workspace_root
    assert saved["metadata"]["unrelated"] == 42
    assert manager.get_project(item.id).name == "Road studio"
    reloaded = projects.ProjectManager(manager.projects_dir).get_project(item.id)
    assert reloaded.logo_data_url == saved["logo_data_url"]
    assert (root / "reports/before.png").read_bytes() == original
    assert client.put(f"/projects/{item.id}", json={"logo_data_url": ""}).json()["logo_data_url"] == ""
    assert manager.get_project(item.id).name == "Road studio"


@pytest.mark.parametrize("patch", [
    {"name": "   "}, {"name": "x" * 121}, {"name": "bad\nname"},
    {"logo_data_url": "https://example.com/logo.png"},
    {"logo_data_url": "data:image/svg+xml;base64,PHN2Zy8+"},
    {"logo_data_url": "data:image/png;base64,bm90IGFuIGltYWdl"},
    {"logo_data_url": "data:image/png;base64,%%%"},
    {"logo_data_url": "data:image/png;base64," + "A" * 2_800_000},
])
def test_invalid_project_identity_never_partially_changes_saved_project(env, patch):
    item, manager, _, _ = env
    from api.routes.projects import router
    app = FastAPI(); app.include_router(router)
    before = manager.get_project(item.id).model_dump()
    response = TestClient(app).put(f"/projects/{item.id}", json={"name": "Should not persist", **patch})
    assert response.status_code in {400, 422}
    assert manager.get_project(item.id).model_dump() == before
    assert projects.ProjectManager(manager.projects_dir).get_project(item.id).model_dump() == before


def test_project_routes_keep_existing_api_auth_boundary(env, monkeypatch):
    from api.server import app
    item, _, _, _ = env
    monkeypatch.setattr(config, "api_auth_enabled", True)
    monkeypatch.setattr(config, "api_auth_localhost_bypass", False)
    monkeypatch.setattr(config, "api_auth_key", "workspace-test-key")
    client = TestClient(app, base_url="http://127.0.0.1")
    url = f"/projects/{item.id}/workspace"
    assert client.get(url).status_code == 401
    assert client.get(url, headers={"x-echospeak-key": "workspace-test-key"}).status_code == 200
