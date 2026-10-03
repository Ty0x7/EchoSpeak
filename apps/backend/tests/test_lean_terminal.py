"""Terminal: commands are never killed at their timeout, servers don't block the
agent, nothing waits for a keyboard, and results say what happened.

Runs real commands with the host backend (bash on Linux/macOS) and the sandbox
launch script under local bash: the same script `docker exec` runs.
"""

from __future__ import annotations

import os
import subprocess
import time
import uuid

import pytest

from agent.lean import terminal as term
from agent.lean.terminal import LAUNCH_SCRIPT, Terminal, failure_hint, interactive_problem, is_long_running, parse_launch, redirect_targets

pytestmark = pytest.mark.skipif(os.name == "nt", reason="uses bash; the PowerShell host path has its own tests")


@pytest.fixture
def project(tmp_path, monkeypatch):
    from config import config

    root = tmp_path / "proj"
    root.mkdir()
    monkeypatch.setattr(config, "terminal_execution_mode", "host", raising=False)
    monkeypatch.setattr(config, "file_tool_root", str(root), raising=False)
    monkeypatch.setattr(config, "file_tool_extra_roots", [], raising=False)
    monkeypatch.setattr(term, "_PROC_DIR", tmp_path / "procs")
    yield root
    term.stop_all_processes()
    term._PROCESSES.clear()


def _run(root, command: str, **extra) -> str:
    return Terminal(str(root)).run({"command": command, **extra})


def test_output_exit_code_and_stderr_come_back_separately(project):
    out = _run(project, "echo hello; echo careful >&2")
    assert out.startswith("[ok ·") and "hello" in out and "[stderr]\ncareful" in out
    failed = _run(project, "echo partial; exit 3")
    assert failed.startswith("[exit code 3 ·") and "partial" in failed


def test_missing_program_gets_a_hint(project):
    out = _run(project, "definitely-not-a-real-program-xyz --version")
    assert out.startswith("[exit code 127") and "Hint: that program isn't installed on this PC" in out


def test_a_slow_command_moves_to_the_background_instead_of_being_killed(project):
    started = time.perf_counter()
    out = _run(project, "echo started; sleep 2; echo finished > done.txt; echo all done", timeout=1)
    assert time.perf_counter() - started < 1.9
    assert "still running after 1s, so it keeps going in the background" in out and "started" in out
    process_id = out.split("process ", 1)[1].split(" ", 1)[0]
    time.sleep(2.3)
    status = Terminal(str(project)).process_output({"process_id": process_id})
    assert "finished (ok)" in status and "all done" in status
    assert (project / "done.txt").read_text().strip() == "finished"  # the work was not lost


def test_servers_start_in_the_background_without_blocking(project, monkeypatch):
    monkeypatch.setattr(term, "SERVER_WAIT", 1)
    (project / "app.log").write_text("ready\n")
    started = time.perf_counter()
    out = _run(project, "tail -f app.log")
    assert time.perf_counter() - started < 3
    assert "started in the background (it keeps running)" in out and "ready" in out
    process_id = out.split("process ", 1)[1].split(" ", 1)[0]
    assert "still running" in Terminal(str(project)).process_output({"process_id": process_id})
    assert Terminal(str(project)).process_stop({"process_id": process_id}) == f"Stopped process {process_id}."
    assert "finished" in Terminal(str(project)).process_output({"process_id": process_id})


def test_nothing_waits_for_a_keyboard(project):
    started = time.perf_counter()
    out = _run(project, 'read answer; echo "got:[$answer]"', timeout=30)
    assert time.perf_counter() - started < 5 and "got:[]" in out  # stdin is closed, not inherited
    assert _run(project, "vim notes.txt").startswith("Error: not run, because it is a full-screen program")
    assert "npm init -y" in _run(project, "npm init")
    assert "opens an interactive session" in _run(project, "python3")


def test_git_never_opens_an_editor(project):
    git = "git -c user.name=t -c user.email=t@example.com"
    _run(project, f"git init -q . && echo x > a.txt && git add a.txt")
    out = _run(project, f"{git} commit", timeout=20)
    assert out.startswith("[exit code") and "Hint: git commit needs the message" in out


def test_files_overwritten_by_redirection_can_be_undone(project):
    from agent.checkpoints import undo_last_change
    from agent.tools import bind_tool_execution_context, reset_tool_execution_context

    (project / "notes.txt").write_text("original\n")
    token = bind_tool_execution_context({"thread_id": "term-undo", "project_root": str(project)})
    try:
        _run(project, "echo replaced > notes.txt")
    finally:
        reset_tool_execution_context(token)
    assert (project / "notes.txt").read_text() == "replaced\n"
    assert "Successfully reverted" in undo_last_change("term-undo", str(project))
    assert (project / "notes.txt").read_text() == "original\n"


def test_too_many_background_processes_stops_the_new_one(project, monkeypatch):
    monkeypatch.setattr(term, "MAX_BACKGROUND", 0)
    out = _run(project, "sleep 5", timeout=1)
    assert out.startswith("[stopped after 1s") and "already running" in out
    assert term._PROCESSES == {}


# ── the sandbox launch script, run by local bash ────────────────────────

def _launch(command: str, wait: int) -> tuple[bool, int, str, str]:
    proc = subprocess.run(["bash", "-c", LAUNCH_SCRIPT, "_", uuid.uuid4().hex[:8], str(wait), command],
                          capture_output=True, text=True, timeout=wait + 10)
    return parse_launch(proc.stdout, proc.stderr)


def test_sandbox_launch_returns_output_exit_code_and_stderr():
    assert _launch("echo hi; echo oops >&2; exit 3", 5) == (True, 3, "hi", "oops")
    assert _launch("read x; echo got:$x", 5) == (True, 0, "got:", "")


def test_sandbox_launch_leaves_slow_commands_running():
    finished, pid, _, _ = _launch("sleep 3", 1)
    assert finished is False and pid > 0
    os.killpg(pid, 15)


# ── classification ──────────────────────────────────────────────────────

@pytest.mark.parametrize("command, expected", [
    ("npm run dev", True), ("pnpm dev", True), ("npx vite", True), ("vite --port 3000", True), ("npx vite build", False),
    ("python -m http.server 8000", True), ("uvicorn app:app --reload", True), ("docker compose up", True),
    ("docker compose up -d", False), ("tsc --watch", True), ("npm run build", False), ("npm test", False),
    ("pytest -q", False), ("Get-Content log.txt -Wait", True),
])
def test_long_running_commands(command, expected):
    assert is_long_running(command) is expected


@pytest.mark.parametrize("command, refused", [
    ("vim a.txt", True), ("less log.txt", True), ("python", True), ("python script.py", False), ("node", True),
    ("npm init", True), ("npm init -y", False), ("git add -p", True), ("git add .", False),
    ("$name = Read-Host 'Name'", True), ("git commit -m 'msg'", False),
])
def test_interactive_commands(command, refused):
    assert bool(interactive_problem(command)) is refused


def test_redirect_targets_and_hints():
    assert redirect_targets('echo hi > out.txt 2> err.log; cat a >> "my notes.md"') == ["out.txt", "my notes.md"]
    assert redirect_targets("Set-Content -Path config.json -Value $x; Get-Date | Out-File today.txt") == ["config.json", "today.txt"]
    assert redirect_targets("make > /dev/null 2>&1; dir > $null") == []
    assert "no &&" in failure_hint(1, "The token '&&' is not a valid statement separator in this version.", "host")
    assert "network=true" in failure_hint(1, "fatal: unable to access: Could not resolve host: github.com", "docker")
