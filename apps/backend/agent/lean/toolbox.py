"""Toolsets and tool execution for the lean loop.

Like Hermes, an agent sees a fixed, named set of tools for the whole turn.
Nothing is re-selected per message, so a tool the agent needs halfway
through a task is always there. Small local models get compact schemas.
"""

from __future__ import annotations

import contextvars
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from loguru import logger

from config import config

TOOLSETS: dict[str, list[str]] = {
    "core": [
        "get_system_time", "calculate", "system_info",
        "file_list", "file_read", "file_find", "file_search", "file_edit", "file_write",
        "file_mkdir", "file_copy", "file_move", "file_delete", "checkpoint_undo", "artifact_write",
        "project_status", "project_update_context",
    ],
    "research": [
        "web_search", "safe_web_fetch", "youtube_transcript",
        "weather_live", "sports_live", "browse_task",
    ],
    "terminal": ["terminal", "process_start", "process_output", "process_stop"],
    "vision": ["take_screenshot", "analyze_screen", "vision_qa"],
    "desktop": [
        "open_application", "open_chrome", "notepad_write",
        "desktop_list_windows", "desktop_find_control", "desktop_activate_window",
        "desktop_click", "desktop_type_text", "desktop_send_hotkey",
    ],
    "comms": [
        "email_read_inbox", "email_search", "email_get_thread", "email_send", "email_reply",
        "discord_read_channel", "discord_send_channel", "discord_web_read_recent",
        "discord_web_send", "discord_contacts_add", "discord_contacts_discover",
    ],
    "self": ["self_list", "self_read", "self_grep", "self_git_status", "self_edit", "self_rollback"],
    "memory": ["memory_save", "memory_search"],
    # Skill, MCP, and Connection tools that registered at runtime.
    "skills": ["@external"],
}

DEFAULT_TOOLSETS = ["core", "research", "terminal", "vision", "memory", "skills"]

# Read-only tools can run in parallel when the model asks for several at once.
PARALLEL_SAFE = {
    "get_system_time", "calculate", "system_info", "file_list", "file_read", "file_find", "file_search",
    "web_search", "safe_web_fetch", "youtube_transcript", "weather_live",
    "sports_live", "project_status", "memory_search", "email_read_inbox",
    "email_search", "email_get_thread", "discord_read_channel", "self_list",
    "self_read", "self_grep", "self_git_status", "desktop_list_windows",
}

_FAILURE_PREFIXES = (
    "refused", "[exit code", "[timed out",
    "failed", "error", "path not allowed", "cwd not allowed", "command rejected",
    "command blocked", "tool '", "file write is disabled", "terminal commands are disabled",
    "rejected", "mutation blocked", "playwright error", "unsupported", "exitcode=127",
)


@dataclass
class ToolResult:
    ok: bool
    output: str
    duration_ms: int


@dataclass
class NativeTool:
    """A lean-runtime tool implemented here instead of in ToolRegistry."""

    name: str
    description: str
    parameters: dict[str, Any]
    func: Callable[[dict[str, Any]], str]
    parallel_safe: bool = False


def _flag_enabled(flag: str) -> bool:
    return bool(getattr(config, str(flag).lower(), False))


