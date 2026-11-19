"""UI lab: every major screen of the real TUI, driven by keyboard, captured as terminal screenshots.

The visual-evaluation loop (docs/research/cli-design.md §9): run the real TUI against the real
backend, capture each screen and state, judge the pictures, fix, capture again, compare.

- The backend is isolated (temporary data, runtime, state and home folders) and answers through
  a scripted model that makes real tool calls, so every tool line, approval, question, delegation
  and failure on screen comes from real backend events. Web search goes to a local fake SearXNG.
- The TUI runs as plain ``echospeak`` in a private tmux server, in a project folder that already
  has conversations, exactly as the owner starts it.
- Each shot is saved as ``.ansi`` (tmux ``capture-pane -e``), ``.txt`` and ``.png`` (ansishot.py,
  painted in the owner's terminal theme, or ``--palette standard``), plus ``index.json``.

    apps/terminal/.venv/bin/python apps/terminal/tests/uilab.py --out DIR [--only startup,sessions] [--no-png]

No model inference, no network, no paid API: it never touches a running EchoSpeak or your tmux.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import ansishot  # noqa: E402
import sizelab as lab  # noqa: E402
from fake_model import ScriptedModel  # noqa: E402

from echospeak_cli import control  # noqa: E402
from echospeak_cli.client import new_request_id, submit  # noqa: E402

DEFAULT = "Noted. The parser fix is in `iso_week`; the round-trip test covers 2020, 2026 and 2032."

ANSWER = """## Why 2026-W53 becomes 2027-W01

ISO years have **53 weeks** when 28 December falls in week 53. The parser assumed every year has 52,
so the last week rolls into the next year and the round trip breaks.

1. `_long_year(y)` already knows which years are long.
2. `parse_week` never asks it, so week 53 overflows.
3. The fix is one condition, plus a test for 2020, 2026 and 2032.

```python
def _long_year(y: int) -> bool:
    return date(y, 12, 28).isocalendar()[1] == 53
```

| Year | Weeks | Before | After |
|------|-------|--------|-------|
| 2020 | 53 | 2021-W01 | 2020-W53 |
| 2026 | 53 | 2027-W01 | 2026-W53 |
| 2027 | 52 | 2027-W52 | 2027-W52 |

