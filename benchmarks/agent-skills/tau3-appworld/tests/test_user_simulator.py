from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from tau2.data_model.message import AssistantMessage, MultiToolMessage, SystemMessage, ToolMessage
from tau2.user.user_simulator_base import UserState

import user_simulator
from user_simulator import ResponsesUserSimulator


def item(**values):
    return SimpleNamespace(**values, model_dump=lambda **_: values)


def test_responses_tool_loop_preserves_reasoning(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(user_simulator, "OpenAI", lambda **_: client)
    reasoning = item(type="reasoning", id="rs_1", encrypted_content="opaque", summary=[])
    call = item(
        type="function_call", id="fc_1", call_id="call_1", name="lookup", arguments='{"key":"x"}'
    )
    text = item(type="message", role="assistant", content=[{"type": "output_text", "text": "Done"}])
    response = MagicMock(
        side_effect=[
            SimpleNamespace(
                status="completed", output=[reasoning, call], output_text="", usage=None
            ),
            SimpleNamespace(status="completed", output=[text], output_text="Done", usage=None),
            SimpleNamespace(status="completed", output=[text], output_text="Done", usage=None),
        ]
    )
    monkeypatch.setattr(user_simulator, "traced_responses_create", response)
    simulator = ResponsesUserSimulator(llm="gpt-6-luna", llm_args={"reasoning_effort": "low"})
    simulator.set_seed(42)
    state = UserState(
        system_messages=[SystemMessage(role="system", content="Act as a user")], messages=[]
    )
    first, state = simulator.generate_next_message(
        AssistantMessage(role="assistant", content="Hello"), state
    )
    assert first.tool_calls[0].requestor == "user"
    result, state = simulator.generate_next_message(
        MultiToolMessage(
            role="tool",
            tool_messages=[
                ToolMessage(role="tool", id="call_1", content="found", requestor="user")
            ],
        ),
        state,
    )
    assert result.content == "Done"
    simulator.generate_next_message(
        AssistantMessage(role="assistant", content="Anything else?"), state
    )
    sent = response.call_args_list[1].args[2]
    assert sent["model"] == "gpt-6-luna"
    assert sent["reasoning"] == {"effort": "low"}
    assert "temperature" not in sent and "seed" not in sent
    assert sent["include"] == ["reasoning.encrypted_content"]
    assert sent["input"][2:4] == [reasoning.model_dump(), call.model_dump()]
    assert sent["input"][4] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "found",
    }
    assert response.call_args_list[2].args[2]["input"][2:5] == sent["input"][2:5]
    client.chat.completions.create.assert_not_called()


def test_rejects_unsupported_simulator_settings():
    simulator = ResponsesUserSimulator(llm="gpt-6-luna", llm_args={"temperature": 0})
    state = UserState(system_messages=[], messages=[])
    with pytest.raises(ValueError, match="low reasoning"):
        simulator.generate_next_message(AssistantMessage(role="assistant", content="Hello"), state)


def test_simulator_uses_responses_endpoint(monkeypatch):
    import json

    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "resp_test",
                "object": "response",
                "created_at": 0,
                "model": "gpt-6-luna",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "id": "msg_test",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {"type": "output_text", "text": "A fictional reply", "annotations": []}
                        ],
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            },
        )

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setattr(
        user_simulator,
        "openai_http_client",
        lambda _: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    tool = SimpleNamespace(
        openai_schema={
            "type": "function",
            "function": {
                "name": "lookup",
                "description": "A fictional tool",
                "parameters": {"type": "object", "properties": {"key": {"type": "string"}}},
            },
        }
    )
    simulator = ResponsesUserSimulator(
        llm="gpt-6-luna", tools=[tool], llm_args={"reasoning_effort": "low"}
    )
    state = UserState(system_messages=[], messages=[])
    result, _ = simulator.generate_next_message(
        AssistantMessage(role="assistant", content="Hello"), state
    )
    assert result.content == "A fictional reply"
    assert result.usage == {"prompt_tokens": 10, "completion_tokens": 5}
    assert len(requests) == 1
    assert requests[0].url.path == "/v1/responses"
    payload = json.loads(requests[0].content)
    assert payload["reasoning"] == {"effort": "low"}
    assert payload["tools"][0]["type"] == "function"
    assert payload["tools"][0]["name"] == "lookup"
    assert "function" not in payload["tools"][0]
    assert "temperature" not in payload
