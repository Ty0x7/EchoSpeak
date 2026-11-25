"""MCP lab: the whole connection lifecycle with the real model, in an isolated backend (campaign round 3, 25 Nov 2026).

1. Turn 1, the owner's words: work with the notes folder through an MCP server; find one and set it up.
   Expected: Echo searches the registry (find_integrations) and suggests one (propose_integration). It can't
   install or approve anything itself.
2. The simulated owner approves the suggestion through the endpoint Settings uses, filling a required
   folder/path setting with the notes folder (the server is installed and started there).
3. Turn 2: "approved, now list the notes using it". Expected: Echo checks the connection and calls the server's tool.
The judgement is from evidence: tool calls, the proposal and server state, and whether the answer names the real
files. Network: the public MCP registry, and the server package the proposal launches.

    apps/terminal/.venv/bin/python apps/terminal/tests/mcplab.py [--model qwen3.5:9b] [--out FILE]
"""
from __future__ import annotations

import argparse
import json
import re
import signal
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import sizelab as lab  # noqa: E402

from echospeak_cli.client import new_request_id, submit  # noqa: E402

NOTES = {"groceries.md": "- milk\n- eggs\n", "trip-ideas.md": "Lisbon in spring\n", "reading-list.md": "Dune\n"}
TURN1 = ("I'd like you to be able to work with files in my notes folder ({notes}) through an MCP server. "
         "Find a suitable one and set it up.")
TURN1_INSIST = ("I'd like to connect an MCP filesystem server for my notes folder ({notes}), specifically an MCP "
                "connection rather than your built-in file access, so it works the same in every chat. Find one and set "
                "it up for me.")
TURN1_QUESTION = ("Is there an MCP server that could let you work with my notes folder ({notes})? Just curious what "
                  "the options are; don't set anything up yet.")
TURN2 = "Done, I approved it in Settings. Now list the notes in that folder using it."


OWNER_ANSWER = "Yes, please set up the best option for me."
QUESTIONS: list = []  # every ask_user question Echo asked
TIMELINE: list = []   # (seconds, event type, short detail) for every streamed event, kept even if a turn fails


def run_turn(client, thread: str, text: str, events: list) -> dict:
    mark = len(events)
    reply = []
    t0 = time.time()
    TIMELINE.append((0.0, "turn_start", text[:60]))
    for event in submit(client, thread, text, new_request_id()):
        events.append(event)
        kind = event.get("type")
        TIMELINE.append((round(time.time() - t0, 1), kind, str(event.get("name") or event.get("data") or "")[:60]))
        if kind == "approval_request" and event.get("id"):
            if event.get("question"):   # ask_user: the owner says go ahead (counted as a question asked)
                QUESTIONS.append(str(event.get("question"))[:200])
                client.post(f"/lean/approvals/{event['id']}", {"decision": OWNER_ANSWER})
            else:
                client.post(f"/lean/approvals/{event['id']}", {"decision": "allow"})
        if kind == "agent_token":
            reply.append(str(event.get("data") or ""))
        if kind in {"final", "error"}:
            break
    return _summary(events, mark, reply)


def _summary(events: list, mark: int, reply: list) -> dict:
    tools = [(e.get("name"), bool(e.get("ok")), str(e.get("summary") or e.get("output") or "")[:160])
             for e in events[mark:] if e.get("type") == "tool_end"]
    return {"reply": "".join(reply).strip(), "tools": tools}


def approve(client, notes: Path) -> dict:
    waiting = (client.get("/lean/integrations/proposals") or {}).get("items") or []
    if not waiting:
        return {"approved": False, "why": "no suggestion was waiting"}
    proposal = waiting[0]
    values = {}
    for name, spec in (proposal.get("settings") or {}).items():
        hint = f"{name} {spec.get('description') or ''}".lower()
        if spec.get("required") or re.search(r"\b(dir|directory|folder|path|root)\b", hint):
            values[name] = str(notes) if re.search(r"dir|folder|path|root", hint) else str(spec.get("default") or "")
    result = client.post(f"/lean/integrations/proposals/{proposal['id']}/approve", {"values": values})
    return {"approved": bool(result.get("ok")), "proposal": {k: proposal.get(k) for k in ("server_name", "candidate_id",
            "title", "launch")}, "values": values, "server": result.get("server")}


