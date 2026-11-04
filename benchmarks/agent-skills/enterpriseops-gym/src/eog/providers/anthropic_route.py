from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import anthropic

from ..ledger import CostLedger
from ..retry import MAX_ATTEMPTS, call_with_retry
from ..settings import ROUTE_SETTINGS
from .base import (
    ModelTurn,
    ToolCall,
    Usage,
    capture_body,
    parse_arguments,
    parse_raw_response,
    record_call,
    request_reasoning_fields,
)

MODELS = {"opus": "claude-opus-5-5", "fable": "claude-fable-5-1", "haiku": "claude-haiku-5-5"}
UNSUPPORTED_SCHEMA_KEYS = frozenset({"oneOf", "anyOf", "allOf"})


def _message_history(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for message in messages:
        role = message["role"]
        if role == "tool":
            item = {
                "type": "tool_result",
                "tool_use_id": message["tool_call_id"],
                "content": message.get("content", ""),
                "is_error": bool(message.get("is_error", False)),
            }
            if output and output[-1]["role"] == "user" and isinstance(output[-1]["content"], list):
                output[-1]["content"].append(item)
            else:
                output.append({"role": "user", "content": [item]})
        else:
            content = message.get("content", "")
            if role == "assistant" and isinstance(content, list) and content:
                last_block = content[-1]
                if isinstance(last_block, dict) and last_block.get("type") in {"thinking", "redacted_thinking"}:
                    content = content[:-1]
                    if not content:
                        continue
            output.append({"role": role, "content": content})
    if output and output[-1]["role"] == "user":
        last = output[-1]["content"]
        if isinstance(last, str):
            output[-1]["content"] = [{"type": "text", "text": last, "cache_control": {"type": "ephemeral"}}]
        elif last:
            last[-1]["cache_control"] = {"type": "ephemeral"}
    return output


class AnthropicRoute:
    supports_forced_tool_choice = False

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        capture_dir: Path | None = None,
        client: Any = None,
        ledger: CostLedger | None = None,
    ) -> None:
        if model not in MODELS.values():
            raise ValueError(f"Unsupported Anthropic model: {model}")
        self.model = model
        self.settings = ROUTE_SETTINGS[model]
        self.route = model
        self.capture_dir = capture_dir
        self.ledger = ledger
        self.client = client or anthropic.Anthropic(
            api_key=api_key or os.getenv("ANTHROPIC_API_KEY"), max_retries=0, timeout=600
        )

    def complete(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        effort: str,
        task_id: str,
        context: dict[str, Any] | None = None,
        temperature_override: float | None = None,
        attempt_limit: int | None = None,
        tool_choice: str | None = None,
    ) -> ModelTurn:
        request_tools = [
            {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "input_schema": {
                    key: value
                    for key, value in (tool.get("inputSchema") or {}).items()
                    if key not in UNSUPPORTED_SCHEMA_KEYS
                },
            }
            for tool in tools
        ]
        if request_tools:
            request_tools[-1]["cache_control"] = {"type": "ephemeral"}
        body = {
            "model": self.model,
            "max_tokens": self.settings.max_output_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": _message_history(messages),
            "tools": request_tools,
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": effort},
        }
        if tool_choice is not None:
            raise ValueError("Anthropic does not accept a forced tool_choice with thinking enabled")
        temperature = temperature_override if temperature_override is not None else self.settings.temperature
        if temperature is not None:
            body["temperature"] = temperature
        capture_body(self.capture_dir, "anthropic", body)
        successful_call_seconds: list[float] = []

        def record_success(seconds: float) -> None:
            successful_call_seconds.append(seconds)
            if context and isinstance(context.get("model_call_latencies"), list):
                context["model_call_latencies"].append(seconds)

        deadline = context.get("deadline_monotonic") if context else None
        # Parse inside the retried call: a parse error after HTTP 200 is a failed attempt in the ledger.
        parsed_response = call_with_retry(
            lambda timeout: parse_raw_response(self.client.messages.with_raw_response.create(**body, timeout=timeout)),
            route=self.route,
            ledger=self.ledger,
            context=context,
            request_fields=request_reasoning_fields(body),
            deadline_monotonic=deadline,
            model_timeout=600.0,
            on_success_duration=record_success,
            max_attempts=(
                attempt_limit
                if attempt_limit is not None
                else (1 if temperature_override is not None else MAX_ATTEMPTS)
            ),
        )
        http_status = parsed_response.status_code
        response = parsed_response.response
        latency = successful_call_seconds[-1]
        content = [block.model_dump() if hasattr(block, "model_dump") else dict(block) for block in response.content]
        text = "".join(block.get("text", "") for block in content if block.get("type") == "text")
        calls = tuple(
            ToolCall(
                id=block["id"],
                name=block["name"],
                arguments=parse_arguments(block.get("input")),
            )
            for block in content
            if block.get("type") == "tool_use"
        )
        usage_obj = response.usage
        raw_usage = usage_obj.model_dump() if hasattr(usage_obj, "model_dump") else dict(usage_obj)
        output_details = raw_usage.get("output_tokens_details", {})
        usage = Usage(
            input=int(raw_usage.get("input_tokens", 0)),
            cache_read=int(raw_usage.get("cache_read_input_tokens", 0)),
            cache_write=int(raw_usage.get("cache_creation_input_tokens", 0)),
            output=int(raw_usage.get("output_tokens", 0)),
            reasoning=(
                int(output_details.get("thinking_tokens", output_details.get("reasoning_tokens")))
                if output_details.get("thinking_tokens", output_details.get("reasoning_tokens")) is not None
                else None
            ),
        )
        record_call(
            self.ledger,
            model=self.model,
            route=self.route,
            usage=usage,
            latency=latency,
            context=context,
            http_status=http_status,
            request_fields=request_reasoning_fields(body),
        )
        return ModelTurn(text, calls, raw_usage, usage, ({"role": "assistant", "content": content},), latency)
