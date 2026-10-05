"""Shared pieces every route module uses: the Session agent pool, model binding, Project scope,
the lean stream runner, query cancellation and channel runtimes (Discord, heartbeat).
"""

import os
import queue
import asyncio
import threading
import time
from collections import OrderedDict
from typing import Optional, Dict, Any

from fastapi import HTTPException
from loguru import logger

from config import config, ModelProvider, read_runtime_override_payload
from agent.state import get_state_store


_ENV_LM_STUDIO_ONLY = str(os.getenv("LM_STUDIO_ONLY", "")).strip().lower() in ("1", "true", "yes", "on")


def _is_lmstudio_only_enabled() -> bool:
    """Return whether 'LM Studio Only' mode is enabled.

    Priority:
    1) Runtime overrides from settings.json (GUI-controlled)
    2) Environment variable LM_STUDIO_ONLY (fallback default)
    """
    try:
        overrides = _read_runtime_settings()
        if isinstance(overrides, dict) and "lm_studio_only" in overrides:
            return bool(overrides.get("lm_studio_only"))
    except Exception:
        pass
    return bool(_ENV_LM_STUDIO_ONLY)


def _default_cloud_provider() -> "ModelProvider":
    """Choose a sensible default cloud provider when none is explicitly selected."""
    from agent.cloud_providers import default_cloud_provider
    return ModelProvider(default_cloud_provider())


def _default_model_for_provider(provider: "ModelProvider") -> str:
    from agent.cloud_providers import CLOUD_PROVIDERS, cloud_config
    if provider.value in CLOUD_PROVIDERS:
        return str(cloud_config(provider).model or "").strip()
    return str(config.local.model_name or "").strip()


def _ensure_session_model_binding(session_id: str):
    key = _normalize_thread_id(session_id)
    if _is_lmstudio_only_enabled():
        provider = ModelProvider.LM_STUDIO
    else:
        provider = config.local.provider if config.use_local_models else _default_cloud_provider()
    store = get_state_store()
    binding = store.ensure_session_model_binding(
        key,
        provider_id=provider.value,
        model_id=_default_model_for_provider(provider) or "default",
        provider_configuration_id="global-default",
    )
    if (
        _is_lmstudio_only_enabled()
        and binding.provider_id != ModelProvider.LM_STUDIO.value
    ):
        previous_revision = binding.binding_revision
        binding = store.update_session_model_binding(
            key,
            provider_id=ModelProvider.LM_STUDIO.value,
            model_id=_default_model_for_provider(ModelProvider.LM_STUDIO) or "default",
            expected_revision=binding.binding_revision,
            provider_configuration_id="global-default",
        )
        cancel_incompatible = globals().get("_cancel_incompatible_session_work")
        if callable(cancel_incompatible):
            cancel_incompatible(
                key,
                reason=(
                    "LM Studio-only configuration changed Session provider binding from revision "
                    f"{previous_revision} to {binding.binding_revision}"
                ),
            )
    return binding


_agent_pool: "OrderedDict[str, Any]" = OrderedDict()
_agent_pool_lock = threading.Lock()
_agent_pool_max = 8
_discord_bot_task: Optional[asyncio.Task] = None
_discord_bot_token_value: str = ""


def forget_discord_runtime() -> None:
    """Drop the handle on a stopped Discord bot (server shutdown)."""
    global _discord_bot_task, _discord_bot_token_value
    _discord_bot_task = None
    _discord_bot_token_value = ""

_metrics_lock = threading.Lock()
_metrics = {
    "requests": 0,
    "errors": 0,
    "tool_calls": 0,
    "tool_errors": 0,
}


def _read_runtime_settings() -> dict:
    try:
        data = read_runtime_override_payload(include_secrets=True, migrate_legacy=True)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _normalize_thread_id(thread_id: Optional[str]) -> str:
    if thread_id is None:
        return "default"
    val = str(thread_id).strip()
    return val or "default"


def get_agent(thread_id: Optional[str] = None):
    """Get or create the agent instance.

    Canonical Turns always use a Session-keyed agent actor. The pre-8.0 shared
    mutable agent switch is ignored because it cannot safely run unrelated
    Sessions concurrently under separate Session locks.
    """
    from agent.core import EchoSpeakAgent

    key = _normalize_thread_id(thread_id)
    with _agent_pool_lock:
        binding = _ensure_session_model_binding(key)
        existing = _agent_pool.pop(key, None)
        if existing is not None:
            existing_provider = str(getattr(getattr(existing, "llm_provider", None), "value", "") or "")
            existing_model = str(getattr(getattr(existing, "model_runtime", None), "model_id", "") or "")
            if existing_provider == binding.provider_id and existing_model == binding.model_id:
                _agent_pool[key] = existing
                return existing
            logger.info("Recreating Session agent because its model binding changed: {}", key)
        provider = ModelProvider(binding.provider_id)

        agent = EchoSpeakAgent(
            llm_provider=provider,
            manage_background_services=(key == "default"),
            model_id=binding.model_id,
        )
        _agent_pool[key] = agent
        while len(_agent_pool) > _agent_pool_max:
            _agent_pool.popitem(last=False)
        return agent


