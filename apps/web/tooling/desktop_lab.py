"""Desktop lab: the real web app in Chromium against the real backend and a scripted model.

Checks the unified desktop experience end to end (docs/research/agent-first.md):

- one conversation list (no Chat / Agents / Code switch), each row with who answers, project and state;
- the terminal's filters (All / Echo / Agents / This project);
- the header: agent · model · project · approval mode, for Echo and for a named agent;
- a message sent from the desktop lands in the conversation that is open (read back from the backend);
- a conversation started elsewhere (the terminal's API path) appears in the desktop list;
- attaching a folder asks about trust, and the answer is what the backend stores.

Isolated data folder, auth off, ports chosen at random (the app on 5176, an allowed dev origin).
Needs: apps/backend/.venv, apps/web/node_modules, a Python with playwright, and /usr/bin/chromium.

    <python with playwright> apps/web/tooling/desktop_lab.py [--out DIR]
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "apps" / "terminal" / "tests"))
from fake_model import MODEL, FakeModel  # noqa: E402

APP_PORT = 5176
REPLY = "Noted. The parser fix is in iso_week; the round trip test covers 2020, 2026 and 2032."


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Api:
    def __init__(self, base: str) -> None:
        self.base = base

    def call(self, method: str, path: str, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as response:
            raw = response.read()
        return json.loads(raw) if raw else None

    def say(self, thread_id: str, text: str, agent_id: str = "") -> None:
        body = {"message": text, "thread_id": thread_id, "client_request_id": f"lab-{time.time_ns()}"}
        if agent_id:
            body["agent_id"] = agent_id
        req = urllib.request.Request(self.base + "/query/stream", data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as response:
            for line in response:
                if b'"type": "final"' in line or b'"type":"final"' in line:
                    return

    def history(self, thread_id: str) -> list[str]:
        return [h for h in (self.call("GET", f"/history?thread_id={thread_id}") or {}).get("history") or [] if isinstance(h, str)]


def start(tmp: Path, model_url: str) -> tuple[list[subprocess.Popen], Api]:
    dirs = {n: tmp / n for n in ("data", "logs", "home", "rt", "cwd")}
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    port = free_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ECHOSPEAK_", "API_", "XDG_RUNTIME", "TMUX"))}
    env.update({
        "ECHOSPEAK_DATA_DIR": str(dirs["data"]), "ECHOSPEAK_LOGS_DIR": str(dirs["logs"]), "ECHOSPEAK_HOME": str(dirs["home"]),
        "SOUL_PATH": str(dirs["data"] / "SOUL.md"), "ECHOSPEAK_RUNTIME_DIR": str(dirs["rt"]),
        "API_AUTH_ENABLED": "false", "USE_LOCAL_MODELS": "true", "LOCAL_MODEL_PROVIDER": "vllm",
        "LOCAL_MODEL_URL": model_url, "LOCAL_MODEL_NAME": MODEL, "ECHOSPEAK_DISABLE_WARMUP": "1",
        "LEARNING_ENABLED": "0", "CHECKIN_MODE": "off", "LEAN_QUARANTINE_READER": "off", "PYTHONUNBUFFERED": "1",
        "ASSISTANT_MODE": "unified",
    })
    backend = subprocess.Popen([str(ROOT / "apps/backend/.venv/bin/python"), "app.py", "--mode", "api", "--host", "127.0.0.1",
                                "--port", str(port)], cwd=str(ROOT / "apps/backend"), env=env,
                               stdout=open(tmp / "backend.log", "wb"), stderr=subprocess.STDOUT)
    api = Api(f"http://127.0.0.1:{port}")
    deadline = time.monotonic() + 120
    while True:
        try:
            api.call("GET", "/health")
            break
        except Exception:
            if backend.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError((tmp / "backend.log").read_text()[-3000:])
            time.sleep(0.5)
    web_env = {**os.environ, "VITE_API_BASE_URL": api.base}
    web = subprocess.Popen(["npx", "vite", "--port", str(APP_PORT), "--strictPort", "--host", "127.0.0.1"],
                           cwd=str(ROOT / "apps/web"), env=web_env, stdout=open(tmp / "vite.log", "wb"),
                           stderr=subprocess.STDOUT, start_new_session=True)
    deadline = time.monotonic() + 90
    while True:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{APP_PORT}/", timeout=3)
            break
        except Exception:
            if web.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError((tmp / "vite.log").read_text()[-3000:])
            time.sleep(0.5)
    return [backend, web], api


def main() -> int:
    from playwright.sync_api import sync_playwright

    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(os.environ.get("CLAUDE_JOB_DIR", tempfile.gettempdir())) / "desktoplab"))
    out = Path(ap.parse_args().out)
    out.mkdir(parents=True, exist_ok=True)
    checks: dict[str, bool] = {}
    notes: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="echodesk-") as tmp_name, FakeModel(REPLY, delay=0.01) as model:
        tmp = Path(tmp_name)
        procs, api = start(tmp, model.url)
        try:
            week = api.call("POST", "/threads", {"title": "Week 53 parser", "source": "web"})["thread_id"]
            api.say(week, "why does 2026-W53 come back as 2027-W01?")
            folder = tmp / "cwd" / "work"
            folder.mkdir()
            api.call("POST", "/projects/attach-folder", {"path": str(folder), "trust_state": "trusted", "session_id": week})
            notes_thread = api.call("POST", "/threads", {"title": "Release notes for 11.14", "source": "web"})["thread_id"]
            api.say(notes_thread, "draft the release notes")
            agents = {a["name"].lower(): a["id"] for a in api.call("GET", "/lean/agents").get("items") or []}
            room = api.call("POST", "/lean/rooms", {"name": "Jarvis", "agent_ids": [agents["jarvis"]], "kind": "direct"})
            api.say(room["thread_id"], "who is on the team?", agent_id=agents["jarvis"])

            with sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path="/usr/bin/chromium", args=["--no-sandbox"])
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                errors: list[str] = []
                page.on("pageerror", lambda exc: errors.append(str(exc)))
                page.goto(f"http://127.0.0.1:{APP_PORT}/app", wait_until="domcontentloaded")
                try:
                    page.wait_for_selector(".es-scope-chips", timeout=60000)
                except Exception:
                    page.screenshot(path=str(out / "0-failed-to-load.png"))
                    (out / "0-failed-to-load.txt").write_text(page.inner_text("body")[:4000] + "\n\nERRORS:\n" + "\n".join(errors))
                    raise
                page.wait_for_timeout(1500)
                page.screenshot(path=str(out / "1-unified-sidebar.png"))
                sidebar = page.locator("aside, nav, .es-sidebar-refined").first.inner_text()
                body = page.inner_text("body")
                checks["no Chat / Agents / Code switch"] = page.locator('[role="tablist"][aria-label="Environment"]').count() == 0
                checks["one list: Echo, project and Jarvis conversations"] = all(t in body for t in ("Week 53 parser", "Release notes for 11.14", "Jarvis"))
                checks["rows say who answers and the project"] = "Echo · work" in body
                checks["filters shown"] = all(page.locator(".es-scope-chips button", has_text=t).count() == 1 for t in ("All", "Echo", "Agents"))
                checks["Workbench entry"] = page.locator(".es-nav-item", has_text="Workbench").count() == 1

                page.locator(".es-scope-chips button", has_text="Agents").click()
                page.wait_for_timeout(400)
                listed = page.locator("#es-sec-chats").inner_text()
                checks["Agents filter shows only agent conversations"] = "Jarvis" in listed and "Week 53 parser" not in listed
                page.screenshot(path=str(out / "2-agents-filter.png"))
                page.locator(".es-scope-chips button", has_text="All").click()
                page.wait_for_timeout(400)

                page.locator("#es-sec-chats button.echo-side-button", has_text="Week 53 parser").first.click()
                page.wait_for_selector(".es-identity", timeout=20000)
                page.wait_for_timeout(1200)
                identity = page.locator(".es-identity").inner_text()
                notes["echo header"] = identity
                checks["header: Echo, model, project, approvals"] = all(t in identity for t in ("Echo", "work", "Ask for risky actions"))
                checks["header names the model"] = MODEL.split("/")[-1][:10] in identity or "fake" in identity
                page.screenshot(path=str(out / "3-echo-conversation.png"))

                composer = page.locator("textarea").first
                composer.fill("does the fix cover 2032?")
                composer.press("Enter")
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline and not any("does the fix cover 2032?" in h for h in api.history(week)):
                    time.sleep(0.5)
                time.sleep(2)
                checks["a desktop message lands in the open conversation"] = any("does the fix cover 2032?" in h for h in api.history(week))
                checks["and in no other"] = not any("does the fix cover 2032?" in h for h in api.history(notes_thread))

                page.locator("#es-sec-chats button.echo-side-button", has_text="Jarvis").first.click()
                page.wait_for_timeout(1500)
                identity = page.locator(".es-identity").inner_text() if page.locator(".es-identity").count() else ""
                notes["jarvis header"] = identity
                checks["header follows the agent: Jarvis"] = identity.startswith("Jarvis")
                page.screenshot(path=str(out / "4-jarvis-conversation.png"))

                # A conversation the terminal starts (the same API) shows up in the desktop list.
                tui = api.call("POST", "/threads", {"title": "Started in the terminal", "source": "web"})["thread_id"]
                api.say(tui, "hello from the terminal")
                page.wait_for_function("() => document.body.innerText.includes('Started in the terminal')", timeout=40000)
                checks["a terminal conversation appears in the desktop list"] = True

                # Attaching a folder asks about trust; Cancel attaches it read-only.
                page.locator("#es-sec-chats button.echo-side-button", has_text="Release notes").first.click()
                page.wait_for_timeout(1000)
                second = tmp / "cwd" / "notes-folder"
                second.mkdir()
                asked: list[str] = []
                page.once("dialog", lambda dialog: (asked.append(dialog.message), dialog.dismiss()))
                # The native folder picker can't open in a headless browser, so only it is stubbed. The click, the
                # trust question and the attach request are the app's own.
                page.route("**/projects/pick-folder", lambda route: route.fulfill(
                    status=200, content_type="application/json", body=json.dumps({"path": str(second)})))
                button = page.locator('.session-folder-strip button[title*="Choose or drop a local folder"]').first
                dropped = button.count() > 0
                if dropped:
                    button.click()
                page.wait_for_timeout(2500)
                checks["attaching a folder asks about trust"] = bool(asked) and "Let Echo work in" in asked[0]
                projects = api.call("GET", "/projects") or []
                projects = projects.get("items", projects) if isinstance(projects, dict) else projects
                stored = next((p for p in projects if str(p.get("workspace_root", "")).rstrip("/") == str(second)), None)
                notes["drop"] = f"dispatched={dropped} dialog={asked[:1]} stored={stored and stored.get('trust_state')}"
                checks["Cancel stores it read-only (untrusted)"] = bool(stored) and stored.get("trust_state") != "trusted"

                page.set_viewport_size({"width": 390, "height": 844})
                page.wait_for_timeout(800)
                page.screenshot(path=str(out / "5-narrow.png"))
                checks["no page errors"] = not errors
                notes["page errors"] = " | ".join(errors[:3])
                browser.close()
        finally:
            for proc in procs:
                try:
                    os.killpg(proc.pid, signal.SIGTERM) if proc.args[0] == "npx" else proc.send_signal(signal.SIGTERM)
                except Exception:
                    pass
            for proc in procs:
                try:
                    proc.wait(timeout=20)
                except Exception:
                    proc.kill()
    (out / "checks.json").write_text(json.dumps({"checks": checks, "notes": notes}, indent=2))
    for name, ok in checks.items():
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    for name, text in notes.items():
        print(f"  note {name}: {text}")
    print(f"captures in {out}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
