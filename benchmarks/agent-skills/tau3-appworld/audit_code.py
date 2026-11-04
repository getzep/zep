from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from appworld_guard import BANNED_CODE_NAMES, GUARD_PREFIX, runtime_guard_matches

_BANNED_NAME_PATTERN = re.compile(
    r"(?<!\w)(?:"
    + "|".join(re.escape(name) for name in sorted(BANNED_CODE_NAMES, key=len, reverse=True))
    + r")(?!\w)",
    re.IGNORECASE,
)
_OPEN_CALL_PATTERN = re.compile(r"\bopen\s*\(", re.IGNORECASE)
_AUDIT_SUBSTRINGS = ("truth", "globals", "__")


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in {path} at line {line_number}: {exc}") from exc
        if isinstance(record, dict):
            records.append(record)
    return records


def _call_arguments(call: dict[str, Any]) -> dict[str, Any]:
    arguments = call.get("arguments")
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            decoded = json.loads(arguments)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _matching_patterns(code: str) -> list[str]:
    name_matches = list(_BANNED_NAME_PATTERN.finditer(code))
    allowed_matches = _allowed_name_matches(code, name_matches)
    matches = set()
    for match in name_matches:
        if match.start() not in allowed_matches:
            matches.add(f"name:{match.group().casefold()}")
    matches.update(f"marker:{marker.casefold()}" for marker in runtime_guard_matches(code))
    if _OPEN_CALL_PATTERN.search(code):
        matches.add("call:open(")
    lowered = code.casefold()
    matches.update(f"substring:{item}" for item in _AUDIT_SUBSTRINGS if item in lowered)
    return sorted(matches)


def _allowed_name_matches(code: str, name_matches: list[re.Match[str]]) -> set[int]:
    try:
        tree = ast.parse(code)
    except (RecursionError, SyntaxError, ValueError):
        return set()

    nodes = list(ast.walk(tree))
    attributes = [node for node in nodes if isinstance(node, ast.Attribute)]
    allowed_matches = set()
    for name in ("ex", "ev"):
        if not any(attribute.attr == name for attribute in attributes):
            allowed_matches.update(
                match.start() for match in name_matches if match.group().casefold() == name
            )

    compile_matches = [match for match in name_matches if match.group().casefold() == "compile"]
    compile_attributes = [attribute for attribute in attributes if attribute.attr == "compile"]
    if (
        not any(isinstance(node, ast.Name) and node.id == "compile" for node in nodes)
        and all(
            isinstance(attribute.value, ast.Name) and attribute.value.id == "re"
            for attribute in compile_attributes
        )
        and len(compile_matches) == len(compile_attributes)
    ):
        allowed_matches.update(match.start() for match in compile_matches)
    return allowed_matches


def _stage_from_artifact(path: Path, prefix: str) -> str:
    name = path.name
    if prefix == "transcripts-" and name.startswith(prefix):
        return name[len(prefix) :].removesuffix(".jsonl")
    if prefix == "simulation" and "-appworld-" in name:
        return name.split("-appworld-", 1)[0]
    raise ValueError(f"Cannot determine the stage for {path}")


def _arm_for_stage(
    run_dir: Path, stage: str, rows_by_stage: dict[str, list[dict[str, Any]]]
) -> str:
    for row in rows_by_stage.get(stage, []):
        if row.get("arm") is not None:
            return str(row["arm"])
    manifest_path = run_dir / f"manifest-{stage}.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        arm = manifest.get("arguments", {}).get("arm")
        if arm is not None:
            return str(arm)
    if stage.startswith("heldout-"):
        return stage.removeprefix("heldout-")
    return stage


def _tool_results(record: dict[str, Any]) -> dict[str, str]:
    results = {}
    messages = record.get("messages")
    if not isinstance(messages, list):
        return results
    for message in messages:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "tool":
            call_id = message.get("id") or message.get("call_id")
            output = message.get("content")
        elif message.get("type") == "function_call_output":
            call_id = message.get("call_id")
            output = message.get("output")
        else:
            continue
        if call_id is not None and isinstance(output, str):
            results[str(call_id)] = output
    return results


def _tool_blocks(record: dict[str, Any]) -> list[tuple[str | None, str, str]]:
    blocks = []
    messages = record.get("messages")
    if not isinstance(messages, list):
        return blocks
    results = _tool_results(record)
    for message in messages:
        if not isinstance(message, dict):
            continue
        calls = message.get("tool_calls")
        if not isinstance(calls, list):
            continue
        for call in calls:
            if not isinstance(call, dict) or call.get("name") != "execute_python":
                continue
            code = _call_arguments(call).get("code")
            if isinstance(code, str):
                call_id = call.get("id") or call.get("call_id")
                call_id_str = str(call_id) if call_id is not None else None
                response = results.get(call_id_str) if call_id_str is not None else None
                status = (
                    "blocked"
                    if response is not None and response.startswith(GUARD_PREFIX)
                    else "executed"
                )
                blocks.append((call_id_str, code, status))
    return blocks


