"""Environment-discovery probe with the real model in Chat (campaign round 3, 25 Nov 2026).

Asks questions about this computer that have a factual answer on the machine (VPN, installed apps), each in its
own isolated conversation, and records the tools used and the answer. ECHO_ROOT picks the checkout to run, so the
same probe measures main and a branch.

    ECHO_ROOT=<checkout> apps/terminal/.venv/bin/python apps/terminal/tests/envprobe.py --runs 2 --out FILE
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import sizelab as lab  # noqa: E402

from echospeak_cli.client import new_request_id, submit  # noqa: E402

if os.environ.get("ECHO_ROOT"):
    lab.ROOT = Path(os.environ["ECHO_ROOT"])

QUESTIONS = {
    "vpn": "Am I connected through a VPN right now?",
    "app": "Do I have LibreOffice installed on this computer?",
    "wifi": "Is this computer on Wi-Fi or a wired connection at the moment?",
}


def ask(client, text: str) -> dict:
    thread = client.create_thread("probe")["thread_id"]
    reply, tools = [], []
    for event in submit(client, thread, text, new_request_id()):
        kind = event.get("type")
        if kind == "approval_request" and event.get("id") and not event.get("question"):
            client.post(f"/lean/approvals/{event['id']}", {"decision": "deny"})   # nothing outside chat
            tools.append(("approval_denied", False, str(event.get("tool") or event.get("name") or "")))
        if kind == "agent_token":
            reply.append(str(event.get("data") or ""))
        if kind == "tool_end":
            tools.append((event.get("name"), bool(event.get("ok")), str(event.get("summary") or "")[:120]))
        if kind in {"final", "error"}:
            break
    return {"reply": "".join(reply).strip(), "tools": tools}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3.5:9b")
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    out = {"root": str(lab.ROOT), "model": args.model, "results": []}
    with tempfile.TemporaryDirectory(prefix="echoenv-") as tmp:
        env = {"HOME": str(Path(tmp) / "home"), "LOCAL_MODEL_PROVIDER": "ollama",
               "LOCAL_MODEL_URL": "http://127.0.0.1:11434", "LOCAL_MODEL_NAME": args.model,
               "LEAN_APPROVAL_MODE": "smart", "ASSISTANT_MODE": "unified"}
        proc, _dirs, client = lab.start_backend(Path(tmp), "http://127.0.0.1:11434", env)
        client.timeout = 600
        client.stream_idle = 600
        try:
            for run in range(args.runs):
                for key, text in QUESTIONS.items():
                    out["results"].append({"run": run, "case": key, **ask(client, text)})
        finally:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=30)
            except Exception:  # noqa: BLE001
                proc.kill()
    text = json.dumps(out, indent=1)
    if args.out:
        Path(args.out).write_text(text)
    for r in out["results"]:
        print(r["run"], r["case"], [t[0] for t in r["tools"]], "|", r["reply"][:240].replace("\n", " "))
    return 0


if __name__ == "__main__":
    sys.exit(main())
