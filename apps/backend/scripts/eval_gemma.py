"""Real prompts (20 core + group-work cases), run against a live EchoSpeak backend and model.

Run it after every change to the agent, with Gemma 4 E4B loaded in LM Studio:

    python scripts/eval_gemma.py                      # backend on 127.0.0.1:8000
    python scripts/eval_gemma.py --api http://127.0.0.1:8765 --only 10-15

Each case sends a message the way the app does (POST /query/stream), records
what happened (reply, tools, agents, handoffs, approvals, time), and checks the
outcome. Cases share chats where a follow-up depends on an earlier turn, use a
throwaway project folder for the coding cases, and create their own group
chats. Approvals are answered automatically: allowed, except the delete case,
which is denied to check that the agent takes no for an answer.

Reports land in data/evals/<timestamp>/ (report.md + report.json), and the
summary line compares the pass count with the previous run.

Standard library only, so it runs from any Python that can reach the backend.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "data" / "evals"
CODENAME = "Bluefinch"


# ── HTTP ─────────────────────────────────────────────────────────────────
class Api:
    def __init__(self, base: str, token: str = "") -> None:
        self.base = base.rstrip("/")
        self.headers = {"Content-Type": "application/json"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    def call(self, method: str, path: str, body: Optional[dict[str, Any]] = None, timeout: float = 30) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method, headers=self.headers)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
        return json.loads(raw) if raw else None

    def stream(self, body: dict[str, Any], on_event: Callable[[dict[str, Any]], None], timeout: float) -> None:
        request = urllib.request.Request(
            self.base + "/query/stream", data=json.dumps(body).encode(), method="POST", headers=self.headers
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            for line in response:
                line = line.strip()
                if line:
                    try:
                        on_event(json.loads(line))
                    except json.JSONDecodeError:
                        continue


# ── cases ────────────────────────────────────────────────────────────────
@dataclass
class Result:
    text: str = ""
    tools: list[str] = field(default_factory=list)
    tool_failures: list[str] = field(default_factory=list)
    agents: list[str] = field(default_factory=list)
    delegations: list[str] = field(default_factory=list)
    approvals: list[str] = field(default_factory=list)
    stop_reasons: list[str] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    outcome: dict[str, Any] = field(default_factory=dict)  # group / handed-off jobs: done or stopped
    continuations: list[str] = field(default_factory=list)
    error: str = ""
    seconds: float = 0.0


Check = tuple[str, Callable[[Result, "Context"], bool]]


@dataclass
class Case:
    id: int
    title: str
    prompt: str
    chat: str  # chats with the same key share history
    checks: list[Check]
    project: bool = False
    room: str = ""  # "", "group", or "discussion"
    deny_approvals: bool = False
    timeout: float = 300


@dataclass
class Context:
    api: Api
    project_dir: Path
    project_id: str = ""
    chats: dict[str, str] = field(default_factory=dict)


def said(*needles: str) -> Check:
    return (f"reply mentions {' / '.join(needles)}",
            lambda r, c: any(n.lower() in r.text.lower() for n in needles))


def used(*tools: str) -> Check:
    return (f"used {' or '.join(tools)}", lambda r, c: any(t in r.tools for t in tools))


def no_tools() -> Check:
    return ("no tools", lambda r, c: not r.tools)


def replied(max_chars: int = 4000) -> Check:
    return (f"replied (≤{max_chars} chars)", lambda r, c: bool(r.text.strip()) and len(r.text) <= max_chars and not r.error)


def file_has(name: str, *needles: str) -> Check:
    def check(r: Result, c: Context) -> bool:
        path = c.project_dir / name
        return path.is_file() and all(n in path.read_text(encoding="utf-8", errors="replace") for n in needles)
    return (f"{name} contains {', '.join(needles) or 'something'}", check)


def finished() -> Check:
    return ("job ended Done", lambda r, c: r.outcome.get("status") == "done")


def agents_spoke(n: int) -> Check:
    return (f"≥{n} agents answered", lambda r, c: len({m.get('agent_id') for m in r.messages if m.get('text')}) >= n)


CASES: list[Case] = [
    Case(1, "Greeting", "hi", "small", [replied(600), no_tools()]),
    Case(2, "Arithmetic", "What's 17% of 2,340?", "small", [replied(), said("397.8")]),
    Case(3, "Current time", "What time is it right now?", "small",
         [replied(), ("gave a time", lambda r, c: bool(re.search(r"\b\d{1,2}:\d{2}", r.text)))]),
    Case(4, "Explain briefly", "Explain the difference between a process and a thread in two sentences.", "small",
         [replied(900), no_tools()]),
    Case(5, "Web search", "Search the web: what is the latest stable release of Python?", "research",
         [replied(), used("web_search", "safe_web_fetch", "browse_task"), said("3.")]),
    Case(6, "Weather", "What's the weather in Edmonton today?", "research",
         [replied(), used("weather_live", "web_search")]),
    Case(7, "Save a memory", "Remember that my favourite code editor is VS Code.", "memory-a",
         [replied(), used("memory_save")]),
    Case(8, "Recall in a new chat", "Which code editor do I like best?", "memory-b",
         [replied(), said("VS Code", "VSCode", "Visual Studio Code")]),
    Case(9, "Plant a fact", f"Quick note for later: the new side project's codename is {CODENAME}. Just say OK.",
         "codename-a", [replied(400)]),
    Case(10, "Search past chats", "In another chat I told you a project codename. Search our past chats and tell me what it was.",
         "codename-b", [replied(), used("chat_search"), said(CODENAME)]),
    Case(11, "Write a file", "Create a file named hello.py that prints 'Hello from EchoSpeak'.", "code",
         [replied(), used("file_write", "artifact_write", "terminal"), file_has("hello.py", "Hello from EchoSpeak")], project=True),
    Case(12, "Read it back", "Read hello.py and tell me exactly what it prints.", "code",
         [replied(), said("Hello from EchoSpeak")], project=True),
    Case(13, "Edit the file", "Add a function add(a, b) to hello.py that returns their sum. Keep the print.", "code",
         [replied(), file_has("hello.py", "def add", "Hello from EchoSpeak")], project=True),
    Case(14, "Run it", "Run hello.py and tell me the output.", "code",
         [replied(), used("terminal", "process_start"), said("Hello from EchoSpeak")], project=True, timeout=900),
    Case(15, "Denied delete", "Delete hello.py.", "code",
         [replied(), ("asked first", lambda r, c: bool(r.approvals)), file_has("hello.py")],
         project=True, deny_approvals=True),
    Case(16, "Summarize the chat", "Summarize what we did in this chat in three bullet points.", "code",
         [replied(), said("hello.py")], project=True),
    Case(17, "Handoff", "Ask Jarvis to find out what year the Eiffel Tower was completed, then tell me.", "handoff",
         [replied(), ("handed to Jarvis", lambda r, c: bool(set(r.delegations) & {"jarvis", "scout"})), said("1889")]),
    Case(18, "Group reply", "Each of you: give me one short tip for staying focused.", "group",
         [replied(), agents_spoke(2)], room="group"),
    Case(19, "Discussion", "Discuss briefly: tabs or spaces for Python? Reach one conclusion.", "discussion",
         [replied(), agents_spoke(2),
          ("stayed within the cap", lambda r, c: len([m for m in r.messages if m.get("text")]) <= 4 + 1)],
         room="discussion", timeout=600),
    Case(20, "Follow the format", "Write a haiku about rain. Only the haiku, nothing else.", "small",
         [replied(400), no_tools(),
          ("three lines", lambda r, c: len([l for l in r.text.strip().splitlines() if l.strip()]) == 3)]),
    # Group work must actually get done, not just promised (the "Sure, I'll do that" bug).
    Case(21, "Group: get it done", "@Echo ask Glados to create team.txt containing the line: hello team. "
         "Make sure it actually gets done.", "group-work",
         [file_has("team.txt", "hello team"), finished()], project=True, room="group", timeout=900),
    Case(22, "Group: research then write", "@Echo get Jarvis to find the year the Eiffel Tower was completed, "
         "then have Glados save just that year to eiffel.txt.", "group-chain",
         [file_has("eiffel.txt", "1889"), finished()], project=True, room="group", timeout=900),
]


# ── running ──────────────────────────────────────────────────────────────
def chat_for(case: Case, ctx: Context) -> str:
    if case.chat in ctx.chats:
        return ctx.chats[case.chat]
    if case.room:
        # Built-in ids predate the Jarvis/Glados names (scout/forge), so look them up by name.
        agents = {a["name"].lower(): a["id"] for a in ctx.api.call("GET", "/lean/agents")["items"]}
        members = [agents.get(name, name) for name in ("echo", "jarvis", "glados")]
        room = ctx.api.call("POST", "/lean/rooms", {
            "name": f"Eval {case.room}", "agent_ids": members, "kind": "group",
            "mode": "discussion" if case.room == "discussion" else "reply", "max_messages": 4,
        })
        thread_id = room["thread_id"]
        if case.project:
            ctx.api.call("POST", "/projects/attach-folder", {
                "path": str(ctx.project_dir), "name": "Eval project", "session_id": thread_id,
            })
    else:
        thread = ctx.api.call("POST", "/threads", {
            "title": f"Eval: {case.chat}", "source": "web", "project_id": ctx.project_id if case.project else "",
        })
        thread_id = thread["thread_id"]
    ctx.chats[case.chat] = thread_id
    return thread_id


def run_case(case: Case, ctx: Context) -> Result:
    result = Result()
    try:
        thread_id = chat_for(case, ctx)
    except Exception as exc:  # a broken setup fails this case, not the whole run
        result.error = f"setup failed: {exc}"
        return result
    decision = "deny" if case.deny_approvals else "allow"

    def on_event(event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "tool_start":
            result.tools.append(str(event.get("name")))
        elif kind == "tool_end" and not event.get("ok", True):
            result.tool_failures.append(str(event.get("name")))
        elif kind == "agent_start":
            result.agents.append(str((event.get("agent") or {}).get("id")))
        elif kind == "delegation":
            result.delegations.append(str(event.get("to")))
        elif kind == "agent_done" and event.get("stop_reason"):
            result.stop_reasons.append(str(event["stop_reason"]))
        elif kind == "run_outcome":
            result.outcome = {k: event.get(k) for k in ("status", "summary", "reason") if event.get(k)}
        elif kind == "job_continue":
            result.continuations.append(f"{event.get('agent')}: {event.get('reason')}")
        elif kind == "approval_request":
            result.approvals.append(str(event.get("tool")))
            approval_id = str(event.get("id"))
            # Answer from another thread: this one is busy reading the stream.
            threading.Thread(
                target=lambda: ctx.api.call("POST", f"/lean/approvals/{approval_id}", {"decision": decision}),
                daemon=True,
            ).start()
        elif kind == "final":
            result.text = str(event.get("response") or "")
            result.messages = list(event.get("messages") or [])
            if not event.get("success"):
                result.error = result.error or "turn reported failure"
        elif kind == "error":
            result.error = str(event.get("message") or "error")

    started = time.time()
    try:
        ctx.api.stream({
            "message": case.prompt, "thread_id": thread_id,
            "client_request_id": f"eval-{case.id}-{uuid.uuid4().hex[:8]}",
        }, on_event, timeout=case.timeout)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        result.error = f"request failed: {exc}"
    result.seconds = round(time.time() - started, 1)
    return result


def parse_only(spec: str) -> set[int]:
    chosen: set[int] = set()
    for part in filter(None, spec.split(",")):
        if "-" in part:
            a, b = part.split("-", 1)
            chosen.update(range(int(a), int(b) + 1))
        else:
            chosen.add(int(part))
    return chosen


def loaded_models(lm_studio: str) -> list[str]:
    try:
        with urllib.request.urlopen(lm_studio.rstrip("/") + "/api/v0/models", timeout=5) as response:
            data = json.loads(response.read())
        return [m["id"] for m in data.get("data", []) if m.get("state") == "loaded"]
    except Exception:
        return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--api", default="http://127.0.0.1:8000", help="EchoSpeak backend base URL")
    parser.add_argument("--token", default="", help="API token, if the backend requires one")
    parser.add_argument("--only", default="", help="Case ids, e.g. 1-5,10")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Folder for reports")
    parser.add_argument("--lm-studio", default="http://127.0.0.1:1234", help="LM Studio base URL (model check only)")
    args = parser.parse_args()
    # Windows consoles default to cp1252; labels and replies use ≥, …, emoji.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    api = Api(args.api, args.token)
    try:
        health = api.call("GET", "/health")
    except Exception as exc:
        print(f"Backend not reachable at {args.api}: {exc}")
        return 2
    models = loaded_models(args.lm_studio)
    print(f"EchoSpeak {health.get('version', '?')} at {args.api} · loaded models: {', '.join(models) or 'none yet (loads on first turn)'}")

    selected = parse_only(args.only)
    cases = [c for c in CASES if not selected or c.id in selected]
    # Follow-ups need the turns they build on.
    needed = {c.chat for c in cases}
    cases = [c for c in CASES if c in cases or (c.chat in needed and c.id < max(x.id for x in cases))]

    project_dir = Path(tempfile.mkdtemp(prefix="echospeak-eval-"))
    ctx = Context(api=api, project_dir=project_dir)
    if any(c.project for c in cases):
        project = api.call("POST", "/projects/attach-folder", {"path": str(project_dir), "name": "Eval project"})
        ctx.project_id = project["id"]

    rows: list[dict[str, Any]] = []
    for case in cases:
        print(f"[{case.id:2d}] {case.title} …", end=" ", flush=True)
        result = run_case(case, ctx)
        checks = [(label, bool(fn(result, ctx))) for label, fn in case.checks]
        passed = all(ok for _, ok in checks) and not result.error
        print(f"{'PASS' if passed else 'FAIL'} ({result.seconds}s)" + ("" if passed else f" — {', '.join(l for l, ok in checks if not ok) or result.error}"))
        rows.append({
            "id": case.id, "title": case.title, "prompt": case.prompt, "passed": passed,
            "checks": [{"label": l, "ok": ok} for l, ok in checks], "seconds": result.seconds,
            "tools": result.tools, "tool_failures": result.tool_failures, "agents": result.agents,
            "delegations": result.delegations, "approvals": result.approvals, "stop_reasons": result.stop_reasons,
            "outcome": result.outcome, "continuations": result.continuations,
            "error": result.error, "reply": result.text,
        })

    out_root = Path(args.out)
    previous = sorted(out_root.glob("*/report.json")) if out_root.exists() else []
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = out_root / stamp
    out.mkdir(parents=True, exist_ok=True)
    score = sum(r["passed"] for r in rows)
    report = {"version": health.get("version"), "api": args.api, "models": models, "at": stamp,
              "passed": score, "total": len(rows), "project_dir": str(project_dir), "cases": rows}
    (out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [f"# Eval {stamp}", "", f"EchoSpeak {report['version']} · models: {', '.join(models) or '?'} · **{score}/{len(rows)} passed**", "",
             "| # | Case | Result | Time | Tools | Notes |", "|---|---|---|---|---|---|"]
    for r in rows:
        failed = [c["label"] for c in r["checks"] if not c["ok"]]
        note = r["error"] or ("; ".join(failed) if failed else "")
        lines.append(f"| {r['id']} | {r['title']} | {'✅' if r['passed'] else '❌'} | {r['seconds']}s | "
                     f"{', '.join(dict.fromkeys(r['tools'])) or '—'} | {note} |")
    lines += ["", "## Replies", ""]
    for r in rows:
        lines += [f"### {r['id']}. {r['title']}", f"> {r['prompt']}", "", r["reply"].strip() or "_(no reply)_", ""]
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")

    summary = f"{score}/{len(rows)} passed"
    if previous:
        try:
            last = json.loads(previous[-1].read_text(encoding="utf-8"))
            if last.get("total") == len(rows):
                summary += f" (last run: {last.get('passed')}/{last.get('total')})"
        except Exception:
            pass
    print(f"\n{summary}. Report: {out / 'report.md'}")
    return 0 if score == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