Every caller keeps working; only week 53 changes."""

SCRIPTS = [
    ("explain the week 53 bug", [{"text": ANSWER}]),
    ("check the parser files", [
        {"tools": [{"name": "file_list", "args": {"path": "."}}], "text": "Let me look at the project first."},
        {"tools": [{"name": "file_read", "args": {"path": "parser.py"}}]},
        {"tools": [{"name": "file_read", "args": {"path": "tests/test_weeks.py"}}]},
        {"text": "`parser.py` defines `parse_week` but never calls `_long_year`, and there is no `tests/` folder "
                 "yet, so nothing covers week 53. I'd add `tests/test_weeks.py` with the three long years."},
    ]),
    ("research iso week libraries", [
        {"tools": [{"name": "web_search", "args": {"query": "python iso week date library", "read": 0}}],
         "text": "I'll compare what the standard library and two packages do."},
        {"tools": [{"name": "web_search", "args": {"query": "isoweek package week 53 handling", "read": 0}}]},
        {"text": "**Short answer:** the standard library is enough.\n\n"
                 "- `date.fromisocalendar` (Python 3.8+) handles week 53 correctly [1].\n"
                 "- `isoweek` wraps the same rules with a `Week` type, useful for ranges [2].\n"
                 "- `pendulum` adds time zones but nothing for ISO weeks [3].\n\n"
                 "Sources: [1] docs.python.org · [2] pypi.org/project/isoweek · [3] pendulum.eustace.io"},
    ]),
    ("remember that i prefer iso week numbers", [
        {"tools": [{"name": "memory_save", "args": {"fact": "Prefers ISO week numbers (2026-W53) over US week numbers.",
                                                    "type": "preference"}}]},
        {"text": "Saved. I'll write weeks as ISO week numbers from now on."},
    ]),
    ("ask me which year", [
        {"tools": [{"name": "ask_user", "args": {"question": "Which long year should the first test cover?",
                                                 "options": ["2020", "2026", "2032"], "allow_other": True}}]},
        {"text": "Good choice: 2026 is the year that failed, so the test starts there."},
    ]),
    ("run the tests in the terminal", [
        {"tools": [{"name": "terminal", "args": {"command": "rm -rf build && python -m pytest -q tests"}}],
         "text": "I'll clear the old build and run the tests."},
        {"text": "The run did not happen, so I can't say whether the tests pass."},
    ]),
    ("tell jarvis", [
        {"tools": [{"name": "message_agent", "args": {"agent": "Jarvis", "message": "Compare isoweek and pendulum for week 53."}}],
         "text": "I'll ask Jarvis."},
        {"text": "Jarvis has the question; his answer will arrive in this conversation."},
    ]),
    ("crash test", [{"http_error": 500}]),
    ("write a long essay", [{"text": " ".join(["ISO weeks start on Monday, and week one is the week with the year's first "
                                                "Thursday."] * 18), "delay": 0.06}]),
]

SEARCH = {
    "python iso week date library": [
        ("datetime — Basic date and time types", "https://docs.python.org/3/library/datetime.html",
         "date.fromisocalendar(year, week, day) returns a date from an ISO calendar date."),
        ("isoweek · PyPI", "https://pypi.org/project/isoweek/", "The Week class represents an ISO week."),
        ("Pendulum documentation", "https://pendulum.eustace.io/docs/", "Python datetimes made easy."),
    ],
}


class FakeSearx:
    """GET /search?q=…&format=json, the way SearXNG answers."""

    def __init__(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                q = (parse_qs(urlparse(self.path).query).get("q") or [""])[0]
                rows = SEARCH.get(q) or [(f"Results for {q}", "https://example.org/isoweek", "ISO week 53 explained.")]
                body = json.dumps({"query": q, "results": [{"title": t, "url": u, "content": c} for t, u, c in rows]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def say(client, thread_id: str, text: str, agent_id: str = "") -> None:
    for event in submit(client, thread_id, text, new_request_id(), agent_id=agent_id):
        if event.get("type") in {"final", "error"}:
            return


class Lab:
    def __init__(self, out: Path, palette: str) -> None:
        self.out = out
        self.palette = palette
        self.index: list[dict] = []
        self.size = (110, 32)
        self.prefix = ""
        self.tui_root = lab.ROOT     # whose TUI code runs (--tui-root: e.g. a snapshot of the baseline)

    # ── tmux ─────────────────────────────────────────────────────────
    def launch(self, cwd: Path, env: dict, args: str = "", size=(110, 32)) -> None:
        self.size = size
        launcher = self.tui_root / "apps" / "terminal" / "echospeak"
        py = lab.ROOT / "apps" / "terminal" / ".venv" / "bin" / "python"
        cmd = " ".join(f"{k}={v}" for k, v in env.items()) + f" {py} {launcher} {args}".rstrip()
        lab.tmux("kill-server", check=False)
        lab.tmux("new-session", "-d", "-s", "lab", "-x", str(size[0]), "-y", str(size[1]), "-c", str(cwd), f"env {cmd}")
        for option in (("window-size", "manual"), ("history-limit", "5000"), ("status", "off")):
            lab.tmux("set", "-g", *option)

    def stop(self) -> None:
        for _ in range(2):
            lab.keys("C-c")
            time.sleep(0.4)
        time.sleep(0.6)
        lab.tmux("kill-server", check=False)

    def resize(self, w: int, h: int) -> None:
        self.size = (w, h)
        lab.resize(w, h)

    def screen(self) -> str:
        return lab.capture(history=False)

    def wait(self, needle: str, timeout: float = 30) -> bool:
        try:
            lab.wait_for(needle, timeout)
            return True
        except TimeoutError:
            return False

    def gone(self, needle: str, timeout: float = 60) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if needle not in self.screen():
                return True
            time.sleep(0.2)
        return False

    def idle(self, timeout: float = 90) -> bool:
        """The run is over: no stop hint for a moment."""
        time.sleep(0.8)
        end = time.monotonic() + timeout
        calm = 0
        while time.monotonic() < end:
            s = self.screen()
            busy = "esc to stop" in s or "esc stop" in s or "(esc)" in s
            calm = 0 if busy else calm + 1
            if calm >= 3:
                return True
            time.sleep(0.25)
        return False

    def send(self, text: str) -> None:
        lab.tmux("send-keys", "-t", "lab", "-l", text)
        time.sleep(0.2)
        lab.keys("Enter")

    def shot(self, name: str, note: str = "", history: bool = False) -> None:
        name = self.prefix + name
        ansi = lab.tmux("capture-pane", "-e", "-p", "-t", "lab", *(["-S", "-"] if history else []))
        cur = lab.tmux("display", "-p", "-t", "lab", "#{cursor_x},#{cursor_y},#{cursor_flag}").strip().split(",")
        cursor = (int(cur[0]), int(cur[1])) if len(cur) == 3 and cur[2] == "1" and not history else None
        (self.out / f"{name}.ansi").write_text(ansi)
        (self.out / f"{name}.txt").write_text(lab.tmux("capture-pane", "-p", "-t", "lab",
                                                       *(["-S", "-"] if history else [])))
        rows = len(ansi.rstrip("\n").split("\n")) if history else self.size[1]
        self.index.append({"name": name, "note": note, "cols": self.size[0], "rows": rows,
                           "cursor": cursor, "history": history})
        print(f"  shot {name}  {note}", flush=True)

    def render(self, jobs: int = 4) -> None:
        def one(item):
            ansi = (self.out / f"{item['name']}.ansi").read_text()
            cur = tuple(item["cursor"]) if item["cursor"] else None
            ansishot.render_png(ansi, self.out / f"{item['name']}.png", item["cols"], item["rows"], cur, self.palette)

        with ThreadPoolExecutor(jobs) as pool:
            list(pool.map(one, self.index))


# ── scenes ───────────────────────────────────────────────────────────
def scene_startup(L: Lab, cwd: Path, env: dict) -> None:
    """Plain `echospeak` in a project folder with history: what the owner sees first, frame by frame."""
    L.launch(cwd, env)
    start = time.monotonic()
    last = None
    kept = 0
    while time.monotonic() - start < 3.5 and kept < 18:
        screen = lab.tmux("capture-pane", "-e", "-p", "-t", "lab")
        if screen != last and screen.strip():
            last = screen
            kept += 1
            L.shot(f"01-startup-{int((time.monotonic() - start) * 1000):04d}ms", "a distinct startup frame")
        time.sleep(0.08)
    L.wait("for shortcuts|sessions", 30)
    time.sleep(2.0)
    L.shot("01-startup", "settled startup screen")


def scene_composer(L: Lab) -> None:
    long = ("Can you look at why the parser rolls 2026-W53 into 2027-W01, check every caller of parse_week, "
            "and suggest the smallest change that keeps the public API exactly the same for existing users?")
    lab.tmux("send-keys", "-t", "lab", "-l", "fix it")
    time.sleep(0.4)
    L.shot("02-composer-short", "short input: the gap after the prompt")
    lab.keys("C-u")
    lab.tmux("send-keys", "-t", "lab", "-l", long)
    time.sleep(0.5)
    L.shot("02-composer-long", "long input wraps")
    lab.keys("C-j")
    lab.tmux("send-keys", "-t", "lab", "-l", "Also: café, naïve, 日本語の週番号, emoji 🎉 width check")
    time.sleep(0.5)
    L.shot("02-composer-multiline", "multiline, accents, CJK, emoji")
    lab.keys("Left", "Left", "Left", "Left", "Left")
    time.sleep(0.3)
    L.shot("02-composer-cursor", "cursor moved left inside text (left stays a cursor key)")
    lab.keys("Escape")
    time.sleep(0.15)
    lab.keys("Escape")
    time.sleep(0.6)
    L.shot("02-composer-cleared", "esc esc clears the draft")


def scene_menus(L: Lab) -> None:
    lab.keys("?")
    time.sleep(0.5)
    L.shot("03-shortcuts", "? shortcuts")
    lab.keys("?")
    time.sleep(0.3)
    lab.keys("/")
    time.sleep(0.5)
    L.shot("04-slash-menu", "slash menu")
    lab.keys("Escape")
    time.sleep(0.2)
    lab.keys("BSpace")
    time.sleep(0.4)


def scene_sessions(L: Lab) -> None:
    lab.keys("Left")
    time.sleep(1.2)
    L.shot("05-left-once", "first ← on an empty input")
    lab.keys("Left")
    time.sleep(1.2)
    L.shot("06-left-twice", "second ←")
    lab.keys("Down")
    time.sleep(0.4)
    L.shot("06b-left-twice-down", "second ← then ↓")
    lab.tmux("send-keys", "-t", "lab", "-l", "jar")
    time.sleep(0.6)
    L.shot("07-sessions-search", "type to search: jar")
    lab.keys("Escape")
    time.sleep(0.4)
    L.shot("07b-search-cleared", "esc clears the search")
    lab.keys("Escape")
    time.sleep(0.5)
    L.shot("07c-esc-back", "esc goes back one level")
    for _ in range(3):
        if "for shortcuts" in L.screen() or "? help" in L.screen():
            break
        lab.keys("Escape")
        time.sleep(0.5)
    L.shot("07d-back-to-composer", "back at the composer, conversation untouched")
    # The full page, then Enter on another conversation: it resumes from the page itself.
    lab.keys("Left")
    time.sleep(1.0)
    lab.keys("Left")
    time.sleep(1.2)
    lab.tmux("send-keys", "-t", "lab", "-l", "release")
    time.sleep(0.6)
    L.shot("07e-page-search-release", "the full page, searching")
    lab.keys("Enter")
    time.sleep(2.5)
    L.shot("07f-resumed-from-page", "Enter on the page resumed that conversation")


def scene_resume(L: Lab) -> None:
    lab.keys("Left")
    time.sleep(1.0)
    lab.tmux("send-keys", "-t", "lab", "-l", "week 53")
    time.sleep(0.5)
    lab.keys("Enter")
    time.sleep(2.5)
    L.shot("08-resumed", "resumed the project conversation from the picker")


def scene_conversation(L: Lab) -> None:
    L.send("explain the week 53 bug")
    time.sleep(1.4)
    L.shot("09-streaming", "answer streaming")
    L.idle()
    time.sleep(0.6)
    L.shot("10-answer", "finished markdown answer: list, code, table")
    L.send("check the parser files")
    time.sleep(1.0)
    L.shot("11a-tools-running", "tools running")
    L.idle()
    time.sleep(0.5)
    L.shot("11-tools", "tool calls: ok and failed")
    L.send("research iso week libraries")
    L.idle()
    time.sleep(0.5)
    L.shot("12-research", "search, findings, sources")
    L.send("remember that I prefer ISO week numbers")
    L.idle()
    time.sleep(0.5)
    L.shot("13-memory", "memory save")


def switch_to(L: Lab, query: str) -> None:
    """Open the picker with ← and resume the conversation the query finds."""
    lab.keys("Left")
    time.sleep(1.0)
    lab.tmux("send-keys", "-t", "lab", "-l", query)
    time.sleep(0.5)
    lab.keys("Enter")
    time.sleep(2.5)


def scene_attention(L: Lab, client) -> None:
    L.send("ask me which year to test first")
    if L.wait("Which long year", 30):
        time.sleep(0.6)
        L.shot("14-question", "a question from the agent")
        lab.keys("Down")
        time.sleep(0.2)
        lab.keys("Down")
        time.sleep(0.2)
        lab.keys("Enter")
    L.idle()
    time.sleep(0.5)
    L.shot("14b-answered", "after answering")
    switch_to(L, "test plan")
    L.shot("15a-glados", "switched to the GLaDOS conversation")
    control.put_setting(client, "lean_approval_mode", "always")
    time.sleep(0.5)
    L.send("run the tests in the terminal")
    if L.wait("needs your OK", 30):
        time.sleep(0.6)
        L.shot("15-approval", "approval request")
        lab.keys("n")
    L.idle()
    time.sleep(0.5)
    L.shot("15b-denied", "after denying")
    control.put_setting(client, "lean_approval_mode", "smart")
    L.send("tell jarvis to compare isoweek and pendulum")
    L.idle()
    time.sleep(0.5)
    L.shot("16-delegation", "a message to another agent")
    L.send("crash test")
    L.idle()
    time.sleep(0.5)
    L.shot("17-failure", "the model call fails")
    L.send("write a long essay about iso weeks")
    time.sleep(2.2)
    L.shot("18a-long-stream", "long answer streaming")
    lab.keys("Escape")
    L.idle()
    time.sleep(0.5)
    L.shot("18-stopped", "stopped with esc")


def scene_sizes(L: Lab) -> None:
    for w, h in ((80, 24), (60, 20), (40, 16), (160, 44)):
        L.resize(w, h)
        time.sleep(1.0)
        L.shot(f"19-size-{w}x{h}", f"conversation at {w}×{h}")
    L.resize(60, 20)
    time.sleep(0.6)
    lab.keys("Left")
    time.sleep(1.0)
    L.shot("20-sessions-60x20", "session picker at 60×20")
    lab.keys("Left")
    time.sleep(1.0)
    L.shot("20b-sessions-page-60x20", "second ← at 60×20")
    for _ in range(3):
        lab.keys("Escape")
        time.sleep(0.4)
    L.resize(110, 32)
    time.sleep(0.8)
    L.shot("21-back-110x32", "back at 110×32 after resizes", history=True)


def scene_fresh(L: Lab, root: Path, env: dict) -> None:
    """`echospeak` in a folder that is not a project yet: the folder question, then a clean start."""
    folder = root / "notes"
    folder.mkdir(exist_ok=True)
    L.launch(folder, env)
    if L.wait("Work in this folder?", 25):
        time.sleep(0.8)
        L.shot("22-folder-question", "first start in a new folder")
        lab.keys("n")
    L.wait("for shortcuts|sessions", 20)
    time.sleep(1.5)
    L.shot("23-fresh-start", "fresh folder after the question")
    L.stop()


def seed(client, dirs) -> Path:
    project = dirs["cwd"] / "parser"
    (project / "src").mkdir(parents=True)
    (project / "parser.py").write_text("from datetime import date\n\n\ndef _long_year(y):\n    return date(y, 12, 28)"
                                       ".isocalendar()[1] == 53\n\n\ndef parse_week(text):\n    year, week = text.split('-W')\n"
                                       "    return int(year), int(week)\n")
    (project / "README.md").write_text("# parser\n\nISO week parsing.\n")
    week = client.create_thread("Week 53 parser")["thread_id"]
    client.post("/projects/attach-folder", {"path": str(project), "trust_state": "trusted", "session_id": week})
    for text in ("why does 2026-W53 come back as 2027-W01?", "which callers use parse_week?",
                 "ok, and does the fix cover 2032?", "write the test for 2020 too"):
        say(client, week, text)
    notes = client.create_thread("Release notes for 11.14")["thread_id"]
    say(client, notes, "draft the release notes")
    say(client, notes, "shorter, and mention the session manager")
    agents = {a["name"].lower(): a["id"] for a in (client.get("/lean/agents") or {}).get("items") or []}
    room = client.post("/lean/rooms", {"name": "Competitor pricing", "agent_ids": [agents["jarvis"]], "kind": "direct"})
    say(client, room["thread_id"], "what do the three competitors charge?", agent_id=agents["jarvis"])
    if "glados" in agents:
        room2 = client.post("/lean/rooms", {"name": "Test plan review", "agent_ids": [agents["glados"]], "kind": "direct"})
        say(client, room2["thread_id"], "review the test plan", agent_id=agents["glados"])
    # The project chat is the most recent one, so plain `echospeak` in the folder finds it.
    say(client, week, "thanks, ship it")
    return project


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(os.environ.get("CLAUDE_JOB_DIR", tempfile.gettempdir())) / "uilab"))
    ap.add_argument("--only", default="", help="comma list of scenes: startup,composer,menus,sessions,resume,"
                                               "conversation,attention,sizes,fresh")
    ap.add_argument("--palette", default="owner", choices=["owner", "standard"])
    ap.add_argument("--no-png", action="store_true")
    ap.add_argument("--tui-root", default="", help="run the TUI from this checkout (the backend stays this one)")
    ap.add_argument("--motion", default="on", choices=["on", "off"])
    args = ap.parse_args()
    if not shutil.which("tmux"):
        print("uilab needs tmux", file=sys.stderr)
        return 2
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    only = set(filter(None, args.only.split(",")))
    want = (lambda s: not only or s in only)
    L = Lab(out, args.palette)
    if args.tui_root:
        L.tui_root = Path(args.tui_root).resolve()
    search = FakeSearx()
    with tempfile.TemporaryDirectory(prefix="echoui-") as tmp, ScriptedModel(SCRIPTS, DEFAULT, delay=0.02) as model:
        proc, dirs, client = lab.start_backend(Path(tmp), model.url, {"WEB_SEARCH_PROVIDER": "searxng",
                                                                       "SEARXNG_BASE_URL": search.url,
                                                                       "ENABLE_SYSTEM_ACTIONS": "true",
                                                                       "ALLOW_TERMINAL_COMMANDS": "true",
                                                                       # Search only on this machine: without it the
                                                                       # backend also merges DuckDuckGo results.
                                                                       "PRIVACY_MODE": "private",
                                                                       "HOME": str(Path(tmp) / "home")})
        try:
            theme = Path.home() / ".local/state/omarchy/current/theme/colors.toml"
            if theme.exists():   # the owner's theme colours, so diff tints and the accent match their terminal
                dest = dirs["home"] / ".local/state/omarchy/current/theme"
                dest.mkdir(parents=True, exist_ok=True)
                shutil.copy(theme, dest / "colors.toml")
            project = seed(client, dirs)
            env = {"ECHOSPEAK_RUNTIME_DIR": str(dirs["rt"]), "ECHOSPEAK_STATE_DIR": str(dirs["state"] / "ui"),
                   "TERM": "tmux-256color", "COLORTERM": "truecolor", "LANG": "C.UTF-8", "HOME": str(dirs["home"]),
                   **({"ECHO_REDUCED_MOTION": "1"} if args.motion == "off" else {})}
            main_scenes = [s for s in ("startup", "composer", "menus", "sessions", "resume", "conversation",
                                       "attention", "sizes") if want(s)]
            if main_scenes:
                if "startup" in main_scenes:
                    scene_startup(L, project, env)
                else:
                    L.launch(project, env)
                    L.wait("for shortcuts|sessions", 30)
                    time.sleep(1.5)
                for name in main_scenes[1:] if "startup" in main_scenes else main_scenes:
                    print(f"scene {name}", flush=True)
                    if name == "attention":
                        scene_attention(L, client)
                    else:
                        globals()[f"scene_{name}"](L)
                L.shot("99-full-scrollback", "the whole session, scrollback included", history=True)
                L.stop()
            if want("fresh"):
                scene_fresh(L, dirs["cwd"], env)
        finally:
            lab.tmux("kill-server", check=False)
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=30)
            except Exception:  # noqa: BLE001
                proc.kill()
            search.close()
        report = {"shots": L.index, "offered_tools": sorted(model.offered), "missing_tools": model.missing}
    (out / "index.json").write_text(json.dumps(report, indent=1))
    print(f"missing tools: {model.missing}")
    if not args.no_png:
        print("rendering PNGs…", flush=True)
        L.render()
    print(f"{len(L.index)} shots in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
