from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from eog.effort_preflight import EFFORT_LEVELS
from eog.errors import BudgetExceeded, NonRetryableProviderError
from eog.ledger import CostLedger
from eog.prices import PRICES, cost_usd, price_manifest
from eog.providers.deepseek_route import (
    DEEPSEEK_EFFORTS,
    DEEPSEEK_MATRIX_EFFORTS,
    DEEPSEEK_MODEL,
    DeepSeekRoute,
)
from eog.run import EXECUTOR_MATRIX_SETTINGS, _resolve_run_config, _route

TOOL = {
    "name": "calendar__find_event",
    "description": "Find an event.",
    "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
}


def _response(message: dict[str, Any], *, usage: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"choices": [{"message": message}], "usage": usage or {}}


def test_request_body_uses_chat_completions_and_reasoning_effort(tmp_path) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_response({"role": "assistant", "content": "ready"}))

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        route = DeepSeekRoute(
            api_key="proxy-token",
            client=client,
            endpoint="https://api.example.test",
            capture_dir=tmp_path,
        )
        route.complete(
            "system",
            [{"role": "user", "content": "Reply with ready."}],
            [TOOL],
            effort="none",
            task_id="test",
            attempt_limit=1,
        )

    request = requests[0]
    body = json.loads(request.content)
    assert str(request.url) == "https://api.example.test/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer proxy-token"
    assert body["model"] == DEEPSEEK_MODEL
    assert body["reasoning_effort"] == "none"
    assert body["tool_choice"] == "auto"
    assert body["tools"][0]["function"]["name"] == TOOL["name"]
    assert "temperature" not in body
    assert (tmp_path / "deepseek.json").is_file()


def test_request_omits_empty_tools() -> None:
    requests: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=_response({"role": "assistant", "content": "ready"}))

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        route = DeepSeekRoute(api_key="proxy-token", client=client, endpoint="https://api.example.test")
        route.complete("system", [{"role": "user", "content": "Reply ready."}], [], effort="none", task_id="test")

    assert "tools" not in requests[0]
    assert "tool_choice" not in requests[0]


@pytest.mark.parametrize(
    ("endpoint", "base_url", "message"),
    [
        ("http://example.test", "https://env.test", "endpoint must use https"),
        (None, "http://example.test", "DEEPSEEK_BASE_URL must use https"),
    ],
)
def test_rejects_non_https_endpoint_before_request(
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str | None,
    base_url: str,
    message: str,
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_response({"role": "assistant", "content": "ready"}))

    monkeypatch.setenv("DEEPSEEK_BASE_URL", base_url)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client, pytest.raises(ValueError, match=message):
        DeepSeekRoute(api_key="proxy-token", client=client, endpoint=endpoint)

    assert requests == []


def test_medium_matrix_effort_maps_to_the_servers_high_effort() -> None:
    requests: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=_response({"role": "assistant", "content": "ready"}))

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        route = DeepSeekRoute(api_key="proxy-token", client=client, endpoint="https://api.example.test")
        route.complete("system", [{"role": "user", "content": "Reply ready."}], [], effort="medium", task_id="test")

    assert requests[0]["reasoning_effort"] == "high"


