"""Production closure: approval identity, concurrency, research, and verified writes."""

from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path

import pytest


def _coding_agent(tmp_path, monkeypatch, project_root, *, session="s1"):
    from agent.active_work import ActiveWorkStore
    from agent.core import EchoSpeakAgent
    from agent.projects import ProjectManager
    from agent.session_memory import SessionMemoryDistiller
    from agent.state import StateStore
    from config import ModelProvider, config
    import agent.projects as projects_mod
    import agent.state as state_mod

    manager = ProjectManager(tmp_path / "projects")
    project = manager.attach_folder(str(project_root), name="PC", trust_state="trusted")
    runtime = StateStore(tmp_path / "runtime")
    monkeypatch.setattr(projects_mod, "_project_manager", manager)
    monkeypatch.setattr(state_mod, "_state_store", runtime)
    monkeypatch.setattr(config, "enable_system_actions", True)
    monkeypatch.setattr(config, "allow_file_write", True)
    monkeypatch.setattr(config, "allow_terminal_commands", False)
    monkeypatch.setattr(config, "disable_native_tool_calling", True)
    monkeypatch.setattr(config, "verification_telemetry_enabled", False)
    monkeypatch.setattr(config, "file_tool_root", str(project_root))

    agent = EchoSpeakAgent(
        memory_path=str(tmp_path / "memory"),
        llm_provider=ModelProvider.OPENAI,
        manage_background_services=False,
    )
    agent._session_memory = SessionMemoryDistiller(tmp_path / "sessions")
    agent._active_work_store = ActiveWorkStore(tmp_path / "active-work")
    agent._allow_llm_tool_calling = lambda: False
    agent.select_thread_runtime(session)
    agent.activate_project(project.id)
    return agent, runtime, project, manager


def test_duplicate_claim_pending_approval_is_idempotent(tmp_path):
    from agent.state import StateStore

    runtime = StateStore(tmp_path / "rt")
    approval = runtime.create_approval(
        thread_id="t1",
        session_id="t1",
        project_id="p1",
        tool="file_write",
        kwargs={"path": "a.txt", "content": "x"},
        original_input="write a",
        preview="write",
        summary="write",
        canonical_arguments_hash="abc",
    )
    first = runtime.claim_pending_approval(approval.id)
    second = runtime.claim_pending_approval(approval.id)
    assert first is not None and first.status == "consuming"
    assert second is None  # second concurrent/duplicate claim fails closed


def test_mutation_precondition_ignores_freeze_metadata(tmp_path, monkeypatch):
    """Regression: path_basename freeze fields must not block legitimate writes."""
    from agent.tools import _mutation_precondition_denial, update_tool_execution_context, _mutation_path_version

    target = tmp_path / "index.html"
    target.write_text("<html></html>", encoding="utf-8")
    entry = _mutation_path_version(target, "path")
    # Same content identity + extra freeze fields (as stored on ApprovalRecord)
    expected = {
        "version": 2,
        "entries": [entry],
        "tool": "file_write",
        "path_basename": "index.html",
        "original_input_sha256": "deadbeef",
    }
    update_tool_execution_context(mutation_precondition=expected)
    assert _mutation_precondition_denial("file_write") == ""
    # Real content change must still deny
    target.write_text("<html>changed</html>", encoding="utf-8")
    denial = _mutation_precondition_denial("file_write")
    assert "changed after approval" in denial


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("file_delete", ("path",)),
        ("file_move", ("src", "dst")),
        ("file_copy", ("src", "dst")),
        ("file_mkdir", ("path",)),
    ],
)
def test_all_filesystem_mutation_arguments_are_versioned_at_tool_boundary(
    tmp_path, tool_name, arguments
):
    from agent.tools import (
        bind_tool_execution_context,
        _mutation_path_version,
        _mutation_precondition_denial,
        reset_tool_execution_context,
    )

    paths = {}
    entries = []
    for argument in arguments:
        path = tmp_path / f"{argument}.txt"
        if argument != "dst":
            path.write_text(f"approved-{argument}", encoding="utf-8")
        paths[argument] = path
        entries.append(_mutation_path_version(path, argument))
    token = bind_tool_execution_context(
        {"mutation_precondition": {"version": 2, "entries": entries}}
    )
    try:
        assert _mutation_precondition_denial(tool_name) == ""

        changed_argument = arguments[-1]
        paths[changed_argument].write_text("changed-after-approval", encoding="utf-8")
        assert "changed after approval" in _mutation_precondition_denial(tool_name)
    finally:
        reset_tool_execution_context(token)


def test_skill_audit_no_disabled_executable(tmp_path, monkeypatch):
    from agent.skill_status_audit import audit_all_skills

    rows = audit_all_skills(
        available_capabilities={"research", "approvals"},
        available_artifacts=set(),
    )
    for r in rows:
        if r["status"] in {"disabled", "invalid", "deprecated", "prompt_only", "blocked_missing_tool", "blocked_missing_model", "blocked_missing_artifact"}:
            assert r["executable"] is False
        if r["status"] == "executable":
            assert r["executable"] is True


