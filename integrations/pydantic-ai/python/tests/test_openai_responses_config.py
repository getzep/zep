from __future__ import annotations

import dataclasses
import importlib.util
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIModelProfile, OpenAIResponsesModel
from pydantic_ai.profiles.openai import openai_model_profile


def load_example(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("ZEP_API_KEY", "test-zep-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    path = Path(__file__).parents[1] / "examples" / "basic_agent.py"
    spec = importlib.util.spec_from_file_location("pydantic_ai_example", path)
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
    outputs = [
        [
            {
                "type": "reasoning",
                "id": "rs_1",
                "summary": [{"type": "summary_text", "text": "thinking"}],
                "encrypted_content": "enc_1",
            },
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

    async def fake_request(
        transport: httpx.AsyncHTTPTransport, request: httpx.Request
    ) -> httpx.Response:
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
            "output": outputs[index],
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
        return httpx.Response(200, json=payload, request=request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", fake_request)

    async def dummy_tool() -> str:
        return "tool result"

    agent = Agent(module.build_model(), model_settings=module.MODEL_SETTINGS, tools=[dummy_tool])
    result = await agent.run("Use the tool.")

    assert result.output == "done"
    assert len(requests) == 2
    for url, body in requests:
        assert url.endswith("/responses")
        assert body["reasoning"]["effort"] == "low"
        assert "reasoning.encrypted_content" in body["include"]
        assert body["store"] is False
        assert "temperature" not in body
        assert "previous_response_id" not in body
    _, second_body = requests[1]
    second_input = second_body["input"]
    call_index = next(
        index
        for index, item in enumerate(second_input)
        if item.get("type") == "function_call" and item.get("call_id") == "call_1"
    )
    assert call_index > 0
    assert second_input[call_index - 1]["type"] == "reasoning"
    assert second_input[call_index - 1]["id"] == "rs_1"
    assert second_input[call_index - 1]["encrypted_content"] == "enc_1"


def test_luna_profile_keeps_default_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_example(monkeypatch)
    assert isinstance(module.build_model(), OpenAIResponsesModel)
    default_profile = dataclasses.asdict(
        OpenAIModelProfile.from_profile(openai_model_profile("gpt-6-luna"))
    )
    luna_profile = dataclasses.asdict(module.LUNA_PROFILE)
    overridden_fields = {
        "supports_thinking",
        "openai_supports_reasoning",
        "openai_supports_encrypted_reasoning_content",
    }
    for field in overridden_fields:
        default_profile.pop(field)
        luna_profile.pop(field)
    assert luna_profile == default_profile
