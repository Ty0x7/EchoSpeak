"""Research swarm, phase 1: read-only workers under a lead agent.

The lead (the agent the user is talking to) calls ``spawn_workers`` with a few
independent research questions. Each becomes a short-lived worker: an ordinary
LeanTurn with a fresh context, the lead's model, and only the read-only research
tools the lead itself has (search, reading pages, reading files). Workers run a
few at a time, share one token budget and one clock, and return structured
findings. The lead writes the answer; workers never talk to the user, never
appear as chat members and can't ask for approval.

Why this shape (docs/research/swarms.md): parallel workers help breadth-first
research and hurt sequential work; independent agents amplify errors unless a
lead checks them; small local models get worse with more copies of themselves;
writes stay single-threaded. So: read-only, few, bounded, lead-verified, and
off by default until the bench (scripts/echo_bench.py --swarm) shows a gain.

Boundaries: a worker's tools are a subset of the lead's (so Settings ›
Permissions, toolsets and Blocked apps already apply), Private/Offline mode is
enforced by Toolbox.run as for any call, outside content a worker reads taints
the whole request (Rule of Two), and its findings reach the lead as untrusted
content. Stop cancels every worker.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from loguru import logger

from agent.lean import progress, settings
from agent.lean.toolbox import NativeTool, ToolResult, Toolbox

MAX_WORKERS = 6
MAX_CALLS_PER_REQUEST = 2
WORKER_SECONDS = 240.0
# Read-only research tools a worker may have, if the lead has them.
WORKER_TOOLS = {
    "web_search", "safe_web_fetch", "youtube_transcript", "research_notebook", "read_output",
    "weather_live", "sports_live", "stock_history", "product_search", "video_search", "image_search",
    "file_read", "file_list", "file_find", "file_search", "project_workspace", "document_search", "calculate",
}
_RESEARCH_TOOLS = {"web_search", "safe_web_fetch", "file_read", "file_search", "document_search", "project_workspace"}

DESCRIPTION = (
    "Research several independent questions at the same time with short-lived, read-only workers, then write the "
    "answer yourself from their findings. Use it only for deep, broad research: many sources, or several separate "
    "sub-questions that don't depend on each other. Don't use it for a quick comparison you can search yourself, a "
    "single fact, for "
    "step-by-step work where one answer feeds the next, or for anything that changes files or sends messages. "
    "Simple question: no workers. Comparison: one worker per option (2-4). Broad research: up to 6. "
    "Each worker gets only its goal, so make each goal self-contained."
)

WORKER_PROMPT = """You are a research worker for {lead}, who is helping their user. You have exactly one question to answer.

How to work:
- Use your tools to find the answer now: search, then open and read the most relevant pages. Snippets are leads, not evidence.
- Read at least one real source before answering. Prefer primary and recent sources. If sources disagree, say so.
- You can only look things up. You can't change files, send anything, ask the user, or get approvals.
- Stay on your question; other workers cover the rest.
- Keep going until you can answer, then stop.

Finish with only a JSON object, no other text:
{{"answer": "what you found, with numbers and dates where they matter", "sources": [{{"url": "...", "title": "..."}}], "confidence": "high | medium | low", "open_questions": ["what you couldn't confirm"]}}
List only sources you actually opened. If you couldn't find it, say so in "answer" and use "low".

