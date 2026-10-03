"""When is the job done? Completion tracking for multi-agent work.

A reply with no tool calls ends an agent's *turn*: it has the floor no more.
It does not end the *job*. The job is the user's message in a group chat, or
any request where work was handed to a teammate. It ends only when:

- an agent calls ``complete_task`` with a summary and a completion check agrees, or
- the orchestrator's completion check finds the request answered, or
- a backstop trips: too many rounds, the token budget, agents repeating
  themselves, or rounds that make no progress. Then it stops with a visible reason.

Completion is judged from evidence, not from what agents say: every tool call
an agent makes is recorded (what ran, whether it worked), the task board shows
who owns what and its state, and the completion check reads both. A task
marked done by an agent that never touched a tool, when the task needed real
work, stays open. The design follows Magentic-One's progress ledger (is the
request satisfied, are we looping, is progress being made, who is next) and
OpenHands' rule-based stuck detection; see docs/research/harness-review.md.

Nothing ends silently: every job closes with an outcome the chat shows as
"Done: <summary>" or "Stopped: <reason>".
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Optional

# Phrases that announce work instead of doing it. Matched only on short replies
# that end the turn without a tool call or handoff.
_PROMISE = re.compile(
    r"""(?ix)
    # First person, anywhere in the reply: "Sure, I'll write it", "Let me check".
    (^|[\s,.!])(
        i'?ll\s+(?:do|handle|take|get|start|write|make|build|fix|look|check|run|create|update|set|work|go|put|add|have)\b|
        i\s+will\s+(?:do|handle|take|get|start|write|make|build|fix|look|check|run|create|update|set|work|go|put|add|have)\b|
        let\s+me\s+(?:do|handle|take|get|start|write|make|build|fix|look|check|run|create|update|set|see|find|grab|pull|try|put|add)\b|
        i'?m\s+(?:going\s+to|gonna|on\s+it|starting|working\s+on)\b|
        leave\s+it\s+(?:to|with)\s+me\b|
        give\s+me\s+a\s+(?:sec|second|moment|minute)\b
    )
    # Bare acknowledgements only open a reply ("On it.", "Will do!"): in the middle
    # they describe someone else ("Glados is on it").
    |^\W*(?:(?:sure|ok(?:ay)?|yes|yep|yeah|alright|got\s+it|no\s+problem)\W+)*
        (?:on\s+it|will\s+do|consider\s+it\s+done|working\s+on\s+it|coming\s+(?:right\s+)?up)\b
    """
)


def is_promise_without_action(text: str) -> bool:
    """A short reply that commits to doing something now, rather than a result.

    Long answers that happen to contain "I'll" are results, and a question
    back to the user is a legitimate way to end a turn.
    """
    body = str(text or "").strip()
    if not body or len(body) > 400:
        return False
    if body.rstrip().endswith("?"):
        return False
    return bool(_PROMISE.search(body))


def _fingerprint(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9']+", str(text or "").lower())
    return {" ".join(words[i:i + 2]) for i in range(max(0, len(words) - 1))} or set(words)


def near_duplicate(a: str, b: str, threshold: float = 0.85) -> bool:
    fa, fb = _fingerprint(a), _fingerprint(b)
    if len(fa) < 4 or len(fb) < 4:
        return False
    return len(fa & fb) / len(fa | fb) >= threshold


_ACTION_WORDS = re.compile(
    r"\b(write|wrote|create|make|build|add|edit|fix|change|update|install|run|execute|test|delete|remove|"
    r"rename|move|copy|save|send|post|download|deploy|commit|generate|refactor|implement|configure|set\s+up)\b",
    re.I,
)


def needs_action(task: str) -> bool:
    """Whether a task asks for real work (files, commands, changes), not just an answer."""
    return bool(_ACTION_WORDS.search(str(task or "")))


@dataclass
class Evidence:
    """One tool call made while working on the job: what ran and whether it worked."""
    agent: str
    tool: str
    label: str
    ok: bool
    output: str = ""


@dataclass
class Subtask:
    id: str
    owner: str  # agent name
    task: str
    assigned_by: str
    status: str = "open"  # open | done | failed
    summary: str = ""
    # Tool calls the owner made while working on it: all of them, and the ones that worked.
    attempts: int = 0
    actions: int = 0


@dataclass
class Job:
    goal: str
    max_rounds: int = 4
    token_budget: int = 200_000
    # Stop when this many verification rounds in a row brought nothing new.
    max_stalls: int = 2
    subtasks: list[Subtask] = field(default_factory=list)
    claim: Optional[dict[str, str]] = None  # {"by": name, "summary": ...} from complete_task
    outcome: Optional[dict[str, str]] = None  # {"status": "done"|"stopped", "summary"|"reason"}
    rounds: int = 0
    tokens: int = 0
    texts: list[tuple[str, str]] = field(default_factory=list)  # (agent name, text) spoken this job
    evidence: list[Evidence] = field(default_factory=list)
    repeats: int = 0
    stalls: int = 0
    _progress_mark: tuple[int, int] = (0, 0)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # ── recording ───────────────────────────────────────────────────────
    def open_subtask(self, *, owner: str, task: str, assigned_by: str) -> Subtask:
        with self._lock:
            item = Subtask(id=f"t{len(self.subtasks) + 1}", owner=owner, task=task.strip()[:600], assigned_by=assigned_by)
            self.subtasks.append(item)
            return item

    def close_subtask(self, item: Subtask, *, done: bool, summary: str = "") -> None:
        with self._lock:
            item.status = "done" if done else "failed"
            item.summary = summary.strip()[:800]

    def record_tool(self, agent: str, tool: str, label: str, ok: bool, output: str = "") -> None:
        """A tool call made on this job. Read by the completion check and the progress detector."""
        with self._lock:
            self.evidence.append(Evidence(agent, tool, str(label or tool)[:200], bool(ok), " ".join(str(output or "").split())[:240]))

    def successful_actions(self) -> int:
        return sum(1 for item in self.evidence if item.ok)

    def claim_complete(self, by: str, summary: str) -> None:
        with self._lock:
            self.claim = {"by": by, "summary": summary.strip()[:800]}

    def record_text(self, agent: str, text: str, tokens: int = 0) -> None:
        """Count tokens and notice when agents start repeating each other."""
        with self._lock:
            self.tokens += max(0, int(tokens or 0))
            body = str(text or "").strip()
            if not body:
                return
            if any(near_duplicate(body, earlier) for _, earlier in self.texts):
                self.repeats += 1
            self.texts.append((agent, body))

    def open_subtasks(self) -> list[Subtask]:
        return [s for s in self.subtasks if s.status == "open"]

    # ── backstops ───────────────────────────────────────────────────────
    def note_progress(self) -> bool:
        """Call once per verification round. True if anything moved since the last call.

        Progress is a new successful tool call or a task newly finished. Talk alone
        is not progress: that is what turns a group into a discussion loop.
        """
        with self._lock:
            # New tasks are not progress: a checker that keeps adding them would never stall.
            mark = (
                sum(1 for item in self.evidence if item.ok),
                sum(1 for item in self.subtasks if item.status == "done"),
            )
            moved = mark != self._progress_mark
            self._progress_mark = mark
            self.stalls = 0 if moved else self.stalls + 1
            return moved

    def backstop(self) -> str:
        """A reason to stop now, or ''."""
        if self.repeats >= 2:
            return "the agents started repeating themselves"
        if self.token_budget and self.tokens >= self.token_budget:
            return f"it used its token budget ({self.tokens:,} of {self.token_budget:,})"
        if self.max_stalls and self.stalls >= self.max_stalls:
            return f"{self.stalls} rounds in a row made no progress (no tool succeeded and no task was finished)"
        if self.rounds >= self.max_rounds:
            return f"it reached the limit of {self.max_rounds} rounds"
        return ""

    def finish(self, summary: str) -> dict[str, str]:
        self.outcome = {"status": "done", "summary": summary.strip()[:600] or "Finished."}
        return self.outcome

    def stop(self, reason: str) -> dict[str, str]:
        self.outcome = {"status": "stopped", "reason": reason.strip()[:600]}
        return self.outcome

    # ── completion check ────────────────────────────────────────────────
    def transcript(self, limit: int = 6000) -> str:
        lines = [f"[{who}]: {text}" for who, text in self.texts]
        for item in self.subtasks:
            lines.append(f"(task for {item.owner} from {item.assigned_by}: {item.task} -> {item.status}"
                         + (f": {item.summary}" if item.summary else "") + ")")
        body = "\n\n".join(lines)
        return body[-limit:]

    def task_board(self) -> str:
        """Shared state every agent working on the job sees: who owns what, and where it stands."""
        if not self.subtasks:
            return ""
        lines = []
        for item in self.subtasks:
            line = f"- {item.id} [{item.status}] {item.owner}: {item.task[:240]}"
            if item.summary:
                line += f" -> {item.summary[:200]}"
            lines.append(line)
        return "\n".join(lines)

    def evidence_log(self, limit: int = 30) -> str:
        """What was actually run, newest last. The ground truth for "is it done?"."""
        rows = self.evidence[-limit:]
        if not rows:
            return "(no tool calls yet: nothing has been done beyond talking)"
        lines = [f"- {e.agent}: {e.label} -> {'ok' if e.ok else 'FAILED'}" + (f": {e.output[:160]}" if e.output else "") for e in rows]
        if len(self.evidence) > limit:
            lines.insert(0, f"(…{len(self.evidence) - limit} earlier tool calls not shown)")
        return "\n".join(lines)

    def unbacked_tasks(self) -> list[Subtask]:
        """Tasks marked done that needed real work, by an owner who made no successful tool call."""
        return [s for s in self.subtasks if s.status == "done" and s.actions == 0 and needs_action(s.task)]

    def review_prompt(self, members: list[str]) -> str:
        claim = (f"{self.claim['by']} says it is complete: {self.claim['summary']}\n\n" if self.claim else "")
        board = self.task_board()
        stalled = (f"Note: the last {self.stalls} round(s) made no progress. If the approach is stuck, "
                   "give a different instruction or a different agent.\n\n" if self.stalls else "")
        return (
            "You check whether a team of AI agents has finished what the user asked. Judge from what was "
            "actually done: the tool log shows what really ran and whether it worked. A promise, a plan, or an "
            "agent saying \"done\" is not done. If the request needed work (files, commands, changes), it is done "
            "only when the tool log shows that work succeeding. A plain question is done when it was answered.\n\n"
            f"User's request:\n{self.goal[:2000]}\n\n"
            + (f"Task board:\n{board}\n\n" if board else "")
            + f"Tool log (what actually ran):\n{self.evidence_log()}\n\n"
            f"Transcript of this request:\n{self.transcript()}\n\n{claim}{stalled}"
            f"Agents: {', '.join(members)}\n\n"
            'Answer with JSON only: {"done": true|false, "summary": "what was delivered, one sentence", '
            '"reason": "if not done: what is missing", "in_loop": true|false, '
            '"next": "agent who should continue", "instruction": "the concrete next action for them"}'
        )


def parse_review(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", str(text or ""), re.S)
    if not match:
        return {}
    raw = match.group(0)
    for attempt in (raw, re.sub(r",\s*([}\]])", r"\1", raw)):
        try:
            data = json.loads(attempt)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            continue
    return {}


PROMISE_NUDGE = (
    "You said you would do it, but you made no tool call and handed nothing off, so nothing has happened yet. "
    "Do it now: call the tool(s) that do the work, or hand it to the right teammate with delegate_to_agent. "
    "If you can't do it, say why in one line."
)

ASSIGN_TASKS_DESCRIPTION = (
    "Turn the plan into work: give each task to the teammate who should do it (you can include yourself). "
    "Each task must be a concrete action with a checkable result, e.g. \"write hello.py that prints hi and run it\". "
    "Tasks go on the shared task board and each owner does theirs with their own tools. "
    "If the user only asked a question, don't assign tasks; just answer it."
)

COMPLETE_TASK_DESCRIPTION = (
    "Call this when the task you were given is finished, with a short summary of what you delivered "
    "(files written, answers found, actions taken). This is how the work is marked done: saying "
    "\"I'll do it\" or \"done\" in text does not count. Don't call it to report a plan or a partial result."
)
