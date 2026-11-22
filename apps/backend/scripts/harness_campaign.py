"""One command for a model evaluation campaign: baseline and current harness, judged, compared and summarised.

The five-model evaluation was run by hand: export the baseline code, run each model, copy run folders,
combine, judge, tabulate. That cost as much wall time as some of the runs. This does it in one pass:

    python scripts/harness_campaign.py --models gemma4-12b-it:q4_K_M,deepseek-v4-qwen3.5-9b:q4_K_M \
        --baseline-ref c2ffb65 --repeat 3 --label new-models

For each model, in turn (so it's loaded once and stays loaded):
  1. load it and keep it loaded for the campaign (Ollama keep_alive), so no run starts cold;
  2. run the cases on the baseline harness (a `git archive` of --baseline-ref, made once, with today's
     eval scripts copied in so cases and judge are identical), then on this worktree's harness;
  3. judge each run in a background thread while the next one runs.
Then it writes campaign.json and campaign.md (scores, criteria, critical runs, cost, model speed from
the server's own timings) into data/evals/harness/<stamp>-<label>/.

Only the models named are run. Nothing is downloaded or pulled.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
import shutil
import signal
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
OUT = ROOT / "data" / "evals" / "harness"
CACHE = Path.home() / ".cache" / "echospeak-harness-eval"
SCRIPTS = ("harness_eval.py", "harness_judge.py", "model_recorder.py")


def baseline_tree(ref: str) -> Path:
    """apps/backend as of `ref`, with today's eval scripts, made once and reused."""
    tree = CACHE / f"baseline-{ref}"
    backend = tree / "apps" / "backend"
    if not backend.exists():
        tree.mkdir(parents=True, exist_ok=True)
        archive = tree / "src.tar"
        subprocess.run(["git", "-C", str(REPO), "archive", "-o", str(archive), ref, "apps/backend"], check=True)
        subprocess.run(["tar", "-xf", str(archive), "-C", str(tree)], check=True)
        archive.unlink()
    for name in SCRIPTS:
        shutil.copy(ROOT / "scripts" / name, backend / "scripts" / name)
    return backend


def keep_loaded(model: str, upstream: str) -> float:
    """Load the model now and keep it loaded for the campaign; returns the load time."""
    started = time.time()
    body = json.dumps({"model": model, "prompt": "", "keep_alive": "2h"}).encode()
    request = urllib.request.Request(upstream.rstrip("/") + "/api/generate", data=body,
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=600) as response:
        response.read()
    return round(time.time() - started, 1)


def unload(model: str, upstream: str) -> None:
    """Release the model and Ollama's host-RAM prompt cache. keep_alive 2h held about 8 GB after the 18 November
    campaign (MemAvailable 1.9 GB, 10 GB swapped); unloading freed it. Best effort."""
    try:
        body = json.dumps({"model": model, "keep_alive": 0}).encode()
        request = urllib.request.Request(upstream.rstrip("/") + "/api/generate", data=body,
                                         headers={"Content-Type": "application/json"})
        urllib.request.urlopen(request, timeout=60).read()
    except Exception:
        pass


def run_eval(backend: Path, model: str, label: str, repeat: int, only: str, cases: str) -> Path:
    before = set(OUT.glob(f"*-{label}")) | set((backend / "data/evals/harness").glob(f"*-{label}"))
    args = [sys.executable, "scripts/harness_eval.py", "--model", model, "--repeat", str(repeat), "--label", label]
    args += ["--only", only] if only else ["--set", cases]
    subprocess.run(args, cwd=backend, check=True)
    produced = sorted(set((backend / "data/evals/harness").glob(f"*-{label}")) - before)
    if len(produced) != 1:
        raise RuntimeError(f"Expected one fresh evaluation run for {label}, found {len(produced)}")
    run = produced[0]
    if backend != ROOT:  # keep every run in one place
        target = OUT / run.name
        shutil.copytree(run, target)
        run = target
    return run


def judge(run: Path) -> dict[str, Any]:
    child = subprocess.run([sys.executable, str(ROOT / "scripts" / "harness_judge.py"), str(run)],
                           capture_output=True, text=True, timeout=1800)
    if child.returncode:
        diagnostic = (child.stderr or child.stdout).strip()[-600:]
        raise RuntimeError(f"harness_judge exited {child.returncode}: {diagnostic}")
    return json.loads((run / "judge.json").read_text(encoding="utf-8"))


