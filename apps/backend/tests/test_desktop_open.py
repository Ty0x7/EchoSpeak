"""Opening what Echo built on a Linux desktop (agent/desktop_open.py), with a fake desktop: no real launches.

Owner's GTA_CLONE session (26 November 2026): `firefox … &` (not installed) and `xdg-open … &` ran in the sandbox,
which has no screen; both exited 0 and the terminal's note said they had opened.
"""

import json
import os
from pathlib import Path
import stat
import subprocess

import pytest

from agent import desktop_open
from agent.lean.terminal import launcher_note

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the freedesktop opener is for Linux desktops")


# ── the terminal note on the session's real commands and outputs ─────────────────────────────────────────────────
def test_a_backgrounded_opener_in_the_sandbox_is_never_reported_as_opened():
    note = launcher_note("xdg-open /home/dev/Work/GTA_CLONE/index.html &", 0, "", "", "bwrap")
    assert note.startswith("Hint: nothing opened") and "open_application" in note
    assert "means it was opened" not in note


def test_a_missing_browser_is_reported_as_not_installed():
    err = "/usr/bin/bash: line 1: firefox: command not found"
    for where in ("bwrap", "host"):
        note = launcher_note("firefox /home/dev/Work/GTA_CLONE/index.html &", 0, "", err, where)
        assert note.startswith("Hint: nothing opened"), where
    assert "isn't installed" in launcher_note("firefox index.html", 127, "", err, "host")


def test_a_backgrounded_opener_on_this_pc_is_unverified():
    note = launcher_note("xdg-open /home/dev/Work/GTA_CLONE/index.html &", 0, "", "", "host")
    assert "isn't known" in note and "open_application" in note


# ── a fake desktop ───────────────────────────────────────────────────────────────────────────────────────────────
def _exe(folder: Path, name: str, body: str) -> Path:
    path = folder / name
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def _entry(apps: Path, file: str, name: str, exec_line: str, mime: str = "", extra: str = "") -> None:
    apps.mkdir(parents=True, exist_ok=True)
    (apps / file).write_text(f"[Desktop Entry]\nType=Application\nName={name}\nExec={exec_line}\n"
                             f"MimeType={mime}\n{extra}")


@pytest.fixture
def desk(tmp_path, monkeypatch):
    """Two browsers (one the default for HTML), a file manager, a stale entry and a fake xdg-mime."""
    bin_dir, data = tmp_path / "bin", tmp_path / "share"
    bin_dir.mkdir()
    apps = data / "applications"
    record = tmp_path / "launched.txt"
    _exe(bin_dir, "goodbrowser", f'echo "goodbrowser $@" >> {record}')
    _exe(bin_dir, "brokenbrowser", f'echo "brokenbrowser $@" >> {record}; echo "cannot open display" >&2; exit 1')
    _exe(bin_dir, "files", f'echo "files $@" >> {record}')
    defaults = {"text/html": "broken.desktop", "inode/directory": "files.desktop",
                "x-scheme-handler/https": "good.desktop"}
    _exe(bin_dir, "xdg-mime", 'if [ "$2" = default ]; then case "$3" in '
         + " ".join(f'{k}) echo {v};;' for k, v in defaults.items()) + ' esac; '
         'else case "$3" in *.html) echo text/html;; esac; fi')
    _entry(apps, "good.desktop", "Good Browser", "goodbrowser %U", "text/html;x-scheme-handler/https;")
    _entry(apps, "broken.desktop", "Broken Browser", "brokenbrowser %U", "text/html;")
    _entry(apps, "files.desktop", "Files", "files %U", "inode/directory;")
    _entry(apps, "gone.desktop", "Gone App", "not-installed-anywhere %F", "text/html;")
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "XDG_DATA_HOME": str(data), "XDG_DATA_DIRS": str(tmp_path / "none"),
           "WAYLAND_DISPLAY": "wayland-test", "XDG_RUNTIME_DIR": str(tmp_path)}
    monkeypatch.setattr(desktop_open, "data_dirs", lambda env=None: [apps])
    # The fake apps run as plain children: no systemd scope, no real compositor.
    monkeypatch.setattr(desktop_open.shutil, "which", _which_in(env["PATH"]))
    monkeypatch.setattr(desktop_open, "windows", lambda env=None: None)
    monkeypatch.setattr(desktop_open, "page_errors", lambda target, env=None, wait_ms=0: None)
    return {"env": env, "record": record, "tmp": tmp_path}


