"""Settings, model providers, the soul and the avatar config."""

import os
import json
import asyncio
import importlib.util
import threading
import time
from pathlib import Path
from typing import Optional, List, Dict, Any
from urllib.request import Request as UrlRequest, urlopen
from urllib.error import URLError, HTTPError

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from loguru import logger

from config import (
    config,
    DATA_DIR,
    ModelProvider,
    SECRET_NESTED_SETTINGS,
    SECRET_TOP_LEVEL_SETTINGS,
    _strip_mcp_secret_overrides,
    write_runtime_override_payload,
)
from agent.state import get_state_store
from api.deps import (
    _ACTIVE_QUERY_CANCELLATIONS,
    _ACTIVE_QUERY_CANCEL_LOCK,
    _agent_pool,
    _agent_pool_lock,
    _cancel_incompatible_session_work,
    _default_cloud_provider,
    _default_model_for_provider,
    _ensure_session_model_binding,
    _is_lmstudio_only_enabled,
    _normalize_thread_id,
    _read_runtime_settings,
    _reconcile_discord_bot_runtime,
    _reconcile_heartbeat_runtime,
    _require_automation_project_scope,
)

router = APIRouter()
LM_STUDIO_DEFAULT_URL = "http://localhost:1234"


def _assert_provider_available(provider: "ModelProvider") -> None:
    if provider == ModelProvider.GEMINI:
        if importlib.util.find_spec("langchain_google_genai") is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Gemini provider requires 'langchain-google-genai' on the backend. "
                    "Install it in apps/backend venv: pip install langchain-google-genai"
                ),
            )


def _resolve_runtime_provider(session_id: Optional[str] = None) -> "ModelProvider":
    """Resolve the provider the next query would use without creating an agent."""
    if session_id:
        return ModelProvider(_ensure_session_model_binding(session_id).provider_id)
    if _is_lmstudio_only_enabled():
        return ModelProvider.LM_STUDIO
    return config.local.provider if config.use_local_models else _default_cloud_provider()


def _provider_default_base_url(provider: "ModelProvider") -> str:
    from agent.model_runtime import resolve_local_provider_base_url

    return resolve_local_provider_base_url(provider)


def _provider_configured_base_url(provider: "ModelProvider") -> str:
    from agent.model_runtime import resolve_local_provider_base_url

    configured = str(getattr(getattr(config, "local", None), "base_url", "") or "")
    return resolve_local_provider_base_url(provider, configured)


def _local_provider_models_url(provider: "ModelProvider", base_url: str) -> str:
    base = (base_url or _provider_default_base_url(provider)).rstrip("/")
    if provider == ModelProvider.OLLAMA:
        return f"{base}/api/tags"
    if base.endswith("/v1"):
        return f"{base}/models"
    return f"{base}/v1/models"


def _provider_recovery_message(provider: "ModelProvider", detail: str = "") -> str:
    name = provider.value
    if provider == ModelProvider.LM_STUDIO:
        return (
            "LM Studio is selected, but I cannot reach its local server. "
            f"Start LM Studio, load a model, enable the local server at {LM_STUDIO_DEFAULT_URL}, "
            "then try again. You can also switch EchoSpeak to another provider."
        )
    if provider == ModelProvider.OLLAMA:
        return (
            "Ollama is selected, but I cannot reach the Ollama server. "
            "Start Ollama, make sure a model is installed, then try again."
        )
    if provider in (ModelProvider.LOCALAI, ModelProvider.VLLM):
        return (
            f"{name} is selected, but I cannot reach its local model server. "
            "Start the server or switch EchoSpeak to another provider, then try again."
        )
    if provider == ModelProvider.OPENAI:
        return "OpenAI is selected, but no OpenAI API key is configured. Add an API key or switch provider."
    if provider == ModelProvider.GEMINI:
        return "Gemini is selected, but Gemini is not ready. Add a Gemini API key/dependency or switch provider."
    if provider == ModelProvider.LLAMA_CPP:
        return "llama.cpp is selected, but its local model path is not ready. Check the model path or switch provider."
    return f"Model provider {name} is not ready. {detail}".strip()


