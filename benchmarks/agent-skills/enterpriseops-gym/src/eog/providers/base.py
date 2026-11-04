from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..errors import AbortRequestedError
from ..prices import prompt_token_count


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: Any


@dataclass(frozen=True, slots=True)
class Usage:
    input: int = 0
    cache_read: int = 0
    cache_write: int = 0
    output: int = 0
    reasoning: int | None = 0


@dataclass(frozen=True, slots=True)
class ModelTurn:
    text: str
    tool_calls: tuple[ToolCall, ...]
    raw_usage: dict[str, Any]
    usage: Usage
    history_items: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    latency_seconds: float = 0.0
    reasoning_text: str = ""
    served_provider: str | None = None


def check_abort_requested(context: dict[str, Any] | None) -> None:
    abort_requested = context.get("abort_requested") if context else None
    if abort_requested is not None and abort_requested.is_set():
        raise AbortRequestedError("A second stop signal requested an episode abort")


def parse_arguments(value: str | dict[str, Any] | None) -> Any:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def capture_body(directory: Path | None, route: str, body: dict[str, Any]) -> None:
    if directory is None:
        return
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{route}.json"
    if path.exists():
        return

    def redact(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: "[REDACTED]"
                if re.search(r"(api.?key|authorization|token|secret|credential)", key, re.I)
                else redact(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [redact(item) for item in value]
        return value

    path.write_text(json.dumps(redact(body), indent=2) + "\n")


def request_reasoning_fields(body: dict[str, Any]) -> dict[str, str | None]:
    output_config = body.get("output_config")
    reasoning = body.get("reasoning")
    if isinstance(output_config, dict) and "effort" in output_config:
        effort = output_config["effort"]
    elif isinstance(reasoning, dict) and "effort" in reasoning:
        effort = reasoning["effort"]
    else:
        effort = body.get("reasoning_effort")
    thinking = body.get("thinking")
    thinking_type = thinking.get("type") if isinstance(thinking, dict) else None
    return {
        "request_effort": effort if isinstance(effort, str) else None,
        "request_thinking_type": thinking_type if isinstance(thinking_type, str) else None,
    }


@dataclass(frozen=True, slots=True)
class ParsedResponse:
    status_code: int
    response: Any


def parse_raw_response(raw_response: Any) -> ParsedResponse:
    """Parse an SDK raw response inside the retried call, so that a parse error counts as a failed attempt."""
    return ParsedResponse(status_code=raw_response.status_code, response=raw_response.parse())


def record_call(
    ledger: Any,
    *,
    model: str,
    route: str,
    usage: Usage,
    latency: float,
    context: dict[str, Any] | None,
    http_status: int | None = None,
    request_fields: dict[str, Any] | None = None,
) -> None:
    if ledger is None:
        return
    context_fields = ("cell", "arm", "task", "trial", "attempt")
    ledger.append_call(
        {
            **{key: context[key] for key in context_fields if context and key in context},
            **(request_fields or {}),
            "model": model,
            "route": route,
            "input_tokens": usage.input,
            "prompt_tokens": prompt_token_count(
                model,
                input_tokens=usage.input,
                cache_read_tokens=usage.cache_read,
                cache_write_tokens=usage.cache_write,
            ),
            "cache_read_tokens": usage.cache_read,
            "cache_write_tokens": usage.cache_write,
            "output_tokens": usage.output,
            "reasoning_tokens": usage.reasoning,
            "latency_seconds": latency,
            "http_status": http_status,
        }
    )


def sdk_tool_schema(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool.get("inputSchema", tool.get("parameters", {"type": "object", "properties": {}})),
            "strict": False,
        }
        for tool in tools
    ]
