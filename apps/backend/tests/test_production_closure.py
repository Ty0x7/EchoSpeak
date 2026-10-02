"""Production-closure regressions: named-file pin, approval identity, skills truth, research artifacts."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest


def test_explicit_file_excludes_and_named_pin():
    from agent.core import EchoSpeakAgent
    from config import ModelProvider

    agent = EchoSpeakAgent.__new__(EchoSpeakAgent)
    files = ["proj/index.html", "proj/game.js", "proj/style.css"]
    named = agent._explicit_files_named_in_request(
        "Change the button text in index.html. Do not edit game.js.",
        files,
    )
    assert [Path(f).name for f in named] == ["index.html"]
    assert agent._file_write_path_allowed_by_request(
        "Change the button text in index.html.", "proj/index.html", files
    )
    assert not agent._file_write_path_allowed_by_request(
        "Change the button text in index.html. Do not edit game.js.",
        "proj/game.js",
        files,
    )
    assert not agent._file_write_path_allowed_by_request(
        "Change the button text in index.html.",
        "proj/game.js",
        files,
    )


def test_skill_status_audit_classifies():
    from agent.skill_status_audit import audit_all_skills

    rows = audit_all_skills(available_capabilities=set(), available_artifacts=set())
    assert rows
    # Disabled packages if any must not be executable
    for r in rows:
        if r["status"] == "disabled":
            assert r["executable"] is False


def test_research_artifact_ownership_and_lookup(tmp_path, monkeypatch):
    import agent.research_artifacts as ra

    monkeypatch.setattr(ra, "_ROOT", tmp_path / "arts")
    art = ra.build_research_artifact_from_tool_output(
        output="Findings https://example.com/a and https://example.com/b about oilers.",
        query="edmonton oilers",
        project_id="p1",
        session_id="s1",
        execution_id="e1",
        tool_run_id="tr1",
        objective="research oilers",
        verified=True,
    )
    saved = ra.save_research_artifact(art)
    assert saved.status == "ready"
    assert saved.citations
    found = ra.find_compatible_research_artifact(
        project_id="p1", session_id="s1", objective="research oilers highlights"
    )
    assert found is not None and found.id == saved.id
    # Wrong project must not match when project_id set on artifact
    assert ra.find_compatible_research_artifact(project_id="p2", objective="oilers") is None


def test_parallel_orchestrator_route_is_retired():
    from api.server import app

    assert "/orchestrate" not in {route.path for route in app.routes}
