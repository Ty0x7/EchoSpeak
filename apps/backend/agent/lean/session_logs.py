"""agent_sessions: read the real session logs of coding agents on this computer.

Asked to review what the latest Claude Code and Codex sessions had done, Echo read the project's
documentation and a written summary, and reported from those (conversation audit, 16 November
2026): it said the installer was not built after the AppImage had been installed, and repeated old
test counts as current. The logs themselves were never opened, because nothing told Echo where
they are and they sit outside the folders its file tools may read.

This tool lists and reads those logs directly:

- Claude Code: ``~/.claude/projects/<project>/<session>.jsonl``
- Codex: ``~/.codex/sessions/<yyyy>/<mm>/<dd>/rollout-*.jsonl``

It is read-only, for the owner only, and limited to those two folders. What it returns is a
record of what was said and done, including text the other agent read from the web: it is marked
as outside content (agent/lean/policy.py), so it informs and never instructs. A summary a session
wrote about itself is still a summary; the result says which file it came from and when it was
last written, so a reply can tell "the log shows" from "the notes say".
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Iterator, Optional

NAME = "agent_sessions"
LIST_DEFAULT, LIST_MAX = 8, 25
READ_DEFAULT, READ_MAX = 40, 120
TEXT_LIMIT = 700
OUTPUT_LIMIT = 16000

DESCRIPTION = (
    "Read the actual session logs of the coding agents on this computer (Claude Code and Codex): what the user "
    "asked and what the agent said and did. action 'list' shows recent sessions (newest first); action 'read' "
    "shows the messages of one, newest last. Use this, not notes or documentation, when asked what an agent "
    "session did. Read-only."
)
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["list", "read"], "description": "list recent sessions, or read one."},
        "agent": {"type": "string", "enum": ["any", "claude", "codex"], "description": "Which agent's sessions (default any)."},
        "id": {"type": "string", "description": "For read: the session id from list (or 'latest')."},
        "limit": {"type": "integer", "description": "list: how many sessions. read: how many messages from the end."},
        "query": {"type": "string", "description": "read: only messages containing this text."},
    },
    "required": ["action"],
}


# Credentials pasted into another agent's session (the configured EchoSpeak secrets are already redacted for
# every tool by the toolbox). The log goes to whatever model Echo runs on, which may be a cloud provider.
_SECRET_SHAPES = re.compile(
    r"\b(?:sk-(?:ant-|proj-)?[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"AKIA[0-9A-Z]{16}|xox[abposr]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{30,}|glpat-[A-Za-z0-9_-]{20,})\b"
    r"|(?i:\b(?:bearer|token|api[_-]?key|secret|password|passwd)\b\s*[:=]\s*)[\"']?[^\s\"']{12,}")


def redact(text: str) -> str:
    return _SECRET_SHAPES.sub("[redacted secret]", str(text or ""))


def roots() -> dict[str, Path]:
    home = Path(os.path.expanduser("~"))
    return {"claude": home / ".claude" / "projects", "codex": home / ".codex" / "sessions"}


def _files(agent: str) -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    for name, root in roots().items():
        if agent not in {"any", name} or not root.is_dir():
            continue
        pattern = "*/*.jsonl" if name == "claude" else "**/rollout-*.jsonl"
        for path in root.glob(pattern):
            if path.is_file() and not path.is_symlink():
                found.append((name, path))
    found.sort(key=lambda item: item[1].stat().st_mtime, reverse=True)
    return found


def _rows(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                yield row


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind in {"text", "input_text", "output_text"}:
            parts.append(str(block.get("text") or ""))
        elif kind == "tool_use":
            detail = json.dumps(block.get("input") or {}, default=str)[:200]
            parts.append(f"[tool {block.get('name')}: {detail}]")
        elif kind == "tool_result":
            parts.append("[tool result]")
    return "\n".join(part for part in parts if part)


def messages(agent: str, path: Path) -> list[tuple[str, str, str]]:
    """(time, role, text) for each user and assistant message in a session log, oldest first."""
    out: list[tuple[str, str, str]] = []
    for row in _rows(path):
        when = str(row.get("timestamp") or "")
        if agent == "claude":
            if row.get("type") not in {"user", "assistant"} or row.get("isMeta"):
                continue
            message = row.get("message") or {}
            role, text = str(message.get("role") or row.get("type")), _text_of(message.get("content"))
        else:
            payload = row.get("payload") or {}
            kind = payload.get("type")
            if kind == "message" and payload.get("role") in {"user", "assistant"}:
                role, text = str(payload.get("role")), _text_of(payload.get("content"))
            elif kind in {"function_call", "local_shell_call", "custom_tool_call"}:
                role, text = "assistant", f"[tool {payload.get('name') or kind}: {str(payload.get('arguments') or payload.get('input') or '')[:200]}]"
            else:
                continue
        text = redact(text.strip())
        if text and not text.startswith(("<environment_context>", "<system-reminder>", "<local-command", "<command-")):
            out.append((when, role, text))
    return out


def _first_request(agent: str, path: Path) -> str:
    for _when, role, text in messages(agent, path)[:40]:
        if role == "user":
            return " ".join(text.split())[:140]
    return ""


def _ago(seconds: float) -> str:
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"


def _stamp(path: Path) -> str:
    modified = path.stat().st_mtime
    return f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(modified))} ({_ago(time.time() - modified)})"


def list_sessions(agent: str = "any", limit: int = LIST_DEFAULT) -> str:
    files = _files(agent)[:max(1, min(LIST_MAX, limit))]
    if not files:
        return "No Claude Code or Codex session logs were found on this computer."
    lines = ["Coding-agent session logs on this computer, newest first. Read one with action 'read' and its id."]
    for name, path in files:
        label = "Claude Code" if name == "claude" else "Codex"
        where = path.parent.name if name == "claude" else ""
        lines.append(f"- {label} · id {path.stem} · last written {_stamp(path)}"
                     + (f" · project {where}" if where else "") + f"\n  first request: {_first_request(name, path) or '(none recorded)'}")
    return "\n".join(lines)


def _find(agent: str, ref: str) -> Optional[tuple[str, Path]]:
    files = _files(agent)
    if not files:
        return None
    if ref in {"", "latest", "last", "newest"}:
        return files[0]
    return next(((name, path) for name, path in files if path.stem == ref or path.stem.endswith(ref) or ref in path.stem), None)


def read_session(ref: str, agent: str = "any", limit: int = READ_DEFAULT, query: str = "") -> str:
    hit = _find(agent, str(ref or "").strip())
    if hit is None:
        return f"Error: no session log matches {ref!r}. Use action 'list' to see the ids."
    name, path = hit
    rows = messages(name, path)
    needle = query.strip().casefold()
    if needle:
        rows = [row for row in rows if needle in row[2].casefold()]
    total = len(rows)
    rows = rows[-max(1, min(READ_MAX, limit)):]
    label = "Claude Code" if name == "claude" else "Codex"
    head = (f"{label} session {path.stem}: the session's own log, last written {_stamp(path)}.\n"
            f"File: {path}\nShowing the last {len(rows)} of {total} messages"
            + (f" containing {query!r}" if needle else "") + ". This is the record itself, not a summary of it.")
    body = []
    size = 0
    for when, role, text in reversed(rows):
        entry = f"[{when[:19]}] {role}: {text[:TEXT_LIMIT]}" + ("…" if len(text) > TEXT_LIMIT else "")
        size += len(entry)
        if size > OUTPUT_LIMIT:
            body.append("… earlier messages left out; read again with a query or a smaller limit …")
            break
        body.append(entry)
    return head + "\n\n" + "\n\n".join(reversed(body))


def run(args: dict[str, Any]) -> str:
    action = str(args.get("action") or "list").strip().lower()
    agent = str(args.get("agent") or "any").strip().lower()
    agent = agent if agent in {"any", "claude", "codex"} else "any"
    try:
        limit = int(args.get("limit") or 0)
    except (TypeError, ValueError):
        limit = 0
    if action == "read":
        return read_session(str(args.get("id") or "latest"), agent, limit or READ_DEFAULT, str(args.get("query") or ""))
    return list_sessions(agent, limit or LIST_DEFAULT)


def tool():
    from agent.lean.toolbox import NativeTool

    return NativeTool(NAME, DESCRIPTION, PARAMETERS, run, parallel_safe=True)
