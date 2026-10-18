"""Claude's own computer toolset, run through Echo's checked executor."""
import json
import threading

import pytest
from PIL import Image

from agent.lean import computer, observations, policy
from agent.lean.approvals import get_approval_broker
from agent.lean.cloud_streams import anthropic_messages, anthropic_tools
from agent.lean.loop import LeanTurn
from agent.lean.personas import AgentPersona
from agent.lean.provider import ModelTurn, ToolCall
from agent.lean.toolbox import Toolbox
from config import config

SCREEN = {"left": 0, "top": 0, "width": 1920, "height": 1080, "monitor": 1}


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(observations, "root", lambda: tmp_path / "observations")
    monkeypatch.setattr(config, "computer_native_tools", True, raising=False)
    monkeypatch.setattr(config, "enable_system_actions", True)
    monkeypatch.setattr(config, "allow_desktop_automation", True)
    monkeypatch.setattr(policy, "_secret_values", lambda: [])
    monkeypatch.setattr(policy, "audit", lambda *a, **kw: None)


def capture(session="chat", screen=SCREEN):
    return observations.save(Image.new("RGB", (1920, 1080), "red"), session, "Monitor 1", screen=screen)


def schemas(*toolsets):
    box = Toolbox(toolsets=list(toolsets), extra_tools=computer.tools("chat", threading.Event()), session_id="chat")
    return box, box.schemas()


def test_claude_5_gets_its_toolset_in_place_of_computer_action():
    box, tools = schemas("vision", "desktop")
    assert "left_click" in box.names and not any(t["function"]["name"] == "left_click" for t in tools)  # hidden
    native = anthropic_tools("claude-opus-5-5", tools)
    names = [t.get("name") or t["type"] for t in native]
    assert "computer_toolset_20260801" in names and "read_image" in names
    assert not {"computer_action", "take_screenshot", "vision_qa"} & set(names)
    assert native[-1]["configs"]["left_mouse_down"] == {"enabled": False}


@pytest.mark.parametrize("model, toolsets, on", [("claude-sonnet-4-6", ("vision", "desktop"), True),
                                                ("claude-opus-5-5", ("vision",), True),
                                                ("claude-opus-5-5", ("vision", "desktop"), False)])
def test_everything_else_keeps_computer_action(monkeypatch, model, toolsets, on):
    monkeypatch.setattr(config, "computer_native_tools", on, raising=False)
    _, tools = schemas(*toolsets)
    assert not any(t.get("type") == "computer_toolset_20260801" for t in anthropic_tools(model, tools))


def test_toolset_results_carry_their_name_and_their_image():
    item = capture()
    hydrated = observations.hydrate([
        {"role": "user", "content": "click it"},
        {"role": "assistant", "content": "", "provider_blocks": [
            {"type": "tool_use", "id": "t1", "name": "screenshot", "toolset_name": "computer", "input": {}}]},
        {"role": "tool", "tool_call_id": "t1", "name": "screenshot", "content": "Captured",
         "observations": [observations.reference(item)]},
    ], inline_tool=True)
    _, turns = anthropic_messages(hydrated)
    result = turns[-1]["content"][0]
    assert result["type"] == "tool_result" and result["toolset_name"] == "computer"
    assert [part["type"] for part in result["content"]] == ["text", "text", "image"]
    assert len(turns) == 3  # no separate user message with the image


def test_native_actions_map_onto_checked_steps():
    item = capture()
    assert computer._native_actions("left_click", {"coordinate": [10, 20], "text": "shift"}, item) == [
        {"action": "hold", "text": "shift", "button": "left", "coordinate": [10, 20], "duration": 0.05}]
    assert computer._native_actions("middle_click", {"coordinate": [1, 2]}, item) == [{"action": "click", "coordinate": [1, 2], "button": "middle"}]
    assert computer._native_actions("key", {"text": "ctrl+Return", "repeat": 2}, item) == [{"action": "key", "text": "ctrl+enter"}] * 2
    left = computer._native_actions("scroll", {"coordinate": [5, 5], "scroll_direction": "left", "scroll_amount": 50}, item)
    assert left == [{"action": "scroll", "coordinate": [5, 5], "amount": -20, "horizontal": True}]
    for name, args in [("hold_key", {"text": "w", "duration": 30}), ("key", {"text": "a", "repeat": 50})]:
        with pytest.raises(ValueError):
            computer._native_actions(name, args, item)
    with pytest.raises(ValueError, match="left_click_drag"):
        computer.native("chat", "left_mouse_down", {}, threading.Event())


def test_zoom_reads_the_original_pixels_behind_model_coordinates(monkeypatch):
    item = capture()
    computer._current["chat"] = item["id"]
    crops = []
    monkeypatch.setattr(computer, "read_image", lambda session, args: crops.append(args["crop"]) or "image")
    computer.native("chat", "zoom", {"region": [0, 0, 640, 360]}, threading.Event())
    assert crops == [[0, 0, 960, 540]]  # model 1280x720 -> screen 1920x1080


def test_a_claude_batch_runs_under_one_grant_and_stops_at_the_first_failure(monkeypatch):
    item = capture()
    monkeypatch.setattr(computer, "_grab", lambda session, args: item)
    ran = []

    def act(session, args, cancel=None):
        step = args["actions"][0]
        ran.append(step["action"])
        if step["action"] == "type":
            raise ValueError("Window moved or resized; capture it again before acting")
        return computer._result(item, "Executed 1 actions.")
    monkeypatch.setattr(computer, "act", act)
    box = Toolbox(toolsets=["vision", "desktop"], extra_tools=computer.tools("chat", threading.Event()), session_id="chat")
    broker, approvals = get_approval_broker(), []

    def emit(event):
        if event.get("type") == "approval_request":
            approvals.append(event["tool"])
            broker.resolve(event["id"], "always")

    seen = []

    class Claude:
        count = 0

        def stream_turn(self, messages, **kwargs):
            self.count += 1
            seen.append(messages)
            if self.count == 1:
                return ModelTurn(tool_calls=[ToolCall("s", "screenshot", "{}")])
            if self.count == 2:
                return ModelTurn(tool_calls=[
                    ToolCall("a", "left_click", json.dumps({"coordinate": [640, 360]})),
                    ToolCall("b", "type", json.dumps({"text": "hello"})),
                    ToolCall("c", "key", json.dumps({"text": "Return"})),
                ])
            return ModelTurn(content="Typing failed because the window moved.")

    LeanTurn(client=Claude(), persona=AgentPersona(id="echo", name="Echo"), system_prompt="t", history=[], toolbox=box,
             session_id="chat", request_id="r", execution_id="e", emit=emit, cancel=threading.Event(),
             persist_tool_runs=False).run("Type hello in the box")
    assert ran == ["click", "type"]  # key never ran
    assert approvals == ["computer_action"]  # one card; For this chat covered type
    results = {m.get("tool_call_id"): m["content"] for m in seen[-1] if m.get("role") == "tool"}
    assert results["c"] == "Not executed: an earlier computer action in this turn failed."
