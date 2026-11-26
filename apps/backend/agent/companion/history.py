"""Bounded, read-only reconciliation of authorized local coding-agent session records."""

from __future__ import annotations

import json
import logging
import os
import re
import stat
import threading
import time
from datetime import datetime, timedelta
from itertools import islice
from pathlib import Path

from config import config
from agent.companion import hook_client, install, knowledge
from agent.companion.observe import normalise

logger = logging.getLogger(__name__)
INTERVAL = 90
LOOKBACK = 36 * 3600
TAIL_BYTES = 1024 * 1024
PREFIX_BYTES = 64 * 1024
MAX_FILES = 24
MAX_ENTRIES = 512
_lock = threading.Lock()
_checked_at = float("-inf")
_checked_scope = ()
_seen: dict[Path, tuple] = {}


def _entries(root: Path, pattern: str):
    try:
        if root.is_dir() and root.resolve() == root:
            yield from islice(root.glob(pattern), MAX_ENTRIES)
    except OSError:
        return


def _files(agent: str, home: Path, now: float):
    today = datetime.fromtimestamp(now).date()
    if agent == "codex":
        root = Path(os.environ.get("CODEX_HOME") or home / ".codex").expanduser().absolute() / "sessions"
        for days in range(3):
            date = today - timedelta(days=days)
            yield from _entries(root / date.strftime("%Y/%m/%d"), "rollout-*.jsonl")
    else:
        folders = sorted(_recent(_entries(home / ".claude/projects", "*"), now, directories=True), reverse=True)
        for _, folder in folders[:MAX_FILES]:
            yield from _entries(folder, "*.jsonl")


def _recent(paths, now: float, *, directories: bool = False):
    for path in paths:
        try:
            info = path.lstat()
            valid = stat.S_ISDIR(info.st_mode) if directories else stat.S_ISREG(info.st_mode)
            if valid and info.st_uid == os.getuid() and (directories or info.st_mtime >= now - LOOKBACK):
                yield info.st_mtime, path
        except OSError:
            continue


def _rows(data: bytes):
    for line in data.splitlines():
        try:
            row = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        if isinstance(row, dict):
            yield row


def _time(value) -> float:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError, TypeError, OverflowError):
        return 0.0


def _record(agent, session, cwd, turn, name, at, text, scope, path, *, operation=""):
    raw = {"session_id": session, "cwd": cwd, "turn_id": turn, "hook_event_name": name,
           "transcript_path": str(path)}
    raw["prompt" if name == "UserPromptSubmit" else "last_assistant_message"] = text
    event = normalise({"agent": agent, "received_at": at, "event": hook_client.clean(raw, scope)})
    if event:
        event["meta"].update(via="history", operation=operation)
    return event


def _codex(rows, cwd):
    turn = ""
    for row in rows:
        payload = row.get("payload") or {}
        kind = payload.get("type")
        at = _time(row.get("timestamp"))
        if row.get("type") == "turn_context":
            cwd = payload.get("cwd") or cwd
            turn = payload.get("turn_id") or turn
        elif row.get("type") == "event_msg" and kind == "task_started":
            turn = payload.get("turn_id") or ""
            yield cwd, turn, "ActivityStarted", at, "", ""
        elif row.get("type") == "event_msg" and kind == "user_message":
            yield cwd, turn, "UserPromptSubmit", at, payload.get("message") or "", ""
        elif row.get("type") == "event_msg" and kind in {"task_complete", "turn_aborted"}:
            yield cwd, payload.get("turn_id") or turn, "Stop" if kind == "task_complete" else "Interrupted", at, payload.get("last_agent_message") or "", ""
            turn = ""
        elif turn and row.get("type") == "event_msg" and kind in {"item_started", "item_completed"}:
            item = payload.get("item") or {}
            if item.get("type") == "UserMessage" and kind == "item_completed":
                blocks = item.get("content") or []
                prompt = "\n".join(b.get("text", "") for b in blocks if b.get("type", "").lower() in {"text", "input_text"})
                if prompt:
                    yield cwd, turn, "UserPromptSubmit", at, prompt, ""
            elif item.get("type") in {"CommandExecution", "commandExecution", "FileChange", "fileChange"}:
                operation = _operation(item.get("command")) if "command" in item["type"].lower() else "a file change"
                if kind == "item_completed" and (item.get("exit_code") not in (None, 0)
                                                 or item.get("status") in {"failed", "declined"}):
                    operation = ""
                yield cwd, str(item.get("id") or at), "OperationDone" if kind == "item_completed" else "OperationStarted", at, "", operation


def _operation(command) -> str:
    # Persist only the category. Discard arguments, paths, command text and outputs.
    if re.search(r"\b(pytest|unittest|vitest|jest|ctest)\b|\b(cargo|npm|go)\s+test\b", str(command or "")):
        return "a test command"
    return "a command"


