"""Count model replies that were empty (no content, no tool call) in recorded calls, per eval case.

    python scripts/empty_replies.py data/evals/harness/<run>

An empty reply after reasoning ("answer-in-reasoning") leaves the user with nothing even when the model
had worked out the answer. Uses each case's time window to attribute calls.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    run = Path(sys.argv[1])
    total = empty = thought = 0
    per_case: dict[str, int] = {}
    for path in sorted((run / "cases").glob("*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        for call in case["model_calls"]:
            if call["request"].get("tools") is None and len(call["request"].get("messages", [])) == 1:
                continue  # a side call (compaction, reader)
            total += 1
            resp = call["response"]
            if not resp.get("tool_calls") and not str(resp.get("content") or "").strip():
                empty += 1
                thought += bool(resp.get("reasoning_chars"))
                per_case[path.stem] = per_case.get(path.stem, 0) + 1
    print(f"{run.name}: {empty} empty replies of {total} agent calls ({empty / max(1, total):.0%}); {thought} of them after reasoning")
    print("  by case:", ", ".join(f"{k}×{v}" for k, v in per_case.items()) or "none")
    return 0


if __name__ == "__main__":
    sys.exit(main())
