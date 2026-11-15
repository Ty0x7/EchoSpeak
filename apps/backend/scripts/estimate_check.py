"""How far EchoSpeak's prompt-size estimate is from the server's own count, over recorded model calls.

    python scripts/estimate_check.py data/evals/harness/<run>/model-calls.jsonl [...]

Reads calls recorded by scripts/model_recorder.py and compares loop._estimate_tokens (what context
fitting and the cut-prompt check in agent/lean/window.py rely on) with the prompt tokens the server
reported. Prints the spread of actual/estimate, the worst cases, and the calls the server cut.
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.lean.loop import _estimate_tokens  # noqa: E402


def main() -> int:
    ratios = []
    for name in sys.argv[1:]:
        for line in Path(name).read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            req = row["request"]
            actual = int((row["response"].get("usage") or {}).get("prompt_tokens") or 0)
            if not actual or "messages" not in req:
                continue
            estimate = _estimate_tokens(req["messages"], req.get("tools") or [])
            ratios.append((actual / max(1, estimate), estimate, actual, Path(name).parent.name, row["seq"]))
    if not ratios:
        print("no calls")
        return 1
    values = [r[0] for r in ratios]
    q = statistics.quantiles(values, n=20)
    print(f"calls {len(values)} · actual/estimate: min {min(values):.2f} · p5 {q[0]:.2f} · median {statistics.median(values):.2f} "
          f"· p95 {q[-1]:.2f} · max {max(values):.2f}")
    for r in sorted(ratios)[:3] + sorted(ratios)[-3:]:
        print(f"  ratio {r[0]:.2f} estimate {r[1]} actual {r[2]} ({r[3]} #{r[4]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
