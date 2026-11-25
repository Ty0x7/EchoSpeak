"""Layout lab: where the composer and a new answer sit, frame by frame (issues: response anchoring, slash dismissal).

Reuses uilab's isolated backend, scripted model and private tmux. Two scenes:

- ``slash``: the composer row before ``/``, with the menu open, and after Esc, repeated several times; plus the
  same after typing ``/he`` (a filtered menu) and after Esc-dismissing it.
- ``stream``: a fresh conversation (no history on screen), one message, frames sampled every 50 ms while the
  answer streams. For each frame it records the row of the first answer line and of the composer, so a
  bottom-anchored start followed by a jump to the top shows as a row that moves up.

    apps/terminal/.venv/bin/python apps/terminal/tests/layoutlab.py --out DIR [--tui-root DIR] [--no-png]

Writes ``layout.json`` with the measured rows and pass/fail per check. No model inference, no network.
"""
from __future__ import annotations

import argparse
import json
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
import uilab  # noqa: E402
from fake_model import ScriptedModel  # noqa: E402

STREAM_TEXT = ("Here is the plan.\n\n" + "\n".join(f"{i}. Step {i} of the parser fix, with a short reason." for i in range(1, 9))
               + "\n\nThat's everything.")
SCRIPTS = [("stream a numbered plan", [{"text": STREAM_TEXT, "delay": 0.03}])] + uilab.SCRIPTS


def prompt_row(screen: str) -> int:
    """The composer's row: the last line that starts with the prompt glyph."""
    rows = [i for i, line in enumerate(screen.split("\n")) if line.lstrip().startswith("›")]
    return rows[-1] if rows else -1


def footer_row(screen: str) -> int:
    rows = [i for i, line in enumerate(screen.split("\n")) if re.search(r"for shortcuts|\? help|esc to stop|esc stop", line)]
    return rows[-1] if rows else -1


def last_text_row(screen: str) -> int:
    rows = [i for i, line in enumerate(screen.split("\n")) if line.strip()]
    return rows[-1] if rows else -1


def scene_slash(L: uilab.Lab, result: dict) -> None:
    base = L.screen()
    L.shot("s00-before", "composer before /")
    before = {"prompt": prompt_row(base), "last": last_text_row(base)}
    trials = []
    for i, typed in enumerate(["/", "/", "/he", "/", "/"]):
        lab.tmux("send-keys", "-t", "lab", "-l", typed)
        time.sleep(0.6)
        opened = L.screen()
        L.shot(f"s{i + 1:02d}a-open", f"menu open after {typed!r}")
        lab.keys("Escape")
        time.sleep(0.5)
        after_esc = L.screen()
        L.shot(f"s{i + 1:02d}b-esc", "after Esc")
        # Clear whatever text is left so the next trial starts from an empty composer.
        lab.keys("C-u")
        time.sleep(0.4)
        cleared = L.screen()
        L.shot(f"s{i + 1:02d}c-cleared", "after clearing the input")
        trials.append({"typed": typed, "open_prompt": prompt_row(opened), "open_last": last_text_row(opened),
                       "esc_prompt": prompt_row(after_esc), "esc_last": last_text_row(after_esc),
                       "cleared_prompt": prompt_row(cleared), "cleared_last": last_text_row(cleared),
                       "esc_text": after_esc})
    ok = all(t["esc_last"] <= before["last"] + 0 and t["cleared_last"] == before["last"] for t in trials)
    result["slash"] = {"before": before, "trials": [{k: v for k, v in t.items() if k != "esc_text"} for t in trials],
                       "pass": ok}


