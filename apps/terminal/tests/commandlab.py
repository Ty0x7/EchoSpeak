"""Command lab: every slash command and the main keys in the real TUI, against a real backend.

A private tmux, an isolated backend and a scripted model (no GPU). Each step sends keys, captures
the screen and checks for the text that shows it worked, and that no error or traceback appeared.
It finishes with a backend restart on a new port while the terminal stays open: the next message
must reach the new backend.

    apps/terminal/.venv/bin/python apps/terminal/tests/commandlab.py [--out DIR]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import sizelab as lab  # noqa: E402
from fake_model import FakeModel  # noqa: E402

REPLY = ("The parser treats ISO week 53 as the previous year. That is wrong for long years, so 2026-W53 turns into "
         "2027-W01 and the round trip fails. Check _long_year(y) in iso_week, apply the same rule in parse_week, and "
         "add a test for 2020, 2026 and 2032. That keeps every caller unchanged.")
BAD = re.compile(r"Traceback|Error:|TypeError|AttributeError|KeyError|cannot reach the backend|Window too small")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(os.environ.get("CLAUDE_JOB_DIR", tempfile.gettempdir())) / "commandlab"))
    out = Path(ap.parse_args().out)
    out.mkdir(parents=True, exist_ok=True)
    checks: dict[str, bool] = {}
    shots: dict[str, str] = {}

    def step(name: str, keys: list[str], expect: str, *, wait: float = 1.0, timeout: float = 12.0, history: bool = False) -> str:
        for key in keys:
            lab.keys(key)
            time.sleep(0.15)
        screen = ""
        deadline = time.monotonic() + timeout
        time.sleep(wait)
        while time.monotonic() < deadline:
            screen = lab.capture(history=history)
            if re.search(expect, screen):
                break
            time.sleep(0.3)
        shots[name] = screen
        checks[name] = bool(re.search(expect, screen)) and not BAD.search(screen)
        return screen

    with tempfile.TemporaryDirectory(prefix="echocmd-") as tmp_name, FakeModel(REPLY, delay=0.06) as model:
        tmp = Path(tmp_name)
        proc, dirs, client = lab.start_backend(tmp, model.url)
        env = {"ECHOSPEAK_RUNTIME_DIR": str(dirs["rt"]), "ECHOSPEAK_STATE_DIR": str(dirs["state"] / "cmd"),
               "TERM": "tmux-256color", "LANG": "C.UTF-8", "ECHO_REDUCED_MOTION": "1", "HOME": str(dirs["home"])}
        launcher = lab.ROOT / "apps" / "terminal" / "echospeak"
        py = lab.ROOT / "apps" / "terminal" / ".venv" / "bin" / "python"
        cmd = " ".join(f"{k}={v}" for k, v in env.items()) + f" {py} {launcher} new"
        project = dirs["cwd"] / "shop"
        project.mkdir()
        lab.tmux("kill-server", check=False)
        lab.tmux("new-session", "-d", "-s", "lab", "-x", "120", "-y", "34", "-c", str(project), f"env {cmd}")
        for option in (("window-size", "manual"), ("history-limit", "5000"), ("status", "off")):
            lab.tmux("set", "-g", *option)
        try:
            try:
                lab.wait_for("Work in this folder?", timeout=20)
                time.sleep(0.4)
                lab.keys("n")
            except TimeoutError:
                pass
            lab.wait_for("sessions", timeout=30)
            time.sleep(0.5)
            step("? shortcuts", ["?"], r"for your sessions")
            step("esc closes shortcuts", ["Escape"], r"← sessions")
            step("/help", ["/help", "Enter"], r"/sessions", history=True)
            step("/status", ["/status", "Enter"], r"(?i)backend")
            step("a message gets an answer", ["explain the week 53 bug", "Enter"], r"keeps every caller unchanged",
                 timeout=40, history=True)
            step("/usage", ["/usage", "Enter"], r"(?i)token")
            step("/model opens the model picker", ["/model", "Enter"], r"(?i)model")
            step("esc closes the picker", ["Escape"], r"← sessions")
            step("/agents opens the roster", ["/agents", "Enter"], r"Jarvis")
            step("esc closes the roster", ["Escape"], r"← sessions")
            step("/agent Jarvis", ["/agent Jarvis", "Enter"], r"direct session with Jarvis")
            step("/agent - returns to Echo", ["/agent -", "Enter"], r"talking to Echo")
            step("/permissions shows switches", ["/permissions", "Enter"], r"(?i)files|terminal|permission")
            step("esc closes permissions", ["Escape"], r"← sessions")
            step("/mode opens the mode picker on unified", ["/mode", "Enter"], r"Assistant mode.*\n.*unified.*\(current\)")
            step("esc closes the mode picker", ["Escape"], r"← sessions")
            step("/memory", ["/memory", "Enter"], r"(?i)memor|nothing|remember")
            step("/approvals", ["/approvals", "Enter"], r"(?i)approval|nothing|waiting")
            step("/project asks to trust the folder", ["/project", "Enter"], r"Trust this folder\?")
            step("y attaches it", ["y"], r"shop")
            step("/project - detaches", ["/project -", "Enter"], r"detached")
            step("/sessions opens the manager", ["/sessions", "Enter"], r"Sessions ·")
            step("esc returns", ["Escape"], r"← sessions")
            step("/verbose toggles", ["/verbose", "Enter"], r"(?i)verbose|full tool output")
            step("/verbose again", ["/verbose", "Enter"], r"(?i)verbose|full tool output")
            step("shift+tab cycles approvals", ["BTab"], r"(?i)autonomous|auto")
            step("shift+tab again", ["BTab"], r"(?i)ask (before every action|always)")
            step("shift+tab back to smart", ["BTab"], r"(?i)ask (for risky actions|if risky)")
            step("esc stops a running task", ["write it again in more detail", "Enter"], r"(?i)thinking|working|connecting|starting|esc",
                 wait=1.2, timeout=8)
            step("…and it says Stopped", ["Escape"], r"(?i)stopped", timeout=20, history=True)
            step("/new", ["/new", "Enter"], r"New session")
            step("/clear", ["/clear", "Enter"], r"EchoSpeak")

            # The backend restarts on another port while the terminal stays open.
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=30)
            for name in ("endpoint.json",):
                (dirs["rt"] / name).unlink(missing_ok=True)
            proc, dirs, client = lab.start_backend(tmp, model.url)
            screen = step("after a backend restart the next message is answered",
                          ["hello again", "Enter"], r"keeps every caller unchanged|reconnected", timeout=40, history=True)
            if "reconnected" in screen and "keeps every caller unchanged" not in screen:
                # The first send hit the old address; the terminal reconnected and asks to send it again.
                step("…sent again after reconnecting", ["hello again", "Enter"], r"keeps every caller unchanged",
                     timeout=40, history=True)
            checks["the restart was noticed and reconnected"] = "reconnected" in lab.capture(history=True) or \
                checks.get("after a backend restart the next message is answered", False)
            lab.keys("C-c")
            time.sleep(0.3)
            lab.keys("C-c")
            time.sleep(0.6)
        finally:
            lab.tmux("kill-server", check=False)
            for name, text in shots.items():
                (out / (re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") + ".txt")).write_text(text)
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=30)
            except Exception:
                proc.kill()
    (out / "checks.json").write_text(json.dumps(checks, indent=2))
    for name, ok in checks.items():
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"{sum(checks.values())} of {len(checks)} passed · captures in {out}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
