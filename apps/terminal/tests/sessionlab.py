"""Session lab: the session manager in the real TUI, against the real backend and a scripted model.

Creates conversations through the API (as the desktop app does): two with Echo, one in a project, one
direct conversation with Jarvis. Then, in a private tmux, it opens the manager with ← on an empty
input, searches, filters, resumes a conversation, sends a message there, and checks through the
backend that the message landed in the conversation that was chosen, not the one the terminal
started in. Every screen is captured, scrollback included.

    apps/terminal/.venv/bin/python apps/terminal/tests/sessionlab.py [--out DIR]

Isolated data, runtime and state folders; it never touches a running EchoSpeak or your tmux.
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
from fake_model import FakeModel  # noqa: E402

from echospeak_cli.client import new_request_id, submit  # noqa: E402

REPLY = "Noted. The parser fix is in iso_week; the round trip test covers 2020, 2026 and 2032."


def say(client, thread_id: str, text: str, agent_id: str = "") -> None:
    for event in submit(client, thread_id, text, new_request_id(), agent_id=agent_id):
        if event.get("type") in {"final", "error"}:
            return


def history(client, thread_id: str) -> list[str]:
    return [h for h in (client.get("/history", thread_id=thread_id) or {}).get("history") or [] if isinstance(h, str)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(os.environ.get("CLAUDE_JOB_DIR", tempfile.gettempdir())) / "sessionlab"))
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    shots: dict[str, str] = {}
    checks: dict[str, bool] = {}
    with tempfile.TemporaryDirectory(prefix="echosess-") as tmp, FakeModel(REPLY, delay=0.01) as model:
        proc, dirs, client = lab.start_backend(Path(tmp), model.url)
        try:
            # Conversations made the way the desktop makes them.
            week = client.create_thread("Week 53 parser")["thread_id"]
            say(client, week, "why does 2026-W53 come back as 2027-W01?")
            notes = client.create_thread("Release notes for 11.14")["thread_id"]
            say(client, notes, "draft the release notes")
            folder = dirs["cwd"] / "work"
            folder.mkdir()
            project = client.post("/projects/attach-folder", {"path": str(folder), "trust_state": "trusted",
                                                              "session_id": week})
            agents = {a["name"].lower(): a["id"] for a in (client.get("/lean/agents") or {}).get("items") or []}
            room = client.post("/lean/rooms", {"name": "Jarvis", "agent_ids": [agents["jarvis"]], "kind": "direct"})
            say(client, room["thread_id"], "who is on the team?", agent_id=agents["jarvis"])
            overview = (client.get("/sessions/overview") or {}).get("items") or []
            (out / "overview.json").write_text(json.dumps(overview, indent=2))
            by_id = {row["thread_id"]: row for row in overview}
            checks["overview lists all three"] = {week, notes, room["thread_id"]} <= set(by_id)
            checks["overview: Jarvis's conversation is his"] = by_id.get(room["thread_id"], {}).get("agents", [{}])[0].get("name") == "Jarvis"
            checks["overview: project shown"] = (by_id.get(week, {}).get("project") or {}).get("name") == project.get("name", "work")

            env = {"ECHOSPEAK_RUNTIME_DIR": str(dirs["rt"]), "ECHOSPEAK_STATE_DIR": str(dirs["state"] / "lab"),
                   "TERM": "tmux-256color", "LANG": "C.UTF-8", "ECHO_REDUCED_MOTION": "1", "HOME": str(dirs["home"])}
            launcher = lab.ROOT / "apps" / "terminal" / "echospeak"
            py = lab.ROOT / "apps" / "terminal" / ".venv" / "bin" / "python"
            cmd = " ".join(f"{k}={v}" for k, v in env.items()) + f" {py} {launcher} new"
            lab.tmux("kill-server", check=False)
            lab.tmux("new-session", "-d", "-s", "lab", "-x", "110", "-y", "30", "-c", str(dirs["cwd"]), f"env {cmd}")
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
                shots["1-idle"] = lab.capture(history=False)
                checks["footer says ← sessions"] = "← sessions" in shots["1-idle"]

                # Left with text in the input moves the cursor; it doesn't open anything.
                lab.keys("hello", "Left", "Left")
                time.sleep(0.4)
                checks["left while typing is a cursor key"] = "Sessions ·" not in lab.capture(history=False)
                lab.keys("End", "C-u")      # C-u clears to the start of the line from the cursor
                time.sleep(0.3)

                lab.keys("Left")
                lab.wait_for("Sessions ·", timeout=10)
                time.sleep(0.8)
                shots["2-manager"] = lab.capture(history=False)
                screen = shots["2-manager"]
                checks["manager lists the conversations"] = all(t in screen for t in ("Week 53 parser", "Release notes", "Jarvis"))
                checks["manager shows the project"] = "work" in screen.lower()

                lab.keys("j", "a", "r")
                time.sleep(0.5)
                shots["3-search"] = lab.capture(history=False)
                checks["search narrows the list"] = "Jarvis" in shots["3-search"] and "Release notes" not in shots["3-search"]
                lab.keys("Escape")
                time.sleep(0.4)
                shots["3b-search-cleared"] = lab.capture(history=False)
                lab.keys("Tab")
                time.sleep(0.2)
                lab.keys("Tab")
                time.sleep(0.4)
                shots["4-filter-agents"] = lab.capture(history=False)
                checks["agents filter"] = "Jarvis" in shots["4-filter-agents"] and "Week 53 parser" not in shots["4-filter-agents"]
                lab.keys("BTab")
                time.sleep(0.2)
                lab.keys("BTab")
                time.sleep(0.4)
                shots["4b-filter-all"] = lab.capture(history=False)
                lab.keys("w", "e", "e", "k")
                time.sleep(0.5)
                shots["4c-search-week"] = lab.capture(history=False)
                lab.keys("Enter")
                lab.wait_for("Resumed Week 53 parser", timeout=10)
                time.sleep(0.8)
                shots["5-resumed"] = lab.capture(history=False)
                checks["resume shows the conversation"] = "2026-W53" in shots["5-resumed"] and "Resumed Week 53 parser" in shots["5-resumed"]

                lab.keys("does the fix cover 2032?", "Enter")
                # Done when the backend has the message and a reply after it (the screen's wording varies).
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    said = history(client, week)
                    asked = [i for i, h in enumerate(said) if "does the fix cover 2032?" in h]
                    if asked and any(h.startswith("Assistant: ") for h in said[asked[-1] + 1:]):
                        break
                    time.sleep(0.3)
                time.sleep(1.0)
                shots["6-after-message"] = lab.capture(history=False)
                said = history(client, week)
                checks["the message went to the chosen conversation"] = any("does the fix cover 2032?" in h for h in said)
                checks["and to no other"] = not any("does the fix cover 2032?" in h for h in history(client, notes))

                lab.keys("Left")
                lab.wait_for("Sessions ·", timeout=10)
                time.sleep(0.6)
                lab.keys("Right")
                time.sleep(0.6)
                shots["7-back"] = lab.capture(history=False)
                checks["right returns to the conversation"] = "Sessions ·" not in shots["7-back"] and "Week 53 parser" in shots["7-back"]

                lab.resize(40, 16)
                time.sleep(0.6)
                lab.keys("Left")
                lab.wait_for("Sessions|/ 4|/ 3", timeout=10)
                time.sleep(0.6)
                shots["8-manager-40x16"] = lab.capture(history=False)
                checks["fits 40 columns"] = all(len(line) <= 40 for line in shots["8-manager-40x16"].splitlines())
                lab.keys("Escape")
                time.sleep(0.3)
                lab.keys("C-c")
                time.sleep(0.3)
                lab.keys("C-c")
                time.sleep(0.6)
            finally:
                lab.tmux("kill-server", check=False)
                for name, text in shots.items():
                    (out / f"{name}.txt").write_text(text)
        finally:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=30)
            except Exception:
                proc.kill()
    for name, text in shots.items():
        (out / f"{name}.txt").write_text(text)
    (out / "checks.json").write_text(json.dumps(checks, indent=2))
    for name, ok in checks.items():
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"captures in {out}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