def _which_in(path):
    import shutil

    real = shutil.which      # captured before the patch: desktop_open.shutil is the same module

    def which(cmd, mode=os.F_OK | os.X_OK, path=None):
        return None if cmd == "systemd-run" else real(cmd, mode=mode, path=path or os.environ.get("PATH"))
    return which


def _open(desk, app="", target="", blocked=lambda entry, argv: "", wait=0.6):
    return desktop_open.open_on_desktop(app, target, log_dir=desk["tmp"] / "logs", blocked=blocked,
                                        env=desk["env"], wait=wait)


def test_apps_are_found_by_name_command_or_id_and_stale_entries_are_not(desk):
    env = desk["env"]
    assert desktop_open.find_app("good browser", env)[0].id == "good.desktop"
    assert desktop_open.find_app("goodbrowser", env)[0].id == "good.desktop"
    assert desktop_open.find_app("files", env)[0].id == "files.desktop"
    entry, why = desktop_open.find_app("Gone App", env)
    assert entry is None and "no longer installed" in why
    assert "not installed" in desktop_open.find_app("firefox", env)[1]


def test_exec_field_codes_take_the_target_once():
    entry = desktop_open.Entry("x.desktop", Path("x"), "X", "app --new-window %U %i %c")
    assert desktop_open.exec_argv(entry, "/a b/index.html") == ["app", "--new-window", "/a b/index.html"]
    assert desktop_open.exec_argv(desktop_open.Entry("y", Path("y"), "Y", "viewer"), "/f.png") == ["viewer", "/f.png"]


def test_a_failing_default_app_falls_back_to_another_installed_handler(desk, tmp_path):
    page = tmp_path / "game" / "index.html"
    page.parent.mkdir()
    page.write_text("<title>Game</title><script src='game.js'></script>")
    (page.parent / "game.js").write_text("const canvas = document.querySelector('canvas');")
    out = _open(desk, target=str(page))
    assert "Broken Browser exited with code 1" in out and "Good Browser" in out, out
    assert f"goodbrowser {page}" in desk["record"].read_text()
    assert "wasn't checked" in out          # no window list on this fake desktop: said, not claimed


def test_a_requested_app_that_is_missing_uses_the_default_and_says_so(desk, tmp_path):
    folder = tmp_path / "project"
    folder.mkdir()
    out = _open(desk, app="firefox", target=str(folder))
    assert out.startswith('Tried first: "firefox" is not installed') and "in Files" in out, out


def test_blocked_apps_are_skipped_on_every_route(desk, tmp_path):
    page = tmp_path / "a.html"
    page.write_text("<title>A</title>")
    out = _open(desk, target=str(page), blocked=lambda entry, argv: "goodbrowser" if "goodbrowser" in argv[0] else "")
    assert "Blocked apps (goodbrowser)" in out
    assert "goodbrowser" not in (desk["record"].read_text() if desk["record"].exists() else "")


def test_no_graphical_session_opens_nothing(tmp_path, monkeypatch):
    env = {"PATH": "/usr/bin", "XDG_RUNTIME_DIR": str(tmp_path)}
    out = desktop_open.open_on_desktop("", "/etc/hostname", log_dir=tmp_path, blocked=lambda e, a: "", env=env)
    assert out.startswith("Error: nothing was opened: there is no graphical session")


def test_a_window_that_shows_the_page_verifies_the_launch(desk, tmp_path, monkeypatch):
    page = tmp_path / "index.html"
    page.write_text("<title>GTA Clone</title>")
    seen = iter([[], [{"id": "0x1", "title": "GTA Clone - Good Browser", "app": "goodbrowser", "pid": 1,
                       "where": "monitor DP-1, workspace 11"}]])
    monkeypatch.setattr(desktop_open, "windows", lambda env=None: next(seen, [{"id": "0x1", "title": "GTA Clone - Good Browser", "app": "goodbrowser", "pid": 1, "where": "monitor DP-1, workspace 11"}]))
    out = _open(desk, app="Good Browser", target=str(page), wait=2)
    assert out.startswith("Opened") and 'Verified: window "GTA Clone - Good Browser"' in out and "DP-1" in out, out


