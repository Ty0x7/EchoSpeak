"""
The EchoSpeak app object.

One ``EchoSpeakAgent`` holds what a session needs around the lean runtime: the
selected model, memory, the soul, the active skill workspace and Project, the
tool inventory, and the doctor / capability reports. Every channel (web,
Discord, Telegram, Twitch, Twitter) answers through ``process_query``, which
runs one turn on ``agent.lean.runtime``.
"""

import hashlib
import importlib.util
import os
import sys
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import config, ModelProvider
from agent.memory import get_agent_memory
from agent.skills_registry import (
    build_skills_prompt,
    load_skills,
    load_skill_tools,
    load_workspace,
)
from agent.tools import get_available_tools, TOOL_METADATA
from agent.tool_registry import ToolRegistry
from agent.state import get_state_store
from agent.model_adapters import get_family_adapter
from agent.model_runtime import ModelRuntimeClient


class EchoSpeakAgent:
    """Model, memory, soul and scope for one pooled session."""

    def __init__(self, memory_path: Optional[str] = None, llm_provider: ModelProvider = None, manage_background_services: bool = True, model_id: Optional[str] = None):
        logger.info("Initializing Echo Speak Agent...")
        default_cloud_provider = str(getattr(config, "default_cloud_provider", ModelProvider.OPENAI.value) or "").strip().lower()
        openai_key = str(getattr(getattr(config, "openai", None), "api_key", "") or "").strip()
        gemini_key = str(getattr(getattr(config, "gemini", None), "api_key", "") or "").strip()
        if default_cloud_provider == ModelProvider.GEMINI.value:
            fallback_provider = ModelProvider.GEMINI if gemini_key or not openai_key else ModelProvider.OPENAI
        elif default_cloud_provider == ModelProvider.OPENAI.value:
            fallback_provider = ModelProvider.OPENAI if openai_key or not gemini_key else ModelProvider.GEMINI
        elif gemini_key and not openai_key:
            fallback_provider = ModelProvider.GEMINI
        else:
            fallback_provider = ModelProvider.OPENAI
        self.llm_provider = llm_provider or (config.local.provider if config.use_local_models else fallback_provider)
        self._bound_model_id = str(model_id or "").strip()
        self.model_runtime = ModelRuntimeClient(self.llm_provider, self._bound_model_id)
        self.provider_info = {
            "provider": self.llm_provider.value,
            "model": self.model_runtime.model_id,
        }

        # Thread-pooled agents share the one canonical in-process memory owner.
        self.memory = get_agent_memory(memory_path)
        self.document_store = None
        if getattr(config, "document_rag_enabled", False):
            if self.memory.embeddings is None:
                logger.warning("Document RAG disabled: embeddings unavailable")
            else:
                try:
                    from agent.document_store import DocumentStore

                    self.document_store = DocumentStore(
                        self.memory.embeddings,
                        str(getattr(config, "docs_index_path", "")),
                        str(getattr(config, "docs_meta_path", "")),
                    )
                except Exception as exc:
                    logger.warning(f"Document RAG disabled: {exc}")
        self._last_doc_sources: list[dict[str, Any]] = []

        self._state_store = get_state_store()
        self._soul_cache: Dict[str, Any] = {"path": "", "mtime_ns": -1, "max_chars": 0, "content": ""}
        self._current_thread_id: str = "default"
        self._current_source: str = ""
        self._current_user_role: str = "owner"
        self._workspace_id: Optional[str] = None
        self._workspace_name: str = ""
        self._workspace_prompt: str = ""
        self._skills_prompt: str = ""
        self._active_skill_defs: list = []
        self._active_project_id: Optional[str] = None
        self._request_lock = threading.RLock()
        self._request_result_local = threading.local()

        # One tool inventory (ToolRegistry) for every session. Domain modules
        # self-register; one missing optional dependency never blocks the rest.
        ToolRegistry.register_from_metadata(get_available_tools(), TOOL_METADATA)
        for domain_module in ("agent.voice_runtime", "agent.generation_runtime"):
            try:
                __import__(domain_module)
            except Exception as domain_tools_exc:
                logger.debug("Domain tool registration skipped for {}: {}", domain_module, domain_tools_exc)

        # Connections are loaded before Skills: Skills use Connection
        # capabilities, they never establish authentication.
        from agent.connections import get_connection_registry
        self._connection_registry = get_connection_registry()
        try:
            from agent.connection_lifecycle import get_connection_lifecycle_service

            get_connection_lifecycle_service().migrate_legacy_settings(config)
        except Exception as connection_migration_exc:
            logger.warning("Legacy Connection migration failed closed: {}", connection_migration_exc)

        self.configure_workspace(getattr(config, "default_workspace", "").strip() or None)

        # MCP tools join the same registry through the process-wide manager.
        self._mcp_manager = None
        try:
            from agent.mcp_client import get_mcp_manager

            mcp_mgr = get_mcp_manager()
            self._mcp_manager = mcp_mgr
            if getattr(config, "mcp_servers", None):
                mcp_mgr.initialize_servers(config.mcp_servers)
        except Exception as e:
            logger.warning(f"Failed to initialize MCP servers: {e}")

        self._tool_inventory_snapshot = ToolRegistry.inventory_snapshot(config)
        logger.info(
            "Agent initialized inventory_revision={} inventory_count={} provider={}",
            self._tool_inventory_snapshot.get("revision"),
            self._tool_inventory_snapshot.get("count"),
            self.llm_provider.value,
        )

    def _load_soul(self) -> str:
        """
        Load SOUL.md content if it exists and is enabled.

        The soul defines the agent's core identity, values, communication style,
        and boundaries. It is loaded once per session and injected into the
        system prompt BEFORE skills, giving it highest priority.

        Returns:
            str: Soul content or empty string if not found/disabled.
        """
        # Check if soul system is enabled
        soul_config = getattr(config, "soul", None)
        if soul_config is None:
            return ""
        if not getattr(soul_config, "enabled", True):
            logger.debug("SOUL.md system disabled via config")
            return ""

        # Packaged desktop defaults resolve to durable app data rather than
        # PyInstaller's temporary extraction directory.
        from agent.lean.soul import soul_path as _soul_path

        soul_path = _soul_path()

        # Check if file exists
        if not soul_path.exists():
            logger.debug(f"SOUL.md not found at {soul_path}")
            return ""

        max_chars = int(getattr(soul_config, "max_chars", 8000) or 8000)
        try:
            stat = soul_path.stat()
            # Size too: two writes inside one mtime tick (coarse filesystems) must not
            # serve the old text, or a verified soul edit would read back stale.
            mtime_ns, size = stat.st_mtime_ns, stat.st_size
            cache = getattr(self, "_soul_cache", {}) or {}
            if (
                str(cache.get("path") or "") == str(soul_path)
                and int(cache.get("mtime_ns", -1)) == mtime_ns
                and int(cache.get("size", -1)) == size
                and int(cache.get("max_chars", 0)) == max_chars
            ):
                return str(cache.get("content") or "")
        except OSError:
            mtime_ns, size = -1, -1
        
        # Read and validate content
        try:
            content = soul_path.read_text(encoding="utf-8").strip()
            if not content:
                logger.debug(f"SOUL.md is empty at {soul_path}")
                return ""

            # Apply character limit
            if len(content) > max_chars:
                logger.warning(
                    f"SOUL.md exceeds {max_chars} chars, truncating. "
                    f"Consider splitting into smaller sections."
                )
                content = content[:max_chars]

            self._soul_cache = {
                "path": str(soul_path),
                "mtime_ns": mtime_ns,
                "size": size,
                "max_chars": max_chars,
                "content": content,
            }
            logger.info(f"Loaded SOUL.md from {soul_path} ({len(content)} chars)")
            return content

        except Exception as e:
            logger.warning(f"Failed to load SOUL.md: {e}")
            return ""

    def _clear_session_project_scope(
        self,
        *,
        thread_id: Optional[str] = None,
        reason: str = "Project detached",
        stop_preview: bool = True,
    ) -> None:
        """Authoritative clear of Project scope for one Session (detach / switch / delete).

        Clears ThreadSessionState path fields, pending approvals/retries, the
        request-local tool root, and the optional preview process together.
        """
        tid = str(thread_id or self._thread_key() or "default").strip() or "default"
        prev = self._state_store.get_thread_state(tid)
        if prev.pending_approval_id:
            try:
                self._state_store.update_approval(
                    prev.pending_approval_id,
                    status="canceled",
                    outcome_summary=reason,
                )
            except Exception as exc:
                logger.debug("Could not cancel pending approval on scope clear: {}", exc)
        if self._thread_key() == tid:
            self._active_project_id = None
            try:
                from agent.tools import set_active_project_root, update_tool_execution_context

                set_active_project_root(None)
                update_tool_execution_context(project_root="", workspace_root="", active_project_id="")
            except Exception:
                pass
        self._state_store.update_thread_state(
            tid,
            active_project_id="",
            project_path="",
            workspace_root="",
            pending_approval_id="",
            pending_actions=[],
            retry_target={},
            objective="",
        )
        if stop_preview:
            try:
                from agent.project_preview import stop_preview_for_scope_change

                stop_preview_for_scope_change(
                    tid,
                    reason=reason,
                    detached_project_id=str(prev.active_project_id or ""),
                    state_store=self._state_store,
                )
            except Exception as exc:
                logger.debug("Preview stop failed for {}: {}", tid, exc)
        logger.info("Session project scope cleared thread={} reason={}", tid, reason)

    def activate_project(self, project_id: Optional[str]) -> bool:
        """Activate a project by ID (or deactivate by passing None).

        When active, the project's context_prompt is injected into the system prompt.
        Attach/switch/detach is one scope transaction (ThreadSessionState +
        approvals + preview + tool root).

        Args:
            project_id: Project ID to activate, or None to deactivate.

        Returns:
            True if the project was found and activated (or deactivated).
        """
        if project_id is None:
            self._clear_session_project_scope(reason="Project deactivated")
            logger.info("Project deactivated")
            return True
        try:
            from agent.projects import get_project_manager
            pm = get_project_manager()
            project = pm.get_project(project_id)
            if project:
                previous_state = self._state_store.get_thread_state(self._thread_key())
                prev_id = str(previous_state.active_project_id or "").strip()
                next_id = str(project_id).strip()
                metadata = dict(project.metadata or {})
                project_path = str(
                    project.workspace_root
                    or metadata.get("project_path")
                    or metadata.get("workspace_root")
                    or metadata.get("path")
                    or ""
                ).strip()
                previous_path = str(previous_state.project_path or "").strip()
                try:
                    path_changed = bool(previous_path or project_path) and os.path.normcase(
                        os.path.abspath(previous_path)
                    ) != os.path.normcase(os.path.abspath(project_path))
                except (OSError, ValueError):
                    path_changed = previous_path != project_path
                scope_changed = prev_id != next_id or path_changed
                # ProjectManager owns Project identity/root. A real scope change
                # invalidates projected session authority; an idempotent refresh
                # must not discard approvals or retry state.
                if scope_changed and (prev_id or previous_path or previous_state.pending_approval_id):
                    self._clear_session_project_scope(
                        reason="Invalidated because the active Project changed",
                        stop_preview=True,
                    )
                self._active_project_id = project_id
                self._state_store.update_thread_state(
                    self._thread_key(),
                    active_project_id=project_id,
                    project_path=project_path,
                    workspace_root=project_path,
                )
                try:
                    from agent.tools import set_active_project_root

                    if project_path:
                        set_active_project_root(project_path)
                except Exception:
                    pass
                logger.info(f"Activated project: {project.name}")
                return True
            logger.warning(f"Project not found: {project_id}")
            return False
        except Exception as e:
            logger.warning(f"Failed to activate project: {e}")
            return False

    def configure_workspace(self, workspace_id: Optional[str]) -> None:
        workspace_id = (workspace_id or "").strip()
        self._workspace_id = workspace_id or None
        skills_dir = Path(getattr(config, "skills_dir", "") or "").expanduser()
        workspaces_dir = Path(getattr(config, "workspaces_dir", "") or "").expanduser()
        try:
            skills_dir.mkdir(parents=True, exist_ok=True)
            workspaces_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        skills = load_skills(skills_dir)
        workspace = load_workspace(workspaces_dir, workspace_id) if workspace_id else None
        skill_defs = []
        if workspace is not None:
            for skill_id in workspace.skill_ids:
                skill = skills.get(skill_id)
                if skill is not None:
                    skill_defs.append(skill)
        self._active_skill_defs = list(skill_defs)
        self._skills_prompt = build_skills_prompt(skill_defs)
        self._workspace_prompt = (workspace.prompt if workspace else "").strip()
        self._workspace_name = (workspace.name if workspace else "").strip()

        # Skill tools join the shared ToolRegistry; the lean Toolbox reads it per turn.
        # A skill's TOOLS.txt is prompt metadata only, never a runtime ceiling.
        for skill_def in skill_defs:
            load_skill_tools(skills_dir / skill_def.id)
        self._skills_fingerprint = self._compute_skills_fingerprint(skills_dir, workspaces_dir, workspace_id)
        self._state_store.update_thread_state(self._thread_key(), workspace_id=str(self._workspace_id or ""))

    def _compute_skills_fingerprint(self, skills_dir: Path, workspaces_dir: Path, workspace_id: str) -> str:
        h = hashlib.sha256()
        base_paths: list[Path] = []
        try:
            if skills_dir.exists():
                base_paths.append(skills_dir)
        except Exception:
            pass
        try:
            if workspaces_dir.exists() and workspace_id:
                ws = (workspaces_dir / workspace_id)
                if ws.exists():
                    base_paths.append(ws)
        except Exception:
            pass

        for base in base_paths:
            try:
                for p in sorted(base.rglob("*")):
                    try:
                        if not p.is_file():
                            continue
                        if p.name.startswith("."):
                            continue
                        if p.suffix.lower() not in {".md", ".txt", ".json"}:
                            continue
                        st = p.stat()
                        h.update(str(p).encode("utf-8", errors="ignore"))
                        h.update(str(int(st.st_mtime_ns)).encode("utf-8"))
                        h.update(str(int(st.st_size)).encode("utf-8"))
                    except Exception:
                        continue
            except Exception:
                continue
        return h.hexdigest()

    def _load_webhook_secret(self) -> str:
        secret = str(getattr(config, "webhook_secret", "") or "").strip()
        if secret:
            return secret
        path_val = str(getattr(config, "webhook_secret_path", "") or "").strip()
        if not path_val:
            return ""
        path = Path(path_val).expanduser()
        try:
            if path.exists():
                return path.read_text(encoding="utf-8").strip()
        except Exception:
            return ""
        return ""

    def get_doctor_report(self) -> Dict[str, Any]:
        if self.llm_provider == ModelProvider.OPENAI:
            provider_model = config.openai.model
        elif self.llm_provider == ModelProvider.GEMINI:
            provider_model = config.gemini.model
        else:
            provider_model = config.local.model_name
        provider_base_url = None
        if self.llm_provider not in (ModelProvider.OPENAI, ModelProvider.GEMINI, ModelProvider.LLAMA_CPP):
            provider_base_url = config.local.base_url

        provider_ok = True
        provider_notes: list[str] = []
        if self.llm_provider == ModelProvider.OPENAI:
            api_key = config.openai.api_key or os.getenv("OPENAI_API_KEY", "")
            if not api_key:
                provider_ok = False
                provider_notes.append("Missing OPENAI_API_KEY")
        elif self.llm_provider == ModelProvider.GEMINI:
            api_key = config.gemini.api_key or os.getenv("GEMINI_API_KEY", "")
            if not api_key:
                provider_ok = False
                provider_notes.append("Missing GEMINI_API_KEY")
        elif self.llm_provider not in (ModelProvider.LLAMA_CPP,):
            if not (provider_base_url or "").strip():
                provider_ok = False
                provider_notes.append("Missing base_url for local provider")

        memory_ok = bool(getattr(self.memory, "embeddings", None)) or not bool(getattr(self.memory, "use_faiss", True))
        docs_enabled = bool(getattr(config, "document_rag_enabled", False))
        docs_ok = not docs_enabled or self.document_store is not None

        cron_enabled = bool(getattr(config, "cron_enabled", False))
        cron_available = importlib.util.find_spec("croniter") is not None
        webhook_enabled = bool(getattr(config, "webhook_enabled", False))
        webhook_secret = self._load_webhook_secret()
        webhook_ok = not webhook_enabled or bool(webhook_secret)
        routine_webhook_count = 0
        try:
            from agent.routines import get_routine_manager

            routine_manager = get_routine_manager()
            routine_webhook_count = len([
                routine for routine in routine_manager.list_routines(enabled_only=True)
                if str(getattr(routine, "trigger_type", "") or "") == "webhook"
                and str(getattr(routine, "webhook_path", "") or "").strip()
            ])
        except Exception:
            routine_webhook_count = 0

        file_root = str(getattr(config, "file_tool_root", "") or ".").strip() or "."
        term_deny = [
            str(x).strip().lower()
            for x in (getattr(config, "terminal_command_denylist", None) or [])
            if str(x).strip()
        ]
        discord_diag: Dict[str, Any] = {
            "enabled": bool(getattr(config, "allow_discord_bot", False)),
            "token_set": bool(getattr(config, "discord_bot_token", "")),
            "auto_confirm": bool(getattr(config, "discord_bot_auto_confirm", False)),
            "owner_id_set": bool(str(getattr(config, "discord_bot_owner_id", "") or "").strip()),
            "uses_shared_process_query": True,
            "wrapper_marker_supported": True,
            "last_source": str(getattr(self, "_current_source", "") or ""),
            "last_thread_id": str(getattr(self, "_current_thread_id", "") or ""),
            "last_user_role": str(getattr(self, "_current_user_role", "") or ""),
            "bot_running": False,
            "has_loop": False,
            "guild_count": 0,
        }
        try:
            from discord_bot import get_bot

            bot = get_bot()
            if bot is not None:
                try:
                    discord_diag["bot_running"] = bool(bot.is_running())
                except Exception:
                    discord_diag["bot_running"] = False
                discord_diag["has_loop"] = bool(getattr(bot, "_loop", None))
                client = getattr(bot, "client", None)
                guilds = list(getattr(client, "guilds", []) or []) if client is not None else []
                discord_diag["guild_count"] = len(guilds)
        except Exception as exc:
            discord_diag["error"] = str(exc)[:200]
        integrations_diag: Dict[str, Any] = {"discord": discord_diag}

        telegram_diag: Dict[str, Any] = {
            "enabled": bool(getattr(config, "allow_telegram_bot", False)),
            "token_set": bool(getattr(config, "telegram_bot_token", "")),
            "allowed_users_count": len(list(getattr(config, "telegram_allowed_users", []) or [])),
            "auto_confirm": bool(getattr(config, "telegram_auto_confirm", False)),
            "running": False,
        }
        try:
            from telegram_bot import get_telegram_bot

            tg = get_telegram_bot()
            telegram_diag["running"] = bool(tg and tg.is_running())
        except Exception as exc:
            telegram_diag["error"] = str(exc)[:200]
        integrations_diag["telegram"] = telegram_diag

        twitch_diag: Dict[str, Any] = {
            "enabled": bool(getattr(config, "allow_twitch", False)),
            "client_id_set": bool(getattr(config, "twitch_client_id", "")),
            "client_secret_set": bool(getattr(config, "twitch_client_secret", "")),
            "bot_token_set": bool(getattr(config, "twitch_bot_access_token", "")),
            "eventsub_secret_set": bool(getattr(config, "twitch_eventsub_secret", "")),
            "running": False,
        }
        try:
            import twitch_bot as _twitch_mod

            twi = getattr(_twitch_mod, "_twitch_bot", None)
            if twi is not None:
                twitch_diag["running"] = bool(twi.is_running())
        except Exception as exc:
            twitch_diag["error"] = str(exc)[:200]
        integrations_diag["twitch"] = twitch_diag

        twitter_diag: Dict[str, Any] = {
            "enabled": bool(getattr(config, "allow_twitter", False)),
            "bearer_token_set": bool(getattr(config, "twitter_bearer_token", "")),
            "access_token_set": bool(getattr(config, "twitter_access_token", "")),
            "access_token_secret_set": bool(getattr(config, "twitter_access_token_secret", "")),
            "running": False,
        }
        try:
            import twitter_bot as _twitter_mod

            tw = getattr(_twitter_mod, "_twitter_bot", None)
            if tw is not None:
                twitter_diag["running"] = bool(tw.is_running())
        except Exception as exc:
            twitter_diag["error"] = str(exc)[:200]
        integrations_diag["twitter"] = twitter_diag
        issues: list[str] = []
        if not provider_ok:
            issues.append("provider")
        if not memory_ok:
            issues.append("memory")
        if not docs_ok:
            issues.append("documents")
        if cron_enabled and not cron_available:
            issues.append("croniter")
        if not webhook_ok:
            issues.append("webhook_secret")
        if routine_webhook_count > 0 and not webhook_secret:
            issues.append("routine_webhooks_unsigned")
        if discord_diag["enabled"] and discord_diag["token_set"] and not discord_diag["bot_running"]:
            issues.append("discord_bot")
        if telegram_diag["enabled"] and telegram_diag["token_set"] and not telegram_diag["running"]:
            issues.append("telegram_bot")
        if twitch_diag["enabled"] and not (twitch_diag["client_id_set"] and twitch_diag["client_secret_set"]):
            issues.append("twitch_config")
        if twitter_diag["enabled"] and not (twitter_diag["bearer_token_set"] or twitter_diag["access_token_set"]):
            issues.append("twitter_config")

        return {
            "ok": len(issues) == 0,
            "issues": issues,
            "provider": {
                "id": self.llm_provider.value,
                "model": provider_model,
                "base_url": provider_base_url,
                "ok": provider_ok,
                "notes": provider_notes,
            },
            "memory": {
                "path": str(getattr(self.memory, "memory_path", "")),
                "use_faiss": bool(getattr(self.memory, "use_faiss", False)),
                "file_memory_enabled": bool(getattr(self.memory, "file_memory_enabled", False)),
                "ok": memory_ok,
            },
            "documents": {
                "enabled": docs_enabled,
                "ok": docs_ok,
            },
            "workspace": self.project_scope_report(),
            "tools": {
                "count": len(ToolRegistry.get_names()),
            },
            "tool_calling": self._tool_calling_diagnostics(),
            "discord": discord_diag,
            "integrations": integrations_diag,
            "features": {
                "action_parser_enabled": bool(getattr(config, "action_parser_enabled", True)),
                "system_actions": bool(getattr(config, "enable_system_actions", False)),
                "allow_file_write": bool(getattr(config, "allow_file_write", False)),
                "allow_terminal_commands": bool(getattr(config, "allow_terminal_commands", False)),
                "terminal_denylist": term_deny,
                "file_tool_root": file_root,
                "cron_enabled": cron_enabled,
                "croniter_available": cron_available,
                "webhook_enabled": webhook_enabled,
                "webhook_secret_set": bool(webhook_secret),
                "routine_webhook_count": routine_webhook_count,
                "routine_webhooks_signed": bool(webhook_secret),
            },
        }

    def _format_doctor_report(self, report: Dict[str, Any]) -> str:
        status = "OK" if report.get("ok") else "CHECK"
        lines = [f"Doctor report ({status})"]
        provider = report.get("provider") or {}
        prov_line = f"Provider: {provider.get('id')} ({provider.get('model')})"
        if not provider.get("ok"):
            prov_line += " [check]"
        lines.append(prov_line)
        for note in provider.get("notes") or []:
            lines.append(f"  - {note}")

        memory = report.get("memory") or {}
        mem_line = "OK" if memory.get("ok") else "CHECK"
        lines.append(
            f"Memory: {mem_line} (faiss={memory.get('use_faiss')}, file={memory.get('file_memory_enabled')})"
        )

        docs = report.get("documents") or {}
        docs_line = "OK" if docs.get("ok") else "CHECK"
        lines.append(f"Docs: {docs_line} (enabled={docs.get('enabled')})")

        workspace = report.get("workspace") or {}
        lines.append(f"Workspace: {workspace.get('id') or 'none'}")

        tools = report.get("tools") or {}
        lines.append(f"Tools: {tools.get('count', 0)} available")

        tool_calling = report.get("tool_calling") or {}
        if tool_calling:
            lines.append(f"Tool calling: native={tool_calling.get('native_tool_calling_enabled')}")

        discord = report.get("discord") or {}
        if discord:
            lines.append(
                "Discord bot: "
                + f"enabled={discord.get('enabled')} "
                + f"running={discord.get('bot_running')} "
                + f"shared_core={discord.get('uses_shared_process_query')}"
            )

        integrations = report.get("integrations") or {}
        if isinstance(integrations, dict) and integrations:
            parts: list[str] = []
            for name in ("telegram", "twitch", "twitter"):
                item = integrations.get(name) or {}
                if isinstance(item, dict):
                    parts.append(f"{name}=enabled:{item.get('enabled')} running:{item.get('running')}")
            if parts:
                lines.append("Integrations: " + " | ".join(parts))

        features = report.get("features") or {}
        lines.append(
            f"Action Parser: {'enabled' if features.get('action_parser_enabled') else 'disabled'}"
        )
        sa = "enabled" if features.get("system_actions") else "disabled"
        lines.append(
            "System actions: "
            + sa
            + f" (file_write={features.get('allow_file_write')}, terminal={features.get('allow_terminal_commands')})"
        )
        lines.append(f"FILE_TOOL_ROOT: {features.get('file_tool_root')}")
        term_deny = features.get("terminal_denylist") or []
        if isinstance(term_deny, list):
            lines.append(
                "TERMINAL_COMMAND_DENYLIST: "
                + (", ".join(term_deny) if term_deny else "(empty)")
            )
        cron_line = "enabled" if features.get("cron_enabled") else "disabled"
        cron_check = "ok" if features.get("croniter_available") else "missing"
        lines.append(f"Cron: {cron_line} (croniter={cron_check})")
        webhook_line = "enabled" if features.get("webhook_enabled") else "disabled"
        webhook_check = "set" if features.get("webhook_secret_set") else "missing"
        lines.append(f"Webhook: {webhook_line} (secret={webhook_check})")
        if int(features.get("routine_webhook_count") or 0) > 0:
            lines.append(
                f"Routine webhooks: {features.get('routine_webhook_count')} "
                + f"(signed={features.get('routine_webhooks_signed')})"
            )

        if report.get("issues"):
            lines.append("Issues: " + ", ".join(report["issues"]))
        return "\n".join(lines)

    def format_doctor_report(self, report: Optional[Dict[str, Any]] = None) -> str:
        if report is None:
            report = self.get_doctor_report()
        return self._format_doctor_report(report)

    def _tool_policy_flags_satisfied(self, name: str) -> bool:
        from agent.tool_registry import ToolRegistry

        flags = ToolRegistry.get_permission_flags(name)
        for flag in flags:
            attr_name = str(flag or "").strip().lower()
            if attr_name and not bool(getattr(config, attr_name, False)):
                return False
        return True

    def _allow_llm_tool_calling(self) -> bool:
        """Equal access: every configured provider may attempt native tool calling.

        No provider- or model-name allowlists. Native-call parsing remains in
        the selected model-family adapter; malformed responses stay inside the
        bounded canonical repair loop.
        Explicit opt-out only via DISABLE_NATIVE_TOOL_CALLING=true.
        USE_TOOL_CALLING_LLM remains an Ollama format-wrapper opt-in and does
        not gate whether tool-capable stages may be attempted.
        """
        if bool(getattr(config, "disable_native_tool_calling", False)):
            return False
        return True

    def _tool_calling_diagnostics(self) -> Dict[str, Any]:
        selected_model_id = self._selected_model_id()
        family_adapter = get_family_adapter(selected_model_id, self.llm_provider.value)
        native_supported = bool(family_adapter.capabilities.native_tool_calls)
        native_enabled = bool(self._allow_llm_tool_calling() and native_supported)
        return {
            "provider": self.llm_provider.value,
            "model": selected_model_id,
            "model_family": family_adapter.family.value,
            "chat_template": family_adapter.template,
            "adapter_version": family_adapter.version,
            "model_turn_contract_version": "8.0.0",
            "semantic_runtime": "lean",
            "execution_loop": "lean",
            "native_tool_calling_supported": native_supported,
            "native_tool_calling_enabled": native_enabled,
            "action_parser_enabled": bool(getattr(config, "action_parser_enabled", True)),
            "printed_tool_syntax_executable": False,
            "lmstudio_tool_calling": bool(getattr(config, "lmstudio_tool_calling", False)),
            "use_tool_calling_llm": bool(getattr(config, "use_tool_calling_llm", False)),
            "disable_native_tool_calling": bool(getattr(config, "disable_native_tool_calling", False)),
        }

    def _selected_model_id(self) -> str:
        """Return the bound model id without requiring test/provider shims to own it."""
        return str(
            getattr(getattr(self, "model_runtime", None), "model_id", "")
            or dict(getattr(self, "provider_info", {}) or {}).get("model")
            or getattr(getattr(config, "local", None), "model_name", "")
            or "default"
        )

    def _tool_calling_mode_label(self) -> str:
        diag = self._tool_calling_diagnostics()
        if diag.get("native_tool_calling_enabled"):
            return "canonical_native_tool_calls"
        if diag.get("action_parser_enabled"):
            return "canonical_structured_decision"
        return "canonical_text_only"

    def get_last_doc_sources(self) -> list:
        return list(self._last_doc_sources or [])

    def _is_action_tool(self, tool_name: str) -> bool:
        return ToolRegistry.is_action(tool_name)

    def _action_configured(self, tool_name: str) -> bool:
        if tool_name == "open_chrome":
            return bool(getattr(config, "enable_system_actions", False) and getattr(config, "allow_open_chrome", False))
        if tool_name == "browse_task":
            return bool(getattr(config, "enable_system_actions", False) and getattr(config, "allow_playwright", False))
        if tool_name in {"discord_read_channel", "discord_send_channel"}:
            return bool(getattr(config, "allow_discord_bot", False))
        if tool_name in {"discord_web_send", "discord_contacts_discover"}:
            return bool(getattr(config, "enable_system_actions", False) and getattr(config, "allow_playwright", False))
        if tool_name == "open_application":
            return bool(getattr(config, "enable_system_actions", False) and getattr(config, "allow_open_application", False))
        if tool_name in {"desktop_click", "desktop_type_text", "desktop_activate_window", "desktop_send_hotkey"}:
            return bool(getattr(config, "enable_system_actions", False) and getattr(config, "allow_desktop_automation", False))
        if tool_name == "file_write":
            return bool(getattr(config, "enable_system_actions", False) and getattr(config, "allow_file_write", False))
        if tool_name in {"file_move", "file_copy", "file_delete", "file_mkdir", "checkpoint_undo"}:
            return bool(getattr(config, "enable_system_actions", False) and getattr(config, "allow_file_write", False))
        if tool_name == "artifact_write":
            return bool(getattr(config, "enable_system_actions", False) and getattr(config, "allow_file_write", False))
        if tool_name == "notepad_write":
            return bool(
                getattr(config, "enable_system_actions", False)
                and getattr(config, "allow_open_application", False)
                and getattr(config, "allow_desktop_automation", False)
                and getattr(config, "allow_file_write", False)
            )
        if tool_name == "terminal_run":
            return bool(getattr(config, "enable_system_actions", False) and getattr(config, "allow_terminal_commands", False))
        if tool_name in {"email_send", "email_reply"}:
            return bool(getattr(config, "allow_email", False))
        if tool_name == "whatsapp_send":
            return bool(getattr(config, "allow_whatsapp", False))
        if tool_name in {"self_edit", "self_rollback", "self_git_status", "self_read", "self_grep", "self_list"}:
            return bool(getattr(config, "enable_system_actions", False) and getattr(config, "allow_self_modification", False))
        entry = ToolRegistry.get(tool_name)
        if entry is not None and entry.category == "mcp":
            if entry.is_action:
                return bool(getattr(config, "enable_system_actions", False))
            return True
        if tool_name == "project_status":
            return True  # Safe read-only tool, always allowed
        # Registry policy flags are the configuration authority for extension
        # actions. Flagless action plugins remain explicit system-action opt-in.
        if entry is not None and entry.is_action:
            return bool(
                self._tool_policy_flags_satisfied(tool_name)
                and (entry.policy_flags or getattr(config, "enable_system_actions", False))
            )
        return False

    def _thread_key(self, thread_id: Optional[str] = None) -> str:
        value = str(thread_id or getattr(self, "_current_thread_id", "default") or "default").strip()
        return value or "default"

    def select_thread_runtime(self, thread_id: Optional[str]) -> str:
        """Point this pooled agent at one Session; durable scope comes from StateStore."""
        key = str(thread_id or "default").strip() or "default"
        self._current_thread_id = key
        state = self._state_store.get_thread_state(key)
        # Project selection is Session-owned: reset shared-agent residue first.
        self._active_project_id = str(state.active_project_id or "").strip() or None
        return key

    def _session_permissions_snapshot(self) -> dict[str, bool]:
        return {
            "system_actions": bool(getattr(config, "enable_system_actions", False)),
            "file_write": bool(getattr(config, "allow_file_write", False)),
            "terminal": bool(getattr(config, "allow_terminal_commands", False)),
            "desktop": bool(getattr(config, "allow_desktop_automation", False)),
            "playwright": bool(getattr(config, "allow_playwright", False)),
            "open_application": bool(getattr(config, "allow_open_application", False)),
            "open_chrome": bool(getattr(config, "allow_open_chrome", False)),
            "email": bool(getattr(config, "allow_email", False)),
            "whatsapp": bool(getattr(config, "allow_whatsapp", False)),
            "discord_bot": bool(getattr(config, "allow_discord_bot", False)),
            "self_modification": bool(getattr(config, "allow_self_modification", False)),
            "voice_actions": bool(getattr(config, "allow_voice_actions", False)),
            "generation_actions": bool(getattr(config, "allow_generation_actions", False)),
        }

    def project_scope_report(self, thread_id: Optional[str] = None) -> dict[str, Any]:
        """Authoritative Session Project scope for capabilities / readiness UIs.

        interaction_mode (chat/research/coding skill workspace) is independent of
        Project attachment. Never report skill-workspace id as the Project name.
        """
        key = self._thread_key(thread_id)
        state = self._state_store.get_thread_state(key)
        project_id = str(state.active_project_id or getattr(self, "_active_project_id", None) or "").strip()
        project_path = str(state.project_path or state.workspace_root or "").strip()
        project_name = ""
        authorized_paths: list[str] = []
        if project_id:
            try:
                from agent.projects import get_project_manager

                project = get_project_manager().get_project(project_id)
                if project is not None:
                    project_name = str(project.name or "").strip()
                    project_path = str(project.workspace_root or project_path or "").strip()
            except Exception:
                pass
        if project_path:
            authorized_paths = [project_path]
        perms = self._session_permissions_snapshot()
        interaction_mode = str(getattr(self, "_workspace_id", None) or state.workspace_id or "").strip() or "chat"
        # skill workspace display name only when no Project is attached
        skill_ws_name = str(getattr(self, "_workspace_name", None) or "").strip()
        return {
            # Do not use "chat" as the Project/workspace identity when a Project is attached.
            "id": project_id or None,
            "name": project_name or ("none" if not project_id else project_id),
            "interaction_mode": interaction_mode,
            "skill_workspace_id": interaction_mode,
            "skill_workspace_name": skill_ws_name or None,
            "project_attached": bool(project_id and project_path),
            "project_id": project_id or None,
            "workspace_name": project_name or "none",
            "project_path": project_path or None,
            "authorized_paths": authorized_paths,
            "permissions": {
                "filesystem_read": bool(project_id and project_path),
                "filesystem_write": bool(project_id and project_path and perms.get("file_write") and perms.get("system_actions")),
                "terminal": bool(project_id and project_path and perms.get("terminal") and perms.get("system_actions")),
                "browser": bool(perms.get("playwright") and perms.get("system_actions")),
                "desktop": bool(perms.get("desktop") and perms.get("system_actions")),
                "system_actions": bool(perms.get("system_actions")),
            },
        }

    def _capability_registry(self) -> dict[str, dict[str, Any]]:
        """Machine-readable capabilities derived from the real registered inventory."""
        registered = frozenset(ToolRegistry.get_names())
        specs: dict[str, dict[str, Any]] = {
            "research": {
                "supported_task": "Gather and synthesize current evidence",
                "required_tools": ["web_search"],
                "preconditions": ["A concrete research question"],
                "permissions": [],
                "configuration": ["At least one search provider"],
                "limitations": ["Read-only; source quality can limit conclusions"],
                "composes_with": ["coding", "conversation"],
            },
            "filesystem_read": {
                "supported_task": "Inspect project files and directories",
                "required_tools": ["file_list", "file_read"],
                "preconditions": ["A thread workspace or project root"],
                "permissions": [],
                "configuration": ["FILE_TOOL_ROOT or an active project"],
                "limitations": ["Restricted to the current thread scope"],
                "composes_with": ["coding", "research"],
            },
            "filesystem_write": {
                "supported_task": "Create or modify project files",
                "required_tools": ["file_write"],
                "preconditions": ["A thread project root", "Confirmation when required"],
                "permissions": ["system_actions", "file_write"],
                "configuration": [],
                "limitations": ["Cannot write outside the thread project scope"],
                "composes_with": ["filesystem_read", "verification"],
            },
            "terminal": {
                "supported_task": "Run project-local verification or build commands",
                "required_tools": ["terminal_run"],
                "preconditions": ["A thread project root", "Confirmation when required"],
                "permissions": ["system_actions", "terminal"],
                "configuration": [],
                "limitations": ["Command chaining is rejected; cwd is thread-scoped"],
                "composes_with": ["coding", "verification"],
            },
            "conversation": {
                "supported_task": "Answer and maintain conversational continuity",
                "required_tools": [],
                "preconditions": [],
                "permissions": [],
                "configuration": [],
                "limitations": ["Current facts require research"],
                "composes_with": ["research", "coding"],
            },
        }
        permissions = self._session_permissions_snapshot()
        for name, spec in specs.items():
            required = list(spec.get("required_tools") or [])
            installed = all(tool in registered for tool in required)
            configured = all(bool(permissions.get(flag, False)) for flag in spec.get("permissions") or [])
            spec["installed"] = installed
            spec["configured"] = configured
            spec["status"] = (
                "direct" if not required else "tool_supported" if installed and configured
                else "blocked_configuration" if installed else "unsupported"
            )
        return specs

    def process_query(
        self,
        user_input: str,
        include_memory: bool = True,
        callbacks: Optional[list] = None,
        thread_id: Optional[str] = None,
        source: Optional[str] = None,
        discord_user_info: Optional[Dict[str, Any]] = None,
        requested_approval_id: Optional[str] = None,
        cancel_event: Optional[threading.Event] = None,
        request_id: str = "",
        thinking_enabled: bool = True,
        reasoning_effort: str = "medium",
        caller_role: str = "",
    ) -> tuple:
        """Run one turn on the lean runtime (every channel uses this path).

        ``caller_role`` is owner / trusted / public. When it is not given it is
        worked out from the source (and the Discord user, for Discord).
        """
        from agent.adapters import get_adapter
        from agent.lean.runtime import run_lean_query

        if not caller_role:
            role = get_adapter(source).resolve_role(source, discord_user_info)
            caller_role = str(getattr(role, "value", role) or "public").lower()

        def emit(event: dict) -> None:
            for callback in list(callbacks or []):
                put = getattr(callback, "_put", None)
                if callable(put):
                    put(event)

        result = run_lean_query(
            self,
            message=user_input,
            session_id=str(thread_id or "default").strip() or "default",
            request_id=request_id,
            emit=emit,
            cancel=cancel_event or threading.Event(),
            source=str(source or "web"),
            thinking_enabled=thinking_enabled,
            reasoning_effort=reasoning_effort,
            caller_role=caller_role,
        )
        self._request_result_local.execution_id = str(result.get("execution_id") or "")
        return str(result.get("response") or ""), bool(result.get("success"))

    def completed_execution_id_for_current_worker(self) -> str:
        """The Execution this worker thread just finished, immune to later Session work."""
        return str(getattr(self._request_result_local, "execution_id", "") or "")
