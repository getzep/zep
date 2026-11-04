from __future__ import annotations

import io
import json
import threading
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

import eog.providers.anthropic_route as anthropic_route_module
import eog.providers.openai_route as openai_route_module
import eog.run as run_module
from eog.agent import MODEL_CONTEXT
from eog.claude_sdk_agent import MODEL, AnthropicCaptureProxy
from eog.effort_preflight import EFFORT_LEVELS
from eog.errors import NonRetryableProviderError, RetryExhaustedError
from eog.ledger import CostLedger
from eog.prices import PRICES
from eog.providers.anthropic_route import AnthropicRoute
from eog.providers.deepseek_route import DeepSeekRoute
from eog.providers.openai_route import OpenAIRoute
from eog.providers.zai_route import ZAIRoute
from eog.retry import call_with_retry


def _raw_sdk_resource(resource: Any) -> SimpleNamespace:
    def create(**body: Any) -> SimpleNamespace:
        parsed = resource.create(**body)
        return SimpleNamespace(status_code=200, parse=lambda: parsed)

    return SimpleNamespace(with_raw_response=SimpleNamespace(create=create))


def _status_raw_sdk_resource(resource: Any, status: int) -> SimpleNamespace:
    def create(**body: Any) -> SimpleNamespace:
        if status >= 400:
            request = httpx.Request("POST", "https://provider.test")
            response = httpx.Response(status, json={"error": "bad request"}, request=request)
            raise httpx.HTTPStatusError("bad request", request=request, response=response)
        parsed = resource.create(**body)
        return SimpleNamespace(status_code=status, parse=lambda: parsed)

    return SimpleNamespace(with_raw_response=SimpleNamespace(create=create))


@pytest.mark.parametrize("status", [200, 400])
def test_anthropic_http_status_is_recorded(status: int, tmp_path) -> None:
    parsed = SimpleNamespace(
        content=[],
        usage=SimpleNamespace(model_dump=lambda: {"input_tokens": 1, "output_tokens": 1}),
    )
    ledger = CostLedger(tmp_path / "anthropic-status.jsonl")
    context = {"model_call_http_statuses": [], "attempt_failures": [], "model_call_latencies": []}
    route = AnthropicRoute(
        "claude-opus-5-5",
        client=SimpleNamespace(messages=_status_raw_sdk_resource(SimpleNamespace(create=lambda **_: parsed), status)),
        ledger=ledger,
    )

    if status == 200:
        route.complete(
            "system", [{"role": "user", "content": "test"}], [], effort="max", task_id="test", context=context
        )
    else:
        with pytest.raises(NonRetryableProviderError):
            route.complete(
                "system", [{"role": "user", "content": "test"}], [], effort="max", task_id="test", context=context
            )

    assert ledger.rows()[0]["http_status"] == status
    assert context["model_call_http_statuses"] == [status]


@pytest.mark.parametrize("status", [200, 400])
def test_openai_http_status_is_recorded(status: int, tmp_path) -> None:
    parsed = SimpleNamespace(
        output=[],
        usage=SimpleNamespace(
            model_dump=lambda: {
                "input_tokens": 1,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens": 1,
                "output_tokens_details": {"reasoning_tokens": 0},
            }
        ),
    )
    ledger = CostLedger(tmp_path / "openai-status.jsonl")
    context = {"model_call_http_statuses": [], "attempt_failures": [], "model_call_latencies": []}
    route = OpenAIRoute(
        client=SimpleNamespace(responses=_status_raw_sdk_resource(SimpleNamespace(create=lambda **_: parsed), status)),
        ledger=ledger,
    )

    if status == 200:
        route.complete(
            "system", [{"role": "user", "content": "test"}], [], effort="low", task_id="test", context=context
        )
    else:
        with pytest.raises(NonRetryableProviderError):
            route.complete(
                "system", [{"role": "user", "content": "test"}], [], effort="low", task_id="test", context=context
            )

    assert ledger.rows()[0]["http_status"] == status
    assert context["model_call_http_statuses"] == [status]


