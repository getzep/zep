from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

import eog.providers.openrouter_route as openrouter_route_module
from eog.errors import ContextOverflowError, NonRetryableProviderError
from eog.ledger import CostLedger
from eog.prices import cost_usd
from eog.providers.openrouter_route import OPENROUTER_MODEL_IDS, OpenRouterRoute

TOOL = {
    "name": "calendar__find_event",
    "description": "Find an event.",
    "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
}


def _success_response(message: dict[str, Any] | None = None, **fields: Any) -> dict[str, Any]:
    return {
        "id": "gen-1",
        "provider": "DeepInfra",
        "choices": [{"message": message or {"role": "assistant", "content": "done"}}],
        "usage": {},
        **fields,
    }


@pytest.mark.parametrize(("effort", "enabled"), (("on", True), ("off", False)))
def test_request_body_uses_nemotron_openrouter_settings(effort: str, enabled: bool, tmp_path) -> None:
    requests: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=_success_response())

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        route = OpenRouterRoute(
            "nemotron-3.5-lightning",
            api_key="test-key",
            capture_dir=tmp_path,
            client=client,
            endpoint="https://provider.test",
        )
        route.complete(
            "system",
            [{"role": "user", "content": "Find the event."}],
            [TOOL],
            effort=effort,
            task_id="test",
            attempt_limit=1,
        )

    body = requests[0]
    assert body["model"] == OPENROUTER_MODEL_IDS["nemotron-3.5-lightning"] == "nvidia/nemotron-3.5-lightning"
    assert body["reasoning"] == {"enabled": enabled}
    assert body["provider"] == {"order": ["deepinfra", "coreweave"], "allow_fallbacks": False}
    assert body["max_tokens"] == 32_768
    assert "temperature" not in body
    assert body["tools"] == [
        {
            "type": "function",
            "function": {
                "name": TOOL["name"],
                "description": TOOL["description"],
                "parameters": TOOL["inputSchema"],
            },
        }
    ]
    assert (tmp_path / "openrouter.json").is_file()


def test_unsupported_effort_raises_value_error() -> None:
    route = OpenRouterRoute("nemotron-3.5-lightning", api_key="test-key")

    with pytest.raises(ValueError, match="Unsupported Nemotron effort"):
        route.complete("system", [], [], effort="high", task_id="test")


def test_tool_calls_usage_provider_and_cost_are_recorded(tmp_path) -> None:
    raw_tool_calls = [
        {
            "id": "call-1",
            "type": "function",
            "function": {"name": TOOL["name"], "arguments": '{"query":"launch"}'},
        }
    ]
    result = _success_response(
        {
            "role": "assistant",
            "content": None,
            "reasoning": "Search the calendar.",
            "tool_calls": raw_tool_calls,
        },
        usage={
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "prompt_tokens_details": {"cached_tokens": 20, "cache_write_tokens": 5},
            "completion_tokens_details": {"reasoning_tokens": 13},
            "cost": 0.00002,
        },
    )
    ledger = CostLedger(tmp_path / "ledger.jsonl")

    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=result)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        route = OpenRouterRoute(
            "nemotron-3.5-lightning",
            api_key="test-key",
            client=client,
            ledger=ledger,
            endpoint="https://provider.test",
        )
        turn = route.complete("system", [], [TOOL], effort="on", task_id="test")

    assert [(call.name, call.arguments) for call in turn.tool_calls] == [(TOOL["name"], {"query": "launch"})]
    assert turn.reasoning_text == "Search the calendar."
    assert turn.served_provider == "DeepInfra"
    assert turn.usage.input == 100
    assert turn.usage.cache_read == 20
    assert turn.usage.cache_write == 5
    assert turn.usage.output == 50
    assert turn.usage.reasoning == 13
    row = ledger.rows()[0]
    assert row["served_provider"] == "DeepInfra"
    assert row["request_effort"] == "on"
    assert row["openrouter_cost_usd"] == 0.00002
    assert (
        row["usd"]
        == cost_usd(
            "nemotron-3.5-lightning",
            input_tokens=100,
            cache_read_tokens=20,
            cache_write_tokens=5,
            output_tokens=50,
        )
        == pytest.approx(0.0000164)
    )