def get_existing_agent(thread_id: Optional[str] = None):
    """Get an already-initialized agent without creating a new one."""
    key = _normalize_thread_id(thread_id)
    with _agent_pool_lock:
        existing = _agent_pool.get(key)
        if existing is not None:
            _agent_pool.move_to_end(key)
        return existing


def _discord_process_query(
    user_input: str,
    include_memory: bool = True,
    callbacks: list | None = None,
    thread_id: str | None = None,
    source: str | None = None,
    discord_user_info: dict | None = None,
    untrusted_sources: list | None = None,
):
    agent = get_agent(thread_id)
    return agent.process_query(
        user_input=user_input,
        include_memory=include_memory,
        callbacks=callbacks,
        thread_id=thread_id,
        source=source or "discord_bot",
        discord_user_info=discord_user_info,
        untrusted_sources=untrusted_sources,
    )


async def _discord_startup_health_check() -> None:
    try:
        await asyncio.sleep(6)
        from discord_bot import get_bot

        bot = get_bot()
        if bot is None:
            logger.error("Discord bot health-check: bot instance is None (startup failed or never created)")
            return
        running = False
        try:
            running = bool(bot.is_running())
        except Exception:
            running = False
        logger.info(
            f"Discord bot health-check: running={running} has_loop={bool(getattr(bot, '_loop', None))}"
        )
        if not running:
            logger.error(
                "Discord bot does not appear to be connected. "
                "If the bot shows as online in Discord, check privileged Gateway Intents (Message Content) in the Developer Portal. "
                "Otherwise verify DISCORD_BOT_TOKEN/ALLOW_DISCORD_BOT and look for 'Discord bot background task crashed' logs."
            )
    except Exception as exc:
        logger.warning(f"Discord bot health-check failed: {exc}")


async def _reconcile_discord_bot_runtime() -> None:
    global _discord_bot_task, _discord_bot_token_value

    try:
        from discord_bot import get_bot, start_discord_bot, stop_discord_bot
    except Exception as exc:
        logger.warning(f"Discord bot module unavailable: {exc}")
        return

    desired_token = str(getattr(config, "discord_bot_token", "") or "").strip()
    desired_enabled = bool(getattr(config, "allow_discord_bot", False) and desired_token)

    bot = get_bot()
    running = bool(bot and bot.is_running())
    token_changed = bool(_discord_bot_token_value and desired_token and _discord_bot_token_value != desired_token)

    if (not desired_enabled) or token_changed:
        if bot is not None:
            try:
                await stop_discord_bot()
            except Exception as exc:
                logger.warning(f"Failed to stop Discord bot: {exc}")
        _discord_bot_task = None
        if not desired_enabled:
            _discord_bot_token_value = ""
            return
        bot = get_bot()
        running = bool(bot and bot.is_running())

    if desired_enabled and not running:
        try:
            started_bot = await start_discord_bot(
                token=desired_token,
                process_query_func=_discord_process_query,
                agent_name="EchoSpeak",
            )
            _discord_bot_task = getattr(started_bot, "_task", None)
            _discord_bot_token_value = desired_token
            logger.info("Discord bot startup initiated")
            asyncio.create_task(_discord_startup_health_check())
        except Exception as exc:
            _discord_bot_task = None
            logger.warning(f"Failed to start Discord bot: {exc}")
    elif desired_enabled:
        _discord_bot_task = getattr(bot, "_task", None) if bot is not None else None
        _discord_bot_token_value = desired_token


_heartbeat_runtime_lock = threading.Lock()