def _check_provider_readiness(
    provider: Optional["ModelProvider"] = None,
    timeout: float = 1.5,
    *,
    model_id: str = "",
) -> dict[str, Any]:
    """Fast query preflight so provider outages become clear user-facing failures."""
    p = provider or _resolve_runtime_provider()
    try:
        _assert_provider_available(p)
    except HTTPException as exc:
        return {
            "ok": False,
            "provider": p.value,
            "message": _provider_recovery_message(p, str(exc.detail)),
            "detail": str(exc.detail),
        }

    if p == ModelProvider.OPENAI:
        key = str(getattr(getattr(config, "openai", None), "api_key", "") or "").strip()
        return {
            "ok": bool(key),
            "provider": p.value,
            "message": "" if key else _provider_recovery_message(p),
            "detail": "" if key else "Missing OPENAI_API_KEY",
        }

    if p == ModelProvider.GEMINI:
        key = str(getattr(getattr(config, "gemini", None), "api_key", "") or "").strip()
        return {
            "ok": bool(key),
            "provider": p.value,
            "message": "" if key else _provider_recovery_message(p),
            "detail": "" if key else "Missing GEMINI_API_KEY",
        }

    if p == ModelProvider.LLAMA_CPP:
        model_path = str(model_id or getattr(getattr(config, "local", None), "model_name", "") or "").strip()
        ok = bool(model_path and Path(model_path).exists())
        return {
            "ok": ok,
            "provider": p.value,
            "message": "" if ok else _provider_recovery_message(p),
            "detail": "" if ok else f"Model path not found: {model_path or '(empty)'}",
        }

    if p in (ModelProvider.OLLAMA, ModelProvider.LM_STUDIO, ModelProvider.LOCALAI, ModelProvider.VLLM):
        base_url = _provider_configured_base_url(p)
        url = _local_provider_models_url(p, base_url)
        try:
            req = UrlRequest(url, headers={"Accept": "application/json"})
            with urlopen(req, timeout=timeout) as resp:
                status = int(getattr(resp, "status", 200) or 200)
                if 200 <= status < 300:
                    if not hasattr(resp, "read"):
                        return {"ok": True, "provider": p.value, "message": "", "detail": ""}
                    payload_bytes = resp.read(512_001)
                    if len(payload_bytes) > 512_000:
                        raise ValueError("Provider model inventory exceeded 512 KB")
                    payload = json.loads(payload_bytes.decode("utf-8")) if payload_bytes else {}
                    rows = payload.get("models") if p == ModelProvider.OLLAMA else payload.get("data")
                    if not isinstance(rows, list):
                        rows = []
                    available_ids = {
                        str((item or {}).get("id") or (item or {}).get("name") or (item or {}).get("model") or "").strip().lower()
                        for item in rows
                        if isinstance(item, dict)
                    }
                    available_ids.discard("")
                    configured_model = str(model_id or getattr(getattr(config, "local", None), "model_name", "") or "").strip().lower()
                    model_loaded = bool(
                        configured_model
                        and any(
                            candidate == configured_model
                            or candidate.endswith("/" + configured_model)
                            or configured_model.endswith("/" + candidate)
                            for candidate in available_ids
                        )
                    )
                    if configured_model and not model_loaded:
                        detail = (
                            f"Configured model is not loaded: {configured_model}. "
                            f"Provider reported {len(available_ids)} model(s)."
                        )
                        return {
                            "ok": False,
                            "provider": p.value,
                            "message": _provider_recovery_message(p, detail),
                            "detail": detail,
                        }
                    return {"ok": True, "provider": p.value, "message": "", "detail": ""}
                detail = f"HTTP {status} from {url}"
        except HTTPError as exc:
            detail = f"HTTP {getattr(exc, 'code', '')} from {url}".strip()
        except URLError as exc:
            detail = f"Cannot connect to {url}: {getattr(exc, 'reason', exc)}"
        except TimeoutError:
            detail = f"Timed out connecting to {url}"
        except Exception as exc:
            detail = f"Cannot connect to {url}: {exc}"
        return {
            "ok": False,
            "provider": p.value,
            "message": _provider_recovery_message(p, detail),
            "detail": detail,
        }

    return {"ok": True, "provider": p.value, "message": "", "detail": ""}


