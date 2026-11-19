"""How much of each model request a single-slot model server could reuse from the request before it.

llama.cpp (and Ollama with OLLAMA_NUM_PARALLEL=1) keeps one cached prompt: the last one processed, whatever
conversation or side call it came from. A request costs prefill only for what differs from that prompt. This reads a
harness_eval run's model-calls.jsonl (scripts/model_recorder.py) and reports, for every call, the share of it that
matches the previous call from the start, where the first difference falls (system prompt section, tools, which
message), and the first-chunk time when the recorder captured it (Ollama streams nothing until prefill is done).

    apps/backend/.venv/bin/python apps/backend/scripts/prefix_reuse.py RUN_DIR [--json]

Order follows Qwen's template: system text, then the tool definitions, then the messages in order.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path


def parts(request: dict) -> list[tuple[str, str]]:
    messages = list(request.get("messages") or [])
    out: list[tuple[str, str]] = []
    if messages and messages[0].get("role") == "system":
        system = str(messages[0].get("content") or "")
        # Named sections ("## What you know about the user"), so a break can be attributed.
        pieces = re.split(r"(?m)^(?=## )", system)
        for piece in pieces:
            title = piece.split("\n", 1)[0].strip("# ").strip()[:48] if piece.startswith("## ") else "system (start)"
            out.append((f"system: {title}", piece))
        messages = messages[1:]
    out.append(("tools", json.dumps(request.get("tools") or [], sort_keys=True)))
    for i, m in enumerate(messages):
        body = str(m.get("content") or "") + json.dumps(m.get("tool_calls") or [], sort_keys=True)
        out.append((f"message {i + 1} ({m.get('role')})", f"<{m.get('role')}>" + body))
    return out


def compare(prev: dict, cur: dict) -> dict:
    a, b = parts(prev), parts(cur)
    total = sum(len(t) for _n, t in b)
    shared = 0
    broke = "identical through the previous request"
    for (na, ta), (nb, tb) in zip(a, b):
        if ta == tb and na == nb:
            shared += len(tb)
            continue
        common = next((i for i, (x, y) in enumerate(zip(ta, tb)) if x != y), min(len(ta), len(tb)))
        shared += common
        broke = nb
        break
    return {"chars": total, "shared": shared, "share": round(shared / total, 3) if total else 0.0, "broke_at": broke}


def kind(request: dict) -> str:
    return "main" if request.get("tools") else "side"


def server(row: dict) -> dict:
    """llama.cpp's own account (Ollama's OpenAI endpoint passes it on): prompt_n is the tokens it actually processed."""
    response = row.get("response") or {}
    usage, timings = response.get("usage") or {}, response.get("timings") or {}
    if isinstance(usage, str) or isinstance(timings, str) or not usage.get("prompt_tokens") or "prompt_n" not in timings:
        return {}
    return {"prompt_tokens": usage["prompt_tokens"], "processed": timings["prompt_n"], "prompt_ms": timings.get("prompt_ms", 0)}


def analyse(run_dir: Path) -> dict:
    rows = [json.loads(line) for line in (run_dir / "model-calls.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    rows.sort(key=lambda r: r["started"])
    report = []
    for prev, cur in zip(rows, rows[1:]):
        c = compare(prev["request"], cur["request"])
        c.update({"seq": cur.get("seq"), "kind": kind(cur["request"]), "after": kind(prev["request"]),
                  "first_chunk_s": cur.get("first_chunk_s"), "latency_s": cur.get("latency_s"), **server(cur)})
        report.append(c)
    main = [r for r in report if r["kind"] == "main"]
    breaks: dict[str, int] = {}
    for r in main:
        key = re.sub(r"message \d+", "message n", r["broke_at"])
        breaks[key] = breaks.get(key, 0) + 1
    summary = {
        "calls": len(rows), "main_calls": len(main), "side_calls": sum(1 for r in rows if kind(r["request"]) == "side"),
        "main_mean_reused_share": round(statistics.mean(r["share"] for r in main), 3) if main else None,
        "main_reused_chars": sum(r["shared"] for r in main), "main_total_chars": sum(r["chars"] for r in main),
        "main_after_a_side_call": sum(1 for r in main if r["after"] == "side"),
        "first_break_by_place": dict(sorted(breaks.items(), key=lambda kv: -kv[1])),
    }
    served = [r for r in main if r.get("prompt_tokens")]
    if served:
        # What the model server says it did: prompt tokens it processed versus those it took from its cache.
        total = sum(r["prompt_tokens"] for r in served)
        summary["server_prompt_tokens"] = total
        summary["server_processed_tokens"] = sum(r["processed"] for r in served)
        summary["server_cached_share"] = round(1 - summary["server_processed_tokens"] / total, 3) if total else None
        summary["server_prefill_ms_mean"] = round(statistics.mean(r["prompt_ms"] for r in served))
        summary["server_prefill_ms_median"] = round(statistics.median(r["prompt_ms"] for r in served))
        summary["server_prefill_ms_total"] = round(sum(r["prompt_ms"] for r in served))
        summary["calls_mostly_reprocessed"] = sum(1 for r in served if r["processed"] > 0.5 * r["prompt_tokens"])
    timed = [r for r in main if r["first_chunk_s"] is not None]
    if timed:
        summary["main_first_chunk_s_mean"] = round(statistics.mean(r["first_chunk_s"] for r in timed), 2)
        summary["main_first_chunk_s_median"] = round(statistics.median(r["first_chunk_s"] for r in timed), 2)
    return {"summary": summary, "calls": report}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    result = analyse(Path(args.run_dir))
    if args.json:
        print(json.dumps(result, indent=1))
    else:
        for key, value in result["summary"].items():
            print(f"{key:28} {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
