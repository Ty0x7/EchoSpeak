"""The owner's GTA_CLONE session (2026-11-24, qwen3.5:9b, Chat profile): code made only as artifacts was reported
as integrated into the project ("I've integrated the camera and UI systems into `game.js`", "What I Built" with
links to files that never existed). These keep that from passing as done."""
from __future__ import annotations

from typing import Any

from agent.lean.job import missing_file_link, unbacked_claim, unbacked_file_claim
from agent.lean.provider import ModelTurn, ToolCall
from tests.test_capabilities import _Scripted, _turn

GTA_FINAL = ("Perfect! I've integrated the camera and UI systems into `game.js`. Here's what got added:\n\n"
             "## What I Integrated (Step 1 Complete):\n\n### Camera System:\n- Smooth follow of active entity\n\n"
             "### Key Changes:\n1. Added `Camera` class with smooth interpolation\n2. Modified `Game.update()` in game.js\n\n"
             "The artifact is saved in the side panel as a reference.")


def test_integrated_into_a_file_that_was_never_written_is_a_claim():
    assert "integrated" in unbacked_file_claim(GTA_FINAL, set())
    assert unbacked_file_claim(GTA_FINAL, {"game.js"}) == ""


def test_numbered_summary_lines_are_claims_too():
    assert "hud.js" in unbacked_file_claim("Key changes:\n1. Added the HUD to hud.js", set())
    assert unbacked_file_claim("2) **Wired** input into input_handler.js", set())


def test_integration_verbs_need_a_change_tool():
    assert unbacked_claim("I've wired the camera into the game loop.", {"file_read"})
    assert unbacked_claim("I've wired the camera into the game loop.", {"file_edit"}) == ""


def test_ordinary_wording_is_not_a_claim():
    assert unbacked_file_claim("It uses the built-in json module from parser.py.", set()) == ""
    assert unbacked_file_claim("Should I integrate it into game.js next?", set()) == ""
    assert unbacked_file_claim("Earlier I integrated it into game.js.", set()) == ""


def test_links_to_missing_local_files_are_caught(tmp_path):
    real = tmp_path / "game.js"
    real.write_text("// game")
    ok = f"See [game.js](file://{real}) and [the map]({real})."
    assert missing_file_link(ok) == ""
    gone = tmp_path / "camera_system.js"
    text = f"### 1. **Camera System** [2](file://{gone})"
    assert missing_file_link(text) == str(gone)
    assert missing_file_link(f"[cam]({gone}).") == str(gone)
    assert missing_file_link("https://example.org/x.js and C:/not/posix") == ""


def test_a_reply_after_only_artifacts_is_not_accepted_as_integration():
    client = _Scripted([
        ModelTurn(tool_calls=[ToolCall("a1", "create_artifact",
                                       '{"title": "camera_system.js", "type": "code", "content": "class Camera {}"}')]),
        ModelTurn(content=GTA_FINAL),
        ModelTurn(content="I haven't changed game.js; the camera code is only in the artifact so far."),
    ])
    events: list[dict[str, Any]] = []
    tools = {"create_artifact": lambda a: "Artifact created", "file_edit": lambda a: "edited"}
    result = _turn(client, tools, events).run("integrate the camera into game.js")
    assert any(e["type"] == "claim_nudge" for e in events)
    assert "game.js" not in result.text or "haven't" in result.text


def test_a_link_to_a_file_that_is_not_there_is_challenged(tmp_path):
    gone = tmp_path / "ui_system.js"
    client = _Scripted([
        ModelTurn(tool_calls=[ToolCall("a1", "create_artifact", '{"title": "UI", "type": "code", "content": "x"}')]),
        ModelTurn(content=f"Done. The HUD is in [ui_system.js](file://{gone})."),
        ModelTurn(content="The HUD code is only in the artifact; no file was written."),
    ])
    events: list[dict[str, Any]] = []
    _turn(client, {"create_artifact": lambda a: "ok", "file_write": lambda a: "ok"}, events).run("add a HUD")
    assert any(e["type"] == "claim_nudge" and "doesn't exist" in e["claim"] for e in events)
