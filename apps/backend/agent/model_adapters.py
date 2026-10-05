"""Model families and reasoning-effort controls.

Used for diagnostics and the generation budget. Tool-call parsing lives in the
lean runtime (agent/lean/provider.py); nothing here authorizes or runs tools.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any


class ModelFamily(str, Enum):
    QWEN = "qwen"
    GEMMA = "gemma"
    GLM = "glm"
    GENERIC = "generic"


@dataclass(frozen=True)
class AdapterCapabilities:
    family: ModelFamily
    template: str
    adapter_version: str
    native_tool_calls: bool = True
    streamed_tool_calls: bool = True
    reasoning_channel: bool = True
    sequential_tools: bool = True


REASONING_EFFORT_MAP: dict[str, dict[str, Any]] = {
    "minimal": {"openai_effort": "low", "budget_tokens": 1024, "label": "Minimal"},
    "low": {"openai_effort": "low", "budget_tokens": 2048, "label": "Low"},
    "medium": {"openai_effort": "medium", "budget_tokens": 4096, "label": "Medium"},
    "high": {"openai_effort": "high", "budget_tokens": 8192, "label": "High"},
    "extra_high": {"openai_effort": "high", "budget_tokens": 16384, "label": "Extra High"},
    "max": {"openai_effort": "high", "budget_tokens": 32768, "label": "Max"},
    "ultra": {"openai_effort": "high", "budget_tokens": 65536, "label": "Ultra"},
}


def resolve_reasoning_effort(
    effort: str,
    family: ModelFamily | str = ModelFamily.GENERIC,
    model_id: str = "",
) -> dict[str, Any]:
    """Translate UI effort into one provider-native control, when documented.

    OpenAI-compatible does not mean OpenAI reasoning controls are accepted.
    In particular EchoSpeak currently uses LM Studio's Chat Completions path,
    while LM Studio documents ``reasoning.effort`` on its Responses path.  The
    adapter therefore reports local/OpenAI-compatible models as unsupported
    instead of optimistically sending a parameter the active endpoint may
    ignore or reject.
    """
    key = str(effort or "medium").lower().replace(" ", "_")
    config = REASONING_EFFORT_MAP.get(key, REASONING_EFFORT_MAP["medium"])
    provider = str(getattr(family, "value", family) or "generic").casefold()
    m_low = str(model_id or "").lower()
    openai_reasoning = provider == "openai" and any(
        marker in m_low
        for marker in ("o1", "o3", "o4", "gpt-5", "gpt-6", "reasoner")
    )
    gemini_thinking = provider == "gemini" and any(
        marker in m_low for marker in ("gemini-2.5", "gemini-3")
    )
    if openai_reasoning:
        return {
            "native_support": True,
            "control_kind": "openai_reasoning_effort",
            "effort_level": key,
            "reasoning_effort": config["openai_effort"],
            "budget_tokens": config["budget_tokens"],
            # Current OpenAI reasoning-only families do not all accept an off
            # value.  The runtime never represents a low/minimal setting as off.
            "supports_disable": "gpt-5.1" in m_low or "gpt-5.2" in m_low,
        }
    if gemini_thinking:
        is_flash = "flash" in m_low
        is_gemini_3 = "gemini-3" in m_low
        return {
            "native_support": True,
            "control_kind": "gemini_thinking",
            "effort_level": key,
            "reasoning_effort": None,
            "budget_tokens": min(int(config["budget_tokens"]), 24576),
            "supports_disable": is_flash and not is_gemini_3,
        }
    return {
        "native_support": False,
        "control_kind": "none",
        "effort_level": key,
        "reasoning_effort": None,
        "budget_tokens": config["budget_tokens"],
        "supports_disable": False,
        # Local OpenAI-compatible hosts and Ollama do not expose the same
        # native reasoning controls as OpenAI/Gemini.  The runtime may still
        # apply the selected effort as a bounded generation budget using the
        # provider's documented output parameter.  It never labels this as
        # native chain-of-thought control.
        "output_parameter": (
            "num_predict" if provider == "ollama"
            else "max_tokens" if provider in {"lmstudio", "localai", "vllm", "llama_cpp"}
            else ""
        ),
        "note": "Native reasoning control is unavailable; effort is applied as a bounded output budget when the provider supports one.",
    }




@dataclass(frozen=True)
class ModelFamilyAdapter:
    family: ModelFamily = ModelFamily.GENERIC
    template: str = "chatml"
    version: str = "generic-v1"

    @property
    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(family=self.family, template=self.template, adapter_version=self.version)


_ADAPTERS = {
    # llama.cpp reads the exact Jinja template embedded in the GGUF.
    ModelFamily.QWEN: ModelFamilyAdapter(ModelFamily.QWEN, "qwen-model-metadata", "qwen-v1"),
    ModelFamily.GEMMA: ModelFamilyAdapter(ModelFamily.GEMMA, "gemma", "gemma-v2"),
    ModelFamily.GLM: ModelFamilyAdapter(ModelFamily.GLM, "glm-openai-tools", "glm-native-v1"),
}


def detect_model_family(model_id: str) -> ModelFamily:
    key = str(model_id or "").lower()
    if "qwen" in key:
        return ModelFamily.QWEN
    if "gemma" in key:
        return ModelFamily.GEMMA
    if re.search(r"(?:^|[/_.-])glm(?:[-_.:/]|$)", key) or "z-ai" in key or "zai/" in key:
        return ModelFamily.GLM
    return ModelFamily.GENERIC


def get_family_adapter(model_id: str, provider: str = "") -> ModelFamilyAdapter:
    family = detect_model_family(model_id)
    if family in _ADAPTERS:
        return _ADAPTERS[family]
    if str(provider or "").strip().lower() == "gemini":
        return ModelFamilyAdapter(version="gemini-provider-v1")
    return ModelFamilyAdapter()


def get_provider_adapter(provider: str, model_id: str = "") -> ModelFamilyAdapter:
    return get_family_adapter(model_id, provider)
