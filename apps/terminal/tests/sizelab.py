"""Size lab: the real TUI in a private tmux server, against the real backend and a scripted model.

Drives a session through idle, the slash menu, a streamed answer and a sequence of window resizes
(tmux re-wraps lines on resize, like kitty, ghostty, foot and alacritty), and captures each screen
including scrollback. It then counts ghosts: copies of the live region left behind when a resize
re-wraps the old region and the redraw erases the wrong number of rows.

    apps/terminal/.venv/bin/python apps/terminal/tests/sizelab.py [--out DIR] [--sizes 120x40,60x20]

Isolated data, runtime and state folders; it never touches a running EchoSpeak or your tmux.
Needs the backend venv (apps/backend/.venv) and the TUI venv (apps/terminal/.venv).
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from fake_model import MODEL, FakeModel  # noqa: E402

from echospeak_cli.client import Client, ConnectError  # noqa: E402

KEY = "lab-key-" + "x" * 24
SOCK = f"echolab-{os.getpid()}"
REPLY = ("## Week 53\n\nThe parser treats **ISO week 53** as the previous year. That is wrong for long years, "
         "so `2026-W53` turns into `2027-W01` and the round trip fails.\n\n"
         "1. Check `_long_year(y)` in `iso_week`.\n2. Apply the same rule in `parse_week`.\n"
         "3. Add a test for 2020, 2026 and 2032.\n\n```python\ndef _long_year(y):\n    return date(y, 12, 28).isocalendar()[1] == 53\n```\n\n"
         "That keeps every caller unchanged.")


def tmux(*args: str, check: bool = True) -> str:
    r = subprocess.run(["tmux", "-L", SOCK, "-f", "/dev/null", *args], capture_output=True, text=True, check=False)
    if check and r.returncode != 0:
        raise RuntimeError(f"tmux {' '.join(args)}: {r.stderr}")
    return r.stdout


def capture(history: bool = True) -> str:
    return tmux("capture-pane", "-p", "-t", "lab", *(["-S", "-"] if history else []))


def wait_for(needle: str, timeout: float = 60) -> str:
    deadline = time.monotonic() + timeout
    while True:
        screen = capture(history=False)
        if any(n in screen for n in needle.split("|")):
            return screen
        if time.monotonic() > deadline:
            raise TimeoutError(f"{needle!r} never appeared:\n{screen}")
        time.sleep(0.15)


def keys(*k: str) -> None:
    tmux("send-keys", "-t", "lab", *k)


def resize(w: int, h: int) -> None:
    tmux("resize-window", "-t", "lab", "-x", str(w), "-y", str(h))


RULE_ONLY = re.compile(r"^\s*[─-]{8,}\s*$")


def ghosts(full: str, footer_mark: str) -> dict:
    """Live-region pieces that appear more than once: footers, composer rules, prompts."""
    lines = full.rstrip("\n").split("\n")
    return {
        "footers": sum(1 for line in lines if footer_mark in line and ("shortcuts" in line or "·" in line)
                       and line.startswith("  ")),
        "rule_only_lines": sum(1 for line in lines if RULE_ONLY.match(line)),
        "prompts": sum(1 for line in lines if line.startswith(("› ", "> "))),
    }


def start_backend(tmp: Path, model_url: str, extra_env: Optional[dict] = None) -> tuple[subprocess.Popen, dict, Client]:
    dirs = {name: tmp / name for name in ("data", "logs", "home", "rt", "state", "cwd")}
    for d in dirs.values():
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ECHOSPEAK_", "API_", "XDG_RUNTIME", "TMUX"))}
    env.update({
        "ECHOSPEAK_DATA_DIR": str(dirs["data"]), "ECHOSPEAK_LOGS_DIR": str(dirs["logs"]),
        "ECHOSPEAK_HOME": str(dirs["home"]), "SOUL_PATH": str(dirs["data"] / "SOUL.md"),
        "ECHOSPEAK_RUNTIME_DIR": str(dirs["rt"]), "ECHOSPEAK_RUNTIME_KIND": "desktop",
        "API_AUTH_ENABLED": "true", "API_AUTH_LOCALHOST_BYPASS": "false", "API_AUTH_KEY": KEY,
        "USE_LOCAL_MODELS": "true", "LOCAL_MODEL_PROVIDER": "vllm", "LOCAL_MODEL_URL": model_url,
        "LOCAL_MODEL_NAME": MODEL, "ECHOSPEAK_DISABLE_WARMUP": "1", "LEARNING_ENABLED": "0",
        "CHECKIN_MODE": "off", "LEAN_QUARANTINE_READER": "off", "PYTHONUNBUFFERED": "1",
    })
    env.update(extra_env or {})
    py = ROOT / "apps" / "backend" / ".venv" / "bin" / "python"
    log = open(tmp / "backend.log", "wb")  # noqa: SIM115 - stays open for the backend's lifetime (the child writes it)
    proc = subprocess.Popen([str(py), "app.py", "--mode", "api", "--host", "127.0.0.1", "--port", str(port)],
                            cwd=str(ROOT / "apps" / "backend"), env=env, stdout=log, stderr=subprocess.STDOUT)
    client = Client(f"http://127.0.0.1:{port}", KEY, timeout=10)
    deadline = time.monotonic() + 120
    while True:
        if proc.poll() is not None:
            raise RuntimeError((tmp / "backend.log").read_text()[-3000:])
        try:
            client.health()
            break
        except ConnectError:
            if time.monotonic() > deadline:
                raise RuntimeError("backend did not start")
            time.sleep(0.5)
    return proc, dirs, client


def run_lab(out: Path, sizes: list[tuple[int, int]], tmp: Path) -> list[dict]:
    results = []
    with FakeModel(REPLY, delay=0.08) as model:   # ~6 s of streaming: time to resize mid-answer
        proc, dirs, _client = start_backend(tmp, model.url)
        try:
            for w, h in sizes:
                results.append(scenario(out, dirs, w, h))
        finally:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
    return results


def scenario(out: Path, dirs: dict, w: int, h: int) -> dict:
    tag = f"{w}x{h}"
    env = {"ECHOSPEAK_RUNTIME_DIR": str(dirs["rt"]), "ECHOSPEAK_STATE_DIR": str(dirs["state"] / tag),
           "TERM": "tmux-256color", "LANG": "C.UTF-8", "ECHO_REDUCED_MOTION": "1", "HOME": str(dirs["home"])}
    launcher = ROOT / "apps" / "terminal" / "echospeak"
    py = ROOT / "apps" / "terminal" / ".venv" / "bin" / "python"
    cmd = " ".join(f"{k}={v}" for k, v in env.items()) + f" {py} {launcher} new"
    tmux("kill-server", check=False)
    tmux("new-session", "-d", "-s", "lab", "-x", str(w), "-y", str(h), "-c", str(dirs["cwd"]), f"env {cmd}")
    tmux("set", "-g", "window-size", "manual")
    tmux("set", "-g", "history-limit", "5000")
    tmux("set", "-g", "status", "off")
    shots: dict[str, str] = {}
    report: dict = {"size": tag, "ghosts": {}, "shots": []}
    try:
        try:
            wait_for("Work in this folder?", timeout=20)
            time.sleep(0.4)
            keys("n")
        except TimeoutError:
            pass
        wait_for("for shortcuts" if w >= 60 else MODEL[:6], timeout=30)
        time.sleep(0.5)
        shots["1-idle"] = capture()
        keys("/")
        time.sleep(0.5)
        shots["2-menu"] = capture()
        keys("Escape")
        time.sleep(0.3)
        keys("C-u")
        keys("explain the week 53 bug", "Enter")
        time.sleep(1.2)
        shots["3-streaming"] = capture()
        # Resize while the live region is at its tallest (stream tail + status), then back.
        resize(max(20, w // 2), h)
        time.sleep(0.6)
        shots["3b-streaming-narrow"] = capture()
        resize(w, h)
        time.sleep(0.6)
        shots["3c-streaming-back"] = capture()
        wait_for("Answered in|Worked for", timeout=60)
        time.sleep(1.0)
        shots["4-answered"] = capture()
        steps = [(max(20, w // 2), h), (max(14, w // 3), h), (w, h), (w, max(6, h // 2)), (w + 30, h), (w, h)]
        for i, (rw, rh) in enumerate(steps):
            resize(rw, rh)
            time.sleep(0.8)
            shots[f"5-resize-{i}-{rw}x{rh}"] = capture()
        keys("C-c")
        time.sleep(0.3)
        keys("C-c")
        time.sleep(0.8)
    finally:
        tmux("kill-server", check=False)
    for name, text in shots.items():
        (out / f"{tag}-{name}.txt").write_text(text)
        report["ghosts"][name] = ghosts(text, MODEL[:10])
        report["shots"].append(name)
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(os.environ.get("CLAUDE_JOB_DIR", tempfile.gettempdir())) / "sizelab"))
    ap.add_argument("--sizes", default="120x40,80x24,60x20,40x16")
    args = ap.parse_args()
    if not shutil.which("tmux"):
        print("sizelab needs tmux", file=sys.stderr)
        return 2
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    sizes = [tuple(int(x) for x in s.split("x")) for s in args.sizes.split(",")]
    with tempfile.TemporaryDirectory(prefix="echolab-") as tmp:
        results = run_lab(out, sizes, Path(tmp))
    bad = 0
    for r in results:
        print(f"\n== {r['size']}")
        for name, g in r["ghosts"].items():
            flag = "" if g["footers"] <= 1 and g["prompts"] <= 2 else "   <-- ghost"
            bad += bool(flag)
            print(f"  {name:28} footers={g['footers']} prompts={g['prompts']} rule-only={g['rule_only_lines']}{flag}")
    print(f"\ncaptures in {out}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
