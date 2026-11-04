from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from openai import OpenAI

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
    sdk_tool_schema,
)


class OpenAIRoute:
    def __init__(
        self,
        *,
        model: str = "gpt-6-luna",
        api_key: str | None = None,
        capture_dir: Path | None = None,
        client: Any = None,
        ledger: CostLedger | None = None,
    ) -> None:
        self.model = model
        self.settings = ROUTE_SETTINGS[self.model]
        self.route = self.model
        self.capture_dir = capture_dir
        self.ledger = ledger
        self.client = client or OpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"), max_retries=0, timeout=600)

    @staticmethod
    def _input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for message in messages:
            if message.get("provider_items") is not None:
                items.extend(message["provider_items"])
            elif message["role"] == "tool":
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": message["tool_call_id"],
                        "output": message.get("content", ""),
                    }
                )
            else:
                items.append({"role": message["role"], "content": message.get("content", "")})
        return items

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
        body = {
            "model": self.model,
            "instructions": system,
            "input": self._input(messages),
            "tools": sdk_tool_schema(tools),
            "reasoning": {"effort": effort},
            "max_output_tokens": self.settings.max_output_tokens,
            "prompt_cache_key": f"eog-{task_id}",
            "include": ["reasoning.encrypted_content"],
            "store": False,
        }
        if tool_choice is not None:
            body["tool_choice"] = {"type": "function", "name": tool_choice}
        temperature = temperature_override if temperature_override is not None else self.settings.temperature
        if temperature is not None:
            body["temperature"] = temperature
        capture_body(self.capture_dir, "openai", body)
        successful_call_seconds: list[float] = []

        def record_success(seconds: float) -> None:
            successful_call_seconds.append(seconds)
            if context and isinstance(context.get("model_call_latencies"), list):
                context["model_call_latencies"].append(seconds)

        deadline = context.get("deadline_monotonic") if context else None
        # Parse inside the retried call: a parse error after HTTP 200 is a failed attempt in the ledger.
        parsed_response = call_with_retry(
            lambda timeout: parse_raw_response(self.client.responses.with_raw_response.create(**body, timeout=timeout)),
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
        output = [
            item.model_dump(by_alias=True, exclude_none=True) if hasattr(item, "model_dump") else dict(item)
            for item in response.output
        ]
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for item in output:
            if item.get("type") == "message":
                for part in item.get("content", []):
                    if part.get("type") in {"output_text", "text"}:
                        text_parts.append(part.get("text", ""))
            elif item.get("type") == "function_call":
                calls.append(ToolCall(item["call_id"], item["name"], parse_arguments(item.get("arguments"))))
        usage_raw = response.usage.model_dump() if hasattr(response.usage, "model_dump") else dict(response.usage)
        input_details = usage_raw.get("input_tokens_details", {})
        output_details = usage_raw.get("output_tokens_details", {})
        reasoning_tokens = output_details.get("reasoning_tokens")
        usage = Usage(
            input=int(usage_raw.get("input_tokens", 0)),
            cache_read=int(input_details.get("cached_tokens", 0)),
            output=int(usage_raw.get("output_tokens", 0)),
            reasoning=int(reasoning_tokens) if reasoning_tokens is not None else None,
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
        history = ({"role": "assistant", "provider_items": output},)
        return ModelTurn("".join(text_parts), tuple(calls), usage_raw, usage, history, latency)
