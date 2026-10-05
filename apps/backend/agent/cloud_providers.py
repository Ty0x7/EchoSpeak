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
    key = str(cloud_config(provider).api_key or "") if provider in CLOUD_PROVIDERS else ""
    from agent.lean.policy import redact_secrets
    safe = redact_secrets(detail.replace(key, "[redacted]") if key else detail)
    category = cloud_error_category(status, safe)
    remedies = {
        "authentication": "API key rejected. Check the saved key and any account/IP restrictions.",
        "billing": "API credits or spending limit exhausted. Check API billing; retrying will not restore access.",
        "region": "API access is unavailable in this region. Check the provider's supported regions.",
        "permission": "Access denied. Check API key permissions and this account's access to the selected model.",
        "rate_limit": "Request rate limit reached. Wait briefly and reduce request frequency.",
        "model": "Model or endpoint unavailable. Refresh models and use an API model supported by this chat transport.",
        "overloaded": "Provider temporarily unavailable or overloaded. Try again shortly.",
        "request": "Request rejected. Check the selected model and its supported parameters.",
    }
    message = remedies.get(category, "Provider request failed.")
    if provider == "gemini" and "access_token_type_unsupported" in safe.lower():
        message = "Google rejected the saved credential type. Save a Gemini Developer API key from Google AI Studio, then refresh the model catalog and select an API model ID."
    return f"{label}: {message} (HTTP {status})." + (f" Details: {safe[:500]}" if safe else "")


def cloud_error_category(status: int, detail: str = "") -> str:
    low = detail.lower()
    if any(word in low for word in ("unsupported_country", "unsupported region", "country, region", "location is not supported")):
        return "region"
    if status == 402 or any(word in low for word in ("insufficient_quota", "billing_hard_limit", "credit balance", "exceeded your current quota", "spending limit", "billing_not_enabled")):
        return "billing"
    if status == 401 or any(word in low for word in ("api key not valid", "invalid_api_key", "reported as leaked", "unauthenticated")):
        return "authentication"
    if status == 403:
        return "permission"
    if status == 429:
        return "rate_limit"
    if status == 404 or any(word in low for word in ("model_not_found", "not supported in the v1", "not supported on this endpoint")):
        return "model"
    if status in (408, 500, 502, 503, 504, 529):
        return "overloaded"
    return "request"


def chat_model_issue(provider: str, model: str) -> str:
    low = model.lower()
    if not model or model == "default":
        return "Select an exact API model ID first."
    if len(model) > 200 or any(ch.isspace() for ch in model):
        return "Use an API model ID, rather than its display name."
    if provider in {"openai", "xai"} and not _model_entry(provider, {"id": model})["chat"]:
        return "This is a specialized model or requires a different endpoint; select a supported chat model."
    if provider == "gemini" and any(word in low for word in ("imagen", "veo", "embedding", "tts", "lyria", "image")):
        return "Use image/video models in Creations and choose a text or Live model for chat."
    return ""

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
    return {"id": model, "name": item.get("displayName") or item.get("display_name") or model, "chat": chat, "live": live,
            "transport": "gemini_live" if live else "anthropic_messages" if provider == "anthropic" else "chat_completions",
            "validated": False, "reason": reason}

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
        result["message"] = f"Catalog reachable · {len(entries)} models listed · {len(result['models'])} chat candidates. Selected-model response not tested."
    except (httpx.HTTPError, ValueError, TypeError):
        result["message"] = f"Couldn't load {CLOUD_LABELS[provider]} models. Check your internet connection and API key, then refresh."
    return result


def test_cloud_model(provider: str, model: str = "", api_key: str | None = None) -> dict:
    """Explicit small inference through the same adapter as chat. Never execute tools."""
    from agent.lean.provider import ChatClient, Endpoint, ProviderError
    import threading
    key = str(api_key if api_key and api_key != "***" else cloud_config(provider).api_key).strip()
    model = str(model or cloud_config(provider).model or "").strip().removeprefix("models/")
    result = {"ok": False, "check": "generation", "model": model, "error_code": "", "message": ""}
    issue = chat_model_issue(provider, model)
    if issue or not key or key == "***":
        return {**result, "error_code": "configuration", "message": issue or "Save an API key first."}
    client = ChatClient(Endpoint(BASE_URLS[provider], key, model, provider, False))
    # Bound both the HTTP timeout and Live adapter deadline without changing saved settings.
    client._http.timeout = httpx.Timeout(25, connect=10)
    client.live_timeout = 25
    cancel = threading.Event()
    timer = threading.Timer(25, cancel.set)
    timer.start()
    try:
        turn = client.stream_turn([{"role": "user", "content": "Reply with just OK."}], max_tokens=512, cancel=cancel)
        if cancel.is_set() or not turn.content.strip():
            return {**result, "error_code": "empty_response", "message": "No text response received within the bounded test. The model is not validated."}
        return {**result, "ok": True, "message": f"{CLOUD_LABELS[provider]} · {model} responded through EchoSpeak's chat adapter. Tool use and media were not tested."}
    except ProviderError as exc:
        return {**result, "error_code": cloud_error_category(exc.status, exc.detail), "message": str(exc).replace(key, "[redacted]")}
    except (httpx.HTTPError, TimeoutError, OSError):
        return {**result, "error_code": "network", "message": "Response test could not reach the provider or timed out. Check your connection."}
    finally:
        timer.cancel()
        client.close()
