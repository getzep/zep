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
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": "The lookup returned Zep. TERMINATE"}
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
    user.initiate_chat(assistant, message="Use lookup to search for Zep.", max_turns=3)

    assert mock_client.responses.create.call_count == 2
    first, second = [call.kwargs for call in mock_client.responses.create.call_args_list]
    for args in (first, second):
        assert args["model"] == "gpt-6-luna"
        assert args["reasoning"] == {"effort": "low"}
        assert args["store"] is False
        assert args["include"] == ["reasoning.encrypted_content"]
        assert "previous_response_id" not in args
        assert "temperature" not in args
        assert "top_p" not in args
    second_input = second["input"]
    call_index = next(
        i for i, item in enumerate(second_input) if item.get("type") == "function_call"
    )
    assert second_input[call_index - 1] == {
        "type": "reasoning",
        "id": "rs_1",
        "summary": [],
        "encrypted_content": "encrypted-state",
    }
    assert second_input[call_index]["call_id"] == "call_1"
    assert second_input[call_index + 1] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "Found Zep",
    }
    tool = first["tools"][0]
    assert tool["type"] == "function"
    assert tool["name"] == "lookup"
    assert tool["description"] == "Look up a value"
    assert tool["parameters"]["type"] == "object"
    assert tool["strict"] is False