def _copy_jsonish(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _copy_jsonish(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_copy_jsonish(v) for v in value]
    return value


def _redact_settings_payload(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {}
    out = _copy_jsonish(payload)
    for key in SECRET_TOP_LEVEL_SETTINGS:
        if key in out:
            out[key] = "" if str(out.get(key) or "").strip() == "" else "***"
    for section, secret_keys in SECRET_NESTED_SETTINGS.items():
        patch = out.get(section)
        if not isinstance(patch, dict):
            continue
        for secret_key in secret_keys:
            if secret_key in patch:
                patch[secret_key] = "" if str(patch.get(secret_key) or "").strip() == "" else "***"
    if isinstance(out.get("mcp_servers"), dict):
        out["mcp_servers"] = _strip_mcp_secret_overrides(out["mcp_servers"])
    return out


def _deep_merge(dst: dict, src: dict) -> dict:
    out = dict(dst)
    for k, v in (src or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out.get(k) or {}, v)
        else:
            out[k] = v
    return out


def _validate_settings_effective(effective: dict) -> list[dict]:
    """Return a list of validation issues.

    Each issue is {"key": "path.like.this", "message": "...", "severity": "error"|"warning"}.
    """
    issues: list[dict] = []
    s = effective or {}

    use_local = bool(s.get("use_local_models"))
    openai_api_key = ((s.get("openai") or {}).get("api_key") or "").strip()
    gemini_api_key = ((s.get("gemini") or {}).get("api_key") or "").strip()
    local_provider = ((s.get("local") or {}).get("provider") or "").strip()
    local_base_url = ((s.get("local") or {}).get("base_url") or "").strip()
    local_model = ((s.get("local") or {}).get("model_name") or "").strip()

    if use_local:
        if not local_provider:
            issues.append({"key": "local.provider", "message": "Local provider is required when Use Local Models is enabled.", "severity": "error"})
        if not local_base_url:
            issues.append({"key": "local.base_url", "message": "Local base URL is required when Use Local Models is enabled.", "severity": "error"})
        if not local_model:
            issues.append({"key": "local.model_name", "message": "Local model name is required when Use Local Models is enabled.", "severity": "error"})
    else:
        # Cloud provider: need either OpenAI or Gemini API key
        if not openai_api_key and not gemini_api_key:
            issues.append({"key": "cloud.api_key", "message": "An API key is required for cloud providers. Add either an OpenAI or Gemini API key.", "severity": "error"})

    embedding_provider = ((s.get("embedding") or {}).get("provider") or "").strip()
    if embedding_provider == "openai" and not openai_api_key:
        issues.append({"key": "embedding.provider", "message": "Embedding provider=openai has no OpenAI API key configured. EchoSpeak will fall back to local embeddings when available.", "severity": "warning"})

    enable_system_actions = bool(s.get("enable_system_actions"))
    allow_flags = [
        "allow_open_chrome",
        "allow_playwright",
        "allow_desktop_automation",
        "allow_file_write",
        "allow_terminal_commands",
        "allow_open_application",
        "allow_self_modification",
    ]
    if not enable_system_actions:
        for k in allow_flags:
            if bool(s.get(k)):
                issues.append({"key": k, "message": "Enable System Actions must be ON to enable this permission.", "severity": "error"})

    if bool(s.get("allow_terminal_commands")):
        denylist = s.get("terminal_command_denylist")
        if not isinstance(denylist, list):
            issues.append({"key": "terminal_command_denylist", "message": "Terminal commands are enabled but TERMINAL_COMMAND_DENYLIST is missing.", "severity": "warning"})
        root = str(s.get("file_tool_root") or "").strip()
        if not root:
            issues.append({"key": "file_tool_root", "message": "Set FILE_TOOL_ROOT to restrict terminal/file operations.", "severity": "warning"})

    if bool(s.get("allow_file_write")):
        root = str(s.get("file_tool_root") or "").strip()
        if not root:
            issues.append({"key": "file_tool_root", "message": "Set FILE_TOOL_ROOT to restrict file writes.", "severity": "warning"})

    api_host = str(((s.get("api") or {}).get("host") or "")).strip().lower()
    api_auth_enabled = bool(s.get("api_auth_enabled"))
    api_auth_key = str(s.get("api_auth_key") or "").strip()
    if api_auth_enabled and not api_auth_key:
        issues.append({"key": "api_auth_key", "message": "API auth is enabled but API_AUTH_KEY is empty.", "severity": "error"})
    if api_host in {"0.0.0.0", "::", "[::]"} and (not api_auth_enabled or not api_auth_key):
        issues.append({"key": "api_auth_enabled", "message": "API_HOST is network-facing. API authentication and a non-empty key are required.", "severity": "error"})

    if bool(s.get("webhook_enabled")):
        secret = str(s.get("webhook_secret") or "").strip()
        secret_path = str(s.get("webhook_secret_path") or "").strip()
        if not secret and not secret_path:
            issues.append({"key": "webhook_secret", "message": "Webhooks enabled but WEBHOOK_SECRET / WEBHOOK_SECRET_PATH is not set.", "severity": "error"})

    if bool(s.get("allow_open_application")):
        from config import _normalize_open_application_allowlist

        allowlist = _normalize_open_application_allowlist(s.get("open_application_allowlist"))
        if not allowlist:
            issues.append({"key": "open_application_allowlist", "message": "Application launching is enabled but OPEN_APPLICATION_ALLOWLIST is empty.", "severity": "error"})

    if bool(s.get("allow_self_modification")):
        issues.append({"key": "allow_self_modification", "message": "Self-modification is enabled. This is high-risk and should stay off outside controlled development sessions.", "severity": "warning"})

    if bool(s.get("allow_discord_bot")):
        token = str(s.get("discord_bot_token") or "").strip()
        owner_id = str(s.get("discord_bot_owner_id") or "").strip()
        allowed_users = s.get("discord_bot_allowed_users")
        allowed_roles = s.get("discord_bot_allowed_roles")
        if not token:
            issues.append({"key": "discord_bot_token", "message": "Discord bot is enabled but DISCORD_BOT_TOKEN is empty.", "severity": "error"})
        if not owner_id:
            issues.append({"key": "discord_bot_owner_id", "message": "Set DISCORD_BOT_OWNER_ID to enable owner-level Discord bot protections.", "severity": "warning"})
        has_allowed_users = isinstance(allowed_users, list) and any(str(x).strip() for x in allowed_users)
        has_allowed_roles = isinstance(allowed_roles, list) and any(str(x).strip() for x in allowed_roles)
        if not has_allowed_users and not has_allowed_roles:
            issues.append({"key": "discord_bot_allowed_roles", "message": "Discord bot server access is open. Set DISCORD_BOT_ALLOWED_ROLES for role-based server gating, or DISCORD_BOT_ALLOWED_USERS for explicit user allowlisting.", "severity": "warning"})

    if bool(s.get("allow_telegram_bot")):
        token = str(s.get("telegram_bot_token") or "").strip()
        allowed_users = s.get("telegram_allowed_users")
        if not token:
            issues.append({"key": "telegram_bot_token", "message": "Telegram bot is enabled but TELEGRAM_BOT_TOKEN is empty.", "severity": "error"})
        if not isinstance(allowed_users, list) or not any(str(x).strip() for x in allowed_users):
            issues.append({"key": "telegram_allowed_users", "message": "Telegram bot allowed users list is empty. Consider restricting access explicitly.", "severity": "warning"})

    if bool(s.get("allow_twitch")):
        if not str(s.get("twitch_client_id") or "").strip() or not str(s.get("twitch_client_secret") or "").strip():
            issues.append({"key": "twitch_client_secret", "message": "Twitch is enabled but TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET is incomplete.", "severity": "error"})
        if bool(s.get("twitch_chat_reply_enabled")) and not str(s.get("twitch_bot_access_token") or "").strip():
            issues.append({"key": "twitch_bot_access_token", "message": "Twitch chat replies are enabled but TWITCH_BOT_ACCESS_TOKEN is empty.", "severity": "error"})
        if str(s.get("twitch_eventsub_callback_url") or "").strip() and not str(s.get("twitch_eventsub_secret") or "").strip():
            issues.append({"key": "twitch_eventsub_secret", "message": "Twitch EventSub callback is configured but TWITCH_EVENTSUB_SECRET is empty.", "severity": "warning"})

    if bool(s.get("allow_twitter")):
        bearer = str(s.get("twitter_bearer_token") or "").strip()
        access = str(s.get("twitter_access_token") or "").strip()
        access_secret = str(s.get("twitter_access_token_secret") or "").strip()
        if not bearer and not access:
            issues.append({"key": "twitter_bearer_token", "message": "Twitter/X is enabled but no bearer or access token is configured.", "severity": "error"})
        if access and not access_secret:
            issues.append({"key": "twitter_access_token_secret", "message": "Twitter/X access token is set but TWITTER_ACCESS_TOKEN_SECRET is empty.", "severity": "error"})

    if bool(s.get("allow_calendar")) and not str(s.get("google_calendar_credentials_path") or "").strip():
        issues.append({"key": "google_calendar_credentials_path", "message": "Calendar integration is enabled but GOOGLE_CALENDAR_CREDENTIALS_PATH is empty.", "severity": "error"})

    if bool(s.get("allow_spotify")):
        if not str(s.get("spotify_client_id") or "").strip() or not str(s.get("spotify_client_secret") or "").strip():
            issues.append({"key": "spotify_client_secret", "message": "Spotify integration is enabled but SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET is incomplete.", "severity": "error"})

    if bool(s.get("allow_notion")) and not str(s.get("notion_token") or "").strip():
        issues.append({"key": "notion_token", "message": "Notion integration is enabled but NOTION_TOKEN is empty.", "severity": "error"})

    if bool(s.get("allow_github")) and not str(s.get("github_token") or "").strip():
        issues.append({"key": "github_token", "message": "GitHub integration is enabled but GITHUB_TOKEN is empty.", "severity": "error"})

    if bool(s.get("allow_home_assistant")):
        if not str(s.get("home_assistant_url") or "").strip() or not str(s.get("home_assistant_token") or "").strip():
            issues.append({"key": "home_assistant_token", "message": "Home Assistant integration is enabled but HOME_ASSISTANT_URL / HOME_ASSISTANT_TOKEN is incomplete.", "severity": "error"})

    if bool(s.get("allow_whatsapp")) and not str(s.get("whatsapp_api_url") or "").strip():
        issues.append({"key": "whatsapp_api_url", "message": "WhatsApp integration is enabled but WHATSAPP_API_URL is empty.", "severity": "error"})

    if bool(s.get("a2a_enabled")) and not str(s.get("a2a_auth_key") or "").strip():
        issues.append({"key": "a2a_auth_key", "message": "A2A is enabled without A2A_AUTH_KEY. Add an auth key before using this outside a trusted local environment.", "severity": "warning"})

    return issues


def _sanitize_incoming_settings(patch: dict) -> dict:
    if not isinstance(patch, dict):
        return {}

    nested_sections = {
        "openai": set(getattr(config.openai, "model_dump")().keys()),
        "gemini": set(getattr(config.gemini, "model_dump")().keys()),
        "local": set(getattr(config.local, "model_dump")().keys()),
        "patches": set(getattr(config.patches, "model_dump")().keys()),
        "embedding": set(getattr(config.embedding, "model_dump")().keys()),
        "voice": set(getattr(config.voice, "model_dump")().keys()),
        "personaplex": set(getattr(config.personaplex, "model_dump")().keys()),
        "api": set(getattr(config.api, "model_dump")().keys()),
        "soul": set(getattr(config.soul, "model_dump")().keys()),
    }
    known_top_level = set(config.to_public_dict().keys()) | {"lm_studio_only"}
    out: dict[str, Any] = {}
    for key, value in patch.items():
        if key in nested_sections:
            if not isinstance(value, dict):
                continue
            allowed_fields = nested_sections[key]
            section_patch = {k: v for k, v in value.items() if k in allowed_fields}
            if section_patch:
                out[key] = section_patch
            continue
        if key in known_top_level:
            out[key] = value

    # If the UI sends redacted placeholders, ignore them.
    openai_patch = out.get("openai")
    if isinstance(openai_patch, dict):
        val = openai_patch.get("api_key")
        if isinstance(val, str) and val.strip() == "***":
            openai_patch = dict(openai_patch)
            openai_patch.pop("api_key", None)
            out["openai"] = openai_patch

    gemini_patch = out.get("gemini")
    if isinstance(gemini_patch, dict):
        val = gemini_patch.get("api_key")
        if isinstance(val, str) and val.strip() == "***":
            gemini_patch = dict(gemini_patch)
            gemini_patch.pop("api_key", None)
            out["gemini"] = gemini_patch

    for secret_key in SECRET_TOP_LEVEL_SETTINGS:
        val = out.get(secret_key)
        if isinstance(val, str) and val.strip() == "***":
            out.pop(secret_key, None)

    if "open_application_allowlist" in out:
        from config import _normalize_open_application_allowlist

        out["open_application_allowlist"] = _normalize_open_application_allowlist(
            out.get("open_application_allowlist")
        )

    return out


def _force_lmstudio_config() -> None:
    config.use_local_models = True
    config.local.provider = ModelProvider.LM_STUDIO
    if not (config.local.base_url or "").strip():
        config.local.base_url = LM_STUDIO_DEFAULT_URL


def _cancel_active_queries_for_session(session_id: str) -> int:
    """Signal only queries owned by one exact Session."""
    key = _normalize_thread_id(session_id)
    with _ACTIVE_QUERY_CANCEL_LOCK:
        active = [
            row
            for row in _ACTIVE_QUERY_CANCELLATIONS.values()
            if row[0] == key
        ]
    for _owner, event, _execution_id in active:
        event.set()
    return len(active)


class SettingsResponse(BaseModel):
    settings: Dict[str, Any]
    overrides: Dict[str, Any]
    issues: List[Dict[str, Any]] = Field(default_factory=list)


class SettingsTestRequest(BaseModel):
    target: str = Field(..., description="openai | gemini | local | ollama | openai_compat")
    base_url: Optional[str] = None
    api_key: Optional[str] = None


class SettingsTestResponse(BaseModel):
    ok: bool
    target: str
    message: str
    latency_ms: Optional[float] = None


def _settings_response() -> "SettingsResponse":
    """Effective settings (redacted), the current override patch and any issues."""
    s = config.to_public_dict()
    # Not a config field: lives only in the override file (or LM_STUDIO_ONLY), so add it here.
    s["lm_studio_only"] = _is_lmstudio_only_enabled()
    overrides = _redact_settings_payload(_sanitize_incoming_settings(_read_runtime_settings()))
    return SettingsResponse(settings=s, overrides=overrides, issues=_validate_settings_effective(s))


@router.get("/settings", response_model=SettingsResponse)
async def get_settings():
    """Return the effective settings (redacted) and the current override patch."""
    return _settings_response()


def _http_get_json(url: str, headers: Optional[dict] = None, timeout_s: float = 6.0) -> tuple[int, Any]:
    req = UrlRequest(url, headers=headers or {}, method="GET")
    with urlopen(req, timeout=timeout_s) as resp:
        code = int(getattr(resp, "status", 200) or 200)
        raw = resp.read().decode("utf-8", errors="ignore")
        try:
            return code, json.loads(raw) if raw.strip() else {}
        except Exception:
            return code, {"raw": raw[:2000]}


def _normalize_base_url(url: str) -> str:
    u = (url or "").strip().rstrip("/")
    return u


@router.post("/settings/test", response_model=SettingsTestResponse)
def settings_test(request: SettingsTestRequest):
    target = (request.target or "").strip().lower()
    base_url = (request.base_url or "").strip() or None
    api_key = (request.api_key or "").strip() or None

    started = time.perf_counter()
    try:
        if target == "openai":
            key = api_key or (getattr(getattr(config, "openai", None), "api_key", "") or "").strip()
            if not key or key == "***":
                return SettingsTestResponse(ok=False, target=target, message="Missing OpenAI API key.")
            url = "https://api.openai.com/v1/models"
            code, _ = _http_get_json(url, headers={"Authorization": f"Bearer {key}"}, timeout_s=6.0)
            ok = 200 <= code < 300
            ms = (time.perf_counter() - started) * 1000.0
            return SettingsTestResponse(ok=ok, target=target, message=f"HTTP {code}", latency_ms=ms)

        if target == "gemini":
            key = api_key or (getattr(getattr(config, "gemini", None), "api_key", "") or "").strip()
            if not key or key == "***":
                return SettingsTestResponse(ok=False, target=target, message="Missing Gemini API key.")
            url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
            code, data = _http_get_json(url, timeout_s=6.0)
            ok = 200 <= code < 300
            ms = (time.perf_counter() - started) * 1000.0
            if ok:
                count = 0
                try:
                    count = len((data or {}).get("models") or [])
                except Exception:
                    count = 0
                return SettingsTestResponse(ok=True, target=target, message=f"OK (models={count})", latency_ms=ms)
            return SettingsTestResponse(ok=False, target=target, message=f"HTTP {code}", latency_ms=ms)

        if target in {"local", "openai_compat"}:
            url0 = base_url or (getattr(getattr(config, "local", None), "base_url", "") or "").strip()
            url0 = _normalize_base_url(url0)
            if not url0:
                return SettingsTestResponse(ok=False, target=target, message="Missing local base URL.")
            url = f"{url0}/v1/models"
            code, data = _http_get_json(url, timeout_s=5.0)
            ok = 200 <= code < 300
            ms = (time.perf_counter() - started) * 1000.0
            if ok:
                count = 0
                try:
                    count = len((data or {}).get("data") or [])
                except Exception:
                    count = 0
                return SettingsTestResponse(ok=True, target=target, message=f"OK (models={count})", latency_ms=ms)
            return SettingsTestResponse(ok=False, target=target, message=f"HTTP {code}", latency_ms=ms)

        if target == "ollama":
            url0 = base_url or (getattr(getattr(config, "local", None), "base_url", "") or "").strip()
            url0 = _normalize_base_url(url0)
            if not url0:
                return SettingsTestResponse(ok=False, target=target, message="Missing Ollama base URL.")
            url = f"{url0}/api/tags"
            code, data = _http_get_json(url, timeout_s=5.0)
            ok = 200 <= code < 300
            ms = (time.perf_counter() - started) * 1000.0
            if ok:
                models = 0
                try:
                    models = len((data or {}).get("models") or [])
                except Exception:
                    models = 0
                return SettingsTestResponse(ok=True, target=target, message=f"OK (models={models})", latency_ms=ms)
            return SettingsTestResponse(ok=False, target=target, message=f"HTTP {code}", latency_ms=ms)

        return SettingsTestResponse(ok=False, target=target, message="Unknown target. Use: openai | gemini | local | ollama | openai_compat")
    except HTTPError as e:
        ms = (time.perf_counter() - started) * 1000.0
        return SettingsTestResponse(ok=False, target=target, message=f"HTTP {getattr(e, 'code', 'error')}: {str(e)}", latency_ms=ms)
    except URLError as e:
        ms = (time.perf_counter() - started) * 1000.0
        return SettingsTestResponse(ok=False, target=target, message=f"Network error: {str(e)}", latency_ms=ms)
    except Exception as e:
        ms = (time.perf_counter() - started) * 1000.0
        return SettingsTestResponse(ok=False, target=target, message=str(e), latency_ms=ms)


def _current_default_binding() -> tuple[str, str]:
    """Provider and model a chat that follows the defaults would use right now."""
    provider = config.local.provider if config.use_local_models else _default_cloud_provider()
    return provider.value, _default_model_for_provider(provider) or "default"


def _apply_settings_patch(patch: dict) -> None:
    """Persist a settings patch and keep provider, port and open chats consistent."""
    from agent.model_runtime import is_known_local_default_url, local_provider_default_url

    existing = _sanitize_incoming_settings(_read_runtime_settings())
    local_patch = patch.get("local") if isinstance(patch.get("local"), dict) else None
    if local_patch and "provider" in local_patch and "base_url" not in local_patch:
        # Picking another app moves the address with it, unless a custom address was typed.
        current_url = str(((existing.get("local") or {}).get("base_url")) or config.local.base_url or "")
        new_url = local_provider_default_url(str(local_patch.get("provider") or ""))
        if new_url and (not current_url.strip() or is_known_local_default_url(current_url)):
            local_patch["base_url"] = new_url
    old_default = _current_default_binding()
    merged = _deep_merge(existing, patch)
    write_runtime_override_payload(merged)
    try:
        config.reload()
    except Exception:
        # Config reload failure shouldn't brick the API; keep serving.
        pass
    new_default = _current_default_binding()
    if new_default != old_default:
        try:
            changed = get_state_store().retarget_default_bindings(
                old_provider_id=old_default[0],
                old_model_id=old_default[1],
                new_provider_id=new_default[0],
                new_model_id=new_default[1],
            )
            if changed:
                logger.info(f"Default model changed; {len(changed)} chat(s) now use {new_default[0]}:{new_default[1]}")
        except Exception as exc:
            logger.warning(f"Could not move chats to the new default model: {exc}")
    # IMPORTANT: the agent/LLM objects are cached in-process.
    # When settings change (model/provider/base_url/tool-calling flags), we must
    # rebuild agents so the new config is actually used.
    with _agent_pool_lock:
        _agent_pool.clear()


_AUTOCONFIG_LOCK = threading.Lock()
_AUTOCONFIG_LAST = 0.0


def _local_setup_chosen() -> bool:
    """The user (or a previous auto-setup) already picked where models run."""
    overrides = _read_runtime_settings() or {}
    if "use_local_models" in overrides:
        return True
    local = overrides.get("local") if isinstance(overrides.get("local"), dict) else {}
    if str(local.get("model_name") or "").strip():
        return True
    if os.getenv("USE_LOCAL_MODELS") or os.getenv("LOCAL_MODEL_NAME"):
        return True
    return bool(
        str(getattr(config.openai, "api_key", "") or "").strip()
        or str(getattr(config.gemini, "api_key", "") or "").strip()
    )


def _autoconfigure_local_provider(force: bool = False) -> Optional[dict]:
    """First run: if LM Studio or Ollama is running, point EchoSpeak at it automatically.

    Retries at most every 20 seconds until something is found, so starting LM Studio
    after EchoSpeak still works. Never overrides a choice the user already made.
    """
    from agent.model_runtime import detect_local_providers

    global _AUTOCONFIG_LAST
    if not force and _local_setup_chosen():
        return None
    with _AUTOCONFIG_LOCK:
        now = time.monotonic()
        if not force and now - _AUTOCONFIG_LAST < 20:
            return None
        _AUTOCONFIG_LAST = now
        found = next((row for row in detect_local_providers() if row.get("running")), None)
        if not found:
            return None
        _apply_settings_patch({
            "use_local_models": True,
            "local": {
                "provider": found["provider"],
                "base_url": found["base_url"],
                "model_name": found["models"][0],
            },
        })
        logger.info(f"Auto-configured local models: {found['provider']} at {found['base_url']} ({found['models'][0]})")
        return found


@router.get("/provider/detect")
async def detect_providers(apply: bool = Query(default=False)):
    """Which local model apps are running right now, and their loaded models."""
    from agent.model_runtime import detect_local_providers

    rows = await asyncio.to_thread(detect_local_providers)
    applied = None
    if apply:
        applied = await asyncio.to_thread(_autoconfigure_local_provider, True)
    return {"providers": rows, "applied": applied}


@router.put("/settings", response_model=SettingsResponse)
async def put_settings(req: Request):
    """Merge and persist runtime settings overrides.

    Secrets are accepted here, but they are stored separately from settings.json.
    """
    try:
        patch = await req.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}")

    patch = _sanitize_incoming_settings(patch if isinstance(patch, dict) else {})
    try:
        _apply_settings_patch(patch)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to write settings: {exc}")

    try:
        await _reconcile_discord_bot_runtime()
    except Exception as exc:
        logger.warning(f"Discord bot reconcile after settings save failed: {exc}")

    try:
        await _reconcile_heartbeat_runtime()
    except Exception as exc:
        logger.warning(f"Heartbeat reconcile after settings save failed: {exc}")

    return _settings_response()


class SoulResponse(BaseModel):
    """Response model for soul endpoint."""
    enabled: bool
    path: str
    content: str
    max_chars: int
    exists: bool


class SoulUpdateRequest(BaseModel):
    """Request model for updating soul content."""
    content: str = Field(..., description="New soul content (markdown)")


@router.get("/soul", response_model=SoulResponse)
async def get_soul():
    """Get current SOUL.md content and configuration."""
    soul_config = getattr(config, "soul", None)
    if soul_config is None:
        return SoulResponse(
            enabled=False,
            path="./SOUL.md",
            content="",
            max_chars=8000,
            exists=False
        )
    
    soul_path_str = getattr(soul_config, "path", "./SOUL.md")
    max_chars = getattr(soul_config, "max_chars", 8000)
    enabled = getattr(soul_config, "enabled", True)
    
    from agent.lean.soul import soul_path as _soul_path

    soul_path = _soul_path()
    
    content = ""
    exists = soul_path.exists()
    
    if exists:
        try:
            content = soul_path.read_text(encoding="utf-8").strip()
        except Exception as e:
            logger.warning(f"Failed to read SOUL.md: {e}")
    
    return SoulResponse(
        enabled=enabled,
        path=soul_path_str,
        content=content,
        max_chars=max_chars,
        exists=exists
    )


@router.put("/soul", response_model=SoulResponse)
async def update_soul(request: SoulUpdateRequest):
    """Update SOUL.md content."""
    soul_config = getattr(config, "soul", None)
    if soul_config is None:
        raise HTTPException(status_code=500, detail="Soul configuration not initialized")
    
    soul_path_str = getattr(soul_config, "path", "./SOUL.md")
    max_chars = getattr(soul_config, "max_chars", 8000)
    
    from agent.lean.soul import soul_path as _soul_path, write_atomic as _write_soul

    soul_path = _soul_path()
    
    # Validate content length
    content = request.content.strip()
    if len(content) > max_chars:
        raise HTTPException(
            status_code=422,
            detail=f"Soul content exceeds max_chars limit ({len(content)} > {max_chars})"
        )
    
    # Write to file
    try:
        _write_soul(soul_path, content)
        logger.info(f"Updated SOUL.md at {soul_path} ({len(content)} chars)")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to write SOUL.md: {e}")
    
    # The soul cache is keyed on file mtime and size, so the next turn reads it.
    return SoulResponse(
        enabled=getattr(soul_config, "enabled", True),
        path=soul_path_str,
        content=content,
        max_chars=max_chars,
        exists=True
    )


class ProviderInfoResponse(BaseModel):
    """Response model for provider information."""
    provider: str
    model: str
    local: bool
    base_url: Optional[str] = None
    available_providers: list
    context_window: int = 0
    max_output_tokens: int = 0
    ready: bool = True
    readiness_message: str = ""
    readiness_detail: str = ""
    model_profile: Dict[str, Any] = Field(default_factory=dict)
    session_id: str = "default"
    binding_revision: int = 1


class SwitchProviderRequest(BaseModel):
    """Request model for switching provider."""
    provider: str = Field(..., description="Provider ID (openai, gemini, ollama, lmstudio, localai, llama_cpp, vllm)")
    session_id: str = Field(min_length=1, max_length=200)
    expected_revision: int = Field(ge=1)
    model: Optional[str] = Field(default=None, description="Model name (or path for llama.cpp)")
    base_url: Optional[str] = Field(default=None, description="Base URL for local servers (Ollama/LM Studio/LocalAI/vLLM)")
    openai_model: Optional[str] = Field(default=None, description="OpenAI model override when provider=openai")
    gemini_model: Optional[str] = Field(default=None, description="Gemini model override when provider=gemini")


@router.get("/settings/catalog")
async def settings_catalog_api(
    session_id: str = Query(...),
    project_id: str = Query(default=""),
):
    """Read-only, secret-free Settings cards projected from canonical owners."""
    from agent.connection_lifecycle import get_connection_lifecycle_service
    from agent.settings_catalog import build_settings_catalog
    from agent.threads import get_thread_manager

    session_key = str(session_id or "").strip()
    if get_thread_manager().get_thread(session_key) is None:
        raise HTTPException(status_code=404, detail="Session not found")

    service = get_connection_lifecycle_service()
    scoped_project_id = (
        _require_automation_project_scope(session_key, project_id)
        if str(project_id or "").strip()
        else ""
    )
    connection_items = service.catalog(
        project_id=scoped_project_id,
        session_id=session_key,
    )
    provider_projection = await get_provider_info(session_key)
    projection = build_settings_catalog(
        runtime_config=config,
        provider_info=provider_projection.model_dump(mode="json"),
        connection_catalog=connection_items,
    )
    return projection.model_dump(mode="json")


@router.get("/provider", response_model=ProviderInfoResponse)
async def get_provider_info(session_id: Optional[str] = Query(default=None)):
    """
    Get current provider information.

    Returns:
        Current provider details and available providers.
    """
    from agent.model_runtime import list_available_providers, resolve_model_profile

    if not _local_setup_chosen():
        await asyncio.to_thread(_autoconfigure_local_provider)
    providers = list_available_providers()
    def _profile_for(prov: ModelProvider, model_name: str) -> dict[str, Any]:
        registry = dict(getattr(config, "model_capability_profiles", {}) or {})
        overrides = dict(registry.get(f"{prov.value}:{model_name}") or registry.get(model_name) or {})
        # Real configured window for every provider — no local 32k / hosted 128k guess.
        trim = int(getattr(config, "llm_trim_max_tokens", 0) or 0)
        ctx_len = int(getattr(config.local, "context_length", 0) or 0)
        real_window = trim or ctx_len
        if real_window > 0:
            overrides.setdefault("context_limit", real_window)
        return resolve_model_profile(prov.value, model_name, overrides).as_dict()

    if _is_lmstudio_only_enabled():
        _force_lmstudio_config()
        binding = _ensure_session_model_binding(session_id or "default")
        providers = [p for p in providers if p.get("id") == ModelProvider.LM_STUDIO.value]
        model_profile = _profile_for(ModelProvider.LM_STUDIO, binding.model_id)
        ctx_w, max_out = int(model_profile["context_limit"]), int(getattr(config.local, "max_tokens", 0) or 4096)
        readiness = _check_provider_readiness(ModelProvider.LM_STUDIO)
        return ProviderInfoResponse(
            provider=ModelProvider.LM_STUDIO.value,
            model=binding.model_id,
            local=True,
            base_url=_provider_configured_base_url(ModelProvider.LM_STUDIO),
            available_providers=providers,
            context_window=ctx_w,
            max_output_tokens=max_out,
            ready=bool(readiness.get("ok")),
            readiness_message=str(readiness.get("message") or ""),
            readiness_detail=str(readiness.get("detail") or ""),
            model_profile=model_profile,
            session_id=binding.session_id,
            binding_revision=binding.binding_revision,
        )

    # Do not instantiate the agent here; provider can be misconfigured (e.g. missing deps)
    # and we still want /provider to respond.
    binding = _ensure_session_model_binding(session_id or "default")
    provider = ModelProvider(binding.provider_id)
    is_local = provider not in (ModelProvider.OPENAI, ModelProvider.GEMINI)
    if provider == ModelProvider.OPENAI:
        model = binding.model_id
    elif provider == ModelProvider.GEMINI:
        model = binding.model_id
    else:
        model = binding.model_id
    base_url = (
        None
        if provider in (ModelProvider.OPENAI, ModelProvider.GEMINI, ModelProvider.LLAMA_CPP)
        else _provider_configured_base_url(provider)
    )
    model_profile = _profile_for(provider, model)
    ctx_w = int(model_profile["context_limit"])
    max_out = int(
        getattr(config.openai, "max_tokens", 0) if provider == ModelProvider.OPENAI
        else getattr(config.gemini, "max_tokens", 0) if provider == ModelProvider.GEMINI
        else getattr(config.local, "max_tokens", 0)
        or 4096
    )
    readiness = _check_provider_readiness(provider, model_id=model)

    return ProviderInfoResponse(
        provider=provider.value,
        model=model,
        local=is_local,
        base_url=base_url,
        available_providers=providers,
        context_window=ctx_w,
        max_output_tokens=max_out,
        ready=bool(readiness.get("ok")),
        readiness_message=str(readiness.get("message") or ""),
        readiness_detail=str(readiness.get("detail") or ""),
        model_profile=model_profile,
        session_id=binding.session_id,
        binding_revision=binding.binding_revision,
    )


@router.post("/provider/switch")
async def switch_provider(request: SwitchProviderRequest):
    """
    Switch to a different model provider.

    Args:
        request: Switch provider request.

    Returns:
        Success message.
    """
    try:
        provider = ModelProvider(request.provider)
        if (
            _is_lmstudio_only_enabled()
            and provider != ModelProvider.LM_STUDIO
        ):
            raise HTTPException(
                status_code=403,
                detail="Only LM Studio models may be selected in LM Studio-only mode",
            )
        _assert_provider_available(provider)
        session_id = _normalize_thread_id(request.session_id)
        current_binding = _ensure_session_model_binding(session_id)
        if current_binding.binding_revision != request.expected_revision:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Session model binding changed from revision "
                    f"{request.expected_revision} to {current_binding.binding_revision}"
                ),
            )
        requested_model = (
            request.openai_model if provider == ModelProvider.OPENAI
            else request.gemini_model if provider == ModelProvider.GEMINI
            else request.model
        )
        selected_model = str(
            requested_model or _default_model_for_provider(provider)
        ).strip()
        if not selected_model:
            raise HTTPException(status_code=422, detail="A model id is required")
        from agent.model_runtime import is_known_local_default_url

        if request.base_url and not is_known_local_default_url(request.base_url):
            configured = str(
                _provider_configured_base_url(provider)
                if provider not in {ModelProvider.OPENAI, ModelProvider.GEMINI}
                else ""
            ).rstrip("/")
            if configured and request.base_url.rstrip("/") != configured:
                raise HTTPException(
                    status_code=409,
                    detail="Provider endpoints are global configuration; change the endpoint in Settings before binding this Session",
                )
        if (
            current_binding.provider_id == provider.value
            and current_binding.model_id == selected_model
        ):
            return {
                "success": True,
                "message": "Session already uses that provider and model",
                "provider": provider.value,
                "model": current_binding.model_id,
                "session_id": session_id,
                "binding_revision": current_binding.binding_revision,
                "cancelled_turns": 0,
                "cancelled_incompatible_work": {"approvals": 0},
            }
        cancelled = _cancel_active_queries_for_session(session_id)
        if cancelled:
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                with _ACTIVE_QUERY_CANCEL_LOCK:
                    if not any(
                        row[0] == session_id
                        for row in _ACTIVE_QUERY_CANCELLATIONS.values()
                    ):
                        break
                await asyncio.sleep(0.05)
        binding = get_state_store().update_session_model_binding(
            session_id,
            provider_id=provider.value,
            model_id=selected_model,
            expected_revision=request.expected_revision,
            provider_configuration_id=current_binding.provider_configuration_id,
        )
        retired = _cancel_incompatible_session_work(
            session_id,
            reason=(
                "Session model binding changed from revision "
                f"{current_binding.binding_revision} to {binding.binding_revision}"
            ),
        )

        with _agent_pool_lock:
            _agent_pool.pop(session_id, None)
        from agent.model_runtime import clear_structured_output_probe_cache
        clear_structured_output_probe_cache()

        return {
            "success": True,
            "message": f"Switched to {provider.value}",
            "provider": provider.value,
            "model": binding.model_id,
            "session_id": session_id,
            "binding_revision": binding.binding_revision,
            "cancelled_turns": cancelled,
            "cancelled_incompatible_work": retired,
        }
    except HTTPException:
        raise
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid provider: {request.provider}")
    except Exception as e:
        logger.error(f"Switch provider error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/provider/models")
