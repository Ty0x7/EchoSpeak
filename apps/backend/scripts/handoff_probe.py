"""Real-model check of the step-limit handoff (campaign 24 November 2026, workstreams 2 and 11).

A coding request whose test command keeps failing reaches a small step limit with no progress. The agent the user
talks to then gets one tool-free call to say where things stand (LeanTurn._handoff_answer); a handoff that claims
unmade changes or calls the work done is withdrawn for the plain note. This runs that turn against the local model
(no paid API) and reports, per trial, which text the user got and whether it mentions steps, limits or budgets.

    .venv/bin/python scripts/handoff_probe.py --trials 3
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.lean.loop import LeanTurn  # noqa: E402
from agent.lean.personas import AgentPersona  # noqa: E402
from agent.lean.provider import ChatClient, Endpoint  # noqa: E402
from agent.lean.toolbox import NativeTool, Toolbox  # noqa: E402

SOURCE = {"parser.py": "def parse_week(text):\n    year, week = text.split('-W')\n    return int(year), int(week)\n",
          "tests/test_parser.py": "from parser import parse_week\n\ndef test_w53():\n    assert parse_week('2026-W53 ') == (2026, 53)\n"}
ENGINE = re.compile(r"(?i)\b(step|steps|limit|budget|max_steps|iteration)s?\b")


def toolbox() -> Toolbox:
    box = Toolbox(toolsets=["none"], session_id="probe")
    obj = {"type": "object"}
    box.native = {
        "file_read": NativeTool("file_read", "Read a file in the project.",
                                {**obj, "properties": {"path": {"type": "string"}}, "required": ["path"]},
                                lambda a: SOURCE.get(str(a.get("path") or "").lstrip("./"), "Error: no such file")),
        "file_list": NativeTool("file_list", "List files in the project.", {**obj, "properties": {"path": {"type": "string"}}},
                                lambda a: "parser.py\ntests/\ntests/test_parser.py"),
        "run_tests": NativeTool("run_tests", "Run the project's tests and show the result.",
                                {**obj, "properties": {}},
                                lambda a: "FAILED tests/test_parser.py::test_w53 - ValueError: invalid literal for int() "
                                          "with base 10: '53 '\n1 failed in 0.02s"),
        "save_file": NativeTool("save_file", "Write a file in the project (whole content).",
                                {**obj, "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                                 "required": ["path", "content"]},
                                lambda a: "Error: the project folder is read-only right now (disk is mounted read-only)"),
    }
    return box


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3.5:9b")
    ap.add_argument("--url", default="http://127.0.0.1:11434/v1")
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--steps", type=int, default=3)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    endpoint = Endpoint(provider="ollama", model=args.model, base_url=args.url, api_key="", local=True)
    rows = []
    for trial in range(args.trials):
        events: list[dict] = []
        turn = LeanTurn(client=ChatClient(endpoint), persona=AgentPersona(id="echo", name="Echo"),
                        system_prompt=("You are Echo, the owner's agent, working in their project folder. Use the tools "
                                       "to inspect, run and fix things; check your work by running the tests."),
                        history=[], toolbox=toolbox(), session_id="probe", request_id=f"r{trial}",
                        execution_id=f"e{trial}", emit=events.append, cancel=threading.Event(),
                        persist_tool_runs=False, goal="Run the parser tests and fix any failure")
        turn.max_steps_override = args.steps
        turn.auto_continue = True
        started = time.time()
        result = turn.run("Run the parser tests and fix whatever fails.")
        withdrawn = any(e.get("type") == "claim_nudge" and e.get("claim") == "handoff withdrawn" for e in events)
        handoff = result.stop_reason == "max_steps" and not result.text.startswith("I haven't finished this yet.")
        rows.append({"trial": trial, "stop": result.stop_reason, "handoff_used": handoff, "handoff_withdrawn": withdrawn,
                     "engine_words": sorted({m.group(0).lower() for m in ENGINE.finditer(result.text)}),
                     "seconds": round(time.time() - started, 1), "text": result.text})
        r = rows[-1]
        print(f"trial {trial}: stop={r['stop']} handoff={'used' if handoff else 'note'}"
              f"{' (withdrawn)' if withdrawn else ''} engine_words={r['engine_words']} {r['seconds']}s\n  "
              + r["text"].replace("\n", "\n  ")[:700], flush=True)
    if args.out:
        Path(args.out).write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
