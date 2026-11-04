from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from crewai import LLM, Agent, Crew, Process, Task
from crewai.llms.providers.openai.completion import OpenAICompletion
from crewai.tools import tool
from openai.types.responses import Response


def response(output: list[dict[str, Any]]) -> Response:
    return Response.model_validate(
        {
            "id": "resp_1",
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


def test_crew_kickoff_uses_stateless_responses_for_tools(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    mock_client = MagicMock()
    mock_client.responses.create.side_effect = [
        response(
            [
                {
                    "id": "fc_1",
                    "type": "function_call",
                    "status": "completed",
                    "arguments": '{"query":"Zep"}',
                    "name": "lookup",
                    "call_id": "call_1",
                }
            ]
        ),
        response(
            [
                {
                    "id": "msg_1",
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "The lookup returned Zep.",
                            "annotations": [],
                        }
                    ],
                }
            ]
        ),
    ]
    monkeypatch.setattr(OpenAICompletion, "_get_sync_client", lambda self: mock_client)
    tool_calls: list[str] = []

    @tool("lookup")
    def lookup(query: str) -> str:
        """Look up a value."""
        tool_calls.append(query)
        return f"Found {query}"

    agent = Agent(
        role="Research assistant",
        goal="Look up the requested value and report the result",
        backstory="You use the lookup tool when the task asks for a search.",
        tools=[lookup],
        llm=LLM(
            model="gpt-6-luna",
            api="responses",
            reasoning_effort="low",
            store=False,
            api_key="sk-test",
            base_url="http://127.0.0.1:9/v1",
        ),
        allow_delegation=False,
        verbose=False,
    )
    task = Task(
        description="Use the lookup tool with query 'Zep', then report its result.",
        expected_output="The result from the lookup tool.",
        agent=agent,
    )
    crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=False)

    result = crew.kickoff()

    assert "The lookup returned Zep." in str(result)
    assert tool_calls == ["Zep"]
    assert mock_client.responses.create.call_count == 2
    first, second = [call.kwargs for call in mock_client.responses.create.call_args_list]
    for params in (first, second):
        assert params["model"] == "gpt-6-luna"
        assert params["reasoning"] == {"effort": "low"}
        assert params["store"] is False
        assert "include" not in params
        assert "previous_response_id" not in params
        assert "temperature" not in params
        assert "top_p" not in params
    assert first["tools"][0]["type"] == "function"
    assert first["tools"][0]["name"] == "lookup"
    assert "function" not in first["tools"][0]
    call_output = next(
        item for item in second["input"] if item.get("type") == "function_call_output"
    )
    assert call_output == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "Found Zep",
    }
    assert not any(item.get("type") == "reasoning" for item in second["input"])
    assert not any(item.get("type") == "function_call" and "id" in item for item in second["input"])
