from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

from ..ledger import CostLedger
from ..retry import MAX_ATTEMPTS, call_with_retry
from ..settings import ROUTE_SETTINGS
from .base import ModelTurn, ToolCall, Usage, capture_body, parse_arguments, record_call

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL_IDS = {"nemotron-3.5-lightning": "nvidia/nemotron-3.5-lightning"}
# The OpenRouter providers that may serve a call, in order, with no fallback to another provider. The
# environment variable EOG_OPENROUTER_PROVIDERS (a comma list) narrows the list, for example to "deepinfra" alone
# when the second provider is slow; the served provider of each call is in the row record.
DEFAULT_OPENROUTER_PROVIDERS = ("deepinfra", "coreweave")


def openrouter_providers() -> list[str]:
    value = os.environ.get("EOG_OPENROUTER_PROVIDERS", "").strip()
    if not value:
        return list(DEFAULT_OPENROUTER_PROVIDERS)
    providers = [item.strip() for item in value.split(",") if item.strip()]
    if not providers:
        raise ValueError("EOG_OPENROUTER_PROVIDERS names no provider")
    return providers


class OpenRouterRoute:
    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        capture_dir: Path | None = None,
        client: httpx.Client | None = None,
        ledger: CostLedger | None = None,
        endpoint: str = OPENROUTER_URL,
    ) -> None:
        if model not in OPENROUTER_MODEL_IDS:
            raise ValueError(f"Unsupported OpenRouter model: {model}")
        self.model = model
        self.settings = ROUTE_SETTINGS[model]
        self.route = "openrouter"
        self.key = api_key or os.getenv("OPENROUTER_API_KEY")
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
        if effort not in {"on", "off"}:
            raise ValueError(f"Unsupported Nemotron effort: {effort}")

        body: dict[str, Any] = {
            "model": OPENROUTER_MODEL_IDS[self.model],
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
            "reasoning": {"enabled": effort == "on"},
            "max_tokens": self.settings.max_output_tokens,
            "provider": {"order": openrouter_providers(), "allow_fallbacks": False},
        }
        if tool_choice is not None:
            body["tool_choice"] = {"type": "function", "function": {"name": tool_choice}}
        temperature = temperature_override if temperature_override is not None else self.settings.temperature
        if temperature is not None:
            body["temperature"] = temperature
        capture_body(self.capture_dir, "openrouter", body)

        deadline = context.get("deadline_monotonic") if context else None

        def request(timeout: float) -> httpx.Response:
            response = self.client.post(
                self.endpoint,
                json=body,
                headers={"authorization": f"Bearer {self.key}", "content-type": "application/json"},
                timeout=timeout,
            )
            response.raise_for_status()
            result = response.json()
            if isinstance(result, dict) and isinstance(result.get("error"), dict) and not result.get("choices"):
                error_code = result["error"].get("code")
                status_code = error_code if type(error_code) is int and 400 <= error_code <= 599 else 502
                error_response = httpx.Response(status_code, json=result, request=response.request)
                raise httpx.HTTPStatusError(
                    "OpenRouter returned an error response",
                    request=response.request,
                    response=error_response,
                )
            return response

        successful_call_seconds: list[float] = []

        def record_success(seconds: float) -> None:
            successful_call_seconds.append(seconds)
            if context and isinstance(context.get("model_call_latencies"), list):
                context["model_call_latencies"].append(seconds)

        request_fields = {
            "request_effort": effort,
            "request_thinking_type": None,
            "request_reasoning_enabled": effort == "on",
        }
        response = call_with_retry(
            request,
            route=self.route,
            ledger=self.ledger,
            context=context,
            request_fields=request_fields,
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
            for call in message.get("tool_calls") or []
        )
        usage_raw = result.get("usage") or {}
        details = usage_raw.get("prompt_tokens_details") or {}
        completion_details = usage_raw.get("completion_tokens_details") or {}
        usage = Usage(
            input=int(usage_raw.get("prompt_tokens") or 0),
            cache_read=int(details.get("cached_tokens") or 0),
            cache_write=int(details.get("cache_write_tokens") or 0),
            output=int(usage_raw.get("completion_tokens") or 0),
            reasoning=int(completion_details.get("reasoning_tokens") or 0),
        )
        assistant = {
            "role": "assistant",
            "content": message.get("content"),
            "tool_calls": message.get("tool_calls") or [],
        }
        reasoning_details = message.get("reasoning_details")
        if reasoning_details:
            assistant["reasoning_details"] = reasoning_details
        record_call(
            self.ledger,
            model=self.model,
            route=self.route,
            usage=usage,
            latency=latency,
            context=context,
            http_status=response.status_code,
            request_fields={
                **request_fields,
                "served_provider": result.get("provider"),
                "openrouter_generation_id": result.get("id"),
                "openrouter_cost_usd": usage_raw.get("cost"),
            },
        )
        return ModelTurn(
            message.get("content") or "",
            calls,
            usage_raw,
            usage,
            (assistant,),
            latency,
            message.get("reasoning") or "",
            result.get("provider"),
        )
