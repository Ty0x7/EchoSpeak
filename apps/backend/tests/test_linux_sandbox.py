"""The Linux terminal sandbox (bubblewrap): mount order, hidden credentials, and live confinement."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from agent.lean import linux_sandbox as sb


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id_ed25519").write_text("PRIVATE KEY", encoding="utf-8")
    (home / ".netrc").write_text("machine x password y", encoding="utf-8")
    (home / "notes.txt").write_text("hello", encoding="utf-8")
    return home


def _mounts(args: list[str]) -> list[tuple[str, str]]:
    out, i = [], 0
    while i < len(args):
        if args[i] in {"--bind", "--ro-bind"}:
            out.append((args[i], args[i + 2]))
            i += 3
        elif args[i] == "--tmpfs":
            out.append(("--tmpfs", args[i + 1]))
            i += 2
        else:
            i += 1
    return out


def test_credentials_stay_hidden_when_the_workspace_is_the_home_folder(tmp_path):
    home = _home(tmp_path)
    mounts = _mounts(sb.wrap(["true"], cwd=home, writable=[home], home=home))
    assert mounts.index(("--bind", str(home))) < mounts.index(("--tmpfs", str(home / ".ssh")))
    assert ("--ro-bind", str(home / ".netrc")) in mounts  # /dev/null over the file


def test_a_workspace_inside_a_hidden_folder_stays_usable(tmp_path):
    home = _home(tmp_path)
    data = tmp_path / "data"
    project = data / "projects" / "demo"
    project.mkdir(parents=True)
    mounts = _mounts(sb.wrap(["true"], cwd=project, writable=[project], hidden=[data], home=home))
    assert mounts.index(("--tmpfs", str(data))) < len(mounts) - 1 - mounts[::-1].index(("--bind", str(project)))


def test_network_is_off_unless_allowed(tmp_path):
    assert "--unshare-net" in sb.wrap(["true"], cwd=tmp_path, writable=[tmp_path], home=tmp_path)
    assert "--unshare-net" not in sb.wrap(["true"], cwd=tmp_path, writable=[tmp_path], network=True, home=tmp_path)


def test_socket_variables_are_dropped():
    env = sb.sandbox_env({"PATH": "/usr/bin", "SSH_AUTH_SOCK": "/run/user/1/ssh", "DBUS_SESSION_BUS_ADDRESS": "x"})
    assert env == {"PATH": "/usr/bin"}


live = pytest.mark.skipif(not sb.available()[0], reason="bubblewrap can't create a sandbox on this machine")


def _run(tmp_path: Path, script: str, *, network: bool = False) -> subprocess.CompletedProcess:
    home = _home(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    args = sb.wrap(["bash", "-c", script], cwd=project, writable=[project], network=network, home=home)
    return subprocess.run(args, capture_output=True, text=True, timeout=30,
                          env=sb.sandbox_env({**os.environ, "HOME": str(home)}))


@live
def test_live_writes_land_only_in_the_workspace(tmp_path):
    result = _run(tmp_path, 'echo ok > out.txt; echo x > "$HOME/escape.txt"; echo x > /etc/escape; cat out.txt')
    assert "ok" in result.stdout
    assert (tmp_path / "project" / "out.txt").read_text().strip() == "ok"
    assert not (tmp_path / "home" / "escape.txt").exists()
    assert "Read-only file system" in result.stderr


@live
def test_live_credentials_are_hidden_but_other_home_files_are_readable(tmp_path):
    result = _run(tmp_path, 'ls -A "$HOME/.ssh" | wc -l; cat "$HOME/.netrc"; cat "$HOME/notes.txt"')
    assert result.stdout.split() == ["0", "hello"]
    assert "password" not in result.stdout  # the file is masked: reading it fails ("Permission denied")


@live
def test_live_no_network_and_no_view_of_other_processes(tmp_path):
    result = _run(tmp_path, "cat /proc/net/dev | grep -c ':' ; ls /proc | grep -c '^[0-9]'")
    interfaces, processes = (int(x) for x in result.stdout.split())
    assert interfaces == 1  # loopback only
    assert processes < 10


@live
def test_live_no_new_privileges(tmp_path):
    result = _run(tmp_path, "grep NoNewPrivs /proc/self/status")
    assert result.stdout.split()[-1] == "1"


# ── Terminal wiring: where commands run, approvals, and no silent unconfined fallback ──

from agent.lean import terminal as term  # noqa: E402


def _mode(monkeypatch, configured: str, *, bwrap: bool, docker: bool = False) -> None:
    monkeypatch.setattr(term.config, "terminal_execution_mode", configured, raising=False)
    monkeypatch.setattr(term, "bwrap_available", lambda: bwrap)
    monkeypatch.setattr(term, "docker_available", lambda max_age=30.0: docker)


def test_auto_prefers_bwrap_on_linux_then_docker_then_host(monkeypatch):
    _mode(monkeypatch, "auto", bwrap=True, docker=True)
    assert term.resolved_mode() == "bwrap"
    _mode(monkeypatch, "auto", bwrap=False, docker=True)
    assert term.resolved_mode() == "docker"
    _mode(monkeypatch, "auto", bwrap=False, docker=False)
    assert term.resolved_mode() == "host"


def test_sandboxed_commands_ask_only_when_they_leave_the_sandbox(monkeypatch):
    _mode(monkeypatch, "auto", bwrap=True)
    monkeypatch.setattr(term, "network_policy", lambda: "ask")
    assert term.approval_for({"command": "pytest -q"}) == (False, "")
    assert term.approval_for({"command": "pip install x", "network": True})[0]
    assert term.approval_for({"command": "ls", "where": "host"})[0]
    assert term.approval_for({"command": "rm -rf build"})[0]  # still your real files


def test_explicit_bwrap_mode_refuses_instead_of_running_unconfined(monkeypatch, tmp_path):
    _mode(monkeypatch, "bwrap", bwrap=False)
    monkeypatch.setattr(sb, "available", lambda max_age=300.0: (False, "user namespaces are disabled"))
    launched = []
    monkeypatch.setattr(term, "_host_launch", lambda *a, **k: launched.append(a))
    t = term.Terminal(str(tmp_path))
    monkeypatch.setattr(t, "_cwd", lambda raw: (tmp_path, ""))
    out = t.run({"command": "echo hi"})
    assert "Linux sandbox can't start" in out and not launched


@live
def test_live_terminal_run_is_confined(monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    _mode(monkeypatch, "auto", bwrap=True)
    monkeypatch.setattr(term, "network_policy", lambda: "ask")
    monkeypatch.setattr(term, "workspace_roots", lambda root="": [project])
    t = term.Terminal(str(project))
    monkeypatch.setattr(t, "_cwd", lambda raw: (project, ""))
    outside = Path.home() / f".echospeak-sandbox-probe-{os.getpid()}"
    out = t.run({"command": f"echo inside > made.txt; echo x > {outside}; cat made.txt"})
    assert (project / "made.txt").read_text().strip() == "inside"
    assert not outside.exists()
    assert "Read-only file system" in out
