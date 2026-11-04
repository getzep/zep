from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from eog.providers.anthropic_route import AnthropicRoute
from eog.providers.openai_route import OpenAIRoute
from eog.providers.zai_route import ZAIRoute
from eog.run import CompositeMCPClient

SCHEMA_FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "gym_tool_schemas.json").read_text())
UNSUPPORTED_ANTHROPIC_KEYS = {"oneOf", "anyOf", "allOf"}


class FakeAnthropicMessages:
    def __init__(self) -> None:
        self.body: dict[str, Any] = {}

    def create(self, **body: Any) -> SimpleNamespace:
        self.body = body
        return SimpleNamespace(
            content=[],
            usage=SimpleNamespace(model_dump=lambda: {"input_tokens": 0, "output_tokens": 0}),
        )

    @property
    def with_raw_response(self) -> SimpleNamespace:
        return SimpleNamespace(
            create=lambda **body: SimpleNamespace(status_code=200, parse=lambda: self.create(**body))
        )


class FakeAnthropic:
    def __init__(self) -> None:
        self.messages = FakeAnthropicMessages()


class FakeResponses:
    def __init__(self) -> None:
        self.body: dict[str, Any] = {}

    def create(self, **body: Any) -> SimpleNamespace:
        self.body = body
        return SimpleNamespace(
            output=[],
            usage=SimpleNamespace(
                model_dump=lambda: {
                    "input_tokens": 0,
                    "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens": 0,
                    "output_tokens_details": {"reasoning_tokens": 0},
                }
            ),
        )

    @property
    def with_raw_response(self) -> SimpleNamespace:
        return SimpleNamespace(
            create=lambda **body: SimpleNamespace(status_code=200, parse=lambda: self.create(**body))
        )


class FakeOpenAI:
    def __init__(self) -> None:
        self.responses = FakeResponses()


def _request_body(provider: str, tools: list[dict[str, Any]]) -> dict[str, Any]:
    if provider == "anthropic":
        client = FakeAnthropic()
        AnthropicRoute("claude-opus-5-5", client=client).complete(
            "system",
            [{"role": "user", "content": "test"}],
            tools,
            effort="max",
            task_id="schema-test",
            attempt_limit=1,
        )
        return client.messages.body
    if provider == "openai_responses":
        client = FakeOpenAI()
        OpenAIRoute(client=client).complete(
            "system",
            [{"role": "user", "content": "test"}],
            tools,
            effort="low",
            task_id="schema-test",
            attempt_limit=1,
        )
        return client.responses.body

    captured: dict[str, Any] = {}

    def capture(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": {}},
        )

    with httpx.Client(transport=httpx.MockTransport(capture)) as client:
        ZAIRoute("glm-5.3-flash", api_key="test-key", client=client).complete(
            "system",
            [{"role": "user", "content": "test"}],
            tools,
            effort="low",
            task_id="schema-test",
            attempt_limit=1,
        )
    return captured


def _provider_schemas(provider: str, body: dict[str, Any]) -> list[dict[str, Any]]:
    if provider == "anthropic":
        return [tool["input_schema"] for tool in body["tools"]]
    if provider == "openai_responses":
        return [tool["parameters"] for tool in body["tools"]]
    return [tool["function"]["parameters"] for tool in body["tools"]]


@pytest.mark.parametrize("provider", ("anthropic", "openai_responses", "zai"))
@pytest.mark.parametrize(
    ("family", "tool_name"),
    (("calendar", "create_event"), ("email", "send_message")),
)
def test_provider_body_preserves_gym_schema(
    provider: str,
    family: str,
    tool_name: str,
) -> None:
    tool = SCHEMA_FIXTURES[family][tool_name]
    body = _request_body(provider, [tool])
    actual = _provider_schemas(provider, body)[0]
    schema = tool["inputSchema"]
    expected = (
        {key: value for key, value in schema.items() if key not in UNSUPPORTED_ANTHROPIC_KEYS}
        if provider == "anthropic"
        else schema
    )

    assert actual == expected
    if family == "email":
        assert "oneOf" in schema
        assert ("oneOf" not in actual) is (provider == "anthropic")


def test_anthropic_removes_only_top_level_union_schema_keys() -> None:
    schema = {
        "type": "object",
        "oneOf": [{"type": "string"}],
        "anyOf": [{"type": "number"}],
        "allOf": [{"type": "object"}],
        "properties": {"nested": {"oneOf": [{"type": "boolean"}]}},
    }
    tool = {"name": "union_tool", "inputSchema": schema}

    body = _request_body("anthropic", [tool])

    assert _provider_schemas("anthropic", body) == [
        {"type": "object", "properties": {"nested": {"oneOf": [{"type": "boolean"}]}}}
    ]


class StubMCPClient:
    def __init__(self, tool: dict[str, Any]) -> None:
        self.tool = tool
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def initialize(self) -> None:
        pass

    def tools_for(self, task: SimpleNamespace) -> list[dict[str, Any]]:
        return [self.tool]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, arguments))
        return {"content": [], "isError": False}

    def close(self) -> None:
        pass


def test_composite_passes_minimal_create_event_arguments_without_added_fields() -> None:
    tool = SCHEMA_FIXTURES["calendar"]["create_event"]
    client = StubMCPClient(tool)
    composite = CompositeMCPClient({"calendar": client})

    exposed_tools = composite.start(SimpleNamespace(selected_tools=["create_event"]))
    arguments = {
        "calendarId": "calendar-123",
        "start": {"dateTime": "2030-01-01T15:00:00Z", "timeZone": "UTC"},
        "end": {"dateTime": "2030-01-01T16:00:00Z", "timeZone": "UTC"},
    }
    result = composite.call_tool("calendar__create_event", arguments)

    assert exposed_tools[0]["inputSchema"] == tool["inputSchema"]
    assert result == {"content": [], "isError": False}
    assert client.calls == [("create_event", arguments)]
    assert client.calls[0][1] is arguments
    assert set(client.calls[0][1]) == {"calendarId", "start", "end"}
