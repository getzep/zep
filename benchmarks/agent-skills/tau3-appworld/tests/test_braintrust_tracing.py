import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from braintrust_tracing import CURRENT_BRAINTRUST_RECORDER, BraintrustLogger, BraintrustRecorder
from redaction import Redactor
from tracing import traced_responses_create


class FakeBraintrust:
    def __init__(self, fail_inserts: int = 0, insert_status: int = 500) -> None:
        self.rows: list[dict[str, Any]] = []
        self.projects: list[str] = []
        self.fail_inserts = fail_inserts
        self.insert_status = insert_status
        self.insert_calls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer bt-key"
        body = json.loads(request.content)
        if request.url.path == "/v1/project":
            self.projects.append(body["name"])
            return httpx.Response(200, json={"id": "project-1", "name": body["name"]})
        assert request.url.path == "/v1/project_logs/project-1/insert"
        self.insert_calls += 1
        if self.insert_calls <= self.fail_inserts:
            return httpx.Response(self.insert_status, json={"error": "fail"})
        self.rows.extend(body["events"])
        return httpx.Response(200, json={"row_ids": [row["id"] for row in body["events"]]})


def _logger(fake: FakeBraintrust) -> BraintrustLogger:
    return BraintrustLogger(
        "pilot",
        api_key="bt-key",
        api_url="https://braintrust.test",
        transport=httpx.MockTransport(fake.handler),
    )


def _recorder(logger: BraintrustLogger, redactor: Redactor | None = None) -> BraintrustRecorder:
    return BraintrustRecorder(
        logger,
        name="tau2-airline-agent",
        metadata={"domain": "airline", "task_index": 3},
        tags=["tau2", "airline", "task:3"],
        redactor=redactor,
    )


class Responses:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(
            output=[
                {
                    "type": "function_call",
                    "call_id": "call-1",
                    "name": "get_reservation",
                    "arguments": '{"id": "ABC123"}',
                }
            ],
            usage=SimpleNamespace(input_tokens=11, output_tokens=7),
        )


def test_missing_api_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BRAINTRUST_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="BRAINTRUST_API_KEY"):
        BraintrustLogger("pilot")


def test_episode_writes_root_llm_and_tool_spans_in_sdk_shape() -> None:
    fake = FakeBraintrust()
    logger = _logger(fake)
    assert fake.projects == ["pilot"]
    client = SimpleNamespace(responses=Responses())
    with _recorder(logger) as recorder:
        assert CURRENT_BRAINTRUST_RECORDER.get() is recorder
        recorder.record_message("user", "Change my flight.")
        traced_responses_create(
            client, "tau2-agent", {"model": "gpt-test", "input": [{"role": "user"}]}
        )
        recorder.record_tool_call("get_reservation", {"id": "ABC123"})
        recorder.record_tool_result("get_reservation", '{"status": "ok"}')
        recorder.record_message("assistant", "Your flight is changed.")
        recorder.set_result(True, True)
        recorder.set_reward(1.0)
    assert CURRENT_BRAINTRUST_RECORDER.get() is None

    root_id = recorder.root_span_id
    start, llm, tool, merge = fake.rows
    assert start["id"] == start["span_id"] == start["root_span_id"] == root_id
    assert start["span_parents"] == []
    assert start["span_attributes"] == {"name": "tau2-airline-agent", "type": "task"}
    assert start["tags"] == ["tau2", "airline", "task:3"]
    assert set(start["metrics"]) == {"start"}
    assert "end" not in start["metrics"]

    assert llm["span_attributes"] == {"name": "tau2-agent", "type": "llm"}
    assert llm["span_parents"] == [root_id]
    assert llm["root_span_id"] == root_id
    assert llm["input"] == [{"role": "user"}]
    assert llm["output"][0]["type"] == "function_call"
    assert llm["metrics"]["prompt_tokens"] == 11
    assert llm["metrics"]["completion_tokens"] == 7
    assert llm["metadata"]["model"] == "gpt-test"

    assert tool["span_attributes"] == {"name": "get_reservation", "type": "tool"}
    assert tool["span_parents"] == [root_id]
    assert tool["input"] == {"id": "ABC123"}
    assert tool["output"] == '{"status": "ok"}'
    assert tool["metrics"]["start"] <= tool["metrics"]["end"]

    assert merge["id"] == root_id
    assert merge["_is_merge"] is True
    assert merge["input"] == {"messages": [{"role": "user", "content": "Change my flight."}]}
    assert merge["output"] == "Your flight is changed."
    assert merge["scores"] == {"reward": 1.0}
    assert merge["metadata"]["succeeded"] is True
    assert merge["metrics"]["end"] >= start["metrics"]["start"]


