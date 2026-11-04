from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class RouteSettings:
    model: str
    max_output_tokens: int
    max_output_source: str
    temperature: float | None
    temperature_source: str


ANTHROPIC_OUTPUTS = "https://docs.anthropic.com/en/docs/about-claude/models"
ANTHROPIC_TEMPERATURE = "https://docs.anthropic.com/en/docs/build-with-claude/working-with-messages"
OPENAI_OUTPUTS = "https://developers.openai.com/api/docs/models/gpt-6-luna"
OPENAI_SOL_OUTPUTS = "https://developers.openai.com/api/docs/models/gpt-6.1-sol"
OPENAI_ASTRA_OUTPUTS = "https://developers.openai.com/api/docs/models/gpt-6-astra"
OPENAI_TEMPERATURE = "https://developers.openai.com/api/docs/guides/latest-model"
ZAI_OUTPUTS = "https://docs.z.ai/api-reference/llm/chat-completion"
ZAI_FLASH_TEMPERATURE = "https://docs.z.ai/guides/vlm/glm-5.3-flash"
ZAI_GLM52_TEMPERATURE = "https://docs.z.ai/guides/llm/glm-5.2"
OPENROUTER_ENDPOINTS = "https://openrouter.ai/api/v1/models/nvidia/nemotron-3.5-lightning/endpoints"
DEEPSEEK_MODEL_CARD = "https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash-1017"

ROUTE_SETTINGS = {
    "claude-opus-5-5": RouteSettings("claude-opus-5-5", 128_000, ANTHROPIC_OUTPUTS, None, ANTHROPIC_TEMPERATURE),
    "claude-fable-5-1": RouteSettings("claude-fable-5-1", 128_000, ANTHROPIC_OUTPUTS, None, ANTHROPIC_TEMPERATURE),
    "claude-haiku-5-5": RouteSettings("claude-haiku-5-5", 64_000, ANTHROPIC_OUTPUTS, None, ANTHROPIC_TEMPERATURE),
    "gpt-6-luna": RouteSettings("gpt-6-luna", 128_000, OPENAI_OUTPUTS, None, OPENAI_TEMPERATURE),
    "gpt-6.1-sol": RouteSettings("gpt-6.1-sol", 128_000, OPENAI_SOL_OUTPUTS, None, OPENAI_TEMPERATURE),
    "gpt-6-astra": RouteSettings("gpt-6-astra", 128_000, OPENAI_ASTRA_OUTPUTS, None, OPENAI_TEMPERATURE),
    "glm-5.3-flash": RouteSettings("glm-5.3-flash", 131_072, ZAI_OUTPUTS, 1.0, ZAI_FLASH_TEMPERATURE),
    "glm-5.2": RouteSettings("glm-5.2", 131_072, ZAI_OUTPUTS, 1.0, ZAI_GLM52_TEMPERATURE),
    "nemotron-3.5-lightning": RouteSettings(
        "nemotron-3.5-lightning",
        32_768,
        OPENROUTER_ENDPOINTS,
        None,
        "https://openrouter.ai/nvidia/nemotron-3.5-lightning",
    ),
    "deepseek-v4.1-flash-1017": RouteSettings(
        "deepseek-v4.1-flash-1017",
        65_536,
        DEEPSEEK_MODEL_CARD,
        None,
        DEEPSEEK_MODEL_CARD,
    ),
}


def route_settings_manifest() -> dict[str, dict[str, str | int | float | None]]:
    return {name: asdict(settings) for name, settings in ROUTE_SETTINGS.items()}
