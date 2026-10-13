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
    monkeypatch.setattr(config, "open_application_allowlist", ["minecraft launcher"])
    monkeypatch.setattr(config, "allow_desktop_automation", True)
    monkeypatch.setattr(config, "allow_file_write", True)


def test_blocked_app_never_resolves_or_launches(monkeypatch):
    monkeypatch.setattr(tools.subprocess, "Popen", lambda *a, **kw: pytest.fail("Unexpected launch"))
    monkeypatch.setattr(app_launcher, "installed_app_id", lambda *a: pytest.fail("Unexpected discovery"))
    result = tools.open_application.invoke({"app": "powershell"})
    assert result.startswith("Error: Application not allowlisted")
    assert "Settings > Permissions > Allowed apps" in result
    assert "Do not bypass" in result


def test_empty_list_is_a_failed_tool_with_actionable_guidance(monkeypatch):
    monkeypatch.setattr(config, "open_application_allowlist", [])
    monkeypatch.setattr(tools.subprocess, "Popen", lambda *a, **kw: pytest.fail("Unexpected launch"))
    box = Toolbox(toolsets=["desktop"], session_id="test")
    box.entries["open_application"] = SimpleNamespace(func=tools.open_application)
    result = box.run("open_application", {"app": "Minecraft Launcher"})
    assert not result.ok
    assert "Allowed apps is empty" in result.output
    assert "separate from terminal" in result.output


@pytest.mark.parametrize("flag", ["enable_system_actions", "allow_open_application"])
def test_allowlist_does_not_override_disabled_launch_permission(monkeypatch, flag):
    monkeypatch.setattr(config, flag, False)
    monkeypatch.setattr(tools.subprocess, "Popen", lambda *a, **kw: pytest.fail("Unexpected launch"))
    assert tools.open_application.invoke({"app": "Minecraft Launcher"}).startswith("Error: Application launching is disabled")


def test_allowed_executable_uses_clean_env_and_no_console(monkeypatch):
    monkeypatch.setattr(config, "open_application_allowlist", ["notepad"])
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
    (None, None, "Allowed application was not found"),
    (MINECRAFT_ID, "--bad-option", "do not support extra arguments"),
])
def test_missing_or_incompatible_store_launch_fails_clearly(monkeypatch, app_id, extra, expected):
    monkeypatch.setattr(tools.subprocess, "Popen", missing_executable)
    monkeypatch.setattr(app_launcher, "installed_app_id", lambda name: app_id)
    monkeypatch.setattr(tools.os, "startfile", lambda *a: pytest.fail("Unexpected Store launch"), raising=False)
    assert expected in tools.open_application.invoke({"app": "Minecraft Launcher", "args": extra})


def test_empty_allowlist_also_blocks_notepad_helper(monkeypatch):
    monkeypatch.setattr(config, "open_application_allowlist", [])
    monkeypatch.setattr(tools.subprocess, "Popen", lambda *a, **kw: pytest.fail("Unexpected launch"))
    token = tools.bind_tool_execution_context({"allowed_tool_names": ["notepad_write"], "thread_id": "test"})
    try:
        assert tools.notepad_write.invoke({"content": "test"}).startswith("Error: Notepad is not allowlisted")
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
    assert result.startswith("Failed to open application")
