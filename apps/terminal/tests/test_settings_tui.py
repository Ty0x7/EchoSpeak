"""Settings and agent permissions from the terminal (cli-design.md §10).

Prompted by a real session (18 November 2026): the owner asked Echo to build a Pong game, Echo said
file creation was turned off in settings, and the terminal offered no way to see or change that. Every
action switch was off, and "Write files" also needs the master switch "Actions on this computer".

Pure helpers are tested directly; the screens through the app's real key bindings, against a client
that records what is written, so a screen that looks right but saves the wrong thing fails here.
"""
from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("prompt_toolkit")
pytest.importorskip("rich")

from prompt_toolkit.keys import Keys  # noqa: E402

from echospeak_cli import control  # noqa: E402
from echospeak_cli.tui import blocks as B  # noqa: E402

from test_sessions import RecordingClient, press  # noqa: E402
from test_sessions import session as _session  # noqa: E402

ALL_OFF = {key: False for key, _l, _d in control.PERMISSIONS}
PONG = ("I'd love to help you build a Pong game! However, I need to let you know that my file creation "
        "permissions are currently turned off in EchoSpeak settings (file editing is disabled).")


# ── pure ─────────────────────────────────────────────────────────────
def test_a_switch_that_is_on_without_the_master_switch_is_blocked():
    s = {**ALL_OFF, "allow_file_write": True}
    assert control.switch_state(s, "allow_file_write") == "blocked"
    assert control.switch_state({**s, "enable_system_actions": True}, "allow_file_write") == "on"
    assert control.switch_state(ALL_OFF, "allow_file_write") == "off"
    assert control.switch_state(ALL_OFF, "enable_system_actions") == "off"


def test_turning_on_a_switch_also_turns_on_what_it_needs():
    assert control.enable_plan(ALL_OFF, "allow_file_write") == ["enable_system_actions", "allow_file_write"]
    assert control.enable_plan({**ALL_OFF, "enable_system_actions": True}, "allow_file_write") == ["allow_file_write"]
    assert control.enable_plan(ALL_OFF, "enable_system_actions") == ["enable_system_actions"]


def test_switches_can_be_named_the_way_people_say_them():
    assert control.switch_key("files") == "allow_file_write"
    assert control.switch_key("terminal") == "allow_terminal_commands"
    assert control.switch_key("actions") == "enable_system_actions"
    assert control.switch_key("Write files") == "allow_file_write"
    assert control.switch_key("nonsense") is None


def test_the_owners_pong_answer_gets_a_hint_naming_the_switches_that_are_really_off():
    hint = control.blocked_hint([PONG], ALL_OFF)
    assert hint and "Write files" in hint and "Actions on this computer" in hint and "/permissions" in hint
    assert control.blocked_hint([PONG], {**ALL_OFF, "enable_system_actions": True, "allow_file_write": True}) is None
    assert control.blocked_hint(["Here is your Pong game, saved to pong.html."], ALL_OFF) is None
    # Explaining permissions is not being blocked by one.
    assert control.blocked_hint(["chmod changes a file's permissions; 644 means the owner can write it."], ALL_OFF) is None
    term = control.blocked_hint(["Error: terminal commands are disabled in Settings > Permissions."],
                                {**ALL_OFF, "enable_system_actions": True})
    assert term and "Terminal commands" in term and "Actions on this computer" not in term


def test_agent_tool_groups_have_names_and_say_which_switch_still_gates_them():
    rows = control.toolset_rows(["core", "terminal", "web"], ["core", "web", "terminal", "desktop"], ALL_OFF)
    by_id = {r["id"]: r for r in rows}
    assert by_id["core"]["label"] == "Files and basics" and by_id["core"]["on"]
    assert "Write files" in by_id["core"]["needs"]               # writing still needs the global switch
    assert "Terminal commands" in by_id["terminal"]["needs"]
    assert by_id["desktop"]["on"] is False and by_id["web"]["needs"] == ""
    on = {**ALL_OFF, "enable_system_actions": True, "allow_terminal_commands": True}
    assert control.toolset_rows(["terminal"], ["terminal"], on)[0]["needs"] == ""


# ── in the app ───────────────────────────────────────────────────────
class SettingsClient(RecordingClient):
    def __init__(self):
        super().__init__()
        self.settings = dict(ALL_OFF, lean_approval_mode="smart", privacy_mode="standard", web_search_provider="auto",
                             assistant_mode="unified")
        self.agents = [{"id": "echo", "name": "Echo", "title": "Personal agent", "toolsets": ["core", "web", "memory"]},
                       {"id": "forge", "name": "Glados", "title": "Builder", "toolsets": ["core", "terminal", "web"]}]
        self.writes: list[tuple[str, str, dict]] = []

    def get(self, path, **params):
        if path == "/settings":
            self.calls.append((path, params))
            return {"settings": dict(self.settings)}
        if path == "/lean/agents":
            return {"items": [dict(a) for a in self.agents]}
        if path == "/lean/toolsets":
            return {"toolsets": [{"id": t, "tools": []} for t in ("core", "web", "live", "terminal", "vision", "desktop",
                                                                   "comms", "self", "memory", "skills")]}
        return super().get(path, **params)

    def request(self, method, path, body=None, params=None):
        self.writes.append((method, path, body or {}))
        if method == "PUT" and path == "/settings":
            self.settings.update(body or {})
            return {"settings": dict(self.settings)}
        if method == "PATCH" and path.startswith("/lean/agents/"):
            agent = next(a for a in self.agents if a["id"] == path.rsplit("/", 1)[-1])
            agent.update(body or {})
            return dict(agent)
        return {}


