from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from zep_cloud.core.api_error import ApiError

from eog.recompile import (
    NATIVE_TRANSFORM_TEXT,
    TRANSFORM_TEXT,
    ReplayRow,
    _call_with_rate_limit_retry,
    close_action,
    event_kwargs,
    event_payload,
    filter_replay_export,
    load_replay_rows,
    prepare_replay_file,
    replay_manifest,
    replay_traces,
    run_shared_transform,
)


def _event(
    sequence: int,
    event_type: str,
    content: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {"content": content, "version": 1}
    if metadata is not None:
        payload["metadata"] = metadata
    return {
        "event_id": f"event-{sequence}",
        "event_type": event_type,
        "event_time": f"2026-10-16T15:00:0{sequence}+00:00",
        "sequence": sequence,
        "payload": json.dumps(payload),
        "payload_json": payload,
    }


def _line(
    uuid: str,
    *,
    closed_at: str,
    passed: bool,
    verified: bool = True,
    infra_failed: bool = False,
    events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "trajectory": {
            "uuid": uuid,
            "objective": f"Objective of {uuid}",
            "task_family": "eog.calendar",
            "metadata": {
                "task_id": f"task-{uuid}",
                "domain": "calendar",
                "run_id": "teacher",
                "trial": 1,
                "side": "teach",
            },
            "closed_at": closed_at,
            "outcome": "succeeded" if passed else "failed",
            "verification": "externally_verified",
        },
        "events": events
        or [
            _event(2, "decision", "I will create the calendar."),
            _event(1, "observation", f"Objective of {uuid}"),
            _event(3, "tool_call", '{"summary": "Beta"}', {"tool_name": "gym-calendar__create_calendar"}),
            _event(4, "tool_result", '{"id": "cal-1"}', {"tool_name": "gym-calendar__create_calendar"}),
            _event(5, "checkpoint", '{"turn": 1}', {"turn": 1, "finished": True}),
        ],
        "run_row": {
            "trace_uuid": uuid,
            "run_id": "teacher",
            "task_id": f"task-{uuid}",
            "trial": 1,
            "side": "teach",
            "domain": "calendar",
            "task_family": "eog.calendar",
            "passed": passed,
            "verified": verified,
            "infra_failed": infra_failed,
        },
    }


def _native_export_line() -> dict[str, Any]:
    trace_uuid = "native-trace"
    tool_name = "gym-csm__find_case"
    context = {"tools": [{"name": tool_name}]}

    def make_event(sequence: int, event_type: str, **fields: Any) -> dict[str, Any]:
        timestamp = f"2026-10-16T15:00:{sequence:02d}+00:00"
        return {
            "agent_uuid": "agent-1",
            "content": f"{event_type} content",
            "created_at": timestamp,
            "event_id": f"event-{sequence}",
            "event_type": event_type,
            "occurred_at": timestamp,
            "processed_at": timestamp,
            "processing_state": "processed",
            "sequence": sequence,
            "sources": [{"type": "task", "id": "task-csm"}],
            "trace_uuid": trace_uuid,
            "updated_at": timestamp,
            "uuid": f"event-uuid-{sequence}",
            **fields,
        }

    events = [
        make_event(6, "checkpoint", metadata={"turn": 1}),
        make_event(2, "decision"),
        make_event(4, "tool_result", context=context, metadata={"tool_name": tool_name}, tool_call_id="call-1"),
        make_event(1, "observation"),
        make_event(3, "tool_call", context=context, tool_call_id="call-1"),
        make_event(5, "reflection", metadata={"turn": 1}),
    ]
    return {
        "run_row": {
            "trace_uuid": trace_uuid,
            "run_id": "teacher",
            "task_id": "task-csm",
            "task_family": "eog.csm",
            "outcome": "succeeded",
            "passed": True,
            "verified": True,
            "infra_failed": False,
        },
        "trajectory": {
            "uuid": trace_uuid,
            "objective": "Find the case.",
            "task_family": "eog.csm",
            "metadata": {"task_id": "task-csm", "domain": "csm"},
            "closed_at": "2026-10-16T15:01:00+00:00",
            "outcome": "succeeded",
            "verification": "verified",
        },
        "events": events,
    }


def _write(tmp_path: Path, lines: list[dict[str, Any]]) -> Path:
    path = tmp_path / "replay.jsonl"
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    return path


def _native_event(event: dict[str, Any], tool_call_id: str, tool_name: str) -> None:
    payload = json.loads(event["payload"])
    payload["context"] = {"tools": [{"name": tool_name}]}
    payload["tool_call_id"] = tool_call_id
    event["payload_json"] = payload
    event["payload"] = json.dumps(payload)


class FakeTraceAPI:
    def __init__(self, *, with_tool_call_id: bool, fail_uuid: str | None = None, delay: float = 0.0) -> None:
        self.created: list[dict[str, Any]] = []
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.closed: list[tuple[str, dict[str, Any]]] = []
        self.abandoned: list[tuple[str, dict[str, Any]]] = []
        self.fail_uuid = fail_uuid
        self.delay = delay
        self.in_flight = 0
        self.max_in_flight = 0
        self.lock = threading.Lock()
        if with_tool_call_id:
            self.append_event = self._append_event_with_tool_call_id  # type: ignore[method-assign]

    def create(self, agent_uuid: str, **kwargs: Any) -> Any:
        with self.lock:
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            index = len(self.created) + 1
            self.created.append({"agent_uuid": agent_uuid, **kwargs})
        if self.fail_uuid and kwargs["metadata"]["task_id"] == f"task-{self.fail_uuid}":
            with self.lock:
                self.in_flight -= 1
            raise RuntimeError("HTTP 500")
        return type("Trace", (), {"uuid_": f"new-{index}"})()

    def append_event(self, agent_uuid: str, trace_uuid: str, **kwargs: Any) -> None:
        time.sleep(self.delay)
        with self.lock:
            self.events.append((trace_uuid, kwargs))

    def _append_event_with_tool_call_id(
        self, agent_uuid: str, trace_uuid: str, *, tool_call_id: str | None = None, **kwargs: Any
    ) -> None:
        with self.lock:
            self.events.append((trace_uuid, {**kwargs, "tool_call_id": tool_call_id}))

    def close(self, agent_uuid: str, trace_uuid: str, **kwargs: Any) -> None:
        with self.lock:
            self.closed.append((trace_uuid, kwargs))
            self.in_flight -= 1

    def abandon(self, agent_uuid: str, trace_uuid: str, **kwargs: Any) -> None:
        with self.lock:
            self.abandoned.append((trace_uuid, kwargs))
            self.in_flight -= 1


def _client(**kwargs: Any) -> Any:
    api = FakeTraceAPI(**kwargs)
    return type("Client", (), {"agent": type("Agent", (), {"trace": api})()})(), api


def _transformed(tmp_path: Path, lines: list[dict[str, Any]]) -> tuple[Path, dict[str, Any]]:
    source = _write(tmp_path, lines)
    target = tmp_path / "replay-transformed.jsonl"
    return target, run_shared_transform(source, target)


def test_shared_transform_sets_context_and_tool_call_id_on_calls_and_results(tmp_path: Path) -> None:
    target, record = _transformed(tmp_path, [_line("a", closed_at="t", passed=True)])
    assert record["script"] == "enterpriseops-gym/scripts/add_tool_context.py"
    assert record["script_source"] == "public Agent Skills harness"
    assert (
        record["stats"]["pairs"] == 1 and record["stats"].get("finish_calls", 0) == 0 and record["stats"]["events"] == 5
    )
    assert record["output_sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
    rows = load_replay_rows(target)
    call, result = rows[0].events[2]["payload"], rows[0].events[3]["payload"]
    assert call["tool_call_id"] == "event-3" and result["tool_call_id"] == "event-3"
    assert call["context"] == result["context"] == {"tools": [{"name": "gym-calendar__create_calendar"}]}
    assert call["metadata"] == {"tool_name": "gym-calendar__create_calendar"}
    assert "context" not in rows[0].events[0]["payload"] and "tool_call_id" not in rows[0].events[4]["payload"]


def test_shared_transform_stops_on_a_result_without_a_call(tmp_path: Path) -> None:
    events = [_event(1, "tool_result", "{}", {"tool_name": "gym-calendar__create_calendar"})]
    with pytest.raises(RuntimeError, match="calls and 1 results"):
        _transformed(tmp_path, [_line("a", closed_at="t", passed=True, events=events)])


def test_native_replay_skips_transform_and_records_manifest_source(tmp_path: Path, monkeypatch: Any) -> None:
    line = _line("a", closed_at="t", passed=True)
    for event in line["events"]:
        if event["event_type"] in {"tool_call", "tool_result"}:
            _native_event(event, "toolu-call-1", "gym-calendar__create_calendar")
    source = _write(tmp_path, [line])
    target = tmp_path / "replay-transformed.jsonl"

    def fail_transform(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("The transform must not run for native tool context.")

    monkeypatch.setattr("eog.recompile.run_shared_transform", fail_transform)
    transform, tool_context_source, counts = prepare_replay_file(source, target)
    rows = load_replay_rows(target)
    manifest = replay_manifest(
        run_id="recompile-1",
        compiler_label="pr-1",
        replay_file=source,
        rows=rows,
        concurrency=4,
        agent_record={"agent_id": "eog-skills-recompile-1", "agent_uuid": "agent-1"},
        repo=Path(__file__).resolve().parents[1],
        transform=transform,
        tool_context_source=tool_context_source,
    )

    assert tool_context_source == "native"
    assert counts["native_events"] == 2 and counts["tool_events"] == 2
    assert target.read_bytes() == source.read_bytes()
    assert transform["script"] == "enterpriseops-gym/scripts/add_tool_context.py"
    assert transform["script_sha256"]
    assert manifest["tool_context_source"] == "native"
    assert manifest["transform"] == NATIVE_TRANSFORM_TEXT


def test_native_v4_export_filters_prepares_and_loads_top_level_event_fields(tmp_path: Path, monkeypatch: Any) -> None:
    record = _native_export_line()
    source = _write(tmp_path, [record])
    selected = tmp_path / "selected.jsonl"
    selected_counts = filter_replay_export(source, selected, ["eog.csm"])
    target = tmp_path / "prepared.jsonl"

    def fail_transform(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("The transform must not run for native tool context.")

    monkeypatch.setattr("eog.recompile.run_shared_transform", fail_transform)
    transform, tool_context_source, counts = prepare_replay_file(selected, target)
    rows = load_replay_rows(target)

    base_keys = {
        "agent_uuid",
        "content",
        "created_at",
        "event_id",
        "event_type",
        "occurred_at",
        "processed_at",
        "processing_state",
        "sequence",
        "sources",
        "trace_uuid",
        "updated_at",
        "uuid",
    }
    extra_keys = {
        "observation": set(),
        "decision": set(),
        "tool_call": {"context", "tool_call_id"},
        "tool_result": {"context", "metadata", "tool_call_id"},
        "checkpoint": {"metadata"},
        "reflection": {"metadata"},
    }
    assert set(record) == {"run_row", "trajectory", "events"}
    assert all("payload" not in event and "payload_json" not in event for event in record["events"])
    assert all(set(event) == base_keys | extra_keys[event["event_type"]] for event in record["events"])
    assert selected_counts["selected_rows"] == 1
    assert tool_context_source == "native"
    assert counts["tool_events"] == counts["native_events"] == 2
    assert transform["script_sha256"]
    assert target.read_bytes() == selected.read_bytes()
    assert len(rows) == 1
    assert [event["sequence"] for event in rows[0].events] == [1, 2, 3, 4, 5, 6]

    source_events = {event["sequence"]: event for event in record["events"]}
    events = {event["sequence"]: event for event in rows[0].events}
    for sequence, event in events.items():
        original = source_events[sequence]
        assert event["event_time"] == original["occurred_at"]
        assert event["payload"]["content"] == original["content"]
        assert event["payload"]["sources"] == original["sources"]
    call = events[3]["payload"]
    result = events[4]["payload"]
    assert call["context"] == result["context"] == {"tools": [{"name": "gym-csm__find_case"}]}
    assert call["tool_call_id"] == result["tool_call_id"] == "call-1"
    assert result["metadata"] == {"tool_name": "gym-csm__find_case"}
    assert events[5]["payload"]["metadata"] == {"turn": 1}
    assert events[6]["payload"]["metadata"] == {"turn": 1}


def test_event_without_payload_or_content_raises() -> None:
    with pytest.raises(ValueError, match="has no JSON payload"):
        event_payload({"event_id": "event-missing-payload", "event_type": "observation"})


@pytest.mark.parametrize(
    ("result_id", "result_name"),
    [
        ("missing-call", "gym-calendar__create_calendar"),
        ("toolu-call-1", "gym-calendar__update_calendar"),
    ],
)
def test_native_replay_stops_on_an_unmatched_result(tmp_path: Path, result_id: str, result_name: str) -> None:
    line = _line("a", closed_at="t", passed=True)
    for event in line["events"]:
        if event["event_type"] == "tool_call":
            _native_event(event, "toolu-call-1", "gym-calendar__create_calendar")
        elif event["event_type"] == "tool_result":
            _native_event(event, result_id, result_name)
    source = _write(tmp_path, [line])

    with pytest.raises(SystemExit, match="event-4"):
        prepare_replay_file(source, tmp_path / "replay-transformed.jsonl")


def test_native_replay_stops_when_tool_calls_share_an_id(tmp_path: Path) -> None:
    line = _line("a", closed_at="t", passed=True)
    line["events"].append(_event(6, "tool_call", "{}", {"tool_name": "gym-calendar__create_calendar"}))
    for event in line["events"]:
        if event["event_type"] in {"tool_call", "tool_result"}:
            _native_event(event, "toolu-call-1", "gym-calendar__create_calendar")
    source = _write(tmp_path, [line])

    with pytest.raises(SystemExit, match="event-6"):
        prepare_replay_file(source, tmp_path / "replay-transformed.jsonl")


def test_mixed_replay_stops_with_tool_context_counts(tmp_path: Path) -> None:
    line = _line("a", closed_at="t", passed=True)
    call = next(event for event in line["events"] if event["event_type"] == "tool_call")
    _native_event(call, "toolu-call-1", "gym-calendar__create_calendar")
    source = _write(tmp_path, [line])
    target = tmp_path / "replay-transformed.jsonl"

    with pytest.raises(SystemExit) as error:
        prepare_replay_file(source, target)

    assert "tool_events=2" in str(error.value)
    assert "native_events=1" in str(error.value)
    assert "legacy_events=1" in str(error.value)
    assert not target.exists()


def test_legacy_replay_runs_the_shared_transform(tmp_path: Path) -> None:
    source = _write(tmp_path, [_line("a", closed_at="t", passed=True)])
    target = tmp_path / "replay-transformed.jsonl"

    transform, tool_context_source, counts = prepare_replay_file(source, target)
    rows = load_replay_rows(target)
    call, result = rows[0].events[2]["payload"], rows[0].events[3]["payload"]

    assert tool_context_source == "transform"
    assert counts["legacy_events"] == 2 and counts["native_events"] == 0
    assert transform["stats"]["traces"] == 1
    assert call["tool_call_id"] == result["tool_call_id"] == "event-3"
    assert call["context"] == result["context"] == {"tools": [{"name": "gym-calendar__create_calendar"}]}


def test_rows_load_in_original_close_order_with_sorted_events(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [
            _line("b", closed_at="2026-10-16T16:00:02+00:00", passed=False),
            _line("a", closed_at="2026-10-16T16:00:01+00:00", passed=True),
        ],
    )
    rows = load_replay_rows(path)
    assert [row.source_trace_uuid for row in rows] == ["a", "b"]
    assert [event["sequence"] for event in rows[0].events] == [1, 2, 3, 4, 5]
    assert rows[0].passed is True and rows[0].verified is True and rows[0].infra_failed is False
    assert rows[0].highest_sequence == 5
    assert rows[0].events[2]["payload"]["metadata"] == {"tool_name": "gym-calendar__create_calendar"}


def test_event_kwargs_forward_the_transformed_payload() -> None:
    event = {
        "event_type": "tool_call",
        "sequence": 3,
        "event_time": "2026-10-16T15:29:07+00:00",
        "payload": {
            "content": "{}",
            "metadata": {"tool_name": "gym-calendar__get_calendar_list"},
            "context": {"tools": [{"name": "gym-calendar__get_calendar_list"}]},
            "tool_call_id": "event-3",
        },
    }
    assert event_kwargs(event) == {
        "event_type": "tool_call",
        "content": "{}",
        "metadata": {"tool_name": "gym-calendar__get_calendar_list"},
        "sequence": 3,
        "occurred_at": "2026-10-16T15:29:07+00:00",
        "context": {"tools": [{"name": "gym-calendar__get_calendar_list"}]},
        "tool_call_id": "event-3",
    }
    plain = event_kwargs({"event_type": "decision", "sequence": 1, "payload": {"content": "Plan."}})
    assert plain == {"event_type": "decision", "content": "Plan.", "metadata": None, "sequence": 1}
    checkpoint = event_kwargs(
        {"event_type": "checkpoint", "sequence": 5, "payload": {"content": "{}", "metadata": {"turn": 1}}}
    )
    assert "context" not in checkpoint and "tool_call_id" not in checkpoint
    assert checkpoint["metadata"] == {"turn": 1}


@pytest.mark.parametrize(
    ("shape", "content"),
    [
        (
            "top-level error",
            '{"success": false, "error": {"status_code": 403, "message": "Forbidden"}}',
        ),
        (
            "detail error",
            '{"detail": {"error": true, "error_code": "NOT_FOUND", "message": '
            "\"Configuration item not found with identifier 'WIFI-UK-005'\"}}",
        ),
        ("detail string", '{"detail": "Group with name \'HR Service Desk Group\' not found"}'),
        (
            "detail list",
            '{"detail": [{"type": "int_parsing", "loc": ["body", "parent_case"], '
            '"msg": "Input should be a valid integer"}]}',
        ),
        ("error prefix", "Error: x-user-email header is required for user identification"),
        ("cross mark", "❌  ❌ Invalid Tool Arguments: ['criteria.to: is required']"),
        ("does not exist", "Calendar with id 'primary' does not exist"),
        (
            "validation errors",
            "Validation errors:\n- conferenceData: Cannot specify both createRequest and "
            "conferenceSolution/entryPoints",
        ),
        ("calendar text", "Calendar adrian_burton-primary not found with timezone America/New_York"),
        ("already member", "User 'james.wilson' is already a member of this channel"),
        ("you must set", "To set backgroundColor/foregroundColor you must set colorRgbFormat=true"),
    ],
)
def test_event_kwargs_flags_error_tool_results(shape: str, content: str) -> None:
    kwargs = event_kwargs(
        {
            "event_type": "tool_result",
            "sequence": 1,
            "payload": {"content": content, "metadata": {"tool_name": "t"}},
        }
    )
    assert kwargs["metadata"] == {"tool_name": "t", "error": True}, shape


@pytest.mark.parametrize(
    ("shape", "content"),
    [
        ("success with error text", '{"id": "abc", "status": "created", "note": "user not found earlier"}'),
        ("null error", '{"success": true, "error": null}'),
        ("detail with extra key", '{"detail": {"id": 1}, "name": "x"}'),
        ("plain text success", "Message sent to channel general"),
        ("JSON list", "[]"),
    ],
)
def test_event_kwargs_keeps_non_error_tool_results(shape: str, content: str) -> None:
    kwargs = event_kwargs(
        {
            "event_type": "tool_result",
            "sequence": 1,
            "payload": {"content": content, "metadata": {"tool_name": "t"}},
        }
    )
    assert kwargs["metadata"] == {"tool_name": "t"}, shape


def test_event_kwargs_does_not_flag_tool_calls_or_remove_existing_error() -> None:
    tool_call = event_kwargs(
        {
            "event_type": "tool_call",
            "sequence": 1,
            "payload": {
                "content": "Error: x-user-email header is required for user identification",
                "metadata": {"tool_name": "t"},
            },
        }
    )
    assert tool_call["metadata"] == {"tool_name": "t"}

    tool_result = event_kwargs(
        {
            "event_type": "tool_result",
            "sequence": 2,
            "payload": {"content": "Message sent to channel general", "metadata": {"tool_name": "t", "error": True}},
        }
    )
    assert tool_result["metadata"] == {"tool_name": "t", "error": True}


@pytest.mark.parametrize(
    ("passed", "verified", "infra_failed", "expected_action", "expected"),
    [
        (True, True, False, "close", {"outcome": "succeeded", "verification": "external"}),
        (False, True, False, "close", {"outcome": "failed", "verification": "external"}),
        (True, False, False, "close", {"outcome": "failed", "verification": "none"}),
        (
            True,
            True,
            True,
            "abandon",
            {"highest_accepted_sequence": 5, "reason": "infra_failed", "verification": "none"},
        ),
    ],
)
def test_close_mapping(passed: bool, verified: bool, infra_failed: bool, expected_action: str, expected: dict) -> None:
    row = ReplayRow(
        source_trace_uuid="a",
        objective="o",
        task_family="eog.calendar",
        metadata={},
        events=tuple({"event_type": "decision", "sequence": index, "payload": {"content": "x"}} for index in (1, 5, 3)),
        run_row={},
        closed_at="",
        passed=passed,
        verified=verified,
        infra_failed=infra_failed,
    )
    action, kwargs = close_action(row, "new-1")
    assert action == expected_action
    assert {key: kwargs[key] for key in expected} == expected
    if action == "close":
        verifier = kwargs["verifier"]
        if verified:
            assert verifier.verifier_id == "eog-verifier" and verifier.class_ == "harness"
            assert verifier.outcome == expected["outcome"] and verifier.evidence_reference == "new-1"
            assert kwargs["reason"] is None
        else:
            assert verifier is None and kwargs["reason"] == "The episode ended before verification."


def test_replay_writes_ledger_in_order_and_sends_the_transform(tmp_path: Path) -> None:
    path, _record = _transformed(
        tmp_path,
        [
            _line("b", closed_at="2026-10-16T16:00:02+00:00", passed=False),
            _line("a", closed_at="2026-10-16T16:00:01+00:00", passed=True),
            _line("c", closed_at="2026-10-16T16:00:03+00:00", passed=True, infra_failed=True),
        ],
    )
    rows = load_replay_rows(path)
    client, api = _client(with_tool_call_id=False)
    traces_path = tmp_path / "traces.jsonl"
    summary = replay_traces(client, "agent-1", rows, run_id="recompile-1", traces_path=traces_path, concurrency=1)
    assert summary == {
        "traces": 3,
        "events": 15,
        "errors": [],
        "concurrency": 1,
        "rate_limit_retries": 0,
    }
    assert [item["metadata"]["task_id"] for item in api.created] == ["task-a", "task-b", "task-c"]
    assert api.created[0]["objective"] == "Objective of a" and api.created[0]["task_family"] == "eog.calendar"
    first_events = [kwargs for trace_uuid, kwargs in api.events if trace_uuid == "new-1"]
    assert [event["sequence"] for event in first_events] == [1, 2, 3, 4, 5]
    assert first_events[2]["context"] == {"tools": [{"name": "gym-calendar__create_calendar"}]}
    assert first_events[2]["metadata"] == {"tool_name": "gym-calendar__create_calendar"}
    assert first_events[2]["tool_call_id"] == first_events[3]["tool_call_id"] == "event-3"
    assert "context" not in first_events[0] and "tool_call_id" not in first_events[0]
    assert first_events[0]["occurred_at"] == "2026-10-16T15:00:01+00:00"
    assert [(trace_uuid, kwargs["outcome"]) for trace_uuid, kwargs in api.closed] == [
        ("new-1", "succeeded"),
        ("new-2", "failed"),
    ]
    assert api.abandoned == [
        ("new-3", {"highest_accepted_sequence": 5, "reason": "infra_failed", "verification": "none"})
    ]
    ledger = [json.loads(line) for line in traces_path.read_text().splitlines()]
    assert [(row["trace_uuid"], row["source_trace_uuid"]) for row in ledger] == [
        ("new-1", "a"),
        ("new-2", "b"),
        ("new-3", "c"),
    ]
    assert ledger[0].pop("closed_at") > ledger[0].pop("source_closed_at") == rows[0].closed_at
    assert ledger[0] == {
        "trace_uuid": "new-1",
        "source_trace_uuid": "a",
        "replay_run_id": "recompile-1",
        "run_id": "teacher",
        "task_id": "task-a",
        "trial": 1,
        "side": "teach",
        "domain": "calendar",
        "task_family": "eog.calendar",
        "passed": True,
        "source_passed": True,
        "verified": True,
        "infra_failed": False,
        "events": 5,
        "close_action": "close",
    }
    assert ledger[2]["passed"] is False and ledger[2]["source_passed"] is True
    assert ledger[2]["close_action"] == "abandon"


def test_recompile_replays_only_selected_families_with_original_trace_fields(tmp_path: Path) -> None:
    selected = _line("selected", closed_at="2026-10-16T16:00:01+00:00", passed=False)
    selected["trajectory"]["task_family"] = "eog.csm"
    selected["trajectory"]["metadata"].update({"task_family": "eog.csm", "outcome": "failed", "passed": False})
    selected["trajectory"].pop("outcome")
    selected["run_row"]["task_family"] = "eog.csm"
    selected["run_row"]["custom_field"] = "original"
    selected["events"] = [
        {
            "event_id": "event-2",
            "event_type": "decision",
            "occurred_at": "2026-10-16T15:00:02+00:00",
            "sequence": 2,
            "payload_json": {"content": "Second.", "sources": [{"source_id": "source-2"}]},
        },
        {
            "event_id": "event-1",
            "event_type": "observation",
            "occurred_at": "2026-10-16T15:00:01+00:00",
            "sequence": 1,
            "payload_json": {"content": "First.", "sources": [{"source_id": "source-1"}]},
        },
    ]
    other = _line("other", closed_at="2026-10-16T16:00:02+00:00", passed=True)
    other["trajectory"]["task_family"] = "eog.email"
    source = _write(tmp_path, [selected, other])
    selected_export = tmp_path / "selected.jsonl"
    counts = filter_replay_export(source, selected_export, ["eog.csm"])
    transformed = tmp_path / "selected-transformed.jsonl"
    prepare_replay_file(selected_export, transformed)
    rows = load_replay_rows(transformed)
    client, api = _client(with_tool_call_id=False)

    summary = replay_traces(
        client,
        "agent-1",
        rows,
        run_id="recompile-1",
        traces_path=tmp_path / "traces.jsonl",
        concurrency=1,
    )

    assert counts == {"source_rows": 2, "selected_rows": 1}
    assert summary["traces"] == 1
    assert [item["task_family"] for item in api.created] == ["eog.csm"]
    assert api.created[0]["metadata"] == selected["trajectory"]["metadata"]
    assert [event["sequence"] for _, event in api.events] == [1, 2]
    assert [(event["content"], event["sources"], event["occurred_at"]) for _, event in api.events] == [
        ("First.", [{"source_id": "source-1"}], "2026-10-16T15:00:01+00:00"),
        ("Second.", [{"source_id": "source-2"}], "2026-10-16T15:00:02+00:00"),
    ]
    assert api.closed[0][1]["outcome"] == "failed"
    [ledger_row] = [json.loads(line) for line in (tmp_path / "traces.jsonl").read_text().splitlines()]
    assert ledger_row["trace_uuid"] == "new-1"
    assert ledger_row["source_trace_uuid"] == "selected"
    assert ledger_row["run_id"] == "teacher"
    assert ledger_row["custom_field"] == "original"
    assert ledger_row["replay_run_id"] == "recompile-1"


def test_replay_sends_tool_call_id_when_the_sdk_has_the_parameter(tmp_path: Path) -> None:
    events = [
        _event(1, "tool_call", "{}", {"tool_name": "gym-calendar__create_calendar"}),
        _event(2, "tool_result", "{}", {"tool_name": "gym-calendar__create_calendar"}),
    ]
    target, _record = _transformed(tmp_path, [_line("a", closed_at="t", passed=True, events=events)])
    rows = load_replay_rows(target)
    client, api = _client(with_tool_call_id=True)
    replay_traces(client, "agent-1", rows, run_id="r", traces_path=tmp_path / "t.jsonl", concurrency=2)
    assert [kwargs["tool_call_id"] for _trace, kwargs in api.events] == ["event-1", "event-1"]
    assert api.events[0][1]["context"] == {"tools": [{"name": "gym-calendar__create_calendar"}]}


def test_replay_limits_in_flight_traces_and_records_errors(tmp_path: Path) -> None:
    lines = [_line(f"t{index}", closed_at=f"2026-10-16T16:00:0{index}+00:00", passed=True) for index in range(6)]
    rows = load_replay_rows(_write(tmp_path, lines))
    client, api = _client(with_tool_call_id=False, fail_uuid="t2", delay=0.01)
    summary = replay_traces(client, "agent-1", rows, run_id="r", traces_path=tmp_path / "t.jsonl", concurrency=2)
    assert summary["traces"] == 5 and summary["events"] == 25
    assert summary["errors"] == [{"source_trace_uuid": "t2", "error_class": "RuntimeError", "message": "HTTP 500"}]
    assert api.max_in_flight <= 2
    ledger = [json.loads(line) for line in (tmp_path / "t.jsonl").read_text().splitlines()]
    assert [row["source_trace_uuid"] for row in ledger] == ["t0", "t1", "t3", "t4", "t5"]
    with pytest.raises(ValueError, match="at least 1"):
        replay_traces(client, "agent-1", rows, run_id="r", traces_path=tmp_path / "u.jsonl", concurrency=0)


def test_replay_retries_rate_limit_errors_and_counts_them(tmp_path: Path, monkeypatch: Any) -> None:
    class RateLimitedCreateAPI(FakeTraceAPI):
        def __init__(self) -> None:
            super().__init__(with_tool_call_id=False)
            self.create_attempts = 0

        def create(self, agent_uuid: str, **kwargs: Any) -> Any:
            self.create_attempts += 1
            if self.create_attempts <= 2:
                raise ApiError(status_code=429, headers={"retry-after": "2"}, body={})
            return super().create(agent_uuid, **kwargs)

        def append_event(self, agent_uuid: str, trace_uuid: str, **kwargs: Any) -> None:
            with self.lock:
                self.events.append((trace_uuid, kwargs))

    sleep_calls: list[int] = []
    monkeypatch.setattr("eog.recompile.time.sleep", sleep_calls.append)
    api = RateLimitedCreateAPI()
    client = type("Client", (), {"agent": type("Agent", (), {"trace": api})()})()
    rows = load_replay_rows(_write(tmp_path, [_line("a", closed_at="t", passed=True)]))

    replay = replay_traces(
        client,
        "agent-1",
        rows,
        run_id="r",
        traces_path=tmp_path / "traces.jsonl",
        concurrency=1,
    )

    assert api.create_attempts == 3
    assert sleep_calls == [3, 3]
    assert replay["traces"] == 1 and replay["errors"] == []
    assert replay["rate_limit_retries"] == 2


def test_rate_limit_retry_does_not_retry_other_api_errors(monkeypatch: Any) -> None:
    attempts = 0
    sleep_calls: list[int] = []
    monkeypatch.setattr("eog.recompile.time.sleep", sleep_calls.append)

    def fail() -> None:
        nonlocal attempts
        attempts += 1
        raise ApiError(status_code=500, headers={"retry-after": "2"}, body={})

    with pytest.raises(ApiError):
        _call_with_rate_limit_retry(fail)

    assert attempts == 1 and sleep_calls == []


@pytest.mark.parametrize("headers", [None, {"retry-after": "invalid"}])
def test_rate_limit_retry_stops_after_ten_attempts_and_uses_default_delay(
    monkeypatch: Any, headers: dict[str, str] | None
) -> None:
    attempts = 0
    retries: list[bool] = []
    sleep_calls: list[int] = []
    monkeypatch.setattr("eog.recompile.time.sleep", sleep_calls.append)

    def fail() -> None:
        nonlocal attempts
        attempts += 1
        raise ApiError(status_code=429, headers=headers, body={})

    with pytest.raises(ApiError):
        _call_with_rate_limit_retry(fail, on_retry=lambda: retries.append(True))

    assert attempts == 10
    assert len(retries) == 9
    assert sleep_calls == [61] * 9


def test_manifest_records_label_commit_transform_and_counts(tmp_path: Path) -> None:
    path = _write(tmp_path, [_line("a", closed_at="t1", passed=True), _line("b", closed_at="t2", passed=False)])
    rows = load_replay_rows(path)
    manifest = replay_manifest(
        run_id="recompile-1",
        compiler_label="pr-1+2",
        replay_file=path,
        rows=rows,
        concurrency=4,
        agent_record={"agent_id": "eog-skills-recompile-1", "agent_uuid": "agent-1"},
        repo=Path(__file__).resolve().parents[1],
        task_scope="all",
    )
    assert manifest["compiler_label"] == "pr-1+2" and manifest["stage"] == "recompile"
    assert manifest["task_scope"] == "all"
    assert manifest["tool_context_source"] == "transform"
    assert len(manifest["harness_git_commit"]) == 40
    assert manifest["transform"] == TRANSFORM_TEXT and "context" in TRANSFORM_TEXT and "tool_call_id" in TRANSFORM_TEXT
    assert manifest["replay_concurrency"] == 4 and manifest["replay_file_sha256"]
    assert manifest["source_traces"] == 2 and manifest["source_traces_passed"] == 1 and manifest["source_events"] == 10
    assert manifest["task_families"] == ["eog.calendar"] and manifest["agent"]["agent_uuid"] == "agent-1"


def test_replay_refreshes_close_metadata_and_preserves_source_close_time(tmp_path: Path) -> None:
    line = _line("source", closed_at="2026-10-16T16:00:01+00:00", passed=True)
    line["run_row"].update(
        {"closed_at": line["trajectory"]["closed_at"], "close_action": "source-close", "events": 999}
    )
    rows = load_replay_rows(_write(tmp_path, [line]))
    client, _api = _client(with_tool_call_id=False)
    traces_path = tmp_path / "traces.jsonl"

    replay_traces(client, "agent-1", rows, run_id="recompile-1", traces_path=traces_path, concurrency=1)

    record = json.loads(traces_path.read_text().splitlines()[0])
    assert record["closed_at"] != line["run_row"]["closed_at"]
    assert record["source_closed_at"] == line["run_row"]["closed_at"]
    assert record["close_action"] == "close"
    assert record["events"] == len(rows[0].events)