def test_tool_calls_usage_and_reasoning_history_are_preserved(tmp_path) -> None:
    requests: list[dict[str, Any]] = []
    tool_call = {
        "id": "call-1",
        "type": "function",
        "function": {"name": TOOL["name"], "arguments": '{"query":"launch"}'},
    }
    response = _response(
        {
            "role": "assistant",
            "content": None,
            "reasoning_content": "Search the calendar.",
            "tool_calls": [tool_call],
        },
        usage={
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "prompt_tokens_details": {"cached_tokens": 20},
            "completion_tokens_details": {"reasoning_tokens": 13},
        },
    )

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=response)

    ledger = CostLedger(tmp_path / "costs.jsonl")
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        route = DeepSeekRoute(api_key="proxy-token", client=client, endpoint="https://api.example.test", ledger=ledger)
        first = route.complete(
            "system", [{"role": "user", "content": "Find launch."}], [TOOL], effort="high", task_id="t"
        )
        history = [
            {"role": "user", "content": "Find launch."},
            *first.history_items,
            {"role": "tool", "tool_call_id": "call-1", "content": "Found it."},
        ]
        route.complete("system", history, [TOOL], effort="high", task_id="t")

    assert [(call.name, call.arguments) for call in first.tool_calls] == [(TOOL["name"], {"query": "launch"})]
    assert first.usage.input == 100
    assert first.usage.cache_read == 20
    assert first.usage.output == 50
    assert first.usage.reasoning == 13
    assert requests[1]["messages"][2] == first.history_items[0]
    assert first.history_items[0]["reasoning_content"] == "Search the calendar."
    assert len(ledger.rows()) == 2
    assert all(row["usd"] is None for row in ledger.rows())
    assert cost_usd(DEEPSEEK_MODEL, input_tokens=100, output_tokens=50) is None
    assert PRICES[DEEPSEEK_MODEL] is None
    assert {"model": DEEPSEEK_MODEL, "priced": False} in price_manifest()


def test_unpriced_model_cannot_use_a_configured_cost_limit(tmp_path) -> None:
    ledger = CostLedger(tmp_path / "costs.jsonl", max_usd=1)

    with pytest.raises(BudgetExceeded, match="without a model price"):
        ledger.append_call({"model": DEEPSEEK_MODEL, "input_tokens": 1})

    assert ledger.rows()[0]["usd"] is None
    assert ledger.total_usd == 0


def test_401_is_a_failed_nonretryable_call() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(401, json={"error": {"message": "proxy auth required"}})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        route = DeepSeekRoute(api_key="proxy-token", client=client, endpoint="https://api.example.test")
        with pytest.raises(NonRetryableProviderError):
            route.complete("system", [], [], effort="none", task_id="test")

    assert len(requests) == 1


def test_explicit_effort_and_run_route_selection(monkeypatch, tmp_path) -> None:
    assert DEEPSEEK_EFFORTS == ("none", "low", "medium", "high", "xhigh", "max")
    assert DEEPSEEK_MATRIX_EFFORTS == ("low", "medium", "max")
    assert EFFORT_LEVELS[DEEPSEEK_MODEL] == ("low", "medium", "max")
    assert EFFORT_LEVELS["claude-haiku-5-5"] == ("low", "medium", "max")
    assert [(setting["model"], setting["effort"]) for setting in EXECUTOR_MATRIX_SETTINGS] == [
        ("gpt-6-luna", "low"),
        ("gpt-6-luna", "medium"),
        ("gpt-6-luna", "max"),
        ("claude-haiku-5-5", "low"),
        ("claude-haiku-5-5", "medium"),
        ("claude-haiku-5-5", "max"),
        (DEEPSEEK_MODEL, "low"),
        (DEEPSEEK_MODEL, "medium"),
        (DEEPSEEK_MODEL, "max"),
    ]
    assert _resolve_run_config("executor", "stirrup", DEEPSEEK_MODEL, "none", 1) == (DEEPSEEK_MODEL, "none", 1)
    assert _resolve_run_config("executor", "stirrup", DEEPSEEK_MODEL, "medium", 1) == (
        DEEPSEEK_MODEL,
        "medium",
        1,
    )
    with pytest.raises(SystemExit, match="Pass --effort"):
        _resolve_run_config("executor", "stirrup", DEEPSEEK_MODEL, None, 1)
    with pytest.raises(SystemExit, match="Unsupported DeepSeek effort"):
        _resolve_run_config("executor", "stirrup", DEEPSEEK_MODEL, "invalid", 1)

    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.example.test")
    monkeypatch.setenv("DEEPSEEK_BEARER_TOKEN", "proxy-token")
    route = _route(DEEPSEEK_MODEL, None, CostLedger(tmp_path / "costs.jsonl"))
    assert isinstance(route, DeepSeekRoute)
