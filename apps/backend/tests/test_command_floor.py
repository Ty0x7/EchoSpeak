"""The command floor: system, disk and home-folder wipes always ask, in every approval mode."""
from __future__ import annotations

import pytest

from agent.lean import approvals, settings

CATASTROPHIC = [
    "rm -rf /", "rm -rf /*", "sudo rm -rf --no-preserve-root /", "rm -fr ~", "rm -rf ~/", "rm -r -f $HOME",
    'rm -rf "$HOME"', "rm --recursive --force /etc", "cd /tmp && rm -rf /usr", "rm -Rf /home",
    "mkfs.ext4 /dev/sda1", "mkfs -t xfs /dev/nvme0n1p2", "wipefs -a /dev/sda", "mkswap /dev/sdb",
    "dd if=/dev/zero of=/dev/nvme0n1 bs=1M", "cat image > /dev/sda", ":(){ :|:& };:", "chmod -R 777 /",
    "chown -R nobody /", "Remove-Item -Recurse -Force C:\\", "Format-Volume -DriveLetter D", "format c: /q",
    "rd /s /q C:\\",
]
ORDINARY = [
    "rm -rf build", "rm -rf ./node_modules", "rm -rf /tmp/echo-test", "rm -rf ~/projects/app/dist",
    "rm file.txt", "ls -la /", "du -sh ~", "dd if=disk.img of=copy.img", "chmod -R 755 ./scripts",
    "echo hi > /dev/null", "git clean -fdx", "pytest -q", "find . -name '*.pyc'", "chmod 644 /etc/hosts.bak",
    "docker rm -f web",
]


@pytest.mark.parametrize("command", CATASTROPHIC)
def test_catastrophic_commands_are_on_the_floor(command):
    assert approvals.catastrophic_command(command), command
    assert approvals.dangerous_command(command)
    # Never coverable by "Always allow" or autonomous mode.
    assert approvals.scope_for("terminal", {"command": command}, "outside the sandbox") is None


@pytest.mark.parametrize("command", ORDINARY)
def test_ordinary_work_is_not_on_the_floor(command):
    assert not approvals.catastrophic_command(command), command


@pytest.mark.parametrize("mode", ["never", "smart", "always", "autonomous"])
def test_the_floor_asks_in_every_mode(monkeypatch, mode):
    monkeypatch.setattr(settings, "approval_mode", lambda: mode)
    needs, reason = approvals.tool_needs_approval(None, "terminal", {"command": "rm -rf ~"})
    assert needs and "always asks" in reason
    # Outside the floor, "never" still means never.
    if mode == "never":
        assert approvals.tool_needs_approval(None, "terminal", {"command": "rm -rf build"}) == (False, "")
