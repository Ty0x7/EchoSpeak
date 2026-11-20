"""Pure event -> text reducer. No I/O: ``Renderer.feed(event)`` returns chunks to write verbatim.

Every server-provided string goes through ``sanitize`` before it is placed in output. Our own
styling escapes are added afterwards and only when ``color`` is on (a TTY without NO_COLOR/--plain).
"""
from __future__ import annotations

import json
import re
from typing import Any

# Whole escape sequences first (so they vanish cleanly), then any remaining control byte.
_ESCAPES = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]?"                      # CSI ... final (or truncated)
    r"|\x1b[\]PX^_][^\x07\x1b\x9c]*(?:\x07|\x1b\\|\x9c)?"  # OSC/DCS/SOS/PM/APC ... BEL/ST (or unterminated)
    r"|\x9b[0-?]*[ -/]*[@-~]?"                        # 8-bit CSI
    r"|[\x90\x98\x9d\x9e\x9f][^\x07\x1b\x9c]*(?:\x07|\x1b\\|\x9c)?"  # 8-bit DCS/SOS/OSC/PM/APC
    r"|\x1b[ -/]*[0-~]?"                              # other ESC sequences, lone ESC
)
# C0 except \t and \n, DEL, C1, and bidi overrides/isolates (visual spoofing).
_CONTROLS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f‪-‮⁦-⁩]")


def sanitize(text: Any) -> str:
    """Make untrusted text inert for a terminal: no escape sequences, no control characters.

    A lone CR (which would let text overwrite what came before it on the line) becomes a newline.
    """
    s = "" if text is None else str(text)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = _ESCAPES.sub("", s)
    return _CONTROLS.sub("", s)


_MESSAGE_KEYS = ("detail", "message", "error", "msg", "reason", "description")


def _message_of(value: Any) -> str:
    """The human message in a decoded error body: detail/message/error/msg, nested; validation lists as 'field: msg'."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value[:3]:
            if isinstance(item, dict) and item.get("msg"):
                loc = [str(x) for x in item.get("loc") or [] if str(x) not in {"body", "query", "path"}]
                parts.append((".".join(loc) + ": " if loc else "") + str(item["msg"]))
            else:
                parts.append(_message_of(item))
        return "; ".join(p for p in parts if p)
    if isinstance(value, dict):
        for key in _MESSAGE_KEYS:
            if value.get(key):
                return _message_of(value[key])
    return ""


def plain_error(value: Any) -> str:
    """Error text for people: a JSON error body (whole, or after a prefix such as 'HTTP 500: ') becomes its message.

    The model server and the API answer with JSON ('{"error": "model requires more system memory …"}', FastAPI's
    validation lists), and the terminal used to print it raw (tui-refine open item, 19 November 2026).
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        return _message_of(value) or str(value)
    start = value.find("{") if "{" in value else -1
    bracket = value.find("[") if "[" in value else -1
    if bracket != -1 and (start == -1 or bracket < start):
        start = bracket
    if start == -1:
        return value
    try:
        decoded, end = json.JSONDecoder().raw_decode(value[start:])
    except ValueError:
        return value
    message = _message_of(decoded)
    if not message:
        return value
    return (value[:start] + message + value[start + end:]).strip()


