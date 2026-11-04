#!/usr/bin/env python3
"""Add context.tools and tool_call_id to exported Agent Skills trace events.

Input is a JSONL export in which each row has `trajectory`, `run_row`, and
`events`. Each event has `event_id`, `event_type`, `sequence`, `payload`
(the payload as a JSON string), and `payload_json` (the same payload as an
object). The tool name of a `tool_call` or `tool_result` event is only in
`payload.metadata.tool_name`.

The script writes a copy of each row. For each tool event, the payload gets:

- `context.tools = [{"name": <metadata.tool_name>}]`
- `tool_call_id`: the `event_id` of the call event. A result gets the
  `event_id` of its call.

A turn is the run of events up to and including a `checkpoint` event. In a
turn, the calls come before the results, and the results are in call order.
The k-th result of the turn pairs with the k-th call that is not a `finish`
call. A `finish` call has no result. The script stops with an error when a
turn does not follow this shape, when the tool names of a pair are not equal,
or when an event already has a different `context.tools` or `tool_call_id`.

The script does not change `metadata`, `content`, or any other field.
`content_digest` stays the digest of the source event. Append the copy with
the API to get new digests.

Usage:
    add_tool_context.py INPUT.jsonl OUTPUT.jsonl
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from collections import Counter
from typing import Any

FINISH_TOOL = "finish"
MAX_TOOL_CALL_ID_BYTES = 256


class TransformError(ValueError):
    pass


def _payload(event: dict[str, Any]) -> dict[str, Any]:
    from_string = json.loads(event["payload"])
    if from_string != event["payload_json"]:
        raise TransformError(f"event {event['event_id']}: payload and payload_json differ")
    return from_string


def _tool_name(event: dict[str, Any], payload: dict[str, Any]) -> str:
    name = (payload.get("metadata") or {}).get("tool_name")
    if not isinstance(name, str) or not name.strip():
        raise TransformError(f"event {event['event_id']}: no metadata.tool_name")
    return name


def _set_tool_fields(event: dict[str, Any], payload: dict[str, Any], name: str, call_id: str) -> None:
    if not call_id.strip() or len(call_id.encode()) > MAX_TOOL_CALL_ID_BYTES:
        raise TransformError(f"event {event['event_id']}: call ID is blank or longer than 256 bytes")
    tools = [{"name": name}]
    context = payload.get("context")
    if context is None:
        context = {}
    elif not isinstance(context, dict):
        raise TransformError(f"event {event['event_id']}: context is not an object")
    if "tools" in context and context["tools"] != tools:
        raise TransformError(f"event {event['event_id']}: context.tools differs from metadata.tool_name")
    if "tool_call_id" in payload and payload["tool_call_id"] != call_id:
        raise TransformError(f"event {event['event_id']}: tool_call_id differs from the derived call ID")
    context["tools"] = tools
    payload["context"] = context
    payload["tool_call_id"] = call_id
    event["payload_json"] = payload
    event["payload"] = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _turns(events: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    turns: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for event in events:
        current.append(event)
        if event["event_type"] == "checkpoint":
            turns.append(current)
            current = []
    if current:
        turns.append(current)
    return turns


def transform_row(row: dict[str, Any], stats: Counter) -> dict[str, Any]:
    events = sorted(row["events"], key=lambda event: event["sequence"])
    sequences = [event["sequence"] for event in events]
    if len(set(sequences)) != len(sequences):
        raise TransformError(f"trajectory {row['trajectory']['uuid']}: duplicate sequence")
    for turn in _turns(events):
        calls: list[tuple[dict[str, Any], dict[str, Any], str]] = []
        results: list[tuple[dict[str, Any], dict[str, Any], str]] = []
        for event in turn:
            if event["event_type"] == "tool_call":
                if results:
                    raise TransformError(f"event {event['event_id']}: tool_call after a tool_result in one turn")
                payload = _payload(event)
                calls.append((event, payload, _tool_name(event, payload)))
            elif event["event_type"] == "tool_result":
                payload = _payload(event)
                results.append((event, payload, _tool_name(event, payload)))
        paired_calls = [call for call in calls if call[2] != FINISH_TOOL]
        if len(paired_calls) != len(results):
            raise TransformError(
                f"trajectory {row['trajectory']['uuid']}: a turn has {len(paired_calls)} calls "
                f"and {len(results)} results"
            )
        for (call, _call_payload, call_name), (
            result,
            result_payload,
            result_name,
        ) in zip(paired_calls, results, strict=True):
            if call_name != result_name:
                raise TransformError(
                    f"event {result['event_id']}: result tool {result_name!r} does not match call tool {call_name!r}"
                )
            _set_tool_fields(result, result_payload, result_name, call["event_id"])
            stats["pairs"] += 1
        for call, call_payload, call_name in calls:
            _set_tool_fields(call, call_payload, call_name, call["event_id"])
            stats["calls"] += 1
            if call_name == FINISH_TOOL:
                stats["finish_calls"] += 1
        stats["results"] += len(results)
        stats["turns"] += 1
    stats["traces"] += 1
    stats["events"] += len(events)
    row["events"] = events
    return row


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    if os.path.exists(argv[2]) and os.path.samefile(argv[1], argv[2]):
        raise TransformError("the output path must not be the input path")
    stats: Counter = Counter()
    digest = hashlib.sha256()
    with (
        open(argv[1], encoding="utf-8") as source,
        open(argv[2], "w", encoding="utf-8") as target,
    ):
        for line in source:
            if not line.strip():
                continue
            row = transform_row(json.loads(line), stats)
            encoded = json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            digest.update(encoded.encode())
            target.write(encoded)
    print(
        json.dumps({**stats, "output_sha256": digest.hexdigest()}, sort_keys=True),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except TransformError as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
