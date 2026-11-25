"""Project lab: does a conversation started in the terminal keep the folder's project? (unified mode: no project = Chat)

Runs the real TUI in a trusted project folder against an isolated backend and walks the owner's sequences:
a first message, /clear then a message, /new then a message, and switching to a session without a project
through ← then starting a new one. After each step it reads the newest session's project from the backend.

    apps/terminal/.venv/bin/python apps/terminal/tests/projectlab.py [--tui-root DIR]
"""
from __future__ import annotations

import argparse
import json
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


def newest(client) -> dict:
    rows = client.get("/threads") or {}
    rows = rows.get("threads") or rows.get("items") or rows if isinstance(rows, dict) else rows
    rows = sorted(rows, key=lambda r: str(r.get("created_at") or ""))
    return rows[-1] if rows else {}


def project_of(client, thread_id: str) -> str:
    for path in (f"/threads/{thread_id}", f"/sessions/{thread_id}"):
        try:
            body = client.get(path) or {}
        except Exception:  # noqa: BLE001
            continue
        for key in ("project_id", "active_project_id"):
            if body.get(key):
                return str(body[key])
        state = body.get("state") or body.get("thread_state") or {}
        if isinstance(state, dict) and state.get("active_project_id"):
            return str(state["active_project_id"])
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tui-root", default="")
    args = ap.parse_args()
    L = uilab.Lab(Path(tempfile.mkdtemp(prefix="projlab-")), "owner")
    if args.tui_root:
        L.tui_root = Path(args.tui_root).resolve()
    search = uilab.FakeSearx()
    result = {}
    with tempfile.TemporaryDirectory(prefix="echoproj-") as tmp, ScriptedModel(uilab.SCRIPTS, uilab.DEFAULT, delay=0.01) as model:
        proc, dirs, client = lab.start_backend(Path(tmp), model.url, {"WEB_SEARCH_PROVIDER": "searxng",
                                                                       "SEARXNG_BASE_URL": search.url,
                                                                       "PRIVACY_MODE": "private",
                                                                       "HOME": str(Path(tmp) / "home")})
        try:
            project = uilab.seed(client, dirs)
            # A conversation with no project, like a desktop chat, to switch to.
            loose = client.create_thread("Desktop chat about hockey")["thread_id"]
            uilab.say(client, loose, "when do the oilers play")
            env = {"ECHOSPEAK_RUNTIME_DIR": str(dirs["rt"]), "ECHOSPEAK_STATE_DIR": str(dirs["state"] / "ui"),
                   "TERM": "tmux-256color", "COLORTERM": "truecolor", "LANG": "C.UTF-8", "HOME": str(dirs["home"])}
            L.launch(project, env)
            L.wait("for shortcuts|sessions", 30)
            time.sleep(2.0)
            expected = client.get("/projects") or {}

            def step(name: str, keys: list) -> None:
                for k in keys:
                    if k.startswith("!"):
                        lab.keys(k[1:])
                        time.sleep(1.0)
                    else:
                        L.send(k)
                        L.idle()
                        time.sleep(0.6)
                row = newest(client)
                result[name] = {"title": row.get("title"), "project": project_of(client, str(row.get("thread_id") or row.get("id")))}
                print(name, result[name], flush=True)

            step("first", ["hello there"])
            step("after_clear", ["/clear", "hello after clear"])
            step("after_new", ["/new", "hello after new"])
            step("after_switch_then_new", ["!Left", "hockey", "!Enter", "/new", "hello after switching"])
            L.stop()
            result["projects"] = expected
        finally:
            lab.tmux("kill-server", check=False)
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=30)
            except Exception:  # noqa: BLE001
                proc.kill()
            search.close()
    print(json.dumps({k: v for k, v in result.items() if k != "projects"}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
