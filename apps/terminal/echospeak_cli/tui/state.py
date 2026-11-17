"""The run reducer: backend events -> (scrollback blocks, live state). Pure, no I/O, no clock reads.

A port of the GUI's wording (``apps/web/src/lean/{liveReducer.ts,steps.ts,LiveStatus.tsx}``) so the
terminal says the same honest things the app does: what is running, what each step did, and whether
the work was verified. Every server string is sanitized when it is rendered (``blocks.render``).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

from ..render import NORMAL_STOPS, one_line, sanitize
from . import blocks as B
from . import diffview as DV
from .mdstream import MdStream
from .motion import fmt_elapsed, thinking_words

ECHO_IDS = {"echo", "chat", "code"}

# ── steps.ts port ────────────────────────────────────────────────────
_BUCKETS: dict[str, tuple[str, int]] = {
    "web_search": ("search", 0), "safe_web_fetch": ("pages", 1), "product_search": ("prices", 2),
    "image_search": ("images", 3), "video_search": ("videos", 3), "file_read": ("read", 4),
    "file_list": ("look", 5), "file_find": ("look", 5), "file_search": ("look", 5),
    "file_write": ("change", 6), "file_edit": ("change", 6), "file_move": ("change", 6), "file_copy": ("change", 6),
    "file_mkdir": ("change", 6), "file_delete": ("change", 6),
    "terminal": ("run", 7), "terminal_run": ("run", 7), "process_start": ("run", 7), "process_output": ("run", 7),
    "process_stop": ("run", 7), "memory_search": ("memory", 8), "memory_save": ("memory", 8),
    "calculate": ("math", 8), "create_artifact": ("artifact", 9), "update_artifact": ("artifact", 9),
    "delegate_to_agent": ("team", 10),
}


def _plural(n: int, one: str, many: str = "") -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _times(n: int) -> str:
    return f" ×{n}" if n > 1 else ""


_LABELS = {
    "search": lambda n: f"Searched the web{_times(n)}",
    "pages": lambda n: f"Read {_plural(n, 'page')}",
    "prices": lambda n: f"Checked prices{_times(n)}",
    "images": lambda n: "Found pictures",
    "videos": lambda n: "Found videos",
    "read": lambda n: f"Read {_plural(n, 'file')}",
    "look": lambda n: f"Looked through files{_times(n)}",
    "change": lambda n: f"Changed {_plural(n, 'file')}",
    "run": lambda n: f"Ran {_plural(n, 'command')}",
    "memory": lambda n: "Checked memory",
    "math": lambda n: f"Did {n} calculations" if n > 1 else "Did a calculation",
    "artifact": lambda n: f"Built {n} artifacts" if n > 1 else "Built an artifact",
    "team": lambda n: "Asked a teammate",
}


def bucket_of(name: str) -> str:
    return _BUCKETS.get(name, ("", 0))[0]


def steps_summary(tools: list[dict]) -> str:
    """"Searched the web ×3 · Read 5 pages · Changed 1 file" for finished tools [{name, failed, pages}]."""
    counts: dict[str, int] = {}
    order: dict[str, int] = {}
    other = failed = 0
    changed_paths: set[str] = set()
    for tool in tools:
        if tool.get("failed"):
            failed += 1
        name = str(tool.get("name") or "")
        if name in _BUCKETS:
            key, rank = _BUCKETS[name]
            path = str(tool.get("path") or "")
            # Several edits to one file are one changed file (the preview pane counts files too).
            if key == "change" and path:
                if path in changed_paths:
                    continue
                changed_paths.add(path)
            counts[key] = counts.get(key, 0) + 1
            order[key] = rank
        else:
            other += 1
        pages = int(tool.get("pages") or 0)
        if name == "web_search" and pages:
            counts["pages"] = counts.get("pages", 0) + pages
            order["pages"] = 1
    parts = [_LABELS[k](counts[k]) for k in sorted(counts, key=lambda k: order[k])]
    if other:
        parts.append(_plural(other, "other step"))
    if failed:
        parts.append(f"{failed} failed")
    return " · ".join(parts)


def phase_after(tool_name: str, failed: bool) -> str:
    kind = bucket_of(tool_name)
    if kind in {"search", "pages", "prices", "images", "videos"}:
        return "Looking over the results"
    if kind == "run":
        return "Working out what went wrong" if failed else "Checking the output"
    if kind in {"change", "artifact"}:
        return "Checking the changes"
    if kind in {"read", "look"}:
        return "Reading through it"
    if kind == "team":
        return "Waiting on the team"
    return "Deciding what to do next"


# ── verification wording (agent/lean/verification.py STATUS_LABELS, lower-cased on the work record) ──
VERIFICATION = {
    "verified": "Verified",
    "checks pass": "Checks pass",
    "partly verified": "Partly verified",
    "not verified: the last check failed": "Not verified: the last check failed",
    "not verified": "Not verified",
    "unverified": "Not verified",
    "blocked": "Blocked",
    "observed": "Not verified: steps ran, but no check measured the result",
}
VERIFIED = {"verified", "checks pass"}


def outcome_blocks(*, outcome: Optional[dict], work: Optional[dict], journal: str, success: Optional[bool],
                   error: str, caps_glyphs, denied: bool = False) -> list[B.Block]:
    """The end of a run, stated explicitly. A tool ✓ never implies Done; only the outcome + verification do."""
    g = caps_glyphs
    verification = str((work or {}).get("verification") or "").strip().lower()
    label = VERIFICATION.get(verification, sanitize(verification).capitalize() if verification else "")
    verified = verification in VERIFIED
    if journal in {"cancelled", "interrupted"} or (outcome and outcome.get("status") == "stopped"):
        reason = one_line((outcome or {}).get("reason") or "you stopped it", 300)
        return [B.Outcome(g.stopped, f"Stopped: {reason}", "bold")]
    if denied and success is False and not outcome:
        return [B.Outcome(g.denied, "Stopped: you denied a step it needed", "bold")]
    if journal == "failed" or (success is False and not outcome):
        return [B.Outcome(g.fail, "The run failed" + (f": {one_line(error, 300)}" if error else ""), "error")]
    if outcome and outcome.get("status") == "done":
        summary = one_line(outcome.get("summary") or "Finished.", 400)
        if verified:
            return [B.Outcome(g.done, f"Done: {summary}", "accent", label)]
        return [B.Outcome(g.unverified, f"Done (not verified): {summary}", "bold", label or "Not verified")]
    if work and str(work.get("status") or "") == "completed":
        if verified:
            return [B.Outcome(g.done, f"Done · {label}", "accent")]
        return [B.Outcome(g.unverified, "Finished (not verified)", "bold", label or "Not verified")]
    if work and str(work.get("status") or "") in {"failed", "interrupted"}:
        return [B.Outcome(g.fail, f"The work {work.get('status')}", "error", label)]
    return []


# ── live model ───────────────────────────────────────────────────────
@dataclass
class Tool:
    id: str
    name: str
    label: str
    started: float
    detail: str = ""
    msg: str = ""


@dataclass
class Msg:
    id: str
    agent_id: str
    name: str
    title: str = ""
    role: str = ""
    delegated_by: str = ""
    status: str = "streaming"
    last: str = ""                # "" thinking tool text note approval question approved tool_done tool_failed
    last_tool: str = ""
    thinking_since: Optional[float] = None
    thinking_text: str = ""
    thought_s: float = 0.0
    md: MdStream = field(default_factory=MdStream)
    printed: bool = False         # something of this message is already in scrollback
    bullet_next: bool = True      # the next markdown block gets the "● " bullet
    started: float = 0.0


@dataclass
class Pending:
    """An approval or question waiting for the owner."""
    evt: dict
    verified: bool


class Run:
    def __init__(self, request_id: str, started: float, *, glyphs, session_agent: str = "", verbose: bool = False,
                 tool_lines: int = 3) -> None:
        self.request_id = request_id
        self.started = started
        self.g = glyphs
        self.session_agent = session_agent    # the agent this session talks to ("" = Echo)
        self.verbose = verbose
        self.tool_lines = tool_lines
        self.routing = False
        self.autonomous = False
        self.order: list[str] = []
        self.msgs: dict[str, Msg] = {}
        self.tools: dict[str, Tool] = {}
        self.finished_tools: list[dict] = []
        self.decisions: dict[str, str] = {}   # tool_call_id -> decision
        self.approval_info: dict[str, str] = {}  # approval id -> summary
        self.last_event = started
        self.last_specific = started          # last time the activity changed meaningfully
        self.tokens = 0
        self.context: Optional[tuple[int, int]] = None
        self.outcome: Optional[dict] = None
        self.work: Optional[dict] = None
        self.success: Optional[bool] = None
        self.error = ""
        self.held = 0
        self.approvals: list[Pending] = []    # surfaced to the controller
        self.final_text = ""
        self.stopping = False
        self.last_agent_printed = ""
        self.thought_total = 0.0
        self.transcript: list[B.Block] = []   # full-detail copy for the pager
        self.changes: list[DV.Change] = []    # files this run changed (tool_end meta.file_change), for /diff
        self.approval_changes: dict[str, DV.Change] = {}   # approval id -> the proposed change it showed

    # ── helpers ──────────────────────────────────────────────────────
    def _msg(self, evt: dict) -> Optional[Msg]:
        return self.msgs.get(str(evt.get("message_id") or ""))

    def _end_thinking(self, m: Msg, now: float) -> list[B.Block]:
        if m.thinking_since is None:
            return []
        secs = max(0.0, now - m.thinking_since)
        m.thinking_since = None
        m.thought_s += secs
        self.thought_total += secs
        text, m.thinking_text = m.thinking_text, ""
        if secs < 0.5 and not self.verbose:
            return []
        m.printed = True
        return [B.ThinkingLine(secs, text)]

    def _flush_text(self, m: Msg) -> list[B.Block]:
        return [self._md(m, block) for block in m.md.flush()]

    def _md(self, m: Msg, text: str) -> B.MarkdownBlock:
        bullet = m.bullet_next
        m.bullet_next = False
        m.printed = True
        return B.MarkdownBlock(text, bullet=bullet)

    def _header_needed(self, m: Msg) -> bool:
        if self.last_agent_printed and self.last_agent_printed != m.agent_id:
            return True
        if not self.last_agent_printed:
            # The first speaker gets a header unless it is Echo answering in Echo's own session.
            return not (m.agent_id in ECHO_IDS and self.session_agent in {"", "echo", "chat", "code"}) or bool(m.delegated_by)
        return bool(m.delegated_by)

    # ── reducer ──────────────────────────────────────────────────────
    def feed(self, evt: dict, now: float) -> list[B.Block]:
        try:
            out = self._feed(evt, now)
        except Exception:  # a malformed or new event never breaks the client
            out = []
        self.last_event = now
        # Tool lines add their full-output copy to the transcript themselves (_tool_end).
        self.transcript.extend(b for b in out if not isinstance(b, (B.ToolLine, B.FileDiff)))
        return out

    def _feed(self, evt: dict, now: float) -> list[B.Block]:
        kind = str(evt.get("type") or "")
        g = self.g
        if kind == "run_start":
            self.autonomous = bool(evt.get("autonomous"))
            return []
        if kind == "routing":
            self.routing = True
            self.last_specific = now
            return []
        if kind == "agent_start":
            agent = evt.get("agent") if isinstance(evt.get("agent"), dict) else {}
            mid = str(evt.get("message_id") or "")
            by = evt.get("delegated_by")
            m = Msg(id=mid, agent_id=str(agent.get("id") or evt.get("agent_id") or ""),
                    name=one_line(agent.get("name") or evt.get("agent_id") or "Echo", 40) or "Echo",
                    title=one_line(agent.get("title") or "", 40), role=str(evt.get("role") or ""),
                    delegated_by=one_line(by.get("name") if isinstance(by, dict) else (by or ""), 40), started=now)
            self.msgs[mid] = m
            self.order = [x for x in self.order if x != mid] + [mid]
            self.routing = False
            self.last_specific = now
            out: list[B.Block] = []
            if self._header_needed(m):
                out.append(B.AgentHeader(m.agent_id, m.name, m.title, m.delegated_by))
                m.bullet_next = False
            self.last_agent_printed = m.agent_id
            if evt.get("model_route"):
                out.append(B.Note("·", one_line(evt["model_route"], 300), "dim", 2))
            return out
        m = self._msg(evt)
        if kind == "reasoning_delta":
            if not m:
                return []
            if m.thinking_since is None:
                m.thinking_since = now
                self.last_specific = now
            m.thinking_text += str(evt.get("text") or "")
            m.last = "thinking"
            return []
        if kind == "agent_token":
            if not m:
                return []
            out = self._end_thinking(m, now)
            text = sanitize(evt.get("data"))
            if m.last != "text":
                self.last_specific = now
            m.last = "text"
            return out + [self._md(m, block) for block in m.md.feed(text)]
        if kind == "text_replace":
            if not m:
                return []
            had = m.md.total.strip()
            m.md = MdStream()
            text = sanitize(evt.get("text"))
            m.md.total = text
            if not text.strip():
                return []
            if not had:
                return [self._md(m, text)]
            return [B.Note(g.revised, "Answer revised", "dim", 2), B.MarkdownBlock(text)]
        if kind == "tool_start":
            out: list[B.Block] = []
            if m:
                out += self._end_thinking(m, now) + self._flush_text(m)
                m.last = "tool"
            tid = str(evt.get("id") or "")
            self.tools[tid] = Tool(tid, str(evt.get("name") or "tool"),
                                   one_line(evt.get("label") or evt.get("name") or "tool", 200), now,
                                   msg=m.id if m else "")
            self.last_specific = now
            return out
        if kind == "tool_progress":
            tool = self.tools.get(str(evt.get("id") or ""))
            if tool:
                tool.detail = one_line(evt.get("text"), 120)
            return []
        if kind == "tool_end":
            return self._tool_end(evt, m, now)
        if kind in {"approval_request", "approval_pending"}:
            if m:
                out = self._end_thinking(m, now) + self._flush_text(m)
                m.last = "question" if (evt.get("kind") == "question" or evt.get("question")) else "approval"
            else:
                out = []
            proposed = DV.parse(evt.get("file_change"))
            if proposed is not None:
                self.approval_changes[str(evt.get("id") or "")] = proposed
                if evt.get("tool_call_id"):
                    self.approval_changes[str(evt["tool_call_id"])] = proposed
            self.approvals.append(Pending(evt, verified=not evt.get("_recovered")))
            self.approval_info[str(evt.get("id") or "")] = one_line(evt.get("summary") or evt.get("question") or evt.get("tool"), 160)
            self.last_specific = now
            return out
        if kind == "approval_resolved":
            aid = str(evt.get("id") or "")
            self.approvals = [p for p in self.approvals if str(p.evt.get("id")) != aid]
            decision = str(evt.get("decision") or "")
            if evt.get("tool_call_id"):
                self.decisions[str(evt["tool_call_id"])] = decision
            if m and m.last in {"approval", "question"}:
                m.last = "approved"
            self.last_specific = now
            what = self.approval_info.get(aid, "")
            suffix = f": {what}" if what else ""
            if decision == "answered" or evt.get("answer"):
                return [B.Note(g.steer, f"You answered: {one_line(evt.get('answer'), 300)}", "dim", 2)]
            if decision == "allow":
                return [B.Note(g.ok, f"Allowed{suffix}", "dim", 2)]
            if decision == "deny":
                change = self.approval_changes.get(aid)
                if change is not None:
                    return [B.Note(g.denied, f"Denied {DV.noun(change)} {g.sep} {DV.where(change, _caps_of(g))}", "bold", 2)]
                return [B.Note(g.denied, f"Denied{suffix}", "bold", 2)]
            if decision in {"timeout", "cancelled"}:
                return [B.Note(g.denied, f"Approval {decision}{suffix}", "dim", 2)]
            return []
        if kind == "approval_held":
            self.held += 1
            return [B.Note(g.held, f"Held for you: {one_line(evt.get('tool'), 60)}: {one_line(evt.get('summary'), 200)}",
                           "attention", 2)]
        if kind == "auto_approved":
            return [B.Note(g.ok, one_line(evt.get("note") or "Allowed automatically", 300), "dim", 2)]
        if kind == "steer_applied":
            if m:
                m.last = "note"
            return [B.Note(g.steer, f"You added: {one_line(evt.get('text'), 400)}", "dim", 0)]
        if kind == "delegation":
            return [B.Note(g.handoff, f"{one_line(evt.get('from'), 40)} handed work to {one_line(evt.get('to'), 40)}: "
                                      f"{one_line(evt.get('task'), 300)}", "dim", 0)]
        if kind == "context_compacted":
            return [B.RuleLine("Earlier steps summarized to fit the context window")]
        if kind in {"answer_note", "claim_unverified", "security_note", "verification_nudge"}:
            default = "Outside content contained instructions; they were left out." if kind == "security_note" else "Not verified."
            if kind == "answer_note" and str(evt.get("verification") or "").lower() in {"verified", "checks pass", "checks_pass", "passed"}:
                return [B.Note(self.g.ok, one_line(evt.get("note") or "", 500), "dim", 2)]
            return [B.Note("!", one_line(evt.get("note") or default, 500), "bold", 2)]
        if kind == "verification":
            # The backend's measured check. Its note is written for the model ("Fix the implementation…");
            # the owner gets the first sentence, dim (the full note in verbose mode and the transcript).
            note = one_line(evt.get("note"), 600)
            first = note.split(". ")[0].rstrip(".")
            return [B.Note(g.check, note if self.verbose else first, "dim", 2)]
        if kind == "fail_streak":
            return [B.Note("!", f"{one_line(evt.get('tool'), 60)} failed {one_line(evt.get('count'), 6)} times in a row",
                           "bold", 2)]
        if kind == "memory_saved":
            # The executing memory tool emits this before its tool_end receipt. Show that one
            # durable success row; keep standalone events visible for older backends.
            if any(t.name == "memory_save" for t in self.tools.values()):
                return []
            return [B.Note("·", "Saved to memory", "dim", 2)]
        if kind == "job_continue":
            return [B.Note(g.ellipsis, f"Not done yet: {one_line(evt.get('reason') or 'work remains', 300)}. "
                                       f"{one_line(evt.get('agent') or 'An agent', 40)} continues.", "dim", 0)]
        if kind == "task_board":
            tasks = evt.get("tasks") if isinstance(evt.get("tasks"), list) else []
            return [B.TaskBoard(tasks)] if tasks else []
        if kind == "token_usage":
            try:
                self.tokens += int(evt.get("completion") or 0)
                prompt, limit = int(evt.get("prompt") or 0), int(evt.get("context_limit") or 0)
                if prompt and limit:
                    self.context = (prompt, limit)
            except (TypeError, ValueError):
                pass
            return []
        if kind == "agent_done":
            if not m:
                return []
            out = self._end_thinking(m, now) + self._flush_text(m)
            if evt.get("empty"):
                m.status = "done"
                return out
            done_text = sanitize(evt.get("text"))
            cancelled = str(evt.get("error") or "").strip().lower() in {"cancelled", "canceled"}
            if cancelled and done_text.strip() in {"Stopped.", "Stopped", ""}:
                done_text = ""  # the run's own "■ Stopped" line says it
            if done_text.strip() and not m.md.total.strip():
                out.append(self._md(m, done_text))
                m.md.total = done_text
            m.status = "failed" if evt.get("success") is False and evt.get("error") else "done"
            stop = str(evt.get("stop_reason") or "")
            if stop not in NORMAL_STOPS:
                words = {"max_steps": "stopped at the step limit before finishing"}.get(stop, f"stopped: {one_line(stop, 60)}")
                out.append(B.Note("!", f"{m.name} {words}", "bold", 2))
            if evt.get("success") is False and evt.get("error") and not cancelled:
                out.append(B.Note(g.fail, one_line(evt.get("error"), 400), "error", 2))
            for tool in list(self.tools.values()):
                if tool.msg == m.id:
                    self.tools.pop(tool.id, None)
            if m.md.total.strip():
                self.final_text = m.md.total
            return out
        if kind == "run_outcome":
            self.outcome = {k: evt.get(k) for k in ("status", "summary", "reason")}
            return []
        if kind == "work_state":
            self.work = evt.get("work") if isinstance(evt.get("work"), dict) else None
            return []
        if kind == "final":
            self.success = bool(evt.get("success"))
            if evt.get("response") and not self.final_text:
                self.final_text = sanitize(evt.get("response"))
            return []
        if kind == "error":
            self.error = one_line(evt.get("message") or "error", 400)
            return [B.Note(g.fail, self.error, "error", 0)]
        if kind == "audio_omitted":
            return [B.Note("·", one_line(evt.get("message"), 300), "dim", 2)]
        return []

    def _tool_end(self, evt: dict, m: Optional[Msg], now: float) -> list[B.Block]:
        tid = str(evt.get("id") or "")
        tool = self.tools.pop(tid, None)
        name = str(evt.get("name") or (tool.name if tool else "tool"))
        ok = bool(evt.get("ok"))
        outcome = str(evt.get("outcome") or ("ok" if ok else "failed"))
        decision = self.decisions.get(tid, "")
        if ok:
            status = "ok"
        elif outcome == "held":
            status = "held"
        elif decision in {"deny", "timeout", "cancelled"} or outcome in {"denied", "refused", "blocked", "skipped"}:
            status = "denied"
        else:
            status = "fail"
        label = one_line(evt.get("done_label") or (tool.label if tool else name), 200)
        summary = one_line(evt.get("summary") or "", 160)
        if name == "memory_save" and ok and summary == "Saved":
            summary = "verified on disk" if (evt.get("meta") or {}).get("memory_verified") else ""
        if status == "denied":
            summary = "you denied it" if decision == "deny" else (summary or outcome)
        elif status == "held":
            summary = "held for your OK · /approvals"
        output = sanitize(evt.get("output") or "")
        preview, more = self._preview(name, ok, output) if status not in {"denied", "held"} else ([], 0)
        if status not in {"denied", "held"}:
            meta = evt.get("meta") if isinstance(evt.get("meta"), dict) else {}
            change = meta.get("file_change") if isinstance(meta.get("file_change"), dict) else {}
            self.finished_tools.append({"name": name, "failed": status == "fail", "pages": meta.get("pages", 0),
                                        "path": str(change.get("path") or "")})
        if m:
            m.last = "tool_done" if ok else "tool_failed"
            m.last_tool = name
            m.printed = True
        self.last_specific = now
        if status == "denied" and decision == "deny" and tid in self.approval_changes:
            return []   # "⊘ Denied edit · path" already said it
        meta = evt.get("meta") if isinstance(evt.get("meta"), dict) else {}
        change = DV.parse(meta.get("file_change")) if status == "ok" else None
        if change is not None:
            self.changes.append(change)
            dur = float(evt.get("duration_ms") or 0)
            self.transcript.append(B.FileDiff(change, "ok", dur, full=True))
            return [B.FileDiff(change, "ok", dur, full=self.verbose)]
        line = B.ToolLine(status, label, summary, float(evt.get("duration_ms") or 0), preview, more)
        full = B.ToolLine(status, label, summary, float(evt.get("duration_ms") or 0),
                          [x for x in output.splitlines() if x.strip()][:400], 0)
        self.transcript.append(full)  # the pager shows everything
        if self.verbose:
            return [full]
        return [line]

    def _preview(self, name: str, ok: bool, output: str) -> tuple[list[str], int]:
        lines = [x.rstrip() for x in output.splitlines() if x.strip()]
        kind = bucket_of(name)
        if lines and lines[0].startswith("[") and lines[0].endswith("]") and kind == "run":
            lines = lines[1:]  # the terminal tool's "[ok · 0.0s · this PC · /path]" header
        n = self.tool_lines
        if not ok and lines:
            first_error = next((i for i, x in enumerate(lines) if "error" in x.lower() or "fail" in x.lower()), 0)
            shown = lines[first_error:first_error + n + 1]
            return shown, max(0, len(lines) - len(shown))
        if kind == "run" and lines:
            shown = lines[-n:]
            return shown, max(0, len(lines) - len(shown))
        return [], 0

    # ── queries for the live region ─────────────────────────────────
    def working(self) -> list[Msg]:
        return [self.msgs[i] for i in self.order if i in self.msgs and self.msgs[i].status == "streaming"]

    def needs_ok(self) -> bool:
        return bool(self.approvals)

    def _activity_of(self, m: Msg, now: float) -> str:
        name = m.name or "Echo"
        if m.last == "" and not m.md.total:
            return f"{name} is summarizing" if m.role == "merge" else f"{name} is starting"
        if m.last == "thinking" and m.thinking_since is not None:
            return thinking_words(name, now - m.thinking_since)
        if m.last == "tool":
            running = [t for t in self.tools.values() if t.msg == m.id]
            if running:
                t = running[-1]
                return f"{t.label} · {t.detail}" if t.detail else t.label
        if m.last == "question":
            return f"{name} is asking you something"
        if m.last == "approval":
            return f"{name} needs your OK"
        if m.last == "text":
            return f"{name} is writing the answer"
        if m.last in {"tool_done", "tool_failed"}:
            return f"{name}: {phase_after(m.last_tool, m.last == 'tool_failed')}"
        if m.last == "approved":
            return f"{name}: Carrying on"
        return f"{name} is summarizing" if m.role == "merge" else f"{name} is working"

    def activity(self, now: float) -> str:
        if self.stopping:
            return "Stopping"
        if self.routing and not self.order:
            return "Choosing who should answer"
        all_msgs = [self.msgs[i] for i in self.order if i in self.msgs]
        if not all_msgs:
            return "Connecting to the model"
        working = self.working()
        if len(working) > 1:
            waiting = next((m for m in working if m.last in {"approval", "question"}), None)
            if waiting:
                return self._activity_of(waiting, now)
            names = [m.name or "Echo" for m in working]
            joined = " and ".join(names) if len(names) <= 2 else ", ".join(names[:-1]) + " and " + names[-1]
            return f"{joined} are working"
        current = working[0] if working else all_msgs[-1]
        act = self._activity_of(current, now)
        if current.delegated_by:
            return f"{current.delegated_by} {self.g.handoff} {current.name} · {act}"
        return act

    def running_tools(self) -> list[Tool]:
        return list(self.tools.values())

    def tail(self) -> str:
        working = self.working()
        m = working[-1] if working else None
        return m.md.tail() if m else ""

    def context_pct(self) -> Optional[int]:
        if not self.context:
            return None
        prompt, limit = self.context
        return max(0, min(100, round(100 * prompt / limit))) if limit else None

    def finish(self, journal: str, now: float) -> list[B.Block]:
        """Flush what is open, then the outcome and the turn separator."""
        out: list[B.Block] = []
        for mid in self.order:
            m = self.msgs.get(mid)
            if m:
                out += self._end_thinking(m, now) + self._flush_text(m)
        out += outcome_blocks(outcome=self.outcome, work=self.work, journal=journal, success=self.success,
                              error=self.error, caps_glyphs=self.g,
                              denied=any(d == "deny" for d in self.decisions.values()))
        elapsed = fmt_elapsed(now - self.started)
        summary = steps_summary(self.finished_tools)
        if self.finished_tools:
            out.append(B.RuleLine(f"Worked for {elapsed}" + (f" · {summary}" if summary else ""), gap=True))
        elif journal == "completed" and now - self.started >= 1:
            thought = f" · thought for {fmt_elapsed(round(self.thought_total))}" if self.thought_total >= 1 else ""
            out.append(B.RuleLine(f"Answered in {elapsed}{thought}", gap=True))
        self.transcript.extend(out)
        return out


def _caps_of(glyphs) -> Any:
    from .theme import Caps

    return Caps(glyphs=glyphs)


def tool_input_detail(raw: Any) -> str:
    """A one-line hint from a tool_start ``input`` preview (command, path, query or url)."""
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    for key in ("command", "cmd", "path", "file_path", "query", "q", "url"):
        if data.get(key):
            return one_line(data[key], 120)
    return ""
