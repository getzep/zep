from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import anthropic
from openai import BadRequestError

from call_ledger import anthropic_http_client, record_failure

CONTENT_ITEM = "anthropic_content"
DEFAULT_MAX_OUTPUT_TOKENS = 64000
CONTEXT_LIMIT_MARKERS = ("prompt is too long", "context window", "too many total text bytes")


def reports_reasoning_tokens(model: str) -> bool:
    """Anthropic counts thinking tokens in the output tokens and does not report them alone."""
    return not model.startswith("claude-")


def _stream_error_outcome(exc: anthropic.APIStatusError) -> str:
    body: dict[str, Any] = exc.body if isinstance(exc.body, dict) else {}
    nested = body.get("error")
    error: dict[str, Any] = nested if isinstance(nested, dict) else body
    return f"stream:{error.get('type') or 'error'}"


def _tool(tool: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": tool["name"],
        "description": tool.get("description", ""),
        "input_schema": tool.get("parameters") or {"type": "object", "properties": {}},
    }


def _messages(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert Responses input items to Anthropic messages and merge consecutive same-role turns."""
    messages: list[dict[str, Any]] = []

    def add(role: str, blocks: list[dict[str, Any]]) -> None:
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"].extend(blocks)
        else:
            messages.append({"role": role, "content": list(blocks)})

    for item in items:
        item_type = item.get("type")
        if item_type == CONTENT_ITEM:
            add("assistant", item["content"])
        elif item.get("anthropic_derived"):
            continue
        elif item_type == "function_call_output":
            add(
                "user",
                [
                    {
                        "type": "tool_result",
                        "tool_use_id": item["call_id"],
                        "content": str(item.get("output") or ""),
                    }
                ],
            )
        elif item.get("role") in ("user", "assistant"):
            content = item.get("content") or ""
            if isinstance(content, list):
                text = "".join(
                    str(part.get("text", "")) for part in content if isinstance(part, dict)
                )
            else:
                text = str(content)
            add(item["role"], [{"type": "text", "text": text or " "}])
        else:
            raise ValueError(f"Unsupported input item for Anthropic: {item_type}")
    return messages


def _response(message: Any) -> SimpleNamespace:
    blocks = [block.model_dump(exclude_none=True) for block in message.content]
    output: list[dict[str, Any]] = [{"type": CONTENT_ITEM, "content": blocks}]
    text = "".join(block["text"] for block in blocks if block["type"] == "text")
    if text:
        output.append(
            {
                "type": "message",
                "role": "assistant",
                "anthropic_derived": True,
                "content": [{"type": "output_text", "text": text}],
            }
        )
    for block in blocks:
        if block["type"] == "tool_use":
            output.append(
                {
                    "type": "function_call",
                    "anthropic_derived": True,
                    "call_id": block["id"],
                    "name": block["name"],
                    "arguments": json.dumps(block.get("input") or {}),
                }
            )
    usage = message.usage
    cache_read = (
        int(usage.cache_read_input_tokens) if usage.cache_read_input_tokens is not None else None
    )
    cache_write = (
        int(usage.cache_creation_input_tokens)
        if usage.cache_creation_input_tokens is not None
        else None
    )
    return SimpleNamespace(
        status="incomplete" if message.stop_reason == "max_tokens" else "completed",
        output=output,
        output_text=text,
        stop_reason=message.stop_reason,
        usage=SimpleNamespace(
            input_tokens=int(usage.input_tokens or 0) + (cache_read or 0) + (cache_write or 0),
            input_tokens_details=SimpleNamespace(
                cached_tokens=cache_read, cache_creation_tokens=cache_write
            ),
            output_tokens=int(usage.output_tokens or 0),
            output_tokens_details=SimpleNamespace(reasoning_tokens=None),
        ),
    )


class _AnthropicResponses:
    native_thinking = True

    def __init__(self, client: anthropic.Anthropic) -> None:
        self._client = client

    def create(self, **kwargs: Any) -> SimpleNamespace:
        request: dict[str, Any] = {
            "model": kwargs["model"],
            "system": kwargs.get("instructions") or "",
            "messages": _messages(list(kwargs.get("input") or [])),
            "max_tokens": kwargs.get("max_output_tokens") or DEFAULT_MAX_OUTPUT_TOKENS,
            "thinking": {"type": "adaptive"},
            "cache_control": {"type": "ephemeral"},
        }
        if tools := kwargs.get("tools"):
            request["tools"] = [_tool(tool) for tool in tools]
        if effort := (kwargs.get("reasoning") or {}).get("effort"):
            request["output_config"] = {"effort": effort}
        try:
            with self._client.messages.stream(**request) as stream:
                message = stream.get_final_message()
        except anthropic.APIStatusError as exc:
            if exc.status_code < 400:
                # The transport saw HTTP 200, so it did not count this stream error event.
                record_failure("anthropic", "POST /v1/messages", _stream_error_outcome(exc))
            if isinstance(exc, anthropic.BadRequestError) and any(
                marker in str(exc).lower() for marker in CONTEXT_LIMIT_MARKERS
            ):
                raise BadRequestError(
                    f"maximum context length: {str(exc)[:400]}",
                    response=exc.response,
                    body=exc.body,
                ) from exc
            raise
        return _response(message)


class AnthropicResponsesClient:
    """A Responses-shaped client that sends each request to the Anthropic Messages API.

    Requests use adaptive thinking, the requested effort, and automatic prompt caching.
    Reasoning tokens are not reported separately by Anthropic, so they count as output tokens.
    """

    def __init__(self, api_key: str) -> None:
        self.responses = _AnthropicResponses(
            anthropic.Anthropic(
                api_key=api_key, max_retries=8, http_client=anthropic_http_client("anthropic")
            )
        )