@pytest.mark.parametrize("status", [200, 400])
def test_deepseek_http_status_is_recorded(status: int, tmp_path) -> None:
    ledger = CostLedger(tmp_path / "deepseek-status.jsonl")
    context = {"model_call_http_statuses": [], "attempt_failures": [], "model_call_latencies": []}
    request = httpx.Request("POST", "https://provider.test")
    body = (
        {"choices": [{"message": {"role": "assistant", "content": "ready"}}], "usage": {}}
        if status == 200
        else {"error": "bad request"}
    )
    response = httpx.Response(status, json=body, request=request)
    client = httpx.Client(transport=httpx.MockTransport(lambda _: response))
    route = DeepSeekRoute(api_key="test-key", client=client, endpoint="https://provider.test", ledger=ledger)

    if status == 200:
        route.complete(
            "system", [{"role": "user", "content": "test"}], [], effort="low", task_id="test", context=context
        )
    else:
        with pytest.raises(NonRetryableProviderError):
            route.complete(
                "system", [{"role": "user", "content": "test"}], [], effort="low", task_id="test", context=context
            )

    assert ledger.rows()[0]["http_status"] == status
    assert context["model_call_http_statuses"] == [status]
    client.close()


def test_anthropic_ledger_records_effort_thinking_and_reasoning_tokens(tmp_path) -> None:
    usage = {
        "input_tokens": 12,
        "output_tokens": 8,
        "cache_read_input_tokens": 2,
        "cache_creation_input_tokens": 1,
        "output_tokens_details": {"thinking_tokens": 5},
    }

    class Messages:
        def __init__(self) -> None:
            self.body: dict[str, Any] = {}

        def create(self, **body: Any) -> SimpleNamespace:
            self.body = body
            return SimpleNamespace(content=[], usage=SimpleNamespace(model_dump=lambda: usage))

    messages = Messages()
    ledger = CostLedger(tmp_path / "anthropic.jsonl")
    route = AnthropicRoute(
        "claude-opus-5-5", client=SimpleNamespace(messages=_raw_sdk_resource(messages)), ledger=ledger
    )
    route.complete("system", [{"role": "user", "content": "test"}], [], effort="max", task_id="test", attempt_limit=1)

    row = ledger.rows()[0]
    assert row["request_effort"] == messages.body["output_config"]["effort"] == "max"
    assert row["request_thinking_type"] == messages.body["thinking"]["type"] == "adaptive"
    assert row["reasoning_tokens"] == 5


def test_anthropic_ledger_keeps_missing_reasoning_tokens_as_none(tmp_path) -> None:
    usage = {"input_tokens": 12, "output_tokens": 8}

    class Messages:
        def create(self, **_body: Any) -> SimpleNamespace:
            return SimpleNamespace(content=[], usage=SimpleNamespace(model_dump=lambda: usage))

    ledger = CostLedger(tmp_path / "anthropic.jsonl")
    route = AnthropicRoute(
        "claude-opus-5-5", client=SimpleNamespace(messages=_raw_sdk_resource(Messages())), ledger=ledger
    )
    route.complete("system", [{"role": "user", "content": "test"}], [], effort="max", task_id="test", attempt_limit=1)

    assert ledger.rows()[0]["reasoning_tokens"] is None


def test_openai_ledger_records_effort_and_reasoning_tokens(tmp_path) -> None:
    usage = {
        "input_tokens": 12,
        "output_tokens": 8,
        "input_tokens_details": {"cached_tokens": 2},
        "output_tokens_details": {"reasoning_tokens": 6},
    }

    class Responses:
        def __init__(self) -> None:
            self.body: dict[str, Any] = {}

        def create(self, **body: Any) -> SimpleNamespace:
            self.body = body
            return SimpleNamespace(output=[], usage=SimpleNamespace(model_dump=lambda: usage))

    responses = Responses()
    ledger = CostLedger(tmp_path / "openai.jsonl")
    route = OpenAIRoute(client=SimpleNamespace(responses=_raw_sdk_resource(responses)), ledger=ledger)
    route.complete("system", [{"role": "user", "content": "test"}], [], effort="low", task_id="test", attempt_limit=1)

    row = ledger.rows()[0]
    assert row["request_effort"] == responses.body["reasoning"]["effort"] == "low"
    assert row["request_thinking_type"] is None
    assert row["reasoning_tokens"] == 6


def test_openai_ledger_keeps_missing_reasoning_tokens_as_none(tmp_path) -> None:
    usage = {"input_tokens": 12, "output_tokens": 8}

    class Responses:
        def create(self, **_body: Any) -> SimpleNamespace:
            return SimpleNamespace(output=[], usage=SimpleNamespace(model_dump=lambda: usage))

    ledger = CostLedger(tmp_path / "openai.jsonl")
    route = OpenAIRoute(client=SimpleNamespace(responses=_raw_sdk_resource(Responses())), ledger=ledger)
    turn = route.complete(
        "system",
        [{"role": "user", "content": "test"}],
        [],
        effort="low",
        task_id="test",
        attempt_limit=1,
    )

    assert turn.usage.reasoning is None
    assert ledger.rows()[0]["reasoning_tokens"] is None


