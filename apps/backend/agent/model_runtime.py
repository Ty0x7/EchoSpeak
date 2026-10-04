"""Machine-readable model capabilities and provider communication policies.

Core lifecycle code consumes this neutral contract. Provider-specific formatting,
stop tokens, and cleanup live in adapters here rather than scattered branches.

Capability fields are for observability and operator configuration only.
They must not gate runtime access: all configured models receive the same full
functional defaults. Real context-window sizes must come from configuration or
explicit overrides — never from local-versus-hosted capability guesses.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from hashlib import sha256
from dataclasses import dataclass, asdict, field
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import httpx
from loguru import logger

from config import DATA_DIR, ModelProvider, config

try:
    from langchain_openai import ChatOpenAI

    class ReasoningChatOpenAI(ChatOpenAI):
        """Preserve documented reasoning metadata without exposing it as content."""

        def _convert_delta_to_message_chunk(self, value: dict, default_class: Any) -> Any:
            chunk = super()._convert_delta_to_message_chunk(value, default_class)
            reasoning = (value.get("delta") or {}).get("reasoning_content")
            if reasoning is not None:
                chunk.additional_kwargs["reasoning_content"] = reasoning
            return chunk

        def _convert_dict_to_message(self, value: dict) -> Any:
            message = super()._convert_dict_to_message(value)
            reasoning = (value.get("message") or {}).get("reasoning_content")
            if reasoning is not None:
                message.additional_kwargs["reasoning_content"] = reasoning
            return message
except ImportError:
    ChatOpenAI = None
    ReasoningChatOpenAI = None

try:
    from langchain_ollama import ChatOllama
except ImportError:
    ChatOllama = None

try:
    from langchain_google_genai import ChatGoogleGenerativeAI  # type: ignore
except ImportError:
    ChatGoogleGenerativeAI = None

try:
    from langchain_community.llms import LlamaCpp, VLLM
except ImportError:
    LlamaCpp = None
    VLLM = None


# Full functional defaults shared by every model (local or hosted).
_FULL_PLAN_DEPTH = 12
_FULL_AUTONOMOUS_STEPS = 64
_FULL_PARALLELISM = 3
_FULL_CONFIDENCE_THRESHOLD = 0.72
# Universal fallback only when no real configured/discovered limit exists.
# Not a local-vs-hosted tier.
_UNIVERSAL_CONTEXT_FALLBACK = 32768


def _openai_compatible_request_timeout(*, local_stream: bool) -> Any:
    """Build one explicit transport timeout; hidden SDK retries stay disabled."""

    total = max(
        5.0,
        float(getattr(config, "model_request_timeout_seconds", 180.0) or 180.0),
    )
    if not local_stream:
        return total
    idle = min(
        total,
        max(
            5.0,
            float(
                getattr(config, "model_stream_idle_timeout_seconds", 45.0)
                or 45.0
            ),
        ),
    )
    return httpx.Timeout(
        total,
        connect=min(10.0, total),
        read=idle,
        write=min(30.0, total),
        pool=min(10.0, total),
    )


@dataclass(frozen=True)
class ModelCapabilityProfile:
    model_id: str
    provider: str
    local: bool
    context_limit: int
    recommended_budget: int
    native_tools: bool
    tool_call_format: str
    structured_output_reliability: str
    reasoning: str
    coding: str
    instruction_following: str
    long_context_reliability: str
    vision: bool = False
    multimodal: bool = False
    streaming: bool = True
    # Observability / optional config — runtime must not treat these as gates.
    recommended_plan_depth: int = _FULL_PLAN_DEPTH
    maximum_autonomous_steps: int = _FULL_AUTONOMOUS_STEPS
    recommended_parallelism: int = _FULL_PARALLELISM
    confidence_threshold: float = _FULL_CONFIDENCE_THRESHOLD
    one_tool_at_a_time: bool = False
    structured_output_repair: bool = True
    source: str = "configured-default"
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StructuredOutputCapability:
    """Explicit capability decision for the Turn Understanding request only."""

    mode: str
    method: str
    reason: str
    probe_key: str = ""
    server_version: str = ""
    probed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SelectedModelReadiness:
    """Result of checking the exact model selected by the current Session."""

    state: str
    provider: str
    model_id: str
    action: str = "none"
    instance_id: str = ""
    load_time_seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class SelectedModelReadinessError(RuntimeError):
    """The selected provider/model could not be made ready without fallback."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = str(code or "selected_model_unavailable")


