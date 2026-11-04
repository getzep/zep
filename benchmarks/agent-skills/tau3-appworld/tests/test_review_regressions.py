"""Regression tests for the benchmark harness."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import anthropic
import httpx
import openai
import pytest
from tenacity import wait_none


def test_resume_runs_only_infrastructure_errors_again_tau2() -> None:
    import run

    rows = [
        {"domain": "d", "task_index": 0, "trial": 1, "error": "AgentContextLimitError"},
        {"domain": "d", "task_index": 1, "trial": 1, "error": "AgentResponseFailedError"},
        {"domain": "d", "task_index": 2, "trial": 1, "error": None},
        {"domain": "d", "task_index": 3, "trial": 1, "error": "ConnectError"},
        {
            "domain": "d",
            "task_index": 4,
            "trial": 1,
            "error": "PermissionDeniedError",
            "error_detail": "PermissionDeniedError:403",
        },
        {
            "domain": "d",
            "task_index": 5,
            "trial": 1,
            "error": "InternalServerError",
            "error_detail": "InternalServerError:502",
        },
        {
            "domain": "d",
            "task_index": 6,
            "trial": 1,
            "error": "ApiError",
            "error_detail": "ApiError:409",
        },
        {
            "domain": "d",
            "task_index": 7,
            "trial": 1,
            "error": "APIStatusError",
            "error_detail": "APIStatusError:200",
        },
    ]
    jobs = [("d", index, 1, None) for index in range(8)]
    pending = run._pending_jobs(jobs, run._completed_keys(rows))
    assert [job[1] for job in pending] == [3, 5, 6, 7]


def test_resume_reruns_all_error_rows_again_appworld() -> None:
    from appworld_run import startup_jobs

    latest = {
        ("a_1", 1): {
            "task_id": "a_1",
            "trial": 1,
            "error": "BadRequestError",
            "error_detail": "BadRequestError:400",
        },
        ("b_1", 1): {"task_id": "b_1", "trial": 1, "error": None, "succeeded": False},
        ("c_1", 1): {
            "task_id": "c_1",
            "trial": 1,
            "error": "HTTPStatusError",
            "error_detail": "HTTPStatusError:503",
        },
    }
    jobs = startup_jobs(["a_1", "b_1", "c_1"], latest, trials=1, max_attempts=1)
    assert [(job[1], job[2]) for job in jobs] == [("a_1", 1), ("c_1", 1)]


def test_attempt_counts_include_rows_with_no_attempt_field() -> None:
    import run

    rows = [
        {"domain": "d", "task_index": 0, "trial": 1, "error": "ConnectError"},
        {"domain": "d", "task_index": 0, "trial": 1, "error": "ConnectError", "attempt": 2},
        {"domain": "d", "task_index": 1, "trial": 1, "error": None},
    ]
    assert run._attempt_counts(rows) == {("d", 0, 1): 2, ("d", 1, 1): 1}


def test_empty_model_turn_ends_the_episode_as_an_agent_failure() -> None:
    from tau2.data_model.message import UserMessage

    from agent import AgentEmptyResponseError, ZepResponsesAgent

    responses = SimpleNamespace(
        create=lambda **_: SimpleNamespace(
            output=[{"type": "reasoning", "summary": []}],
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        )
    )
    agent = ZepResponsesAgent(
        [],
        "policy",
        openai_client=SimpleNamespace(responses=responses),
        agent_model="m",
        reasoning_effort="high",
        recorder=MagicMock(),
    )
    with pytest.raises(AgentEmptyResponseError):
        agent.generate_next_message(UserMessage(role="user", content="hi"), agent.get_init_state())


def test_tool_events_keep_whitespace_and_use_the_tool_event_limit() -> None:
    from redaction import Redactor
    from zep_capture import MAX_TOOL_RESULT_EVENT_CHARS, TrajectoryRecorder

    client = MagicMock()
    client.agent.trace.create.return_value = SimpleNamespace(uuid="trace-1")
    recorder = TrajectoryRecorder(
        client,
        "agent",
        "fam",
        "obj",
        "env",
        {},
        redactor=Redactor(),
        discoverable_tools=frozenset(),
        stage="teacher",
        run_id="r",
        domain="appworld",
        task_index=0,
        trial=1,
    )
    code = "for page in range(10):\n    if page:\n        print(page)\n" + "x = 1\n" * 600
    long_output = "line\n" * 5000
    with recorder:
        recorder.record_message("user", "hi   there")
        recorder.record_tool_call("execute_python", {"code": code})
        recorder.record_tool_result("execute_python", '{\n  "a": 1\n}')
        recorder.record_tool_result("execute_python", long_output)
    events = [call.kwargs for call in client.agent.trace.append_event.call_args_list]
    call_event = next(event for event in events if event["event_type"] == "tool_call")
    results = [event for event in events if event["event_type"] == "tool_result"]
    assert json.loads(call_event["content"])["code"] == code
    assert results[0]["content"] == '{\n  "a": 1\n}'
    assert len(results[1]["content"]) == MAX_TOOL_RESULT_EVENT_CHARS
    assert results[1]["content"].endswith("...[truncated]")
    observation = next(event for event in events if event["event_type"] == "observation")
    assert observation["content"] == "user: hi there"


def test_appworld_context_limit_is_classified_and_not_retried() -> None:
    import appworld_run
    from agent import AgentContextLimitError

    calls = []

    def create(**_: Any) -> Any:
        calls.append(1)
        raise openai.BadRequestError(
            "This endpoint's maximum context length is 262144 tokens.",
            response=httpx.Response(400, request=httpx.Request("POST", "https://x/v1/responses")),
            body=None,
        )

    completion = cast(Any, appworld_run._completion).retry_with(wait=wait_none())
    with pytest.raises(AgentContextLimitError):
        completion(SimpleNamespace(responses=SimpleNamespace(create=create)), {"model": "m"})
    assert len(calls) == 1


def test_appworld_other_400_is_not_retried() -> None:
    import appworld_run

    calls = []

    def create(**_: Any) -> Any:
        calls.append(1)
        raise openai.BadRequestError(
            "bad",
            response=httpx.Response(400, request=httpx.Request("POST", "https://x")),
            body=None,
        )

    completion = cast(Any, appworld_run._completion).retry_with(wait=wait_none())
    with pytest.raises(openai.BadRequestError):
        completion(SimpleNamespace(responses=SimpleNamespace(create=create)), {"model": "m"})
    assert len(calls) == 1


class _FakeServer:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.blocked_codes = 0

    def begin_episode(self) -> None:
        self.blocked_codes = 0

    def verify_guard_state(self, _task_id: str) -> float:
        return 0.0

    def call(self, method: str, **body: Any) -> Any:
        self.calls.append(method)
        return {
            "initialize": {
                "supervisor": {
                    "first_name": "A",
                    "last_name": "B",
                    "email": "a@b.c",
                    "phone_number": "1",
                },
                "instruction": "Do the task.",
            },
            "task_completed": False,
            "evaluate": {"success": False, "passes": [], "num_tests": 3},
            "close": None,
        }[method]


def _appworld_episode(
    response: Any,
    *,
    server: _FakeServer | None = None,
    response_inputs: list[list[dict[str, Any]]] | None = None,
    max_steps: int = 40,
) -> tuple[Any, list[int]]:
    import appworld_run

    responses_calls: list[int] = []
    pending_responses = list(response) if isinstance(response, list) else None

    def create(**kwargs: Any) -> Any:
        responses_calls.append(1)
        if response_inputs is not None:
            response_inputs.append(kwargs["input"])
        return pending_responses.pop(0) if pending_responses is not None else response

    client = MagicMock()
    client.agent.trace.create.return_value = SimpleNamespace(uuid="trace-1")
    result, _ = appworld_run.run_episode(
        server=cast(Any, server or _FakeServer()),
        client=client,
        openai_client=MagicMock(),
        agent_openai_client=SimpleNamespace(responses=SimpleNamespace(create=create)),
        agent_uuid="agent",
        stage="heldout-none",
        run_id="r",
        task_id="s_2",
        task_index=0,
        trial=1,
        model="m",
        reasoning_effort="low",
        max_steps=max_steps,
        arm="none",
        memory_agent_uuid=None,
        corpus=None,
        curator_model="m",
        curator_reasoning_effort="low",
    )
    return result, responses_calls


def test_unknown_tool_returns_error_without_executing_code() -> None:
    server = _FakeServer()
    response_inputs: list[list[dict[str, Any]]] = []
    unknown_tool = SimpleNamespace(
        status="completed",
        output=[
            {
                "type": "function_call",
                "name": "unknown_tool",
                "arguments": '{"code":"print(1)"}',
                "call_id": "call-1",
            }
        ],
        output_text="",
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
    )
    text_response = SimpleNamespace(
        status="completed",
        output=[{"type": "message", "content": [{"type": "output_text", "text": "Done."}]}],
        output_text="Done.",
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
    )

    result, _ = _appworld_episode(
        [unknown_tool, text_response],
        server=server,
        response_inputs=response_inputs,
        max_steps=2,
    )

    assert "execute" not in server.calls
    assert result.malformed_tool_calls == 1
    assert any(
        item.get("type") == "function_call_output"
        and item.get("output") == "Unknown tool: unknown_tool"
        for item in response_inputs[1]
    )


def test_appworld_failed_response_is_an_error_with_a_ledger_row() -> None:
    result, calls = _appworld_episode(
        SimpleNamespace(
            status="failed",
            error=SimpleNamespace(code="server_error", message="upstream"),
            output=[],
            output_text="",
            usage=None,
        )
    )
    assert len(calls) == 1
    assert result.error == "AgentResponseFailedError" and result.succeeded is False
    assert result.failed_calls == [
        {"service": "model", "operation": "response.failed", "outcome": "server_error", "count": 1}
    ]


def test_appworld_incomplete_response_is_an_error_with_a_ledger_row() -> None:
    result, calls = _appworld_episode(
        SimpleNamespace(
            status="incomplete",
            incomplete_details=SimpleNamespace(reason="max_output_tokens"),
            output=[],
            output_text="",
            usage=None,
        )
    )
    assert len(calls) == 1
    assert result.error == "AgentResponseIncompleteError"
    assert result.failed_calls == [
        {
            "service": "model",
            "operation": "response.incomplete",
            "outcome": "max_output_tokens",
            "count": 1,
        }
    ]


def test_appworld_rows_have_a_cost() -> None:
    import argparse
    from dataclasses import fields

    from appworld_run import AppWorldResult
    from run import cost_usd

    assert "cost_usd" in {item.name for item in fields(AppWorldResult)}
    prices = argparse.Namespace(
        input_price_per_million=1.0,
        cached_input_price_per_million=0.1,
        output_price_per_million=2.0,
        cache_write_price_per_million=None,
    )
    row = {"prompt_tokens": 1000, "cached_tokens": 500, "completion_tokens": 100}
    assert cost_usd(row, prices) == pytest.approx((500 * 1.0 + 500 * 0.1 + 100 * 2.0) / 1e6)


def test_anthropic_transient_errors_use_the_episode_retry_loop() -> None:
    import agent

    def failing(exc_factory: Any) -> tuple[list[int], Any]:
        calls: list[int] = []

        def create(**_: Any) -> Any:
            calls.append(1)
            raise exc_factory()

        return calls, SimpleNamespace(responses=SimpleNamespace(create=create))

    request = httpx.Request("POST", "https://x/v1/messages")
    completion = cast(Any, agent._completion).retry_with(wait=wait_none())
    for factory in (
        lambda: anthropic.InternalServerError(
            "overloaded", response=httpx.Response(529, request=request), body=None
        ),
        lambda: anthropic.RateLimitError(
            "slow down", response=httpx.Response(429, request=request), body=None
        ),
        lambda: anthropic.APIConnectionError(request=request),
        lambda: openai.InternalServerError(
            "server", response=httpx.Response(500, request=request), body=None
        ),
    ):
        calls, client = failing(factory)
        with pytest.raises((anthropic.APIError, openai.APIError)):
            completion(client, {"model": "m"})
        assert len(calls) == agent.RETRY_LIMIT
    calls, client = failing(
        lambda: anthropic.BadRequestError(
            "bad", response=httpx.Response(400, request=request), body=None
        )
    )
    with pytest.raises(anthropic.BadRequestError):
        completion(client, {"model": "m"})
    assert len(calls) == 1


def test_anthropic_stream_error_event_is_in_the_ledger() -> None:
    from anthropic_client import _AnthropicResponses
    from call_ledger import CURRENT_LEDGER, CallLedger, LedgerTransport

    body = (
        b'event: message_start\ndata: {"type":"message_start","message":{"id":"m","type":"message",'
        b'"role":"assistant","model":"claude-haiku-5-5","content":[],"stop_reason":null,'
        b'"usage":{"input_tokens":1,"output_tokens":1}}}\n\n'
        b'event: error\ndata: {"type":"error","error":{"type":"overloaded_error",'
        b'"message":"Overloaded"}}\n\n'
    )
    transport = LedgerTransport(
        "anthropic",
        httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=body
            )
        ),
    )
    client = anthropic.Anthropic(
        api_key="x", max_retries=0, http_client=httpx.Client(transport=transport)
    )
    ledger = CallLedger()
    token = CURRENT_LEDGER.set(ledger)
    try:
        with pytest.raises(anthropic.APIStatusError):
            _AnthropicResponses(client).create(
                model="claude-haiku-5-5", input=[{"role": "user", "content": "hi"}]
            )
    finally:
        CURRENT_LEDGER.reset(token)
    assert ledger.rows() == [
        {
            "service": "anthropic",
            "operation": "POST /v1/messages",
            "outcome": "stream:overloaded_error",
            "count": 1,
        }
    ]


def test_anthropic_reasoning_tokens_are_not_available() -> None:
    from anthropic_client import _response, reports_reasoning_tokens

    message = SimpleNamespace(
        content=[
            SimpleNamespace(
                model_dump=lambda **_: {"type": "thinking", "thinking": "x" * 400, "signature": "s"}
            ),
            SimpleNamespace(model_dump=lambda **_: {"type": "text", "text": "ok"}),
        ],
        stop_reason="end_turn",
        usage=SimpleNamespace(
            input_tokens=10,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
            output_tokens=300,
        ),
    )
    usage = _response(message).usage
    assert usage.output_tokens == 300 and usage.output_tokens_details.reasoning_tokens is None
    assert not reports_reasoning_tokens("claude-haiku-5-5")
    assert reports_reasoning_tokens("qwen/qwen3.8-27b")


def test_cost_needs_every_required_price() -> None:
    import argparse

    from run import cost_usd

    prices = argparse.Namespace(
        input_price_per_million=1.0,
        cached_input_price_per_million=None,
        output_price_per_million=2.0,
        cache_write_price_per_million=None,
    )
    assert cost_usd({"prompt_tokens": 10, "completion_tokens": 1}, prices) is None