def audit_run_directory(run_dir: Path) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    if not run_dir.is_dir():
        raise ValueError(f"Run directory does not exist: {run_dir}")

    result_files = sorted(run_dir.glob("*.jsonl"))
    rows_by_stage: dict[str, list[dict[str, Any]]] = {}
    for path in result_files:
        if path.name.startswith(("transcripts-", "traces-")):
            continue
        rows_by_stage[path.stem] = _load_jsonl(path)

    transcript_files = sorted(run_dir.glob("transcripts-*.jsonl"))
    simulation_files = sorted((run_dir / "simulations").rglob("*.json"))
    files_scanned = transcript_files + simulation_files
    unique_blocks: dict[tuple[str, str, str], dict[str, Any]] = {}
    anonymous_index = 0

    for path in files_scanned:
        is_transcript = path.name.startswith("transcripts-")
        stage = _stage_from_artifact(path, "transcripts-" if is_transcript else "simulation")
        arm = _arm_for_stage(run_dir, stage, rows_by_stage)
        records = _load_jsonl(path) if is_transcript else [json.loads(path.read_text("utf-8"))]
        for record_index, record in enumerate(records, 1):
            task_id = str(record.get("task_id") or "unknown")
            for block_index, (call_id, code, execution_status) in enumerate(
                _tool_blocks(record), 1
            ):
                if call_id is None:
                    anonymous_index += 1
                    identity = f"anonymous-{anonymous_index}"
                else:
                    identity = call_id
                code_hash = hashlib.sha256(code.encode()).hexdigest()
                key = (stage, task_id, f"{identity}:{code_hash}")
                source = path.relative_to(run_dir).as_posix()
                existing = unique_blocks.get(key)
                if existing is not None:
                    if source not in existing["sources"]:
                        existing["sources"].append(source)
                    continue
                unique_blocks[key] = {
                    "stage": stage,
                    "arm": arm,
                    "task_id": task_id,
                    "call_id": call_id,
                    "code": code,
                    "execution_status": execution_status,
                    "patterns": _matching_patterns(code),
                    "sources": [source],
                    "record_index": record_index,
                    "block_index": block_index,
                }

    arms: dict[str, dict[str, Any]] = {}
    for block in unique_blocks.values():
        arm_data = arms.setdefault(
            block["arm"],
            {
                "blocks_scanned": 0,
                "executed_blocks": 0,
                "blocked_blocks": 0,
                "matching_blocks": 0,
                "matching_executed_blocks": 0,
                "matching_blocked_blocks": 0,
                "pattern_counts": Counter(),
                "matching_blocks_detail": [],
                "tasks": {},
            },
        )
        task_data = arm_data["tasks"].setdefault(
            block["task_id"],
            {
                "blocks_scanned": 0,
                "executed_blocks": 0,
                "blocked_blocks": 0,
                "matching_blocks": 0,
                "matching_executed_blocks": 0,
                "matching_blocked_blocks": 0,
                "pattern_counts": Counter(),
                "matching_blocks_detail": [],
            },
        )
        arm_data["blocks_scanned"] += 1
        task_data["blocks_scanned"] += 1
        arm_data[f"{block['execution_status']}_blocks"] += 1
        task_data[f"{block['execution_status']}_blocks"] += 1
        if not block["patterns"]:
            continue
        arm_data["matching_blocks"] += 1
        task_data["matching_blocks"] += 1
        matching_key = f"matching_{block['execution_status']}_blocks"
        arm_data[matching_key] += 1
        task_data[matching_key] += 1
        detail = {
            key: block[key]
            for key in (
                "stage",
                "arm",
                "task_id",
                "call_id",
                "code",
                "execution_status",
                "patterns",
                "sources",
                "record_index",
                "block_index",
            )
        }
        arm_data["matching_blocks_detail"].append(detail)
        task_data["matching_blocks_detail"].append(detail)
        arm_data["pattern_counts"].update(block["patterns"])
        task_data["pattern_counts"].update(block["patterns"])

    total_pattern_counts: Counter[str] = Counter()
    for arm_data in arms.values():
        total_pattern_counts.update(arm_data["pattern_counts"])
        arm_data["pattern_counts"] = dict(sorted(arm_data["pattern_counts"].items()))
        for task_data in arm_data["tasks"].values():
            task_data["pattern_counts"] = dict(sorted(task_data["pattern_counts"].items()))
    return {
        "run_dir": str(run_dir),
        "files_scanned": [path.relative_to(run_dir).as_posix() for path in files_scanned],
        "blocks_scanned": sum(item["blocks_scanned"] for item in arms.values()),
        "executed_blocks": sum(item["executed_blocks"] for item in arms.values()),
        "blocked_blocks": sum(item["blocked_blocks"] for item in arms.values()),
        "matching_blocks": sum(item["matching_blocks"] for item in arms.values()),
        "matching_executed_blocks": sum(item["matching_executed_blocks"] for item in arms.values()),
        "matching_blocked_blocks": sum(item["matching_blocked_blocks"] for item in arms.values()),
        "pattern_counts": dict(sorted(total_pattern_counts.items())),
        "by_arm": dict(sorted(arms.items())),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit executed AppWorld code in a run directory's transcripts and simulations."
    )
    parser.add_argument("run_dir", type=Path, help="AppWorld Skills run directory to scan")
    args = parser.parse_args(argv)
    try:
        summary = audit_run_directory(args.run_dir)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    json.dump(summary, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
