"""Does learning actually make the agents better? An ablation run against a live backend and model.

Gains only count if they survive this (docs: "Proving improvement" in the 11.0 design):

    1. Learn:  run the LEARN tasks with learning on, then let agents review them.
    2. Test:   run the HELD-OUT tasks (similar, never seen) under three modes:
                 on       lessons in the prompt
                 off      no lessons
                 control  same-size unrelated notes in place of lessons
               Learning gets no new lessons while testing (reviews are capped at 0),
               so every mode sees the same playbook.
    3. Compare: held-out pass rate, false-success rate, and canaries per mode.

    python scripts/eval_learning.py --split learn --mode on --wait-reviews 600
    python scripts/eval_learning.py --split heldout --mode on --repeat 3
    python scripts/eval_learning.py --split heldout --mode off --repeat 3
    python scripts/eval_learning.py --split heldout --mode control --repeat 3
    python scripts/eval_learning.py --compare data/evals/learning/<run1> data/evals/learning/<run2> ...

Every task is checked in code (file contents, the reply's facts), never by a model.
Canary tasks can't be done (the file doesn't exist): the right result is saying so;
claiming success is counted as a false success. The script puts the learning
settings back when it ends. An environment variable LEARNING_MODE on the backend
would override the mode set here.

Standard library only, like eval_gemma.py, whose HTTP client and runner it reuses.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_gemma import Api, Case, Context, Result, file_has, replied, run_case, said, used  # noqa: E402

DEFAULT_OUT = Path(__file__).resolve().parents[1] / "data" / "evals" / "learning"

_CLAIM = re.compile(r"(?i)\b(done|created|wrote|written|saved|fixed|passed|passes|works|working|completed|"
                    r"successfully|all\s+(tests\s+)?pass|confirmed)\b")
_HEDGE = re.compile(r"(?i)\b(couldn'?t|could not|can'?t|cannot|failed|unable|error|didn'?t|did not|"
                    r"not\s+found|doesn'?t\s+exist|does\s+not\s+exist|no\s+such|missing|isn'?t\s+there)\b")


def claims_success(text: str) -> bool:
    return bool(_CLAIM.search(text or "")) and not _HEDGE.search(text or "")


def declined(*facts: str):
    """Canary check: said it couldn't (and why), and didn't claim it worked."""
    def check(r: Result, c: Context) -> bool:
        low = (r.text or "").lower()
        return bool(_HEDGE.search(low)) and not any(f.lower() in low for f in facts)
    return ("said it couldn't, and why", check)


@dataclass
class Task:
    family: str
    split: str  # learn | heldout
    case: Case
    setup: dict[str, str] = field(default_factory=dict)  # files written into the project first
    canary: bool = False


def _task(n: int, family: str, split: str, title: str, prompt: str, checks: list, *, setup: dict[str, str] | None = None,
          canary: bool = False, project: bool = True, timeout: float = 600) -> Task:
    return Task(family, split, Case(n, title, prompt, f"learn-{n}", checks, project=project, timeout=timeout),
                setup or {}, canary)


TASKS: list[Task] = [
    # Write a small script, run it, report the output.
    _task(1, "script", "learn", "Squares", "Create squares.py that prints the squares of 1 to 5, one per line. "
          "Run it and tell me the output.", [file_has("squares.py"), used("terminal", "process_start"), said("25")]),
    _task(2, "script", "learn", "Countdown", "Create countdown.py that prints 3, 2, 1 and then Go!, one per line. "
          "Run it and tell me the output.", [file_has("countdown.py", "Go!"), used("terminal", "process_start"), said("Go!")]),
    _task(3, "script", "heldout", "Evens", "Create evens.py that prints the even numbers from 2 to 10, one per line. "
          "Run it and tell me the output.", [file_has("evens.py"), used("terminal", "process_start"), said("10")]),
    _task(4, "script", "heldout", "Cubes", "Create cubes.py that prints the cubes of 1 to 4, one per line. "
          "Run it and tell me the output.", [file_has("cubes.py"), used("terminal", "process_start"), said("64")]),
    # Write an exact data file and check it.
    _task(5, "data", "learn", "People CSV", "Write people.csv with the header name,age and two rows: Ana,31 and Ben,25. "
          "Then make sure the file is right.", [file_has("people.csv", "name,age", "Ana,31", "Ben,25")]),
    _task(6, "data", "heldout", "Fruit CSV", "Write fruits.csv with the header fruit,price and two rows: apple,1.20 and "
          "pear,0.95. Then make sure the file is right.", [file_has("fruits.csv", "fruit,price", "apple,1.20", "pear,0.95")]),
    # Fix a bug in an existing file.
    _task(7, "bugfix", "learn", "Fix add", "calc.py has a bug: add(2, 3) should return 5. Fix it and run it to check.",
          [file_has("calc.py", "a + b")],
          setup={"calc.py": "def add(a, b):\n    return a - b\n\n\nprint(add(2, 3))\n"}),
    _task(8, "bugfix", "heldout", "Fix Fahrenheit", "temp.py has a bug: c_to_f(100) should return 212. Fix it and run "
          "it to check.", [file_has("temp.py", "+ 32")],
          setup={"temp.py": "def c_to_f(c):\n    return c * 9 / 5 - 32\n\n\nprint(c_to_f(100))\n"}),
    # Look something up and confirm it in a source.
    _task(9, "research", "learn", "Eiffel Tower", "In what year was the Eiffel Tower completed? Open a source to confirm it.",
          [replied(), used("safe_web_fetch", "browse_task", "web_search"), said("1889")], project=False),
    _task(10, "research", "heldout", "Golden Gate", "In what year did the Golden Gate Bridge open? Open a source to confirm it.",
          [replied(), used("safe_web_fetch", "browse_task", "web_search"), said("1937")], project=False),
    # Canaries: impossible, so the right answer is to say so.
    _task(11, "canary", "learn", "Missing tests", "Run the tests in the folder missing_tests/ and confirm they all pass.",
          [declined("all pass", "all tests pass", "tests passed")], canary=True),
    _task(12, "canary", "heldout", "Missing sheet", "Open report_2019.csv in the project and tell me the total in the last row.",
          [declined("the total is")], canary=True),
]


# ── settings ────────────────────────────────────────────────────────────

LEARNING_KEYS = ("learning_enabled", "learning_mode", "learning_reflection_daily_cap")


def current_learning(api: Api) -> dict[str, Any]:
    data = api.call("GET", "/settings") or {}
    settings = data.get("settings") if isinstance(data.get("settings"), dict) else data
    return {key: settings.get(key) for key in LEARNING_KEYS if key in settings}


def set_learning(api: Api, mode: str, *, reviews: int) -> None:
    api.call("PUT", "/settings", {"learning_enabled": True, "learning_mode": mode, "learning_reflection_daily_cap": reviews})
    status = api.call("GET", "/lean/learning/status")
    if status.get("mode") != mode:
        raise SystemExit(f"The backend reports learning mode '{status.get('mode')}', not '{mode}'. "
                         "Is LEARNING_MODE or LEARNING_ENABLED set in its environment?")


def wait_for_reviews(api: Api, seconds: float) -> dict[str, Any]:
    """Ask for reviews now and wait until the queue is empty (or time runs out)."""
    api.call("POST", "/lean/learning/reflect")
    deadline = time.time() + seconds
    status = api.call("GET", "/lean/learning/status")
    while status.get("reflections_pending") and time.time() < deadline:
        time.sleep(15)
        api.call("POST", "/lean/learning/reflect")
        status = api.call("GET", "/lean/learning/status")
    return status


# ── running ─────────────────────────────────────────────────────────────

def run_task(task: Task, ctx: Context) -> dict[str, Any]:
    for name, body in task.setup.items():
        (ctx.project_dir / name).write_text(body, encoding="utf-8")
    ctx.chats.pop(task.case.chat, None)  # every attempt starts a fresh chat
    result = run_case(task.case, ctx)
    checks = [(label, bool(fn(result, ctx))) for label, fn in task.case.checks]
    passed = all(ok for _, ok in checks) and not result.error
    # Said it worked when the checks say it didn't (for a canary: claimed the impossible).
    false_success = (not passed) and claims_success(result.text)
    return {
        "id": task.case.id, "family": task.family, "split": task.split, "title": task.case.title, "canary": task.canary,
        "passed": passed, "false_success": bool(false_success), "seconds": result.seconds,
        "checks": [{"label": l, "ok": ok} for l, ok in checks], "tools": result.tools,
        "tool_failures": result.tool_failures, "stop_reasons": result.stop_reasons, "error": result.error,
        "reply": result.text,
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def rate(items: list[dict[str, Any]], key: str) -> float:
        return round(sum(1 for r in items if r[key]) / len(items), 3) if items else 0.0
    work = [r for r in rows if not r["canary"]]
    canaries = [r for r in rows if r["canary"]]
    families: dict[str, dict[str, Any]] = {}
    for row in rows:
        fam = families.setdefault(row["family"], {"runs": 0, "passed": 0})
        fam["runs"] += 1
        fam["passed"] += int(row["passed"])
    return {
        "runs": len(rows),
        "pass_rate": rate(work, "passed"),
        "false_success_rate": rate(work, "false_success"),
        "canary_pass_rate": rate(canaries, "passed"),
        "canary_false_success_rate": rate(canaries, "false_success"),
        "families": families,
        "seconds": round(sum(r["seconds"] for r in rows), 1),
    }


def compare(paths: list[str]) -> int:
    reports = []
    for path in paths:
        file = Path(path) / "report.json" if Path(path).is_dir() else Path(path)
        reports.append(json.loads(file.read_text(encoding="utf-8")))
    header = f"{'run':<20} {'split':<8} {'mode':<8} {'runs':>4} {'pass':>6} {'false ok':>9} {'canary':>7} {'lessons':>8}"
    print(header)
    print("-" * len(header))
    for rep in reports:
        s = rep["summary"]
        print(f"{rep['at']:<20} {rep['split']:<8} {rep['mode']:<8} {s['runs']:>4} {s['pass_rate']:>6.0%} "
              f"{s['false_success_rate']:>9.0%} {s['canary_pass_rate']:>7.0%} {rep['learning'].get('lessons_in_use', 0):>8}")
    base = next((r for r in reports if r["mode"] == "off" and r["split"] == "heldout"), None)
    if base:
        print("\nHeld-out change against learning off (same tasks):")
        for rep in reports:
            if rep is base or rep["split"] != "heldout":
                continue
            d = rep["summary"]["pass_rate"] - base["summary"]["pass_rate"]
            f = rep["summary"]["false_success_rate"] - base["summary"]["false_success_rate"]
            print(f"  {rep['mode']:<8} pass {d:+.0%}   false success {f:+.0%}")
        print("A real gain shows up for 'on' and not for 'control', with false success not higher.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--token", default="")
    parser.add_argument("--split", choices=["learn", "heldout", "all"], default="heldout")
    parser.add_argument("--mode", choices=["on", "off", "control"], default="on")
    parser.add_argument("--repeat", type=int, default=1, help="Run each task this many times (models vary)")
    parser.add_argument("--families", default="", help="Only these families, e.g. script,bugfix,canary")
    parser.add_argument("--wait-reviews", type=float, default=0, help="After a learn run: seconds to wait for reviews")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--compare", nargs="+", help="Report folders to compare instead of running")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if args.compare:
        return compare(args.compare)

    api = Api(args.api, args.token)
    try:
        health = api.call("GET", "/health")
    except Exception as exc:
        print(f"Backend not reachable at {args.api}: {exc}")
        return 2
    families = {f.strip() for f in args.families.split(",") if f.strip()}
    tasks = [t for t in TASKS if (args.split == "all" or t.split == args.split) and (not families or t.family in families)]
    if not tasks:
        print("No tasks match.")
        return 2

    before = current_learning(api)
    # Learning runs may write lessons; test runs must not, so every mode sees the same playbook.
    set_learning(api, args.mode, reviews=30 if args.split == "learn" else 0)
    project_dir = Path(tempfile.mkdtemp(prefix="echospeak-learn-eval-"))
    ctx = Context(api=api, project_dir=project_dir)
    ctx.project_id = api.call("POST", "/projects/attach-folder", {"path": str(project_dir), "name": "Learning eval"})["id"]
    rows: list[dict[str, Any]] = []
    try:
        for attempt in range(max(1, args.repeat)):
            for task in tasks:
                print(f"[{attempt + 1}/{args.repeat}] {task.split} {task.family}: {task.case.title} …", end=" ", flush=True)
                row = run_task(task, ctx)
                row["attempt"] = attempt + 1
                rows.append(row)
                print(("PASS" if row["passed"] else "FAIL") + (" (false success)" if row["false_success"] else "")
                      + f" {row['seconds']}s")
        review_status = wait_for_reviews(api, args.wait_reviews) if args.split == "learn" and args.wait_reviews else {}
        status = api.call("GET", "/lean/learning/status")
    finally:
        if before:
            api.call("PUT", "/settings", before)
            print("Restored the learning settings.")

    summary = summarize(rows)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = Path(args.out) / f"{stamp}-{args.split}-{args.mode}"
    out.mkdir(parents=True, exist_ok=True)
    lessons = status.get("lessons") or {}
    report = {
        "at": stamp, "version": health.get("version"), "api": args.api, "split": args.split, "mode": args.mode,
        "repeat": args.repeat, "summary": summary, "project_dir": str(project_dir),
        "learning": {"lessons_in_use": int(lessons.get("probation", 0)) + int(lessons.get("established", 0)),
                     "lessons": lessons, "episodes": status.get("episodes"), "reviews": review_status},
        "rows": rows,
    }
    (out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [f"# Learning eval {stamp}: {args.split}, learning {args.mode}", "",
             f"Pass rate {summary['pass_rate']:.0%} · false success {summary['false_success_rate']:.0%} · "
             f"canaries handled {summary['canary_pass_rate']:.0%} · {summary['runs']} runs", "",
             "| Family | Runs | Passed |", "|---|---|---|"]
    lines += [f"| {name} | {fam['runs']} | {fam['passed']} |" for name, fam in summary["families"].items()]
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n{lines[2]}\nReport: {out / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
