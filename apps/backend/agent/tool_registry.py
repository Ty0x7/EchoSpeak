"""
Tool Registry for EchoSpeak.

Provides a central registry that tools self-register into via decorator.
Replaces the hardcoded lists in core.py (_create_tools, _is_action_tool,
_action_allowed) with a single source of truth.

Usage in tools.py:
    from agent.tool_registry import ToolRegistry

    @ToolRegistry.register(
        name="web_search",
        description="Search the web for information",
        category="research",
    )
    @tool(args_schema=WebSearchArgs)
    def web_search(query: str) -> str:
        ...

Usage in core.py:
    entries = ToolRegistry.get_all()       # all registered ToolEntry objects
    safe    = ToolRegistry.get_safe()      # non-action tool functions only
    ToolRegistry.is_action("file_write")   # True
    ToolRegistry.get_permission_flags("file_write")  # ["ENABLE_SYSTEM_ACTIONS", "ALLOW_FILE_WRITE"]
"""

from __future__ import annotations

import hashlib
import json
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set


@dataclass(frozen=True)
class ToolEntry:
    """Metadata for a single registered tool."""

    name: str
    func: Any  # the LangChain @tool-decorated function
    description: str
    category: str = "general"
    is_action: bool = False
    risk_level: str = "safe"  # "safe" | "moderate" | "destructive"
    policy_flags: tuple = ()  # env flags required to enable
    keyword_hints: tuple = ()  # keywords for heuristic routing
    owner: str = "builtin"
    origin: str = "native"  # native | skill | connection | mcp
    connection_id: str = ""
    mcp_server: str = ""
    health: str = "healthy"
    input_schema: Dict[str, Any] = field(default_factory=dict)
    output_schema: Dict[str, Any] = field(default_factory=dict)
    approval_required: bool = False
    available: bool = True
    unavailable_reason: str = ""
    project_ids: tuple = ()
    session_ids: tuple = ()


_registration_context: ContextVar[tuple[str, bool]] = ContextVar(
    "echospeak_tool_registration_context",
    default=("builtin", False),
)


