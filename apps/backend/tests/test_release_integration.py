"""Regressions at the boundary between the 11.8 harness and project workspace."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from agent import a2a_trust
from agent.lean import approvals, policy, settings
from agent.lean.job import Job
from agent.lean.loop import TurnResult
from agent.lean.runtime import LeanSession


@pytest.mark.parametrize("mode", ["smart", "always", "never"])
@pytest.mark.parametrize("name", ["ask_remote_agent", "a2a_delegate"])
def test_remote_task_submission_always_requires_owner_approval(monkeypatch, mode, name):
    monkeypatch.setattr(settings, "approval_mode", lambda: mode)
    # Native tools do not carry the registry's is_action attribute.
    needs, reason = approvals.tool_needs_approval(SimpleNamespace(), name, {})
    assert needs and "independent agent" in reason


def test_legacy_agent_results_keep_the_same_trust_boundary_as_new_agent_results():
    entry = SimpleNamespace(origin="skill", is_action=True, risk_level="moderate", category="a2a")
    assert policy.is_external_action("a2a_delegate", entry)
    for name in ("a2a_delegate", "a2a_discover", "ask_remote_agent"):
        assert policy.is_untrusted_source(name, entry)


def test_legacy_remote_reply_preserves_text_and_nonterminal_status():
    assert a2a_trust._reply_text({"id": "old-task", "status": "completed", "messages": [
        {"role": "user", "parts": [{"type": "text", "text": "Question"}]},
        {"role": "agent", "parts": [{"type": "text", "text": "Actual answer"}]},
    ]}) == "Actual answer"
    assert "working" in a2a_trust._reply_text({"id": "old-task", "status": "working"})


@pytest.mark.parametrize("name", ["project_workspace", "save_project_image", "save_project_report"])
def test_project_report_bookkeeping_cannot_verify_an_implementation_task(name):
    session = LeanSession.__new__(LeanSession)
    session.results = []
    session.job = Job(goal="Fix the application bug")
    task = session.job.open_subtask(owner="Echo", task="Fix the application bug", assigned_by="owner")
    session._active_subtask = task
    session._name_of = lambda agent_id: "Echo"
    session._add_result(TurnResult(text="Recorded findings", success=True, message_id="report", agent_id="echo",
                                  timeline=[{"kind": "tool", "name": name, "status": "done"}]))
    session.job.close_subtask(task, done=True, summary="Claimed fixed")
    assert session.job.successful_actions() == 0
    assert task in session.job.unbacked_tasks()


def test_server_runtime_and_all_bundled_skills_import_without_langchain(tmp_path):
    backend = Path(__file__).resolve().parents[1]
    code = r'''
import importlib.abc
import importlib.util
from pathlib import Path
import socket
import sys

class NoLangChain(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith("langchain"):
            raise ImportError("LangChain must not be required by the release: " + fullname)

def offline(*args, **kwargs):
    raise AssertionError("This import smoke check must not connect to any service")

sys.meta_path.insert(0, NoLangChain())
socket.socket.connect = offline
socket.create_connection = offline
import api.server
import agent.lean.runtime
from agent.tool_decorator import Tool
count = 0
for path in sorted(Path("skills").glob("*/tools.py")):
    name = "release_skill_" + path.parent.name
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    assert any(isinstance(value, Tool) for value in vars(module).values()), path
    count += 1
assert count >= 9, count
print("Server, lean runtime and", count, "skill packages imported without LangChain or network")
'''
    env = dict(os.environ, ECHOSPEAK_DATA_DIR=str(tmp_path / "clean-data"), PYTHONPATH=str(backend), MCP_SERVERS="{}")
    result = subprocess.run([sys.executable, "-c", code], cwd=backend, env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