Today is {today}."""


@dataclass
class Worker:
    index: int
    goal: str
    expected: str = ""
    id: str = field(default_factory=lambda: f"wk_{uuid.uuid4().hex[:8]}")
    status: str = "queued"  # queued | running | done | partial | failed | skipped | cancelled
    steps: int = 0
    tools: int = 0
    tokens: int = 0
    started: float = 0.0
    ended: float = 0.0
    answer: str = ""
    confidence: str = ""
    sources: list[dict[str, Any]] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    note: str = ""
    activity: str = ""
    cancel: threading.Event = field(default_factory=threading.Event)
    turn: Any = None
    result: Any = None

    def elapsed(self) -> float:
        if not self.started:
            return 0.0
        return round((self.ended or time.time()) - self.started, 1)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id, "index": self.index, "goal": self.goal, "status": self.status, "steps": self.steps,
            "tools": self.tools, "tokens": self.tokens, "elapsed_s": self.elapsed(), "confidence": self.confidence,
            "answer": self.answer[:600], "note": self.note, "activity": self.activity[:120],
            "sources": [{"url": s.get("url", ""), "title": s.get("title", ""), "opened": bool(s.get("opened"))}
                        for s in self.sources[:8]],
            "open_questions": self.open_questions[:4],
        }


# Requests that ask for deep research by name, or for many sources.
_DEEP = re.compile(r"(?i)\b(deep[\s-]?(?:research|search|dive)|in[\s-]depth|thorough(?:ly)?|comprehensive(?:ly)?|"
                   r"exhaustive(?:ly)?|extensive(?:ly)?|literature\s+review|research\s+(?:widely|broadly))\b")
_SOURCES = re.compile(r"(?i)\b(\d{1,2}|five|six|seven|eight|nine|ten|twelve|fifteen|twenty)\+?\s+"
                      r"(?:different\s+|separate\s+|independent\s+|good\s+|reliable\s+)?sources\b")
_WORDS = {"five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twelve": 12, "fifteen": 15, "twenty": 20}


def wants_workers(goal: str, effort: str = "") -> bool:
    """Workers are for deep research: Ultra effort, or a request for in-depth or many-source research."""
    if str(effort or "").lower() == "ultra":
        return True
    text = str(goal or "")
    if _DEEP.search(text):
        return True
    count = _SOURCES.search(text)
    if count:
        raw = count.group(1).lower()
        return (int(raw) if raw.isdigit() else _WORDS.get(raw, 0)) >= 5
    return False


def is_local(endpoint: Any) -> bool:
    if bool(getattr(endpoint, "local", False)):
        return True
    url = str(getattr(endpoint, "base_url", "") or "").lower()
    return any(host in url for host in ("localhost", "127.0.0.1", "[::1]"))


def _parse_findings(text: str) -> dict[str, Any]:
    """The worker's closing JSON, or its plain text as the answer when it didn't follow the format."""
    body = str(text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", body, re.S)
    candidates = [fenced.group(1)] if fenced else []
    start, end = body.find("{"), body.rfind("}")
    if start != -1 and end > start:
        candidates.append(body[start:end + 1])
    for raw in candidates:
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        if isinstance(data, dict) and ("answer" in data or "sources" in data):
            return data
    return {"answer": body}


def _opened_urls(turn: Any) -> set[str]:
    """Pages this worker actually read, from its own tool records (not what it says it read)."""
    opened: set[str] = set()
    for item in getattr(turn, "timeline", []) or []:
        if item.get("kind") != "tool" or item.get("status") != "done":
            continue
        if item.get("name") == "safe_web_fetch" and str(item.get("target") or "").startswith("http"):
            opened.add(str(item["target"]).rstrip("/"))
        if item.get("name") == "web_search":
            opened.update(u.rstrip("/") for u in re.findall(r"^### (https?://\S+)", str(item.get("output") or ""), re.M))
    return opened


def _checked(turn: Any) -> bool:
    """At least one research tool ran and worked: the answer rests on something looked up."""
    return any(item.get("kind") == "tool" and item.get("status") == "done" and item.get("name") in _RESEARCH_TOOLS | WORKER_TOOLS
               for item in getattr(turn, "timeline", []) or [])


class Swarm:
    """One spawn_workers call: schedules, budgets, cancels and collects its workers."""

    def __init__(self, *, session: Any, persona: Any, lead_toolbox: Toolbox, cancel: threading.Event,
                 state: Optional[Callable[[dict], None]] = None, spans: Optional[Callable[..., Any]] = None) -> None:
        self.session, self.persona, self.lead_toolbox, self.lead_cancel = session, persona, lead_toolbox, cancel
        self.state = state
        self.spans = spans
        self.workers: list[Worker] = []
        self.budget = settings.swarm_token_budget()
        self.deadline = 0.0
        self.stop_reason = ""
        self._lock = threading.Lock()
        from agent.coordination import current
        self.work_context = current()

    # ── setup ───────────────────────────────────────────────────────────
    def worker_toolbox(self) -> Toolbox:
        lead = self.lead_toolbox
        box = Toolbox(toolsets=["none"], session_id=lead.session_id, project_root=lead.project_root)
        box.entries = {n: e for n, e in lead.entries.items() if n in WORKER_TOOLS}
        box.native = {n: t for n, t in lead.native.items() if n in WORKER_TOOLS and not t.handoff and not t.ends_turn}
        box.deferred = set()
        box.requested = set(box.entries) | set(box.native)
        return box

    def tokens_used(self) -> int:
        return sum(w.tokens for w in self.workers)

    def publish(self) -> None:
        if self.state is None:
            return
        try:
            self.state({"kind": "workers", "budget": self.budget, "used": self.tokens_used(),
                        "stop_reason": self.stop_reason, "workers": [w.public() for w in self.workers]})
        except Exception:
            logger.debug("Could not publish worker state", exc_info=True)

    # ── one worker ──────────────────────────────────────────────────────
    def _on_event(self, worker: Worker, event: dict[str, Any]) -> None:
        kind = event.get("type")
        with self._lock:
            if kind == "step_start":
                worker.steps = int(event.get("step") or worker.steps)
            elif kind == "tool_start":
                worker.activity = str(event.get("label") or event.get("name") or "")
            elif kind == "tool_end":
                worker.tools += 1
                worker.activity = str(event.get("done_label") or worker.activity)
            elif kind == "tool_progress":
                worker.activity = str(event.get("text") or worker.activity)
            elif kind == "token_usage":
                # The event carries one step's usage; the turn keeps the running "fresh" total.
                usage = getattr(worker.turn, "usage", None) or {}
                worker.tokens = int(usage.get("fresh") or usage.get("total") or worker.tokens)
            else:
                return
            over = self.tokens_used() > self.budget
        if over and not self.stop_reason:
            self.stop_reason = f"the workers' shared budget of {self.budget:,} tokens ran out"
            for other in self.workers:
                other.cancel.set()
        self.publish()

    def _run_one(self, worker: Worker) -> None:
        from datetime import datetime

        from agent.lean.loop import LeanTurn
        from agent.lean.personas import AgentPersona
        from agent.lean.provider import ChatClient, reasoning_effort_for

        if self.lead_cancel.is_set() or worker.cancel.is_set():
            worker.status = "cancelled" if self.lead_cancel.is_set() else "skipped"
            worker.note = "stopped before it started" if self.lead_cancel.is_set() else self.stop_reason or "skipped"
            self.publish()
            return
        if self.tokens_used() > self.budget or time.time() > self.deadline:
            worker.status, worker.note = "skipped", self.stop_reason or "out of time for the swarm"
            self.publish()
            return
        worker.status, worker.started = "running", time.time()
        self.publish()
        endpoint = self.session._endpoint_for(self.persona)
        client = ChatClient(endpoint, reasoning_effort=reasoning_effort_for(endpoint, False, "low"))
        name = f"{self.persona.name} · worker {worker.index}"
        prompt = WORKER_PROMPT.format(lead=self.persona.name, today=datetime.now().strftime("%A, %B %d, %Y"))
        brief = worker.goal + (f"\n\nWhat the lead needs back: {worker.expected}" if worker.expected else "")
        stack = ExitStack()
        child_context = None
        # One span per worker on the lead's trace (Activity › Timing); each worker also saves its own trace.
        span = stack.enter_context(self.spans("swarm.worker", worker=worker.index, goal=worker.goal[:200])) if self.spans else None
        try:
            if self.work_context:
                from agent.coordination import work_scope
                parent = self.work_context
                box = self.worker_toolbox()
                work = parent.coordinator.create(objective=worker.goal, agent_id=f"worker:{parent.attempt_id}:{worker.index}",
                    principal=parent.principal, parent_id=parent.work_id, session_id=self.session.session_id,
                    tools=list(box.names), key=f"worker:{parent.attempt_id}:{worker.index}",
                    budget={"tokens": min(self.budget, 16000), "seconds": 300, "children": 0}, taint=self.session._taint)
                child_context = parent.coordinator.claim(work["id"], principal=parent.principal, session_id=self.session.session_id)
                parent.coordinator.bind(child_context, list(box.names), execution_id=self.session.execution_id,
                    model={"model": str(getattr(endpoint, "model", "")), "kind": "temporary_worker"})
                stack.enter_context(work_scope(child_context))
            for attempt in (1, 2):  # one retry when the model call itself fails
                turn = LeanTurn(
                    client=client,
                    persona=AgentPersona(id=self.persona.id, name=name, model=self.persona.model),
                    system_prompt=prompt, history=[], toolbox=self.worker_toolbox(),
                    session_id=self.session.session_id, request_id=self.session.request_id,
                    execution_id=self.session.execution_id, emit=lambda e, w=worker: self._on_event(w, e),
                    cancel=worker.cancel, persist_tool_runs=False, interactive=False,
                    taint=self.session._taint, goal=worker.goal, promise_guard=True,
                )
                turn.meta = {"swarm_worker": worker.index}
                turn.max_steps_override = settings.swarm_worker_steps()
                turn.steerable = False  # the user's steer is for the lead, not one worker's subtask
                turn.final_grace = True  # out of steps: one last tool-less call to report its findings
                worker.turn = turn
                result = turn.run(brief, announce=False)
                worker.result = result
                if result.success or worker.cancel.is_set() or self.lead_cancel.is_set() or attempt == 2:
                    break
                worker.note = f"retried after: {result.error[:120]}"
            self._finish(worker)
        except Exception as exc:  # a worker must never take the lead's turn down
            logger.warning("Research worker failed: {}", exc)
            worker.status, worker.note = "failed", f"{type(exc).__name__}: {exc}"[:200]
        finally:
            if child_context:
                child_context.coordinator.finish(child_context,
                    status="completed" if worker.status == "done" else "cancelled" if worker.status == "cancelled" else "failed",
                    result=worker.answer, evidence=worker.sources, verification="sources_opened" if worker.status == "done" else "unverified")
            if span is not None:
                span.set(status=worker.status, tokens=worker.tokens, steps=worker.steps, tools=worker.tools,
                         sources=sum(1 for s in worker.sources if s.get("opened")))
            stack.close()
            worker.ended = time.time()
            client.close()
            self.publish()

    def _finish(self, worker: Worker) -> None:
        result, turn = worker.result, worker.turn
        findings = _parse_findings(getattr(result, "text", ""))
        opened = _opened_urls(turn)
        sources = []
        for row in findings.get("sources") or []:
            if not isinstance(row, dict):
                continue
            url = str(row.get("url") or "").strip()
            if not url.startswith("http"):
                continue
            sources.append({"url": url, "title": str(row.get("title") or "")[:160],
                            "opened": url.rstrip("/") in opened})
        cited = {s["url"].rstrip("/") for s in sources}
        sources += [{"url": u, "title": "", "opened": True} for u in sorted(opened - cited)][:6]
        worker.sources = sources
        worker.answer = str(findings.get("answer") or "").strip()[:4000]
        worker.confidence = str(findings.get("confidence") or "").strip().lower()[:12]
        worker.open_questions = [str(q)[:300] for q in (findings.get("open_questions") or []) if str(q).strip()][:6]
        stopped = worker.cancel.is_set() or self.lead_cancel.is_set()
        if self.lead_cancel.is_set():
            worker.status = "cancelled"
        elif not result.success and not stopped:
            # The loop's friendly error text is not an answer.
            worker.status, worker.note, worker.answer = "failed", (result.error or "no answer")[:200], ""
        elif worker.answer and _checked(turn) and not stopped and result.stop_reason not in {"max_steps", "unverified_claim"}:
            worker.status = "done"
        elif worker.answer:
            worker.status = "partial"
            worker.note = worker.note or ("stopped early: " + (self.stop_reason or result.stop_reason or "time")
                                          if stopped or result.stop_reason else "answered without looking anything up")
        else:
            worker.status, worker.note = "failed", worker.note or "no answer"

    # ── the whole call ──────────────────────────────────────────────────
    def run(self, tasks: list[dict[str, Any]]) -> ToolResult:
        started = time.time()
        self.workers = [Worker(index=i + 1, goal=t["goal"], expected=t.get("expected", "")) for i, t in enumerate(tasks)]
        local = is_local(self.session._endpoint_for(self.persona))
        parallel = settings.swarm_parallel(local)
        # Workers sharing one local GPU each go slower (or wait in its queue), so each gets more time.
        sharing = min(parallel, len(self.workers)) if local else 1
        limit = WORKER_SECONDS * (1 + (sharing - 1) / 2)
        self.deadline = started + max(limit, WORKER_SECONDS * 2)
        self.publish()
        stop_watch = threading.Event()

        def watch() -> None:
            # Stop and per-worker time limits, checked from outside the workers.
            while not stop_watch.wait(0.25):
                for worker in self.workers:
                    if self.lead_cancel.is_set() and not worker.cancel.is_set():
                        worker.cancel.set()
                    elif worker.status == "running" and time.time() - worker.started > limit and not worker.cancel.is_set():
                        worker.note = f"stopped at the {int(limit)}s limit"
                        worker.cancel.set()

        watcher = threading.Thread(target=watch, name="swarm-watch", daemon=True)
        watcher.start()
        try:
            with ThreadPoolExecutor(max_workers=parallel, thread_name_prefix="swarm") as pool:
                list(pool.map(self._run_one, self.workers))
        finally:
            stop_watch.set()
            watcher.join(timeout=1.0)
        self.publish()
        self._learn()
        done = [w for w in self.workers if w.status in {"done", "partial"}]
        result = ToolResult(bool(done), self.report(time.time() - started), 0,
                            meta={"workers": len(self.workers), "done": sum(1 for w in self.workers if w.status == "done")})
        return result

    def report(self, seconds: float) -> str:
        lines = [f"{len(self.workers)} workers finished in {seconds:.0f}s using {self.tokens_used():,} of {self.budget:,} "
                 "tokens. Their findings are below: check them against each other, resolve conflicts, then compose the "
                 "answer yourself, citing the sources they opened. These are findings only: nothing has been saved to "
                 "a file or done yet. Do the rest of the request (for example file_write) before saying it's done."]
        if self.stop_reason:
            lines.append(f"Note: {self.stop_reason}; later workers were skipped or stopped early.")
        url_owners: dict[str, list[int]] = {}
        for worker in self.workers:
            label = {"done": "done", "partial": "partial", "failed": "failed", "skipped": "skipped",
                     "cancelled": "stopped"}.get(worker.status, worker.status)
            head = f"\n## Worker {worker.index}: {worker.goal}\nStatus: {label}"
            if worker.confidence:
                head += f" · confidence {worker.confidence}"
            if worker.note:
                head += f" · {worker.note}"
            lines.append(head)
            if worker.answer:
                lines.append(worker.answer)
            opened = [s for s in worker.sources if s.get("opened")]
            claimed = [s for s in worker.sources if not s.get("opened")]
            if opened:
                lines.append("Sources it opened:\n" + "\n".join(f"- {s['url']}" + (f" ({s['title']})" if s['title'] else "") for s in opened[:6]))
            if claimed:
                lines.append("Cited but not opened by this worker (treat as unverified): " + ", ".join(s["url"] for s in claimed[:4]))
            if worker.open_questions:
                lines.append("Open questions: " + "; ".join(worker.open_questions))
            for source in opened:
                url_owners.setdefault(source["url"].rstrip("/"), []).append(worker.index)
        shared = {url: owners for url, owners in url_owners.items() if len(set(owners)) > 1}
        if shared:
            lines.append("\nSources more than one worker used (count them once): "
                         + "; ".join(f"{url} (workers {', '.join(map(str, sorted(set(o))))})" for url, o in list(shared.items())[:6]))
        return "\n".join(lines)

    # ── learning ────────────────────────────────────────────────────────
    def _learn(self) -> None:
        """Each worker's run is its own episode (role "worker"), graded from its own tool records."""
        if self.lead_cancel.is_set() or getattr(self.session, "caller_role", "owner") != "owner":
            return
        try:
            if settings.learning_mode() != "on":
                return
            from agent.learning import get_experience_store, reliability
            from agent.learning.episodes import build_episodes

            store = get_experience_store()
            if self.persona.id in store.paused_agents():
                return
            endpoint = self.session._endpoint_for(self.persona)
            for worker in self.workers:
                if worker.result is None:
                    continue
                for episode in build_episodes(
                    goal=worker.goal, results=[worker.result], job=None, taint=list(self.session._taint),
                    session_id=self.session.session_id, execution_id=self.session.execution_id, source="swarm",
                    names={self.persona.id: self.persona.name}, endpoints={self.persona.id: (endpoint.provider, endpoint.model)},
                    lessons_used={}, team=False,
                ):
                    episode.role = "worker"
                    # Workers run on their lead's model: when the router picked it, their spend
                    # counts toward auto mode's daily cloud budget (routing.cloud_tokens_today).
                    if self.persona.id in (getattr(self.session, "_route_overrides", None) or {}):
                        episode.routed = "router"
                    episode.summary = (f"Worker {worker.index}: {worker.status}. " + episode.summary)[:600]
                    store.add_episode(episode)
                    reliability.record_episode(episode)
        except Exception:
            logger.warning("Learning: could not record research workers", exc_info=True)