def test_targets_are_classified_without_guessing(desk, tmp_path):
    env = desk["env"]
    assert desktop_open.classify("https://example.com/x", env)[0].mime == "x-scheme-handler/https"
    assert desktop_open.classify("example.com", env)[0].uri == "https://example.com"
    assert "full path" in desktop_open.classify("index.html", env)[1]
    assert "aren't opened" in desktop_open.classify("javascript:alert(1)", env)[1]
    assert "does not exist" in desktop_open.classify(str(tmp_path / "missing.html"), env)[1]
    assert desktop_open.classify(f"file://{tmp_path}", env)[0].kind == "folder"


# ── pages that need a server ─────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("html, script, package, expect", [
    ("<script src='game.js'></script>", "let x = 1;", None, ""),                                   # GTA_CLONE's shape
    ("<script type='module' src='main.js'></script>", "", None, "modules"),
    ("<script src='app.js'></script>", "fetch('levels.json').then(r => r.json())", None, "run time"),
    ("<script type='module' src='/src/main.tsx'></script>", "", {"dev": "vite"}, "bundler"),
])
def test_server_needed(tmp_path, html, script, package, expect):
    (tmp_path / "index.html").write_text(html)
    for name in ("game.js", "main.js", "app.js"):
        (tmp_path / name).write_text(script)
    if package:
        (tmp_path / "package.json").write_text(json.dumps({"scripts": package}))
    reason, command = desktop_open.server_needed(tmp_path / "index.html")
    assert (expect in reason) if expect else reason == ""
    if package:
        assert command == "npm run dev"
    elif expect:
        assert command.startswith("python3 -m http.server")


def test_a_page_that_needs_a_server_is_not_opened_as_a_file(desk, tmp_path):
    (tmp_path / "index.html").write_text("<script type='module' src='main.js'></script>")
    out = _open(desk, target=str(tmp_path / "index.html"))
    assert out.startswith("Error: not opened as a file") and "http.server" in out
    assert not desk["record"].exists()


def test_a_localhost_url_with_no_server_is_not_opened(desk):
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    out = _open(desk, target=f"http://127.0.0.1:{port}/")
    assert "nothing is listening" in out and not desk["record"].exists()


# ── the tool and where it is offered ─────────────────────────────────────────────────────────────────────────────
def test_the_tool_keeps_its_permission_switches_and_takes_no_shell_arguments(monkeypatch):
    from agent import tools
    from config import config

    monkeypatch.setattr(config, "enable_system_actions", True)
    monkeypatch.setattr(config, "allow_open_application", False)
    assert "disabled" in tools.open_application.invoke({"target": "/etc/hostname"})
    monkeypatch.setattr(config, "allow_open_application", True)
    assert "no free-form arguments" in tools.open_application.invoke({"app": "chromium", "args": "--no-sandbox"})
    monkeypatch.setattr(config, "open_application_blocklist", ["chromium"])
    assert "Blocked apps" in tools.open_application.invoke({"app": "chromium", "target": "/etc/hostname"})


def test_code_mode_can_present_its_work():
    from agent.lean import code_mode
    from agent.lean.toolbox import TOOLSETS

    assert "open_application" in TOOLSETS["terminal"]
    assert "open_application" not in code_mode._NOT_IN_CODE
    assert "open_application" in code_mode.rules()


# ── looking at what opened (Wayland) ─────────────────────────────────────────────────────────────────────────────
_WINS = [
    {"id": "a", "title": "GTA Clone - Chromium", "app": "chromium", "pid": 1, "where": "monitor DP-1, workspace 11",
     "workspace": 11, "rect": (967, 44, 941, 1024), "floating": False},
    {"id": "b", "title": "omarchy:Claude", "app": "foot", "pid": 2, "where": "monitor DP-1, workspace 11",
     "workspace": 11, "rect": (900, 100, 600, 600), "floating": True},
    {"id": "c", "title": "Notes - Editor", "app": "editor", "pid": 3, "where": "monitor DP-1, workspace 4",
     "workspace": 4, "rect": (0, 0, 800, 600), "floating": False},
]