def test_null_tool_calls_and_usage_counts_are_recorded(tmp_path) -> None:
    result = _success_response(
        {"role": "assistant", "content": "done", "tool_calls": None},
        usage={
            "prompt_tokens": None,
            "completion_tokens": None,
            "prompt_tokens_details": {"cached_tokens": None, "cache_write_tokens": None},
            "completion_tokens_details": {"reasoning_tokens": None},
        },
    )
    ledger = CostLedger(tmp_path / "null-usage.jsonl")

    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=result)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        route = OpenRouterRoute(
            "nemotron-3.5-lightning",
            api_key="test-key",
            client=client,
            ledger=ledger,
            endpoint="https://provider.test",
        )
        turn = route.complete("system", [], [], effort="off", task_id="test", attempt_limit=1)

    assert turn.text == "done"
    assert turn.tool_calls == ()
    assert turn.history_items[0]["tool_calls"] == []
    assert turn.usage.input == 0
    assert turn.usage.cache_read == 0
    assert turn.usage.cache_write == 0
    assert turn.usage.output == 0
    assert turn.usage.reasoning == 0
    rows = ledger.rows()
    assert len(rows) == 1
    assert rows[0]["route"] == "openrouter"


@pytest.mark.parametrize(
    ("reasoning_details", "expected"),
    (({"type": "reasoning.text", "text": "Need to search."}, True), (None, False)),
)
def test_reasoning_details_are_preserved_only_when_present(
    reasoning_details: dict[str, str] | None, expected: bool
) -> None:
    message: dict[str, Any] = {"role": "assistant", "content": "done"}
    if reasoning_details is not None:
        message["reasoning_details"] = [reasoning_details]

    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_success_response(message))

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        route = OpenRouterRoute(
            "nemotron-3.5-lightning",
            api_key="test-key",
            client=client,
            endpoint="https://provider.test",
        )
        turn = route.complete("system", [], [], effort="on", task_id="test", attempt_limit=1)

    history_item = turn.history_items[0]
    assert ("reasoning_details" in history_item) is expected
    if expected:
        assert history_item["reasoning_details"] == [reasoning_details]


def test_history_round_trip_preserves_tool_results_and_reasoning_details() -> None:
    requests: list[dict[str, Any]] = []
    tool_call = {
        "id": "call-1",
        "type": "function",
        "function": {"name": TOOL["name"], "arguments": '{"query":"launch"}'},
    }
    responses = [
        _success_response(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [tool_call],
                "reasoning_details": [{"type": "reasoning.text", "text": "Search the calendar."}],
            }
        ),
        _success_response({"role": "assistant", "content": "Found the event."}),
    ]

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=responses[len(requests) - 1])

    user = {"role": "user", "content": "Find the launch event."}
    tool_result = {"role": "tool", "tool_call_id": "call-1", "content": "Found it."}
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        route = OpenRouterRoute(
            "nemotron-3.5-lightning",
            api_key="test-key",
            client=client,
            endpoint="https://provider.test",
        )
        first = route.complete("system", [user], [TOOL], effort="on", task_id="test")
        route.complete("system", [user, *first.history_items, tool_result], [TOOL], effort="on", task_id="test")

    assert requests[1]["messages"] == [
        {"role": "system", "content": "system"},
        user,
        first.history_items[0],
        tool_result,
    ]