@pytest.mark.parametrize(
    ("route_name", "effort", "thinking_type"),
    [
        ("anthropic", "max", "adaptive"),
        ("openai", "low", None),
        ("zai", "high", "enabled"),
    ],
)
def test_provider_route_failures_record_request_fields(route_name, effort, thinking_type, tmp_path) -> None:
    request = httpx.Request("POST", "https://provider.test")
    response = httpx.Response(400, json={"error": {"message": "bad request"}}, request=request)
    error = httpx.HTTPStatusError("bad request", request=request, response=response)

    def fail(**_body: Any) -> SimpleNamespace:
        raise error

    ledger = CostLedger(tmp_path / f"{route_name}.jsonl")
    if route_name == "anthropic":
        route = AnthropicRoute(
            "claude-opus-5-5",
            client=SimpleNamespace(messages=_raw_sdk_resource(SimpleNamespace(create=fail))),
            ledger=ledger,
        )
    elif route_name == "openai":
        route = OpenAIRoute(
            client=SimpleNamespace(responses=_raw_sdk_resource(SimpleNamespace(create=fail))), ledger=ledger
        )
    else:
        transport = httpx.MockTransport(lambda _request: response)
        with httpx.Client(transport=transport) as client:
            route = ZAIRoute("glm-5.2", api_key="test-key", client=client, ledger=ledger)
            with pytest.raises(NonRetryableProviderError):
                route.complete(
                    "system",
                    [{"role": "user", "content": "test"}],
                    [],
                    effort=effort,
                    task_id="test",
                    attempt_limit=1,
                )
        failure = ledger.rows()[0]
        assert failure["request_effort"] == effort
        assert failure["request_thinking_type"] == thinking_type
        return

    with pytest.raises(NonRetryableProviderError):
        route.complete(
            "system",
            [{"role": "user", "content": "test"}],
            [],
            effort=effort,
            task_id="test",
            attempt_limit=1,
        )
    failure = ledger.rows()[0]
    assert failure["request_effort"] == effort
    assert failure["request_thinking_type"] == thinking_type


def test_zai_read_timeout_records_failure_with_runner_context(tmp_path) -> None:
    calls = 0

    def timeout(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise TimeoutError("The read operation timed out")

    context = {
        "cell": "reproduce",
        "arm": "none",
        "task": "timeout-repro",
        "trial": 1,
        "abort_requested": threading.Event(),
        "attempt_failures": [],
        "model_call_latencies": [],
    }
    ledger = CostLedger(tmp_path / "zai-timeout.jsonl")
    with httpx.Client(transport=httpx.MockTransport(timeout), timeout=600) as client:
        route = ZAIRoute("glm-5.3-flash", api_key="test-key", client=client, ledger=ledger)
        with pytest.raises(RetryExhaustedError, match="Provider retry limit reached"):
            route.complete(
                "system",
                [],
                [],
                effort="max",
                task_id="timeout-repro",
                context=context,
                attempt_limit=1,
            )

    assert calls == 1
    assert len(context["attempt_failures"]) == 1
    failure = context["attempt_failures"][0]
    assert failure["error_class"] == "timeout"
    assert failure["billing_status"] == "unbilled_timeout"
    assert "abort_requested" not in failure
    assert ledger.rows() == [failure]


def test_attempt_failure_snapshots_mutable_context(tmp_path) -> None:
    calls = 0
    context = {
        "tool_call_latencies": [0.1],
        "claude_task_tools_used": ["search"],
        "task_context": {"steps": ["before failure"]},
        "attempt_failures": [],
        "model_call_latencies": [],
    }
    ledger = CostLedger(tmp_path / "retry-context.jsonl")

    def request() -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("The read operation timed out")
        return httpx.Response(200, json={"ok": True})

    result = call_with_retry(
        request,
        route="glm",
        ledger=ledger,
        context=context,
        max_attempts=2,
        base_delay=0,
        max_delay=0,
        sleep=lambda _: None,
    )

    context["tool_call_latencies"].append(0.2)
    context["claude_task_tools_used"].append("create")
    context["task_context"]["steps"].append("after failure")
    failure = context["attempt_failures"][0]

    assert result.status_code == 200
    assert failure["tool_call_latencies"] == [0.1]
    assert failure["claude_task_tools_used"] == ["search"]
    assert failure["task_context"] == {"steps": ["before failure"]}
    assert failure["tool_call_latencies"] is not context["tool_call_latencies"]
    assert failure["task_context"] is not context["task_context"]


def test_zai_ledger_records_effort_thinking_and_reasoning_tokens(tmp_path) -> None:
    request_body: dict[str, Any] = {}

    def respond(request: httpx.Request) -> httpx.Response:
        request_body.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "done"}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 8,
                    "completion_tokens_details": {"reasoning_tokens": 4},
                },
            },
        )

    ledger = CostLedger(tmp_path / "zai.jsonl")
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        route = ZAIRoute("glm-5.2", api_key="test-key", client=client, ledger=ledger)
        route.complete(
            "system",
            [{"role": "user", "content": "test"}],
            [],
            effort="high",
            task_id="test",
            attempt_limit=1,
        )

    row = ledger.rows()[0]
    assert row["request_effort"] == request_body["reasoning_effort"] == "high"
    assert row["request_thinking_type"] == request_body["thinking"]["type"] == "enabled"
    assert row["reasoning_tokens"] == 4


