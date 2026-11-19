"""Context-window probe: what 16K, 32K and 64K actually cost on this machine (docs/research/context-strategy.md).

For each window: load the model with that num_ctx (Ollama's native API honours it per request), fill about 85% of
it with varied text that starts with one fact, and ask for the fact. Records the loaded size and GPU share from
/api/ps, prompt (prefill) and generation speed from Ollama's own timings, and whether the fact came back. One
sample per size: a cost measurement, not a quality benchmark. Unloads the model at the end.

    apps/backend/.venv/bin/python apps/backend/scripts/context_probe.py [--model qwen3.5:9b] [--sizes 16384,32768,65536]
"""
from __future__ import annotations

import argparse
import json
import random
import time
import urllib.request

BASE = "http://127.0.0.1:11434"
WORDS = ("river garden copper lantern meadow signal harbor quiet amber orbit cedar velvet canyon ember falcon glacier "
         "harvest island jasmine kettle ladder marble nectar oyster pepper quartz raven saddle timber umbrella valley "
         "walnut yonder zephyr anchor bramble cobalt dune fable grove hollow ivory juniper kelp lilac mosaic").split()


def call(path: str, body: dict, timeout: float = 900) -> dict:
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def ps(model: str) -> dict:
    with urllib.request.urlopen(BASE + "/api/ps", timeout=10) as resp:
        rows = json.loads(resp.read()).get("models") or []
    return next((r for r in rows if r.get("name") == model or r.get("model") == model), {})


def filler(tokens: int, seed: int) -> str:
    rng = random.Random(seed)
    # Numbered sentences keep it from compressing into repeats.
    words = int(tokens / 1.52)          # measured: 1.52 qwen3.5 tokens per word of this filler
    out, n = [], 0
    while len(out) < words:
        n += 1
        out += [f"({n})"] + [rng.choice(WORDS) for _ in range(11)] + ["."]
    return " ".join(out[:words])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3.5:9b")
    ap.add_argument("--sizes", default="16384,32768,65536")
    ap.add_argument("--fill", type=float, default=0.80)
    args = ap.parse_args()
    results = []
    for size in [int(s) for s in args.sizes.split(",")]:
        code = f"{random.Random(size).randint(1000, 9999)}-{random.Random(size + 1).choice(WORDS)}"
        prompt = (f"Remember this access code: {code}.\n\n" + filler(int(size * args.fill), size)
                  + "\n\nWhat was the access code at the very start? Answer with the code only.")
        started = time.monotonic()
        reply = call("/api/generate", {"model": args.model, "prompt": prompt, "stream": False, "think": False,
                                       "options": {"num_ctx": size, "num_predict": 64, "temperature": 0}})
        wall = time.monotonic() - started
        loaded = ps(args.model)
        row = {
            "num_ctx": size,
            "prompt_tokens": reply.get("prompt_eval_count"),
            "prefill_tok_s": round(reply["prompt_eval_count"] / (reply["prompt_eval_duration"] / 1e9), 1)
            if reply.get("prompt_eval_duration") else None,
            "gen_tok_s": round(reply["eval_count"] / (reply["eval_duration"] / 1e9), 1) if reply.get("eval_duration") else None,
            "load_s": round(reply.get("load_duration", 0) / 1e9, 1),
            "wall_s": round(wall, 1),
            "size_gb": round(loaded.get("size", 0) / 1e9, 2),
            "vram_gb": round(loaded.get("size_vram", 0) / 1e9, 2),
            "gpu_share": round(loaded.get("size_vram", 0) / loaded["size"], 3) if loaded.get("size") else None,
            "served_ctx": loaded.get("context_length"),
            "recalled": code in str(reply.get("response") or ""),
            "answer": str(reply.get("response") or "").strip()[:60],
        }
        results.append(row)
        print(json.dumps(row), flush=True)
    call("/api/generate", {"model": args.model, "keep_alive": 0})     # unload: leave the GPU as it was
    print(json.dumps({"unloaded": not ps(args.model)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
