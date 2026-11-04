from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

from zep_cloud.core.api_error import ApiError
from zep_cloud.types.agent_trace_close_verifier import AgentTraceCloseVerifier

from .ledger import JsonlLedger

DEFAULT_REPLAY_CONCURRENCY = 4
MAX_API_CALL_ATTEMPTS = 10
LOGGER = logging.getLogger(__name__)
SHARED_TRANSFORM_SCRIPT = Path(__file__).resolve().parents[2] / "scripts/add_tool_context.py"
SHARED_TRANSFORM_SOURCE = "public Agent Skills harness"
TRANSFORM_TEXT = (
    "The transform script scripts/add_tool_context.py transforms the replay file before the replay. It copies "
    "metadata.tool_name into context.tools, and it sets tool_call_id to the event_id of the "
    "call event, on the call event and on its result event: a turn is the run of events up to and including a "
    "checkpoint event, and in a turn the k-th result pairs with the k-th call that is not finish. The replay then "
    "sends each event with the same event_type, content, metadata, sequence, context, tool_call_id, and sources as "
    "the transformed event, and the source event_time as occurred_at. The replay also sets metadata.error = true on "
    "a tool_result event when its content matches the error rule in tool_result_content_is_error."
)
NATIVE_TRANSFORM_TEXT = (
    "The replay file already has context.tools and tool_call_id on every tool event, as the teacher recorded them. "
    "The shared transform did not run; the replay copies the file and checks that each tool_result tool_call_id "
    "matches exactly one tool_call with the same context.tools names. The replay sends each event with the same "
    "event_type, content, metadata, sequence, context, tool_call_id, and sources, and the source event_time as "
    "occurred_at. The replay also sets metadata.error = true on a tool_result event when its content matches the "
    "error rule in tool_result_content_is_error."
)
_GYM_ERROR_TEXT = re.compile(
    r"\bnot found\b|\balready a member\b|\byou must set\b|\bdoes not exist\b",
    re.IGNORECASE,
)


def _rate_limit_delay(error: ApiError) -> int:
    headers = error.headers or {}
    retry_after = next((value for key, value in headers.items() if key.lower() == "retry-after"), None)
    try:
        seconds = int(retry_after)
    except (TypeError, ValueError):
        seconds = 60
    if seconds < 0:
        seconds = 60
    return seconds + 1


def _call_with_rate_limit_retry(
    call: Callable[..., Any],
    *args: Any,
    on_retry: Callable[[], None] | None = None,
    **kwargs: Any,
) -> Any:
    for attempt in range(1, MAX_API_CALL_ATTEMPTS + 1):
        try:
            return call(*args, **kwargs)
        except ApiError as error:
            if error.status_code != 429 or attempt == MAX_API_CALL_ATTEMPTS:
                raise
            delay = _rate_limit_delay(error)
            if on_retry is not None:
                on_retry()
            LOGGER.warning(
                "Zep replay API returned HTTP 429; retry %d of %d in %d seconds.",
                attempt,
                MAX_API_CALL_ATTEMPTS - 1,
                delay,
            )
            time.sleep(delay)
    raise RuntimeError("The replay API retry loop ended without a result.")