class ToolRegistry:
    """Central registry for EchoSpeak tools.

    Tools self-register via the ``@ToolRegistry.register(...)`` decorator.
    The registry is intentionally module-level (class-level dict) so that
    importing a module is sufficient to register its tools.
    """

    _entries: Dict[str, ToolEntry] = {}
    _revision: int = 0
    _lock = threading.RLock()

    @classmethod
    def _put(cls, entry: ToolEntry) -> None:
        """Store one entry and advance the immutable inventory revision on change."""
        with cls._lock:
            if cls._entries.get(entry.name) == entry:
                return
            cls._entries[entry.name] = entry
            cls._revision += 1

    @classmethod
    @contextmanager
    def registration_scope(cls, owner: str, *, reject_conflicts: bool = True):
        """Bind provenance for one reviewed dynamic registration transaction."""
        token = _registration_context.set((str(owner or "unknown"), bool(reject_conflicts)))
        try:
            yield
        finally:
            _registration_context.reset(token)

    # ── Registration ────────────────────────────────────────────────

    @classmethod
    def register(
        cls,
        name: str,
        description: str,
        category: str = "general",
        is_action: bool = False,
        risk_level: str = "safe",
        policy_flags: Optional[List[str]] = None,
        keyword_hints: Optional[List[str]] = None,
        origin: str = "native",
        connection_id: str = "",
        mcp_server: str = "",
        health: str = "healthy",
        input_schema: Optional[Dict[str, Any]] = None,
        output_schema: Optional[Dict[str, Any]] = None,
        approval_required: Optional[bool] = None,
        available: bool = True,
        unavailable_reason: str = "",
    ):
        """Decorator that registers a tool function in the global registry.

        Can be stacked with LangChain's ``@tool`` decorator — order does not
        matter because we store whatever the decorator returns (which is the
        LangChain StructuredTool wrapper if ``@tool`` ran first, or the raw
        function if ``@register`` ran first and ``@tool`` will wrap it next).

        The registry re-captures the final object at first access via
        ``_resolve()``, so late-binding is safe.
        """
        _flags = tuple(policy_flags or [])
        _hints = tuple(keyword_hints or [])

        def decorator(func: Callable) -> Callable:
            owner, reject_conflicts = _registration_context.get()
            existing = cls._entries.get(name)
            if existing is not None and reject_conflicts:
                raise ValueError(
                    f"Tool registration collision for '{name}': "
                    f"owner '{owner}' cannot replace '{existing.owner}'"
                )
            cls._put(ToolEntry(
                name=name,
                func=func,
                description=description,
                category=category,
                is_action=is_action,
                risk_level=risk_level,
                policy_flags=_flags,
                keyword_hints=_hints,
                owner=owner,
                origin=str(origin or "native"),
                connection_id=str(connection_id or ""),
                mcp_server=str(mcp_server or ""),
                health=str(health or "unknown"),
                input_schema=dict(input_schema or {}),
                output_schema=dict(output_schema or {}),
                approval_required=bool(is_action if approval_required is None else approval_required),
                available=bool(available),
                unavailable_reason=str(unavailable_reason or ""),
            ))
            return func

        return decorator

    # ── Bulk registration from existing TOOL_METADATA ───────────────

    @classmethod
    def register_from_metadata(
        cls,
        tool_funcs: List[Any],
        metadata: Dict[str, Dict[str, Any]],
    ) -> None:
        """Register tools in bulk from the legacy *get_available_tools()* list
        and the *TOOL_METADATA* dict in tools.py.

        This is the **migration bridge** — it lets us use the registry
        immediately without rewriting every ``@tool`` in tools.py.

        Once all tools use ``@ToolRegistry.register``, this method can be
        removed.
        """
        for func in tool_funcs:
            name = getattr(func, "name", None)
            if not name:
                continue
            if name in cls._entries:
                # Already registered via decorator — skip
                continue
            desc = getattr(func, "description", "") or ""
            meta = metadata.get(name, {})
            risk = meta.get("risk_level", "safe")
            requires_confirm = meta.get("requires_confirmation", False)
            flags = tuple(meta.get("policy_flags", []))
            cls._put(ToolEntry(
                name=name,
                func=func,
                description=desc,
                category=_infer_category(name),
                is_action=requires_confirm,
                risk_level=risk,
                policy_flags=flags,
                owner="legacy_metadata",
                origin="native",
                approval_required=bool(requires_confirm),
            ))

    @classmethod
    def register_entry(cls, entry: ToolEntry, *, reject_conflicts: bool = True) -> ToolEntry:
        """Register a fully typed dynamic entry without bypassing collision policy."""
        existing = cls._entries.get(entry.name)
        if existing is not None and reject_conflicts:
            raise ValueError(
                f"Tool registration collision for '{entry.name}': "
                f"owner '{entry.owner}' cannot replace '{existing.owner}'"
            )
        cls._put(entry)
        return entry

    @classmethod
    def remove_owned(cls, name: str, owner: str) -> bool:
        """Remove only the entry owned by the caller; never delete a collision winner."""
        current = cls._entries.get(str(name or ""))
        if current is None or current.owner != str(owner or ""):
            return False
        with cls._lock:
            cls._entries.pop(current.name, None)
            cls._revision += 1
        return True

    @classmethod
    def set_owner_availability(
        cls,
        owner: str,
        *,
        available: bool,
        health: str,
        reason: str = "",
    ) -> int:
        """Atomically project transport health onto all tools owned by a provider."""
        changed = 0
        for name, entry in list(cls._entries.items()):
            if entry.owner != str(owner or ""):
                continue
            updated = replace(
                entry,
                available=bool(available),
                health=str(health or "unknown"),
                unavailable_reason="" if available else str(reason or "unavailable"),
            )
            if updated != entry:
                cls._put(updated)
                changed += 1
        return changed

    @classmethod
    def describe(cls, name: str) -> Optional[Dict[str, Any]]:
        entry = cls.get(name)
        if entry is None:
            return None
        return {
            "name": entry.name,
            "description": entry.description,
            "category": entry.category,
            "origin": entry.origin,
            "owner": entry.owner,
            "connection_id": entry.connection_id,
            "mcp_server": entry.mcp_server,
            "health": entry.health,
            "schema": dict(entry.input_schema or {}),
            "output_schema": dict(entry.output_schema or {}),
            "approval_required": bool(entry.approval_required or entry.is_action),
            "available": bool(entry.available),
            "unavailable_reason": entry.unavailable_reason,
            "risk_level": entry.risk_level,
            "project_ids": list(entry.project_ids),
            "session_ids": list(entry.session_ids),
        }

    @classmethod
    def available_in_scope(cls, name: str, *, project_id: str = "", session_id: str = "") -> bool:
        entry = cls.get(name)
        if entry is None or not entry.available:
            return False
        if entry.project_ids and str(project_id or "") not in set(entry.project_ids):
            return False
        if entry.session_ids and str(session_id or "") not in set(entry.session_ids):
            return False
        return True

    # ── Queries ─────────────────────────────────────────────────────

    @classmethod
    def get(cls, name: str) -> Optional[ToolEntry]:
        """Get a single tool entry by name."""
        return cls._entries.get(name)

    @classmethod
    def get_all(cls) -> Dict[str, ToolEntry]:
        """Return all registered tool entries."""
        return dict(cls._entries)

    @classmethod
    def inventory_snapshot(cls, config: Any = None) -> Dict[str, Any]:
        """Return one deterministic inventory artifact used by prompt and execution."""
        entries = list(cls._entries.values())
        if config is not None:
            enabled = {
                str(getattr(func, "name", "") or getattr(func, "__name__", ""))
                for func in cls.get_config_filtered_funcs(config)
            }
            entries = [entry for entry in entries if entry.name in enabled]
        records = [
            {
                "name": entry.name,
                "owner": entry.owner,
                "origin": entry.origin,
                "category": entry.category,
                "available": bool(entry.available),
                "approval_required": bool(entry.approval_required or entry.is_action),
                "health": entry.health,
            }
            for entry in sorted(entries, key=lambda item: item.name)
        ]
        canonical = json.dumps(records, sort_keys=True, separators=(",", ":"))
        return {
            "revision": cls._revision,
            "count": len(records),
            "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "tools": records,
        }

    @classmethod
    def get_funcs(cls) -> List[Any]:
        """Return all tool functions (for LangChain agent init)."""
        return [e.func for e in cls._entries.values() if e.available]

    @classmethod
    def get_safe_funcs(cls) -> List[Any]:
        """Return non-action tool functions only (safe for LLM tool-calling)."""
        return [e.func for e in cls._entries.values() if e.available and not e.is_action]

    @classmethod
    def get_config_filtered_funcs(cls, config: Any) -> List[Any]:
        """Return tool functions filtered by current config flags.

        Non-action tools are always included.  Action tools are included
        only when **all** of their ``policy_flags`` evaluate to ``True``
        on the supplied *config* object.  This allows action tools to
        appear in the LLM tool list when the user has enabled the
        corresponding safety gates (e.g. ``ENABLE_SYSTEM_ACTIONS``,
        ``ALLOW_FILE_WRITE``).

        The flag names stored in ``policy_flags`` use UPPER_CASE env-var
        style (``ENABLE_SYSTEM_ACTIONS``).  Config properties use
        snake_case (``enable_system_actions``).  We check both.
        """
        result: List[Any] = []
        for entry in cls._entries.values():
            if not entry.available:
                continue
            if not entry.is_action:
                result.append(entry.func)
                continue
            # Action tool — check every required policy flag
            if not entry.policy_flags:
                # No flags required → include
                result.append(entry.func)
                continue
            all_ok = True
            for flag in entry.policy_flags:
                attr_name = flag.lower()  # e.g. ENABLE_SYSTEM_ACTIONS → enable_system_actions
                if not bool(getattr(config, attr_name, False)):
                    all_ok = False
                    break
            if all_ok:
                result.append(entry.func)
        return result

    @classmethod
    def get_by_category(cls, category: str) -> List[ToolEntry]:
        """Return all tools in a specific category."""
        return [e for e in cls._entries.values() if e.category == category]

    @classmethod
    def is_action(cls, name: str) -> bool:
        """Check if a tool is an action tool (requires confirmation)."""
        entry = cls._entries.get(name)
        return entry.is_action if entry else False

    @classmethod
    def get_permission_flags(cls, name: str) -> List[str]:
        """Get the env permission flags required for a tool."""
        entry = cls._entries.get(name)
        return list(entry.policy_flags) if entry else []

    @classmethod
    def get_names(cls) -> Set[str]:
        """Return set of all registered tool names."""
        return set(cls._entries.keys())

    @classmethod
    def get_action_names(cls) -> Set[str]:
        """Return set of all action tool names."""
        return {e.name for e in cls._entries.values() if e.is_action}

    @classmethod
    def clear(cls) -> None:
        """Clear all registered tools (for testing)."""
        with cls._lock:
            if cls._entries:
                cls._entries.clear()
                cls._revision += 1


