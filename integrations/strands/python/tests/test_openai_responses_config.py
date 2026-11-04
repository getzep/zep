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
    assert model.stateful is False
    request = model._format_request([])
    assert request["model"] == "gpt-6-luna"
    assert request["reasoning"]["effort"] == "low"
    assert "reasoning.encrypted_content" in request["include"]
    assert request["store"] is False


def test_a_new_conversation_drops_unreferenced_reasoning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = load_example(monkeypatch).build_model()
    model._reasoning_by_call_id = {"old-call": [{"type": "reasoning", "id": "old-reasoning"}]}

    model._format_request([])

    assert model._reasoning_by_call_id == {}


@pytest.mark.asyncio
async def test_stateless_tool_loop_replays_encrypted_reasoning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_example(monkeypatch)
    model = module.build_model()
    requests: list[tuple[str, dict[str, Any]]] = []
    responses_seen: list[str] = []
    events_seen: list[str] = []
    capture_response = model._capture_response
    capture_reasoning_event = model._capture_reasoning_event

    async def record_response(response: Any) -> None:
        responses_seen.append(type(response).__module__)
        await capture_response(response)

    def record_event(event: dict[str, Any], pending: list[dict[str, Any]]) -> None:
        events_seen.append(event.get("type", ""))
        capture_reasoning_event(event, pending)

    monkeypatch.setattr(model, "_capture_response", record_response)
    monkeypatch.setattr(model, "_capture_reasoning_event", record_event)

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
            "store": False,
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

        if index in (1, 2):
            call_number = index
            reasoning = {
                "type": "reasoning",
                "id": f"rs_{call_number}",
                "summary": [],
                "encrypted_content": f"enc_{call_number}",
                "status": "completed",
            }
            call = {
                "type": "function_call",
                "id": f"fc_{call_number}",
                "call_id": f"call_{call_number}",
                "name": "dummy_tool",
                "arguments": "{}",
                "status": "completed",
            }
            return [
                event("response.created", response=response(f"resp_{index}", "in_progress", [])),
                event("response.output_item.added", output_index=0, item=reasoning),
                event("response.output_item.done", output_index=0, item=reasoning),
                event(
                    "response.output_item.added",
                    output_index=1,
                    item={**call, "status": "in_progress"},
                ),
                event(
                    "response.function_call_arguments.delta",
                    item_id=f"fc_{call_number}",
                    output_index=1,
                    delta="{}",
                ),
                event(
                    "response.function_call_arguments.done",
                    item_id=f"fc_{call_number}",
                    output_index=1,
                    arguments="{}",
                ),
                event("response.output_item.done", output_index=1, item=call),
                event(
                    "response.completed",
                    response=response(f"resp_{index}", "completed", [reasoning, call]),
                ),
            ]

        text = "done" if index == 3 else "done again"
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
            event("response.output_item.done", output_index=0, item=message),
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
        stream_module = httpx2 if isinstance(transport, httpx2.AsyncHTTPTransport) else httpx

        class FakeAsyncByteStream(stream_module.AsyncByteStream):
            async def __aiter__(self) -> Any:
                yield stream.encode()

            async def aclose(self) -> None:
                return

        return response_type(
            200,
            headers={"content-type": "text/event-stream"},
            stream=FakeAsyncByteStream(),
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

    await agent.invoke_async("A new turn.")

    assert len(requests) == 4
    assert len(responses_seen) == 4
    assert any(event.startswith("response.output_item.done") for event in events_seen)
    for url, body in requests:
        assert url.endswith("/responses")
        assert body["reasoning"]["effort"] == "low"
        assert "reasoning.encrypted_content" in body["include"]
        assert body["store"] is False
        assert "previous_response_id" not in body
        assert "temperature" not in body

    first_turn_follow_up = requests[1][1]["input"]
    second_tool_follow_up = requests[2][1]["input"]
    second_turn = requests[3][1]["input"]
    assert model._reasoning_by_call_id.keys() == {"call_1", "call_2"}

    def assert_reasoning_before_call(
        input_items: list[dict[str, Any]], reasoning_id: str, encrypted_content: str, call_id: str
    ) -> None:
        call_index = next(
            index
            for index, item in enumerate(input_items)
            if item.get("type") == "function_call" and item.get("call_id") == call_id
        )
        assert call_index > 0
        assert input_items[call_index - 1] == {
            "type": "reasoning",
            "id": reasoning_id,
            "summary": [],
            "encrypted_content": encrypted_content,
        }

    assert_reasoning_before_call(first_turn_follow_up, "rs_1", "enc_1", "call_1")
    for input_items in (second_tool_follow_up, second_turn):
        assert_reasoning_before_call(input_items, "rs_1", "enc_1", "call_1")
        assert_reasoning_before_call(input_items, "rs_2", "enc_2", "call_2")
    assert second_turn[-1]["role"] == "user"
    assert second_turn[-1]["content"][0]["text"] == "A new turn."


def test_sse_parser_handles_an_event_split_across_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_example(monkeypatch)
    event = {
        "type": "response.output_item.done",
        "item": {
            "type": "reasoning",
            "id": "rs_1",
            "summary": [],
            "encrypted_content": "enc_1",
        },
    }
    payload = f"event: {event['type']}\ndata: {json.dumps(event)}\r\n\r\n".encode()
    split_at = payload.index(b"enc_1") + 2

    buffer, parsed = module._parse_sse_chunk(b"", payload[:split_at])
    assert parsed == []
    buffer, parsed = module._parse_sse_chunk(buffer, payload[split_at:])
    assert parsed == [event]


def test_reasoning_is_attached_only_to_the_first_parallel_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = load_example(monkeypatch).build_model()
    pending: list[dict[str, Any]] = []
    model._capture_reasoning_event(
        {
            "type": "response.output_item.done",
            "item": {
                "type": "reasoning",
                "id": "rs_1",
                "summary": [],
                "encrypted_content": "enc_1",
                "status": "completed",
            },
        },
        pending,
    )
    model._capture_reasoning_event(
        {
            "type": "response.output_item.done",
            "item": {"type": "function_call", "call_id": "call_1"},
        },
        pending,
    )
    model._capture_reasoning_event(
        {
            "type": "response.output_item.done",
            "item": {"type": "function_call", "call_id": "call_2"},
        },
        pending,
    )

    assert pending == []
    assert model._reasoning_by_call_id == {
        "call_1": [
            {
                "type": "reasoning",
                "id": "rs_1",
                "summary": [],
                "encrypted_content": "enc_1",
            }
        ]
    }
