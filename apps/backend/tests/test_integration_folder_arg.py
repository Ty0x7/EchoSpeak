"""A connection setting can be a start-up argument (the folder a filesystem server may use) — campaign round 3.

Found live (mcplab, qwen3.5:9b): asked for an MCP filesystem server, find_integrations offered only unreviewed
community packages, because the official reference server isn't in the MCP registry and EchoSpeak couldn't pass
it the folder it needs (settings were env vars and headers only).
"""
from __future__ import annotations

import pytest

from agent import integrations


def _proposal(tmp_path):
    return {"server_name": "files", "launch": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem@2.4.0"]},
            "settings": [{"name": "Folder", "kind": "arg", "folder": True, "required": True}],
            "capability_policies": {"list_directory": "read"}}


def test_the_folder_is_one_argument_after_the_package(tmp_path):
    notes = tmp_path / "my notes"
    notes.mkdir()
    config = integrations.server_config(_proposal(tmp_path), {"Folder": str(notes)})
    assert config["args"][-1] == str(notes.resolve())
    assert config["args"][:2] == ["-y", "@modelcontextprotocol/server-filesystem@2.4.0"]
    assert "env" not in config


@pytest.mark.parametrize("value", ["relative/notes", "/definitely/not/here", "--help"])
def test_a_folder_setting_refuses_anything_but_an_existing_absolute_folder(tmp_path, value):
    with pytest.raises(ValueError, match="isn't an existing folder"):
        integrations.server_config(_proposal(tmp_path), {"Folder": value})


def test_a_required_folder_must_be_given(tmp_path):
    with pytest.raises(ValueError, match="Folder is required"):
        integrations.server_config(_proposal(tmp_path), {})


def test_a_filesystem_request_finds_the_reviewed_guide_first(monkeypatch):
    monkeypatch.setattr(integrations, "_get_json", lambda *a, **k: {"servers": [], "metadata": {}})
    found = integrations.search("MCP filesystem server for my notes folder")
    first = (found.get("items") or found.get("results") or [{}])[0]
    assert first["id"] == "guide:filesystem"
    assert first["publisher"].startswith("official")
    assert first["settings"][0]["kind"] == "arg"


def test_reading_tools_are_safe_and_writes_ask():
    from agent.integration_guides import guide
    policies = guide("filesystem")["capability_policies"]
    assert policies["list_directory"] == "read" and policies["read_text_file"] == "read"
    assert not any(name in policies for name in ("write_file", "edit_file", "move_file", "create_directory"))