async def settle(tui):
    """Yield until the screens have drawn. (The shared settle waits for every task; these loops stay open.)"""
    for _ in range(40):
        await asyncio.sleep(0)


def session(coro_fn, client=None):
    """test_sessions' harness, with the real agent loader (it is stubbed there)."""
    from echospeak_cli.tui import app as A

    async def wrapped(tui):
        tui._load_agents = A.EchoTUI._load_agents.__get__(tui)
        await coro_fn(tui)
    _session(wrapped, client)


def _ready(tui):
    """Let the open dialog take keys now (its 300 ms typed-ahead guard has passed)."""
    if tui.dialog is not None:
        tui.dialog.opened_at = -10.0


async def _step(tui, *keys):
    await settle(tui)
    _ready(tui)
    press(tui, *keys)
    await settle(tui)


def _title(tui) -> str:
    return tui.dialog.title if tui.dialog is not None else ""


def test_settings_opens_a_hub_and_permissions_turn_write_files_on_with_the_master_switch():
    async def go(tui):
        tui._spawn(tui._run_command("/settings"))
        await settle(tui)
        assert "Settings" in _title(tui)
        labels = [o.label for o in tui.dialog.options]
        assert any("Permissions" in label for label in labels) and any("Agents" in label for label in labels)
        await _step(tui, "1")                                         # Permissions
        assert "Permissions" in _title(tui)
        write = next(i for i, o in enumerate(tui.dialog.options) if "Write files" in o.label)
        await _step(tui, str(write + 1))
        assert tui.dialog is not None and "Write files" in _title(tui)
        body = " ".join(t for _s, t in tui.dialog.body)
        assert "Actions on this computer" in body                     # it says what else turns on
        await _step(tui, "y")
        put = [w for w in tui.client.writes if w[0] == "PUT"]
        assert put == [("PUT", "/settings", {"enable_system_actions": True, "allow_file_write": True})]
        assert "Permissions" in _title(tui)                           # the list stays open for the next change
        row = next(o for o in tui.dialog.options if "Write files" in o.label)
        assert "[on]" in row.label
        await _step(tui, Keys.Escape)
        assert "Settings" in _title(tui)                              # back to the hub
        await _step(tui, Keys.Escape)
        assert tui.dialog is None
    session(go, SettingsClient())


def test_permissions_takes_a_switch_name_and_on_or_off():
    async def go(tui):
        tui._spawn(tui._run_command("/permissions terminal on"))
        await _step(tui, "y")
        assert ("PUT", "/settings", {"enable_system_actions": True, "allow_terminal_commands": True}) in tui.client.writes
        tui._spawn(tui._run_command("/permissions terminal off"))
        await settle(tui)
        assert ("PUT", "/settings", {"allow_terminal_commands": False}) in tui.client.writes   # off needs no question
    session(go, SettingsClient())


def test_tools_shows_an_agents_tool_groups_and_saves_a_change():
    async def go(tui):
        tui._spawn(tui._run_command("/tools glados"))
        await settle(tui)
        assert "Glados" in _title(tui)
        labels = [o.label for o in tui.dialog.options]
        assert any(label.startswith("[on]") and "Files and basics" in label for label in labels)
        desktop = next(i for i, o in enumerate(tui.dialog.options) if "Mouse, keyboard" in o.label)
        await _step(tui, str(desktop + 1))
        assert "Desktop" in _title(tui) or "desktop" in " ".join(t for _s, t in tui.dialog.body).lower()
        await _step(tui, "y")                                         # control of the desktop asks first
        patch = [w for w in tui.client.writes if w[0] == "PATCH"]
        assert patch and patch[-1][1] == "/lean/agents/forge" and "desktop" in patch[-1][2]["toolsets"]
        assert set(patch[-1][2]["toolsets"]) >= {"core", "terminal", "web"}   # nothing else was dropped
        web = next(i for i, o in enumerate(tui.dialog.options) if "Web search" in o.label)
        await _step(tui, str(web + 1))                                # turning a group off doesn't ask
        assert "web" not in tui.client.writes[-1][2]["toolsets"]
    session(go, SettingsClient())


def test_privacy_and_web_search_are_changed_from_the_hub():
    async def go(tui):
        tui._spawn(tui._run_command("/settings"))
        await settle(tui)
        privacy = next(i for i, o in enumerate(tui.dialog.options) if o.label.startswith("Privacy"))
        await _step(tui, str(privacy + 1))
        private = next(i for i, o in enumerate(tui.dialog.options) if o.value == "private")
        await _step(tui, str(private + 1))
        assert ("PUT", "/settings", {"privacy_mode": "private"}) in tui.client.writes
        assert "Settings" in _title(tui)
    session(go, SettingsClient())


def test_a_blocked_answer_ends_with_one_line_pointing_at_the_switch():
    async def go(tui):
        from echospeak_cli.tui.state import Run

        tui._out_event = asyncio.Event()
        tui.run = Run("r1", tui.now(), glyphs=tui.caps.glyphs)
        tui.run.feed({"type": "agent_start", "message_id": "m1", "agent": {"id": "echo", "name": "Echo"}}, tui.now())
        tui.run.feed({"type": "agent_done", "message_id": "m1", "text": PONG}, tui.now())
        await tui._end_run("completed")
        said = "".join(tui._out)
        assert "Write files" in said and "/permissions" in said
    session(go, SettingsClient())


def test_the_commands_are_listed():
    from echospeak_cli.tui.commands import COMMANDS, lookup

    assert lookup("/settings", COMMANDS) and lookup("/config", COMMANDS) is lookup("/settings", COMMANDS)
    assert lookup("/tools", COMMANDS)