def test_arm_b_response_usage_reads_sse_thinking_tokens_and_keeps_missing_value_none() -> None:
    sse = (
        b'data: {"type":"message_start","message":{"usage":{"output_tokens_details":'
        b'{"thinking_tokens":4}}}}\n\n'
        b'data: {"type":"message_delta","usage":{"output_tokens_details":'
        b'{"thinking_tokens":9,"reasoning_tokens":7}}}\n\n'
    )
    usage = AnthropicCaptureProxy._response_usage(sse, "text/event-stream")
    assert usage["reasoning_tokens"] == 9

    no_reasoning = b'data: {"type":"message_delta","usage":{"output_tokens":8}}\n\n'
    usage_without_reasoning = AnthropicCaptureProxy._response_usage(no_reasoning, "text/event-stream")
    assert usage_without_reasoning["reasoning_tokens"] is None


def test_arm_b_response_usage_reads_json_reasoning_token_fallback() -> None:
    body = json.dumps({"message": {"usage": {"output_tokens_details": {"reasoning_tokens": 7}}}}).encode()
    usage = AnthropicCaptureProxy._response_usage(body, "application/json")
    assert usage["reasoning_tokens"] == 7


def test_arm_b_attempt_records_request_fields_and_reasoning_tokens(tmp_path) -> None:
    ledger = CostLedger(tmp_path / "claude.jsonl")
    proxy = AnthropicCaptureProxy(
        capture_path=tmp_path / "capture.json",
        cost_ledger=ledger,
        task_id="task-1",
        trial=1,
        context={"model_call_latencies": []},
        allowed_tools=set(),
    )
    proxy._record_attempt(
        model=MODEL,
        status=200,
        error_type=None,
        usage={
            "input_tokens": 12,
            "cache_read_input_tokens": 2,
            "cache_creation_input_tokens": 1,
            "output_tokens": 8,
            "reasoning_tokens": 5,
        },
        latency=0.5,
        request_sha256="abc",
        request_effort="max",
        request_thinking_type="adaptive",
    )

    row = ledger.rows()[0]
    assert row["request_effort"] == "max"
    assert row["request_thinking_type"] == "adaptive"
    assert row["reasoning_tokens"] == 5


def test_arm_b_failed_attempt_records_request_fields(tmp_path) -> None:
    ledger = CostLedger(tmp_path / "claude.jsonl")
    proxy = AnthropicCaptureProxy(
        capture_path=tmp_path / "capture.json",
        cost_ledger=ledger,
        task_id="task-1",
        trial=1,
        context={"attempt_failures": []},
        allowed_tools=set(),
    )
    proxy._record_attempt(
        model=MODEL,
        status=503,
        error_type="provider_error",
        usage={
            "input_tokens": 12,
            "cache_read_input_tokens": 2,
            "cache_creation_input_tokens": 1,
            "output_tokens": 8,
            "reasoning_tokens": 5,
        },
        latency=0.5,
        request_sha256="abc",
        request_effort="max",
        request_thinking_type="adaptive",
    )

    failure = ledger.rows()[0]
    assert failure["request_effort"] == "max"
    assert failure["request_thinking_type"] == "adaptive"


