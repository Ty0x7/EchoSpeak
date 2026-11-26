"""Echo Companion observation: hook client, normalisation, session states, focus, installer."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from agent.companion import hook_client, install, observe
from agent.companion.observe import CompanionStore, activity, normalise

HOOK = Path(hook_client.__file__)

# Shapes captured from Claude Code 2.1.332 hooks (paths shortened).
CLAUDE = {
    "SessionStart": {"session_id": "s1", "transcript_path": "/t/s1.jsonl", "cwd": "{root}/proj", "hook_event_name": "SessionStart", "source": "startup"},
    "UserPromptSubmit": {"session_id": "s1", "transcript_path": "/t/s1.jsonl", "cwd": "{root}/proj", "prompt_id": "p1",
                         "permission_mode": "default", "hook_event_name": "UserPromptSubmit", "prompt": "Fix the parser"},
    "Stop": {"session_id": "s1", "transcript_path": "/t/s1.jsonl", "cwd": "{root}/proj", "prompt_id": "p1", "permission_mode": "default",
             "hook_event_name": "Stop", "stop_hook_active": False, "last_assistant_message": "Fixed it. Tests pass.",
             "background_tasks": [], "effort": {"level": "medium"}},
    "SessionEnd": {"session_id": "s1", "transcript_path": "/t/s1.jsonl", "cwd": "{root}/proj", "hook_event_name": "SessionEnd", "reason": "other"},
}


def _payload(name: str, root: Path, **extra) -> dict:
    data = json.loads(json.dumps(CLAUDE[name]).replace("{root}", str(root)))
    data.update(extra)
    return data


@pytest.fixture()
def home(tmp_path, monkeypatch):
    home = tmp_path / "companion"
    root = tmp_path / "work"
    (root / "proj").mkdir(parents=True)
    (tmp_path / "elsewhere").mkdir()
    home.mkdir()
    (home / "scope.json").write_text(json.dumps({"enabled": True, "agents": {"claude-code": True, "codex": True},
                                                 "roots": [str(root)], "capture_prompts": True}))
    monkeypatch.setenv("ECHOSPEAK_COMPANION_HOME", str(home))
    return home, root


def _run_hook(agent: str, payload: dict, home: Path, *argv: str) -> subprocess.CompletedProcess:
    import os
    env = {**os.environ, "ECHOSPEAK_COMPANION_HOME": str(home)}
    return subprocess.run([sys.executable, str(HOOK), agent, *argv], input=json.dumps(payload) if not argv else "",
                          capture_output=True, text=True, env=env, timeout=10)


def _spooled(home: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted((home / "spool").glob("*.json"))] if (home / "spool").exists() else []


def test_hook_is_silent_and_keeps_only_allowlisted_fields(home):
    home_dir, root = home
    payload = _payload("Stop", root, tool_input={"command": "cat ~/.ssh/id_rsa"}, tool_response="secret output",
                       last_assistant_message="Done. Used token=abcd1234efgh5678 and sk-live_ABCDEFGHIJKLMNOPQRST.")
    result = _run_hook("claude-code", payload, home_dir)
    assert result.returncode == 0 and result.stdout == "" and result.stderr == ""
    [record] = _spooled(home_dir)
    event = record["event"]
    assert "tool_input" not in event and "tool_response" not in event and "background_tasks" not in event
    assert "abcd1234efgh5678" not in event["last_assistant_message"] and "sk-live" not in event["last_assistant_message"]
    assert "[redacted]" in event["last_assistant_message"]
    mode = (home_dir / "spool").stat().st_mode & 0o777
    assert mode == 0o700


def test_hook_drops_events_outside_chosen_folders_and_disabled_agents(home, tmp_path):
    home_dir, root = home
    outside = _payload("Stop", root, cwd=str(tmp_path / "elsewhere"))
    assert _run_hook("claude-code", outside, home_dir).returncode == 0
    sneaky = _payload("Stop", root, cwd=str(root) + "-other")
    assert _run_hook("claude-code", sneaky, home_dir).returncode == 0
    assert _spooled(home_dir) == []
    scope = json.loads((home_dir / "scope.json").read_text())
    scope["agents"]["claude-code"] = False
    (home_dir / "scope.json").write_text(json.dumps(scope))
    _run_hook("claude-code", _payload("Stop", root), home_dir)
    assert _spooled(home_dir) == []


def test_hook_survives_garbage_and_unknown_agents(home):
    home_dir, _ = home
    import os
    env = {**os.environ, "ECHOSPEAK_COMPANION_HOME": str(home_dir)}
    for agent, data in (("claude-code", "not json"), ("someone", "{}"), ("codex", "[1,2]")):
        result = subprocess.run([sys.executable, str(HOOK), agent], input=data, capture_output=True, text=True, env=env, timeout=10)
        assert result.returncode == 0 and result.stdout == ""
    assert _spooled(home_dir) == []


def test_prompt_capture_can_be_turned_off(home):
    home_dir, root = home
    scope = json.loads((home_dir / "scope.json").read_text())
    scope["capture_prompts"] = False
    (home_dir / "scope.json").write_text(json.dumps(scope))
    _run_hook("claude-code", _payload("UserPromptSubmit", root), home_dir)
    assert "prompt" not in _spooled(home_dir)[0]["event"]


def test_codex_notify_argument_and_stop_hook_are_one_turn(home, tmp_path):
    home_dir, root = home
    notify = {"type": "agent-turn-complete", "thread-id": "th1", "turn-id": "t9", "cwd": str(root / "proj"),
              "input-messages": ["fix config"], "last-assistant-message": "Repaired the config: a missing comma."}
    assert _run_hook("codex", {}, home_dir, json.dumps(notify)).returncode == 0
    stop = {"session_id": "th1", "turn_id": "t9", "cwd": str(root / "proj"), "hook_event_name": "Stop",
            "last_assistant_message": "Repaired the config: a missing comma.", "model": "gpt-5"}
    _run_hook("codex", stop, home_dir)
    store = CompanionStore(tmp_path / "o.sqlite3", home_dir / "spool")
    assert store.ingest_spool() == 1
    [session] = store.sessions()
    assert session["agent"] == "codex" and session["turns"] == 1 and session["state"] == "your_turn"
    assert "missing comma" in session["last_message"]
    assert list((home_dir / "spool").glob("*.json")) == []


def test_session_lifecycle_states(home, tmp_path):
    home_dir, root = home
    store = CompanionStore(tmp_path / "o.sqlite3", home_dir / "spool")
    t = 1000.0
    for offset, name in enumerate(("SessionStart", "UserPromptSubmit")):
        store.add(normalise({"agent": "claude-code", "received_at": t + offset, "event": _payload(name, root)}))
    assert store.sessions(now=t + 2)[0]["state"] == "working"
    assert store.sessions(now=t + 2 + observe.QUIET_AFTER + 1)[0]["state"] == "quiet"
    store.add(normalise({"agent": "claude-code", "received_at": t + 3, "event": {
        **_payload("Stop", root), "hook_event_name": "Notification", "notification_type": "permission_prompt",
        "message": "Claude needs your permission to use Bash"}}))
    row = store.sessions(now=t + 4)[0]
    assert row["state"] == "needs_permission" and "permission" in row["attention"]
    store.add(normalise({"agent": "claude-code", "received_at": t + 5, "event": _payload("Stop", root)}))
    row = store.sessions(now=t + 6)[0]
    assert row["state"] == "your_turn" and row["turns"] == 1 and row["last_prompt"] == "Fix the parser"
    assert row["project"] == "proj"
    store.add(normalise({"agent": "claude-code", "received_at": t + 7, "event": _payload("SessionEnd", root)}))
    assert store.sessions(now=t + 8)[0]["state"] == "ended"


def test_focus_is_latest_prompt_and_close_calls_are_uncertain(home, tmp_path, monkeypatch):
    home_dir, root = home
    monkeypatch.setattr(observe, "echo_sessions", lambda limit=30: [])
    store = CompanionStore(tmp_path / "o.sqlite3", home_dir / "spool")
    t = 10_000.0
    store.add(normalise({"agent": "codex", "received_at": t, "event": {"session_id": "cx", "turn_id": "a", "cwd": str(root / "proj"),
                                                                        "hook_event_name": "UserPromptSubmit", "prompt": "long refactor"}}))
    store.add(normalise({"agent": "claude-code", "received_at": t + 120, "event": _payload("UserPromptSubmit", root)}))
    result = activity(store, include_echo=False, now=t + 130)
    assert result["focus"]["agent"] == "claude-code" and not result["focus"]["uncertain"]
    by_agent = {row["agent"]: row for row in result["sessions"]}
    assert by_agent["claude-code"]["interacting"] and by_agent["codex"]["background"]
    store.add(normalise({"agent": "codex", "received_at": t + 125, "event": {"session_id": "cx", "turn_id": "b", "cwd": str(root / "proj"),
                                                                              "hook_event_name": "UserPromptSubmit", "prompt": "and this"}}))
    result = activity(store, include_echo=False, now=t + 130)
    assert result["focus"]["uncertain"] and len(result["focus"]["candidates"]) == 2


def test_malformed_spool_files_are_discarded(home, tmp_path):
    home_dir, _ = home
    spool = home_dir / "spool"
    spool.mkdir()
    (spool / "1-bad.json").write_text("{not json")
    (spool / "2-odd.json").write_text(json.dumps({"agent": "evil", "event": {"hook_event_name": "Stop", "session_id": "x"}}))
    store = CompanionStore(tmp_path / "o.sqlite3", spool)
    assert store.ingest_spool() == 0 and list(spool.iterdir()) == []


def test_installer_preserves_existing_hooks_and_is_reversible(home, tmp_path):
    claude, codex = tmp_path / "claude", tmp_path / "codex"
    claude.mkdir()
    codex.mkdir()
    mine = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "notify-send done"}]}]}, "theme": "dark"}
    (claude / "settings.json").write_text(json.dumps(mine))
    (codex / "config.toml").write_text('model = "gpt-5"\n\n[features]\nhooks = true\n')
    changes = install.plan(claude_dir=claude, codex_dir=codex, add=True)
    assert len(changes) == 3
    result = install.apply(changes, add=True, roots=[str(tmp_path / "work")], agents=("claude-code", "codex"))
    assert len(result["backups"]) == 2  # settings.json and config.toml existed
    settings = json.loads((claude / "settings.json").read_text())
    assert settings["theme"] == "dark"
    stop_commands = [h["command"] for g in settings["hooks"]["Stop"] for h in g["hooks"]]
    assert "notify-send done" in stop_commands and any(install.MARK in c for c in stop_commands)
    toml = (codex / "config.toml").read_text()
    assert toml.splitlines()[0].startswith("notify = [") and toml.index("notify") < toml.index("[features]")
    assert install.plan(claude_dir=claude, codex_dir=codex, add=True) == []  # idempotent
    assert install.status(claude_dir=claude, codex_dir=codex)["claude_code"]
    install.apply(install.plan(claude_dir=claude, codex_dir=codex, add=False), add=False, roots=[], agents=("claude-code", "codex"))
    assert json.loads((claude / "settings.json").read_text()) == mine
    assert install.MARK not in (codex / "config.toml").read_text()
    assert "hooks" not in json.loads((codex / "hooks.json").read_text())


def test_installer_never_replaces_an_existing_codex_notify(tmp_path, home):
    codex = tmp_path / "codex"
    codex.mkdir()
    (codex / "config.toml").write_text('notify = ["my-notifier"]\n')
    changes = install.plan(claude_dir=tmp_path / "claude", codex_dir=codex, add=True, agents=("codex",))
    assert all(c["path"].name != "config.toml" for c in changes)


def test_echo_conversations_and_named_agents_join_the_same_list(monkeypatch, tmp_path, home):
    import agent.session_overview as overview_module

    rows = [
        {"thread_id": "t1", "title": "Harness work", "kind": "echo", "agents": [{"id": "echo", "name": "Echo"}],
         "status": "running", "created_at": 1.0, "last_active_at": 9_990.0, "message_count": 6,
         "project": {"name": "EchoSpeak", "root": "/w/echospeak"}, "last_request": "Run the evals", "preview": "Running…"},
        {"thread_id": "t2", "title": "Ops", "kind": "agent", "agents": [{"id": "glados", "name": "Glados"}],
         "status": "needs_approval", "created_at": 1.0, "last_active_at": 9_000.0, "message_count": 2,
         "project": None, "last_request": "Clean logs", "preview": ""},
    ]
    monkeypatch.setattr(overview_module, "overview", lambda limit=30: rows)
    store = CompanionStore(tmp_path / "o.sqlite3", home[0] / "spool")
    result = activity(store, include_echo=True, now=10_000.0)
    by_label = {r["label"]: r for r in result["sessions"]}
    assert by_label["Echo"]["state"] == "working" and by_label["Echo"]["project"] == "EchoSpeak"
    assert by_label["Glados"]["state"] == "needs_permission"
    assert result["focus"]["agent"] == "echo" and result["focus"]["label"] == "Echo"
