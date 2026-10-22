"""Fixes from the owner's 2026-10-22 live test on gemma-4-e4b.

- Blocked apps: "try running regedit from the terminal instead" asked for approval instead of refusing.
- mics.md: "I've written the comparison to mics.md" came with a terminal call and no file was ever written.
- Start button: a guessed click hit empty taskbar, and the reply said the Start menu opened.
- Research workers: spawned for a three-microphone comparison, and ran one at a time.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from agent.lean import computer, policy, settings
from agent.lean.job import unbacked_file_claim, written_names
from agent.lean.provider import ModelTurn, ToolCall
from agent.lean.runtime import _ON_THIS_PC
from agent.lean.swarm import wants_workers
from agent.lean.toolbox import NativeTool, Toolbox
from config import config
from tests.test_capabilities import _Scripted, _turn
from tests.test_computer_observations import capture, desktop  # noqa: F401  (desktop is a fixture)


# ── Blocked apps hold in the terminal too ───────────────────────────────────
@pytest.fixture
def blocklist(monkeypatch):
    monkeypatch.setattr(config, "open_application_blocklist", ["regedit", "gpedit.msc", "diskpart"])


@pytest.mark.parametrize("command", [
    "regedit", "start regedit", "Start-Process regedit.exe", 'cmd /c "C:\\Windows\\regedit.exe"',
    "powershell -c 'Start-Process gpedit.msc'", "diskpart /s script.txt",
])
def test_a_blocked_app_is_refused_from_the_terminal_without_asking(blocklist, command):
    decision = policy.evaluate("terminal", {"command": command, "where": "host"})
    assert decision.action == "deny" and decision.rule == "blocked_app"
    assert "Blocked apps" in decision.reason and "don't open it any other way" in decision.reason


@pytest.mark.parametrize("command", ["dir", "git status", "reg query HKCU\\Software", "python regedit_notes.py"])
def test_ordinary_commands_are_not_mistaken_for_blocked_apps(blocklist, command):
    assert policy.evaluate("terminal", {"command": command}).rule != "blocked_app"


def test_the_loop_refuses_it_and_never_runs_or_asks(blocklist):
    ran: list[str] = []
    client = _Scripted([ModelTurn(tool_calls=[ToolCall("c1", "terminal", '{"command": "regedit", "where": "host"}')]),
                        ModelTurn(content="Registry Editor is in your Blocked apps, so I can't open it.")])
    events: list[dict[str, Any]] = []
    _turn(client, {"terminal": lambda a: ran.append(a["command"]) or "ok"}, events).run("Try running regedit")
    assert ran == [] and not any(e["type"] == "approval_request" for e in events)
    result = client.calls[1][-1]["content"]
    assert result.startswith("Blocked by EchoSpeak's safety policy") and "is in Blocked apps" in result


# ── A named file must have been written ──────────────────────────────────────
def test_a_claimed_file_needs_a_write_with_that_name():
    claim = "I've written the full comparison to mics.md. Now I'll run dir."
    assert unbacked_file_claim(claim, set()) == "I've written the full comparison to mics.md."
    assert unbacked_file_claim(claim, {"mics.md"}) == ""
    assert unbacked_file_claim("I'll write it to mics.md next.", set()) == ""  # a plan, not a claim
    assert unbacked_file_claim("I read mics.md and it looks fine.", set()) == ""


def test_writes_and_commands_record_the_files_they_touch():
    assert written_names("file_write", {"path": "C:\\Users\\me\\Documents\\mics.md"}) == {"mics.md"}
    assert written_names("file_copy", {"src": "a.txt", "destination": "out/b.txt"}) == {"b.txt"}
    assert written_names("terminal", {"command": "echo hi > notes.txt && type notes.txt"}) == {"notes.txt"}
    assert written_names("web_search", {"query": "mics.md"}) == set()


def test_research_workers_back_no_claim_that_something_was_written():
    from agent.lean.job import unbacked_claim

    assert unbacked_claim("I've created the comparison file.", {"spawn_workers"})


def test_a_false_file_claim_beside_a_tool_call_is_caught_and_corrected():
    wrote: list[str] = []
    client = _Scripted([
        ModelTurn(content="I've written the comparison to mics.md. Now I'll list the folder.",
                  tool_calls=[ToolCall("c1", "list_folder", '{"path": "."}')]),
        ModelTurn(tool_calls=[ToolCall("c2", "file_write", '{"path": "C:/Users/me/Documents/mics.md", "content": "# Mics"}')]),
        ModelTurn(content="I've written mics.md with the comparison."),
    ])
    events: list[dict[str, Any]] = []
    tools = {"list_folder": lambda a: "desktop.ini", "file_write": lambda a: wrote.append(a["path"]) or "Wrote mics.md"}
    result = _turn(client, tools, events).run("compare mics, write mics.md, run dir")
    note = client.calls[1][-1]["content"]
    assert "no tool has written mics.md" in note and note.startswith("desktop.ini")
    assert any(e["type"] == "claim_nudge" for e in events)
    assert wrote and result.text == "I've written mics.md with the comparison." and result.stop_reason != "unverified_claim"


def test_a_false_file_claim_in_the_final_reply_is_challenged():
    client = _Scripted([ModelTurn(content="I've saved the comparison to mics.md."),
                        ModelTurn(content="I haven't saved mics.md yet; here is the comparison instead.")])
    _turn(client, {"file_write": lambda a: "Wrote"}, []).run("write mics.md")
    assert "isn't true yet" in client.calls[1][-1]["content"]


# ── "on this PC" picks the terminal's destination ────────────────────────────
@pytest.mark.parametrize("goal, host", [
    ('Run "dir" in the terminal on this PC.', True), ("run it on my computer", True), ("check it on the host", True),
    ("Run dir in the terminal", False), ("open this project", False),
])
def test_on_this_pc_is_recognised(goal, host):
    assert bool(_ON_THIS_PC.search(goal)) is host


def test_a_preferred_host_fills_only_a_missing_destination():
    box = Toolbox(toolsets=["none"])
    box.native = {"terminal": NativeTool("terminal", "t", {"type": "object", "properties": {}}, lambda a: "ok")}
    box.prefer_host = True
    assert box.normalize_call("terminal", {"command": "dir"})[1]["where"] == "host"
    assert box.normalize_call("terminal", {"command": "dir", "where": "sandbox"})[1]["where"] == "sandbox"


# ── Research workers: deep research only, all at once ────────────────────────
@pytest.mark.parametrize("goal, effort, wanted", [
    ("Research the history of the Raspberry Pi in depth across at least 8 sources", "medium", True),
    ("Do a deep dive on solid-state batteries", "medium", True),
    ("Compare five sources on this claim", "medium", True),
    ("Research three good budget microphones and write mics.md", "medium", False),
    ("Compare the Pixel and the iPhone battery", "medium", False),
    ("Check 2 sources for the date", "medium", False),
    ("Compare the Pixel and the iPhone battery", "ultra", True),
])
def test_workers_are_offered_for_deep_research_or_ultra(goal, effort, wanted):
    assert wants_workers(goal, effort) is wanted


def test_workers_run_all_at_once_unless_set_one_at_a_time(monkeypatch):
    monkeypatch.setattr(config, "lean_swarm_one_at_a_time", False, raising=False)
    monkeypatch.setattr(config, "lean_swarm_local_parallel", 6, raising=False)
    monkeypatch.setattr(config, "lean_swarm_cloud_parallel", 6, raising=False)
    assert settings.swarm_parallel(True) == 6 and settings.swarm_parallel(False) == 6
    monkeypatch.setattr(config, "lean_swarm_one_at_a_time", True)
    assert settings.swarm_parallel(True) == 1 and settings.swarm_parallel(False) == 1


# ── Screen control: named controls, and whether anything changed ─────────────
class _Node:
    def __init__(self, kind: str, name: str, box: tuple[int, int, int, int], children: list["_Node"] = ()) -> None:
        self.element_info = SimpleNamespace(control_type=kind)
        self._name, self._box, self._children = name, box, list(children)

    def window_text(self) -> str:
        return self._name

    def rectangle(self):
        left, top, right, bottom = self._box
        return SimpleNamespace(left=left, top=top, right=right, bottom=bottom,
                               width=lambda: right - left, height=lambda: bottom - top)

    def children(self) -> list["_Node"]:
        return self._children


def test_named_controls_are_listed_in_the_screenshots_coordinates():
    taskbar = _Node("Pane", "Taskbar", (0, 1032, 1920, 1080), [
        _Node("Button", "Start", (566, 1034, 614, 1078)),
        _Node("Button", "Search", (614, 1034, 662, 1078)),
        _Node("Pane", "", (0, 0, 1, 1)),
        _Node("Button", "", (700, 1034, 748, 1078)),  # unnamed: nothing to read
        _Node("Button", "Off screen", (2000, 1034, 2048, 1078)),
    ])
    item = {"screen": {"left": 0, "top": 0, "width": 1920, "height": 1080}, "model_width": 1280, "model_height": 720}
    assert computer.visible_controls(item, roots=[taskbar]) == [("Start", 393, 704), ("Search", 425, 704)]


def test_a_screen_that_did_not_change_says_the_click_probably_missed():
    before = Image.new("RGB", (1920, 1080), (20, 20, 20))
    same = before.copy()
    same.paste((200, 200, 200), (1800, 1040, 1830, 1060))  # the clock ticked
    opened = before.copy()
    opened.paste((230, 230, 230), (500, 300, 1400, 1030))  # the Start menu
    assert computer._change(before, same) < 0.005
    assert "probably missed" in computer.change_note(computer._change(before, same))
    assert computer._change(before, opened) > 0.3
    assert computer.change_note(computer._change(before, opened)).startswith("About ")


def test_an_action_that_changed_nothing_says_so(desktop, monkeypatch):
    item, gui, events = desktop
    monkeypatch.setattr(computer, "_capture", lambda args: (Image.new("RGB", (1920, 1080), "red"), item["screen"], "Monitor 1"))
    monkeypatch.setattr(computer, "_grab", lambda session, args: capture(screen=item["screen"]))  # still red: nothing opened
    monkeypatch.setattr(computer, "_controls_note", lambda item: "")
    result = computer.act("t", {"observation_id": item["id"], "actions": [{"action": "click", "coordinate": [28, 690]}]},
                          SimpleNamespace(is_set=lambda: False, wait=lambda seconds: False))
    assert result.ok and "looks unchanged" in result.output and "probably missed" in result.output


def test_the_action_tool_teaches_keys_and_listed_points():
    tool = next(t for t in computer.tools("s", __import__("threading").Event()) if t.name == "computer_action")
    assert "win opens Start" in tool.description and "the point the screenshot result lists" in tool.description