async def _reconcile_heartbeat_runtime() -> None:
    from agent.heartbeat import HeartbeatManager, get_heartbeat_manager, set_heartbeat_manager

    desired_enabled = bool(getattr(config, "heartbeat_enabled", False))
    heartbeat_project_id = str(getattr(config, "heartbeat_project_id", "") or "").strip()
    heartbeat_session_id = str(getattr(config, "heartbeat_session_id", "") or "").strip()
    if desired_enabled and (not heartbeat_project_id or not heartbeat_session_id):
        logger.error("Heartbeat requires HEARTBEAT_PROJECT_ID and HEARTBEAT_SESSION_ID")
        desired_enabled = False

    with _heartbeat_runtime_lock:
        hb = get_heartbeat_manager()
        if not desired_enabled:
            if hb is not None and hb.is_running:
                hb.stop()
            return

        agent = get_agent()
        hb = get_heartbeat_manager()
        if hb is None:
            hb = HeartbeatManager(
                agent=agent,
                project_id=heartbeat_project_id,
                session_id=heartbeat_session_id,
            )
            set_heartbeat_manager(hb)
        else:
            hb.set_agent(agent)
            hb.update_config(
                interval_minutes=getattr(config, "heartbeat_interval", 30),
                prompt=getattr(config, "heartbeat_prompt", ""),
                channels=list(getattr(config, "heartbeat_channels", ["web"])),
                project_id=heartbeat_project_id,
                session_id=heartbeat_session_id,
            )

        if not hb.is_running:
            hb.start()


def _apply_thread_scope(agent, thread_id: Optional[str], workspace_override: Optional[str] = None) -> dict[str, Any]:
    store = get_state_store()
    normalized_thread_id = _normalize_thread_id(thread_id)
    if hasattr(agent, "select_thread_runtime"):
        agent.select_thread_runtime(normalized_thread_id)
    else:
        setattr(agent, "_current_thread_id", normalized_thread_id)
    state = store.get_thread_state(normalized_thread_id)

    workspace_value = str(workspace_override or "").strip()
    if workspace_value:
        if workspace_value.lower() in {"auto", "default", "none", "clear"}:
            agent.configure_workspace(None)
            workspace_id = ""
        else:
            agent.configure_workspace(workspace_value)
            workspace_id = workspace_value
    else:
        workspace_id = str(state.workspace_id or "").strip()
        agent.configure_workspace(workspace_id or None)

    project_id = str(state.active_project_id or "").strip()
    # The agent instance is shared, but Project scope is Session-owned. Never
    # copy the previously selected Session's Project into a fresh Session.
    if project_id:
        if project_id != str(getattr(agent, "_active_project_id", None) or ""):
            agent.activate_project(project_id)
    else:
        # Full detach transaction — do not leave soft path / ActiveWork / preview.
        if str(getattr(agent, "_active_project_id", None) or "") or str(state.project_path or ""):
            if hasattr(agent, "_clear_session_project_scope"):
                agent._clear_session_project_scope(
                    thread_id=normalized_thread_id,
                    reason="Session has no attached Project",
                )
            else:
                setattr(agent, "_active_project_id", None)
        else:
            setattr(agent, "_active_project_id", None)
    updated = store.get_thread_state(normalized_thread_id)
    # Keep workspace_id / provider stamps without re-writing a cleared project id.
    updated = store.update_thread_state(
        normalized_thread_id,
        workspace_id=str(getattr(agent, "_workspace_id", None) or updated.workspace_id or ""),
        runtime_provider=str(getattr(getattr(agent, "llm_provider", None), "value", getattr(agent, "llm_provider", "")) or ""),
    )
    return updated.model_dump()


def _metric_inc(key: str, amount: int = 1) -> None:
    with _metrics_lock:
        if key not in _metrics:
            _metrics[key] = 0
        _metrics[key] += amount


# Tool name → agent_mode classification for visualizer


def _safe_stream_failure(exc: BaseException) -> str:
    name = type(exc).__name__.casefold()
    detail = str(exc or "").casefold()
    if "cancel" in name or "cancel" in detail:
        return "Stopped by Ty."
    if "timeout" in name or "stall" in name or "timed out" in detail:
        return "Verified work is preserved; the remaining details were not reliable enough to state."
    if "connect" in name or "unavailable" in detail:
        return "The model is unavailable right now. Nothing was changed."
    return "The turn ended with a scoped recovery result; verified work is preserved."


