from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter, defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic, sleep
from types import SimpleNamespace
from typing import Any

from appworld_run import DOMAIN, ENVIRONMENT, VERIFIER_ID, appworld_api_names, task_family_for
from config import load_config, zep_client
from redaction import Redactor
from skill_snapshot import SnapshotExportError, compile_snapshot, wait_for_compilation, write_funnel
from zep_capture import LOAD_SKILL_TOOL_NAME, TrajectoryRecorder, ensure_agent


class ReplayInputError(ValueError):
    pass


@dataclass(frozen=True)
class ReplayAction:
    method: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any]


@dataclass(frozen=True)
class ReplayEpisode:
    row: dict[str, Any]
    task_family: str
    objective: str
    redactor: Redactor
    actions: list[ReplayAction]
    planned_open_offset_seconds: float | None = None
    planned_close_offset_seconds: float | None = None


class _CaptureTrace:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
        self.calls.append({"operation": "create", "args": args, "kwargs": kwargs})
        return SimpleNamespace(uuid="captured-trace")

    def append_event(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append({"operation": "append_event", "args": args, "kwargs": kwargs})

    def close(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append({"operation": "close", "args": args, "kwargs": kwargs})

    def abandon(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append({"operation": "abandon", "args": args, "kwargs": kwargs})


class _CaptureClient:
    def __init__(self) -> None:
        self.agent = SimpleNamespace(trace=_CaptureTrace())


def _jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReplayInputError(f"{path.name} has invalid JSON on line {line_number}") from exc
        if not isinstance(record, dict):
            raise ReplayInputError(f"{path.name} has a non-object record on line {line_number}")
        records.append(record)
    return records


def _nonempty(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def _validate_source_rows(rows: list[dict[str, Any]]) -> None:
    parent_fields = (
        "parent_trajectory_uuid",
        "parent_trace_uuid",
        "parent_trace_id",
        "parent_trace",
        "parent_uuid",
    )
    error_fields = ("error", "error_detail", "error_type", "trace_error")
    unsupported_task_ids = set()
    for row in rows:
        task_id = str(row.get("task_id") or "<missing task_id>")
        try:
            attempt = int(row.get("attempt") or 1)
        except (TypeError, ValueError):
            unsupported_task_ids.add(task_id)
            continue
        if attempt > 1 or any(_nonempty(row.get(field)) for field in parent_fields + error_fields):
            unsupported_task_ids.add(task_id)
    if unsupported_task_ids:
        task_ids = ", ".join(sorted(unsupported_task_ids))
        raise ReplayInputError(
            "Replay source has unsupported retries, parent Traces, or error rows for "
            f"task IDs: {task_ids}"
        )


def _transcript_jsonl(path: Path) -> tuple[list[tuple[int, dict[str, Any]]], list[dict[str, Any]]]:
    records: list[tuple[int, dict[str, Any]]] = []
    errors = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            errors.append(
                {
                    "file": path.name,
                    "line": line_number,
                    "error": "invalid JSON",
                }
            )
            continue
        if not isinstance(record, dict):
            errors.append(
                {
                    "file": path.name,
                    "line": line_number,
                    "error": "record is not an object",
                }
            )
            continue
        records.append((line_number, record))
    return records, errors


def latest_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[tuple[str, int], tuple[datetime, int, dict[str, Any]]] = {}
    for index, row in enumerate(rows):
        task_id = row.get("task_id")
        started_at = row.get("started_at")
        if not isinstance(task_id, str) or not task_id or not isinstance(started_at, str):
            raise ReplayInputError("Every teacher row must have task_id and started_at fields")
        try:
            timestamp = datetime.fromisoformat(started_at)
            trial = int(row["trial"])
            int(row["task_index"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ReplayInputError(
                f"Teacher row {task_id} has an invalid trial or start time"
            ) from exc
        if not isinstance(row.get("succeeded"), bool):
            raise ReplayInputError(f"Teacher row {task_id} has no Boolean succeeded value")
        key = (task_id, trial)
        previous = latest.get(key)
        if previous is None or (timestamp, index) > (previous[0], previous[1]):
            latest[key] = (timestamp, index, row)
    return [
        record
        for _, _, record in sorted(
            latest.values(),
            key=lambda item: (item[0], item[1], str(item[2]["task_id"]), int(item[2]["trial"])),
        )
    ]


def _source_timestamp(value: Any, task_id: str, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ReplayInputError(f"Teacher row {task_id} has no {field} timestamp")
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ReplayInputError(f"Teacher row {task_id} has an invalid {field} timestamp") from exc
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC)


def _source_time_offsets(
    rows: list[dict[str, Any]],
) -> dict[tuple[str, int], tuple[float, float]]:
    parsed = []
    for row in rows:
        task_id = str(row["task_id"])
        started_at = _source_timestamp(row.get("started_at"), task_id, "started_at")
        finished_at = _source_timestamp(row.get("finished_at"), task_id, "finished_at")
        if finished_at < started_at:
            raise ReplayInputError(f"Teacher row {task_id} closes before it starts")
        parsed.append((row, started_at, finished_at))
    if not parsed:
        return {}
    first_start = min(started_at for _, started_at, _ in parsed)
    return {
        (str(row["task_id"]), int(row["trial"])): (
            (started_at - first_start).total_seconds(),
            (finished_at - first_start).total_seconds(),
        )
        for row, started_at, finished_at in parsed
    }


def _messages_for_row(
    row: dict[str, Any],
    transcripts: dict[str, list[dict[str, Any]]],
    source_dir: Path,
    *,
    task_has_multiple_trials: bool = False,
) -> list[dict[str, Any]]:
    task_id = str(row["task_id"])
    candidates = transcripts.get(task_id, [])
    matching_trial = [record for record in candidates if record.get("trial") == row.get("trial")]
    if matching_trial:
        candidates = matching_trial
        task_has_multiple_trials = False
    elif task_has_multiple_trials and len(candidates) < 2:
        raise ReplayInputError(f"The saved transcript for task {task_id} is ambiguous")
    if len(candidates) == 1 and not task_has_multiple_trials:
        messages = candidates[0].get("messages")
        if isinstance(messages, list) and all(isinstance(message, dict) for message in messages):
            return messages
    simulation_path = (
        source_dir
        / "simulations"
        / f"teacher-{DOMAIN}-{row.get('task_index')}-{row.get('trial')}.json"
    )
    if simulation_path.is_file():
        simulation = json.loads(simulation_path.read_text(encoding="utf-8"))
        if not isinstance(simulation, dict):
            raise ReplayInputError(f"Simulation file for task {task_id} is not an object")
        if simulation.get("task_id") != task_id:
            raise ReplayInputError(f"Simulation task ID does not match teacher row {task_id}")
        messages = simulation.get("messages")
        if isinstance(messages, list) and all(isinstance(message, dict) for message in messages):
            matches = [
                record
                for record in candidates
                if _simulation_messages_match(record.get("messages"), messages)
            ]
            if len(matches) == 1:
                matched_messages = matches[0].get("messages")
                if isinstance(matched_messages, list):
                    return matched_messages
    if not candidates:
        raise ReplayInputError(f"No transcript was saved for task {task_id}")
    raise ReplayInputError(f"The saved transcript for task {task_id} is ambiguous")


def _simulation_messages_match(transcript: Any, simulation: list[dict[str, Any]]) -> bool:
    if not isinstance(transcript, list) or len(transcript) != len(simulation):
        return False
    for transcript_message, simulation_message in zip(transcript, simulation, strict=True):
        if not isinstance(transcript_message, dict):
            return False
        if any(transcript_message.get(key) != value for key, value in simulation_message.items()):
            return False
    return True


def _redactor_and_objective(messages: list[dict[str, Any]]) -> tuple[Redactor, str]:
    if (
        not isinstance(messages, list)
        or not messages
        or any(not isinstance(message, dict) for message in messages)
        or messages[0].get("role") != "user"
    ):
        raise ReplayInputError("The transcript does not start with a user message")
    first = str(messages[0].get("content") or "")
    supervisor_line, separator, objective = first.partition("\nTask: ")
    prefix = "Supervisor: "
    if not separator or not supervisor_line.startswith(prefix):
        raise ReplayInputError(
            "The first user message does not contain the saved Supervisor fields"
        )
    fields = supervisor_line[len(prefix) :].rsplit(", ", 2)
    if len(fields) != 3:
        raise ReplayInputError("The first user message has invalid Supervisor fields")
    name, email, phone = fields
    names = name.split(" ", 1)
    if len(names) != 2 or not email or not phone:
        raise ReplayInputError("The first user message has invalid Supervisor fields")
    supervisor = {
        "first_name": names[0],
        "last_name": names[1],
        "email": email,
        "phone_number": phone.removesuffix("."),
    }
    if not supervisor["phone_number"]:
        raise ReplayInputError("The first user message has no Supervisor phone number")
    return Redactor.from_values(supervisor), objective


def _action(method: str, *args: Any, **kwargs: Any) -> ReplayAction:
    return ReplayAction(method=method, args=args, kwargs=kwargs)


def _actions_for_transcript(
    row: dict[str, Any],
    messages: list[dict[str, Any]],
    *,
    skill_reader_enabled: bool,
) -> list[ReplayAction]:
    # TrajectoryRecorder.emit events are local only and are never posted.
    actions = [_action("record_message", "user", str(messages[0].get("content") or ""))]
    refusal = row.get("end_reason") == "refusal" or row.get("stop_reason") == "refusal"
    last_assistant = max(
        (index for index, message in enumerate(messages) if message.get("role") == "assistant"),
        default=-1,
    )
    index = 1
    while index < len(messages):
        message = messages[index]
        role = message.get("role")
        if role == "user":
            actions.append(_action("record_message", "user", str(message.get("content") or "")))
            index += 1
            continue
        if role == "assistant":
            calls = message.get("tool_calls") or []
            if not isinstance(calls, list):
                raise ReplayInputError(f"Transcript message {index} has invalid tool_calls")
            if not calls:
                text = str(message.get("content") or "").strip()
                if text and not (refusal and index == last_assistant):
                    actions.append(_action("record_message", "assistant", text))
                index += 1
                continue
            index += 1
            for call in calls:
                if not isinstance(call, dict):
                    raise ReplayInputError(
                        f"Transcript message {index - 1} has an invalid tool call"
                    )
                name = call.get("name")
                call_id = call.get("id")
                arguments = call.get("arguments")
                if not isinstance(name, str) or not isinstance(call_id, str) or not call_id:
                    raise ReplayInputError(
                        f"Transcript message {index - 1} has an invalid tool call"
                    )
                if not isinstance(arguments, dict):
                    raise ReplayInputError(f"Tool call {call_id} has invalid arguments")
                malformed_error = call.get("malformed_error")
                context_tools = None
                if malformed_error:
                    recorded_arguments = {"arguments": str(call.get("raw_arguments") or "")}
                else:
                    recorded_arguments = arguments
                    if name == "execute_python":
                        code = str(arguments.get("code") or "")
                        context_tools = appworld_api_names(code)
                skip_tool_events = skill_reader_enabled and name == LOAD_SKILL_TOOL_NAME
                if not skip_tool_events:
                    actions.append(
                        _action(
                            "record_tool_call",
                            name,
                            recorded_arguments,
                            call_id=call_id,
                            context_tools=context_tools,
                        )
                    )
                if index >= len(messages):
                    raise ReplayInputError(f"Tool call {call_id} has no saved tool result")
                result = messages[index]
                if result.get("role") != "tool" or result.get("id") != call_id:
                    raise ReplayInputError(f"Tool call {call_id} has no matching saved tool result")
                if not skip_tool_events:
                    actions.append(
                        _action(
                            "record_tool_result",
                            name,
                            str(result.get("content") or ""),
                            call_id=call_id,
                            context_tools=context_tools,
                        )
                    )
                index += 1
            continue
        if role == "tool":
            raise ReplayInputError(
                f"Tool result at transcript message {index} has no matching tool call"
            )
        raise ReplayInputError(f"Transcript message {index} has unsupported role")
    return actions


def build_replay_episode(
    row: dict[str, Any],
    messages: list[dict[str, Any]],
    *,
    skill_reader_enabled: bool = False,
    planned_open_offset_seconds: float | None = None,
    planned_close_offset_seconds: float | None = None,
) -> ReplayEpisode:
    task_id = str(row["task_id"])
    redactor, objective = _redactor_and_objective(messages)
    return ReplayEpisode(
        row=row,
        task_family=task_family_for(task_id, "scenario"),
        objective=objective,
        redactor=redactor,
        actions=_actions_for_transcript(
            row,
            messages,
            skill_reader_enabled=skill_reader_enabled,
        ),
        planned_open_offset_seconds=planned_open_offset_seconds,
        planned_close_offset_seconds=planned_close_offset_seconds,
    )


def load_replay_episodes(
    source_dir: Path,
) -> tuple[list[ReplayEpisode], list[dict[str, Any]], int]:
    source_rows = _jsonl(source_dir / "teacher.jsonl")
    _validate_source_rows(source_rows)
    rows = latest_rows(source_rows)
    time_offsets = _source_time_offsets(rows)
    transcript_records, errors = _transcript_jsonl(source_dir / "transcripts-teacher.jsonl")
    transcripts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for line_number, record in transcript_records:
        task_id = record.get("task_id")
        if isinstance(task_id, str):
            transcripts[task_id].append(record)
        else:
            errors.append(
                {
                    "file": "transcripts-teacher.jsonl",
                    "line": line_number,
                    "error": "record has no task_id",
                }
            )
    manifest = json.loads((source_dir / "manifest-teacher.json").read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ReplayInputError("The teacher manifest is not an object")
    arguments = manifest.get("arguments") or {}
    if not isinstance(arguments, dict):
        raise ReplayInputError("The teacher manifest has invalid arguments")
    skill_reader_enabled = arguments.get("arm") == "skills" and bool(
        arguments.get("skills_snapshot")
    )
    episodes: list[ReplayEpisode] = []
    task_trial_counts = Counter(str(row["task_id"]) for row in rows)
    for row in rows:
        task_id = str(row["task_id"])
        try:
            messages = _messages_for_row(
                row,
                transcripts,
                source_dir,
                task_has_multiple_trials=task_trial_counts[task_id] > 1,
            )
            episodes.append(
                build_replay_episode(
                    row,
                    messages,
                    skill_reader_enabled=skill_reader_enabled,
                    planned_open_offset_seconds=time_offsets[(task_id, int(row["trial"]))][0],
                    planned_close_offset_seconds=time_offsets[(task_id, int(row["trial"]))][1],
                )
            )
        except (ReplayInputError, json.JSONDecodeError) as exc:
            errors.append(
                {
                    "task_id": task_id,
                    "trial": row.get("trial"),
                    "error": str(exc),
                }
            )
    return episodes, errors, len(rows)


def _new_recorder(
    client: Any,
    episode: ReplayEpisode,
    *,
    agent_uuid: str,
    source_run_id: str,
    run_id: str,
) -> TrajectoryRecorder:
    row = episode.row
    return TrajectoryRecorder(
        client,
        agent_uuid,
        episode.task_family,
        episode.objective,
        ENVIRONMENT,
        {
            "harness": "appworld-skills",
            "stage": "replay",
            "task_index": str(row["task_index"]),
            "source_run_id": source_run_id,
        },
        redactor=episode.redactor,
        stage="replay",
        run_id=run_id,
        domain=DOMAIN,
        task_index=int(row["task_index"]),
        trial=int(row["trial"]),
        verifier_id=VERIFIER_ID,
        objective_from_request=False,
        parent_trajectory_uuid=row.get("parent_trajectory_uuid"),
        discoverable_tools=frozenset(),
    )


def _record_episode(
    client: Any,
    episode: ReplayEpisode,
    *,
    agent_uuid: str,
    source_run_id: str,
    run_id: str,
    close_at_offset_seconds: float | None = None,
    replay_started_at: float | None = None,
    clock: Callable[[], float] = monotonic,
    sleep_fn: Callable[[float], None] = sleep,
) -> str | None:
    recorder = _new_recorder(
        client,
        episode,
        agent_uuid=agent_uuid,
        source_run_id=source_run_id,
        run_id=run_id,
    )
    with recorder:
        for action in episode.actions:
            getattr(recorder, action.method)(*action.args, **action.kwargs)
        recorder.set_result(bool(episode.row.get("succeeded")), True)
        if close_at_offset_seconds is not None:
            if replay_started_at is None:
                raise ValueError("A replay start time is required for a planned close time")
            _wait_until(
                replay_started_at + close_at_offset_seconds,
                clock=clock,
                sleep_fn=sleep_fn,
            )
    return recorder.trajectory_uuid


def _wait_until(
    target: float,
    *,
    clock: Callable[[], float],
    sleep_fn: Callable[[float], None],
) -> None:
    while (remaining := target - clock()) > 0:
        sleep_fn(remaining)


def _event_counts(calls: list[dict[str, Any]]) -> Counter[str]:
    return Counter(
        str(call["kwargs"]["event_type"]) for call in calls if call["operation"] == "append_event"
    )


def _planned_time_summary(episodes: list[ReplayEpisode]) -> dict[str, Any]:
    timed = [
        episode
        for episode in episodes
        if episode.planned_open_offset_seconds is not None
        and episode.planned_close_offset_seconds is not None
    ]
    if not timed:
        return {
            "planned_first_open_offset_seconds": None,
            "planned_last_close_offset_seconds": None,
            "planned_offset_span_seconds": None,
            "families_with_overlapping_traces": 0,
            "overlapping_task_families": [],
        }
    intervals_by_family: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for episode in timed:
        intervals_by_family[episode.task_family].append(
            (
                float(episode.planned_open_offset_seconds),
                float(episode.planned_close_offset_seconds),
            )
        )
    overlapping_families = []
    for family, intervals in intervals_by_family.items():
        if any(
            first_start < second_end and second_start < first_end
            for index, (first_start, first_end) in enumerate(intervals)
            for second_start, second_end in intervals[index + 1 :]
        ):
            overlapping_families.append(family)
    first_open = min(float(episode.planned_open_offset_seconds) for episode in timed)
    last_close = max(float(episode.planned_close_offset_seconds) for episode in timed)
    overlapping_families.sort()
    return {
        "planned_first_open_offset_seconds": first_open,
        "planned_last_close_offset_seconds": last_close,
        "planned_offset_span_seconds": last_close - first_open,
        "families_with_overlapping_traces": len(overlapping_families),
        "overlapping_task_families": overlapping_families,
    }


def dry_run_summary(
    episodes: list[ReplayEpisode],
    errors: list[dict[str, Any]],
    *,
    source_run_id: str,
    run_id: str,
    rows: int,
    pace: str = "original",
) -> dict[str, Any]:
    total_counts: Counter[str] = Counter()
    task_reports = []
    for episode in episodes:
        client = _CaptureClient()
        _record_episode(
            client,
            episode,
            agent_uuid="dry-run-agent",
            source_run_id=source_run_id,
            run_id=run_id,
        )
        counts = _event_counts(client.agent.trace.calls)
        total_counts.update(counts)
        task_reports.append(
            {
                "task_id": episode.row["task_id"],
                "trial": episode.row["trial"],
                "started_at": episode.row["started_at"],
                "task_family": episode.task_family,
                "planned_open_offset_seconds": episode.planned_open_offset_seconds,
                "planned_close_offset_seconds": episode.planned_close_offset_seconds,
                "event_count": sum(counts.values()),
                "event_type_counts": dict(sorted(counts.items())),
            }
        )
    return {
        "source_run_id": source_run_id,
        "run_id": run_id,
        "pace": pace,
        "rows": rows,
        "valid_rows": len(episodes),
        "total_events": sum(total_counts.values()),
        "events_by_type": dict(sorted(total_counts.items())),
        **_planned_time_summary(episodes),
        "tasks": task_reports,
        "transcript_errors": errors,
        "unparseable_transcripts": [
            error
            for error in errors
            if error.get("file") == "transcripts-teacher.jsonl"
            or "transcript" in str(error.get("error", "")).lower()
            and "tool result" not in str(error.get("error", "")).lower()
        ],
        "unmatched_tool_results": [
            error
            for error in errors
            if "tool result" in str(error.get("error", "")).lower()
            or "matching saved tool result" in str(error.get("error", "")).lower()
        ],
    }


def _run_replay(
    client: Any,
    episodes: list[ReplayEpisode],
    *,
    agent_uuid: str,
    source_run_id: str,
    run_id: str,
    concurrency: int,
    pace: str = "original",
    clock: Callable[[], float] = monotonic,
    sleep_fn: Callable[[float], None] = sleep,
) -> list[dict[str, Any]]:
    replay_started_at = clock()

    def replay(episode: ReplayEpisode) -> dict[str, Any]:
        row = episode.row
        planned_open_offset = float(episode.planned_open_offset_seconds or 0.0)
        planned_close_offset = float(episode.planned_close_offset_seconds or 0.0)
        actual_open_offset = None
        try:
            if pace == "original":
                _wait_until(
                    replay_started_at + planned_open_offset,
                    clock=clock,
                    sleep_fn=sleep_fn,
                )
            actual_open_offset = round(clock() - replay_started_at, 6)
            trace_uuid = _record_episode(
                client,
                episode,
                agent_uuid=agent_uuid,
                source_run_id=source_run_id,
                run_id=run_id,
                close_at_offset_seconds=(planned_close_offset if pace == "original" else None),
                replay_started_at=replay_started_at,
                clock=clock,
                sleep_fn=sleep_fn,
            )
            if not trace_uuid:
                raise RuntimeError("Trace creation returned no UUID")
            actual_close_offset = round(clock() - replay_started_at, 6)
            return {
                "task_id": row["task_id"],
                "trial": row["trial"],
                "task_family": episode.task_family,
                "trace_uuid": trace_uuid,
                "passed": bool(row.get("succeeded")),
                "planned_open_offset_seconds": planned_open_offset,
                "actual_open_offset_seconds": actual_open_offset,
                "planned_close_offset_seconds": planned_close_offset,
                "actual_close_offset_seconds": actual_close_offset,
                "close_drift_seconds": round(
                    abs(actual_close_offset - planned_close_offset),
                    6,
                ),
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "task_id": row["task_id"],
                "trial": row["trial"],
                "task_family": episode.task_family,
                "trace_uuid": None,
                "passed": False,
                "error_type": type(exc).__name__,
                "planned_open_offset_seconds": planned_open_offset,
                "actual_open_offset_seconds": actual_open_offset,
                "planned_close_offset_seconds": planned_close_offset,
                "actual_close_offset_seconds": None,
                "close_drift_seconds": None,
            }

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        return list(executor.map(replay, episodes))


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-run-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--pace", choices=("original", "fast"), default="original")
    parser.add_argument("--compile-timeout", type=float, default=10_800)
    parser.add_argument("--concurrency", type=int, default=12)
    parser.add_argument(
        "--results-root",
        default=str(Path(__file__).resolve().parent / "results"),
    )
    args = parser.parse_args()
    if args.compile_timeout <= 0:
        parser.error("--compile-timeout must be greater than zero")
    if args.concurrency < 1:
        parser.error("--concurrency must be at least one")
    if Path(args.source_run_id).name != args.source_run_id or args.source_run_id in {".", ".."}:
        parser.error("--source-run-id must be a single directory name")
    if Path(args.run_id).name != args.run_id or args.run_id in {".", ".."}:
        parser.error("--run-id must be a single directory name")
    results_root = Path(args.results_root)
    source_dir = results_root / args.source_run_id
    output_dir = results_root / args.run_id
    resolved_source = source_dir.resolve()
    resolved_output = output_dir.resolve()
    if (
        resolved_source == resolved_output
        or resolved_source in resolved_output.parents
        or resolved_output in resolved_source.parents
    ):
        parser.error("The source and output result directories must be separate.")
    try:
        episodes, errors, row_count = load_replay_episodes(source_dir)
    except (OSError, ReplayInputError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    if args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=False)
        report = dry_run_summary(
            episodes,
            errors,
            source_run_id=args.source_run_id,
            run_id=args.run_id,
            rows=row_count,
            pace=args.pace,
        )
        _write_json(output_dir / "replay-dry-run.json", report)
        print(json.dumps(report, indent=2, sort_keys=True))
        if errors:
            raise SystemExit(1)
        return
    if errors:
        parser.error("The source run has invalid transcripts. Use --dry-run to list the errors.")

    try:
        config = load_config(require_zep=True, require_openai=False)
    except RuntimeError as exc:
        parser.error(str(exc))
    client = zep_client(config.api_key, config.api_url)
    output_dir.mkdir(parents=True, exist_ok=False)
    families = sorted({episode.task_family for episode in episodes})
    source_manifest = json.loads((source_dir / "manifest-teacher.json").read_text(encoding="utf-8"))
    source_agent_uuids = sorted(
        {
            str(episode.row.get("agent_uuid") or source_manifest.get("agent_uuid") or "")
            for episode in episodes
        }
        - {""}
    )
    agent = ensure_agent(
        client,
        "appworld-replay",
        run_id=args.run_id,
        compilation_mode="on_close",
        task_families=[],
    )
    git_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=Path(__file__).resolve().parents[2],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    manifest = {
        "run_id": args.run_id,
        "source_run_id": args.source_run_id,
        "source_agent_uuids": source_agent_uuids,
        "agent_uuid": agent["agent_uuid"],
        "family_scope": "scenario",
        "task_ids": [episode.row["task_id"] for episode in episodes],
        "compilation_mode": "on_close",
        "pace": args.pace,
        "max_close_drift_seconds": None,
        **_planned_time_summary(episodes),
        "arguments": vars(args),
        "git_commit": git_commit,
    }
    manifest_path = output_dir / "manifest-replay.json"
    _write_json(manifest_path, manifest)
    results = _run_replay(
        client,
        episodes,
        agent_uuid=agent["agent_uuid"],
        source_run_id=args.source_run_id,
        run_id=args.run_id,
        concurrency=args.concurrency,
        pace=args.pace,
    )
    close_drifts = [
        float(result["close_drift_seconds"])
        for result in results
        if result.get("close_drift_seconds") is not None
    ]
    manifest["max_close_drift_seconds"] = max(close_drifts, default=0.0)
    _write_json(manifest_path, manifest)
    traces_path = output_dir / "traces-replay.jsonl"
    traces_path.write_text(
        "".join(json.dumps(result, sort_keys=True) + "\n" for result in results),
        encoding="utf-8",
    )
    summary: dict[str, Any] = {
        "stage": "replay",
        "agent_uuid": agent["agent_uuid"],
        "source_run_id": args.source_run_id,
        "rows": len(results),
        "failed_replays": sum(bool(result.get("error_type")) for result in results),
    }
    if summary["failed_replays"]:
        _write_json(output_dir / "replay.json", summary)
        raise SystemExit(1)
    passed = {
        family: sum(
            bool(episode.row.get("succeeded"))
            for episode in episodes
            if episode.task_family == family
        )
        for family in families
    }
    compile_wait = wait_for_compilation(
        client,
        agent["agent_uuid"],
        passed,
        ceiling_seconds=args.compile_timeout,
    )
    summary["compile_wait"] = compile_wait
    try:
        snapshot = compile_snapshot(
            client,
            agent["agent_uuid"],
            families,
            output_dir / "skills",
            traces_path=traces_path,
            wait=compile_wait,
        )
        summary["snapshot_sha256"] = snapshot["sha256"]
        summary["funnel"] = write_funnel(
            output_dir / "skills",
            families,
            snapshot,
            traces_path,
        )
    except Exception as exc:
        export_error: dict[str, Any] = {"type": type(exc).__name__}
        if isinstance(exc, SnapshotExportError):
            export_error["failures"] = exc.failures
        summary["snapshot_export_error"] = export_error
        _write_json(output_dir / "replay.json", summary)
        raise SystemExit(1) from exc
    _write_json(output_dir / "replay.json", summary)


if __name__ == "__main__":
    main()
