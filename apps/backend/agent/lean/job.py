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


# Claims that something was already done ("I've saved it", "has been updated").
_DONE_VERBS = (r"saved|updated|added|wrote|written|created|deleted|removed|sent|stored|changed|edited|installed|"
               r"committed|recorded|renamed|moved|copied|scheduled|posted|uploaded|downloaded")
_ACTION_CLAIM = re.compile(
    rf"""(?ix)
    \b(?:i'?ve|i\s+have|i\s+just|i'?ve\s+just|i\s+(?:now\s+)?(?:also\s+)?)\s*(?:now\s+|also\s+|successfully\s+)?(?:{_DONE_VERBS})\b
    | \b(?:has|have)\s+(?:now\s+)?been\s+(?:successfully\s+)?(?:{_DONE_VERBS})\b
    | \b(?:is|are)\s+now\s+(?:saved|stored|updated|in\s+(?:my|your)\s+(?:soul|memory))\b
    | \b(?:it'?s|that'?s|it\s+is|that\s+is)\s+(?:now\s+|all\s+)?(?:saved|stored|updated|added|in\s+(?:my|your)\s+(?:soul|memory))\b
    | ^\W*(?:saved|updated|done)\W+(?:to|in)\s+(?:my\s+|your\s+)?(?:memory|soul)\b
    """
)
# Promises to keep something beyond this chat: only a memory or soul write does that.
_PERSIST_CLAIM = re.compile(
    r"(?i)\b(?:i'?ll|i\s+will|i'?m\s+going\s+to)\s+(?:always\s+)?(?:remember|keep\s+(?:that|this|it)\s+in\s+mind)\b"
    r"|\b(?:noted|saved)\s+(?:that\s+)?(?:for\s+(?:next\s+time|the\s+future|future\s+chats))\b"
    r"|\bfrom\s+now\s+on,?\s+i'?ll\b"
)
# Talking about an earlier turn ("I saved that yesterday") is not a claim about this one.
_EARLIER = re.compile(r"(?i)\b(earlier|before|yesterday|last\s+time|previously|already\s+(?:had|did))\b")
PERSIST_TOOLS = {"memory_save", "soul_update"}
# Tools that only read: a successful call doesn't back a claim that something changed.
READ_ONLY_TOOLS = {
    "get_system_time", "calculate", "system_info", "file_list", "file_read", "file_find", "file_search",
    "web_search", "safe_web_fetch", "youtube_transcript", "weather_live", "sports_live", "project_status",
    "memory_search", "chat_search", "email_read_inbox", "email_search", "email_get_thread", "discord_read_channel",
    "self_list", "self_read", "self_grep", "self_git_status", "desktop_list_windows", "process_output",
    "stock_history", "product_search", "video_search", "image_search", "take_screenshot", "analyze_screen", "vision_qa",
}


def unbacked_claim(text: str, succeeded: set[str]) -> str:
    """What the reply claims was done that no successful tool call this turn did, or ''.

    ``succeeded`` is the names of tools that worked in this turn. A claim about
    the soul needs soul_update; about memory or remembering, memory_save or
    soul_update; any other "I've saved/sent/created…" needs some tool that
    changes things (or a teammate's handoff).
    """
    body = str(text or "")
    if not body.strip():
        return ""
    sentences = [s for s in re.split(r"(?<=[.!?])\s+|\n+", body) if s.strip() and not _EARLIER.search(s)]
    claims = [s for s in sentences if _ACTION_CLAIM.search(s) or _PERSIST_CLAIM.search(s)]
    if not claims:
        return ""
    # Bookkeeping (marking done, assigning) changes nothing the claim could be about.
    changed = {name for name in succeeded if name not in READ_ONLY_TOOLS and name not in {"complete_task", "assign_tasks"}}
    for claim in claims:
        low = claim.lower()
        if re.search(r"\b(soul|personality)\b", low):
            if "soul_update" not in changed:
                return claim.strip()
        elif _PERSIST_CLAIM.search(claim) or re.search(r"\b(memory|memories|remember)\b", low):
            if not changed & PERSIST_TOOLS:
                return claim.strip()
        elif not changed:
            return claim.strip()
    return ""


