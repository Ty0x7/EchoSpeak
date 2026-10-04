from __future__ import annotations

from agent.model_adapters import detect_model_family, get_family_adapter


def test_family_detection_and_explicit_templates():
    assert detect_model_family("Qwen3.5-9B-GGUF").value == "qwen"
    assert detect_model_family("gemma-4-E2B-it").value == "gemma"
    assert get_family_adapter("Qwen3.5-9B").template == "qwen-model-metadata"
    assert get_family_adapter("gemma-4-E2B").template == "gemma"

    assert get_family_adapter("gemma-4-E2B").capabilities.native_tool_calls is True
    assert get_family_adapter("some-model", "gemini").version == "gemini-provider-v1"


def test_resolve_reasoning_effort_mapping_and_honesty():
    from agent.model_adapters import resolve_reasoning_effort

    # OpenAI o1/o3 reasoning model
    result_o1 = resolve_reasoning_effort("high", "openai", "o1-mini")
    assert result_o1["reasoning_effort"] == "high"
    assert result_o1["native_support"] is True

    # Generic model without native effort parameter
    result_gemma = resolve_reasoning_effort("max", "gemma", "gemma-2-9b")
    assert result_gemma["native_support"] is False
    assert result_gemma["reasoning_effort"] is None
    assert result_gemma["budget_tokens"] == 32768
