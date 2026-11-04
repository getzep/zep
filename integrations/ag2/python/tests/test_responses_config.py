from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from autogen import AssistantAgent, LLMConfig, UserProxyAgent

EXAMPLES = Path(__file__).parents[1] / "examples"
sys.path.insert(0, str(EXAMPLES))

from responses_client import OpenAIResponsesModelClient  # noqa: E402


def response(output: list[dict[str, object]]) -> SimpleNamespace:
    return SimpleNamespace(
        id="resp_1",
        created_at=1,
        model="gpt-6-luna",
        output=output,
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )


def test_assistant_tool_round_trip_uses_stateless_responses(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    mock_client = MagicMock()
    mock_client.responses.create.side_effect = [
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
                            "text": "The lookups found Zep and memory. TERMINATE",
                        }
                    ],
                }
            ]
        ),
        response(
            [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "The two lookup values were Zep and memory. TERMINATE",
                        }
                    ],
                }
            ]
        ),
    ]
    config = LLMConfig(
        {
            "model": "gpt-6-luna",
            "reasoning_effort": "low",
            "model_client_cls": "OpenAIResponsesModelClient",
            "api_key": "sk-test",
        }
    )
    assistant = AssistantAgent(
        name="assistant",
        llm_config=config,
        is_termination_msg=lambda message: "TERMINATE" in (message.get("content") or ""),
    )
    user = UserProxyAgent(
        name="user",
        human_input_mode="NEVER",
        code_execution_config=False,
        is_termination_msg=lambda message: "TERMINATE" in (message.get("content") or ""),
    )

    def lookup(query: str) -> str:
        return f"Found {query}"

    assistant.register_for_llm(description="Look up a value")(lookup)
    user.register_for_execution()(lookup)
    assistant.register_model_client(model_client_cls=OpenAIResponsesModelClient, client=mock_client)
    user.initiate_chat(assistant, message="Look up Zep and memory.", max_turns=10)
    user.initiate_chat(
        assistant,
        clear_history=False,
        message="What were the two lookup values?",
        max_turns=10,
    )

    assert mock_client.responses.create.call_count == 4
    first, second, third, fourth = [
        call.kwargs for call in mock_client.responses.create.call_args_list
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
    for request in (third, fourth):
        input_items = request["input"]
        first_call_index = assert_reasoning_replayed(input_items, "call_1")
        assert input_items[first_call_index + 1]["call_id"] == "call_1"
        second_call_index = assert_reasoning_replayed(input_items, "call_2")
        assert input_items[second_call_index + 1]["call_id"] == "call_2"
        assert input_items[second_call_index + 1] == {
            "type": "function_call_output",
            "call_id": "call_2",
            "output": "Found memory",
        }
    assert any(
        item.get("role") == "user" and item.get("content") == "What were the two lookup values?"
        for item in fourth["input"]
    )
    tool = first["tools"][0]
    assert tool["type"] == "function"
    assert tool["name"] == "lookup"
    assert tool["description"] == "Look up a value"
    assert tool["parameters"]["type"] == "object"
    assert tool["strict"] is False


def test_assistant_content_follows_reasoning_before_tool_calls(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    mock_client = MagicMock()
    mock_client.responses.create.side_effect = [
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
                    "arguments": "{}",
                },
            ]
        ),
        response(
            [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "Done"}],
                }
            ]
        ),
    ]
    client = OpenAIResponsesModelClient(
        {"model": "gpt-6-luna", "reasoning_effort": "low"},
        client=mock_client,
    )

    client.create({"messages": [{"role": "user", "content": "Look up a value."}]})
    client.create(
        {
            "messages": [
                {
                    "role": "assistant",
                    "content": "I will look up the value.",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "lookup", "arguments": "{}"},
                        }
                    ],
                }
            ]
        }
    )

    assert mock_client.responses.create.call_args_list[1].kwargs["input"] == [
        {
            "type": "reasoning",
            "id": "rs_1",
            "summary": [],
            "encrypted_content": "encrypted-state",
        },
        {"role": "assistant", "content": "I will look up the value."},
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "lookup",
            "arguments": "{}",
        },
    ]
