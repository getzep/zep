from __future__ import annotations

import statistics
from typing import Any

from .providers.base import ModelTurn
from .providers.deepseek_route import DEEPSEEK_MATRIX_EFFORTS

EFFORT_LEVELS = {
    "claude-opus-5-5": ("low", "medium", "high", "max"),
    "claude-fable-5-1": ("low", "medium", "high", "max"),
    "claude-haiku-5-5": ("low", "medium", "max"),
    "gpt-6-luna": ("low", "medium", "high", "xhigh", "max"),
    "gpt-6.1-sol": ("low", "medium", "high", "xhigh", "max"),
    "gpt-6-astra": ("low", "medium", "high", "xhigh", "max"),
    "glm-5.3-flash": ("low", "high", "max"),
    "glm-5.2": ("low", "high", "xhigh", "max"),
    "deepseek-v4.1-flash-1017": DEEPSEEK_MATRIX_EFFORTS,
}
FIXED_PROMPT = (
    "A school has 4 boxes of pencils. Each box contains 3 packs, and each pack contains 8 pencils. "
    "The school gives 17 pencils to each of 5 classes, then receives 9 more pencils. "
    "How many pencils remain? Show the calculation and the final number."
)


def evaluate_effort_preflight(
    model: str, rows: list[dict[str, Any]], *, efforts: tuple[str, ...] | None = None
) -> dict[str, Any]:
    levels = efforts or EFFORT_LEVELS[model]
    token_field = (
        "output_tokens"
        if model.startswith("claude-") and all(row.get("reasoning_tokens") is None for row in rows)
        else "reasoning_tokens"
    )
    medians = {
        level: statistics.median(int(row[token_field]) for row in rows if row["effort"] == level) for level in levels
    }
    passed = medians[levels[-1]] > medians[levels[0]]
    return {"model": model, "medians": medians, "passed": passed}


def run_effort_preflight(
    route: Any,
    model: str,
    *,
    task_id: str = "effort-preflight",
    efforts: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    rows = []
    levels = efforts or EFFORT_LEVELS[model]
    for effort in levels:
        for repeat in range(1, 4):
            turn: ModelTurn = route.complete(
                "You are a concise assistant.",
                [{"role": "user", "content": FIXED_PROMPT}],
                [],
                effort=effort,
                task_id=task_id,
            )
            rows.append(
                {
                    "effort": effort,
                    "repeat": repeat,
                    "reasoning_tokens": turn.usage.reasoning,
                    "output_tokens": turn.usage.output,
                    "latency_seconds": turn.latency_seconds,
                }
            )
    report = {"rows": rows, **evaluate_effort_preflight(model, rows, efforts=levels)}
    if not report["passed"]:
        raise RuntimeError(f"Reasoning-token effort preflight failed for {model}")
    return report
