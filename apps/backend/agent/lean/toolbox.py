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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from loguru import logger

from agent.lean.policy import redact_payload, redact_secrets

from agent.lean.widgets import collect as collect_widgets
from config import config

# Kept small on purpose (see docs/research/harness-review.md, tool audit): every
# schema is sent on every request, and small models choose worse from long lists.
TOOLSETS: dict[str, list[str]] = {
    "core": [
        "calculate", "system_info",
        "file_list", "file_read", "file_find", "file_search", "file_edit", "file_write",
        "file_copy", "file_move", "file_delete", "checkpoint_undo",
        "project_status", "create_artifact", "update_artifact",
        "create_media", "creation_status",
    ],
    # Looking things up on the web.
    "web": ["web_search", "safe_web_fetch", "youtube_transcript", "research_notebook"],
    # Live data and media shown as cards.
    "live": [
        "weather_live", "sports_live", "browse_task",
        "stock_history", "product_search", "video_search", "image_search",
    ],
    "terminal": ["terminal", "process_output", "process_stop"],
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
    "self": ["self_list", "self_read", "self_grep", "self_git_status", "self_edit", "self_rollback", "project_update_context"],
    "memory": ["memory_save", "memory_search", "chat_search", "document_search", "soul_update"],
    # Skill, MCP, and Connection tools that registered at runtime.
    "skills": ["@external"],
}

# Names kept so stored personas and API callers keep working; not offered in the editor.
TOOLSET_ALIASES: dict[str, list[str]] = {"research": ["web", "live"]}
for _alias, _parts in TOOLSET_ALIASES.items():
    TOOLSETS[_alias] = [tool for part in _parts for tool in TOOLSETS[part]]

DEFAULT_TOOLSETS = ["core", "web", "live", "terminal", "vision", "memory", "skills"]

# Names models trained on other harnesses reach for (Claude Code, Codex, OpenAI
# examples): run the matching tool instead of failing with "unknown tool".
# A value may carry fixed arguments, e.g. the old process_start tool.
TOOL_ALIASES: dict[str, tuple[str, dict[str, Any]]] = {
    **{name: ("terminal", {}) for name in ("bash", "shell", "run_command", "run_shell_command", "exec_command",
                                           "execute_command", "run_terminal_cmd", "powershell", "terminal_run")},
    "process_start": ("terminal", {"background": True}),
    **{name: ("file_read", {}) for name in ("read_file", "view_file", "read", "cat")},
    **{name: ("file_write", {}) for name in ("write_file", "create_file", "write")},
    **{name: ("file_edit", {}) for name in ("edit_file", "edit", "str_replace", "replace_in_file", "apply_edit")},
    **{name: ("file_search", {}) for name in ("grep", "grep_search", "search_files", "search_code", "rg")},
    **{name: ("file_find", {}) for name in ("glob", "find_files", "file_glob", "find")},
    **{name: ("file_list", {}) for name in ("ls", "list_dir", "list_directory", "list_files")},
    **{name: ("safe_web_fetch", {}) for name in ("web_fetch", "fetch", "fetch_url", "open_url", "browse")},
    **{name: ("web_search", {}) for name in ("search_web", "google_search", "internet_search", "search")},
    **{name: ("memory_save", {}) for name in ("save_memory", "remember")},
    **{name: ("memory_search", {}) for name in ("recall", "search_memory")},
}

# Argument names from the same harnesses, per target tool: renamed, never overriding.
ARG_ALIASES: dict[str, dict[str, str]] = {
    "file_read": {"file_path": "path", "filename": "path"},
    "file_write": {"file_path": "path", "filename": "path", "contents": "content", "text": "content"},
    "file_edit": {"file_path": "path", "old_string": "old_text", "new_string": "new_text", "old_str": "old_text",
                  "new_str": "new_text"},
    "file_search": {"query": "pattern", "regex": "pattern", "include": "file_glob"},
    "file_find": {"glob": "pattern", "name": "pattern"},
    "file_list": {"directory": "path", "dir": "path", "dir_path": "path"},
    "terminal": {"cmd": "command", "workdir": "cwd", "run_in_background": "background"},
    "safe_web_fetch": {"link": "url"},
    "web_search": {"q": "query"},
}

# Read-only tools can run in parallel when the model asks for several at once.
PARALLEL_SAFE = {
    "get_system_time", "calculate", "system_info", "file_list", "file_read", "file_find", "file_search",
    "web_search", "safe_web_fetch", "youtube_transcript", "weather_live",
    "sports_live", "project_status", "memory_search", "chat_search", "email_read_inbox",
    "email_search", "email_get_thread", "discord_read_channel", "self_list",
    "self_read", "self_grep", "self_git_status", "desktop_list_windows",
}

