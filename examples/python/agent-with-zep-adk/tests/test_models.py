"""Model resolution tests for LiteLLM and native Gemini."""

from __future__ import annotations

import pytest
from google.adk.models.lite_llm import LiteLlm

from agent_with_zep_adk.models import resolve_model


def test_resolve_litellm_model_with_reasoning_effort():
    model, config = resolve_model("openai/gpt-6-luna", "high")

    assert isinstance(model, LiteLlm)
    assert model.model == "openai/gpt-6-luna"
    assert model._additional_args["reasoning_effort"] == "high"
    assert config is None


def test_resolve_gemini_with_thinking_config():
    model, config = resolve_model("gemini-3.5-flash", "low")

    assert model == "gemini-3.5-flash"
    assert config.thinking_config.thinking_level == "LOW"


def test_resolve_gemini_rejects_xhigh():
    with pytest.raises(ValueError, match="Gemini supports minimal, low, medium, and high"):
        resolve_model("gemini-3.5-flash", "xhigh")
