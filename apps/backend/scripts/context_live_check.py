"""Live checks for the chosen context window on a real local Ollama (PR #193 revision, 25 November 2026).

Each step runs in its own process (``--step``), so nothing cached in one process helps the next: a "restarted
backend" really is a fresh process. Variants this creates are named echospeak/<model>:<N>k and are removed with
``--step cleanup``.

    .venv/bin/python scripts/context_live_check.py --step load --model qwen3.5:9b --tokens 32768
    .venv/bin/python scripts/context_live_check.py --step report --model qwen3.5:9b --tokens 32768
    .venv/bin/python scripts/context_live_check.py --step needle --model qwen3.5:9b --tokens 32768 --prompt-tokens 24000
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import config  # noqa: E402


def setup(model: str, tokens: int) -> None:
    config.local.provider = "ollama"
    config.local.base_url = "http://127.0.0.1:11434"
    config.local.model_name = model
    config.local.context_length = tokens


def ps() -> list:
    rows = json.load(urllib.request.urlopen("http://127.0.0.1:11434/api/ps")).get("models") or []
    return [(r["name"], r.get("context_length"), round(r.get("size_vram", 0) / 2**30, 2), round(r.get("size", 0) / 2**30, 2))
            for r in rows]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", required=True, choices=["load", "report", "needle", "show", "cleanup"])
    ap.add_argument("--model", default="qwen3.5:9b")
    ap.add_argument("--tokens", type=int, default=32768)
    ap.add_argument("--prompt-tokens", type=int, default=24000)
    ap.add_argument("--base", action="store_true", help="send to the base model (the server's own window), not a variant")
    args = ap.parse_args()
    setup(args.model, 0 if args.base else args.tokens)
    from agent import context_windows
    from agent.lean import window
    from agent.lean.provider import ChatClient, resolve_endpoint

    if args.step == "show":
        print(json.dumps({"trained": context_windows.trained_max("http://127.0.0.1:11434", args.model),
                          "choices": [c["label"] for c in context_windows.choices("http://127.0.0.1:11434", args.model)]}))
        return 0
    if args.step == "cleanup":
        import subprocess
        names = [line.split()[0] for line in subprocess.run(["ollama", "list"], capture_output=True, text=True).stdout.splitlines()[1:]
                 if line.startswith(context_windows.VARIANT_PREFIX)]
        for name in names:
            urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:11434/api/generate",
                                                          data=json.dumps({"model": name, "keep_alive": 0}).encode()))
            subprocess.run(["ollama", "rm", name], capture_output=True)
        print("removed", names)
        return 0
    endpoint = resolve_endpoint("ollama")
    if args.step == "report":
        import asyncio
        from api.routes.settings import context_windows_info
        info = asyncio.run(context_windows_info())
        print(json.dumps({k: info[k] for k in ("configured", "requested", "served", "state", "served_as", "loaded")}))
        return 0
    if args.step == "load":
        started = time.time()
        reply = ChatClient(endpoint, reasoning_effort="none").stream_turn([{"role": "user", "content": "Reply with OK."}],
                                                                        max_tokens=8)
        window._served.clear()
        print(json.dumps({"wire": endpoint.wire_model, "reply": reply.content.strip(), "seconds": round(time.time() - started, 1),
                          "served": window.served(endpoint), "ps": ps()}))
        return 0
    # needle: a fact near the start, then filler, then the question. A cut prompt loses the start.
    filler = ("The maintenance log records routine checks of the cooling loop, the backup power and the access doors. "
              "Each entry notes the time, the inspector and whether anything needed attention. ")
    per = len(filler) // 4
    body = "Remember this: the vault code is 7319.\n\n" + filler * max(1, (args.prompt_tokens - 200) // per)
    question = "\n\nWhat is the vault code? Answer with just the number."
    started = time.time()
    reply = ChatClient(endpoint, reasoning_effort="none").stream_turn([{"role": "user", "content": body + question}],
                                                                    max_tokens=16)
    usage = getattr(reply, "usage", None) or {}
    window._served.clear()
    print(json.dumps({"wire": endpoint.wire_model, "sent_tokens_est": len(body + question) // 4,
                      "server_prompt_tokens": (usage or {}).get("prompt_tokens") if isinstance(usage, dict) else None,
                      "answer": reply.content.strip()[:40], "found": "7319" in (reply.content or ""),
                      "seconds": round(time.time() - started, 1), "served": window.served(endpoint), "ps": ps()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
