"""Run the replay cases (tests/replays/*.json) against your real model before a release.

Each case sends the user's message from a chat that once went wrong to the model
you have configured (or --provider / --model), with the recorded tool outputs
played back, and checks tool choice and the reply against the case's
expectations. No internet is used by the tools; the model call is real.

    python scripts/replay_eval.py
    python scripts/replay_eval.py --provider lmstudio --model qwen3.5-9b --repeat 3
    python scripts/replay_eval.py --only search-broken-ai-news

Exits 1 if any case fails, so it can gate a release.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv: list[str] | None = None) -> int:
    from agent.lean import replays

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--provider", default="", help="Model provider (default: the one in Settings)")
    parser.add_argument("--model", default="", help="Model id (default: the one in Settings)")
    parser.add_argument("--repeat", type=int, default=1, help="Runs per case; a case passes only if every run passes")
    parser.add_argument("--only", default="", help="Comma-separated case names")
    args = parser.parse_args(argv)

    wanted = {name.strip() for name in args.only.split(",") if name.strip()}
    cases = [case for case in replays.load_cases() if not wanted or case.name in wanted]
    if not cases:
        print("No replay cases found.")
        return 1
    client = replays.live_client(args.provider, args.model)
    print(f"Model: {client.endpoint.provider} / {client.endpoint.model}  ·  {len(cases)} case(s) × {args.repeat}")
    failed = 0
    for case in cases:
        problems_by_run = []
        for _ in range(max(1, args.repeat)):
            outcome = replays.run_case(case, client)
            problems_by_run.append((outcome, replays.score(case, outcome)))
        passed = sum(1 for _, problems in problems_by_run if not problems)
        ok = passed == len(problems_by_run)
        failed += 0 if ok else 1
        print(f"\n{'PASS' if ok else 'FAIL'}  {case.name}  ({passed}/{len(problems_by_run)} runs)")
        for outcome, problems in problems_by_run:
            if problems:
                print(f"   tools: {outcome.tools or 'none'}")
                for problem in problems:
                    print(f"   - {problem}")
                print(f"   reply: {outcome.reply[:240]!r}")
    print(f"\n{len(cases) - failed}/{len(cases)} cases passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
