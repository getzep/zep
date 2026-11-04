from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from autogen_agentchat.agents import AssistantAgent
from autogen_core.models import UserMessage
from autogen_core.tools import FunctionTool

EXAMPLES = Path(__file__).parents[1] / "examples"
sys.path.insert(0, str(EXAMPLES))

from responses_client import OpenAIResponsesChatCompletionClient  # noqa: E402


def response(output: list[dict[str, object]], status: str = "completed") -> SimpleNamespace:
    return SimpleNamespace(
        output=output,
        status=status,
        incomplete_details=None,
        usage=SimpleNamespace(input_tokens=12, output_tokens=8),
    )


@pytest.mark.asyncio
async def test_assistant_tool_round_trip_uses_stateless_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    mock_client = MagicMock()
    mock_client.responses.create = AsyncMock(
        side_effect=[
            response(
                [
                    {
                        "type": "reasoning",
                        "id": "rs_1",
                        "summary": [],
                        "encrypted_content": "encrypted-state",
                    },
                    {
                        "type": "function_call",
                        "call_id": "call_1",
                        "name": "lookup",
                        "arguments": '{"query":"Zep"}',
                    },
                ]
            ),
            response(
                [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "The lookup found Zep.",
                            }
                        ],
                    }
                ]
            ),
            response(
                [
                    {
                        "type": "reasoning",
                        "id": "rs_2",
                        "summary": [],
                        "encrypted_content": "encrypted-state-2",
                    },
                    {
                        "type": "function_call",
                        "call_id": "call_2",
                        "name": "lookup",
                        "arguments": '{"query":"memory"}',
                    },
                ]
            ),
            response(
                [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "The lookup found memory.",
                            }
                        ],
                    }
                ]
            ),
        ]
    )
    client = OpenAIResponsesChatCompletionClient(client=mock_client)

    async def lookup(query: str) -> str:
        return f"Found {query}"

    tool = FunctionTool(lookup, description="Look up a value")
    agent = AssistantAgent(
        name="assistant",
        model_client=client,
        tools=[tool],
        reflect_on_tool_use=True,
    )

    result = await agent.run(task="Look up Zep.")
    follow_up = await agent.run(task="Look up memory as a second query.")

    assert str(result.messages[-1].content) == "The lookup found Zep."
    assert str(follow_up.messages[-1].content) == "The lookup found memory."
    assert mock_client.responses.create.await_count == 4
    first, second, third, fourth = [
        call.kwargs for call in mock_client.responses.create.await_args_list
    ]
    for args in (first, second, third, fourth):
        assert args["model"] == "gpt-6-luna"
        assert args["reasoning"] == {"effort": "low"}
        assert args["store"] is False
        assert args["include"] == ["reasoning.encrypted_content"]
        assert "previous_response_id" not in args
        assert "temperature" not in args
        assert "top_p" not in args

    reasoning_by_call = {
        "call_1": {
            "type": "reasoning",
            "id": "rs_1",
            "summary": [],
            "encrypted_content": "encrypted-state",
        },
        "call_2": {
            "type": "reasoning",
            "id": "rs_2",
            "summary": [],
            "encrypted_content": "encrypted-state-2",
        },
    }

    def assert_reasoning_replayed(input_items: list[dict], call_id: str) -> int:
        call_index = next(
            i
            for i, item in enumerate(input_items)
            if item.get("type") == "function_call" and item.get("call_id") == call_id
        )
        assert input_items[call_index - 1] == reasoning_by_call[call_id]
        return call_index

    second_input = second["input"]
    call_index = assert_reasoning_replayed(second_input, "call_1")
    assert second_input[call_index]["call_id"] == "call_1"
    assert second_input[call_index + 1] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "Found Zep",
    }
    third_input = third["input"]
    first_call_index = assert_reasoning_replayed(third_input, "call_1")
    assert third_input[first_call_index + 1] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "Found Zep",
    }
    assert any(
        item.get("role") == "user" and item.get("content") == "Look up memory as a second query."
        for item in third_input
    )
    fourth_input = fourth["input"]
    first_call_index = assert_reasoning_replayed(fourth_input, "call_1")
    assert fourth_input[first_call_index + 1]["call_id"] == "call_1"
    second_call_index = assert_reasoning_replayed(fourth_input, "call_2")
    assert fourth_input[second_call_index + 1] == {
        "type": "function_call_output",
        "call_id": "call_2",
        "output": "Found memory",
    }
    assert first["tools"] == [
        {
            "type": "function",
            "name": "lookup",
            "description": "Look up a value",
            "parameters": tool.schema["parameters"],
            "strict": False,
        }
    ]


def test_text_only_example_sets_low_reasoning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    from autogen_ext.models.openai import OpenAIChatCompletionClient

    client = OpenAIChatCompletionClient(
        model="gpt-6-luna",
        reasoning_effort="low",
        model_info={
            "vision": False,
            "function_calling": False,
            "json_output": True,
            "structured_output": True,
            "family": "unknown",
        },
    )
    assert client._raw_config["reasoning_effort"] == "low"
    assert client._raw_config["model"] == "gpt-6-luna"


@pytest.mark.asyncio
async def test_sampling_parameters_are_not_sent_to_responses() -> None:
    mock_client = MagicMock()
    mock_client.responses.create = AsyncMock(
        return_value=response(
            [{"type": "message", "content": [{"type": "output_text", "text": "Hello"}]}]
        )
    )
    client = OpenAIResponsesChatCompletionClient(client=mock_client)

    await client.create(
        [UserMessage(content="Hello", source="user")],
        extra_create_args={
            "input": [{"role": "user", "content": "Replace the request"}],
            "temperature": 0.2,
            "top_p": 0.9,
            "presence_penalty": 0.1,
            "frequency_penalty": 0.1,
            "seed": 42,
            "logit_bias": {},
            "stop": ["END"],
            "best_of": 2,
            "top_k": 10,
            "min_p": 0.2,
            "typical_p": 0.8,
        },
    )

    params = mock_client.responses.create.await_args.kwargs
    assert params["input"] == [{"role": "user", "content": "Hello"}]
    assert (
        not {
            "temperature",
            "top_p",
            "presence_penalty",
            "frequency_penalty",
            "seed",
            "logit_bias",
            "stop",
            "best_of",
            "top_k",
            "min_p",
            "typical_p",
        }
        & params.keys()
    )