def require_complete_scorecard(report: dict[str, Any], model: str, cases: set[str], repeat: int) -> None:
    """Require exactly one scored receipt for each requested case and repetition."""
    expected = Counter((case, rep) for case in cases for rep in range(1, repeat + 1))
    meta = report.get("meta") or {}
    if (not expected or not meta.get("ended") or meta.get("model") != model or meta.get("repeat") != repeat
            or set(meta.get("cases") or []) != cases):
        raise RuntimeError("Incomplete scorecard: evaluation metadata does not match the requested arm")
    observed = Counter((row["id"], row["rep"]) for row in report["results"])
    if observed != expected:
        raise RuntimeError(f"Incomplete scorecard: missing {dict(expected - observed)}, unexpected {dict(observed - expected)}")


def score_arm(run: Path, model: str, cases: set[str], repeat: int) -> dict[str, Any]:
    report = judge(run)
    require_complete_scorecard(report, model, cases, repeat)
    return {"run": run.name, **summary(report), "speed": speed(run)}


def speed(run: Path) -> dict[str, float]:
    """Model speed from the server's own timings: generation and prompt tokens/s, and the share served from cache."""
    gen, prompt, cached = [], [], []
    for line in (run / "model-calls.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        timing, usage = row["response"].get("timings") or {}, row["response"].get("usage") or {}
        if timing.get("predicted_per_second"):
            gen.append(timing["predicted_per_second"])
        if timing.get("prompt_per_second") and timing.get("prompt_n", 0) > 64:
            prompt.append(timing["prompt_per_second"])
        if usage.get("prompt_tokens") and "prompt_n" in timing:
            cached.append(1 - timing["prompt_n"] / max(1, usage["prompt_tokens"]))
    median = lambda xs: round(statistics.median(xs), 1) if xs else None  # noqa: E731
    return {"gen_tok_s": median(gen), "prompt_tok_s": median(prompt),
            "prompt_cached": round(statistics.mean(cached), 2) if cached else None}


def summary(report: dict[str, Any]) -> dict[str, Any]:
    results = report["results"]
    agg = report["aggregate"]
    return {"score": agg["all"], "dev": agg.get("dev"), "heldout": agg.get("heldout"),
            "success": agg["execution_success_rate"], "critical_runs": agg["critical_runs"], "criteria": agg["criteria"],
            "critical": [f"{r['id']} r{r['rep']}: {c}" for r in results for c in r["critical"]],
            "calls": sum(r["metrics"]["model_calls"] for r in results),
            "seconds": round(sum(r["metrics"]["seconds"] for r in results)),
            "cases": {k: v["mean"] for k, v in report["cases"].items()}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--models", required=True, help="Comma-separated Ollama tags")
    parser.add_argument("--baseline-ref", default="", help="Git ref for the baseline harness (omit to skip the baseline)")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--set", default="all")
    parser.add_argument("--only", default="")
    parser.add_argument("--label", default="campaign")
    parser.add_argument("--upstream", default="http://127.0.0.1:11434")
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT / "scripts"))
    from harness_eval import CASE_MEMBERSHIP, SETS, assert_local
    assert_local(args.upstream)
    models = list(dict.fromkeys(m.strip() for m in args.models.split(",") if m.strip()))
    if not models or args.repeat < 1:
        parser.error("Supply at least one model and a positive repeat count")
    if args.only:
        cases = {case.strip() for case in args.only.split(",") if case.strip()}
        if not cases or cases - CASE_MEMBERSHIP.keys():
            parser.error("--only must name registered cases")
    else:
        if args.set not in SETS:
            parser.error(f"Unknown case set: {args.set}")
        cases = {case.id for case in SETS[args.set]}
    folder = OUT / f"{time.strftime('%Y%m%d-%H%M%S')}-{args.label}"
    folder.mkdir(parents=True)
    head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    required = ("baseline", "current") if args.baseline_ref else ("current",)
    campaign: dict[str, Any] = {
        "label": args.label, "head": head, "baseline_ref": args.baseline_ref, "status": "running",
        "models": {model: {} for model in models}, "required_arms": required, "failures": [],
    }
    (folder / "campaign.json").write_text(json.dumps(campaign, indent=1), encoding="utf-8")
    loaded = []
    jobs = {}
    context = {}
    with ThreadPoolExecutor() as workers:
        try:
            base = baseline_tree(args.baseline_ref) if args.baseline_ref else None
            for model in models:
                context = {"model": model}
                slug = model.replace(":", "-").replace(".", "-").replace("/", "-")
                loaded.append(model)
                load = keep_loaded(model, args.upstream)
                campaign["models"][model]["load_seconds"] = load
                print(f"{model}: loaded in {load}s", flush=True)
                for arm in required:
                    context = {"model": model, "arm": arm}
                    backend = base if arm == "baseline" else ROOT
                    run = run_eval(backend, model, f"{args.label}-{arm}-{slug}", args.repeat, args.only, args.set)
                    jobs[model, arm] = (run, workers.submit(score_arm, run, model, cases, args.repeat))
        except (Exception, KeyboardInterrupt) as exc:
            campaign["failures"].append({**context, "reason": str(exc) or "Campaign interrupted"})
        for (model, arm), (run, task) in jobs.items():
            try:
                campaign["models"][model][arm] = task.result()
            except (Exception, KeyboardInterrupt) as exc:
                campaign["failures"].append({"model": model, "arm": arm, "run": str(run),
                                             "reason": str(exc) or "Judging interrupted"})

    for model in loaded:
        try:
            unload(model, args.upstream)
        except KeyboardInterrupt as exc:
            campaign["failures"].append({"model": model, "reason": str(exc) or "Cleanup interrupted"})

    campaign["missing_arms"] = [{"model": model, "arm": arm} for model in models for arm in required
                                if arm not in campaign["models"][model]]
    failed = bool(campaign["failures"] or campaign["missing_arms"])
    campaign["status"] = "failed" if failed else "complete"
    text = markdown(campaign)
    (folder / "campaign.md").write_text(text, encoding="utf-8")
    completed = folder / "campaign.json.tmp"
    completed.write_text(json.dumps(campaign, indent=1), encoding="utf-8")
    completed.replace(folder / "campaign.json")
    print(text)
    return 2 if failed else 0


