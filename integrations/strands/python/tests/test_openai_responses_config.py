from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from openai import _base_client
from strands import Agent, tool
from strands.models.openai_responses import OpenAIResponsesModel

httpx2 = getattr(_base_client, "httpx2", getattr(_base_client, "_httpx2", httpx))


def load_example(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("ZEP_API_KEY", "test-zep-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    path = Path(__file__).parents[1] / "examples" / "basic_agent.py"
    spec = importlib.util.spec_from_file_location("strands_example", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_example_formats_low_reasoning_responses_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_example(monkeypatch)
    model = module.build_model()

    assert isinstance(model, OpenAIResponsesModel)
    assert model.stateful is True
    request = model._format_request([])
    assert request["model"] == "gpt-6-luna"
    assert request["reasoning"]["effort"] == "low"


@pytest.mark.asyncio
async def test_stateful_tool_loop_replays_response_ids_without_duplicate_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_example(monkeypatch)
    model = module.build_model()
    requests: list[tuple[str, dict[str, Any]]] = []

    def response(response_id: str, status: str, output: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "id": response_id,
            "object": "response",
            "created_at": 0,
            "status": status,
            "error": None,
            "incomplete_details": None,
            "instructions": None,
            "max_output_tokens": None,
            "model": "gpt-6-luna",
            "output": output,
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

    def events_for(index: int) -> list[dict[str, Any]]:
        sequence = 0

        def event(event_type: str, **fields: Any) -> dict[str, Any]:
            nonlocal sequence
            item = {"type": event_type, "sequence_number": sequence, **fields}
            sequence += 1
            return item

        if index == 1:
            reasoning = {"type": "reasoning", "id": "rs_1", "summary": []}
            call = {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_1",
                "name": "dummy_tool",
                "arguments": "{}",
                "status": "completed",
            }
            return [
                event("response.created", response=response("resp_1", "in_progress", [])),
                event("response.output_item.added", output_index=0, item=reasoning),
                event(
                    "response.output_item.added",
                    output_index=1,
                    item={**call, "status": "in_progress"},
                ),
                event(
                    "response.function_call_arguments.delta",
                    item_id="fc_1",
                    output_index=1,
                    delta="{}",
                ),
                event(
                    "response.function_call_arguments.done",
                    item_id="fc_1",
                    output_index=1,
                    arguments="{}",
                ),
                event("response.output_item.done", output_index=1, item=call),
                event(
                    "response.completed",
                    response=response("resp_1", "completed", [reasoning, call]),
                ),
            ]

        text = "done" if index == 2 else "new answer"
        message = {
            "type": "message",
            "id": f"msg_{index}",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "annotations": [], "text": text}],
        }
        return [
            event("response.created", response=response(f"resp_{index}", "in_progress", [])),
            event(
                "response.output_text.delta",
                item_id=f"msg_{index}",
                output_index=0,
                content_index=0,
                delta=text,
            ),
            event(
                "response.completed",
                response=response(f"resp_{index}", "completed", [message]),
            ),
        ]

    async def fake_request(transport: Any, request: Any) -> Any:
        body = json.loads(await request.aread())
        requests.append((str(request.url), body))
        index = len(requests)
        stream = "".join(
            f"event: {item['type']}\ndata: {json.dumps(item)}\n\n" for item in events_for(index)
        )
        response_type = (
            httpx2.Response if isinstance(transport, httpx2.AsyncHTTPTransport) else httpx.Response
        )
        return response_type(
            200,
            headers={"content-type": "text/event-stream"},
            content=stream.encode(),
            request=request,
        )

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", fake_request)
    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "handle_async_request", fake_request)

    @tool
    def dummy_tool() -> str:
        """Return a canned tool result."""
        return "tool result"

    agent = Agent(model=model, tools=[dummy_tool], callback_handler=None)
    await agent.invoke_async("Use the tool.")

    assert len(requests) == 2
    for url, body in requests:
        assert url.endswith("/responses")
        assert body["reasoning"]["effort"] == "low"
        assert body["store"] is True
        assert "temperature" not in body

    second_url, second_body = requests[1]
    assert second_url.endswith("/responses")
    assert second_body["previous_response_id"] == "resp_1"
    assert any(
        item.get("type") == "function_call_output" and item.get("call_id") == "call_1"
        for item in second_body["input"]
    )
    assert not any(item.get("role") == "user" for item in second_body["input"]), second_body[
        "input"
    ]
    assert not any(item.get("type") == "function_call" for item in second_body["input"])

    await agent.invoke_async("A new turn.")

    assert len(requests) == 3
    third_url, third_body = requests[2]
    assert third_url.endswith("/responses")
    assert third_body["previous_response_id"] == "resp_2"
    assert len(third_body["input"]) == 1
    assert third_body["input"][0]["role"] == "user"
    assert third_body["input"][0]["content"][0]["text"] == "A new turn."
