from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent
from openai import _base_client

httpx2 = getattr(_base_client, "httpx2", getattr(_base_client, "_httpx2", httpx))


def load_example(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("ZEP_API_KEY", "test-zep-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    path = Path(__file__).parents[1] / "examples" / "react_agent.py"
    spec = importlib.util.spec_from_file_location("langgraph_react_agent_example", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_example_uses_responses_and_replays_reasoning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_example(monkeypatch)
    requests: list[tuple[str, dict[str, Any]]] = []
    responses = [
        [
            {"type": "reasoning", "id": "rs_1", "summary": []},
            {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_1",
                "name": "dummy_tool",
                "arguments": "{}",
                "status": "completed",
            },
        ],
        [
            {"type": "reasoning", "id": "rs_2", "summary": []},
            {
                "type": "message",
                "id": "msg_2",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "annotations": [], "text": "done"}],
            },
        ],
    ]

    async def fake_request(transport: Any, request: Any) -> Any:
        body = json.loads(await request.aread())
        requests.append((str(request.url), body))
        index = len(requests) - 1
        payload = {
            "id": f"resp_{index + 1}",
            "object": "response",
            "created_at": 0,
            "status": "completed",
            "error": None,
            "incomplete_details": None,
            "instructions": None,
            "max_output_tokens": None,
            "model": "gpt-6-luna",
            "output": responses[index],
            "parallel_tool_calls": True,
            "previous_response_id": None,
            "reasoning": {"effort": "low", "summary": None},
            "store": True,
            "temperature": None,
            "text": {"format": {"type": "text"}},
            "tool_choice": "auto",
            "tools": [],
            "top_p": 1.0,
            "truncation": "disabled",
            "usage": {
                "input_tokens": 1,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens": 1,
                "output_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": 2,
            },
            "user": None,
            "metadata": None,
        }
        response_type = (
            httpx2.Response if isinstance(transport, httpx2.AsyncHTTPTransport) else httpx.Response
        )
        return response_type(200, json=payload, request=request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", fake_request)
    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "handle_async_request", fake_request)

    @tool
    def dummy_tool() -> str:
        """Return a canned tool result."""
        return "tool result"

    agent = create_react_agent(model=module.build_model(), tools=[dummy_tool])
    await agent.ainvoke({"messages": [HumanMessage(content="Use the tool.")]})

    assert len(requests) == 2
    for url, body in requests:
        assert url.endswith("/responses")
        assert body["reasoning"]["effort"] == "low"
        assert "temperature" not in body
    _, second_body = requests[1]
    assert second_body.get("previous_response_id") == "resp_1" or any(
        item.get("type") == "reasoning" and item.get("id") == "rs_1"
        for item in second_body.get("input", [])
    )
