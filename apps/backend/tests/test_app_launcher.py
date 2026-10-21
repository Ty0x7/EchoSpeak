"""App-launch permission failures and Windows Store resolution, with no real launches."""

import json
import subprocess
from types import SimpleNamespace

import pytest

from agent import app_launcher, tools
from agent.lean.toolbox import Toolbox
from config import config

MINECRAFT_ID = "Microsoft.4297127D64EC6_8wekyb3d8bbwe!Minecraft"


@pytest.fixture(autouse=True)
def launch_settings(monkeypatch):
    monkeypatch.setattr(config, "enable_system_actions", True)
    monkeypatch.setattr(config, "allow_open_application", True)
    monkeypatch.setattr(config, "open_application_blocklist", ["regedit", "gpedit.msc"])
    monkeypatch.setattr(config, "open_application_favorites", [])
    monkeypatch.setattr(config, "allow_desktop_automation", True)
    monkeypatch.setattr(config, "allow_file_write", True)


@pytest.fixture(autouse=True)
def desktop(tmp_path, monkeypatch):
    # Never read or open the real desktop's shortcuts.
    folder = tmp_path / "Desktop"
    folder.mkdir()
    monkeypatch.setattr(app_launcher, "desktop_dirs", lambda: [folder])
    return folder


def test_blocked_app_never_resolves_or_launches(monkeypatch):
    monkeypatch.setattr(tools.subprocess, "Popen", lambda *a, **kw: pytest.fail("Unexpected launch"))
    monkeypatch.setattr(app_launcher, "installed_app_id", lambda *a: pytest.fail("Unexpected discovery"))
    for name in ("regedit", "REGEDIT.exe", "C:/Windows/regedit.exe", "gpedit"):
        result = tools.open_application.invoke({"app": name})
        assert result.startswith("Error:") and "Blocked apps" in result, name
        assert "Do not open it another way" in result


def test_blocked_shortcut_name_is_refused(monkeypatch, desktop):
    (desktop / "regedit.lnk").write_bytes(b"shortcut")
    monkeypatch.setattr(app_launcher.subprocess, "Popen", lambda *a, **kw: pytest.fail("Unexpected launch"))
    assert "Blocked apps" in tools.open_application.invoke({"app": "regedit"})


def test_unlisted_app_launches_without_any_whitelist(monkeypatch):
    launches = []
    monkeypatch.setattr(tools.subprocess, "Popen", lambda *a, **kw: launches.append(a))
    result = tools.open_application.invoke({"app": "Notepad"})
    assert "launch requested" in result and launches[0][0] == ["notepad.exe"]


def test_unknown_app_fails_clearly_without_mentioning_a_whitelist(monkeypatch):
    monkeypatch.setattr(tools.subprocess, "Popen", missing_executable)
    monkeypatch.setattr(app_launcher, "installed_app_id", lambda name: None)
    result = tools.open_application.invoke({"app": "Totally Not Installed"})
    assert result.startswith("Error: Application was not found") and "allow" not in result.lower()


@pytest.mark.parametrize("flag", ["enable_system_actions", "allow_open_application"])
def test_launching_still_needs_the_permission_switches(monkeypatch, flag):
    monkeypatch.setattr(config, flag, False)
    monkeypatch.setattr(tools.subprocess, "Popen", lambda *a, **kw: pytest.fail("Unexpected launch"))
    assert tools.open_application.invoke({"app": "Minecraft Launcher"}).startswith("Error: Application launching is disabled")


