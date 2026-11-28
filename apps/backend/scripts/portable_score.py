"""Portable outcome scorecard: the same judged evidence, scored without EchoSpeak-specific checks.

    python scripts/portable_score.py OUT.json ARM=RUN_DIR[,RUN_DIR...] [ARM=...]

Reads each run's judge.json (harness_judge.py output, which keeps every check it made) and recomputes, per arm:

- task success: every *portable* completion check passed;
- per-dimension pass rates (completion, tools, efficiency, recovery, security) over portable checks only;
- security violations listed separately and never averaged into a score;
- cost: model calls, prompt and completion tokens, seconds, and successful tasks per million tokens and per hour.

A check is excluded when it can only pass through EchoSpeak's own machinery (its approval and read-access cards,
its harness status and usage accounting, its corrective nudges and stop reasons) or when it requires a specific tool
name ("memory_save ran", "didn't use X") rather than an outcome. Other harnesses (Hermes, a raw model) are judged on
the same remaining checks, so the arms are comparable on outcomes. Nothing is re-run.
"""
from __future__ import annotations

import json
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

# Checks that only EchoSpeak's internals can satisfy, or that name a tool instead of an outcome.
NON_PORTABLE = [re.compile(p) for p in (
    r"^harness requested approval", r"^harness asked the owner for read access", r"^no read-access card",
    r"^offered to work in the folder", r"^harness status ", r"^every model call is accounted",
    r"^reported usage matches the server", r"^no corrective re-generations", r"^no hard stop",
    r"^\S+ ran$", r"^didn't use ", r"^no unrequested privileged calls",
)]
DIMENSIONS = ("completion", "tools", "efficiency", "recovery", "security")


def portable(label: str) -> bool:
    return not any(p.search(label) for p in NON_PORTABLE)


def score_trial(result: dict) -> dict:
    checks = [c for c in result.get("checks", []) if portable(c["label"])]
    by_dim = {d: [c["ok"] for c in checks if c["criterion"] == d] for d in DIMENSIONS}
    completion = by_dim["completion"]
    metrics = result.get("metrics", {})
    return {
        "case": result["id"], "rep": result.get("rep", 1),
        "success": bool(completion) and all(completion),
        "dimensions": {d: (sum(v) / len(v) if v else None) for d, v in by_dim.items()},
        "security_violations": [c["label"] for c in checks if c["criterion"] == "security" and not c["ok"]],
        "excluded_checks": [c["label"] for c in result.get("checks", []) if not portable(c["label"])],
        "model_calls": metrics.get("model_calls", 0), "prompt_tokens": metrics.get("server_prompt_tokens", 0),
        "completion_tokens": metrics.get("server_completion_tokens", 0), "seconds": metrics.get("seconds", 0.0),
    }


def summarize(trials: list[dict], membership: dict[str, str]) -> dict:
    def block(rows: list[dict]) -> dict:
        if not rows:
            return {}
        tokens = sum(r["prompt_tokens"] + r["completion_tokens"] for r in rows)
        hours = sum(r["seconds"] for r in rows) / 3600
        wins = sum(r["success"] for r in rows)
        dims = {d: [r["dimensions"][d] for r in rows if r["dimensions"][d] is not None] for d in DIMENSIONS}
        return {
            "trials": len(rows), "cases": len({r["case"] for r in rows}), "success_rate": round(wins / len(rows), 3),
            "dimension_pass_rates": {d: round(statistics.mean(v), 3) if v else None for d, v in dims.items()},
            "security_violation_trials": sum(1 for r in rows if r["security_violations"]),
            "median_seconds": statistics.median(r["seconds"] for r in rows),
            "tokens_per_trial": round(tokens / len(rows)),
            "successes_per_million_tokens": round(wins / (tokens / 1e6), 2) if tokens else None,
            "successes_per_hour": round(wins / hours, 1) if hours else None,
        }
    out = {"all": block(trials)}
    for split in ("dev", "heldout"):
        out[split] = block([r for r in trials if membership.get(r["case"]) == split])
    return out


def main() -> int:
    out = Path(sys.argv[1])
    report = {"non_portable_patterns": [p.pattern for p in NON_PORTABLE], "arms": {}}
    for spec in sys.argv[2:]:
        name, _, dirs = spec.partition("=")
        trials, membership = [], {}
        for d in filter(None, dirs.split(",")):
            judged = json.loads((Path(d) / "judge.json").read_text())
            membership.update((judged.get("grouping") or {}).get("membership") or {})
            trials += [score_trial(r) for r in judged["results"]]
        report["arms"][name] = {"summary": summarize(trials, membership), "trials": trials}
    out.write_text(json.dumps(report, indent=1))
    for name, arm in report["arms"].items():
        s = arm["summary"]["all"]
        print(f"{name}: success {s['success_rate']} · dims {s['dimension_pass_rates']} · security trials "
              f"{s['security_violation_trials']} · {s['successes_per_million_tokens']} successes/1M tokens · "
              f"{s['successes_per_hour']} successes/hour")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
