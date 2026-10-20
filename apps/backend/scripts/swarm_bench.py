"""Research swarm bench: one agent vs. a lead with read-only workers, on the same tasks and model.

Answers one question with numbers before the swarm is switched on by default for
anyone: with *this* model, do research workers give better answers per token, or
just spend more? It drives the running app like you do (POST /query/stream):

    python scripts/swarm_bench.py --api http://127.0.0.1:8000                  # every task, single then swarm
    python scripts/swarm_bench.py --only parallel --repeat 2
    python scripts/swarm_bench.py --report                                      # summarize saved runs

For each task it runs one agent (research workers off), then the swarm with the
workers' shared budget set to the tokens the single agent used, so the swarm
can't win by spending more (Tran & Kiela 2026: many multi-agent gains are
unmatched compute). Synthesis tokens are counted on top and reported.

Scored in code, never by the model under test:
- correct: every required fact group appears in the reply (tasks below use
  stable, checkable facts);
- sources: distinct links cited in the reply;
- latency, new tokens (lead + workers), failures, and whether workers ran.

Results go to data/evals/swarm/; the model, settings and API are recorded with
each run. Approvals are allowed automatically (research tasks shouldn't ask).
The setting is restored afterwards. Needs only the standard library.

Task kinds: "parallel" (independent lookups: where workers should help),
"sequential" (each step needs the last: where they shouldn't), "conflict"
(sources disagree or are often misquoted: does the answer say so?).
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_gemma import Api  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "evals" / "swarm"

# (id, kind, prompt, required fact groups: each group passes when any of its variants appears)
TASKS: list[tuple[str, str, str, list[list[str]]]] = [
    ("p1", "parallel", "Compare the heights of Mount Everest, K2 and Kangchenjunga in metres. Cite a source for each.",
     [["8848", "8,848", "8849", "8,849"], ["8611", "8,611"], ["8586", "8,586"]]),
    ("p2", "parallel", "When did each of these first fly or launch: the Wright Flyer, Sputnik 1, and the Boeing 747? Cite sources.",
     [["1903"], ["1957"], ["1969"]]),
    ("p3", "parallel", "What are the capitals of Australia, Canada and Brazil, and when did each become the capital? Cite sources.",
     [["Canberra"], ["Ottawa"], ["Brasília", "Brasilia"], ["1960"]]),
    ("p4", "parallel", "Give the year each of these programming languages first appeared: Python, Java, JavaScript and Rust. Cite sources.",
     [["1991"], ["1995"], ["2010", "2015"]]),
    ("p5", "parallel", "Who wrote these novels and in what year were they first published: 1984, Brave New World, Fahrenheit 451? Cite sources.",
     [["Orwell"], ["Huxley"], ["Bradbury"], ["1949"], ["1932"], ["1953"]]),
    ("p6", "parallel", "Compare the lengths of the Nile, the Amazon and the Yangtze in kilometres, with a source for each.",
     [["6650", "6,650", "6695", "6,695"], ["6400", "6,400", "6992", "6,992", "7062", "7,062"], ["6300", "6,300", "6380", "6,380"]]),
    ("p7", "parallel", "What were the host cities of the 2008, 2012 and 2016 Summer Olympics? Cite a source for each.",
     [["Beijing"], ["London"], ["Rio"]]),
    ("p8", "parallel", "Find the atomic numbers of gold, silver, copper and platinum, citing a source.",
     [["79"], ["47"], ["29"], ["78"]]),
    ("p9", "parallel", "Who were the first three people to walk on the Moon, in order? Cite sources.",
     [["Armstrong"], ["Aldrin"], ["Conrad"]]),
    ("p10", "parallel", "Compare the founding years of Microsoft, Apple and Google, with a source for each.",
     [["1975"], ["1976"], ["1998"]]),
    ("s1", "sequential", "Which country hosted the 2014 FIFA World Cup, and what is the capital of that country? Cite sources.",
     [["Brazil"], ["Brasília", "Brasilia"]]),
    ("s2", "sequential", "Who directed the film that won Best Picture at the 2020 Academy Awards, and what was that director's next feature film? Cite sources.",
     [["Bong Joon"], ["Mickey 17"]]),
    ("s3", "sequential", "Find the year the Eiffel Tower was completed, then say who was President of the United States in that year. Cite sources.",
     [["1889"], ["Harrison", "Cleveland"]]),
    ("s4", "sequential", "What element has atomic number 26, and what is its melting point in degrees Celsius? Cite a source.",
     [["iron", "Iron"], ["1538", "1,538"]]),
    ("s5", "sequential", "Who wrote 'Pride and Prejudice', and in which year did that author die? Cite sources.",
     [["Austen"], ["1817"]]),
    ("c1", "conflict", "Is the Great Wall of China visible to the naked eye from low Earth orbit? Say what sources disagree on and cite them.",
     [["not", "no", "isn't", "cannot", "can't", "myth"]]),
    ("c2", "conflict", "Which is longer, the Nile or the Amazon? Explain why sources disagree and cite them.",
     [["disagree", "debate", "dispute", "depends", "measure"], ["Nile"], ["Amazon"]]),
    ("c3", "conflict", "What is the official height of Mount Everest, and why did it change in 2020? Cite sources.",
     [["8848.86", "8,848.86"], ["China", "Nepal"]]),
    ("c4", "conflict", "Do humans use only 10% of their brains? Check what sources say and cite them.",
     [["myth", "false", "not true", "no"]]),
    ("c5", "conflict", "How many moons does Saturn have according to the latest counts, and why do the numbers keep changing? Cite sources.",
     [["14", "146", "274", "145"], ["discover", "confirmed", "count"]]),
]

LINK = re.compile(r"https?://[^\s)\]>\"']+")


def put(api: Api, patch: dict[str, Any]) -> None:
    api.call("PUT", "/settings", patch, timeout=30)


def run_one(api: Api, prompt: str, timeout: float) -> dict[str, Any]:
    thread = api.call("POST", "/threads", {"title": f"Swarm bench {uuid.uuid4().hex[:6]}", "source": "web"})
    out: dict[str, Any] = {"text": "", "tools": [], "error": "", "lead_tokens": 0, "worker_tokens": 0, "workers": [], "failures": 0}

    def on_event(event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "tool_start":
            out["tools"].append(str(event.get("name")))
        elif kind == "tool_end" and not event.get("ok", True):
            out["failures"] += 1
        elif kind == "tool_state" and isinstance(event.get("state"), dict) and event["state"].get("kind") == "workers":
            out["worker_tokens"] = int(event["state"].get("used") or 0)
            out["workers"] = [w.get("status") for w in event["state"].get("workers") or []]
        elif kind == "agent_done":
            out["lead_tokens"] += int((event.get("usage") or {}).get("fresh") or 0)
        elif kind == "approval_request":
            approval = str(event.get("id"))
            threading.Thread(target=lambda: api.call("POST", f"/lean/approvals/{approval}", {"decision": "allow"}),
                             daemon=True).start()
        elif kind == "final":
            out["text"] = str(event.get("response") or "")
            if not event.get("success"):
                out["error"] = out["error"] or "turn reported failure"
        elif kind == "error":
            out["error"] = str(event.get("message") or "error")

    started = time.time()
    try:
        api.stream({"message": prompt, "thread_id": thread["thread_id"], "client_request_id": f"swarm-{uuid.uuid4().hex[:10]}"},
                   on_event, timeout=timeout)
    except Exception as exc:  # one failed request is a data point, not the end of the run
        out["error"] = f"request failed: {exc}"
    out["seconds"] = round(time.time() - started, 1)
    out["tokens"] = out["lead_tokens"] + out["worker_tokens"]
    return out


def score(text: str, groups: list[list[str]]) -> dict[str, Any]:
    low = text.lower()
    hits = [any(v.lower() in low for v in group) for group in groups]
    return {"correct": all(hits), "facts": f"{sum(hits)}/{len(groups)}", "sources": len(set(LINK.findall(text)))}


def summarize(rows: list[dict[str, Any]]) -> str:
    lines = ["| Kind | Arm | Runs | Correct | Sources (median) | Seconds (median) | Tokens (median) | Failed runs | Workers used |",
             "|---|---|---|---|---|---|---|---|---|"]
    for kind in ("parallel", "sequential", "conflict", "all"):
        for arm in ("single", "swarm"):
            sel = [r for r in rows if r["arm"] == arm and (kind == "all" or r["kind"] == kind)]
            if not sel:
                continue
            med = lambda key: statistics.median([r[key] for r in sel])  # noqa: E731
            lines.append(f"| {kind} | {arm} | {len(sel)} | {sum(r['correct'] for r in sel)}/{len(sel)} | {med('sources'):g} | "
                         f"{med('seconds'):g} | {med('tokens'):,.0f} | {sum(bool(r['error']) for r in sel)} | "
                         f"{sum(bool(r['workers']) for r in sel)} |")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--token", default="")
    parser.add_argument("--only", default="", help="Task ids or kinds, e.g. p1,p2 or parallel")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--report", action="store_true", help="Summarize the saved runs and exit")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if args.report:
        runs = sorted(OUT.glob("*.json"))
        for path in runs:
            data = json.loads(path.read_text(encoding="utf-8"))
            print(f"\n## {path.stem} · {data['model']} · EchoSpeak {data['version']}\n\n{summarize(data['rows'])}")
        return 0 if runs else 1

    api = Api(args.api, args.token)
    try:
        health, provider, before = api.call("GET", "/health"), api.call("GET", "/provider"), api.call("GET", "/settings")
    except Exception as exc:
        print(f"Backend not reachable at {args.api}: {exc}")
        return 2
    settings_before = (before or {}).get("settings", before or {})
    restore = {k: settings_before.get(k) for k in ("lean_research_swarm", "lean_swarm_token_budget") if k in settings_before}
    model = f"{provider.get('provider')}:{provider.get('model')}"
    wanted = {x.strip() for x in args.only.split(",") if x.strip()}
    tasks = [t for t in TASKS if not wanted or t[0] in wanted or t[1] in wanted]
    print(f"Swarm bench · EchoSpeak {health.get('version')} · {model} · {len(tasks)} tasks × {args.repeat}")
    rows: list[dict[str, Any]] = []
    try:
        for rep in range(args.repeat):
            for task_id, kind, prompt, groups in tasks:
                put(api, {"lean_research_swarm": False})
                single = run_one(api, prompt, args.timeout)
                budget = max(10_000, int(single["tokens"] or 0))  # matched: workers get what one agent spent
                put(api, {"lean_research_swarm": True, "lean_swarm_token_budget": budget})
                swarm = run_one(api, prompt, args.timeout)
                for arm, run in (("single", single), ("swarm", swarm)):
                    row = {"task": task_id, "kind": kind, "arm": arm, "rep": rep, **score(run["text"], groups),
                           **{k: run[k] for k in ("seconds", "tokens", "lead_tokens", "worker_tokens", "workers", "failures", "error")},
                           "budget": budget if arm == "swarm" else None, "reply": run["text"][:4000]}
                    rows.append(row)
                print(f"[{task_id}] single {'✅' if rows[-2]['correct'] else '❌'} {single['tokens']:,}t {single['seconds']}s · "
                      f"swarm {'✅' if rows[-1]['correct'] else '❌'} {swarm['tokens']:,}t {swarm['seconds']}s "
                      f"workers={swarm['workers'] or 'none'}")
    finally:
        put(api, restore or {"lean_research_swarm": False})
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    record = {"version": health.get("version"), "model": model, "at": stamp, "repeat": args.repeat, "rows": rows}
    (OUT / f"{stamp}.json").write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\n" + summarize(rows) + f"\n\nSaved to {OUT / (stamp + '.json')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
