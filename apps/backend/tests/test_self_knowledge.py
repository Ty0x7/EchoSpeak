"""Echo's self-knowledge: the running version is authoritative over app names, memories and old chats;
the installed app's own folder is never treated as the user's workspace or project; the retired app
whitelist migrates to suggestions without touching the blocklist."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

import config as config_module
from agent.lean import memory_quality
from agent.lean.personas import AgentPersona
from agent.lean.prompt import build_system_prompt, identity_section
from version import APP_VERSION


def _prompt(**kwargs: Any) -> str:
    return build_system_prompt(persona=AgentPersona(id="echo", name="Echo"), soul_text="You are Echo.", **kwargs)


# ── identity ────────────────────────────────────────────────────────────

def test_identity_comes_from_the_running_app():
    text = identity_section(AgentPersona(id="echo", name="Echo"))
    assert f"version {APP_VERSION}" in text
    assert "created by Ty (ty0x7)" in text
    assert "https://github.com/Ty0x7/EchoSpeak" in text
    assert "never answer \"what version are you\" from them" in text


def test_identity_follows_the_version_module(monkeypatch):
    import version

    monkeypatch.setattr(version, "APP_VERSION", "99.1.0")
    assert "version 99.1.0" in identity_section(AgentPersona(id="echo", name="Echo"))


def test_identity_names_the_desktop_app_only_when_installed(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert "the EchoSpeak desktop app" in identity_section(AgentPersona(id="echo", name="Echo"))
    monkeypatch.delattr(sys, "frozen")
    assert "development build" in identity_section(AgentPersona(id="echo", name="Echo"))


def test_live_version_stays_authoritative_next_to_a_misleading_app_name_and_memory():
    # The 26.2 regression: a shortcut name and a stale memory sit in the same prompt.
    prompt = _prompt(
        computer_note='App or shortcut names the owner often opens: ["26.2", "minecraft launcher"].',
        memories=[{"content": "The current version of EchoSpeak is 10.4.1", "type": "note"},
                  {"content": "Ty likes Minecraft", "type": "note"}],
    )
    assert f"version {APP_VERSION}" in prompt
    assert "10.4.1" not in prompt            # the stale claim is left out of recall
    assert "Ty likes Minecraft" in prompt    # unrelated memories are untouched
    # The identity comes before any context that could carry another number.
    assert prompt.index(f"version {APP_VERSION}") < prompt.index("26.2")


def test_identity_is_in_the_system_message_that_compaction_never_rewrites():
    # loop._fit_context drops history from index 1 and _compact_turn rewrites the user message;
    # messages[0] (this prompt) is rebuilt every turn, so the identity can't be compacted away.
    from agent.lean import loop
    import inspect

    source = inspect.getsource(loop.LeanTurn._fit_context) + inspect.getsource(loop.LeanTurn._compact_turn)
    assert "messages.pop(1)" in source and "messages[0]" not in source
    assert APP_VERSION in _prompt(chat_summary="Earlier: the user asked about version 26.2 of a game.")


@pytest.mark.parametrize("text, stale", [
    ("User: search what verison echospeak is currently on AI: The current version is 10.4.1.", True),
    ("The current version of EchoSpeak is 10.4.1", True),
    ("You're on version 11.2", True),
    ("Ty's Minecraft shortcut is called 26.2", False),
    ("Ty prefers Python 3.12 for scripts", False),
    ("EchoSpeak is Ty's personal agent project", False),
])
def test_only_claims_about_the_current_echospeak_version_are_stale(text, stale):
    assert memory_quality.stale_version_claim(text) is stale


# ── workspace and project_status ────────────────────────────────────────

def test_installed_app_defaults_to_documents_not_its_install_folder(monkeypatch, tmp_path):
    install = tmp_path / "AppData" / "Local" / "EchoSpeak"
    install.mkdir(parents=True)
    home = tmp_path / "home"
    (home / "Documents").mkdir(parents=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(config_module, "REPO_ROOT", install)
    monkeypatch.setattr(config_module.Path, "home", classmethod(lambda cls: home))
    assert config_module.app_install_dir() == install.resolve()
    assert Path(config_module._workspace_root("")) == (home / "Documents").resolve()
    # An old saved value pointing into the install folder is replaced too.
    assert Path(config_module._workspace_root(str(install / "backend"))) == (home / "Documents").resolve()
    # The owner's own choice is kept.
    mine = tmp_path / "Projects"
    mine.mkdir()
    assert Path(config_module._workspace_root(str(mine))) == mine.resolve()


def test_source_checkout_keeps_the_repo_as_its_workspace(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert config_module.app_install_dir() is None
    assert config_module.default_workspace() == config_module.REPO_ROOT


def test_project_status_refuses_to_call_the_install_folder_a_project(monkeypatch, tmp_path):
    from agent import tools
    from config import config

    install = tmp_path / "EchoSpeak"
    (install / "backend").mkdir(parents=True)
    monkeypatch.setattr(config_module, "app_install_dir", lambda: install.resolve())
    monkeypatch.setattr(config, "file_tool_root", str(install))
    monkeypatch.setattr(tools, "_tool_scope_denial", lambda name: "")
    monkeypatch.setattr(tools, "_safe_file_path", lambda raw: install)
    out = tools.project_status.invoke({"workspace_path": "."})
    assert "EchoSpeak's own install folder" in out and "not a user project" in out


def test_project_status_labels_the_workspace_when_no_project_is_attached(monkeypatch, tmp_path):
    from agent import tools
    from config import config

    workspace = tmp_path / "Documents"
    workspace.mkdir()
    monkeypatch.setattr(config_module, "app_install_dir", lambda: None)
    monkeypatch.setattr(config, "file_tool_root", str(workspace))
    monkeypatch.setattr(tools, "get_active_project_root", lambda: None)
    monkeypatch.setattr(tools, "_tool_scope_denial", lambda name: "")
    monkeypatch.setattr(tools, "_safe_file_path", lambda raw: workspace)
    out = tools.project_status.invoke({"workspace_path": "."})
    assert "**Kind:** Your default workspace folder (no project is attached to this chat)" in out


# ── the retired app whitelist ───────────────────────────────────────────

def test_saved_whitelist_migrates_to_favorites_and_keeps_the_blocklist(monkeypatch, tmp_path):
    path = tmp_path / "settings.json"
    payload = {"open_application_allowlist": ["26.2", "Minecraft Launcher"],
               "open_application_blocklist": ["regedit", "steam"]}
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(config_module, "SETTINGS_PATH", path)
    migrated = config_module._migrate_app_allowlist(json.loads(path.read_text(encoding="utf-8")))
    assert "open_application_allowlist" not in migrated
    assert migrated["open_application_favorites"] == ["26.2", "minecraft launcher"]
    assert migrated["open_application_blocklist"] == ["regedit", "steam"]
    assert json.loads(path.read_text(encoding="utf-8")) == migrated  # saved once
    assert config_module._migrate_app_allowlist(dict(migrated)) == migrated  # idempotent


def test_new_installs_block_system_settings_tools_by_default():
    for name in ("regedit", "gpedit.msc", "secpol.msc"):
        assert name in config_module.DEFAULT_APP_BLOCKLIST
