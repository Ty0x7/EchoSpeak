"""Computer control that notices when it isn't getting anywhere (the Minecraft menu loop, 11.10.3).

A 4B model on Minecraft's main menu: desktop_click by name ("Control not found." counted as a
success), then vision_qa asked to "click Singleplayer" six times. Each look worked, the animated
menu made every screenshot slightly different, and nothing told it that looking isn't clicking.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any

import pytest

from agent.lean import computer, loop as loop_module, policy
from agent.lean.provider import ModelTurn, ToolCall
from agent.lean.toolbox import NativeTool, Toolbox, ToolResult
from tests.test_capabilities import _Scripted, _turn

SHOT = "Screenshot obs-1: Minecraft 26.3. Image attached. Coordinate space: 870x519."


@pytest.fixture
def allow_all(monkeypatch):
    """No approvals or policy asks: these tests are about what happens after a call runs."""
    monkeypatch.setattr(policy, "evaluate", lambda *a, **k: policy.Decision("allow", "", ""))
    monkeypatch.setattr(loop_module, "tool_needs_approval", lambda entry, name, args: (False, ""))


def _tool_texts(client: _Scripted) -> list[str]:
    last = client.calls[-1]
    return [str(m.get("content") or "") for m in last if m.get("role") == "tool"]


# ── the run that looped, replayed ──────────────────────────────────────
def test_the_minecraft_loop_is_named_after_three_looks_without_acting(allow_all):
    calls = [ToolCall("s", "take_screenshot", "{}"),
             ToolCall("d", "desktop_click", '{"window_title": "Minecraft", "control_name": "Singleplayer"}')]
    calls += [ToolCall(f"v{i}", "vision_qa", '{"question": "Click the Singleplayer button"}') for i in range(3)]
    client = _Scripted([ModelTurn(tool_calls=[c]) for c in calls] + [ModelTurn(content="I'm stuck on the menu.")])
    events: list[dict[str, Any]] = []
    turn = _turn(client, {
        "take_screenshot": lambda a: SHOT,
        "vision_qa": lambda a: SHOT,
        "desktop_click": lambda a: "Control not found: Minecraft 26.3 reports no named controls at all.",
        "computer_action": lambda a: "Executed 1 actions.",
    }, events)
    turn.run("open minecraft and make a peaceful world")

    outcomes = {item["id"]: item["outcome"] for item in turn.timeline if item.get("kind") == "tool"}
    assert outcomes["d"] == "failed"  # it used to be "ok", and the model believed it had clicked
    texts = _tool_texts(client)
    assert "looked at the screen 3 times in a row without acting" in texts[-1]
    assert "computer_action" in texts[-1]
    assert not any("looked at the screen" in text for text in texts[:-1])
    stalls = [(e["step"], e["kind"], e["count"]) for e in events if e["type"] == "screen_stall"]
    assert stalls == [(5, "looking", 3)]


def test_without_desktop_control_the_note_says_to_tell_the_user(allow_all):
    client = _Scripted([ModelTurn(tool_calls=[ToolCall(f"v{i}", "take_screenshot", "{}")]) for i in range(3)]
                       + [ModelTurn(content="ok")])
    turn = _turn(client, {"take_screenshot": lambda a: SHOT}, [])
    turn.run("what's on my screen?")
    assert "can't control the screen in this chat" in _tool_texts(client)[-1]


def test_actions_that_change_nothing_get_a_change_of_approach(allow_all):
    missed = lambda a: ToolResult(True, "Executed 1 actions. The screen looks unchanged.", 0, outcome="no_effect")
    client = _Scripted([ModelTurn(tool_calls=[ToolCall(f"c{i}", "computer_action", "{}")]) for i in range(2)]
                       + [ModelTurn(content="ok")])
    turn = _turn(client, {"computer_action": missed}, [])
    turn.run("click play")
    texts = _tool_texts(client)
    assert "changed nothing" not in texts[0]
    assert "last 2 screen actions failed or changed nothing visible" in texts[1]
    assert "tab moves between buttons" in texts[1]


def test_an_action_that_works_starts_the_counts_again(allow_all):
    worked = lambda a: ToolResult(True, "Executed 1 actions. About 40% of the screen changed.", 0)
    calls = [ToolCall("s1", "take_screenshot", "{}"), ToolCall("s2", "take_screenshot", "{}"),
             ToolCall("a", "computer_action", "{}"), ToolCall("s3", "take_screenshot", "{}"),
             ToolCall("s4", "take_screenshot", "{}")]
    client = _Scripted([ModelTurn(tool_calls=[c]) for c in calls] + [ModelTurn(content="ok")])
    turn = _turn(client, {"take_screenshot": lambda a: SHOT, "computer_action": worked}, [])
    turn.run("go")
    assert not any("looked at the screen" in text for text in _tool_texts(client))


# ── the tools say what they did ─────────────────────────────────────────
def test_desktop_tool_failures_are_failures():
    box = Toolbox(toolsets=["none"], session_id="t")
    for text in ("Control not found.", "Window not found.", "Click failed: boom", "Hotkey failed: x", "Type failed: y"):
        box.native = {"desktop_click": NativeTool("desktop_click", "", {"type": "object", "properties": {}}, lambda a, t=text: t)}
        result = box.run("desktop_click", {})
        assert (result.ok, result.outcome) == (False, "failed"), text
    box.native = {"desktop_click": NativeTool("desktop_click", "", {"type": "object", "properties": {}}, lambda a: "Clicked.")}
    assert box.run("desktop_click", {}).ok


class _Control:
    def __init__(self, text: str) -> None:
        self.text = text

    def window_text(self) -> str:
        return self.text


class _Window(_Control):
    def __init__(self, title: str, children: list[str]) -> None:
        super().__init__(title)
        self.children = [_Control(name) for name in children]

    def descendants(self) -> list[_Control]:
        return self.children


def test_control_not_found_says_when_a_window_draws_its_own_buttons():
    from agent.tools import _control_not_found

    game = _control_not_found(_Window("Minecraft 26.3", ["Minecraft 26.3", "Minimize", "Maximize", "Close"]), "Singleplayer")
    assert game.startswith("Control not found:") and "reports no named controls" in game and "computer_action" in game
    app = _control_not_found(_Window("Settings", ["Settings", "Close", "Bluetooth"]), "Wi-Fi")
    assert "no control named 'Wi-Fi'" in app and "reports no named controls" not in app


def test_vision_qa_says_it_did_not_click(monkeypatch):
    monkeypatch.setattr(computer, "screenshot", lambda session, args: ToolResult(True, SHOT, 0))
    asked = computer.vision_qa("s", {"question": "Click the Singleplayer button on the main menu"})
    assert asked.outcome == "no_effect" and asked.output.startswith("vision_qa only looks: nothing was clicked")
    assert SHOT in asked.output
    question = computer.vision_qa("s", {"question": "Which buttons does the menu show?"})
    assert question.outcome == "ok" and question.output == SHOT


@pytest.mark.skipif(os.name != "nt", reason="named controls come from Windows UI Automation")
def test_a_window_that_names_only_its_title_bar_is_drawn(monkeypatch):
    monkeypatch.setattr(computer, "visible_controls", lambda item: [("Minimize", 10, 5), ("Close", 40, 5)])
    window = {"screen": {"window": 7}}
    assert computer.DRAWN_NOTE in computer._controls_note(window)
    monkeypatch.setattr(computer, "visible_controls", lambda item: [])
    assert computer._controls_note(window) == computer.DRAWN_NOTE
    assert computer._controls_note({"screen": {"monitor": 1}}) == ""
    monkeypatch.setattr(computer, "visible_controls", lambda item: [("Singleplayer", 400, 200), ("Close", 40, 5)])
    assert computer.DRAWN_NOTE not in computer._controls_note(window)


def test_the_prompt_separates_looking_from_doing_only_when_it_can_act():
    from agent.lean.prompt import WORKING_RULES, _for_tools

    assert "- Looking and doing" in _for_tools(WORKING_RULES, {"take_screenshot", "computer_action"})
    assert "- Looking and doing" not in _for_tools(WORKING_RULES, {"take_screenshot"})
    assert "propose_skill" in _for_tools(WORKING_RULES, {"propose_skill"})
    assert "propose_skill" not in _for_tools(WORKING_RULES, {"file_read"})


def test_the_bundled_skill_is_a_valid_skill():
    from agent.lean import agent_skills

    skill = agent_skills.load(agent_skills.bundled_dir() / "desktop-apps-and-games")
    assert skill.problem == "" and skill.name == "desktop-apps-and-games"
    assert "computer_action" in skill.body and "propose_skill" in skill.body


def test_the_runtime_offers_propose_skill_only_in_the_owners_chats(monkeypatch):
    from agent.lean.personas import AgentPersona
    from agent.lean.runtime import LeanSession

    monkeypatch.setattr(LeanSession, "_is_group", lambda self: False)

    def native(role: str, source: str) -> set[str]:
        session = LeanSession.__new__(LeanSession)
        session.agent = SimpleNamespace(memory=None)
        session.session_id, session.execution_id, session.caller_role, session.job = "s", "e", role, None
        session.room, session.source = None, source
        session.personas = SimpleNamespace(list=lambda: [])
        tools = LeanSession._native_tools(session, AgentPersona(id="echo", name="Echo"), 0, allow_handoff=False)
        return {t.name for t in tools}

    assert "propose_skill" in native("owner", "web")
    assert "propose_skill" not in native("owner", "routine")
    assert "propose_skill" not in native("public", "web")
