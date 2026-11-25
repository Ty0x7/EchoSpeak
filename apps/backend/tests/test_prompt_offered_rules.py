"""Usage rules follow the tools the model sees this turn; safety rules follow every loadable tool (round 2, 25 Nov)."""
from __future__ import annotations

from agent.lean import prompt as P
from tests.test_agent_first import _echo_turn, env  # noqa: F401


def test_usage_rules_need_the_tool_offered_and_safety_rules_stay_while_loadable():
    names = {"terminal", "file_edit", "set_objective", "research_notebook", "take_screenshot", "computer_action",
             "propose_skill"}
    offered = {"terminal", "file_edit", "set_objective"}
    text = P._for_tools(P.WORKING_RULES, names, offered)
    assert "- For substantial engineering work" in text                     # offered
    assert "- Use research_notebook" not in text and "- For visual tasks" not in text
    assert "- When you work out how" not in text                             # propose_skill only deferred
    assert "- Follow the computer-control" in text and "- Looking and doing" in text   # safety: still loadable
    assert "- Follow the computer-control" not in P._for_tools(P.WORKING_RULES, offered, offered)   # not loadable
    assert P._for_tools(P.WORKING_RULES, names) == P._for_tools(P.WORKING_RULES, names, names)       # old callers


def test_a_code_turn_sends_the_terminal_description_once(env, monkeypatch):
    _, turn = _echo_turn(env, monkeypatch, "fix the failing test in parser.py", project=True)
    assert "terminal" in turn.toolbox.names and "terminal" not in turn.toolbox.deferred
    assert "The terminal runs bash" not in turn.system_prompt                # it's in the tool's description
    assert "- Use research_notebook" not in turn.system_prompt
