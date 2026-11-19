"""Settings lab: the owner's Pong session (18 November 2026), replayed, then fixed from the terminal.

Every action switch is off, as it was for the owner. Echo is asked to build a Pong game in the project;
the backend doesn't offer file_write, and the scripted model answers with the owner's session's own
words. Then, only with the keyboard: the hint, /settings → Permissions → Write files (with the master
switch it needs), the same request now writing pong.html after an approval, /tools for Glados, and
/permissions files off. Each step is checked through the backend's API, not only on screen.

    apps/terminal/.venv/bin/python apps/terminal/tests/settingslab.py [--out DIR] [--no-png]
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import sizelab as lab  # noqa: E402
import uilab  # noqa: E402
from fake_model import ScriptedModel  # noqa: E402

from echospeak_cli import control  # noqa: E402

PONG = ("I'd love to help you build a Pong game! However, I need to let you know that my file creation permissions "
        "are currently turned off in EchoSpeak settings (file editing is disabled).")
GAME = "<!doctype html><title>Pong</title><canvas id=c width=640 height=400></canvas><script>/* pong */</script>\n"
SCRIPTS = [("pong game", [
    {"tools": [{"name": "file_write", "args": {"path": "pong.html", "content": GAME}}], "if_missing": PONG},
    {"text": "Saved pong.html. Open it in a browser to play: arrow keys move your paddle."},
])]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(os.environ.get("CLAUDE_JOB_DIR", tempfile.gettempdir())) / "settingslab"))
    ap.add_argument("--no-png", action="store_true")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    checks: dict[str, bool] = {}
    L = uilab.Lab(out, "owner")
    with tempfile.TemporaryDirectory(prefix="echoset-") as tmp, ScriptedModel(SCRIPTS, "Okay.", delay=0.01) as model:
        # No ENABLE_SYSTEM_ACTIONS / ALLOW_*: every action switch starts off, as the owner's did.
        proc, dirs, client = lab.start_backend(Path(tmp), model.url, {"HOME": str(Path(tmp) / "home"),
                                                                       "PRIVACY_MODE": "private"})
        try:
            project = dirs["cwd"] / "work"
            project.mkdir()
            seed = client.create_thread("Earlier")["thread_id"]
            client.post("/projects/attach-folder", {"path": str(project), "trust_state": "trusted", "session_id": seed})
            s = control.settings_of(client)
            checks["starts with every action switch off"] = not any(s.get(k) for k, _l, _d in control.PERMISSIONS)
            env = {"ECHOSPEAK_RUNTIME_DIR": str(dirs["rt"]), "ECHOSPEAK_STATE_DIR": str(dirs["state"] / "set"),
                   "TERM": "tmux-256color", "COLORTERM": "truecolor", "LANG": "C.UTF-8", "HOME": str(dirs["home"]),
                   "ECHO_REDUCED_MOTION": "1"}
            L.launch(project, env)
            L.wait("for shortcuts|sessions", 30)
            time.sleep(1.0)

            # 1. The owner's request, blocked, now with a hint.
            L.send("lets create a pong game here and launch it")
            L.idle()
            time.sleep(0.8)
            L.shot("1-blocked-with-hint", "the owner's request, with the hint")
            screen = L.screen()
            checks["the blocked answer is shown"] = "turned off in EchoSpeak settings" in screen
            checks["one hint names Write files and /permissions"] = "Write files" in screen and "/permissions" in screen

            # 2. /settings → Permissions → Write files → yes.
            L.send("/settings")
            checks["/settings opens the hub"] = L.wait("Agents and their tools", 10)
            time.sleep(0.6)
            L.shot("2-settings-hub", "/settings")
            lab.keys("1")
            checks["Permissions opens"] = L.wait("Approval mode:", 10)
            time.sleep(0.6)
            L.shot("3-permissions-all-off", "every switch off")
            lab.keys("3")                                   # 1 approval mode, 2 actions, 3 write files
            checks["turning on Write files says it needs the master switch"] = L.wait("both will be turned on", 10)
            time.sleep(0.6)
            L.shot("4-confirm-both", "Write files needs Actions on this computer")
            lab.keys("y")
            time.sleep(1.2)
            L.shot("5-permissions-after", "both on; the list stays open")
            s = control.settings_of(client)
            checks["backend: both switches on"] = bool(s.get("enable_system_actions")) and bool(s.get("allow_file_write"))
            checks["backend: nothing else turned on"] = not any(s.get(k) for k, _l, _d in control.PERMISSIONS
                                                               if k not in {"enable_system_actions", "allow_file_write"})
            lab.keys("Escape")
            time.sleep(0.6)
            lab.keys("Escape")
            time.sleep(0.8)

            # 3. The same request works now.
            L.send("lets create a pong game here and launch it")
            if L.wait("needs your OK", 30):
                time.sleep(0.8)
                L.shot("6-approval-to-write", "the write asks first")
                lab.keys("y")
            L.idle()
            time.sleep(0.8)
            L.shot("7-pong-written", "pong.html written")
            checks["pong.html was written in the project"] = (project / "pong.html").exists()

            # 4. /tools Glados → Desktop → yes.
            L.send("/tools glados")
            checks["/tools opens Glados's tool groups"] = L.wait("Glados's tools", 10)
            time.sleep(0.6)
            L.shot("8-glados-tools", "what Glados may use")
            screen = L.screen()
            line = next((i for i, row in enumerate(screen.split("\n")) if "Mouse, keyboard" in row), None)
            number = next((row.strip().split(".")[0] for row in screen.split("\n") if "Mouse, keyboard" in row), "")
            if number.split()[-1:] and number.split()[-1].isdigit():
                lab.keys(number.split()[-1])
                L.wait("Let Glados use", 10)
                time.sleep(0.6)
                L.shot("9-glados-desktop-confirm", "desktop control asks first")
                lab.keys("y")
                time.sleep(1.2)
            agents = {a["name"].lower(): a for a in (client.get("/lean/agents") or {}).get("items") or []}
            checks["backend: Glados can use desktop control"] = "desktop" in (agents.get("glados") or {}).get("toolsets", [])
            checks["backend: Glados kept her other groups"] = {"core", "terminal"} <= set((agents.get("glados") or {})
                                                                                          .get("toolsets", []))
            L.shot("10-glados-after", "desktop on, needing its switches")
            lab.keys("Escape")
            time.sleep(0.8)

            # 5. Off again, from the command line.
            L.send("/permissions files off")
            time.sleep(1.5)
            checks["backend: Write files off again"] = not control.settings_of(client).get("allow_file_write")
            L.shot("11-files-off", "/permissions files off")
            L.shot("99-scrollback", "the whole session", history=True)
            L.stop()
        finally:
            lab.tmux("kill-server", check=False)
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=30)
            except Exception:  # noqa: BLE001
                proc.kill()
    (out / "index.json").write_text(json.dumps({"shots": L.index, "checks": checks}, indent=1))
    if not args.no_png:
        L.render()
    for name, ok in checks.items():
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"{sum(checks.values())} of {len(checks)} passed · captures in {out}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