def run_shared_transform(source: Path, target: Path, *, script: Path = SHARED_TRANSFORM_SCRIPT) -> dict[str, Any]:
    """Run the shared transform script on the replay file and return its record for the manifest."""
    if not script.exists():
        raise FileNotFoundError(f"The shared transform script is not on this checkout: {script}")
    result = subprocess.run(
        [sys.executable, str(script), str(source), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"The shared transform failed (exit {result.returncode}): {result.stderr.strip()[-500:]}")
    stats = json.loads(result.stderr.strip().splitlines()[-1])
    output_sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
    if output_sha256 != stats.get("output_sha256"):
        raise RuntimeError("The transformed file does not have the SHA-256 that the script reported")
    record = _transform_record(source, target, script)
    record["output_sha256"] = output_sha256
    record["stats"] = {key: value for key, value in stats.items() if key != "output_sha256"}
    return record


def _transform_record(source: Path, target: Path, script: Path) -> dict[str, Any]:
    return {
        "script": str(script.relative_to(script.parents[2])),
        "script_sha256": hashlib.sha256(script.read_bytes()).hexdigest(),
        "script_source": SHARED_TRANSFORM_SOURCE,
        "input_file": str(source),
        "input_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "output_file": str(target),
    }


@dataclass(frozen=True, slots=True)
class ReplayRow:
    source_trace_uuid: str
    objective: str
    task_family: str
    metadata: dict[str, Any]
    events: tuple[dict[str, Any], ...]
    run_row: dict[str, Any]
    closed_at: str
    passed: bool
    verified: bool
    infra_failed: bool
    outcome: str | None = None

    @property
    def highest_sequence(self) -> int:
        return max((int(event["sequence"]) for event in self.events), default=0)


def event_payload(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload_json")
    if isinstance(payload, dict):
        return payload
    raw = event.get("payload")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    if event.get("payload_json") is None and raw is None and "content" in event:
        return {}
    raise ValueError(f"Event {event.get('event_id', '?')} has no JSON payload")


_TOOL_EVENT_TYPES = {"tool_call", "tool_result"}


def classify_replay_tool_context(source: Path) -> tuple[str, dict[str, int]]:
    counts = {
        "tool_calls": 0,
        "tool_results": 0,
        "tool_events": 0,
        "context_tools_events": 0,
        "tool_call_id_events": 0,
        "native_events": 0,
        "legacy_events": 0,
        "partial_events": 0,
    }
    with source.open(encoding="utf-8") as replay_file:
        for line in replay_file:
            if not line.strip():
                continue
            record = json.loads(line)
            for event in record.get("events", []):
                event_type = event.get("event_type")
                if event_type not in _TOOL_EVENT_TYPES:
                    continue
                counts["tool_events"] += 1
                counts["tool_calls"] += event_type == "tool_call"
                counts["tool_results"] += event_type == "tool_result"
                payload = event_payload(event)
                context = event.get("context", payload.get("context"))
                tools = context.get("tools") if isinstance(context, dict) else None
                has_tools_field = isinstance(context, dict) and "tools" in context
                has_tools = isinstance(tools, list) and bool(tools)
                has_tool_call_id_field = "tool_call_id" in event or "tool_call_id" in payload
                tool_call_id = event.get("tool_call_id", payload.get("tool_call_id"))
                has_tool_call_id = isinstance(tool_call_id, str) and bool(tool_call_id.strip())
                has_any_tool_context = has_tools_field or has_tool_call_id_field
                has_native_tool_context = has_tools and has_tool_call_id
                counts["context_tools_events"] += has_tools_field
                counts["tool_call_id_events"] += has_tool_call_id_field
                counts["native_events"] += has_native_tool_context
                counts["legacy_events"] += not has_any_tool_context
                counts["partial_events"] += has_any_tool_context and not has_native_tool_context
    if counts["tool_events"] == 0 or counts["legacy_events"] == counts["tool_events"]:
        return "legacy", counts
    if counts["native_events"] == counts["tool_events"]:
        return "native", counts
    return "mixed", counts


def _native_tool_names(event: dict[str, Any], payload: dict[str, Any]) -> tuple[str, ...]:
    context = event.get("context", payload.get("context"))
    tools = context.get("tools") if isinstance(context, dict) else None
    event_id = event.get("event_id", "?")
    if not isinstance(tools, list) or not tools:
        raise SystemExit(f"Native tool context validation failed at event {event_id}: context.tools is empty.")
    names = []
    for tool in tools:
        name = tool.get("name") if isinstance(tool, dict) else None
        if not isinstance(name, str) or not name.strip():
            raise SystemExit(
                f"Native tool context validation failed at event {event_id}: context.tools has no tool name."
            )
        names.append(name)
    return tuple(names)


def _validate_native_tool_context(source: Path) -> None:
    with source.open(encoding="utf-8") as replay_file:
        for line in replay_file:
            if not line.strip():
                continue
            record = json.loads(line)
            trace_uuid = str(record.get("trajectory", {}).get("uuid") or "?")
            calls: dict[str, tuple[tuple[str, ...], str]] = {}
            results: list[tuple[str, tuple[str, ...], str]] = []
            for event in record.get("events", []):
                event_type = event.get("event_type")
                if event_type not in _TOOL_EVENT_TYPES:
                    continue
                payload = event_payload(event)
                event_id = str(event.get("event_id") or "?")
                tool_call_id = event.get("tool_call_id", payload.get("tool_call_id"))
                if not isinstance(tool_call_id, str) or not tool_call_id.strip():
                    raise SystemExit(
                        f"Native tool context validation failed for Trace {trace_uuid} at event {event_id}: "
                        "tool_call_id is empty."
                    )
                names = _native_tool_names(event, payload)
                if event_type == "tool_call":
                    if tool_call_id in calls:
                        previous_event_id = calls[tool_call_id][1]
                        raise SystemExit(
                            f"Native tool context validation failed for Trace {trace_uuid} at event {event_id}: "
                            f"tool_call_id is also used by tool_call event {previous_event_id}."
                        )
                    calls[tool_call_id] = (names, event_id)
                else:
                    results.append((tool_call_id, names, event_id))
            for tool_call_id, names, event_id in results:
                matches = [
                    call_id
                    for call_id, (call_names, _) in calls.items()
                    if call_id == tool_call_id and call_names == names
                ]
                if len(matches) != 1:
                    raise SystemExit(
                        f"Native tool context validation failed for Trace {trace_uuid} at event {event_id}: "
                        f"tool_call_id matches {len(matches)} tool_call events with the same context.tools names."
                    )


def prepare_replay_file(source: Path, target: Path) -> tuple[dict[str, Any], str, dict[str, int]]:
    tool_context_source, counts = classify_replay_tool_context(source)
    if tool_context_source == "mixed":
        raise SystemExit(
            "The replay file has mixed tool context: "
            f"tool_events={counts['tool_events']}, tool_calls={counts['tool_calls']}, "
            f"tool_results={counts['tool_results']}, native_events={counts['native_events']}, "
            f"legacy_events={counts['legacy_events']}, partial_events={counts['partial_events']}, "
            f"context_tools_events={counts['context_tools_events']}, "
            f"tool_call_id_events={counts['tool_call_id_events']}."
        )
    if tool_context_source == "native":
        _validate_native_tool_context(source)
        if not SHARED_TRANSFORM_SCRIPT.exists():
            raise FileNotFoundError(f"The shared transform script is not on this checkout: {SHARED_TRANSFORM_SCRIPT}")
        shutil.copyfile(source, target)
        record = _transform_record(source, target, SHARED_TRANSFORM_SCRIPT)
        record["output_sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
        return record, tool_context_source, counts
    return run_shared_transform(source, target), "transform", counts


def filter_replay_export(source: Path, target: Path, task_families: list[str]) -> dict[str, int]:
    """Write only the exported Traces for the selected task families."""
    selected_families = set(task_families)
    found_families: set[str] = set()
    source_rows = 0
    selected_rows = 0
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open() as source_file, target.open("w") as target_file:
        for line in source_file:
            if not line.strip():
                continue
            source_rows += 1
            record = json.loads(line)
            trajectory = record.get("trajectory") or {}
            run_row = record.get("run_row") or {}
            task_family = trajectory.get("task_family") or run_row.get("task_family")
            if task_family in selected_families:
                target_file.write(line if line.endswith("\n") else f"{line}\n")
                found_families.add(str(task_family))
                selected_rows += 1
    missing_families = selected_families - found_families
    if missing_families:
        target.unlink(missing_ok=True)
        raise ValueError(f"The replay export has no Traces for task families: {sorted(missing_families)}")
    if not selected_families:
        target.unlink(missing_ok=True)
        raise ValueError("Select at least one task family for replay")
    return {"source_rows": source_rows, "selected_rows": selected_rows}


def _closed_at(trajectory: dict[str, Any]) -> str:
    for key in ("closed_at", "abandoned_at", "updated_at", "created_at"):
        value = trajectory.get(key)
        if value:
            return str(value)
    return ""


def load_replay_rows(path: Path) -> list[ReplayRow]:
    """Read the exported Traces and return them in the original close order."""
    rows = []
    for line_number, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        record = json.loads(line)
        trajectory = record["trajectory"]
        run_row = record.get("run_row") or {}
        metadata = dict(trajectory.get("metadata") or {})
        outcome = trajectory.get("outcome") or metadata.get("outcome") or run_row.get("outcome")
        events = tuple(
            {
                "event_id": str(event.get("event_id") or ""),
                "event_time": (
                    event.get("occurred_at") if event.get("occurred_at") is not None else event.get("event_time")
                ),
                "event_type": str(event["event_type"]),
                "sequence": int(event["sequence"]),
                "payload": {
                    **event_payload(event),
                    **{
                        key: event[key]
                        for key in ("content", "metadata", "context", "tool_call_id", "sources")
                        if key in event
                    },
                },
            }
            for event in sorted(record.get("events", []), key=lambda item: int(item["sequence"]))
        )
        if not events:
            raise ValueError(f"Line {line_number}: the Trace {trajectory.get('uuid')} has no events")
        if "infra_failed" in run_row:
            infra_failed = bool(run_row["infra_failed"])
        else:
            infra_failed = bool(trajectory.get("abandoned_at"))
        verified = (
            bool(run_row["verified"])
            if "verified" in run_row
            else trajectory.get("verification") not in (None, "", "unverified")
        )
        passed = bool(run_row["passed"]) if "passed" in run_row else trajectory.get("outcome") == "succeeded"
        task_family = str(trajectory.get("task_family") or run_row.get("task_family") or "")
        if not task_family:
            raise ValueError(f"Line {line_number}: the Trace {trajectory.get('uuid')} has no task family")
        rows.append(
            ReplayRow(
                source_trace_uuid=str(trajectory["uuid"]),
                objective=str(trajectory.get("objective") or ""),
                task_family=task_family,
                metadata=metadata,
                events=events,
                run_row=run_row,
                closed_at=_closed_at(trajectory),
                passed=passed,
                verified=verified,
                infra_failed=infra_failed,
                outcome=str(outcome) if outcome else None,
            )
        )
    rows.sort(key=lambda row: (row.closed_at, row.source_trace_uuid))
    return rows


def event_kwargs(event: dict[str, Any]) -> dict[str, Any]:
    """The append_event keyword arguments of one replayed event. The payload is the output of the shared transform."""
    payload = event["payload"]
    metadata = payload.get("metadata")
    metadata = dict(metadata) if isinstance(metadata, dict) else None
    if event["event_type"] == "tool_result" and tool_result_content_is_error(payload.get("content")):
        metadata = dict(metadata or {})
        metadata["error"] = True
    kwargs: dict[str, Any] = {
        "event_type": event["event_type"],
        "content": payload.get("content"),
        "metadata": metadata or None,
        "sequence": int(event["sequence"]),
    }
    if event.get("event_time") is not None:
        kwargs["occurred_at"] = str(event["event_time"])
    context = payload.get("context")
    if isinstance(context, dict) and context:
        kwargs["context"] = context
    tool_call_id = payload.get("tool_call_id")
    if tool_call_id:
        kwargs["tool_call_id"] = str(tool_call_id)
    if "sources" in payload:
        kwargs["sources"] = payload["sources"]
    return kwargs


def _has_value(value: Any) -> bool:
    return value not in (None, False, "", {}, [])


def tool_result_content_is_error(content: str | None) -> bool:
    """Match JSON error fields, error prefixes, and plain-text Gym errors."""
    text = (content or "").strip()
    try:
        body = json.loads(text)
    except ValueError:
        body = None
    if isinstance(body, dict):
        if _has_value(body.get("error")):
            return True
        return set(body) == {"detail"} and _has_value(body["detail"])
    if body is not None:
        return False
    if text.startswith("❌"):
        return True
    if text.lstrip("❌ ").startswith(("Error", "Validation errors:")):
        return True
    return bool(_GYM_ERROR_TEXT.search(text))


def close_action(row: ReplayRow, new_trace_uuid: str) -> tuple[str, dict[str, Any]]:
    """The close or abandon call of a replayed Trace. It follows TrajectoryRecorder.close."""
    if row.infra_failed:
        return "abandon", {
            "highest_accepted_sequence": row.highest_sequence,
            "reason": "infra_failed",
            "verification": "none",
        }
    outcome = row.outcome or ("succeeded" if row.passed and row.verified else "failed")
    if outcome not in {"succeeded", "failed"}:
        raise ValueError(f"The source Trace has an unsupported outcome: {outcome}")
    return "close", {
        "outcome": outcome,
        "reason": None if row.verified else "The episode ended before verification.",
        "verification": "external" if row.verified else "none",
        "verifier": (
            AgentTraceCloseVerifier(
                verifier_id="eog-verifier",
                class_="harness",
                outcome=outcome,
                evidence_reference=new_trace_uuid,
            )
            if row.verified
            else None
        ),
    }


def _trace_uuid(trace: Any) -> str:
    return str(getattr(trace, "uuid", None) or getattr(trace, "uuid_", "") or "")


def replay_trace(
    client: Any,
    agent_uuid: str,
    row: ReplayRow,
    *,
    run_id: str,
    on_rate_limit_retry: Callable[[], None] | None = None,
) -> dict[str, Any]:
    trace = _call_with_rate_limit_retry(
        client.agent.trace.create,
        agent_uuid,
        on_retry=on_rate_limit_retry,
        objective=row.objective[:512],
        task_family=row.task_family,
        metadata=row.metadata or None,
    )
    new_uuid = _trace_uuid(trace)
    if not new_uuid:
        raise RuntimeError("Zep did not return a Trace UUID")
    for event in row.events:
        _call_with_rate_limit_retry(
            client.agent.trace.append_event,
            agent_uuid,
            new_uuid,
            on_retry=on_rate_limit_retry,
            **event_kwargs(event),
        )
    action, kwargs = close_action(row, new_uuid)
    _call_with_rate_limit_retry(
        getattr(client.agent.trace, action),
        agent_uuid,
        new_uuid,
        on_retry=on_rate_limit_retry,
        **kwargs,
    )
    closed_at = datetime.now(UTC).isoformat()
    record = dict(row.run_row)
    record["trace_uuid"] = new_uuid
    record["source_trace_uuid"] = row.source_trace_uuid
    record["replay_run_id"] = run_id
    record.setdefault("task_family", row.task_family)
    record["source_passed"] = record.get("passed", row.passed)
    record["passed"] = row.passed if row.verified and not row.infra_failed else False
    record.setdefault("verified", row.verified)
    record.setdefault("infra_failed", row.infra_failed)
    record["events"] = len(row.events)
    record["close_action"] = action
    record["source_closed_at"] = row.closed_at
    record["closed_at"] = closed_at
    return record


def replay_traces(
    client: Any,
    agent_uuid: str,
    rows: list[ReplayRow],
    *,
    run_id: str,
    traces_path: Path,
    concurrency: int = DEFAULT_REPLAY_CONCURRENCY,
) -> dict[str, Any]:
    """Replay the rows in their order with at most `concurrency` Traces in flight. Write traces.jsonl in that order."""
    if concurrency < 1:
        raise ValueError("The replay concurrency must be at least 1")
    ledger = JsonlLedger(traces_path)
    errors: list[dict[str, Any]] = []
    replayed = 0
    events = 0
    rate_limit_retries = 0
    retry_lock = threading.Lock()

    def count_rate_limit_retry() -> None:
        nonlocal rate_limit_retries
        with retry_lock:
            rate_limit_retries += 1

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [
            pool.submit(
                replay_trace,
                client,
                agent_uuid,
                row,
                run_id=run_id,
                on_rate_limit_retry=count_rate_limit_retry,
            )
            for row in rows
        ]
        for row, future in zip(rows, futures, strict=True):
            try:
                record = future.result()
            except Exception as error:
                errors.append(
                    {
                        "source_trace_uuid": row.source_trace_uuid,
                        "error_class": type(error).__name__,
                        "message": str(error)[:500],
                    }
                )
                continue
            ledger.append(record)
            replayed += 1
            events += record["events"]
    return {
        "traces": replayed,
        "events": events,
        "errors": errors,
        "concurrency": concurrency,
        "rate_limit_retries": rate_limit_retries,
    }


def git_commit(repo: Path) -> str:
    try:
        return subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def replay_manifest(
    *,
    run_id: str,
    compiler_label: str,
    replay_file: Path,
    rows: list[ReplayRow],
    concurrency: int,
    agent_record: dict[str, str],
    repo: Path,
    transform: dict[str, Any] | None = None,
    tool_context_source: str = "transform",
    task_scope: str = "split",
    selected_replay_file: Path | None = None,
    task_families: list[str] | None = None,
    development_commit: str | None = None,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "stage": "recompile",
        "task_scope": task_scope,
        "compiler_label": compiler_label,
        "harness_git_commit": git_commit(repo),
        "created_utc": datetime.now(UTC).isoformat(),
        "replay_file": str(replay_file),
        "replay_file_sha256": hashlib.sha256(replay_file.read_bytes()).hexdigest(),
        "selected_replay_file": str(selected_replay_file) if selected_replay_file else None,
        "selected_replay_file_sha256": (
            hashlib.sha256(selected_replay_file.read_bytes()).hexdigest() if selected_replay_file else None
        ),
        "development_commit": development_commit,
        "tool_context_source": tool_context_source,
        "replay_concurrency": concurrency,
        "replay_order": (
            "The Traces start in the original close order (trajectory.closed_at). With a concurrency above 1, "
            "the new Traces close in the order that the API completes them; traces.jsonl records closed_at."
        ),
        "transform": NATIVE_TRANSFORM_TEXT if tool_context_source == "native" else TRANSFORM_TEXT,
        "transform_script": transform,
        "sdk_version": version("zep-cloud"),
        "source_traces": len(rows),
        "source_traces_passed": sum(row.passed and row.verified for row in rows),
        "source_traces_infra_failed": sum(row.infra_failed for row in rows),
        "source_events": sum(len(row.events) for row in rows),
        "task_families": task_families or sorted({row.task_family for row in rows}),
        "agent": agent_record,
    }
