"""FastAPI server tests with a fake Zep client and a scripted model."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from agent_with_zep_adk import server
from agent_with_zep_adk.config import Settings
from agent_with_zep_adk.planner import RETRIEVAL_TOOL_NAMES
from tests.test_agent_e2e import ScriptedLlm, _answer, _call


def _body(messages: list[dict]) -> dict:
    return {"trigger": "submit-message", "id": "request-1", "messages": messages}


def _client(monkeypatch, fake_zep, model: ScriptedLlm) -> TestClient:
    settings = Settings(
        zep_api_key="test",
        zep_base_url=None,
        graph_id="test-graph",
        agent_model="scripted",
        judge_model="scripted",
    )

    async def fake_load_orientation(zep, graph_id):
        return {"graph_id": graph_id, "nodes": []}

    monkeypatch.setattr(server, "_settings_and_zep", lambda: (settings, fake_zep))
    monkeypatch.setattr(server, "load_orientation", fake_load_orientation)
    monkeypatch.setattr(server, "resolve_model", lambda name, thinking: (model, None))
    return TestClient(server.app)


def _offered_tools(model: ScriptedLlm) -> set[str]:
    return {
        declaration.name
        for tool in model.requests[0].config.tools or []
        for declaration in tool.function_declarations or []
    }


def test_chat_rejects_request_without_user_message(monkeypatch, fake_zep):
    model = ScriptedLlm(model="scripted", responses=[])
    client = _client(monkeypatch, fake_zep, model)

    response = client.post(
        "/api/chat",
        json=_body(
            [
                {
                    "id": "assistant-1",
                    "role": "assistant",
                    "parts": [{"type": "text", "text": "Previous answer"}],
                }
            ]
        ),
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "The request must include a user message."}
    assert not model.requests


def test_chat_rebuilds_text_history_and_streams_response(monkeypatch, fake_zep):
    model = ScriptedLlm(
        model="scripted",
        responses=[
            _call("search_context", "search-1", {"query": "products"}),
            _answer("done"),
        ],
    )
    client = _client(monkeypatch, fake_zep, model)

    response = client.post(
        "/api/chat?planning=false",
        json=_body(
            [
                {
                    "id": "user-1",
                    "role": "user",
                    "parts": [
                        {"type": "text", "text": "Previous question"},
                        {"type": "tool-call", "text": "ignored user tool part"},
                    ],
                },
                {
                    "id": "assistant-1",
                    "role": "assistant",
                    "parts": [
                        {"type": "text", "text": "Previous answer"},
                        {"type": "tool-result", "text": "ignored assistant tool part"},
                    ],
                },
                {
                    "id": "user-2",
                    "role": "user",
                    "parts": [{"type": "text", "text": "Current question"}],
                },
            ]
        ),
    )

    assert response.status_code == 200
    assert response.headers["x-vercel-ai-ui-message-stream"] == "v1"
    history = [
        (content.role, [part.text for part in content.parts or [] if part.text])
        for content in model.requests[0].contents
    ]
    assert history == [
        ("user", ["Previous question"]),
        ("model", ["Previous answer"]),
        ("user", ["Current question"]),
    ]
    assert "ignored" not in str(model.requests[0].contents)
    assert _offered_tools(model) == RETRIEVAL_TOOL_NAMES
    chunks = [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: {")
    ]
    assert chunks[-1] == {"type": "finish"}
    assert response.text.rstrip().splitlines()[-1] == "data: [DONE]"
