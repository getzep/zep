from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from ..ledger import CostLedger
from ..retry import MAX_ATTEMPTS, call_with_retry
from ..settings import ROUTE_SETTINGS
from .base import ModelTurn, ToolCall, Usage, capture_body, parse_arguments, record_call, request_reasoning_fields

DEEPSEEK_MODEL = "deepseek-v4.1-flash-1017"
DEEPSEEK_EFFORTS = ("none", "low", "medium", "high", "xhigh", "max")
DEEPSEEK_MATRIX_EFFORTS = ("low", "medium", "max")
CHAT_COMPLETIONS_PATH = "/v1/chat/completions"


class DeepSeekRoute:
    def __init__(
        self,
        model: str = DEEPSEEK_MODEL,
        *,
        api_key: str | None = None,
        capture_dir: Path | None = None,
        client: httpx.Client | None = None,
        ledger: CostLedger | None = None,
        endpoint: str | None = None,
    ) -> None:
        if model != DEEPSEEK_MODEL:
            raise ValueError(f"Unsupported DeepSeek model: {model}")
        self.model = model
        self.settings = ROUTE_SETTINGS[model]
        self.route = model
        self.key = api_key or os.getenv("DEEPSEEK_BEARER_TOKEN")
        self.capture_dir = capture_dir
        self.ledger = ledger
        base_url = endpoint or os.getenv("DEEPSEEK_BASE_URL")
        if not base_url:
            raise ValueError("Set DEEPSEEK_BASE_URL to an OpenAI-compatible API base URL.")
        # The bearer token must not travel over plain HTTP.
        if urlsplit(base_url).scheme.lower() != "https":
            source = "endpoint" if endpoint is not None else "DEEPSEEK_BASE_URL"
            raise ValueError(f"{source} must use https.")
        if not self.key:
            raise ValueError("Set DEEPSEEK_BEARER_TOKEN.")
        self.endpoint = f"{base_url.rstrip('/')}{CHAT_COMPLETIONS_PATH}"
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
        if effort not in DEEPSEEK_EFFORTS:
            raise ValueError(f"Unsupported DeepSeek effort: {effort}")
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "reasoning_effort": "high" if effort == "medium" else effort,
            "max_tokens": self.settings.max_output_tokens,
        }
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "parameters": tool.get("inputSchema", {}),
                    },
                }
                for tool in tools
            ]
            body["tool_choice"] = "auto"
        if tool_choice is not None:
            body["tool_choice"] = {"type": "function", "function": {"name": tool_choice}}
        temperature = temperature_override if temperature_override is not None else self.settings.temperature
        if temperature is not None:
            body["temperature"] = temperature
        capture_body(self.capture_dir, "deepseek", body)

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
        http_status = response.status_code
        result = response.json()
        message = result["choices"][0]["message"]
        raw_tool_calls = message.get("tool_calls") or []
        calls = tuple(
            ToolCall(
                call.get("id", ""),
                call["function"]["name"],
                parse_arguments(call["function"].get("arguments")),
            )
            for call in raw_tool_calls
        )
        usage_raw = result.get("usage") or {}
        prompt_details = usage_raw.get("prompt_tokens_details") or {}
        completion_details = usage_raw.get("completion_tokens_details") or {}
        usage = Usage(
            input=int(usage_raw.get("prompt_tokens") or 0),
            cache_read=int(prompt_details.get("cached_tokens") or 0),
            output=int(usage_raw.get("completion_tokens") or 0),
            reasoning=int(completion_details.get("reasoning_tokens") or 0),
        )
        assistant = {
            "role": "assistant",
            "content": message.get("content"),
            "tool_calls": raw_tool_calls,
        }
        if message.get("reasoning_content") is not None:
            assistant["reasoning_content"] = message["reasoning_content"]
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
        return ModelTurn(message.get("content") or "", calls, usage_raw, usage, (assistant,), latency)
