"""Real-model check of the look-up budget's last answer call (owner report, 19 November 2026).

The recorded failure: after six look-up steps, the one tool-free "answer now" call came back with the whole
answer written as reasoning and no reply, and the owner got "I ran out of steps (60)". This sends that call to the
local model (no paid API) the way LeanTurn does, with thinking on (before) and off (now), and counts empty replies.

    .venv/bin/python scripts/grace_probe.py --trials 3
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.lean.chat_run import GRACE_PROMPT  # noqa: E402
from agent.lean.loop import add_note  # noqa: E402
from agent.lean.provider import ChatClient, Endpoint  # noqa: E402

PAGES = [
    ("web_search", "major world news November 2026", "1. US and China leaders meet for a trilateral summit (euronews.com) 2. "
     "Cyberattack hits European grocery chains' refrigeration systems (bbc.com) 3. AI model releases this week (theverge.com)"),
    ("safe_web_fetch", "https://www.euronews.com/2026/11/19/summit", "The leaders of the United States and China met on "
     "Wednesday for talks on trade and export controls, agreeing to resume a working group on chips. " * 6),
    ("safe_web_fetch", "https://www.bbc.com/news/articles/grocery", "A ransomware attack disrupted refrigeration "
     "monitoring at several European supermarket chains, forcing stores to discard chilled stock. " * 6),
    ("web_search", "tech AI developments November 2026", "1. New open-weight model tops coding benchmark 2. EU AI Act "
     "obligations for general-purpose models take effect 3. Chipmakers report record AI demand"),
]


def messages() -> list[dict]:
    out: list[dict] = [
        {"role": "system", "content": "You are Echo, the owner's personal AI assistant, running on their computer. "
                                      "Answer in the user's language. Cite pages you read with inline links."},
        {"role": "user", "content": "who am i talking to right now give me the latest updates on the world"},
    ]
    for i, (tool, arg, result) in enumerate(PAGES):
        key = "query" if tool == "web_search" else "url"
        out.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": f"c{i}", "type": "function", "function": {"name": tool, "arguments": json.dumps({key: arg})}}]})
        out.append({"role": "tool", "tool_call_id": f"c{i}", "name": tool, "content": result})
    add_note(out, GRACE_PROMPT.removeprefix("[EchoSpeak]: "))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3.5:9b")
    ap.add_argument("--url", default="http://127.0.0.1:11434/v1")
    ap.add_argument("--trials", type=int, default=3)
    args = ap.parse_args()
    endpoint = Endpoint(provider="ollama", model=args.model, base_url=args.url, api_key="", local=True)
    results = {}
    for label, effort in (("thinking on (before)", "medium"), ("thinking off (now)", "none")):
        rows = []
        for _ in range(args.trials):
            client = ChatClient(endpoint, reasoning_effort=effort)
            started = time.time()
            turn = client.stream_turn(messages(), tools=[])
            rows.append({"reply_chars": len(turn.content or ""), "reasoning_chars": len(turn.reasoning or ""),
                         "seconds": round(time.time() - started, 1)})
        results[label] = rows
        empty = sum(1 for r in rows if not r["reply_chars"])
        print(f"{label:22} empty replies {empty}/{len(rows)}  " + "  ".join(
            f"[reply {r['reply_chars']} · reasoning {r['reasoning_chars']} · {r['seconds']}s]" for r in rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