# The check step: code changed in this reply must be run before it's called done.
CHANGE_TOOLS = {"file_write", "file_edit", "file_move", "file_copy"}
CHECK_TOOLS = {"terminal", "terminal_run", "process_start"}
CODE_SUFFIXES = (
    ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".html", ".css", ".scss", ".vue", ".svelte",
    ".rs", ".go", ".java", ".kt", ".c", ".h", ".cpp", ".hpp", ".cs", ".rb", ".php", ".swift",
    ".sh", ".ps1", ".bat", ".sql", ".toml", ".gradle",
)


def changed_code_file(name: str, args: dict) -> str:
    """The code file a successful change tool touched, or ''."""
    if name not in CHANGE_TOOLS:
        return ""
    path = str(args.get("destination") or args.get("path") or args.get("file_path") or "").strip()
    return path if path.lower().endswith(CODE_SUFFIXES) else ""


def check_nudge(files: list[str]) -> str:
    shown = ", ".join(files[:4]) + (f" and {len(files) - 4} more" if len(files) > 4 else "")
    return (
        f"You changed {shown} but haven't run anything to check it since. Before you say it's done, run it or its "
        "tests with the terminal and look at the result. If it can't be run here, say plainly that it isn't checked "
        "and why. If the check fails, fix it or say what's left."
    )


def claim_nudge(claim: str) -> str:
    return (
        f"You wrote \"{claim[:200]}\", but no tool call in this turn did that, so it isn't true yet. "
        "Either do it now with the right tool (memory_save for facts about the user, soul_update for how you "
        "behave, or the tool that does the action), or rewrite your reply so it only says what actually "
        "happened. If a tool failed, say it failed."
    )


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


# Hard ceiling on rounds for one message, even while work keeps progressing.
ROUND_CEILING = 60


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
    _progress_mark: tuple[int, int, int] = (0, 0, 0)
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
    def note_progress(self, *, baseline: bool = False) -> bool:
        """Call once per verification round. True if anything moved since the last call.

        ``baseline`` only records where things stand (e.g. once the plan is made),
        so a round in which nobody was asked to act yet is not counted as a stall.

        Progress is measured in what the goal needs. For work (files, commands,
        changes) it is a new successful tool call or a task newly finished: talk
        alone is not progress, which is what turns a group into a discussion loop.
        For a question, a new answer that doesn't repeat an earlier one also counts;
        the repeat detector and the round limit bound that.
        """
        with self._lock:
            # New tasks are not progress: a checker that keeps adding them would never stall.
            mark = (
                sum(1 for item in self.evidence if item.ok),
                sum(1 for item in self.subtasks if item.status == "done"),
                0 if needs_action(self.goal) else len(self.texts) - self.repeats,
            )
            moved = mark != self._progress_mark
            self._progress_mark = mark
            if not baseline:
                self.stalls = 0 if moved else self.stalls + 1
            return moved

    def backstop(self) -> str:
        """A reason to stop now, or ''."""
        if self.repeats >= 2:
            return "the agents started repeating themselves"
        if self.token_budget and self.tokens >= self.token_budget:
            return (f"it used its token budget ({self.tokens:,} new tokens of {self.token_budget:,}). "
                    "Press Continue to keep going from where it stopped")
        if self.max_stalls and self.stalls >= self.max_stalls:
            return f"{self.stalls} rounds in a row made no progress (no tool succeeded and no task was finished)"
        if self.rounds >= ROUND_CEILING:
            return f"it reached {ROUND_CEILING} rounds, the most one message may run"
        # Work (files, commands, builds) keeps going while every round gets something done:
        # a team building a game shouldn't stop halfway and wait for "continue". Questions and
        # discussions can "progress" forever with new words, so the round limit still holds there.
        working = needs_action(self.goal) and self.stalls == 0
        if self.rounds >= self.max_rounds and not working:
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
            f"Agents (pick \"next\" by name): {'; '.join(members)}\n\n"
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


def promise_nudge(tool_names: list[str]) -> str:
    """Ask for the action, naming only a handoff tool this agent actually has."""
    handoff = next((name for name in ("assign_tasks", "delegate_to_agent") if name in tool_names), "")
    return (
        "You said you would do it, but you made no tool call and handed nothing off, so nothing has happened yet. "
        "Do it now: call the tool(s) that do the work"
        + (f", or hand it to the right teammate with {handoff}" if handoff else "")
        + ". If you can't do it, say why in one line."
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