def one_line(text: Any, limit: int = 160) -> str:
    s = " ".join(sanitize(text).split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


class Style:
    def __init__(self, color: bool) -> None:
        self.color = color

    def _wrap(self, code: str, s: str) -> str:
        return f"\x1b[{code}m{s}\x1b[0m" if self.color and s else s

    def dim(self, s: str) -> str:
        return self._wrap("2", s)

    def bold(self, s: str) -> str:
        return self._wrap("1", s)

    def red(self, s: str) -> str:
        return self._wrap("31", s)

    def yellow(self, s: str) -> str:
        return self._wrap("33", s)

    def green(self, s: str) -> str:
        return self._wrap("32", s)

    def cyan(self, s: str) -> str:
        return self._wrap("36", s)


def _duration(ms: Any) -> str:
    try:
        ms = float(ms or 0)
    except (TypeError, ValueError):
        return ""
    if ms <= 0:
        return ""
    return f"{ms:.0f}ms" if ms < 1000 else f"{ms / 1000:.1f}s"


NORMAL_STOPS = {"", "stop", "end_turn", "completed", "done", "finished"}


class Renderer:
    def __init__(self, color: bool = False, show_thinking: bool = False, stream_text: bool = True) -> None:
        self.s = Style(color)
        self.show_thinking = show_thinking
        self.stream_text = stream_text  # False: collect answer text without printing it (send into a pipe)
        self.mid_line = False          # last chunk did not end with a newline
        self.current = ""              # message_id whose text is streaming
        self.names: dict[str, str] = {}
        self.thinking: set[tuple[str, Any]] = set()
        self.tools: dict[str, str] = {}
        self.texts: dict[str, str] = {}
        self.final_text = ""
        self.final_success: bool | None = None
        self.errors: list[str] = []
        self.pending_approvals: dict[str, dict] = {}

    # ── helpers ───────────────────────────────────────────────────────
    def _line(self, text: str) -> list[str]:
        out = ["\n"] if self.mid_line else []
        self.mid_line = False
        self.current = ""
        return out + [text + "\n"]

    def _note(self, text: Any, prefix: str = "· ") -> list[str]:
        return self._line(self.s.dim(prefix + one_line(text, 400)))

    def text_so_far(self) -> str:
        return self.final_text or "\n\n".join(t.strip() for t in self.texts.values() if t.strip())

    # ── the reducer ───────────────────────────────────────────────────
    def feed(self, evt: dict) -> list[str]:
        try:
            return self._feed(evt)
        except Exception:  # forward compatibility: a malformed/new event never crashes the client
            return []

    def _feed(self, evt: dict) -> list[str]:
        kind = str(evt.get("type") or "")
        mid = str(evt.get("message_id") or "")
        if kind == "routing":
            agents = evt.get("agents") or []
            return self._note("routing to " + ", ".join(one_line(a, 40) for a in agents) if agents else "choosing an agent…")
        if kind == "delegation":
            return self._note(f"{one_line(evt.get('from'), 40)} handed work to {one_line(evt.get('to'), 40)}: {one_line(evt.get('task'))}", "→ ")
        if kind == "agent_start":
            agent = evt.get("agent") or {}
            name = one_line(agent.get("name") or evt.get("agent_id") or "agent", 60)
            self.names[mid] = name
            title = one_line(agent.get("title") or "", 40)
            head = self.s.bold(self.s.cyan("● " + name)) + (self.s.dim(f"  {title}") if title else "")
            by = (evt.get("delegated_by") or {}).get("name") if isinstance(evt.get("delegated_by"), dict) else ""
            if by:
                head += self.s.dim(f"  (from {one_line(by, 40)})")
            out = self._line(head)
            if evt.get("model_route"):
                out += self._note(evt["model_route"])
            return out
        if kind == "reasoning_delta":
            key = (mid, evt.get("step"))
            if self.show_thinking:
                out = []
                if key not in self.thinking:
                    self.thinking.add(key)
                    out = self._line(self.s.dim("thinking:"))
                out.append(self.s.dim(sanitize(evt.get("text"))))
                self.mid_line = True
                return out
            if key in self.thinking:
                return []
            self.thinking.add(key)
            return self._note("thinking…", "")
        if kind == "agent_token":
            text = sanitize(evt.get("data"))
            if not text:
                return []
            if not self.stream_text:
                self.texts[mid] = self.texts.get(mid, "") + text
                return []
            out: list[str] = []
            if self.current != mid:
                if self.mid_line:
                    out.append("\n")
                if mid and self.texts.get(mid):
                    out.append(self.s.dim(f"({self.names.get(mid, 'agent')}, continued)") + "\n")
                self.current = mid
            self.texts[mid] = self.texts.get(mid, "") + text
            out.append(text)
            self.mid_line = not text.endswith("\n")
            return out
        if kind == "text_replace":
            text = sanitize(evt.get("text"))
            self.texts[mid] = text
            return self._line(self.s.dim("[answer revised]")) + self._line(text.rstrip("\n"))
        if kind == "tool_start":
            label = one_line(evt.get("label") or evt.get("name") or "tool", 120)
            self.tools[str(evt.get("id") or "")] = label
            return self._line(self.s.dim(f"  ▸ {label}"))
        if kind == "tool_end":
            label = one_line(evt.get("done_label") or self.tools.get(str(evt.get("id") or "")) or evt.get("name") or "tool", 120)
            ok = bool(evt.get("ok"))
            outcome = str(evt.get("outcome") or ("ok" if ok else "failed"))
            bits = [label]
            if evt.get("summary"):
                bits.append(one_line(evt["summary"], 120))
            if outcome not in {"ok", "failed"}:
                bits.append(one_line(outcome, 40))
            if not ok and evt.get("output"):
                bits.append(one_line(evt["output"], 160))
            dur = _duration(evt.get("duration_ms"))
            row = ("  ✓ " if ok else "  ✗ ") + " · ".join(bits) + (f" ({dur})" if dur else "")
            return self._line(self.s.dim(row) if ok and outcome == "ok" else self.s.yellow(row))
        if kind in {"approval_request", "approval_pending"}:
            self.pending_approvals[str(evt.get("id") or "")] = evt
            return self.approval_block(evt)
        if kind == "approval_resolved":
            self.pending_approvals.pop(str(evt.get("id") or ""), None)
            decision = one_line(evt.get("decision") or "", 40)
            answer = f": {one_line(evt.get('answer'), 200)}" if evt.get("answer") else ""
            return self._note(f"approval {decision}{answer}", "  ")
        if kind == "approval_held":
            return self._line(self.s.yellow(f"  ⏸ held for you: {one_line(evt.get('tool'), 60)} — {one_line(evt.get('summary'))}"))
        if kind == "auto_approved":
            return self._note(evt.get("note") or "allowed automatically", "  ")
        if kind == "steer_applied":
            return self._note(f"you added: {one_line(evt.get('text'), 300)}", "  ")
        if kind in {"answer_note", "claim_unverified", "security_note", "verification_nudge"}:
            return self._line(self.s.yellow("  ! " + one_line(evt.get("note") or "not verified", 400)))
        if kind == "verification":
            return self._line(self.s.bold(f"  ≡ verification ({one_line(evt.get('tool'), 40)}): ") + one_line(evt.get("note"), 400))
        if kind == "fail_streak":
            return self._line(self.s.yellow(f"  ! {one_line(evt.get('tool'), 60)} failed {evt.get('count')} times in a row"))
        if kind == "context_compacted":
            return self._note("earlier steps summarized to fit the context window", "  ")
        if kind == "auto_continue":
            return self._note(f"kept going: still making progress (limit now {evt.get('limit')} steps)", "  ")
        if kind == "memory_saved":
            return self._note("memory saved", "  ")
        if kind == "job_continue":
            return self._note(f"not done yet: {one_line(evt.get('reason'))}. {one_line(evt.get('agent') or 'An agent', 40)} continues.")
        if kind == "task_board":
            tasks = evt.get("tasks") if isinstance(evt.get("tasks"), list) else []
            plan = " · ".join(f"{one_line(t.get('owner') or '?', 30)} → {one_line(t.get('task'), 80)}" for t in tasks if isinstance(t, dict))
            return self._note("plan: " + plan) if plan else []
        if kind == "agent_done":
            if evt.get("text"):
                self.texts[mid] = sanitize(evt.get("text"))
            out: list[str] = []
            if self.mid_line:
                out.append("\n")
                self.mid_line = False
            self.current = ""
            stop = str(evt.get("stop_reason") or "")
            if stop not in NORMAL_STOPS:
                out += self._line(self.s.yellow(f"  stopped: {one_line(stop, 60)}"))
            if evt.get("success") is False and evt.get("error"):
                out += self._line(self.s.red(f"  error: {one_line(evt.get('error'), 300)}"))
            return out
        if kind == "run_outcome":
            status = one_line(evt.get("status") or "", 20)
            detail = one_line(evt.get("summary") or evt.get("reason") or "", 400)
            row = f"■ outcome: {status}" + (f" — {detail}" if detail else "")
            return self._line(self.s.bold(self.s.green(row) if status == "done" else self.s.yellow(row)))
        if kind == "final":
            self.final_text = sanitize(evt.get("response"))
            self.final_success = bool(evt.get("success"))
            return []
        if kind == "error":
            msg = one_line(evt.get("message") or "error", 400)
            self.errors.append(msg)
            return self._line(self.s.red("✗ " + msg))
        if kind == "audio_omitted":
            return self._note(evt.get("message"))
        return []  # unknown or uninteresting (step_start, token_usage, status, tool_progress, ...)

    def approval_block(self, evt: dict) -> list[str]:
        """A clearly delimited block. Arguments are shown exactly as the backend exposes them."""
        bar = self.s.yellow("│ ")
        is_q = evt.get("kind") == "question" or bool(evt.get("question"))
        title = "question from the agent" if is_q else f"approval needed: {one_line(evt.get('tool'), 60)}"
        lines = [self.s.yellow(self.s.bold("┌─ " + title))]
        if is_q:
            lines.append(bar + one_line(evt.get("question") or evt.get("summary"), 600))
            for i, opt in enumerate(evt.get("options") or [], 1):
                lines.append(bar + f"  {i}. {one_line(opt, 200)}")
        else:
            if evt.get("summary"):
                lines.append(bar + one_line(evt.get("summary"), 600))
            if evt.get("reason"):
                lines.append(bar + "reason: " + one_line(evt.get("reason"), 600))
            args = evt.get("args")
            if args:
                try:
                    dumped = json.dumps(args, indent=2, ensure_ascii=False, default=str)
                except (TypeError, ValueError):
                    dumped = str(args)
                raw = dumped.splitlines()
                for row in raw[:40]:
                    lines.append(bar + sanitize(row))
                if len(raw) > 40:
                    lines.append(bar + f"… {len(raw) - 40} more lines")
            if evt.get("persistable") and evt.get("scope_label"):
                lines.append(bar + "always-allow scope: " + one_line(evt.get("scope_label"), 200))
        lines.append(self.s.yellow("└─ ") + self.s.dim("id " + one_line(evt.get("id"), 80)))
        out: list[str] = []
        for row in lines:
            out += self._line(row)
        return out
