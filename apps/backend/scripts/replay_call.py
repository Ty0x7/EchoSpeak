"""Replay one recorded model call (scripts/model_recorder.py) against the server, under variants, N times each.

For controlled experiments on a single harness decision: the same messages and tools, one thing changed.

    python scripts/replay_call.py <model-calls.jsonl> <seq> --variant as_is --variant no_think --n 5

Variants:
    as_is         the request exactly as recorded
    no_think      reasoning_effort "none" (Ollama turns thinking off)
    note_as_user  an <echospeak-note> appended to the last tool result moved into its own user message
    drop_note     that note removed
Prints, per variant, how many replies had visible content, called a tool, or were empty.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from model_recorder import summarize_stream  # noqa: E402

NOTE = re.compile(r"\n*<echospeak-note>.*?</echospeak-note>\s*$", re.S)


def variant(request: dict, name: str) -> dict:
    body = copy.deepcopy(request)
    last = body["messages"][-1]
    if name == "no_think":
        body["reasoning_effort"] = "none"
    elif name in {"note_as_user", "drop_note"}:
        match = NOTE.search(str(last.get("content") or ""))
        if match:
            last["content"] = str(last["content"])[: match.start()]
            if name == "note_as_user":
                body["messages"].append({"role": "user", "content": match.group(0).strip()})
    return body


def call(base: str, body: dict) -> dict:
    req = urllib.request.Request(base.rstrip("/") + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as response:
        raw = response.read()
    return summarize_stream(raw)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("calls")
    parser.add_argument("seq", type=int)
    parser.add_argument("--variant", action="append", default=[])
    parser.add_argument("--n", type=int, default=3)
    parser.add_argument("--base", default="http://127.0.0.1:11434")
    args = parser.parse_args()
    row = next(json.loads(l) for l in Path(args.calls).read_text().splitlines() if json.loads(l)["seq"] == args.seq)
    for name in args.variant or ["as_is"]:
        tally = {"content": 0, "tool": 0, "empty": 0}
        for i in range(args.n):
            out = call(args.base, variant(row["request"], name))
            kind = "tool" if out["tool_calls"] else "content" if out["content"].strip() else "empty"
            tally[kind] += 1
            print(f"  [{name} {i + 1}] {kind}: {out['content'][:140]!r} tools={[t['name'] for t in out['tool_calls']]} "
                  f"reasoning={out['reasoning_chars']} finish={out['finish_reason']}"
                  + (f"\n      thought: {out['reasoning'][-300:]!r}" if kind == "empty" else ""))
        print(f"{name}: {tally}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