def scene_stream(L: uilab.Lab, result: dict, new: bool = True) -> None:
    lab.keys("C-u")
    if new:
        L.send("/new")
        time.sleep(1.5)
    L.shot("t00-new", "after /new")
    lab.tmux("send-keys", "-t", "lab", "-l", "stream a numbered plan")
    time.sleep(0.3)
    lab.keys("Enter")
    frames = []
    start = time.monotonic()
    last = None
    while time.monotonic() - start < 12:
        s = L.screen()
        if s != last:
            last = s
            lines = s.split("\n")
            first = next((i for i, line in enumerate(lines) if "Here is the plan" in line), -1)
            asked = next((i for i, line in enumerate(lines) if "stream a numbered plan" in line and i < prompt_row(s)), -1)
            frames.append({"t": round(time.monotonic() - start, 2), "asked_row": asked, "answer_row": first,
                           "prompt_row": prompt_row(s), "last_row": last_text_row(s)})
            if len(frames) % 4 == 1:
                L.shot(f"t{len(frames):03d}-frame", f"streaming frame at {frames[-1]['t']}s")
        if "That's everything" in s and not re.search(r"esc to stop|esc stop", s):
            break
        time.sleep(0.05)
    L.idle()
    time.sleep(0.5)
    L.shot("t99-done", "answer finished")
    rows = [f["answer_row"] for f in frames if f["answer_row"] >= 0]
    moved_up = any(b < a for a, b in zip(rows, rows[1:]))
    result["stream"] = {"frames": frames, "asked_rows_seen": sorted(set(rows)), "asked_row_moved_up": moved_up,
                        "pass": bool(rows) and not moved_up}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--tui-root", default="")
    ap.add_argument("--only", default="slash,stream")
    ap.add_argument("--size", default="110x32")
    ap.add_argument("--no-png", action="store_true")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    w, h = (int(x) for x in args.size.split("x"))
    L = uilab.Lab(out, "owner")
    if args.tui_root:
        L.tui_root = Path(args.tui_root).resolve()
    result: dict = {"tui_root": str(L.tui_root), "size": [w, h]}
    search = uilab.FakeSearx()
    with tempfile.TemporaryDirectory(prefix="echolayout-") as tmp, ScriptedModel(SCRIPTS, uilab.DEFAULT, delay=0.02) as model:
        proc, dirs, client = lab.start_backend(Path(tmp), model.url, {"WEB_SEARCH_PROVIDER": "searxng",
                                                                       "SEARXNG_BASE_URL": search.url,
                                                                       "PRIVACY_MODE": "private",
                                                                       "HOME": str(Path(tmp) / "home")})
        try:
            project = uilab.seed(client, dirs)
            env = {"ECHOSPEAK_RUNTIME_DIR": str(dirs["rt"]), "ECHOSPEAK_STATE_DIR": str(dirs["state"] / "ui"),
                   "TERM": "tmux-256color", "COLORTERM": "truecolor", "LANG": "C.UTF-8", "HOME": str(dirs["home"])}
            only = set(args.only.split(","))
            L.launch(project, env, size=(w, h))
            L.wait("for shortcuts|sessions", 30)
            time.sleep(2.0)
            if "slash" in only:
                scene_slash(L, result)
            if "slashfull" in only:
                # The owner's case: the conversation already fills the screen, so the composer sits at the bottom.
                for _ in range(2):
                    L.send("stream a numbered plan")
                    L.idle()
                time.sleep(0.6)
                L.prefix = "full-"
                sub: dict = {}
                scene_slash(L, sub)
                result["slashfull"] = sub["slash"]
                L.prefix = ""
            if "clear" in only:
                for _ in range(2):
                    L.send("stream a numbered plan")
                    L.idle()
                L.send("/clear")
                time.sleep(1.5)
                L.shot("c00-after-clear", "after /clear")
                L.prefix = "clear-"
                sub = {}
                scene_stream(L, sub, new=False)
                result["clear"] = sub["stream"]
                L.prefix = ""
            if "stream" in only:
                scene_stream(L, result)
            L.stop()
        finally:
            lab.tmux("kill-server", check=False)
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=30)
            except Exception:  # noqa: BLE001
                proc.kill()
            search.close()
    (out / "layout.json").write_text(json.dumps(result, indent=1))
    (out / "index.json").write_text(json.dumps({"shots": L.index}, indent=1))
    if not args.no_png:
        L.render()
    for key in ("slash", "slashfull", "stream", "clear"):
        if key in result:
            print(key, "PASS" if result[key]["pass"] else "FAIL")
    return 0


if __name__ == "__main__":
    sys.exit(main())
