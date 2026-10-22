"""Recover the reported GUI-as-shell mistake through the real governed tool loop."""
import json
import threading

from PIL import Image

from agent.lean import computer, observations, policy, terminal
from agent.lean.loop import LeanTurn
from agent.lean.personas import AgentPersona
from agent.lean.provider import ModelTurn, ToolCall
from agent.lean.toolbox import Toolbox
from config import config


def test_screenshot_bad_terminal_call_and_desktop_recovery(monkeypatch, tmp_path):
    monkeypatch.setattr(observations, "root", lambda: tmp_path / "observations")
    monkeypatch.setattr(config, "enable_system_actions", True)
    monkeypatch.setattr(config, "allow_desktop_automation", True)
    monkeypatch.setattr(config, "allow_terminal_commands", True)
    monkeypatch.setattr(terminal, "resolved_mode", lambda: "docker")
    monkeypatch.setattr(policy, "_secret_values", lambda: [])
    monkeypatch.setattr(policy, "audit", lambda *a, **kw: None)
    before = observations.save(Image.new("RGB", (100, 80), "red"), "t", "Desktop")
    after = observations.save(Image.new("RGB", (100, 80), "blue"), "t", "Opened app")
    monkeypatch.setattr(computer, "_grab", lambda *a: before)
    term = terminal.Terminal()
    monkeypatch.setattr(term, "_launch", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("Unexpected shell launch")))
    monkeypatch.setattr(terminal._SANDBOX, "ensure_container", lambda *a: (_ for _ in ()).throw(AssertionError("Unexpected Docker access")))
    tools = computer.tools("t", threading.Event())
    actions = []

    def act(args):
        actions.append(args)
        assert args["observation_id"] == before["id"]
        return computer._result(after, "Executed one simulated action.")

    next(t for t in tools if t.name == "computer_action").func = act
    box = Toolbox(toolsets=["vision", "desktop", "terminal"], extra_tools=tools + term.tools(), session_id="t")
    approved = []

    def approve(self, call, name, args, step):
        approved.append((name, self._policy_reasons.get(call.id, "")))
        return True, "", ""  # Simulate the user's approval; no desktop input runs.

    monkeypatch.setattr(LeanTurn, "_approve", approve)

    class ScriptedModel:
        count = 0

        def stream_turn(self, messages, **kwargs):
            self.count += 1
            if self.count == 1:
                return ModelTurn(tool_calls=[ToolCall("s", "take_screenshot", "{}")])
            if self.count == 2:
                assert messages[-1]["observations"] == [observations.reference(before)]
                assert box.computer_ready()  # explained once in the system prompt, not per screenshot
                return ModelTurn(tool_calls=[ToolCall("bad", "terminal", json.dumps({"command": 'where="host" click 26.2'}))])
            if self.count == 3:
                assert "separate JSON arguments" in messages[-1]["content"]
                assert "computer_action" in messages[-1]["content"]
                return ModelTurn(tool_calls=[ToolCall("control", "computer_action", json.dumps({
                    "observation_id": before["id"], "actions": [{"action": "double_click", "coordinate": [50, 40]}]}))])
            assert self.count == 4
            assert messages[-1]["observations"] == [observations.reference(after)]
            assert observations.hydrate(messages)[-1]["content"][-1]["type"] == "image_url"
            return ModelTurn(content="The app is open.")

    events = []
    result = LeanTurn(client=ScriptedModel(), persona=AgentPersona(id="echo", name="Echo"), system_prompt="test",
                      history=[], toolbox=box, session_id="t", request_id="r", execution_id="e", emit=events.append,
                      cancel=threading.Event(), persist_tool_runs=False).run("Open the desktop shortcut")
    assert result.success and len(actions) == 1
    assert any(name == "computer_action" and "on screen" in reason for name, reason in approved)
    cards = [w for event in events for w in event.get("widgets", []) if w["type"] == "observation"]
    assert [w["data"]["id"] for w in cards] == [before["id"], after["id"]]
    assert "data:image" not in json.dumps(events)