def _backend(tmp: str, model: str):
    notes = Path(tmp) / "home" / "notes"
    notes.mkdir(parents=True)
    for name, text in NOTES.items():
        (notes / name).write_text(text)
    env = {"HOME": str(Path(tmp) / "home"), "LOCAL_MODEL_PROVIDER": "ollama",
           "LOCAL_MODEL_URL": "http://127.0.0.1:11434", "LOCAL_MODEL_NAME": model,
           "LEAN_APPROVAL_MODE": "smart", "ASSISTANT_MODE": "unified"}
    proc, _dirs, client = lab.start_backend(Path(tmp), "http://127.0.0.1:11434", env)
    client.timeout = 900
    client.stream_idle = 600
    return proc, client, notes


def _stop(proc, tmp: str, log_to: str) -> None:
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=30)
    except Exception:  # noqa: BLE001
        proc.kill()
    if log_to:
        import shutil
        shutil.copy(Path(tmp) / "backend.log", log_to)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3.5:9b")
    ap.add_argument("--out", default="")
    ap.add_argument("--insist", action="store_true", help="the owner asks for an MCP connection specifically")
    ap.add_argument("--question", action="store_true",
                    help="negative control: the owner only asks what exists, so nothing should be suggested")
    ap.add_argument("--turn1-only", action="store_true",
                    help="stop after the suggestion: nothing is approved, so no package is downloaded or started")
    ap.add_argument("--runs", type=int, default=1,
                    help="with --turn1-only: runs, each with a fresh backend and data (a dismissed suggestion would "
                         "otherwise read as the owner's rejection in the next run)")
    args = ap.parse_args()
    out: dict = {"model": args.model, "insist": args.insist}
    request = TURN1_QUESTION if args.question else TURN1_INSIST if args.insist else TURN1
    out["question"] = args.question
    log = lambda i: str(Path(args.out).with_suffix(f".backend{i}.log")) if args.out else ""  # noqa: E731
    if args.turn1_only:
        out["runs"] = []
        for i in range(args.runs):
            with tempfile.TemporaryDirectory(prefix="echomcp-") as tmp:
                proc, client, notes = _backend(tmp, args.model)
                try:
                    thread = client.create_thread("MCP lab")["thread_id"]
                    asked_before = len(QUESTIONS)
                    turn = run_turn(client, thread, request.format(notes=notes), [])
                    turn["asked"] = QUESTIONS[asked_before:]
                    waiting = (client.get("/lean/integrations/proposals") or {}).get("items") or []
                    turn["proposed"] = [p.get("source_id") for p in waiting]
                    out["runs"].append(turn)
                except Exception as exc:  # noqa: BLE001  keep what ran
                    out["runs"].append({"error": repr(exc)})
                finally:
                    _stop(proc, tmp, log(i))
    else:
        with tempfile.TemporaryDirectory(prefix="echomcp-") as tmp:
            proc, client, notes = _backend(tmp, args.model)
            try:
                thread = client.create_thread("MCP lab")["thread_id"]
                events: list = []
                started = time.time()
                out["turn1"] = run_turn(client, thread, request.format(notes=notes), events)
                out["owner_approval"] = approve(client, notes)
                time.sleep(3)
                out["turn2"] = run_turn(client, thread, TURN2, events)
                out["seconds"] = round(time.time() - started, 1)
                names1 = [t[0] for t in out["turn1"]["tools"]]
                names2 = [t[0] for t in out["turn2"]["tools"]]
                mcp_calls = [t for t in out["turn2"]["tools"] if t[0] and t[0].startswith("mcp__")]
                out["checks"] = {
                    "searched the registry": "find_integrations" in names1,
                    "suggested a connection": "propose_integration" in names1,
                    "owner approval started the server": bool((out["owner_approval"].get("server") or {}).get("running")),
                    "used the server's tool": any(ok for _, ok, _ in mcp_calls),
                    "answer names the real notes": all(n in out["turn2"]["reply"] for n in NOTES),
                    "didn't fall back to its own file tools": not any(n in {"file_list", "file_read", "terminal"} for n in names2),
                }
            except Exception as exc:  # noqa: BLE001  keep what ran
                out["error"] = repr(exc)
            finally:
                _stop(proc, tmp, log(0))
    out["timeline"] = TIMELINE
    text = json.dumps(out, indent=1, default=str)
    if args.out:
        Path(args.out).write_text(text)
    print(text[-6000:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
