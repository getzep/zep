from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

from ..ledger import CostLedger
from ..retry import MAX_ATTEMPTS, call_with_retry
from ..settings import ROUTE_SETTINGS
from .base import ModelTurn, ToolCall, Usage, capture_body, parse_arguments, record_call, request_reasoning_fields

ZAI_URL = "https://api.z.ai/api/paas/v4/chat/completions"


class ZAIRoute:
    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        capture_dir: Path | None = None,
        client: httpx.Client | None = None,
        ledger: CostLedger | None = None,
        endpoint: str = ZAI_URL,
    ) -> None:
        if model not in {"glm-5.3-flash", "glm-5.2"}:
            raise ValueError(f"Unsupported Z.AI model: {model}")
        self.model = model
        self.settings = ROUTE_SETTINGS[model]
        self.route = model
        self.key = api_key or os.getenv("Z_AI_KEY")
        self.capture_dir = capture_dir
        self.ledger = ledger
        self.endpoint = endpoint
        self.client = client or httpx.Client(timeout=600)

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
        if self.model == "glm-5.3-flash" and effort == "xhigh":
            raise ValueError("glm-5.3-flash does not support xhigh effort")
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "parameters": tool.get("inputSchema", {}),
                    },
                }
                for tool in tools
            ],
            "thinking": {"type": "enabled"},
            "reasoning_effort": effort,
            "max_tokens": self.settings.max_output_tokens,
            "temperature": (temperature_override if temperature_override is not None else self.settings.temperature),
        }
        if tool_choice is not None:
            body["tool_choice"] = {"type": "function", "function": {"name": tool_choice}}
        capture_body(self.capture_dir, "zai", body)

        deadline = context.get("deadline_monotonic") if context else None

        def request(timeout: float) -> httpx.Response:
            response = self.client.post(
                self.endpoint,
                json=body,
                headers={"authorization": f"Bearer {self.key}", "content-type": "application/json"},
                timeout=timeout,
            )
            response.raise_for_status()
            return response

        successful_call_seconds: list[float] = []

        def record_success(seconds: float) -> None:
            successful_call_seconds.append(seconds)
            if context and isinstance(context.get("model_call_latencies"), list):
                context["model_call_latencies"].append(seconds)

        response = call_with_retry(
            request,
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
        latency = successful_call_seconds[-1]
        result = response.json()
        message = result["choices"][0]["message"]
        calls = tuple(
            ToolCall(
                call.get("id", ""),
                call["function"]["name"],
                parse_arguments(call["function"].get("arguments")),
            )
            for call in message.get("tool_calls", [])
        )
        usage_raw = result.get("usage", {})
        details = usage_raw.get("prompt_tokens_details", {})
        completion_details = usage_raw.get("completion_tokens_details", {})
        usage = Usage(
            input=int(usage_raw.get("prompt_tokens", 0)),
            cache_read=int(details.get("cached_tokens", 0)),
            output=int(usage_raw.get("completion_tokens", 0)),
            reasoning=int(completion_details.get("reasoning_tokens", 0)),
        )
        assistant = {
            "role": "assistant",
            "content": message.get("content"),
            "reasoning_content": message.get("reasoning_content"),
            "tool_calls": message.get("tool_calls", []),
        }
        record_call(
            self.ledger,
            model=self.model,
            route=self.route,
            usage=usage,
            latency=latency,
            context=context,
            http_status=response.status_code,
            request_fields=request_reasoning_fields(body),
        )
        return ModelTurn(message.get("content") or "", calls, usage_raw, usage, (assistant,), latency)