def markdown(campaign: dict[str, Any]) -> str:
    lines = [f"# Campaign {campaign['label']} (harness {campaign['head']}, baseline {campaign['baseline_ref'] or '–'})", "",
             f"**Status: {campaign['status']}**", ""]
    for failure in campaign["failures"]:
        subject = " ".join(failure[key] for key in ("model", "arm") if key in failure)
        lines.append(f"- {subject or 'Campaign'} failed: {failure['reason']}")
    if campaign["missing_arms"]:
        lines += ["", "Unscored required arms: " +
                  ", ".join(f"{item['model']} {item['arm']}" for item in campaign["missing_arms"]), ""]
    lines += [
             "| Model | Baseline | Current | Held-out | Success | Critical runs | Calls | Time | Gen tok/s | Prompt cached |",
             "|---|---:|---:|---|---|---|---|---|---|---|"]
    for model, sides in campaign["models"].items():
        b, c = sides.get("baseline", {}), sides.get("current", {})
        arrow = lambda k, f="{}": (f"{f.format(b[k])} → " if k in b else "") + (f.format(c[k]) if k in c else "–")  # noqa: E731
        lines.append(f"| {model} | {b.get('score', '–')} | **{c.get('score', '–')}** | {arrow('heldout')} | "
                     f"{arrow('success', '{:.0%}')} | {arrow('critical_runs')} | {arrow('calls')} | {arrow('seconds', '{}s')} | "
                     f"{(c.get('speed') or {}).get('gen_tok_s')} | {(c.get('speed') or {}).get('prompt_cached')} |")
    for model, sides in campaign["models"].items():
        for side in ("baseline", "current"):
            if side in sides and sides[side]["critical"]:
                lines.append(f"\n- {model} {side} critical: " + "; ".join(sides[side]["critical"]))
    return "\n".join(lines) + "\n"


def _terminate(_signum, _frame) -> None:
    raise KeyboardInterrupt("Campaign terminated")


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _terminate)
    sys.exit(main())
