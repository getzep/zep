"""Mocked tests for the Responses API endpoint of the proxy. No network calls."""

import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openai.types.responses import ResponseFunctionToolCall, ResponseReasoningItem
from openai.types.responses.response_reasoning_item import Summary

os.environ.setdefault("PROXY_API_KEY", "test-proxy-key")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("ZEP_API_KEY", "test-zep-key")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import proxy_server  # noqa: E402

HEADERS = {"Authorization": f"Bearer {os.environ['PROXY_API_KEY']}"}
EXTRA_BODY = {"user_uuid": "user-uuid", "conversation_id": "conversation-1"}


class FakeEvent:
    def __init__(self, type, **fields):
        self.type = type
        self.fields = fields
        for name, value in fields.items():
            setattr(self, name, value)

    def model_dump(self, mode="python"):
        data = {"type": self.type}
        for name, value in self.fields.items():
            data[name] = value.model_dump(mode=mode) if hasattr(value, "model_dump") else value
        return data


class FakeResponse:
    def __init__(self, output):
        self.output = output

    def model_dump(self, mode="python"):
        return {"output": [item.model_dump(mode=mode) for item in self.output]}


class FakeResponses:
    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []

    async def create(self, **request):
        self.requests.append(request)
        events = self.turns.pop(0)

        async def stream():
            for event in events:
                yield event

        return stream()


@pytest.fixture
def fake_openai(monkeypatch):
    async def no_op(*args, **kwargs):
        return None

    async def zep_context(user_uuid, conversation_id, user_message):
        return "The user likes hiking."

    monkeypatch.setattr(proxy_server, "ensure_zep_user_exists", no_op)
    monkeypatch.setattr(proxy_server, "persist_message_to_zep", no_op)
    monkeypatch.setattr(proxy_server, "add_message_and_get_context", zep_context)
    proxy_server.REASONING_ITEMS_BY_CALL_ID.clear()

    def install(turns):
        responses = FakeResponses(turns)
        monkeypatch.setattr(proxy_server.openai_client, "responses", responses)
        return responses

    return install


def sse_events(text):
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: {")]


def test_responses_request_uses_gpt_6_luna_with_low_reasoning(fake_openai):
    responses = fake_openai(
        [
            [
                FakeEvent("response.output_text.delta", delta="Hello"),
                FakeEvent("response.completed", response=FakeResponse([])),
            ]
        ]
    )
    body = {
        "model": "",
        "instructions": "You are Eva.",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "Hi"}]}],
        "tools": [{"type": "function", "name": "end_call", "parameters": {"type": "object"}}],
        "reasoning": {"summary": "auto"},
        "temperature": 0.7,
        "stream": True,
        "elevenlabs_extra_body": EXTRA_BODY,
    }

    response = TestClient(proxy_server.app).post("/v1/responses", json=body, headers=HEADERS)

    assert response.status_code == 200
    assert response.text.endswith("data: [DONE]\n\n")
    assert [event["type"] for event in sse_events(response.text)] == [
        "response.output_text.delta",
        "response.completed",
    ]
    request = responses.requests[0]
    assert request["model"] == "gpt-6-luna"
    assert request["reasoning"] == {"summary": "auto", "effort": "low"}
    assert request["stream"] is True
    assert request["tools"] == body["tools"]
    assert "temperature" not in request
    assert request["instructions"].startswith("You are Eva.")
    assert "The user likes hiking." in request["instructions"]


def test_tool_loop_sends_the_reasoning_items_back(fake_openai):
    reasoning = ResponseReasoningItem(
        id="rs_1", type="reasoning", summary=[Summary(type="summary_text", text="Plan")]
    )
    function_call = ResponseFunctionToolCall(
        id="fc_1", call_id="call_1", type="function_call", name="end_call", arguments="{}"
    )
    responses = fake_openai(
        [
            [FakeEvent("response.completed", response=FakeResponse([reasoning, function_call]))],
            [FakeEvent("response.completed", response=FakeResponse([]))],
        ]
    )
    client = TestClient(proxy_server.app)
    user_message = {"role": "user", "content": "Bye"}
    client.post(
        "/v1/responses",
        json={"input": [user_message], "elevenlabs_extra_body": EXTRA_BODY},
        headers=HEADERS,
    )

    call_item = function_call.model_dump(mode="json", exclude_none=True)
    output_item = {"type": "function_call_output", "call_id": "call_1", "output": "ok"}
    client.post(
        "/v1/responses",
        json={"input": [user_message, call_item, output_item], "elevenlabs_extra_body": EXTRA_BODY},
        headers=HEADERS,
    )

    second_input = responses.requests[1]["input"]
    assert [item.get("type") for item in second_input] == [
        None,
        "reasoning",
        "function_call",
        "function_call_output",
    ]
    assert second_input[1]["id"] == "rs_1"
    assert responses.requests[1]["reasoning"]["effort"] == "low"


def test_reasoning_items_are_not_sent_twice(fake_openai):
    reasoning = {"id": "rs_1", "type": "reasoning", "summary": []}
    proxy_server.REASONING_ITEMS_BY_CALL_ID["call_1"] = [reasoning]
    call_item = {"type": "function_call", "call_id": "call_1", "name": "end_call", "arguments": "{}"}

    assert proxy_server.restore_reasoning_items([reasoning, call_item]) == [reasoning, call_item]


def test_requests_without_the_proxy_key_are_rejected(fake_openai):
    fake_openai([])
    response = TestClient(proxy_server.app).post(
        "/v1/responses", json={"input": "Hi", "elevenlabs_extra_body": EXTRA_BODY}
    )
    assert response.status_code == 401
