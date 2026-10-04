import json
from pathlib import Path

import pytest


def test_desktop_project_and_routine_roots_follow_configured_data(monkeypatch, tmp_path: Path):
    import config
    from agent.projects import ProjectManager
    from agent.routines import RoutineManager

    monkeypatch.setenv("ECHOSPEAK_RUNTIME_KIND", "desktop")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    assert ProjectManager().projects_dir == tmp_path / "projects"
    assert RoutineManager().routines_dir == tmp_path / "routines"


def test_corrupt_session_registry_is_preserved_and_quarantined(tmp_path: Path):
    from agent.threads import ThreadManager

    path = tmp_path / "threads.json"
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="authoritative file was not overwritten"):
        ThreadManager(path)
    assert path.read_text(encoding="utf-8") == "{not-json"
    guides = list((tmp_path / "corrupt-state").glob("*/RECOVERY.txt"))
    assert guides and "repair or restore" in guides[0].read_text(encoding="utf-8")


def test_startup_readiness_reports_real_steps_without_provider_gate(monkeypatch, tmp_path: Path):
    import agent.startup_readiness as readiness

    monkeypatch.setattr(readiness, "DATA_DIR", tmp_path)
    monkeypatch.setenv("ECHOSPEAK_DESKTOP_INSTANCE_ID", "instance-test")
    for name in (
        "_projects", "_sessions", "_active_scope", "_tools", "_skills", "_runtime_state",
        "_memory", "_jobs", "_media", "_routines", "_heartbeat", "_schema",
    ):
        monkeypatch.setattr(readiness, name, lambda: {"detail": "Ready"})
    payload = readiness.build_startup_readiness()
    assert payload["core_ready"] is True
    assert payload["instance_id"] == "instance-test"
    assert payload["completed_steps"] == payload["total_steps"]
    assert not any(item["key"] == "provider" for item in payload["components"])


def test_browser_mode_also_keeps_projects_in_the_data_folder(monkeypatch, tmp_path: Path):
    import config
    from agent import projects as projects_mod

    monkeypatch.delenv("ECHOSPEAK_RUNTIME_KIND", raising=False)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    legacy = tmp_path / "legacy-projects"
    legacy.mkdir()
    (legacy / "p1.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(projects_mod, "LEGACY_PROJECTS_DIR", legacy)
    manager = projects_mod.ProjectManager()
    assert manager.projects_dir == tmp_path / "projects"
    # A custom data folder never adopts the legacy dev projects.
    assert not (tmp_path / "projects" / "p1.json").exists()