# What the model sees for registry tools, where the registered text is vague,
# overlaps another tool, or exposes parameters that do nothing (Anthropic,
# "Writing effective tools for agents": say when to use each tool, keep
# parameters few and unambiguous). Only the schema changes; the tool doesn't.
TOOL_OVERRIDES: dict[str, dict[str, Any]] = {
    "get_system_time": {"description": "The current local date, time, weekday and time zone on this PC."},
    "calculate": {
        "description": "Evaluate a math expression exactly (arithmetic, percentages, powers, roots). "
        "Use it for any calculation beyond a trivial sum instead of working it out yourself.",
        "params": {"expression": "The expression, e.g. 0.17 * 2340 or sqrt(2) * 10."},
    },
    "system_info": {"description": "This PC's operating system, CPU, GPU and memory."},
    "file_list": {
        "description": "List what is directly inside one folder (files and subfolders). To find files by name "
        "anywhere below a folder use file_find; to search text inside files use file_search.",
        "params": {"path": "Folder to list (default: the project folder).", "limit": "Most entries to return."},
    },
    "web_search": {
        "description": "Search the web for current or factual information. Returns titles, links and snippets. "
        "If the snippets don't answer the question, open the best result with safe_web_fetch.",
        "drop": ["objective", "local_first", "freshness"],
        "params": {"query": "A short keyword query, e.g. 'Edmonton population 2024'."},
    },
    "safe_web_fetch": {
        "params": {"max_text_chars": "Cap on the page text returned; leave it unset unless the page is huge."},
    },
    "weather_live": {
        "description": "Current weather and today's forecast for a place, from a weather API. "
        "Use this rather than web_search for weather.",
    },
    "take_screenshot": {"description": "Save a screenshot of the screen to an image file. "
                        "To read what's on screen use analyze_screen; to ask about it use vision_qa."},
    "analyze_screen": {"description": "Read the text currently on screen (OCR)."},
    "vision_qa": {"description": "Answer a question about what is currently on screen, using a vision model."},
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
    # Cards for the chat built from the tool's own data (see agent/lean/widgets.py).
    widgets: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class NativeTool:
    """A lean-runtime tool implemented here instead of in ToolRegistry."""

    name: str
    description: str
    parameters: dict[str, Any]
    func: Callable[[dict[str, Any]], str]
    parallel_safe: bool = False
    # Set for tools that hand the conversation to another agent. Called before
    # the tool runs: returns an "Error: ..." for bad arguments, otherwise the
    # short note shown on the tool card in the caller's message.
    handoff: Optional[Callable[[dict[str, Any]], str]] = None
    # A "final output" tool (complete_task): once it succeeds the agent's turn ends.
    ends_turn: bool = False
    # Coordination tools (handoff, task board, completion) the runtime offers only
    # when they apply: kept whatever toolsets the persona has.
    always: bool = False


def _alias_key(name: str) -> str:
    """'Web-Fetch' and 'web fetch' -> 'web_fetch'."""
    return re.sub(r"[^a-z0-9]+", "_", str(name or "").lower()).strip("_")


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
        self.native = {name: tool for name, tool in self.native.items() if name in requested or tool.always}

    @property
    def names(self) -> list[str]:
        return list(self.entries) + list(self.native)

    def restrict_to_read_only(self) -> None:
        """Keep only tools that look things up: for turns that plan, not act."""
        self.entries = {name: entry for name, entry in self.entries.items() if name in PARALLEL_SAFE}
        self.native = {name: tool for name, tool in self.native.items() if tool.parallel_safe or tool.always}

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
            parameters = _compact_schema(schema)
            override = TOOL_OVERRIDES.get(name) or {}
            if override.get("description"):
                description = override["description"]
            for dropped in override.get("drop", ()):
                parameters["properties"].pop(dropped, None)
                if dropped in parameters.get("required", []):
                    parameters["required"].remove(dropped)
            for param, text in (override.get("params") or {}).items():
                if param in parameters["properties"]:
                    parameters["properties"][param]["description"] = text
            rendered.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": _compact_description(description, 320),
                    "parameters": parameters,
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

    def handoff(self, name: str) -> Optional[Callable[[dict[str, Any]], str]]:
        tool = self.native.get(name)
        return tool.handoff if tool else None

    def ends_turn(self, name: str) -> bool:
        tool = self.native.get(name)
        return bool(tool and tool.ends_turn)

    def resolve_name(self, name: str) -> str:
        """Accept harmless spelling drift such as 'Web-Search' -> 'web_search'."""
        if name in self.entries or name in self.native:
            return name
        key = re.sub(r"[^a-z0-9]", "", str(name or "").lower())
        for candidate in self.names:
            if re.sub(r"[^a-z0-9]", "", candidate.lower()) == key:
                return candidate
        alias = TOOL_ALIASES.get(_alias_key(name))
        if alias and (alias[0] in self.entries or alias[0] in self.native):
            return alias[0]
        return name

    def normalize_call(self, name: str, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """The tool and arguments to actually run: spelling, other harnesses' names, their argument names."""
        resolved = self.resolve_name(name)
        args = dict(args or {})
        alias = TOOL_ALIASES.get(_alias_key(name))
        if alias and resolved == alias[0] and name not in self.entries and name not in self.native:
            for key, value in alias[1].items():
                args.setdefault(key, value)
        for old, new in ARG_ALIASES.get(resolved, {}).items():
            if old in args and new not in args:
                args[new] = args.pop(old)
        if resolved == "terminal" and resolved in self.native:
            terminal = getattr(self.native[resolved].func, "__self__", None)
            if terminal is not None and hasattr(terminal, "effective_where"):
                # The policy sees the destination the Terminal instance will use,
                # including Auto's host fallback when Docker is unavailable.
                args["where"] = terminal.effective_where(args)
        if resolved == "create_media":
            from agent.generation_service import resolve_selection
            args["provider"], args["model"] = resolve_selection(str(args.get("kind") or "image"),
                str(args.get("provider") or ""), str(args.get("model") or ""))
        return resolved, args

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

            widgets: list[dict[str, Any]] = []
            try:
                output, widgets = contextvars.copy_context().run(collect_widgets, invoke_native)
                ok = not output.lower().startswith(_FAILURE_PREFIXES)
            except Exception as exc:
                logger.warning("Lean native tool {} failed: {}", name, exc)
                output, ok = f"Error: {exc}", False
            return ToolResult(ok, redact_secrets(output), int((time.perf_counter() - started) * 1000),
                              redact_payload(widgets) if ok else [])
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

        widgets: list[dict[str, Any]] = []
        try:
            raw, widgets = contextvars.copy_context().run(collect_widgets, invoke)
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
        return ToolResult(ok, redact_secrets(output), int((time.perf_counter() - started) * 1000),
                          redact_payload(widgets) if ok else [])


def describe_call(name: str, args: dict[str, Any]) -> str:
    """One readable line for the chat timeline and approval cards."""
    pick = lambda *keys: next((str(args[k]) for k in keys if args.get(k)), "")  # noqa: E731
    if name == "web_search":
        return f"Searching “{pick('query', 'q')}”"
    if name == "stock_history":
        symbols = args.get("symbols") or args.get("symbol") or ""
        return f"Stock prices for {', '.join(map(str, symbols)) if isinstance(symbols, list) else symbols}"
    if name == "product_search":
        return f"Shopping for “{pick('query')}”"
    if name == "video_search":
        return f"Finding videos of “{pick('query')}”"
    if name == "image_search":
        return f"Finding pictures of “{pick('query')}”"
    if name in {"create_artifact", "update_artifact"}:
        return f"{'Creating' if name == 'create_artifact' else 'Updating'} {pick('title', 'artifact_id') or 'artifact'}"
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
    if name == "document_search":
        return f"Searching your documents for “{pick('query')}”"
    if name == "soul_update":
        from agent.lean.soul import describe

        return describe(args)
    if name == "delegate_to_agent":
        return f"Asking {pick('agent')} for help"
    preview = ", ".join(f"{k}={str(v)[:40]}" for k, v in list(args.items())[:3])
    return f"{name}({preview})" if preview else name


# The argument that names what a call acted on. Kept on the timeline so finished
# work can be graded afterwards (agent/learning): which file was written, which
# command ran, what was searched. File contents and message bodies never are.
_TARGET_KEYS = ("path", "file", "src", "source", "command", "query", "q", "url", "pattern", "glob")


def call_target(args: dict[str, Any], limit: int = 300) -> str:
    """'src/app.py', 'pytest -q', 'a.txt -> b.txt'; stored credentials redacted."""
    from agent.lean.policy import redact_secrets

    pick = lambda keys: next((str(args[k]) for k in keys if isinstance(args.get(k), str) and args[k].strip()), "")  # noqa: E731
    first, dest = pick(_TARGET_KEYS), pick(("dst", "destination"))
    text = f"{first} -> {dest}" if first and dest and dest != first else (first or dest)
    return redact_secrets(" ".join(text.split()))[:limit]


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