def test_arm_b_gate_failure_records_request_fields(tmp_path) -> None:
    body = json.dumps(
        {
            "model": MODEL,
            "max_tokens": 128000,
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": "low"},
            "tools": [{"name": "allowed"}],
            "system": "system",
        }
    ).encode()

    class Handler:
        def __init__(self) -> None:
            self.command = "POST"
            self.path = "/v1/messages"
            self.headers = {"Content-Length": str(len(body))}
            self.rfile = io.BytesIO(body)
            self.wfile = io.BytesIO()

        def send_response(self, _status: int) -> None:
            pass

        def send_header(self, _name: str, _value: str) -> None:
            pass

        def end_headers(self) -> None:
            pass

    ledger = CostLedger(tmp_path / "claude.jsonl")
    proxy = AnthropicCaptureProxy(
        capture_path=tmp_path / "capture.json",
        cost_ledger=ledger,
        task_id="task-1",
        trial=1,
        context={},
        allowed_tools={"allowed"},
    )
    proxy._forward(Handler())

    failure = ledger.rows()[0]
    assert failure["error_type"] == "request_gate_failed"
    assert failure["request_effort"] == "low"
    assert failure["request_thinking_type"] == "adaptive"


def test_route_gpt6_astra_sends_model_and_max_effort(monkeypatch, tmp_path) -> None:
    class Responses:
        def __init__(self) -> None:
            self.body: dict[str, Any] = {}

        def create(self, **body: Any) -> SimpleNamespace:
            self.body = body
            usage = {
                "input_tokens": 10,
                "output_tokens": 4,
                "output_tokens_details": {"reasoning_tokens": 3},
            }
            return SimpleNamespace(output=[], usage=SimpleNamespace(model_dump=lambda: usage))

    responses = Responses()
    client = SimpleNamespace(responses=_raw_sdk_resource(responses))
    monkeypatch.setattr(openai_route_module, "OpenAI", lambda **_kwargs: client)
    ledger = CostLedger(tmp_path / "astra.jsonl")

    route = run_module._route("gpt-6-astra", None, ledger)
    route.complete("system", [{"role": "user", "content": "test"}], [], effort="max", task_id="test", attempt_limit=1)

    assert isinstance(route, OpenAIRoute)
    assert responses.body["model"] == "gpt-6-astra"
    assert responses.body["reasoning"]["effort"] == "max"
    assert ledger.rows()[0]["request_effort"] == "max"


def test_route_gpt_6_1_sol_uses_openai_route_with_price(monkeypatch, tmp_path) -> None:
    from eog.prices import PRICES

    class Responses:
        def __init__(self) -> None:
            self.body: dict[str, Any] = {}

        def create(self, **body: Any) -> SimpleNamespace:
            self.body = body
            usage = {"input_tokens": 100_000, "output_tokens": 10_000}
            return SimpleNamespace(output=[], usage=SimpleNamespace(model_dump=lambda: usage))

    responses = Responses()
    client = SimpleNamespace(responses=_raw_sdk_resource(responses))
    monkeypatch.setattr(openai_route_module, "OpenAI", lambda **_kwargs: client)
    ledger = CostLedger(tmp_path / "sol.jsonl")

    route = run_module._route("gpt-6.1-sol", None, ledger)
    route.complete("system", [{"role": "user", "content": "test"}], [], effort="xhigh", task_id="test", attempt_limit=1)

    assert isinstance(route, OpenAIRoute)
    assert responses.body["model"] == "gpt-6.1-sol"
    assert responses.body["reasoning"]["effort"] == "xhigh"
    assert PRICES["gpt-6.1-sol"].rates() == (2.0, 0.1, 2.5, 10.0)
    assert ledger.rows()[0]["usd"] == pytest.approx(0.3)


