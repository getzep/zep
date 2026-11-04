from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx

from eog.providers.anthropic_route import AnthropicRoute
from eog.providers.openai_route import OpenAIRoute
from eog.providers.zai_route import ZAIRoute


def test_anthropic_history_drops_only_final_thinking_block() -> None:
    class Messages:
        def __init__(self) -> None:
            self.requests: list[dict[str, Any]] = []

        def create(self, **body: Any) -> SimpleNamespace:
            self.requests.append(body)
            return SimpleNamespace(
                content=[],
                usage=SimpleNamespace(model_dump=lambda: {"input_tokens": 0, "output_tokens": 0}),
            )

        @property
        def with_raw_response(self) -> SimpleNamespace:
            return SimpleNamespace(
                create=lambda **body: SimpleNamespace(status_code=200, parse=lambda: self.create(**body))
            )

    client_messages = Messages()
    route = AnthropicRoute("claude-haiku-5-5", client=SimpleNamespace(messages=client_messages))
    thinking = {"type": "thinking", "thinking": "reasoning", "signature": "sig"}
    tool_use = {
        "type": "tool_use",
        "id": "call-1",
        "name": "calendar__find_event",
        "input": {"query": "launch"},
    }
    final_thinking = {"type": "thinking", "thinking": "more reasoning", "signature": "sig2"}

    def request_for(assistant_content: list[dict[str, Any]]) -> list[dict[str, Any]]:
        route.complete(
            "system",
            [
                {"role": "user", "content": "Find the launch event."},
                {"role": "assistant", "content": assistant_content},
                {"role": "tool", "tool_call_id": "call-1", "content": "Found it."},
            ],
            [],
            effort="high",
            task_id="anthropic-history",
            attempt_limit=1,
        )
        return client_messages.requests[-1]["messages"]

    assistant_content = [thinking.copy(), tool_use.copy(), final_thinking.copy()]
    original_content = [block.copy() for block in assistant_content]
    request_messages = request_for(assistant_content)
    request_assistant = next(message for message in request_messages if message["role"] == "assistant")

    assert request_assistant["content"] == [thinking, tool_use]
    assert request_assistant["content"][0]["signature"] == "sig"
    assert assistant_content == original_content

    tool_use_content = [thinking.copy(), tool_use.copy()]
    original_tool_use_content = [block.copy() for block in tool_use_content]
    request_messages = request_for(tool_use_content)
    request_assistant = next(message for message in request_messages if message["role"] == "assistant")

    assert request_assistant["content"] == tool_use_content
    assert tool_use_content == original_tool_use_content


def test_openai_history_round_trip_preserves_provider_items() -> None:
    provider_items = [
        {
            "type": "function_call",
            "call_id": "call-1",
            "name": "calendar__find_event",
            "arguments": '{"query":"launch"}',
        }
    ]
    history = [
        {"role": "user", "content": "Find the launch event."},
        {"role": "assistant", "provider_items": provider_items},
        {"role": "tool", "tool_call_id": "call-1", "content": "Found it."},
    ]
    assert OpenAIRoute._input(history) == [
        {"role": "user", "content": "Find the launch event."},
        *provider_items,
        {"type": "function_call_output", "call_id": "call-1", "output": "Found it."},
    ]


def test_zai_history_round_trip_preserves_assistant_tool_calls() -> None:
    requests = []
    response_body = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "reasoning_content": "Need to search.",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {"name": "calendar__find_event", "arguments": '{"query":"launch"}'},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "prompt_tokens_details": {"cached_tokens": 0},
            "completion_tokens_details": {"reasoning_tokens": 2},
        },
    }

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=response_body)

    client = httpx.Client(transport=httpx.MockTransport(respond))
    route = ZAIRoute("glm-5.2", api_key="test-key", client=client, endpoint="https://provider.test")
    user = {"role": "user", "content": "Find the launch event."}
    first = route.complete("system", [user], [], effort="xhigh", task_id="test")
    tool_result = {"role": "tool", "tool_call_id": "call-1", "content": "Found it."}
    route.complete("system", [user, *first.history_items, tool_result], [], effort="xhigh", task_id="test")

    assert requests[1]["messages"] == [
        {"role": "system", "content": "system"},
        user,
        first.history_items[0],
        tool_result,
    ]
    client.close()
