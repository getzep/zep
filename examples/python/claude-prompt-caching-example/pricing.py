"""Claude Opus 4.8 pricing and cost helpers.

Prices are USD per million tokens, from https://claude.com/pricing#api
(unchanged from Claude Opus 4.7):

- Base input:            $5.00 / MTok
- 5-minute cache write:  $6.25 / MTok  (1.25x base input)
- Cache read:            $0.50 / MTok  (0.1x base input)
- Output:                $25.00 / MTok

If prices change, update the constants below.
"""

from dataclasses import dataclass

MODEL = "claude-opus-4-8"

# USD per million tokens
INPUT_PER_MTOK = 5.00
CACHE_WRITE_PER_MTOK = 6.25
CACHE_READ_PER_MTOK = 0.50
OUTPUT_PER_MTOK = 25.00


@dataclass
class TurnCost:
    """Dollar cost of a single API call, broken down by token type."""

    uncached_input: float
    cache_write: float
    cache_read: float
    output: float

    @property
    def input_total(self) -> float:
        """Everything except output tokens — the part caching affects."""
        return self.uncached_input + self.cache_write + self.cache_read

    @property
    def total(self) -> float:
        return self.input_total + self.output


def cost_for_usage(
    input_tokens: int,
    cache_creation_input_tokens: int,
    cache_read_input_tokens: int,
    output_tokens: int,
) -> TurnCost:
    """Compute dollar cost from the `usage` fields of an API response."""
    return TurnCost(
        uncached_input=input_tokens * INPUT_PER_MTOK / 1_000_000,
        cache_write=cache_creation_input_tokens * CACHE_WRITE_PER_MTOK / 1_000_000,
        cache_read=cache_read_input_tokens * CACHE_READ_PER_MTOK / 1_000_000,
        output=output_tokens * OUTPUT_PER_MTOK / 1_000_000,
    )


def no_cache_cost(
    input_tokens: int,
    cache_creation_input_tokens: int,
    cache_read_input_tokens: int,
    output_tokens: int,
) -> float:
    """What the same call would cost with prompt caching disabled entirely:
    every prompt token billed at the base input rate."""
    prompt_tokens = input_tokens + cache_creation_input_tokens + cache_read_input_tokens
    return prompt_tokens * INPUT_PER_MTOK / 1_000_000 + output_tokens * OUTPUT_PER_MTOK / 1_000_000