def _run_lean_stream(
    *,
    agent,
    message: str,
    thread_id: Optional[str],
    request_id: str,
    q: queue.Queue,
    cancel_event: threading.Event,
    source: str,
    voice_turn_id: str = "",
    agent_id: str = "",
    thinking_enabled: bool = True,
    reasoning_effort: str = "medium",
) -> None:
    """Stream one lean-runtime turn as NDJSON events (see agent/lean/loop.py)."""
    from agent.lean.runtime import run_lean_query

    session_id = _normalize_thread_id(thread_id) or "default"
    try:
        result = run_lean_query(
            agent,
            message=message,
            session_id=session_id,
            request_id=request_id,
            emit=q.put,
            cancel=cancel_event,
            source=source,
            persona_id=agent_id,
            thinking_enabled=thinking_enabled,
            reasoning_effort=reasoning_effort,
        )
        state_store = get_state_store()
        if voice_turn_id and result.get("execution_id"):
            try:
                from agent.voice_transport import bind_voice_turn_submission

                bind_voice_turn_submission(
                    voice_turn_id,
                    session_id=session_id,
                    request_id=request_id,
                    execution_id=str(result["execution_id"]),
                    task_run_id="",
                    query_completed=True,
                )
            except Exception:
                logger.warning("Voice turn binding failed for lean turn request_id={}", request_id)
        memory_count = 0
        try:
            memory_count = int(agent.memory.count_items(thread_id=session_id) or 0)
        except Exception:
            pass
        q.put({
            "type": "final",
            "runtime": "lean",
            "response": str(result.get("response") or ""),
            "messages": list(result.get("messages") or []),
            "success": bool(result.get("success")),
            "memory_count": memory_count,
            "execution_id": result.get("execution_id"),
            "thread_state": state_store.get_thread_state(session_id).model_dump(),
            "voice_turn_id": voice_turn_id or None,
            "request_id": request_id,
            "at": time.time(),
        })
    except Exception as exc:
        _metric_inc("errors", 1)
        logger.exception("Lean stream worker failed request_id={}", request_id)
        q.put({
            "type": "error",
            "message": _safe_stream_failure(exc),
            "request_id": request_id,
            "at": time.time(),
        })
    finally:
        q.put({"type": "status", "agent_mode": "idle", "at": time.time(), "request_id": request_id})
        q.put(None)


def _start_agent_thread(
    *,
    agent,
    message: str,
    include_memory: bool,
    thread_id: Optional[str],
    workspace: Optional[str],
    request_id: str,
    q: queue.Queue,
    cancel_event: threading.Event,
    thinking_enabled: bool = True,
    reasoning_effort: str = "medium",
    source: str = "web",
    voice_turn_id: str = "",
    agent_id: str = "",
) -> None:
    threading.Thread(
        target=_run_lean_stream,
        kwargs=dict(
            agent=agent, message=message, thread_id=thread_id, request_id=request_id,
            q=q, cancel_event=cancel_event, source=source,
            voice_turn_id=voice_turn_id, agent_id=agent_id,
            thinking_enabled=thinking_enabled, reasoning_effort=reasoning_effort,
        ),
        name="lean-turn",
        daemon=True,
    ).start()


def _cancel_incompatible_session_work(session_id: str, *, reason: str) -> dict[str, int]:
    """Terminalize only work bound to the Session's previous model revision."""
    key = _normalize_thread_id(session_id)
    approval_count = 0
    for approval in get_state_store().list_approvals(thread_id=key, limit=1000):
        if approval.status not in {"pending", "consuming"}:
            continue
        get_state_store().update_approval(
            approval.id, status="canceled", outcome_summary=reason
        )
        approval_count += 1
    state = get_state_store().get_thread_state(key)
    get_state_store().update_thread_state(
        key,
        foreground_task_id="",
        suspended_task_ids=[],
        pending_approval_id="",
        pending_actions=[],
        execution_status="cancelled",
        safest_next_action="Start new work under the selected Session model",
        source_metadata={
            **dict(state.source_metadata or {}),
            "model_binding_cancellation_reason": reason,
            "model_binding_cancelled_at": time.time(),
        },
    )
    return {"approvals": approval_count}


_ACTIVE_QUERY_CANCEL_LOCK = threading.RLock()
_ACTIVE_QUERY_CANCELLATIONS: Dict[str, tuple[str, threading.Event, str]] = {}


def _register_query_cancellation(request_id: str, thread_id: str, event: threading.Event) -> None:
    with _ACTIVE_QUERY_CANCEL_LOCK:
        _ACTIVE_QUERY_CANCELLATIONS[str(request_id)] = (_normalize_thread_id(thread_id), event, "")


def _release_query_cancellation(request_id: str, event: threading.Event) -> None:
    with _ACTIVE_QUERY_CANCEL_LOCK:
        current = _ACTIVE_QUERY_CANCELLATIONS.get(str(request_id))
        if current is not None and current[1] is event:
            _ACTIVE_QUERY_CANCELLATIONS.pop(str(request_id), None)


# === Automation scope ===

def _require_automation_project_scope(session_id: str, project_id: str = "") -> str:
    key = str(session_id or "").strip()
    if not key:
        raise HTTPException(status_code=422, detail="Session scope is required")
    state = get_state_store().get_thread_state(key)
    active_project_id = str(state.active_project_id or "").strip()
    requested_project_id = str(project_id or active_project_id).strip()
    if not requested_project_id or active_project_id != requested_project_id:
        raise HTTPException(status_code=409, detail="Session is not bound to the requested Project")
    from agent.projects import get_project_manager

    if get_project_manager().get_project(requested_project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return requested_project_id