def _claude(rows, session):
    tools = {}
    for row in rows:
        if row.get("sessionId") != session or row.get("isSidechain"):
            continue
        cwd, ident = row.get("cwd"), row.get("uuid")
        if not cwd or not ident:
            continue
        at = _time(row.get("timestamp"))
        message = row.get("message") or {}
        blocks = message.get("content") or []
        blocks = [{"text": blocks, "type": "text"}] if isinstance(blocks, str) else blocks
        text = "\n".join(b["text"] for b in blocks if b.get("type") == "text" and isinstance(b.get("text"), str))
        if row.get("type") == "user" and not row.get("isMeta") and text and not text.lstrip().startswith("<"):
            yield cwd, ident, "UserPromptSubmit", at, text, ""
        elif row.get("type") == "assistant" and message.get("stop_reason") == "end_turn" and text:
            yield cwd, ident, "Stop", at, text, ""
        elif row.get("type") == "assistant":
            for block in blocks:
                if block.get("type") == "tool_use" and block.get("name") in {"Bash", "Edit", "Write", "MultiEdit"}:
                    operation = _operation((block.get("input") or {}).get("command")) if block["name"] == "Bash" else "a file change"
                    tools[block.get("id")] = operation
                    yield cwd, str(block.get("id") or ident), "OperationStarted", at, "", operation
        elif row.get("type") == "user":
            for block in blocks:
                operation = tools.pop(block.get("tool_use_id"), "") if block.get("type") == "tool_result" else ""
                if operation and not block.get("is_error"):
                    yield cwd, ident, "OperationDone", at, "", operation


def read_events(path: Path, agent: str, scope: dict, now: float, eligible):
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            return
        prefix = list(_rows(stream.read(PREFIX_BYTES)))
        if agent == "codex":
            meta = next((r.get("payload", {}) for r in prefix if r.get("type") == "session_meta"), {})
            session, cwd = meta.get("id"), meta.get("cwd")
        else:
            meta = next((r for r in prefix if r.get("sessionId") == path.stem and r.get("cwd")), {})
            session, cwd = meta.get("sessionId"), meta.get("cwd")
        if not session or not eligible(agent, cwd):
            return
        offset = max(0, info.st_size - TAIL_BYTES)
        stream.seek(offset)
        data = stream.read(TAIL_BYTES)
    if offset:
        data = data.partition(b"\n")[2]
    rows = _rows(data)
    parsed = _codex(rows, cwd) if agent == "codex" else _claude(rows, session)
    for observed_cwd, turn, name, at, text, operation in parsed:
        if at < now - LOOKBACK or at > now + 30 or not isinstance(text, str):
            continue
        # A move may be observed inside the same authorized project. It cannot transfer the
        # original session's contents into a different project's archive or prompt.
        if eligible(agent, observed_cwd) != eligible(agent, cwd):
            continue
        event = _record(agent, session, cwd, turn, name, at, text, scope, path, operation=operation)
        if event:
            yield event


def refresh(store, *, force: bool = False, now: float | None = None) -> int:
    global _checked_at, _checked_scope
    if not _lock.acquire(blocking=False):
        return 0
    try:
        tick = time.monotonic()
        scope = install.read_scope()
        if not config.companion_recover_history or not scope["enabled"] or not scope["roots"]:
            _seen.clear()
            _checked_scope = ()
            return 0
        stamp_scope = (tuple(scope["roots"]), tuple(scope["agents"].items()), scope["capture_prompts"])
        if not force and stamp_scope == _checked_scope and tick - _checked_at < INTERVAL:
            return 0
        _checked_at, _checked_scope = tick, stamp_scope
        now = time.time() if now is None else now
        projects = {}

        def eligible(agent, cwd):
            if not cwd or not hook_client.allowed(agent, cwd, scope):
                return ""
            if cwd not in projects:
                projects[cwd] = knowledge._project_for(cwd)[0]
            return projects[cwd]

        current = {}
        added = 0
        for agent, enabled in scope["agents"].items():
            if not enabled:
                continue
            paths = sorted(_recent(_files(agent, Path.home(), now), now), reverse=True)[:MAX_FILES]
            for _, path in paths:
                try:
                    attrs = path.stat()
                    stamp = (attrs.st_mtime_ns, attrs.st_size, stamp_scope)
                    if not force and stamp == _seen.get(path):
                        current[path] = stamp
                    else:
                        events = list(read_events(path, agent, scope, now, eligible))
                        if events:
                            current[path] = stamp
                        # Operations already behind the freshness window do not inform current activity.
                        durable = [e for e in events if e["kind"] not in {"operation_started", "operation_done"}]
                        operations = [e for e in events if e["kind"] in {"operation_started", "operation_done"}]
                        recent = [e for e in operations if now - e["at"] <= 180]
                        reports = [e for e in durable if e["kind"] == "turn_completed" and e["text"]]
                        selected = {e["id"]: e for e in durable[-8:] + reports[-4:] + operations[-1:] + recent[-4:]}
                        for event in sorted(selected.values(), key=lambda e: e["at"]):
                            added += store.add(event)
                except (OSError, ValueError, TypeError, AttributeError):
                    logger.debug("Skipping unreadable Companion history", exc_info=True)
        _seen.clear()
        _seen.update(current)
        return added
    finally:
        _lock.release()