_CATEGORY_MAP = {
    "web_search": "research",
    "safe_web_fetch": "research",
    "youtube_transcript": "research",
    "browse_task": "research",
    "get_system_time": "utility",
    "calculate": "utility",
    "system_info": "utility",
    "analyze_screen": "vision",
    "vision_qa": "vision",
    "take_screenshot": "vision",
    "open_chrome": "system",
    "open_application": "system",
    "notepad_write": "system",
    "terminal_run": "system",
    "desktop_list_windows": "desktop",
    "desktop_find_control": "desktop",
    "desktop_click": "desktop",
    "desktop_type_text": "desktop",
    "desktop_activate_window": "desktop",
    "desktop_send_hotkey": "desktop",
    "file_list": "file_ops",
    "file_read": "file_ops",
    "file_write": "file_ops",
    "file_move": "file_ops",
    "file_copy": "file_ops",
    "file_delete": "file_ops",
    "file_mkdir": "file_ops",
    "artifact_write": "file_ops",
    "discord_web_read_recent": "discord",
    "discord_web_send": "discord",
    "discord_contacts_add": "discord",
    "discord_contacts_discover": "discord",
    "discord_read_channel": "discord",
    "discord_send_channel": "discord",
    "self_edit": "self_mod",
    "self_rollback": "self_mod",
    "self_git_status": "self_mod",
    "self_read": "self_mod",
    "self_grep": "self_mod",
    "self_list": "self_mod",
}