def clean_tasks(raw: Any) -> tuple[list[dict[str, str]], str]:
    """Validated, de-duplicated tasks, or an error for the model."""
    from agent.lean.job import near_duplicate

    if not isinstance(raw, list) or not raw:
        return [], "Error: give 'tasks' as a list of {goal, expected_output}."
    tasks: list[dict[str, str]] = []
    for row in raw:
        goal = str((row or {}).get("goal") if isinstance(row, dict) else row or "").strip()
        if len(goal) < 8:
            continue
        if any(near_duplicate(goal, t["goal"], threshold=0.8) for t in tasks):
            continue  # the same question twice would spend twice for one answer
        tasks.append({"goal": goal[:1200], "expected": str((row or {}).get("expected_output") or "")[:400] if isinstance(row, dict) else ""})
    if len(tasks) < 2:
        return [], ("Error: workers are for two or more independent questions. For one question, research it "
                    "yourself with web_search and safe_web_fetch.")
    return tasks[:MAX_WORKERS], ""


def tool(session: Any, persona: Any) -> NativeTool:
    """spawn_workers, bound to its lead's toolbox once that exists (runtime._build_turn)."""
    calls = {"n": 0}
    holder: dict[str, Any] = {}

    def spawn(args: dict[str, Any]) -> ToolResult | str:
        lead_toolbox = holder.get("toolbox")
        if lead_toolbox is None:
            return "Error: workers are not available in this turn."
        if calls["n"] >= MAX_CALLS_PER_REQUEST:
            return (f"Error: this request already used workers {MAX_CALLS_PER_REQUEST} times. Finish with what they "
                    "found, or research the rest yourself.")
        tasks, problem = clean_tasks(args.get("tasks"))
        if problem:
            return problem
        swarm = Swarm(session=session, persona=persona, lead_toolbox=lead_toolbox, cancel=session.cancel,
                      state=progress.state_sink(), spans=progress.span_factory())
        if not ({"web_search", "safe_web_fetch", "file_read", "document_search"} & (set(swarm.worker_toolbox().names))):
            return "Error: you have no research tools for workers to use (web search or file reading)."
        calls["n"] += 1
        progress.report(f"Starting {len(tasks)} research workers")
        return swarm.run(tasks)

    native = NativeTool(
        name="spawn_workers",
        description=DESCRIPTION,
        parameters={"type": "object", "properties": {
            "tasks": {"type": "array", "maxItems": MAX_WORKERS, "items": {"type": "object", "properties": {
                "goal": {"type": "string", "description": "One self-contained research question."},
                "expected_output": {"type": "string", "description": "What you need back, e.g. price, battery life and a source."},
            }, "required": ["goal"]}},
        }, "required": ["tasks"]},
        func=spawn,
    )
    native.bind = lambda toolbox: holder.__setitem__("toolbox", toolbox)  # type: ignore[attr-defined]
    return native
