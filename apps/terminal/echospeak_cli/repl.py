"""The interactive loop and the run follower shared by every command."""
from __future__ import annotations

import os
import queue
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, TextIO

from .client import ApiError, Client, ConnectError, follow, new_request_id, submit
from .render import Renderer, one_line

HELP = """\
Type a message and press Enter. While a run is active, typing steers it (or queues a follow-up).
  /help                 this help
  /sessions             recent sessions
  /resume <id|prefix>   switch to a session (follows its running run, if any)
  /new [--agent ID]     start a new session (created on your first message)
  /agent [ID|-]         answer with this agent in this session (- = default)
  /agents               list agents
  /project [PATH|ID|-]  attach this session to a project (- = none)
  /status               backend, approvals and running runs
  /approvals            answer pending approvals for this session
  /stop                 cancel the active run (also: Ctrl-C during a run)
  /detach               stop following; the run keeps going on the backend
  /quit                 exit (Ctrl-D at the prompt; Ctrl-C twice)
@agent mentions are sent as text; the backend routes them."""


class _Abandon(Exception):
    pass


@dataclass
class RunResult:
    status: str = ""             # journal status: completed | cancelled | failed | interrupted | expired | detached
    success: Optional[bool] = None
    unanswered: list = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "completed" and self.success is not False and not self.unanswered and not self.error


class Out:
    def __init__(self, stream: TextIO, err: Optional[TextIO] = None) -> None:
        self.stream = stream
        self.err = err or stream

    def write(self, chunks) -> None:
        if isinstance(chunks, str):
            chunks = [chunks]
        for chunk in chunks:
            self.stream.write(chunk)
        self.stream.flush()

    def line(self, text: str = "") -> None:
        self.write(text + "\n")


def decision_choices(evt: dict) -> list[tuple[str, str, str]]:
    """(key, decision value, label) the approval route accepts for this item (api/routes/lean.py)."""
    if evt.get("kind") == "question" or evt.get("question"):
        return []
    choices = [("y", "allow", "allow once"), ("n", "deny", "deny")]
    if evt.get("recovered"):
        return choices  # recovered approvals accept only allow/deny
    if evt.get("grantable", True):
        choices.append(("c", "always", "allow for this chat"))
    if evt.get("persistable"):
        label = one_line(evt.get("scope_label") or "this kind of action", 80)
        choices.append(("a", "persist", f"always allow: {label}"))
    return choices


def parse_answer(evt: dict, raw: str) -> Optional[str]:
    """Map what the user typed to a decision value; None means ask again. Never defaults to allow."""
    text = raw.strip()
    if not text:
        return None
    if evt.get("kind") == "question" or evt.get("question"):
        options = [str(o) for o in evt.get("options") or []]
        if text.isdigit() and 1 <= int(text) <= len(options):
            return options[int(text) - 1]
        if any(text.lower() == o.lower() for o in options) or evt.get("allow_other") or not options:
            return text
        return None
    for key, value, _label in decision_choices(evt):
        if text.lower() in {key, value}:
            return value
    return None