_HOSTED_PROVIDERS = {"openai", "gemini"}
_STRUCTURED_PROBE_LOCK = threading.RLock()
_STRUCTURED_PROBE_CACHE: dict[str, tuple[float, StructuredOutputCapability]] = {}


_PROVIDER_CATALOG: tuple[dict[str, Any], ...] = (
    {"id": "openai", "name": "OpenAI", "local": False, "description": "OpenAI models"},
    {"id": "gemini", "name": "Google Gemini", "local": False, "description": "Google Gemini models"},
    {"id": "ollama", "name": "Ollama", "local": True, "description": "Local models served by Ollama"},
    {"id": "lmstudio", "name": "LM Studio", "local": True, "description": "Models served by LM Studio"},
    {"id": "localai", "name": "LocalAI", "local": True, "description": "Models served by LocalAI"},
    {"id": "llama_cpp", "name": "llama.cpp", "local": True, "description": "A configured local GGUF model"},
    {"id": "vllm", "name": "vLLM", "local": True, "description": "Models served by vLLM"},
)

_PROVIDER_REQUIREMENTS: dict[ModelProvider, dict[str, Any]] = {
    ModelProvider.OPENAI: {
        "env_vars": ["OPENAI_API_KEY"],
        "pip_packages": ["langchain-openai"],
        "description": "Requires an OpenAI API key",
    },
    ModelProvider.GEMINI: {
        "env_vars": ["GEMINI_API_KEY"],
        "pip_packages": ["langchain-google-genai"],
        "description": "Requires a Google AI Studio API key",
    },
    ModelProvider.OLLAMA: {
        "env_vars": ["LOCAL_MODEL_URL", "LOCAL_MODEL_NAME"],
        "pip_packages": ["langchain-ollama"],
        "description": "Requires Ollama with the selected model available",
    },
    ModelProvider.LM_STUDIO: {
        "env_vars": ["LOCAL_MODEL_URL", "LOCAL_MODEL_NAME"],
        "pip_packages": ["langchain-openai"],
        "description": "Requires the LM Studio server and exact selected model",
    },
    ModelProvider.LOCALAI: {
        "env_vars": ["LOCAL_MODEL_URL", "LOCAL_MODEL_NAME"],
        "pip_packages": ["langchain-openai"],
        "description": "Requires a LocalAI server",
    },
    ModelProvider.LLAMA_CPP: {
        "env_vars": ["LOCAL_MODEL_NAME"],
        "pip_packages": ["langchain-community", "llama-cpp-python"],
        "description": "Requires llama.cpp bindings and a configured GGUF path",
    },
    ModelProvider.VLLM: {
        "env_vars": ["LOCAL_MODEL_URL", "LOCAL_MODEL_NAME"],
        "pip_packages": ["langchain-community", "vllm"],
        "description": "Requires a vLLM server",
    },
}

_LOCAL_PROVIDER_DEFAULT_URLS: dict[ModelProvider, str] = {
    ModelProvider.OLLAMA: "http://localhost:11434",
    ModelProvider.LM_STUDIO: "http://localhost:1234",
    ModelProvider.LOCALAI: "http://localhost:8080",
    ModelProvider.VLLM: "http://localhost:8000",
}


def resolve_local_provider_base_url(
    provider: ModelProvider | str,
    configured_base_url: str = "",
) -> str:
    """Resolve a local provider endpoint without carrying another provider's default.

    ``LOCAL_MODEL_URL`` remains an explicit custom override. The only values
    rewritten are EchoSpeak's own well-known defaults, which otherwise leak
    across Session-scoped provider changes because local configuration is
    shared while the selected provider is not.
    """

    resolved = provider if isinstance(provider, ModelProvider) else ModelProvider(str(provider))
    configured = str(configured_base_url or "").strip().rstrip("/")
    selected_default = _LOCAL_PROVIDER_DEFAULT_URLS.get(resolved, "")
    if not configured or is_known_local_default_url(configured):
        return selected_default or configured
    return configured


