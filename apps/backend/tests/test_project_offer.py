"""Chat without a project, asked for work in a folder, offers to work there (owner decision on #187)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.lean import approvals, chat_mode, project_offer, settings


def test_folders_that_can_be_offered(tmp_path):
    game = tmp_path / "GTA_CLONE"
    game.mkdir()
    (game / "game.js").write_text("//")
    assert project_offer.check_folder(str(game))[0] == game.resolve()
    assert project_offer.check_folder(str(tmp_path / "missing"))[0] is None
    assert project_offer.check_folder(str(game / "game.js"))[0] is None              # a file
    assert project_offer.check_folder("GTA_CLONE")[0] is None                        # not a full path
    assert project_offer.check_folder(str(Path.home()))[0] is None                   # the whole home folder
    assert project_offer.check_folder("/")[0] is None


@pytest.mark.parametrize("mode", ["always", "smart", "autonomous", "never"])
def test_the_offer_always_asks_the_owner(mode, monkeypatch):
    monkeypatch.setattr(settings, "approval_mode", lambda: mode)
    needs, reason = approvals.tool_needs_approval(SimpleNamespace(is_action=True), "use_project_folder", {"path": "/x"})
    assert needs and "change files and run commands" in reason


def test_chat_may_offer_it_and_is_told_to(monkeypatch):
    assert chat_mode.allowed("use_project_folder", None)
    monkeypatch.setattr(chat_mode, "_unified", lambda: True)
    assert "use_project_folder" in chat_mode.rules() and "instead of writing the work into artifacts" in chat_mode.rules()
    assert "use_project_folder" in chat_mode.rules(compact=True)


def test_approval_attaches_and_the_turn_end_switches_and_continues(tmp_path, monkeypatch):
    folder = tmp_path / "pong"
    folder.mkdir()
    attached, activated, queued = [], [], []

    class PM:
        def attach_folder(self, path, trust_state="trusted"):
            attached.append((path, trust_state))
            return SimpleNamespace(id="proj-1")

    class Agent:
        import threading
        _request_lock = threading.RLock()

        def activate_project(self, pid):
            activated.append(pid)
            return True

    class Store:
        def enqueue_turn(self, sid, message, client_request_id):
            queued.append((sid, message))

    import agent.projects
    import agent.state
    import api.deps
    monkeypatch.setattr(agent.projects, "get_project_manager", lambda: PM())
    monkeypatch.setattr(api.deps, "get_agent", lambda sid: Agent())
    monkeypatch.setattr(api.deps, "_apply_thread_scope", lambda agent, sid: None)
    monkeypatch.setattr(agent.state, "get_state_store", lambda: Store())

    tool = project_offer.tool("s1", "continue development on pong and use tests")
    reply = tool.func({"path": str(folder)})
    assert attached == [(str(folder.resolve()), "trusted")] and "Don't write code or create artifacts" in reply
    assert not activated and not queued                     # nothing moves under the running turn
    assert project_offer.apply_pending("s1")
    assert activated == ["proj-1"] and queued and queued[0][0] == "s1"
    assert "continue development on pong" in queued[0][1] and str(folder.resolve()) in queued[0][1]
    assert not project_offer.apply_pending("s1")            # once


def test_a_bad_folder_is_an_error_not_an_attachment(monkeypatch):
    import agent.projects
    monkeypatch.setattr(agent.projects, "get_project_manager", lambda: pytest.fail("must not attach"))
    assert project_offer.tool("s2", "x").func({"path": "/definitely/not/here"}).startswith("Error:")
    assert project_offer.pending("s2") is None
