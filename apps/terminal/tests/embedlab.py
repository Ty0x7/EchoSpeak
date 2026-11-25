"""Embedding lab: does the owner's one-click "Search by meaning" path work? (campaign round 3, 25 November 2026)

Starts an isolated backend (temporary data, scripted model), reads startup readiness, downloads the local
embedding model through POST /embeddings/local/download exactly as the Settings button does, restarts the backend
on the same data folder, and reads readiness again. Network: one pinned model download from Hugging Face.

    apps/terminal/.venv/bin/python apps/terminal/tests/embedlab.py
"""
from __future__ import annotations

import json
import signal
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import sizelab as lab  # noqa: E402
from fake_model import ScriptedModel  # noqa: E402


def degraded(client) -> dict:
    for _ in range(60):
        r = client.get("/startup/readiness") or {}
        if r.get("full_ready") or r.get("completed_steps") == r.get("total_steps"):
            break
        time.sleep(1)
    return {c["key"]: c.get("detail") for c in r.get("components") or [] if c.get("degraded")}


def stop(proc) -> None:
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=30)
    except Exception:  # noqa: BLE001
        proc.kill()


def main() -> int:
    out = {}
    with tempfile.TemporaryDirectory(prefix="echoembed-") as tmp, ScriptedModel([], "ok", delay=0.01) as model:
        env = {"HOME": str(Path(tmp) / "home"), "DOCUMENT_RAG_ENABLED": "true"}
        proc, dirs, client = lab.start_backend(Path(tmp), model.url, env)
        try:
            out["before"] = degraded(client)
            out["status_before"] = client.get("/embeddings/local/status")
            started = time.time()
            client.timeout = 600
            out["download"] = client.post("/embeddings/local/download", {})
            out["download_seconds"] = round(time.time() - started, 1)
        finally:
            stop(proc)
        proc, dirs, client = lab.start_backend(Path(tmp), model.url, env)   # same folders: the model is kept
        try:
            seen = []
            for second in range(0, 1, 10):     # memory starts lazily; poll for 90 s, then try a first request
                seen.append((second, degraded(client)))
                if "embeddings" not in seen[-1][1]:
                    break
                time.sleep(10)
            out["after_poll"] = seen[-3:]
            if "embeddings" in seen[-1][1]:
                client.get("/memory") if False else None
                try:
                    from echospeak_cli.client import new_request_id, submit
                    thread = client.create_thread("embed lab")["thread_id"]
                    for event in submit(client, thread, "hello", new_request_id()):
                        if event.get("type") in {"final", "error"}:
                            break
                except Exception as exc:  # noqa: BLE001
                    out["first_request_error"] = str(exc)[:200]
                time.sleep(20)
                out["after_first_request"] = degraded(client)
            out["documents_endpoint"] = client.get("/documents")
            out["after_documents_call"] = degraded(client)
        finally:
            stop(proc)
    print(json.dumps(out, indent=1, default=str)[:4000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