def test_http_200_rate_limit_error_retries_and_records_request_fields(monkeypatch, tmp_path) -> None:
    original_call_with_retry = openrouter_route_module.call_with_retry

    def no_sleep(fn, **kwargs):
        return original_call_with_retry(fn, sleep=lambda _seconds: None, **kwargs)

    monkeypatch.setattr(openrouter_route_module, "call_with_retry", no_sleep)
    requests = 0
    ledger = CostLedger(tmp_path / "retry.jsonl")

    def respond(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        if requests == 1:
            return httpx.Response(200, json={"error": {"code": 429, "message": "rate limited"}})
        return httpx.Response(200, json=_success_response())

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        route = OpenRouterRoute(
            "nemotron-3.5-lightning",
            api_key="test-key",
            client=client,
            ledger=ledger,
            endpoint="https://provider.test",
        )
        route.complete("system", [], [], effort="off", task_id="test", attempt_limit=2)

    assert requests == 2
    failure = ledger.rows()[0]
    assert failure["error_class"] == "http_retryable"
    assert failure["http_status"] == 429
    assert failure["request_effort"] == "off"
    assert failure["request_thinking_type"] is None
    assert failure["request_reasoning_enabled"] is False


@pytest.mark.parametrize(
    ("status_code", "error_body"),
    (
        (408, {"error": {"code": 408, "message": "request timeout"}}),
        (200, {"error": {"code": 408, "message": "request timeout"}}),
    ),
    ids=("http-408", "openrouter-error-code-408"),
)
def test_http_408_retries_and_records_failure(
    monkeypatch, tmp_path, status_code: int, error_body: dict[str, Any]
) -> None:
    original_call_with_retry = openrouter_route_module.call_with_retry

    def no_sleep(fn, **kwargs):
        return original_call_with_retry(fn, sleep=lambda _seconds: None, **kwargs)

    monkeypatch.setattr(openrouter_route_module, "call_with_retry", no_sleep)
    requests = 0
    ledger = CostLedger(tmp_path / f"retry-{status_code}.jsonl")

    def respond(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        if requests == 1:
            return httpx.Response(status_code, json=error_body)
        return httpx.Response(200, json=_success_response())

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        route = OpenRouterRoute(
            "nemotron-3.5-lightning",
            api_key="test-key",
            client=client,
            ledger=ledger,
            endpoint="https://provider.test",
        )
        turn = route.complete("system", [], [], effort="off", task_id="test", attempt_limit=2)

    assert turn.text == "done"
    assert requests == 2
    failure = ledger.rows()[0]
    assert failure["error_class"] == "http_retryable"
    assert failure["http_status"] == 408


def test_http_200_bad_request_error_is_non_retryable(tmp_path) -> None:
    ledger = CostLedger(tmp_path / "bad-request.jsonl")

    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": {"code": 400, "message": "invalid request"}})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        route = OpenRouterRoute(
            "nemotron-3.5-lightning",
            api_key="test-key",
            client=client,
            ledger=ledger,
            endpoint="https://provider.test",
        )
        with pytest.raises(NonRetryableProviderError):
            route.complete("system", [], [], effort="on", task_id="test", attempt_limit=2)

    assert ledger.rows()[0]["error_class"] == "http_nonretryable"
    assert ledger.rows()[0]["http_status"] == 400


def test_deepinfra_context_overflow_raises_context_overflow_error(tmp_path) -> None:
    message = (
        "Upstream error from DeepInfra: This model's maximum context length is 262144 tokens. "
        "However, you requested 32768 output tokens and your prompt contains at least 229377 input tokens."
    )
    ledger = CostLedger(tmp_path / "context-overflow.jsonl")

    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": message}})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        route = OpenRouterRoute(
            "nemotron-3.5-lightning",
            api_key="test-key",
            client=client,
            ledger=ledger,
            endpoint="https://provider.test",
        )
        with pytest.raises(ContextOverflowError, match="context length for openrouter"):
            route.complete("system", [], [], effort="on", task_id="test", attempt_limit=3)

    assert ledger.rows()[0]["error_class"] == "context_overflow"
    assert ledger.rows()[0]["http_status"] == 400


def test_tool_choice_forces_the_named_function(tmp_path) -> None:
    requests: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=_success_response())

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        route = OpenRouterRoute(
            "nemotron-3.5-lightning",
            api_key="test-key",
            capture_dir=tmp_path,
            client=client,
            endpoint="https://provider.test",
        )
        route.complete(
            "system",
            [{"role": "user", "content": "Find the event."}],
            [TOOL],
            effort="on",
            task_id="test",
            attempt_limit=1,
            tool_choice="read_skill",
        )
        route.complete(
            "system",
            [{"role": "user", "content": "Find the event."}],
            [TOOL],
            effort="on",
            task_id="test",
            attempt_limit=1,
        )

    assert requests[0]["tool_choice"] == {"type": "function", "function": {"name": "read_skill"}}
    assert "tool_choice" not in requests[1]


def test_openrouter_providers_follow_the_environment(monkeypatch) -> None:
    from eog.providers.openrouter_route import DEFAULT_OPENROUTER_PROVIDERS, openrouter_providers

    monkeypatch.delenv("EOG_OPENROUTER_PROVIDERS", raising=False)
    assert openrouter_providers() == list(DEFAULT_OPENROUTER_PROVIDERS)
    monkeypatch.setenv("EOG_OPENROUTER_PROVIDERS", "deepinfra")
    assert openrouter_providers() == ["deepinfra"]
    monkeypatch.setenv("EOG_OPENROUTER_PROVIDERS", " , ")
    with pytest.raises(ValueError):
        openrouter_providers()
