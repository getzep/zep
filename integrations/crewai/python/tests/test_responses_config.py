from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from crewai import Agent, Crew, Process, Task
from crewai.llms.providers.openai.completion import OpenAICompletion
from crewai.tools import tool
from openai.types.responses import Response

EXAMPLES = Path(__file__).parents[1] / "examples"
sys.path.insert(0, str(EXAMPLES))

from responses_llm import ReasoningReplayOpenAICompletion  # noqa: E402


def response(output: list[dict[str, Any]], response_id: str) -> Response:
    return Response.model_validate(
        {
            "id": response_id,
            "object": "response",
            "created_at": 1,
            "status": "completed",
            "error": None,
            "incomplete_details": None,
            "instructions": None,
            "metadata": None,
            "model": "gpt-6-luna",
            "output": output,
            "parallel_tool_calls": True,
            "temperature": None,
            "tool_choice": "auto",
            "tools": [],
            "top_p": None,
            "truncation": "disabled",
            "usage": {
                "input_tokens": 10,
                "input_tokens_details": {"cache_write_tokens": 0, "cached_tokens": 0},
                "output_tokens": 5,
                "output_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": 15,
            },
        }
    )


def test_a_new_conversation_drops_unreferenced_reasoning() -> None:
    llm = ReasoningReplayOpenAICompletion(api_key="test-openai-key")
    llm._reasoning_by_call["old-call"] = [{"type": "reasoning", "id": "old-reasoning"}]

    params = llm._prepare_responses_params(
        messages=[{"role": "user", "content": "New conversation"}]
    )

    assert llm._reasoning_by_call == {}
    assert params["input"] == [{"role": "user", "content": "New conversation"}]


def test_crew_kickoff_replays_reasoning_for_tool_rounds(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    mock_client = MagicMock()
    mock_client.responses.create.side_effect = [
        response(
            [
                {
                    "id": "rs_1",
                    "type": "reasoning",
                    "summary": [],
                    "encrypted_content": "enc_1",
                },
                {
                    "id": "fc_1",
                    "type": "function_call",
                    "status": "completed",
                    "arguments": '{"query":"Zep"}',
                    "name": "lookup",
                    "call_id": "call_1",
                },
            ],
            "resp_1",
        ),
        response(
            [
                {
                    "id": "rs_2",
                    "type": "reasoning",
                    "summary": [],
                    "encrypted_content": "enc_2",
                },
                {
                    "id": "fc_2",
                    "type": "function_call",
                    "status": "completed",
                    "arguments": '{"query":"memory"}',
                    "name": "lookup",
                    "call_id": "call_2",
                },
            ],
            "resp_2",
        ),
        response(
            [
                {
                    "id": "rs_3",
                    "type": "reasoning",
                    "summary": [],
                    "encrypted_content": "enc_3",
                },
                {
                    "id": "msg_1",
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "The lookups returned Zep and memory.",
                            "annotations": [],
                        }
                    ],
                },
            ],
            "resp_3",
        ),
    ]
    monkeypatch.setattr(OpenAICompletion, "_get_sync_client", lambda self: mock_client)
    tool_outputs: list[str] = []

    @tool("lookup")
    def lookup(query: str) -> str:
        """Look up a value."""
        tool_outputs.append(f"Found {query} on call {len(tool_outputs) + 1}")
        return tool_outputs[-1]

    agent = Agent(
        role="Research assistant",
        goal="Look up the requested value and report the result",
        backstory="You use the lookup tool when the task asks for a search.",
        tools=[lookup],
        llm=ReasoningReplayOpenAICompletion(),
        allow_delegation=False,
        verbose=False,
    )
    task = Task(
        description="Look up Zep and memory with separate tool calls, then report both results.",
        expected_output="The results from both lookup tool calls.",
        agent=agent,
    )
    crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=False)

    result = crew.kickoff()

    assert "The lookups returned Zep and memory." in str(result)
    assert tool_outputs == ["Found Zep on call 1", "Found memory on call 2"]
    assert mock_client.responses.create.call_count == 3
    first, second, third = [call.kwargs for call in mock_client.responses.create.call_args_list]
    for params in (first, second, third):
        assert params["model"] == "gpt-6-luna"
        assert params["reasoning"] == {"effort": "low"}
        assert params["store"] is False
        assert "reasoning.encrypted_content" in params["include"]
        assert "previous_response_id" not in params
        assert "temperature" not in params
        assert "top_p" not in params
        assert params["tools"][0]["type"] == "function"
        assert params["tools"][0]["name"] == "lookup"
        assert "function" not in params["tools"][0]
    reasoning_by_call = {
        "call_1": {
            "id": "rs_1",
            "type": "reasoning",
            "summary": [],
            "encrypted_content": "enc_1",
        },
        "call_2": {
            "id": "rs_2",
            "type": "reasoning",
            "summary": [],
            "encrypted_content": "enc_2",
        },
    }

    def assert_replayed(input_items: list[dict[str, Any]], call_id: str) -> int:
        call_index = next(
            index
            for index, item in enumerate(input_items)
            if item.get("type") == "function_call" and item.get("call_id") == call_id
        )
        assert input_items[call_index - 1] == reasoning_by_call[call_id]
        return call_index

    for params in (first, second, third):
        assert params["input"][0].get("type") != "reasoning"

    second_input = second["input"]
    call_1_index = assert_replayed(second_input, "call_1")
    assert second_input[call_1_index + 1] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "Found Zep on call 1",
    }
    assert [item["id"] for item in second_input if item.get("type") == "reasoning"] == ["rs_1"]

    third_input = third["input"]
    call_1_index = assert_replayed(third_input, "call_1")
    call_2_index = assert_replayed(third_input, "call_2")
    assert third_input[call_1_index + 1] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "Found Zep on call 1",
    }
    assert third_input[call_2_index + 1] == {
        "type": "function_call_output",
        "call_id": "call_2",
        "output": "Found memory on call 2",
    }
    replayed_ids = [item["id"] for item in third_input if item.get("type") == "reasoning"]
    assert replayed_ids == ["rs_1", "rs_2"]
    assert len(replayed_ids) == len(set(replayed_ids))


def test_reasoning_precedes_assistant_content_for_tool_call() -> None:
    llm = ReasoningReplayOpenAICompletion()
    reasoning_item = {
        "id": "rs_1",
        "type": "reasoning",
        "summary": [],
        "encrypted_content": "enc_1",
    }
    llm._reasoning_by_call["call_1"] = [reasoning_item]

    params = llm._prepare_responses_params(
        messages=[
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
    )

    assert params["input"] == [
        reasoning_item,
        {"role": "assistant", "content": "I will look up the value."},
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "lookup",
            "arguments": "{}",
        },
    ]