def _infer_category(name: str) -> str:
    """Infer a tool's category from its name (fallback for legacy tools)."""
    return _CATEGORY_MAP.get(name, "general")


# ── Tool Usage Statistics ────────────────────────────────────────────

class ToolUsageStats:
    """Thread-safe per-tool usage statistics tracker.

    Since ToolEntry is frozen, we track mutable stats separately.
    Used by the /capabilities endpoint to report usage_count,
    last_used_at, and success_rate per tool.
    """

    _lock = threading.Lock()
    _stats: Dict[str, Dict[str, Any]] = {}

    @classmethod
    def record_call(cls, tool_name: str) -> None:
        """Record a successful tool invocation."""
        with cls._lock:
            if tool_name not in cls._stats:
                cls._stats[tool_name] = {"calls": 0, "errors": 0, "last_used": None}
            cls._stats[tool_name]["calls"] += 1
            cls._stats[tool_name]["last_used"] = datetime.now(timezone.utc).isoformat()

    @classmethod
    def record_error(cls, tool_name: str) -> None:
        """Record a tool invocation error (also counts as a call attempt)."""
        with cls._lock:
            if tool_name not in cls._stats:
                cls._stats[tool_name] = {"calls": 0, "errors": 0, "last_used": None}
            cls._stats[tool_name]["calls"] += 1
            cls._stats[tool_name]["errors"] += 1
            cls._stats[tool_name]["last_used"] = datetime.now(timezone.utc).isoformat()

    @classmethod
    def get_stats(cls, tool_name: str) -> Dict[str, Any]:
        """Get usage stats for a specific tool."""
        with cls._lock:
            s = cls._stats.get(tool_name, {"calls": 0, "errors": 0, "last_used": None})
            calls = s["calls"]
            errors = s["errors"]
            success_rate = round((calls - errors) / calls, 3) if calls > 0 else None
            return {
                "usage_count": calls,
                "error_count": errors,
                "last_used_at": s["last_used"],
                "success_rate": success_rate,
            }

    @classmethod
    def get_all_stats(cls) -> Dict[str, Dict[str, Any]]:
        """Get usage stats for all tools."""
        with cls._lock:
            result = {}
            for name, s in cls._stats.items():
                calls = s["calls"]
                errors = s["errors"]
                success_rate = round((calls - errors) / calls, 3) if calls > 0 else None
                result[name] = {
                    "usage_count": calls,
                    "error_count": errors,
                    "last_used_at": s["last_used"],
                    "success_rate": success_rate,
                }
            return result

    @classmethod
    def clear(cls) -> None:
        """Clear all stats (for testing)."""
        with cls._lock:
            cls._stats.clear()