def test_route_claude_fable_sends_max_effort_and_adaptive_thinking(monkeypatch, tmp_path) -> None:
    class Messages:
        def __init__(self) -> None:
            self.body: dict[str, Any] = {}

        def create(self, **body: Any) -> SimpleNamespace:
            self.body = body
            usage = {"input_tokens": 10, "output_tokens": 4}
            return SimpleNamespace(content=[], usage=SimpleNamespace(model_dump=lambda: usage))

    messages = Messages()
    client = SimpleNamespace(messages=_raw_sdk_resource(messages))
    monkeypatch.setattr(anthropic_route_module.anthropic, "Anthropic", lambda **_kwargs: client)
    ledger = CostLedger(tmp_path / "fable.jsonl")

    route = run_module._route("claude-fable-5-1", None, ledger)
    route.complete("system", [{"role": "user", "content": "test"}], [], effort="max", task_id="test", attempt_limit=1)

    assert isinstance(route, AnthropicRoute)
    assert messages.body["model"] == "claude-fable-5-1"
    assert messages.body["output_config"]["effort"] == "max"
    assert messages.body["thinking"]["type"] == "adaptive"
    assert ledger.rows()[0]["request_effort"] == "max"
    assert ledger.rows()[0]["request_thinking_type"] == "adaptive"


def test_new_model_context_effort_levels_and_prices() -> None:
    assert run_module.EFFORTS["claude-fable-5-1"] == "max"
    assert run_module.EFFORTS["gpt-6-astra"] == "max"
    assert MODEL_CONTEXT["claude-fable-5-1"] == 1_000_000
    assert MODEL_CONTEXT["gpt-6-astra"] == 1_050_000
    assert EFFORT_LEVELS["claude-fable-5-1"] == ("low", "medium", "high", "max")
    assert EFFORT_LEVELS["gpt-6-astra"] == ("low", "medium", "high", "xhigh", "max")
    assert run_module.EFFORTS["gpt-6.1-sol"] == "xhigh"
    assert MODEL_CONTEXT["gpt-6.1-sol"] == 1_050_000
    assert EFFORT_LEVELS["gpt-6.1-sol"] == ("low", "medium", "high", "xhigh", "max")
    assert PRICES["claude-fable-5-1"].rates() == (10.0, 0.25, 12.5, 50.0)
    assert PRICES["gpt-6-astra"].rates() == (10.0, 1.0, 12.5, 50.0)
    assert PRICES["gpt-6-astra"].rates(272_001) == (20.0, 2.0, 25.0, 75.0)


def test_openai_route_replays_output_items_with_wire_field_names(tmp_path):
    from pydantic import BaseModel, ConfigDict, Field

    from eog.providers.openai_route import OpenAIRoute

    class Item(BaseModel):
        model_config = ConfigDict(populate_by_name=True)
        type: str = "reasoning_note"
        async_: bool | None = Field(default=False, alias="async")
        status: str | None = None

    usage = {"input_tokens": 1, "output_tokens": 1, "output_tokens_details": {"reasoning_tokens": 0}}

    class Responses:
        def create(self, **_body: Any) -> SimpleNamespace:
            return SimpleNamespace(output=[Item()], usage=SimpleNamespace(model_dump=lambda: usage))

    route = OpenAIRoute(
        model="gpt-6-astra",
        client=SimpleNamespace(responses=_raw_sdk_resource(Responses())),
        ledger=CostLedger(tmp_path / "costs.jsonl"),
    )
    turn = route.complete("system", [{"role": "user", "content": "hi"}], [], effort="max", task_id="t")

    replayed = turn.history_items[0]["provider_items"][0]
    assert "async" in replayed
    assert "async_" not in replayed
    assert "status" not in replayed


@pytest.mark.parametrize("route_name", ["anthropic", "openai"])
def test_parse_error_after_http_200_is_a_failed_attempt(route_name: str, tmp_path) -> None:
    def create(**_: Any) -> SimpleNamespace:
        def parse() -> Any:
            raise ValueError("malformed response body")

        return SimpleNamespace(status_code=200, parse=parse)

    resource = SimpleNamespace(with_raw_response=SimpleNamespace(create=create))
    ledger = CostLedger(tmp_path / f"{route_name}-parse.jsonl")
    context = {"model_call_http_statuses": [], "attempt_failures": [], "model_call_latencies": []}
    if route_name == "anthropic":
        route: Any = AnthropicRoute("claude-opus-5-5", client=SimpleNamespace(messages=resource), ledger=ledger)
        effort = "max"
    else:
        route = OpenAIRoute(client=SimpleNamespace(responses=resource), ledger=ledger)
        effort = "low"

    with pytest.raises((NonRetryableProviderError, RetryExhaustedError)):
        route.complete(
            "system", [{"role": "user", "content": "test"}], [], effort=effort, task_id="test", context=context
        )

    assert context["attempt_failures"]
    assert [row for row in ledger.rows() if row.get("attempt") == 1 and row.get("error_class")]