async def list_provider_models(provider: Optional[str] = Query(default=None)):
    p = None
    if _is_lmstudio_only_enabled():
        p = ModelProvider.LM_STUDIO
    elif provider:
        try:
            p = ModelProvider(provider)
        except Exception:
            raise HTTPException(status_code=400, detail=f"Invalid provider: {provider}")
    else:
        p = config.local.provider if config.use_local_models else _default_cloud_provider()

    if p in (ModelProvider.OLLAMA, ModelProvider.LM_STUDIO, ModelProvider.LOCALAI, ModelProvider.VLLM):
        from agent.model_runtime import list_local_models

        base = _provider_configured_base_url(p)
        models = await asyncio.to_thread(list_local_models, p, base, 4.0)
        if not models:
            logger.warning(f"No {p.value} models found at {base}")
        return {"provider": p.value, "models": models, "base_url": base, "reachable": bool(models)}

    return {"provider": p.value, "models": []}

    if p in (ModelProvider.LM_STUDIO, ModelProvider.LOCALAI, ModelProvider.VLLM):
        try:
            import requests

            base = _provider_configured_base_url(p)
            if base.endswith("/v1"):
                url = f"{base}/models"
            else:
                url = f"{base}/v1/models"

            resp = requests.get(url, timeout=4)
            resp.raise_for_status()
            data = resp.json() or {}
            models = []
            for m in data.get("data") or []:
                model_id = m.get("id")
                if model_id:
                    models.append(model_id)
            return {"provider": p.value, "models": sorted(set(models))}
        except Exception as e:
            logger.warning(f"Failed to list {p.value} models: {e}")
            return {"provider": p.value, "models": []}

    return {"provider": p.value, "models": []}