def _compact_description(text: str, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    period = cut.rfind(". ")
    return (cut[: period + 1] if period > 80 else cut.rstrip() + "…")


def _compact_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Trim a JSON schema to what a small model needs to call the tool."""
    properties = {}
    for key, raw in dict(schema.get("properties") or {}).items():
        prop = dict(raw or {})
        clean: dict[str, Any] = {}
        if "anyOf" in prop and "type" not in prop:
            types = [item.get("type") for item in prop["anyOf"] if isinstance(item, dict) and item.get("type") not in (None, "null")]
            if types:
                clean["type"] = types[0]
        for field in ("type", "enum", "items", "default"):
            if field in prop:
                clean[field] = prop[field]
        if prop.get("description"):
            clean["description"] = _compact_description(prop["description"], 140)
        clean.setdefault("type", "string")
        properties[key] = clean
    compact: dict[str, Any] = {"type": "object", "properties": properties}
    required = [item for item in list(schema.get("required") or []) if item in properties]
    if required:
        compact["required"] = required
    return compact


class Toolbox:
    def __init__(
        self,
        *,
        toolsets: Optional[list[str]] = None,
        extra_tools: Optional[list[NativeTool]] = None,
        session_id: str = "default",
        project_root: str = "",
    ) -> None:
        from agent.tool_registry import ToolRegistry

        self.session_id = session_id
        self.project_root = project_root
        self.notes: list[str] = []
        self.native: dict[str, NativeTool] = {tool.name: tool for tool in (extra_tools or [])}
        wanted: list[str] = []
        include_external = False
        for name in toolsets or DEFAULT_TOOLSETS:
            if name == "all":
                wanted.extend(ToolRegistry.get_names())
                include_external = True
                continue
            for tool in TOOLSETS.get(name, [name]):
                if tool == "@external":
                    include_external = True
                else:
                    wanted.append(tool)
        if include_external:
            for entry_name, entry in ToolRegistry.get_all().items():
                if str(getattr(entry, "origin", "native")) in {"skill", "mcp", "connection"}:
                    wanted.append(entry_name)

        self.entries: dict[str, Any] = {}
        for name in dict.fromkeys(wanted):
            if name in self.native:
                continue
            entry = ToolRegistry.get(name)
            if entry is None or not getattr(entry, "available", True):
                continue
            if any(not _flag_enabled(flag) for flag in getattr(entry, "policy_flags", ()) or ()):
                continue
            self.entries[name] = entry
        # Native tools only appear when one of their toolsets was requested.
        requested = set(wanted)
        self.native = {name: tool for name, tool in self.native.items() if name in requested or name == "delegate_to_agent"}

    @property
    def names(self) -> list[str]:
        return list(self.entries) + list(self.native)

    def schemas(self) -> list[dict[str, Any]]:
        rendered: list[dict[str, Any]] = []
        for name, entry in self.entries.items():
            schema: dict[str, Any] = dict(getattr(entry, "input_schema", {}) or {})
            if not schema.get("properties"):
                func = getattr(entry, "func", None)
                args_schema = getattr(func, "args_schema", None)
                if args_schema is not None:
                    try:
                        schema = args_schema.model_json_schema()
                    except Exception:
                        schema = {"type": "object", "properties": {}}
            description = getattr(entry, "description", "") or getattr(getattr(entry, "func", None), "description", "")
            rendered.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": _compact_description(description),
                    "parameters": _compact_schema(schema),
                },
            })
        for tool in self.native.values():
            rendered.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            })
        return rendered

    def entry(self, name: str) -> Any:
        return self.entries.get(name)

    def is_parallel_safe(self, name: str) -> bool:
        if name in self.native:
            return self.native[name].parallel_safe
        return name in PARALLEL_SAFE

    def resolve_name(self, name: str) -> str:
        """Accept harmless spelling drift such as 'Web-Search' -> 'web_search'."""
        if name in self.entries or name in self.native:
            return name
        key = re.sub(r"[^a-z0-9]", "", str(name or "").lower())
        for candidate in self.names:
            if re.sub(r"[^a-z0-9]", "", candidate.lower()) == key:
                return candidate
        return name

    def tool_context(self) -> dict[str, Any]:
        root = str(self.project_root or "").strip()
        return {
            "thread_id": self.session_id,
            "session_id": self.session_id,
            "allowed_tool_names": list(self.entries),
            "permissions": {},
            "enforce_tools": True,
            # With a Project attached, work stays inside it. Otherwise the
            # configured file roots (workspace, Desktop, extras) apply.
            "strict_scope": bool(root),
            "project_root": root,
            "lean_runtime": True,
        }

    def run(self, name: str, args: dict[str, Any]) -> ToolResult:
        started = time.perf_counter()
        name = self.resolve_name(name)
        if name in self.native:
            from agent.tools import _tool_execution_context

            def invoke_native() -> str:
                token = _tool_execution_context.set(self.tool_context())
                try:
                    return str(self.native[name].func(args) or "")
                finally:
                    _tool_execution_context.reset(token)

            try:
                output = contextvars.copy_context().run(invoke_native)
                ok = not output.lower().startswith(_FAILURE_PREFIXES)
            except Exception as exc:
                logger.warning("Lean native tool {} failed: {}", name, exc)
                output, ok = f"Error: {exc}", False
            return ToolResult(ok, output, int((time.perf_counter() - started) * 1000))
        entry = self.entries.get(name)
        if entry is None:
            close = ", ".join(sorted(self.names)[:40])
            return ToolResult(False, f"Error: unknown tool '{name}'. Available tools: {close}", 0)

        from agent.tools import _tool_execution_context, strip_echo_file_wrapper

        def invoke() -> str:
            token = _tool_execution_context.set(self.tool_context())
            try:
                func = entry.func
                if hasattr(func, "invoke"):
                    return func.invoke(args)
                return func(**args)
            finally:
                _tool_execution_context.reset(token)

        try:
            raw = contextvars.copy_context().run(invoke)
            output = raw if isinstance(raw, str) else json.dumps(raw, default=str, ensure_ascii=False)
            ok = not output.strip().lower().startswith(_FAILURE_PREFIXES)
        except Exception as exc:
            message = str(exc)
            if "validation error" in message.lower():
                message = f"Invalid arguments for {name}: {message[:600]}"
            output, ok = f"Error: {message}", False
            logger.warning("Lean tool {} failed: {}", name, exc)
        try:
            from agent.tool_registry import ToolUsageStats

            ToolUsageStats.record_call(name)
            if not ok:
                ToolUsageStats.record_error(name)
        except Exception:
            pass
        if "<<<ECHO_FILE" in output:
            try:
                first_line = output.split("\n", 1)[0]
                body = strip_echo_file_wrapper(output)
                output = f"{first_line}\n{body}" if not first_line.startswith("<<<") else body
            except Exception:
                pass
        return ToolResult(ok, output, int((time.perf_counter() - started) * 1000))


def describe_call(name: str, args: dict[str, Any]) -> str:
    """One readable line for the chat timeline and approval cards."""
    pick = lambda *keys: next((str(args[k]) for k in keys if args.get(k)), "")  # noqa: E731
    if name == "web_search":
        return f"Searching “{pick('query', 'q')}”"
    if name == "safe_web_fetch":
        return f"Reading {pick('url')}"
    if name in {"file_read", "file_list"}:
        return f"{'Reading' if name == 'file_read' else 'Listing'} {pick('path') or '.'}"
    if name == "file_write":
        return f"Writing {pick('path')}"
    if name == "file_delete":
        return f"Deleting {pick('path')}"
    if name in {"file_move", "file_copy"}:
        return f"{'Moving' if name == 'file_move' else 'Copying'} {pick('src', 'source', 'path')} → {pick('dst', 'destination')}"
    if name == "file_mkdir":
        return f"Creating folder {pick('path')}"
    if name in {"terminal_run", "terminal"}:
        return f"Running `{pick('command')[:160]}`"
    if name == "file_edit":
        return f"Editing {Path(pick('path')).name or pick('path')}"
    if name == "file_search":
        return f"Searching code for “{pick('pattern', 'query')[:80]}”"
    if name == "file_find":
        return f"Finding files {pick('pattern', 'glob')}"
    if name == "process_start":
        return f"Starting `{pick('command')[:140]}` in the background"
    if name == "process_output":
        return f"Checking process {pick('id')}"
    if name == "process_stop":
        return f"Stopping process {pick('id')}"
    if name == "memory_save":
        return "Saving to memory"
    if name == "memory_search":
        return f"Recalling “{pick('query')}”"
    if name == "delegate_to_agent":
        return f"Asking {pick('agent')} for help"
    preview = ", ".join(f"{k}={str(v)[:40]}" for k, v in list(args.items())[:3])
    return f"{name}({preview})" if preview else name


def safe_args_preview(args: dict[str, Any], limit: int = 600) -> dict[str, Any]:
    preview: dict[str, Any] = {}
    for key, value in list(args.items())[:12]:
        text = value if isinstance(value, (int, float, bool)) or value is None else str(value)
        if isinstance(text, str) and len(text) > limit:
            text = text[:limit] + f"… ({len(str(value))} chars)"
        preview[key] = text
    return preview


def project_root_for_session(session_id: str) -> str:
    try:
        from agent.state import get_state_store
        from agent.projects import get_project_manager

        state = get_state_store().get_thread_state(session_id)
        project_id = str(state.active_project_id or "")
        if not project_id:
            return ""
        project = get_project_manager().get_project(project_id)
        root = str(getattr(project, "workspace_root", "") or getattr(project, "git_root", "") or "")
        return root if root and Path(root).exists() else ""
    except Exception:
        return ""
