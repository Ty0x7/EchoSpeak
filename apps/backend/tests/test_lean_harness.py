"""Lean coding harness, terminal, thinking controls, and automations."""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from agent.lean import automations
from agent.lean.approvals import tool_needs_approval
from agent.lean.coding import file_edit, file_find, file_read, file_search, project_overview
from agent.lean.provider import Endpoint, reasoning_effort_for
from agent.lean.terminal import _NEVER, Terminal, mount_plan, to_container_path, translate_windows_paths, workspace_roots
from agent.tools import _tool_execution_context


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "node_modules" / "dep").mkdir(parents=True)
    (root / "src" / "math.js").write_text("function avg(n) {\n  return sum(n) / n.length;\n}\nfunction sum(n) { return 0; }\n", encoding="utf-8")
    (root / "node_modules" / "dep" / "index.js").write_text("function avg() {}\n", encoding="utf-8")
    token = _tool_execution_context.set({"strict_scope": True, "project_root": str(root), "lean_runtime": True})
    yield root
    _tool_execution_context.reset(token)


def test_file_read_numbers_lines_and_pages(project):
    out = file_read({"path": "src/math.js", "offset": 2, "limit": 1})
    assert "2|   return sum(n) / n.length;" in out
    assert "more lines" in out


def test_file_edit_replaces_exact_text_and_shows_diff(project):
    out = file_edit({"path": "src/math.js", "old_text": "return 0;", "new_text": "return n.reduce((a, b) => a + b, 0);"})
    assert out.startswith("Edited")
    assert "+function sum(n) { return n.reduce" in out
    assert "reduce" in (project / "src" / "math.js").read_text(encoding="utf-8")


def test_file_edit_tolerates_pasted_line_numbers(project):
    out = file_edit({"path": "src/math.js", "old_text": "2|   return sum(n) / n.length;", "new_text": "  return sum(n) / Math.max(1, n.length);"})
    assert out.startswith("Edited"), out


def test_file_edit_explains_missing_and_ambiguous_text(project):
    assert "not found" in file_edit({"path": "src/math.js", "old_text": "nope", "new_text": "x"})
    (project / "dup.txt").write_text("a\na\n", encoding="utf-8")
    assert "appears 2 times" in file_edit({"path": "dup.txt", "old_text": "a", "new_text": "b"})
    assert file_edit({"path": "dup.txt", "old_text": "a", "new_text": "b", "replace_all": True}).startswith("Edited")


def test_paths_outside_allowed_folders_are_refused(project):
    assert file_read({"path": str(Path.home() / "secret.txt")}).startswith("Error: path not allowed")


def test_search_and_find_skip_dependency_folders(project):
    hits = file_search({"pattern": r"function avg"})
    assert "src" in hits and "node_modules" not in hits
    found = file_find({"pattern": "*.js"})
    assert "src/math.js" in found and "node_modules" not in found
    assert "src/" in project_overview(str(project))


def test_never_list_blocks_system_destruction():
    for bad in ["format C:", "diskpart", "rm -rf /", "Remove-Item C:\\ -Recurse", "bcdedit /set", "shutdown /s"]:
        assert _NEVER.search(bad), bad
    for ok in ["npm install", "git status", "python -m pytest", "rm -rf node_modules", "Get-ChildItem | Select -First 3"]:
        assert not _NEVER.search(ok), ok


def test_dangerous_terminal_commands_need_approval():
    class E:
        risk_level = "destructive"
        is_action = True
        category = "system"
        origin = "native"

    assert tool_needs_approval(E(), "terminal", {"command": "git push origin main"})[0]
    assert tool_needs_approval(E(), "terminal", {"command": "npm install && rm build.txt"})[0]
    assert not tool_needs_approval(E(), "terminal", {"command": "npm test"})[0]


def test_container_path_translation(tmp_path):
    root = tmp_path / "Desktop"
    (root / "app").mkdir(parents=True)
    plan = [(root, "/work/Desktop")]
    assert to_container_path(root / "app", plan) == "/work/Desktop/app"
    cmd = translate_windows_paths(f"node {root}\\app\\x.js", plan)
    assert cmd == "node /work/Desktop/app/x.js"


def test_workspace_roots_drop_nested_and_home(tmp_path, monkeypatch):
    from config import config

    outer = tmp_path / "work"
    inner = outer / "proj"
    inner.mkdir(parents=True)
    monkeypatch.setattr(config, "file_tool_root", str(outer), raising=False)
    monkeypatch.setattr(config, "file_tool_extra_roots", [str(inner), str(Path.home())], raising=False)
    roots = workspace_roots(str(inner))
    assert roots == [outer.resolve()]
    assert [t for _r, t in mount_plan(str(inner))] == ["/work/work"]


@pytest.mark.skipif(os.name != "nt", reason="host shell test uses PowerShell")
def test_host_terminal_supports_pipes_and_variables(project, monkeypatch):
    from config import config

    monkeypatch.setattr(config, "terminal_execution_mode", "host", raising=False)
    out = Terminal(str(project)).run({"command": "$x = 21; Write-Output ($x * 2) | Out-String"})
    assert "[ok" in out and "42" in out


def test_thinking_toggle_maps_to_reasoning_effort():
    local = Endpoint("http://x/v1", "", "google/gemma-4-e4b", "lmstudio", True)
    assert reasoning_effort_for(local, False, "high") == "none"
    assert reasoning_effort_for(local, True, "extra_high") == "high"
    cloud = Endpoint("https://api.openai.com/v1", "k", "gpt-4o-mini", "openai", False)
    assert reasoning_effort_for(cloud, True, "high") == ""


def test_routine_runs_on_lean_loop_and_delivers(monkeypatch):
    calls = {}

    def fake_run(agent, **kwargs):
        calls.update(kwargs)
        return {"response": "Done: 3 headlines.", "success": True, "execution_id": "exec-1"}

    sent = []
    monkeypatch.setattr(automations, "run_lean_query", fake_run)
    monkeypatch.setattr(automations, "deliver", lambda text, channels, label: sent.append((text, channels)) or channels)
    result = automations.run_automation(
        agent_factory=lambda session_id: object(),
        name="Morning briefing",
        prompt="Summarize news",
        session_id="s1",
        agent_id="scout",
        channels=["discord"],
    )
    assert result["success"] and result["delivered"] == ["discord"]
    assert calls["source"] == "routine" and calls["persona_id"] == "scout"
    assert "automated run" in calls["message"] and "Summarize news" in calls["message"]
    assert sent == [("Done: 3 headlines.", ["discord"])]