def _endpoint_key(url: str) -> str:
    """Compare endpoints by port and path, ignoring localhost spelling and a trailing /v1."""

    from urllib.parse import urlsplit

    text = str(url or "").strip().rstrip("/")
    if not text:
        return ""
    if "://" not in text:
        text = f"http://{text}"
    parts = urlsplit(text)
    host = (parts.hostname or "").lower()
    if host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}:
        host = "localhost"
    path = parts.path.rstrip("/")
    if path.endswith("/v1"):
        path = path[: -len("/v1")]
    return f"{host}:{parts.port or ''}{path}"


def is_known_local_default_url(url: str) -> bool:
    """True when ``url`` is just some local provider's stock address (not a custom one)."""

    key = _endpoint_key(url)
    return bool(key) and key in {_endpoint_key(u) for u in _LOCAL_PROVIDER_DEFAULT_URLS.values()}


def local_provider_default_url(provider: ModelProvider | str) -> str:
    try:
        resolved = provider if isinstance(provider, ModelProvider) else ModelProvider(str(provider))
    except ValueError:
        return ""
    return _LOCAL_PROVIDER_DEFAULT_URLS.get(resolved, "")


def _is_chat_model(model_id: str) -> bool:
    low = model_id.lower()
    return not any(tag in low for tag in ("embed", "embedding", "rerank", "whisper", "tts"))


def list_local_models(provider: ModelProvider | str, base_url: str = "", timeout: float = 2.5) -> list[str]:
    """Chat models a local server currently offers ([] when it is not reachable)."""

    import json as _json
    from urllib.request import Request as _Request, urlopen as _urlopen

    try:
        resolved = provider if isinstance(provider, ModelProvider) else ModelProvider(str(provider))
    except ValueError:
        return []
    base = resolve_local_provider_base_url(resolved, base_url).rstrip("/")
    if not base:
        return []
    if resolved == ModelProvider.OLLAMA:
        url = f"{base[:-3] if base.endswith('/v1') else base}/api/tags"
    else:
        url = f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"
    try:
        with _urlopen(_Request(url, headers={"Accept": "application/json"}), timeout=timeout) as resp:
            raw = resp.read(512_001)
        if len(raw) > 512_000:
            return []
        payload = _json.loads(raw.decode("utf-8") or "{}")
    except Exception:
        return []
    rows = payload.get("models") if resolved == ModelProvider.OLLAMA else payload.get("data")
    names: list[str] = []
    for item in rows if isinstance(rows, list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("id") or item.get("name") or item.get("model") or "").strip()
        if name and _is_chat_model(name) and name not in names:
            names.append(name)
    return names


def detect_local_providers(timeout: float = 1.2) -> list[dict[str, Any]]:
    """Probe every local app on its stock port at once. LM Studio first, then Ollama."""

    from concurrent.futures import ThreadPoolExecutor

    order = [ModelProvider.LM_STUDIO, ModelProvider.OLLAMA, ModelProvider.LOCALAI, ModelProvider.VLLM]
    with ThreadPoolExecutor(max_workers=len(order)) as pool:
        results = list(pool.map(lambda p: (p, list_local_models(p, "", timeout)), order))
    found: list[dict[str, Any]] = []
    for provider, models in results:
        found.append({
            "provider": provider.value,
            "base_url": _LOCAL_PROVIDER_DEFAULT_URLS[provider],
            "running": bool(models),
            "models": models,
        })
    return found


_FIRST_MODEL_CACHE: dict[str, tuple[float, str]] = {}


def first_local_model(provider: ModelProvider | str, base_url: str = "") -> str:
    """The first loaded chat model, cached for a few seconds. Used when no model is chosen yet."""

    key = f"{provider}|{base_url}"
    hit = _FIRST_MODEL_CACHE.get(key)
    now = time.monotonic()
    if hit and now - hit[0] < 10:
        return hit[1]
    models = list_local_models(provider, base_url)
    model = models[0] if models else ""
    _FIRST_MODEL_CACHE[key] = (now, model)
    return model