def test_tool_spans_match_call_ids_and_keep_name_fifo_fallback() -> None:
    fake = FakeBraintrust()
    recorder = _recorder(_logger(fake))
    with recorder:
        recorder.record_tool_call("lookup", {"ordinal": 1}, call_id="call-1")
        recorder.record_tool_call("lookup", {"ordinal": 2}, call_id="call-2")
        recorder.record_tool_result("lookup", "result-2", call_id="call-2")
        recorder.record_tool_result("lookup", "result-1", call_id="call-1")
        recorder.record_tool_call("lookup", {"ordinal": 3})
        recorder.record_tool_call("lookup", {"ordinal": 4})
        recorder.record_tool_result("lookup", "result-3")
        recorder.record_tool_result("lookup", "result-4")

    tool_spans = [row for row in fake.rows if row.get("span_attributes", {}).get("type") == "tool"]
    assert [(row["input"], row["output"]) for row in tool_spans] == [
        ({"ordinal": 2}, "result-2"),
        ({"ordinal": 1}, "result-1"),
        ({"ordinal": 3}, "result-3"),
        ({"ordinal": 4}, "result-4"),
    ]


def test_failed_episode_records_error_and_no_score() -> None:
    fake = FakeBraintrust()
    with pytest.raises(ValueError), _recorder(_logger(fake)) as recorder:
        recorder.record_message("user", "Help.")
        raise ValueError("boom")
    merge = fake.rows[-1]
    assert merge["error"] == "ValueError"
    assert "scores" not in merge
    assert "end" in merge["metrics"]


def test_unverified_episode_has_no_score() -> None:
    fake = FakeBraintrust()
    with _recorder(_logger(fake)) as recorder:
        recorder.record_message("user", "Help.")
    assert "scores" not in fake.rows[-1]


def test_span_text_is_redacted_and_encrypted_reasoning_is_dropped() -> None:
    fake = FakeBraintrust()
    redactor = Redactor.from_values({"reservation_id": "ABC123"})
    client = SimpleNamespace(responses=Responses())
    with _recorder(_logger(fake), redactor) as recorder:
        recorder.record_message("user", "Reservation ABC123 please.")
        traced_responses_create(
            client,
            "tau2-agent",
            {
                "model": "gpt-test",
                "input": [{"type": "reasoning", "encrypted_content": "secret", "summary": []}],
            },
        )
        recorder.record_tool_call("get_reservation", {"id": "ABC123"})
        recorder.record_tool_result("get_reservation", "ABC123 found")
    rows = json.dumps(fake.rows)
    assert "ABC123" not in rows
    assert "encrypted_content" not in rows


def test_llm_error_is_recorded_without_output() -> None:
    class FailingResponses:
        def create(self, **kwargs: Any) -> Any:
            raise TimeoutError

    fake = FakeBraintrust()
    with _recorder(_logger(fake)), pytest.raises(TimeoutError):
        traced_responses_create(
            SimpleNamespace(responses=FailingResponses()),
            "tau2-agent",
            {"model": "gpt-test", "input": []},
        )
    llm = fake.rows[1]
    assert llm["error"] == "TimeoutError"
    assert "output" not in llm


def test_no_recorder_means_no_braintrust_span() -> None:
    client = SimpleNamespace(responses=Responses())
    traced_responses_create(client, "tau2-agent", {"model": "gpt-test", "input": []})
    assert CURRENT_BRAINTRUST_RECORDER.get() is None


def test_insert_retries_server_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("braintrust_tracing.time.sleep", lambda _seconds: None)
    fake = FakeBraintrust(fail_inserts=2)
    logger = _logger(fake)
    logger.insert([{"id": "row-1"}])
    assert fake.insert_calls == 3
    assert logger.rows_sent == 1


def test_insert_does_not_retry_client_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("braintrust_tracing.time.sleep", lambda _seconds: None)
    fake = FakeBraintrust(fail_inserts=5, insert_status=400)
    logger = _logger(fake)
    with pytest.raises(RuntimeError):
        logger.insert([{"id": "row-1"}])
    assert fake.insert_calls == 1
    assert logger.rows_failed == 1