def test_allowed_executable_uses_clean_env_and_no_console(monkeypatch):
    monkeypatch.setenv("API_AUTH_KEY", "test-private-key")
    launches = []
    monkeypatch.setattr(tools.subprocess, "Popen", lambda *a, **kw: launches.append((a, kw)))
    result = tools.open_application.invoke({"app": "Notepad"})
    assert "launch requested" in result
    assert "take_screenshot" in result
    assert launches[0][0][0] == ["notepad.exe"]
    assert "API_AUTH_KEY" not in launches[0][1]["env"]
    assert launches[0][1]["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)


def missing_executable(*args, **kwargs):
    raise FileNotFoundError("No executable by that display name")


def test_allowed_store_app_resolves_and_launches_its_registration(monkeypatch):
    monkeypatch.setattr(tools.subprocess, "Popen", missing_executable)
    resolved = []
    monkeypatch.setattr(app_launcher, "installed_app_id", lambda name: resolved.append(name) or MINECRAFT_ID)
    launches = []
    monkeypatch.setattr(tools.os, "startfile", launches.append, raising=False)
    result = tools.open_application.invoke({"app": "Minecraft Launcher"})
    assert resolved == ["Minecraft Launcher"]
    assert launches == [f"shell:AppsFolder\\{MINECRAFT_ID}"]
    assert "launch requested" in result


@pytest.mark.parametrize("app_id, extra, expected", [
    (None, None, "Application was not found"),
    (MINECRAFT_ID, "--bad-option", "do not support extra arguments"),
])
def test_missing_or_incompatible_store_launch_fails_clearly(monkeypatch, app_id, extra, expected):
    monkeypatch.setattr(tools.subprocess, "Popen", missing_executable)
    monkeypatch.setattr(app_launcher, "installed_app_id", lambda name: app_id)
    monkeypatch.setattr(tools.os, "startfile", lambda *a: pytest.fail("Unexpected Store launch"), raising=False)
    assert expected in tools.open_application.invoke({"app": "Minecraft Launcher", "args": extra})


def test_blocklist_also_covers_the_notepad_helper(monkeypatch):
    monkeypatch.setattr(config, "open_application_blocklist", ["notepad"])
    monkeypatch.setattr(tools.subprocess, "Popen", lambda *a, **kw: pytest.fail("Unexpected launch"))
    token = tools.bind_tool_execution_context({"allowed_tool_names": ["notepad_write"], "thread_id": "test"})
    try:
        assert "Blocked apps" in tools.notepad_write.invoke({"content": "test"})
    finally:
        tools.reset_tool_execution_context(token)


def mock_catalog(monkeypatch, rows, returncode=0):
    calls = []
    def run(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=returncode, stdout=json.dumps(rows))
    monkeypatch.setattr(app_launcher.subprocess, "run", run)
    return calls


def test_start_menu_lookup_uses_exact_name_and_static_script(monkeypatch):
    calls = mock_catalog(monkeypatch, [{"Name": "Minecraft Launcher", "AppID": MINECRAFT_ID}])
    assert app_launcher.installed_app_id("  MINECRAFT  launcher ") == MINECRAFT_ID
    assert app_launcher.installed_app_id("Minecraft") is None
    injected = "Minecraft'; Start-Process cmd; #"
    assert app_launcher.installed_app_id(injected) is None
    assert all(injected not in call[0][0][-1] for call in calls)
    assert all(call[1]["timeout"] == 10 for call in calls)


def test_lookup_accepts_single_powershell_object(monkeypatch):
    mock_catalog(monkeypatch, {"Name": "Minecraft Launcher", "AppID": MINECRAFT_ID})
    assert app_launcher.installed_app_id("Minecraft Launcher") == MINECRAFT_ID


def test_lookup_does_not_guess_between_duplicate_names(monkeypatch):
    mock_catalog(monkeypatch, [{"Name": "Minecraft Launcher", "AppID": MINECRAFT_ID},
                             {"Name": "Minecraft Launcher", "AppID": "Other.Package!App"}])
    with pytest.raises(ValueError, match="Multiple installed apps"):
        app_launcher.installed_app_id("Minecraft Launcher")


@pytest.mark.parametrize("app_id", ["../cmd.exe", 'App!Id" -bad', "shell:untrusted", ""])
def test_lookup_rejects_invalid_registration_ids(monkeypatch, app_id):
    mock_catalog(monkeypatch, [{"Name": "Minecraft Launcher", "AppID": app_id}])
    assert app_launcher.installed_app_id("Minecraft Launcher") is None


def test_failed_catalog_is_not_a_successful_launch(monkeypatch):
    mock_catalog(monkeypatch, [], returncode=1)
    with pytest.raises(ValueError, match="could not read installed apps"):
        app_launcher.installed_app_id("Minecraft Launcher")


def test_store_lookup_failure_becomes_a_failed_tool(monkeypatch):
    monkeypatch.setattr(tools.subprocess, "Popen", missing_executable)
    def timeout(name):
        raise subprocess.TimeoutExpired("powershell", 10)
    monkeypatch.setattr(app_launcher, "installed_app_id", timeout)
    result = tools.open_application.invoke({"app": "Minecraft Launcher"})
    assert result.startswith("Error: Failed to open application")


def test_desktop_shortcut_opens_by_exact_name_through_explorer(monkeypatch, desktop):
    (desktop / "26.2.lnk").write_bytes(b"shortcut")
    monkeypatch.setenv("API_AUTH_KEY", "test-private-key")
    monkeypatch.setattr(app_launcher, "installed_app_id", lambda *a: pytest.fail("Unexpected discovery"))
    launches = []
    monkeypatch.setattr(app_launcher.subprocess, "Popen", lambda *a, **kw: launches.append((a, kw)))
    result = tools.open_application.invoke({"app": "26.2"})
    assert result.startswith("Shortcut launch requested")
    command = launches[0][0][0]
    assert command[0].lower().endswith("explorer.exe") and command[1] == str(desktop / "26.2.lnk")
    assert "API_AUTH_KEY" not in launches[0][1]["env"]


def test_shortcuts_take_no_extra_arguments(monkeypatch, desktop):
    (desktop / "Counter-Strike 2.url").write_text("[InternetShortcut]")
    monkeypatch.setattr(app_launcher.subprocess, "Popen", lambda *a, **kw: pytest.fail("Unexpected launch"))
    assert tools.open_application.invoke({"app": "Counter-Strike 2", "args": "-console"}).startswith("Error: Shortcuts")


@pytest.mark.parametrize("app", ["powershell", "cmd", "C:/Windows/System32/cmd.exe", "python"])
def test_allowed_command_hosts_never_receive_arguments(monkeypatch, app):
    # Arguments to a shell are commands; those go through the terminal's approvals.
    monkeypatch.setattr(tools.subprocess, "Popen", lambda *a, **kw: pytest.fail("Unexpected launch"))
    result = tools.open_application.invoke({"app": app, "args": "-c whoami"})
    assert result.startswith("Error:") and "terminal tool" in result