def list_available_providers() -> list[dict[str, Any]]:
    """Return the configured provider catalog without creating an agent."""

    return [dict(item) for item in _PROVIDER_CATALOG]


def get_provider_requirements(provider: ModelProvider | str) -> dict[str, Any]:
    """Return operator requirements from the provider catalog owner."""

    try:
        resolved = provider if isinstance(provider, ModelProvider) else ModelProvider(str(provider))
    except ValueError:
        return {}
    return dict(_PROVIDER_REQUIREMENTS.get(resolved, {}))


class ModelRuntimeClient:
    """Session-bound provider client used by Echo's canonical model control plane.

    This owns provider construction and response-channel normalization only. It
    does not own a Session, TaskRun, tools, retries, or completion.
    """

    def __init__(self, provider: ModelProvider | str, model_id: str = "") -> None:
        self.provider = provider if isinstance(provider, ModelProvider) else ModelProvider(str(provider))
        self.model_id = str(model_id or self._configured_model_id()).strip()
        if not self.model_id:
            raise ValueError(f"No model is configured for provider {self.provider.value}")
        self.llm = self._create_llm()

    def _configured_model_id(self) -> str:
        if self.provider == ModelProvider.OPENAI:
            return str(config.openai.model or "")
        if self.provider == ModelProvider.GEMINI:
            return str(config.gemini.model or "")
        return str(config.local.model_name or "")

    def _create_llm(self) -> Any:
        if self.provider == ModelProvider.OPENAI:
            if ReasoningChatOpenAI is None:
                raise ImportError("langchain-openai is required for provider=openai")
            return ReasoningChatOpenAI(
                model=self.model_id,
                temperature=config.openai.temperature,
                api_key=config.openai.api_key or "not-needed",
                max_tokens=config.openai.max_tokens,
                request_timeout=_openai_compatible_request_timeout(
                    local_stream=False
                ),
                max_retries=0,
                streaming=True,
            )

        if self.provider == ModelProvider.GEMINI:
            if ChatGoogleGenerativeAI is None:
                raise ImportError("langchain-google-genai is required for provider=gemini")
            model_key = self.model_id.casefold()
            thinking = "pro" in model_key or "2.5" in model_key
            return ChatGoogleGenerativeAI(
                model=self.model_id,
                temperature=config.gemini.temperature,
                max_tokens=config.gemini.max_tokens,
                google_api_key=config.gemini.api_key or "not-needed",
                include_thoughts=thinking,
                thinking_budget=8192 if thinking else 0,
                streaming=True,
            )

        local = config.local
        local_base_url = resolve_local_provider_base_url(self.provider, local.base_url)
        if self.provider == ModelProvider.OLLAMA:
            if ChatOllama is None:
                raise ImportError("langchain-ollama is required for provider=ollama")
            return ChatOllama(
                model=self.model_id,
                base_url=local_base_url,
                temperature=local.temperature,
                num_predict=local.max_tokens,
            )

        if self.provider in {ModelProvider.LM_STUDIO, ModelProvider.LOCALAI}:
            if ReasoningChatOpenAI is None:
                raise ImportError("langchain-openai is required for the selected OpenAI-compatible provider")
            base = local_base_url
            if base and not base.endswith("/v1"):
                base = f"{base}/v1"
            max_tokens = max(int(local.max_tokens or 0), 8192) if any(
                family in self.model_id.casefold() for family in ("gemma", "qwen", "glm")
            ) else int(local.max_tokens or 4096)
            return ReasoningChatOpenAI(
                model=self.model_id,
                temperature=local.temperature,
                base_url=base or None,
                api_key="not-needed",
                max_tokens=max_tokens,
                request_timeout=_openai_compatible_request_timeout(
                    local_stream=True
                ),
                max_retries=0,
                streaming=True,
            )

        if self.provider == ModelProvider.LLAMA_CPP:
            if LlamaCpp is None:
                raise ImportError("langchain-community and llama-cpp-python are required for provider=llama_cpp")
            return LlamaCpp(
                model_path=self.model_id,
                temperature=local.temperature,
                max_tokens=local.max_tokens,
                n_ctx=local.context_length,
                n_gpu_layers=local.gpu_layers,
                use_mmap=local.use_mmap,
                use_mlock=local.use_mlock,
                n_threads=local.threads,
                verbose=True,
            )

        if self.provider == ModelProvider.VLLM:
            if VLLM is None:
                raise ImportError("langchain-community and vllm are required for provider=vllm")
            return VLLM(
                model=self.model_id,
                tensor_parallel_size=1,
                trust_remote_code=True,
                base_url=local_base_url,
            )
        raise ValueError(f"Unsupported model provider: {self.provider.value}")

    @staticmethod
    def coerce_content_to_text(content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    if str(item.get("type") or "").casefold() in {"thinking", "thought", "reasoning"}:
                        continue
                    if "text" in item:
                        parts.append(str(item.get("text") or ""))
                elif item is not None and str(getattr(item, "type", "")).casefold() not in {
                    "thinking", "thought", "reasoning"
                }:
                    parts.append(str(item))
            return " ".join(part for part in parts if part.strip()).strip()
        if isinstance(content, dict):
            if str(content.get("type") or "").casefold() in {"thinking", "thought", "reasoning"}:
                return ""
            if "text" in content:
                return str(content.get("text") or "")
        return str(content)

    @staticmethod
    def extract_reasoning_text(value: Any) -> str:
        parts: list[str] = []
        seen: set[int] = set()

        def walk(item: Any) -> None:
            if item is None:
                return
            if isinstance(item, str):
                parts.extend(
                    match.group(1).strip()
                    for match in re.finditer(r"<think>(.*?)</think>", item, re.IGNORECASE | re.DOTALL)
                    if match.group(1).strip()
                )
                return
            if isinstance(item, (dict, list, tuple, set)) or hasattr(item, "__dict__"):
                identity = id(item)
                if identity in seen:
                    return
                seen.add(identity)
            if isinstance(item, dict):
                reasoning = item.get("reasoning_content")
                if reasoning:
                    parts.append(str(reasoning).strip())
                for key in ("thinking", "thought", "reasoning", "content", "message", "messages", "additional_kwargs"):
                    if key in item:
                        walk(item.get(key))
                return
            if isinstance(item, (list, tuple, set)):
                for child in item:
                    walk(child)
                return
            for attribute in ("content", "message", "messages", "additional_kwargs", "response_metadata"):
                if hasattr(item, attribute):
                    walk(getattr(item, attribute))

        walk(value)
        return "\n\n".join(dict.fromkeys(part for part in parts if part)).strip()

    def resolve_turn_reasoning_control(
        self,
        *,
        thinking_enabled: bool = True,
        reasoning_effort: str = "medium",
    ) -> dict[str, Any]:
        """Resolve one turn-scoped, provider-honest reasoning control."""

        from agent.model_adapters import resolve_reasoning_effort

        resolved = resolve_reasoning_effort(
            reasoning_effort,
            self.provider.value,
            self.model_id,
        )
        bind_parameters: dict[str, Any] = {}
        applied = False
        control_kind = str(resolved.get("control_kind") or "none")
        if control_kind == "openai_reasoning_effort":
            if thinking_enabled:
                bind_parameters["reasoning_effort"] = resolved["reasoning_effort"]
                applied = True
            elif bool(resolved.get("supports_disable")):
                bind_parameters["reasoning_effort"] = "none"
                applied = True
        elif control_kind == "gemini_thinking":
            model_low = self.model_id.casefold()
            if "gemini-3" in model_low:
                if thinking_enabled:
                    level = str(resolved.get("effort_level") or "medium")
                    bind_parameters["thinking_level"] = (
                        "low" if level in {"minimal", "low"}
                        else "high" if level in {"high", "extra_high", "max", "ultra"}
                        else "medium"
                    )
                    applied = True
            elif thinking_enabled:
                bind_parameters["thinking_budget"] = int(resolved["budget_tokens"])
                applied = True
            elif bool(resolved.get("supports_disable")):
                bind_parameters["thinking_budget"] = 0
                bind_parameters["include_thoughts"] = False
                applied = True
        elif thinking_enabled:
            # Local hosts generally do not implement a portable native
            # reasoning-effort field.  Still honor the user's effort choice by
            # changing the bounded generation budget through the parameter the
            # selected provider actually supports.  This is an output budget,
            # not private-thought exposure or a hidden model fallback.
            output_parameter = str(resolved.get("output_parameter") or "")
            if output_parameter:
                try:
                    context_limit = int(
                        getattr(getattr(config, "local", None), "context_length", 0)
                        or 32768
                    )
                except (TypeError, ValueError):
                    context_limit = 32768
                budget = max(1024, int(resolved.get("budget_tokens") or 4096))
                # Keep enough room for the prompt and tools on local models.
                budget = min(budget, max(4096, context_limit // 2), 65536)
                bind_parameters[output_parameter] = budget
                resolved["effective_output_budget"] = budget
                resolved["control_kind"] = "bounded_output_budget"
                applied = True
        note = str(resolved.get("note") or "")
        if not thinking_enabled and not applied:
            note = (
                "The selected model/provider cannot disable reasoning on this endpoint; "
                "no unsupported off parameter was sent."
            )
        return {
            **resolved,
            "thinking_enabled": bool(thinking_enabled),
            "applied": applied,
            "bind_parameters": bind_parameters,
            "note": note,
        }

    def invoke_with_reasoning(
        self,
        prompt: str,
        *,
        thinking_enabled: bool = True,
        reasoning_effort: str = "medium",
        callbacks: Optional[list[Any]] = None,
    ) -> tuple[str, str]:
        control = self.resolve_turn_reasoning_control(
            thinking_enabled=thinking_enabled,
            reasoning_effort=reasoning_effort,
        )
        runnable = self.llm
        bind_parameters = dict(control.get("bind_parameters") or {})
        if bind_parameters:
            runnable = runnable.bind(**bind_parameters)
        if callbacks:
            response = runnable.invoke(prompt, config={"callbacks": list(callbacks)})
        else:
            response = runnable.invoke(prompt)
        return (
            self.coerce_content_to_text(getattr(response, "content", response)),
            self.extract_reasoning_text(response),
        )

    def invoke_conversation_with_reasoning(
        self,
        prompt: str,
        *,
        thinking_enabled: bool = True,
        reasoning_effort: str = "medium",
        callbacks: Optional[list[Any]] = None,
    ) -> tuple[str, str]:
        """Invoke the same exact Session model without an agent/tool envelope."""

        return self.invoke_with_reasoning(
            prompt,
            thinking_enabled=thinking_enabled,
            reasoning_effort=reasoning_effort,
            callbacks=callbacks,
        )

    def invoke(self, prompt: str) -> str:
        return self.invoke_with_reasoning(prompt)[0]

    def invoke_fast(self, prompt: str, max_tokens: int = 256) -> str:
        try:
            response = self.llm.bind(max_tokens=max_tokens).invoke(prompt)
            return self.coerce_content_to_text(getattr(response, "content", response))
        except Exception as exc:
            logger.debug("Fast model binding unavailable; using normal invocation: {}", exc)
            return self.invoke(prompt)


def _llm_base_url(llm: Any, profile: Optional[ModelCapabilityProfile]) -> str:
    metadata = dict(getattr(profile, "metadata", {}) or {})
    candidates = [
        metadata.get("base_url"),
        metadata.get("endpoint"),
        getattr(llm, "openai_api_base", None),
        getattr(llm, "base_url", None),
    ]
    client = getattr(llm, "client", None)
    candidates.extend([
        getattr(client, "base_url", None),
        getattr(getattr(client, "_client", None), "base_url", None),
    ])
    for value in candidates:
        text = str(value or "").strip().rstrip("/")
        if text:
            return text
    return ""


def _lmstudio_rest_root(endpoint: str) -> str:
    base = str(endpoint or "").strip().rstrip("/")
    if base.endswith("/api/v1"):
        base = base[:-7].rstrip("/")
    elif base.endswith("/v1"):
        base = base[:-3].rstrip("/")
    return base


def _read_json_response(request: Request, *, timeout: float, max_bytes: int = 1_000_000) -> dict[str, Any]:
    with urlopen(request, timeout=max(1.0, float(timeout))) as response:
        body = response.read(max_bytes + 1)
    if len(body) > max_bytes:
        raise ValueError("provider response exceeded the readiness size limit")
    decoded = json.loads(body.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("provider readiness response was not an object")
    return decoded


def ensure_selected_model_ready(
    provider: str,
    model_id: str,
    *,
    llm: Any,
    profile: Optional[ModelCapabilityProfile] = None,
    timeout: float = 120.0,
) -> SelectedModelReadiness:
    """Make the exact selected model ready where the provider has an explicit lifecycle.

    This is not model routing: it never substitutes a model, downloads one, or
    changes the Session binding. Providers without a documented explicit load
    lifecycle remain owned by their normal invocation client.
    """

    provider_id = str(provider or "unknown").strip().casefold()
    selected_model = str(model_id or "").strip()
    if provider_id != "lmstudio":
        return SelectedModelReadiness(
            state="provider_managed",
            provider=provider_id,
            model_id=selected_model,
        )
    if not selected_model or selected_model == "default":
        raise SelectedModelReadinessError(
            "LM Studio requires one concrete Session-bound model ID",
            code="selected_model_id_missing",
        )

    endpoint = _llm_base_url(llm, profile)
    root = _lmstudio_rest_root(endpoint)
    if not root:
        raise SelectedModelReadinessError(
            "LM Studio endpoint is not configured",
            code="selected_model_endpoint_missing",
        )
    headers = {
        "Accept": "application/json",
        "Authorization": "Bearer lm-studio",
        "User-Agent": "EchoSpeak/8.0 selected-model-readiness",
    }
    list_request = Request(root + "/api/v1/models", headers=headers, method="GET")
    try:
        catalog = _read_json_response(
            list_request,
            timeout=max(1.0, min(float(timeout), 15.0)),
        )
    except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError, OSError) as exc:
        raise SelectedModelReadinessError(
            f"LM Studio model catalog is unavailable ({type(exc).__name__})",
            code="selected_model_catalog_unavailable",
        ) from exc

    models = catalog.get("models")
    if not isinstance(models, list):
        raise SelectedModelReadinessError(
            "LM Studio model catalog is malformed",
            code="selected_model_catalog_malformed",
        )
    selected_key = selected_model.casefold()
    selected_row: Optional[dict[str, Any]] = None
    for item in models:
        if not isinstance(item, dict):
            continue
        identities = {
            str(item.get("key") or "").strip().casefold(),
            str(item.get("selected_variant") or "").strip().casefold(),
        }
        for instance in list(item.get("loaded_instances") or []):
            if isinstance(instance, dict):
                identities.add(str(instance.get("id") or "").strip().casefold())
        if selected_key in identities:
            selected_row = item
            break
    if selected_row is None:
        raise SelectedModelReadinessError(
            "The Session-bound model is not installed in LM Studio",
            code="selected_model_not_installed",
        )

    loaded_instances = [
        item for item in list(selected_row.get("loaded_instances") or [])
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    ]
    if loaded_instances:
        return SelectedModelReadiness(
            state="loaded",
            provider=provider_id,
            model_id=selected_model,
            instance_id=str(loaded_instances[0].get("id") or ""),
        )

    metadata = dict(getattr(profile, "metadata", {}) or {})
    payload: dict[str, Any] = {"model": selected_model}
    context_length = metadata.get("context_limit")
    if isinstance(context_length, int) and context_length > 0:
        payload["context_length"] = context_length
    load_request = Request(
        root + "/api/v1/models/load",
        data=json.dumps(payload).encode("utf-8"),
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        loaded = _read_json_response(load_request, timeout=max(5.0, float(timeout)))
    except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError, OSError) as exc:
        raise SelectedModelReadinessError(
            f"LM Studio could not load the Session-bound model ({type(exc).__name__})",
            code="selected_model_load_failed",
        ) from exc
    if str(loaded.get("status") or "").casefold() != "loaded":
        raise SelectedModelReadinessError(
            "LM Studio did not confirm that the Session-bound model was loaded",
            code="selected_model_load_unconfirmed",
        )
    return SelectedModelReadiness(
        state="loaded",
        provider=provider_id,
        model_id=selected_model,
        action="loaded",
        instance_id=str(loaded.get("instance_id") or ""),
        load_time_seconds=float(loaded.get("load_time_seconds") or 0.0),
    )


def resolve_model_profile(provider: str, model_id: str, configured: Optional[dict[str, Any]] = None) -> ModelCapabilityProfile:
    """Resolve model metadata with equal full functional defaults for all models.

    context_limit must be supplied via configured metadata (real config / discovery
    / explicit override). When absent, a single universal fallback is used — never
    a larger hosted default or smaller local default.
    """
    provider = str(provider or "unknown").strip().lower()
    model_id = str(model_id or "default").strip() or "default"
    meta = dict(configured or {})
    conformance: Optional[dict[str, Any]] = None  # measured conformance reports were retired in 10.0
    local = bool(meta.get("local", provider not in _HOSTED_PROVIDERS))
    # Physical window only: explicit override or universal fallback. Callers
    # (process_query, /provider) should inject config.local.context_length /
    # llm_trim_max_tokens when known.
    if meta.get("context_limit"):
        context_limit = max(2048, int(meta.get("context_limit") or 0))
    else:
        context_limit = _UNIVERSAL_CONTEXT_FALLBACK
    # Budget tracks the real window — never a tighter tier recommendation.
    recommended_budget = max(1024, min(context_limit, int(meta.get("recommended_budget") or context_limit)))
    return ModelCapabilityProfile(
        model_id=model_id,
        provider=provider,
        local=local,
        context_limit=context_limit,
        recommended_budget=recommended_budget,
        native_tools=bool(
            meta.get(
                "native_tools",
                (conformance.get("capabilities") or {}).get(
                    "native_tool_calls", False
                ) if conformance else True,
            )
        ),
        tool_call_format=str(meta.get("tool_call_format") or ("native" if not local else "json")),
        # Qualitative labels for dashboards/logs only — not runtime gates.
        structured_output_reliability=str(
            meta.get("structured_output_reliability")
            or (
                "measured_pass" if conformance and conformance.get("passed")
                else "measured_partial" if conformance
                else "unmeasured"
            )
        ),
        reasoning=str(meta.get("reasoning") or "unmeasured"),
        coding=str(meta.get("coding") or "unmeasured"),
        instruction_following=str(
            meta.get("instruction_following")
            or (
                "measured_tool_contract"
                if conformance and conformance.get("passed")
                else "unmeasured"
            )
        ),
        long_context_reliability=str(
            meta.get("long_context_reliability") or "unmeasured"
        ),
        vision=bool(meta.get("vision", False)),
        multimodal=bool(meta.get("multimodal", False)),
        streaming=bool(meta.get("streaming", True)),
        recommended_plan_depth=max(1, int(meta.get("recommended_plan_depth") or _FULL_PLAN_DEPTH)),
        maximum_autonomous_steps=max(1, int(meta.get("maximum_autonomous_steps") or _FULL_AUTONOMOUS_STEPS)),
        recommended_parallelism=max(1, int(meta.get("recommended_parallelism") or _FULL_PARALLELISM)),
        confidence_threshold=float(meta.get("confidence_threshold") or _FULL_CONFIDENCE_THRESHOLD),
        one_tool_at_a_time=bool(meta.get("one_tool_at_a_time", False)),
        structured_output_repair=bool(meta.get("structured_output_repair", True)),
        source=(
            "configured+measured" if configured and conformance
            else "measured-conformance" if conformance
            else "configured" if configured
            else "unmeasured-default"
        ),
        metadata=meta,
    )


def clear_structured_output_probe_cache() -> None:
    """Invalidate cached model/host probe decisions (model/provider switch)."""
    with _STRUCTURED_PROBE_LOCK:
        _STRUCTURED_PROBE_CACHE.clear()