# ── Todo List Endpoints ──────────────────────────────────────────────────────


# ── Avatar Config Endpoints ─────────────────────────────────────────────────

_AVATAR_CONFIG_FILE = DATA_DIR / "avatar_config.json"

_DEFAULT_AVATAR_CONFIG = {
    "body_color": "#ffffff",
    "eye_color": "#000000",
    "bg_color": "#0a0a0a",
    "glow_color": "#4f8eff",
    "idle_activity": "auto",
    "breathing_speed": 1.0,
    "eye_size": 1.0,
    "body_roundness": 14,
    "enable_glow": True,
    "enable_idle_activities": True,
    "custom_status_text": "",
}


def _load_avatar_config() -> dict:
    if _AVATAR_CONFIG_FILE.exists():
        try:
            return json.loads(_AVATAR_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return dict(_DEFAULT_AVATAR_CONFIG)


def _save_avatar_config(cfg: dict) -> None:
    _AVATAR_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    _AVATAR_CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


@router.get("/avatar/config")
async def get_avatar_config():
    """Get avatar customization config."""
    return _load_avatar_config()


@router.put("/avatar/config")
async def update_avatar_config(request: Request):
    """Update avatar customization config."""
    data = await request.json()
    cfg = _load_avatar_config()
    cfg.update(data)
    _save_avatar_config(cfg)
    return cfg


@router.post("/avatar/config/reset")
async def reset_avatar_config():
    """Reset avatar config to defaults."""
    _save_avatar_config(dict(_DEFAULT_AVATAR_CONFIG))
    return _DEFAULT_AVATAR_CONFIG
