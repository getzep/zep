"""Resolve model names to Google ADK model instances and generation settings."""

from __future__ import annotations

from google.adk.models import BaseLlm
from google.adk.models.lite_llm import LiteLlm
from google.genai import types


def resolve_model(
    name: str, thinking: str
) -> tuple[str | BaseLlm, types.GenerateContentConfig | None]:
    """Resolve a model name and its thinking configuration."""
    if "/" in name:
        return LiteLlm(model=name, reasoning_effort=thinking), None
    if thinking == "xhigh":
        raise ValueError("Gemini supports minimal, low, medium, and high thinking levels.")
    return name, types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(thinking_level=thinking.upper())
    )