def test_a_window_is_found_by_title_with_what_hides_it(monkeypatch):
    monkeypatch.setattr(desktop_open, "windows", lambda env=None: _WINS)
    monkeypatch.setattr(desktop_open, "visible_workspaces", lambda env=None: {11, 1})
    win, note = desktop_open.find_window("gta clone")
    assert win["id"] == "a" and "partly covered by \"omarchy:Claude\"" in note
    win, why = desktop_open.find_window("Notes")
    assert win is None and "isn't on screen" in why
    assert "no windows match" in desktop_open.find_window("Pong")[1]


def test_wayland_screenshots_use_the_compositor_capture_and_the_window_rect(monkeypatch):
    from agent.lean import computer
    import io
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (941, 1024), (30, 30, 50)).save(buf, "PNG")
    calls = []
    monkeypatch.setattr(desktop_open, "windows", lambda env=None: _WINS[:1])
    monkeypatch.setattr(desktop_open, "visible_workspaces", lambda env=None: {11})
    monkeypatch.setattr(desktop_open, "wayland_capture", lambda region, env=None: calls.append(region) or buf.getvalue())
    image, screen, source = computer._capture_wayland({"window_title": "GTA Clone"}, [{}, {"left": 0, "top": 0, "width": 1920, "height": 1080, "output": "DP-1"}])
    assert calls == [(967, 44, 941, 1024)] and image.size == (941, 1024) and "DP-1" in source
    computer._capture_wayland({"monitor": 1}, [{}, {"left": 0, "top": 0, "width": 1920, "height": 1080, "output": "DP-1"}])
    assert calls[-1] == (0, 0, 1920, 1080)


def test_code_mode_keeps_screenshots_to_check_what_it_built():
    from agent.lean import code_mode

    assert "take_screenshot" not in code_mode._NOT_IN_CODE and "computer_action" in code_mode._NOT_IN_CODE
    assert "screenshot" in code_mode.rules()


def test_a_page_already_showing_counts_only_in_the_launched_apps_window(desk, tmp_path, monkeypatch):
    """Live run 2: a LibreWolf tab with the same title was reported for a Chromium launch."""
    page = tmp_path / "index.html"
    page.write_text("<title>GTA Clone</title>")
    other = {"id": "lw", "title": "GTA Clone — LibreWolf", "app": "librewolf", "pid": 9, "where": "monitor HDMI-A-1, workspace 1"}
    mine = {"id": "gb", "title": "GTA Clone - Good Browser", "app": "goodbrowser", "pid": 8, "where": "monitor DP-1, workspace 11"}
    monkeypatch.setattr(desktop_open, "windows", lambda env=None: [other])
    out = _open(desk, app="Good Browser", target=str(page), wait=0.5)
    assert "LibreWolf" not in out and out.startswith("Launched"), out
    monkeypatch.setattr(desktop_open, "windows", lambda env=None: [other, mine])
    out = _open(desk, app="Good Browser", target=str(page), wait=0.5)
    assert 'window "GTA Clone - Good Browser"' in out, out


def test_a_page_that_throws_is_reported_beside_the_open_window(desk, tmp_path, monkeypatch):
    """Live run 4: qwen3.5:9b looked at GTA_CLONE's empty canvas and said the game was running."""
    page = tmp_path / "index.html"
    page.write_text("<title>GTA Clone</title>")
    win = {"id": "gb", "title": "GTA Clone - Good Browser", "app": "goodbrowser", "pid": 8, "where": "monitor DP-1, workspace 11"}
    seen = iter([[]])
    monkeypatch.setattr(desktop_open, "windows", lambda env=None: next(seen, [win]))
    monkeypatch.setattr(desktop_open, "page_errors",
                        lambda target, env=None, wait_ms=0: ["Uncaught Identifier 'MapSystem' has already been declared"])
    out = _open(desk, app="Good Browser", target=str(page), wait=2)
    assert "Verified" in out and "MapSystem" in out and "doesn't work as built" in out, out


def test_internet_pages_are_not_fetched_again_for_the_page_check():
    target = desktop_open.Target("https://example.com", "https://example.com", "url", "x-scheme-handler/https")
    assert desktop_open.page_errors(target) is None
