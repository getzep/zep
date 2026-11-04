from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from appworld_replay import (
    ReplayInputError,
    _CaptureClient,
    _record_episode,
    build_replay_episode,
    latest_rows,
)
from appworld_run import task_family_for

FIXTURE = Path(__file__).parent / "data" / "appworld_replay_episode.json"


def _plain(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)
    if isinstance(value, dict):
        return {key: _plain(nested) for key, nested in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(nested) for nested in value]
    return value


def _comparable_calls(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    comparable = []
    for call in calls:
        operation = call["operation"]
        kwargs = _plain(call["kwargs"])
        kwargs.pop("idempotency_key", None)
        if operation == "create":
            kwargs.pop("metadata", None)
        if operation == "append_event":
            kwargs.pop("occurred_at", None)
        comparable.append(
            {
                "operation": operation,
                "args": _plain(call["args"])[1:],
                "kwargs": kwargs,
            }
        )
    return comparable


def test_invented_episode_fixture_reconstructs_append_actions() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    episode = build_replay_episode(fixture, fixture["messages"])

    assert episode.task_family == task_family_for("scenario_demo_2", "scenario")
    assert episode.objective == "Inspect a sample event."
    assert [action.method for action in episode.actions] == [
        "record_message",
        "record_tool_call",
        "record_tool_result",
    ]
    call = episode.actions[1]
    assert call.args == (
        "execute_python",
        {"code": "print('sample')"},
    )
    assert call.kwargs == {
        "call_id": "call_demo_001",
        "context_tools": ["execute_python"],
    }
    assert episode.actions[2].args == (
        "execute_python",
        "Sample result.",
    )


def test_latest_rows_keep_failed_attempt_and_sort_by_started_at() -> None:
    rows = [
        {
            "task_id": "scenario_a",
            "task_index": 0,
            "trial": 1,
            "started_at": "2025-01-01T00:00:00Z",
            "succeeded": True,
        },
        {
            "task_id": "scenario_b",
            "task_index": 1,
            "trial": 1,
            "started_at": "2025-01-01T00:01:00Z",
            "succeeded": True,
        },
        {
            "task_id": "scenario_a",
            "task_index": 0,
            "trial": 1,
            "started_at": "2025-01-01T00:02:00Z",
            "succeeded": False,
        },
        {
            "task_id": "scenario_a",
            "task_index": 0,
            "trial": 2,
            "started_at": "2025-01-01T00:03:00Z",
            "succeeded": True,
        },
    ]

    latest = latest_rows(rows)

    assert [(row["task_id"], row["trial"]) for row in latest] == [
        ("scenario_b", 1),
        ("scenario_a", 1),
        ("scenario_a", 2),
    ]
    assert latest[1]["succeeded"] is False


def test_replay_family_uses_scenario_scope() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    episode = build_replay_episode(fixture, fixture["messages"])

    assert episode.task_family == task_family_for(fixture["task_id"], "scenario")
    assert episode.task_family != task_family_for(fixture["task_id"], "domain")


def test_unmatched_tool_result_is_rejected() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    messages = [
        fixture["messages"][0],
        {"role": "tool", "id": "missing-call", "content": "unmatched"},
    ]

    with pytest.raises(ReplayInputError, match="no matching tool call"):
        build_replay_episode(fixture, messages)


class _EpisodeServer:
    def __init__(self) -> None:
        self.completed = False
        self.blocked_codes = 0

    def begin_episode(self) -> None:
        self.blocked_codes = 0

    def verify_guard_state(self, _task_id: str) -> float:
        return 0.0

    def call(self, method: str, **_body: Any) -> Any:
        if method == "initialize":
            return {
                "supervisor": {
                    "first_name": "A",
                    "last_name": "B",
                    "email": "a@b.c",
                    "phone_number": "1",
                },
                "instruction": "Complete the task.",
            }
        if method == "execute":
            self.completed = True
            return "tool result"
        if method == "task_completed":
            return self.completed
        if method == "evaluate":
            return {
                "success": True,
                "passes": [{"name": "complete"}],
                "failures": [],
                "num_tests": 1,
            }
        if method == "close":
            return None
        raise AssertionError(f"Unexpected AppWorld method: {method}")


def _model_response(*, text: str = "", function_call: dict[str, Any] | None = None) -> Any:
    output = (
        [function_call]
        if function_call
        else [{"type": "message", "content": [{"type": "output_text", "text": text}]}]
    )
    return SimpleNamespace(
        status="completed",
        output=output,
        output_text=text,
        stop_reason=None,
        usage=SimpleNamespace(
            input_tokens=1,
            output_tokens=1,
            input_tokens_details=SimpleNamespace(cached_tokens=0, cache_creation_tokens=0),
            output_tokens_details=SimpleNamespace(reasoning_tokens=0),
        ),
    )


def test_saved_transcript_replay_matches_run_episode_trace_calls(tmp_path: Path) -> None:
    import appworld_run

    responses = [
        _model_response(text="I will inspect the API."),
        _model_response(
            function_call={
                "type": "function_call",
                "name": "execute_python",
                "arguments": json.dumps({"code": "print(apis.api_docs.show_app_descriptions())"}),
                "call_id": "fixture-call-2",
            }
        ),
    ]

    def create(**_kwargs: Any) -> Any:
        return responses.pop(0)

    source_client = _CaptureClient()
    result, messages = appworld_run.run_episode(
        server=_EpisodeServer(),
        client=source_client,
        openai_client=SimpleNamespace(),
        agent_openai_client=SimpleNamespace(responses=SimpleNamespace(create=create)),
        agent_uuid="source-agent",
        stage="teacher",
        run_id="source-run",
        task_id="scenario_demo_2",
        task_index=0,
        trial=1,
        model="fake-model",
        reasoning_effort="low",
        max_steps=3,
        arm="none",
        memory_agent_uuid=None,
        corpus=None,
        curator_model="fake-model",
        curator_reasoning_effort="low",
        family=task_family_for("scenario_demo_2", "scenario"),
    )
    row = {
        "task_id": result.task_id,
        "task_index": result.task_index,
        "trial": result.trial,
        "succeeded": result.succeeded,
        "end_reason": result.end_reason,
        "stop_reason": result.stop_reason,
        "started_at": result.started_at,
    }
    transcript_path = tmp_path / "transcripts-teacher.jsonl"
    transcript_path.write_text(
        json.dumps({"task_id": result.task_id, "messages": messages}) + "\n",
        encoding="utf-8",
    )
    saved = json.loads(transcript_path.read_text(encoding="utf-8"))
    replay_episode = build_replay_episode(row, saved["messages"])
    replay_client = _CaptureClient()
    _record_episode(
        replay_client,
        replay_episode,
        agent_uuid="replay-agent",
        source_run_id="source-run",
        run_id="replay-run",
    )

    assert result.succeeded is True
    assert [call["operation"] for call in source_client.agent.trace.calls] == [
        "create",
        "append_event",
        "append_event",
        "append_event",
        "append_event",
        "close",
    ]
    replay_metadata = replay_client.agent.trace.calls[0]["kwargs"]["metadata"]
    assert replay_metadata["stage"] == "replay"
    assert replay_metadata["source_run_id"] == "source-run"
    assert _comparable_calls(source_client.agent.trace.calls) == _comparable_calls(
        replay_client.agent.trace.calls
    )


def test_dry_run_counts_only_appended_events() -> None:
    from appworld_replay import _event_counts

    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    episode = build_replay_episode(fixture, fixture["messages"])
    client = _CaptureClient()
    _record_episode(
        client,
        episode,
        agent_uuid="dry-run-agent",
        source_run_id="source-run",
        run_id="dry-run",
    )

    assert _event_counts(client.agent.trace.calls) == {
        "observation": 1,
        "tool_call": 1,
        "tool_result": 1,
    }
