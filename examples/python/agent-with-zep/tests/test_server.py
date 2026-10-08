"""FastAPI server tests with a fake Zep client and scripted models."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from agent_with_zep import server
from agent_with_zep.agent import build_agent
from agent_with_zep.config import Settings


def _model(*responses: ModelResponse) -> FunctionModel:
    scripted_responses = iter(responses)

    async def stream(_messages: list[ModelMessage], _info: AgentInfo):
        response = next(scripted_responses)
        for index, part in enumerate(response.parts):
            if isinstance(part, TextPart):
                yield part.content
            elif isinstance(part, ToolCallPart):
                yield {
                    index: DeltaToolCall(
                        name=part.tool_name,
                        json_args=part.args_as_json_str(),
                        tool_call_id=part.tool_call_id,
                    )
                }

    return FunctionModel(stream_function=stream)


def _client(monkeypatch, fake_zep, model: FunctionModel) -> TestClient:
    settings = Settings(
        zep_api_key="test",
        zep_base_url=None,
        graph_id="test-graph",
        agent_model="function",
        judge_model="function",
    )
    server._registries.clear()

    async def fake_load_orientation(zep, graph_id):
        return {"graph_id": graph_id, "nodes": []}

    def fake_build_agent(config, _model_name, deps, *, model_settings=None):
        return build_agent(config, model, deps, model_settings=model_settings)

    monkeypatch.setattr(server, "_settings_and_zep", lambda: (settings, fake_zep))
    monkeypatch.setattr(server, "load_orientation", fake_load_orientation)
    monkeypatch.setattr(server, "build_agent", fake_build_agent)
    return TestClient(server.app)


def _body(chat_id: str) -> dict:
    return {
        "trigger": "submit-message",
        "id": chat_id,
        "messages": [
            {
                "id": "user-1",
                "role": "user",
                "parts": [{"type": "text", "text": "List product details."}],
            }
        ],
    }


def _chunks(response) -> list[dict]:
    return [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: {")
    ]


def _tool_outputs(response) -> list[str]:
    return [
        chunk["output"] for chunk in _chunks(response) if chunk["type"] == "tool-output-available"
    ]


def test_chat_holds_text_until_retrieval(monkeypatch, fake_zep):
    model = _model(
        ModelResponse(parts=[TextPart("unsupported")]),
        ModelResponse(parts=[ToolCallPart("search_context", {"query": "Aster products"})]),
        ModelResponse(parts=[TextPart("grounded")]),
    )

    with _client(monkeypatch, fake_zep, model) as client:
        response = client.post("/api/chat?planning=false", json=_body("chat-retry"))

    assert response.status_code == 200
    deltas = "".join(chunk["delta"] for chunk in _chunks(response) if chunk["type"] == "text-delta")
    assert "grounded" in deltas, response.text
    assert "unsupported" not in deltas, response.text


def test_chat_keeps_text_next_to_tool_call(monkeypatch, fake_zep):
    model = _model(
        ModelResponse(
            parts=[
                TextPart("Let me search."),
                ToolCallPart("search_context", {"query": "Aster products"}),
            ]
        ),
        ModelResponse(parts=[TextPart("grounded")]),
    )

    with _client(monkeypatch, fake_zep, model) as client:
        response = client.post("/api/chat?planning=false", json=_body("chat-text-and-tool"))

    assert response.status_code == 200
    deltas = "".join(chunk["delta"] for chunk in _chunks(response) if chunk["type"] == "text-delta")
    assert "Let me search." in deltas
    assert "grounded" in deltas


def test_chat_keeps_handles_per_chat(monkeypatch, fake_zep):
    model = _model(
        ModelResponse(parts=[ToolCallPart("list_nodes", {"label": "Product"})]),
        ModelResponse(parts=[TextPart("Listed products.")]),
        ModelResponse(parts=[ToolCallPart("get_details", {"handle": "n1"})]),
        ModelResponse(parts=[TextPart("The handle resolves.")]),
        ModelResponse(parts=[ToolCallPart("get_details", {"handle": "n1"})]),
        ModelResponse(parts=[ToolCallPart("list_nodes", {"label": "Product"})]),
        ModelResponse(parts=[TextPart("The handle is not in this chat.")]),
    )

    with _client(monkeypatch, fake_zep, model) as client:
        first = client.post("/api/chat?planning=false", json=_body("chat-one"))
        second = client.post("/api/chat?planning=false", json=_body("chat-one"))
        third = client.post("/api/chat?planning=false", json=_body("chat-two"))

    assert first.status_code == second.status_code == third.status_code == 200
    assert any("n1" in output for output in _tool_outputs(first)), first.text
    assert any("details:" in output and "n1" in output for output in _tool_outputs(second))
    assert any("unknown handle n1" in output for output in _tool_outputs(third))