class App:
    def __init__(self, client: Client, renderer: Renderer, out: Out, *, interactive: bool,
                 ask: Callable[[str], str] = input, approval_timeout: float = 0.0,
                 stdin: Optional[TextIO] = None, source_hint: str = "") -> None:
        self.client = client
        self.r = renderer
        self.out = out
        self.interactive = interactive
        self.ask = ask
        self.approval_timeout = approval_timeout
        self.stdin = stdin or sys.stdin
        self.source_hint = source_hint
        self.session: Optional[dict] = None
        self.agent_id = ""
        self.project_id = ""
        self.active = ""          # request id being followed
        self.prompted: set[str] = set()

    def say(self, text: str) -> None:
        """A whole line, starting on a fresh line even if answer text is mid-stream."""
        self.out.write(self.r._line(text))

    # ── sessions ──────────────────────────────────────────────────────
    @property
    def sid(self) -> str:
        return str((self.session or {}).get("thread_id") or "")

    def find_session(self, ref: str) -> dict:
        ref = ref.strip()
        try:
            return self.client.thread(ref)
        except ApiError as exc:
            if exc.status != 404:
                raise
        hits = [t for t in self.client.threads(limit=200) if str(t.get("thread_id", "")).startswith(ref)]
        if len(hits) != 1:
            raise LookupError(f"{'no' if not hits else 'more than one'} session matches {ref!r}")
        return hits[0]

    def project_for_path(self, path: str, create: bool = False) -> str:
        target = Path(path).expanduser().resolve()
        best, best_len = "", -1
        for p in (self.client.get("/projects") or {}).get("items", []):
            if p.get("archived"):
                continue
            for root in (p.get("workspace_root"), p.get("git_root")):
                if not root:
                    continue
                r = Path(root).expanduser().resolve()
                if (target == r or r in target.parents) and len(str(r)) > best_len:
                    best, best_len = str(p.get("id") or ""), len(str(r))
        if best or not create:
            return best
        # Same call the app makes when you attach a folder (apps/web/src/index.tsx).
        made = self.client.post("/projects/attach-folder", {"path": str(target), "trust_state": "trusted"})
        return str(made.get("id") or "")

    def resolve_project(self, ref: str) -> str:
        if os.sep in ref or ref.startswith((".", "~")) or Path(ref).is_dir():
            return self.project_for_path(ref, create=True)
        return str(self.client.get("/projects/" + ref).get("id") or "")

    def default_session(self, cwd: str) -> Optional[dict]:
        """Most recent owner chat for the current directory's project, if one is determinable."""
        try:
            project = self.project_for_path(cwd)
        except (ApiError, OSError):
            return None
        if not project:
            return None
        self.project_id = project
        chats = [t for t in self.client.threads(limit=200)
                 if t.get("project_id") == project and t.get("environment", "chat") == "chat"
                 and t.get("source", "web") == "web" and not t.get("archived")]
        chats.sort(key=lambda t: float(t.get("last_active_at") or 0), reverse=True)
        return chats[0] if chats else None

    def create_session(self, title: str, *, project_id: str) -> dict:
        if not self.agent_id:
            return self.client.create_thread(title=title, project_id=project_id, idempotency_key=new_request_id())
        # A persona answers in a direct room, as in the desktop Agents view.
        room = self.client.post("/lean/rooms", {"name": title, "agent_ids": [self.agent_id], "kind": "direct"})
        thread_id = str(room["thread_id"])
        if project_id:
            self.client.request("POST", f"/projects/{project_id}/activate", params={"thread_id": thread_id})
        return self.client.thread(thread_id)

    def ensure_session(self, first_message: str) -> str:
        if not self.sid:
            self.session = self.create_session(one_line(first_message, 60), project_id=self.project_id)
            self.say(self.r.s.dim(f"new session {self.sid}"))
        return self.sid

    def agent_note(self) -> str:
        if self.agent_id and self.sid and (self.session or {}).get("environment", "chat") == "chat":
            return (f"note: this is a chat session, where the selected model answers; "
                    f"/new then a message starts a session with {self.agent_id}")
        return ""

    def switch(self, session: dict) -> None:
        self.session = session
        self.project_id = str(session.get("project_id") or "")
        self.say(self.r.s.dim(f"session {self.sid}  {one_line(session.get('title') or '(untitled)', 60)}"))
        self.catch_up()

    def catch_up(self) -> None:
        """Follow a run already active in this session, and surface approvals that are waiting."""
        if not self.sid:
            return
        running = [r for r in self.client.runs(self.sid) if r.get("status") == "running"]
        if running:
            self.say(self.r.s.dim(f"following running run {running[0]['id']}"))
            self.follow_run(running[0]["id"])
        else:
            for item in self.client.approvals(self.sid):
                self.on_approval(item, verified=True)

    # ── runs ──────────────────────────────────────────────────────────
    def send(self, text: str, request_id: str = "") -> RunResult:
        sid = self.ensure_session(text)
        request_id = request_id or new_request_id()
        try:
            first = submit(self.client, sid, text, request_id, agent_id=self.agent_id)
        except ApiError as exc:
            self.say(self.r.s.red(f"✗ {one_line(exc.detail, 300)}"))
            return RunResult(status="failed", error=exc.detail)
        result = self.follow_run(request_id, first=first)
        if self.interactive and result.status == "completed":
            self.run_queued()
        return result

    def run_queued(self) -> None:
        """Run follow-ups queued while the turn was busy. Claiming is atomic, so only one client runs each."""
        while True:
            try:
                claimed = self.client.request("POST", "/query/queue/claim", params={"thread_id": self.sid})
            except (ApiError, ConnectError):
                return
            item = (claimed or {}).get("item")
            if not (claimed or {}).get("claimed") or not item:
                return
            self.say(self.r.s.dim(f"› {one_line(item.get('message'), 200)}  (queued)"))
            result = self.follow_run_new(str(item.get("message") or ""), str(item.get("client_request_id") or ""))
            if result.status != "completed":
                return

    def follow_run_new(self, text: str, request_id: str) -> RunResult:
        first = submit(self.client, self.sid, text, request_id, agent_id=self.agent_id)
        return self.follow_run(request_id, first=first)

    def follow_run(self, request_id: str, first=None, after: int = 0) -> RunResult:
        q: queue.Queue = queue.Queue()
        abandon = threading.Event()
        sid = self.sid

        def on_event(evt: dict) -> None:
            if abandon.is_set():
                raise _Abandon()
            q.put(("event", evt))

        def worker() -> None:
            try:
                status, _cursor = follow(self.client, sid, request_id, after=after, first=first, on_event=on_event,
                                         notice=lambda m: q.put(("notice", m)))
                q.put(("done", status))
            except _Abandon:
                pass
            except Exception as exc:  # noqa: BLE001 - surfaced to the user below
                q.put(("fail", exc))

        threading.Thread(target=worker, name="echospeak-follow", daemon=True).start()
        self.active = request_id
        result = RunResult()
        waiting: dict[str, float] = {}
        cancel_sent = False
        try:
            while True:
                try:
                    try:
                        kind, payload = q.get(timeout=0.1)
                    except queue.Empty:
                        if self.interactive and self._poll_typing() == "detach":
                            result.status = "detached"
                            return result
                        if waiting and self.approval_timeout and \
                                time.monotonic() - min(waiting.values()) > self.approval_timeout:
                            self.say(self.r.s.yellow("approval still pending after timeout; leaving the run waiting"))
                            result.status = "detached"
                            return result
                        continue
                    if kind == "notice":
                        self.say(self.r.s.dim(f"· {payload}"))
                    elif kind == "fail":
                        result.status, result.error = "failed", str(payload)
                        self.say(self.r.s.red(f"✗ lost the run: {one_line(payload, 300)}"))
                        return result
                    elif kind == "done":
                        result.status = payload
                        if payload not in {"completed"}:
                            self.say(self.r.s.yellow(f"run {payload}"))
                        return result
                    else:
                        self.out.write(self.r.feed(payload))
                        etype = payload.get("type")
                        if etype == "final":
                            result.success = bool(payload.get("success"))
                        elif etype == "error":
                            result.error = str(payload.get("message") or "error")
                        elif etype == "approval_request":
                            if not self.on_approval(payload, verified=not payload.get("_recovered"), render=False):
                                waiting.setdefault(str(payload.get("id")), time.monotonic())
                        elif etype == "approval_resolved":
                            waiting.pop(str(payload.get("id")), None)
                except KeyboardInterrupt:
                    if cancel_sent:
                        result.status = "detached"
                        return result
                    cancel_sent = True
                    self.cancel(request_id)
        finally:
            abandon.set()
            self.active = ""
            result.unanswered = list(waiting)
            if result.status == "detached":
                self.say(self.r.s.dim(f"detached; run {request_id} keeps going on the backend "
                                           f"(echospeak attach {sid[:8]})"))

    def cancel(self, request_id: str) -> None:
        try:
            res = self.client.cancel(request_id, self.sid)
            msg = "stopping…" if res.get("cancelled") else f"not active ({res.get('reason', '')})"
        except (ApiError, ConnectError) as exc:
            msg = f"stop failed: {exc}"
        self.say(self.r.s.yellow(msg + ("  (Ctrl-C again to detach)" if self.active else "")))

    def _poll_typing(self) -> str:
        """Lines typed during a run steer it, or queue a follow-up when steering isn't possible."""
        try:
            import select

            ready, _, _ = select.select([self.stdin], [], [], 0)
        except (ImportError, OSError, ValueError):
            return ""
        if not ready:
            return ""
        line = self.stdin.readline()
        if not line:
            return "detach"  # Ctrl-D
        text = line.strip()
        if not text:
            return ""
        if text in {"/detach", "/quit", "/exit"}:
            return "detach"
        if text == "/stop":
            self.cancel(self.active)
            return ""
        if text.startswith("/"):
            self.say(self.r.s.dim("only /stop and /detach work during a run; anything else is sent to the agent"))
            return ""
        try:
            res = self.client.steer(self.sid, text)
            self.say(self.r.s.dim(f"· steering queued for the next checkpoint ({res.get('pending', 1)} pending)"))
        except ApiError as exc:
            if exc.status not in {400, 409}:
                raise
            res = self.client.queue(self.sid, text)
            self.say(self.r.s.dim(f"· queued as a follow-up ({res.get('queue_length', 1)} in queue)"))
        return ""

    # ── approvals ─────────────────────────────────────────────────────
    def on_approval(self, evt: dict, verified: bool, render: bool = True) -> bool:
        """Show and (interactive only) answer one approval. Returns True if it was answered."""
        aid = str(evt.get("id") or "")
        if not aid or aid in self.prompted:
            return True
        if render:
            self.out.write(self.r.approval_block(evt))
        if not verified:
            try:
                live = {str(a.get("id")): a for a in self.client.approvals(self.sid)}
            except (ApiError, ConnectError):
                live = {}
            if aid not in live:
                self.say(self.r.s.dim("  (already decided)"))
                return True
            evt = {**evt, **live[aid]}
        if not self.interactive:
            self.say(self.r.s.yellow(
                f"approval pending ({aid}); this terminal will not answer it. Answer in the EchoSpeak app, "
                f"or run: echospeak attach {self.sid[:8]}"))
            return False
        self.prompted.add(aid)
        choices = decision_choices(evt)
        prompt = ("answer (number or text): " if not choices
                  else "  ".join(f"[{k}] {label}" for k, _v, label in choices) + " > ")
        while True:
            try:
                raw = self.ask(prompt)
            except EOFError:
                self.say(self.r.s.yellow("left pending"))
                return False
            decision = parse_answer(evt, raw)
            if decision is None:
                self.say(self.r.s.dim("please choose one of the options"))
                continue
            try:
                res = self.client.decide(aid, decision)
                self.say(self.r.s.dim(f"  sent: {one_line(decision, 80)}" + (
                    f" (work {res['resume_work_id']} resumes)" if res.get("resume_work_id") else "")))
                return True
            except ApiError as exc:
                self.say(self.r.s.red(f"  not accepted: {one_line(exc.detail, 200)}"))
                if exc.status == 404:
                    return True

    def answer_pending(self) -> None:
        items = self.client.approvals(self.sid) if self.sid else self.client.approvals()
        if not items:
            self.say(self.r.s.dim("no approvals waiting"))
        for item in items:
            self.prompted.discard(str(item.get("id")))
            self.on_approval(item, verified=True)

    # ── the loop ──────────────────────────────────────────────────────
    def repl(self, commands: "Callable[[App, str], bool]") -> int:
        try:
            import readline  # noqa: F401 - line editing and history for input()
        except ImportError:
            pass
        self.say(self.r.s.dim("EchoSpeak terminal — /help for commands, Ctrl-D to exit"))
        interrupts = 0
        while True:
            try:
                label = (self.session or {}).get("title") or "new session"
                line = input(f"[{one_line(label, 24)}] › ")
                interrupts = 0
            except EOFError:
                self.out.line()
                return 0
            except KeyboardInterrupt:
                interrupts += 1
                if interrupts >= 2:
                    self.out.line()
                    return 0
                self.say(self.r.s.dim("\n(Ctrl-C again or Ctrl-D to exit)"))
                continue
            text = line.strip()
            if not text:
                continue
            try:
                if text.startswith("/"):
                    if not commands(self, text):
                        return 0
                else:
                    self.send(text)
            except (ApiError, ConnectError, LookupError) as exc:
                self.say(self.r.s.red(f"✗ {one_line(exc, 300)}"))
            except KeyboardInterrupt:
                self.out.line()
