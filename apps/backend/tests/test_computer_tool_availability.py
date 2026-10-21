"""Screen tools must accurately explain the control permissions of this turn."""
import threading
from types import SimpleNamespace

import pytest
from PIL import Image

from agent.lean import computer, observations, toolbox as toolbox_module
from agent.lean.personas import AgentPersona, PersonaStore
from agent.lean.prompt import build_system_prompt
from agent.lean.toolbox import DEFAULT_TOOLSETS, Toolbox
from config import config


@pytest.fixture(autouse=True)
def isolated_desktop(tmp_path, monkeypatch):
    monkeypatch.setattr(observations, "root", lambda: tmp_path / "observations")
    monkeypatch.setattr(toolbox_module.platform, "system", lambda: "Windows")
    monkeypatch.setattr(config, "enable_system_actions", True)
    monkeypatch.setattr(config, "allow_desktop_automation", True)


def box(*toolsets):
    return Toolbox(toolsets=list(toolsets), extra_tools=computer.tools("t", threading.Event()), session_id="t")


def test_default_echo_does_not_silently_gain_desktop_permission(tmp_path):
    store = PersonaStore(tmp_path / "agents.json")
    assert "desktop" not in DEFAULT_TOOLSETS
    assert "desktop" not in store.default().toolsets
    assert "computer_action" not in box(*store.default().toolsets).names
    store.update("echo", {"toolsets": ["vision"]})
    assert PersonaStore(store.path).default().toolsets == ["vision"]


def test_vision_only_schemas_and_prompt_explain_the_missing_control_tool():
    tools = box("vision")
    assert "take_screenshot" in tools.names and "read_image" in tools.names
    assert "computer_action" not in {row["function"]["name"] for row in tools.schemas()}
    note = tools.computer_context()
    assert "not available to this agent" in note
    assert "left sidebar" in note and "Edit button" in note and "enable Desktop control" in note
    prompt = build_system_prompt(persona=AgentPersona(id="echo", name="Echo"), soul_text="", computer_note=note)
    assert note in prompt


def test_explicit_desktop_opt_in_exposes_control_schema():
    tools = box("vision", "desktop")
    schema = next(row["function"] for row in tools.schemas() if row["function"]["name"] == "computer_action")
    assert schema["parameters"]["required"] == ["observation_id", "actions"]
    note = tools.computer_context()
    assert "enabled for this agent" in note and "existing desktop approvals" in note


@pytest.mark.parametrize("flag", ["enable_system_actions", "allow_desktop_automation"])
def test_toolset_cannot_override_a_disabled_global_permission(monkeypatch, flag):
    monkeypatch.setattr(config, flag, False)
    tools = box("vision", "desktop")
    assert "computer_action" in tools.names
    note = tools.computer_context()
    assert "desktop control is disabled" in note
    assert "Settings > Permissions" in note and "Do not bypass" in note


def test_control_without_screen_tools_explains_required_vision_opt_in():
    note = box("desktop").computer_context()
    assert "take_screenshot is not in its toolbox" in note
    assert "also enable Screen & images" in note


def test_non_windows_control_readiness_is_explicit(monkeypatch):
    monkeypatch.setattr(toolbox_module.platform, "system", lambda: "Linux")
    assert "requires Windows" in box("vision", "desktop").computer_context()


def test_screenshot_result_explains_missing_control(monkeypatch):
    item = observations.save(Image.new("RGB", (100, 80), "red"), "t", "Test screen")
    monkeypatch.setattr(computer, "_grab", lambda *_: item)
    result = box("vision").run("take_screenshot", {})
    assert result.ok and result.observations == [observations.reference(item)]
    assert "not available to this agent" in result.output and "terminal" in result.output


def test_ready_control_is_not_repeated_in_every_screenshot(monkeypatch):
    # The system prompt carries it; a game run takes dozens of screenshots.
    item = observations.save(Image.new("RGB", (100, 80), "red"), "t", "Test screen")
    monkeypatch.setattr(computer, "_grab", lambda *_: item)
    tools = box("vision", "desktop")
    assert tools.computer_ready() and "enabled for this agent" in tools.computer_context()
    result = tools.run("take_screenshot", {})
    assert result.ok and "enabled for this agent" not in result.output


def test_no_desktop_details_leak_into_lookup_only_or_planning_toolboxes():
    assert box("web").computer_context() == ""
    tools = box("vision", "desktop")
    tools.restrict_to_read_only()
    assert "computer_action" not in tools.names
    assert tools.computer_context() == ""


def test_app_names_appear_only_when_the_request_is_about_apps(monkeypatch):
    monkeypatch.setattr(config, "allow_open_application", True)
    monkeypatch.setattr(config, "open_application_favorites", ["26.2", "minecraft launcher"])
    monkeypatch.setattr(config, "open_application_blocklist", ["regedit"])
    tools = box("vision", "desktop")
    tools.entries["open_application"] = SimpleNamespace()
    plain = tools.computer_context(goal="what version are you running?")
    assert "Computer control is enabled" in plain
    assert "26.2" not in plain and "regedit" not in plain
    assert "Allowed apps" not in plain and "Blocked apps list are refused" in plain
    launching = tools.computer_context(goal="open minecraft for me")
    assert '"26.2"' in launching and "not a complete list" in launching
    assert '"regedit"' in launching and "never to be opened" in launching
