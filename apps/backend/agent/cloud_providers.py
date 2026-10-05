"""Cloud endpoints and account-scoped model discovery; no keys leave the backend."""
from __future__ import annotations

from typing import Any
import httpx
from config import config

CLOUD_PROVIDERS = ("openai", "gemini", "anthropic", "xai")
CLOUD_LABELS = {"openai": "OpenAI", "gemini": "Google Gemini", "anthropic": "Claude", "xai": "Grok"}
BASE_URLS = {"openai": "https://api.openai.com/v1", "gemini": "https://generativelanguage.googleapis.com/v1beta/openai", "anthropic": "https://api.anthropic.com/v1", "xai": "https://api.x.ai/v1"}

def cloud_config(provider: Any):
    return getattr(config, str(getattr(provider, "value", provider)))

def cloud_headers(provider: str, key: str) -> dict[str, str]:
    if provider == "gemini":
        return {"x-goog-api-key": key}
    if provider == "anthropic":
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
        workspace = str(config.anthropic.workspace_id or "").strip()
        if workspace:
            headers["anthropic-workspace-id"] = workspace
        return headers
    return {"Authorization": f"Bearer {key}"}

def default_cloud_provider() -> str:
    selected = str(config.default_cloud_provider or "openai")
    ready = [p for p in CLOUD_PROVIDERS if cloud_config(p).api_key.strip()]
    # An explicit selection must not silently route a chat to another company.
    return selected if selected in CLOUD_PROVIDERS else (ready[0] if ready else "openai")

def provider_error(provider: str, status: int, detail: str = "") -> str:
    label = CLOUD_LABELS.get(provider, provider)
    if status in (401, 403):
        return f"{label} rejected the API key or access (HTTP {status}). Check the {label} API key in Settings → Models and your account's model access."
    if status == 429:
        return f"{label} rate limit or quota reached (HTTP 429). Check API billing/quota or try again later."
    if status == 404:
        return f"{label} cannot find the selected model (HTTP 404). Refresh models in Settings and use its exact API model ID."
    key = str(cloud_config(provider).api_key or "") if provider in CLOUD_PROVIDERS else ""
    return f"{label} returned HTTP {status}: {(detail.replace(key, '[redacted]') if key else detail)[:500]}"

def _model_entry(provider: str, item: dict) -> dict:
    model = str(item.get("id") or item.get("name") or "").removeprefix("models/")
    low = model.lower()
    methods = item.get("supportedGenerationMethods") or []
    live = provider == "gemini" and ("bidiGenerateContent" in methods or "live" in low or "native-audio" in low)
    chat = True
    reason = ""
    if provider == "gemini":
        chat = live or "generateContent" in methods
        if any(s in low for s in ("imagen", "veo", "embedding", "tts", "lyria", "image")):
            chat = False
    elif provider == "openai":
        chat = not any(s in low for s in ("embedding", "whisper", "tts", "dall-e", "image", "realtime", "audio", "moderation", "transcribe", "sora", "search", "codex", "-pro")) and low.startswith(("gpt-", "chatgpt-", "o1", "o3", "o4"))
    elif provider == "xai":
        chat = not any(s in low for s in ("image", "video", "imagine", "embed", "voice"))
    if not chat:
        reason = "Specialized model; unavailable in the chat picker"
    return {"id": model, "name": item.get("displayName") or item.get("display_name") or model, "chat": chat, "live": live, "reason": reason}

def list_cloud_models(provider: str, api_key: str | None = None) -> dict:
    key = str(api_key if api_key and api_key != "***" else cloud_config(provider).api_key).strip()
    result = {"provider": provider, "models": [], "catalog": [], "reachable": False, "message": ""}
    if not key or key == "***":
        result["message"] = f"Save your {CLOUD_LABELS[provider]} API key to load its available models."
        return result
    headers = cloud_headers(provider, key)
    url = f"{BASE_URLS[provider]}/models"
    if provider == "gemini":
        url = "https://generativelanguage.googleapis.com/v1beta/models"
    entries: dict[str, dict] = {}
    params: dict = {"pageSize": 1000} if provider == "gemini" else ({"limit": 1000} if provider == "anthropic" else {})
    try:
        with httpx.Client(timeout=15, follow_redirects=False) as client:
            seen: set[str] = set()
            for _ in range(100):
                response = client.get(url, headers=headers, params=params)
                if response.status_code >= 400:
                    result["message"] = provider_error(provider, response.status_code, response.text.replace(key, "[redacted]"))
                    return result
                data = response.json()
                for item in data.get("models" if provider == "gemini" else "data", []):
                    entry = _model_entry(provider, item)
                    if entry["id"]:
                        entries[entry["id"]] = entry
                token = data.get("nextPageToken") if provider == "gemini" else (data.get("last_id") if data.get("has_more") else None)
                if not token:
                    break
                if token in seen:
                    raise ValueError("Repeated model catalog page")
                seen.add(token)
                params["pageToken" if provider == "gemini" else "after_id"] = token
            else:
                raise ValueError("Model catalog exceeded pagination limit")
        result.update(catalog=sorted(entries.values(), key=lambda m: m["id"]), models=sorted(m["id"] for m in entries.values() if m["chat"]), reachable=True)
        result["message"] = f"Connected · {len(entries)} models listed · {len(result['models'])} available for chat"
    except (httpx.HTTPError, ValueError, TypeError):
        result["message"] = f"Couldn't load {CLOUD_LABELS[provider]} models. Check your internet connection and API key, then refresh."
    return result
