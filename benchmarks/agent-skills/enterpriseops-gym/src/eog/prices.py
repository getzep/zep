from __future__ import annotations

from dataclasses import dataclass

PRICE_DATE = "2026-10-15"


@dataclass(frozen=True, slots=True)
class TokenPrice:
    model: str
    input: float
    cache_read: float
    cache_write: float
    output: float
    source_url: str
    date: str = PRICE_DATE
    long_context_threshold: int | None = None
    long_context: tuple[float, float, float, float] | None = None

    def rates(self, prompt_tokens: int = 0) -> tuple[float, float, float, float]:
        if self.long_context and self.long_context_threshold and prompt_tokens > self.long_context_threshold:
            return self.long_context
        return self.input, self.cache_read, self.cache_write, self.output


PRICES: dict[str, TokenPrice | None] = {
    "claude-fable-5-1": TokenPrice(
        "claude-fable-5-1",
        10.0,
        0.25,
        12.5,
        50.0,
        "https://docs.anthropic.com/en/docs/about-claude/pricing",
    ),
    "claude-opus-5-5": TokenPrice(
        "claude-opus-5-5",
        4.0,
        0.2,
        5.0,
        20.0,
        "https://www.anthropic.com/claude/opus",
    ),
    "claude-haiku-5-5": TokenPrice(
        "claude-haiku-5-5",
        0.1,
        0.01,
        0.125,
        0.5,
        "https://www.anthropic.com/claude/haiku",
        long_context_threshold=100_000,
        long_context=(0.5, 0.05, 0.625, 2.5),
    ),
    "gpt-6-luna": TokenPrice(
        "gpt-6-luna",
        0.1,
        0.01,
        0.125,
        0.5,
        "https://developers.openai.com/api/docs/pricing",
        long_context_threshold=272_000,
        long_context=(0.2, 0.02, 0.25, 0.75),
    ),
    "gpt-6.1-sol": TokenPrice(
        "gpt-6.1-sol",
        2.0,
        0.1,
        2.5,
        10.0,
        "https://developers.openai.com/api/docs/models/gpt-6.1-sol",
        long_context_threshold=272_000,
        long_context=(4.0, 0.2, 5.0, 15.0),
    ),
    "gpt-6-astra": TokenPrice(
        "gpt-6-astra",
        10.0,
        1.0,
        12.5,
        50.0,
        "https://developers.openai.com/api/docs/models/gpt-6-astra",
        long_context_threshold=272_000,
        long_context=(20.0, 2.0, 25.0, 75.0),
    ),
    "glm-5.3-flash": TokenPrice(
        "glm-5.3-flash",
        0.15,
        0.03,
        0.0,
        0.5,
        "https://docs.z.ai/guides/overview/pricing",
    ),
    "glm-5.2": TokenPrice(
        "glm-5.2",
        1.4,
        0.26,
        0.0,
        4.4,
        "https://docs.z.ai/guides/overview/pricing",
    ),
    "nemotron-3.5-lightning": TokenPrice(
        "nemotron-3.5-lightning",
        0.07,
        0.04,
        0.0,
        0.20,
        "https://openrouter.ai/api/v1/models/nvidia/nemotron-3.5-lightning/endpoints",
        date="2026-10-16",
    ),
    "deepseek-v4.1-flash-1017": None,
}


def prompt_token_count(
    model: str, *, input_tokens: int, cache_read_tokens: int = 0, cache_write_tokens: int = 0
) -> int:
    if model.startswith("claude-"):
        return input_tokens + cache_read_tokens + cache_write_tokens
    return input_tokens


def cost_usd(
    model: str,
    *,
    input_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    output_tokens: int = 0,
    prompt_tokens: int | None = None,
) -> float | None:
    price = PRICES[model]
    if price is None:
        return None
    total_prompt_tokens = (
        prompt_tokens
        if prompt_tokens is not None
        else prompt_token_count(
            model,
            input_tokens=input_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
        )
    )
    rates = price.rates(total_prompt_tokens)
    billable_input = (
        input_tokens - cache_read_tokens if model.startswith(("gpt-", "glm-", "nemotron-")) else input_tokens
    )
    return (
        max(0, billable_input) * rates[0]
        + cache_read_tokens * rates[1]
        + cache_write_tokens * rates[2]
        + output_tokens * rates[3]
    ) / 1_000_000


def price_manifest() -> list[dict[str, str | float | bool]]:
    return [
        {
            "model": price.model,
            "input": price.input,
            "cache_read": price.cache_read,
            "cache_write": price.cache_write,
            "output": price.output,
            "source_url": price.source_url,
            "date": price.date,
        }
        for price in PRICES.values()
        if price is not None
    ] + [{"model": model, "priced": False} for model, price in PRICES.items() if price is None]
