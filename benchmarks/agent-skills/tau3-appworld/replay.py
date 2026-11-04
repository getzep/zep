"""Replay recorded teacher simulations into a fresh Agent with one admission policy.

The replay isolates admission and compilation-coverage effects from teacher
variance: every arm receives the same redacted Trajectories.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Any, cast

from tau2.runner.helpers import get_tasks
from zep_cloud import Zep

from compile import wait_for_summaries
from config import load_config, zep_client
from redaction import Redactor
from run import (
    DEFAULT_SEED,
    ENVIRONMENT_BY_DOMAIN,
    FAMILY_BY_DOMAIN,
    _latest_rows,
    _load_rows,
    _simulation_path,
    apply_literal_policy,
    discoverable_tool_names,
)
from zep_capture import TrajectoryRecorder, ensure_agent, value

LOGGER = logging.getLogger("tau2-bench-skills.replay")
REPLAY_FAMILIES = {**FAMILY_BY_DOMAIN, "appworld": "tool.appworld"}
REPLAY_ENVIRONMENTS = {**ENVIRONMENT_BY_DOMAIN, "appworld": "appworld"}
ALL_FAMILIES = list(REPLAY_FAMILIES.values())


def memory_settings(
    approval: str,
    compilation_mode: str,
    minimum: int | None,
    approval_required_tools: list[str] | None = None,
    provisional_skill_limit: int | None = None,
) -> dict[str, Any]:
    settings: dict[str, Any] = {
        "compilation_mode": compilation_mode,
        "approval": approval,
        "auto_approval_task_families": ALL_FAMILIES,
    }
    if minimum is not None:
        settings["approval_minimum_attempt_families"] = minimum
    if approval_required_tools:
        settings["approval_required_tools"] = approval_required_tools
    if provisional_skill_limit is not None:
        settings["provisional_skill_limit"] = provisional_skill_limit
    return settings


def replay_events(recorder: TrajectoryRecorder, messages: list[dict[str, Any]]) -> None:
    """Replay a tau2 transcript.

    User-device tool calls are observations of the user, not Agent tool calls.
    """
    pending: dict[str, str] = {}
    user_pending: dict[str, str] = {}
    for message in messages:
        role = message.get("role")
        if role == "user":
            for call in message.get("tool_calls") or []:
                user_pending[str(call.get("id"))] = str(call.get("name"))
            if message.get("content"):
                recorder.record_message("user", str(message["content"]))
        elif role == "assistant":
            calls = message.get("tool_calls") or []
            for call in calls:
                call_id = str(call.get("id"))
                pending[call_id] = str(call.get("name"))
                recorder.record_tool_call(
                    str(call.get("name")), call.get("arguments") or {}, call_id=call_id
                )
            if not calls and message.get("content"):
                recorder.record_message("assistant", str(message["content"]))
        elif role == "tool":
            call_id = str(message.get("id"))
            content = str(message.get("content") or "")
            if call_id in user_pending:
                name = user_pending.pop(call_id)
                recorder.record_message("user", f"device {name} result: {content}")
            elif call_id in pending:
                name = pending.pop(call_id)
                recorder.record_tool_result(name, content, call_id=call_id)
            else:
                raise ValueError(f"Tool result has no matching tool call: {call_id}")


def replay(args: argparse.Namespace, client: Zep) -> dict[str, Any]:
    source_dir = Path(args.results_root) / args.source_run_id
    rows = sorted(
        _latest_rows(_load_rows(source_dir / "teacher.jsonl")),
        key=lambda row: str(row.get("started_at", "")),
    )
    agent = ensure_agent(
        client,
        f"replay-{args.arm}",
        run_id=args.run_id,
        compilation_mode="manual" if args.batch else "scheduled",
        approval=args.approval,
    )
    agent_uuid = agent["agent_uuid"]
    apply_literal_policy(
        client,
        agent_uuid,
        args.domains,
        idempotency_prefix=f"{args.run_id}:replay-{args.arm}",
    )
    current = client.agent.get(agent_uuid)
    client.agent.update(
        agent_uuid,
        expected_revision=int(value(current, "revision")),
        memory_settings=cast(
            Any,
            memory_settings(
                args.approval,
                "manual" if args.batch else "scheduled",
                args.minimum_families,
                args.approval_required_tools,
                args.provisional_skill_limit,
            ),
        ),
    )
    tasks = {
        domain: get_tasks(domain, task_split_name=args.task_split)
        for domain in args.domains
        if domain in FAMILY_BY_DOMAIN
    }
    selected: list[dict[str, Any]] = []
    successes: dict[str, int] = {}
    for row in rows:
        domain = str(row["domain"])
        if domain not in args.domains or row.get("error"):
            continue
        if args.max_successes_per_domain is not None:
            if not row.get("succeeded"):
                continue
            if successes.get(domain, 0) >= args.max_successes_per_domain:
                continue
            successes[domain] = successes.get(domain, 0) + 1
        path = _simulation_path(
            source_dir, "teacher", domain, int(row["task_index"]), int(row["trial"])
        )
        if path.is_file():
            selected.append(row)
    held: list[dict[str, Any]] = []
    if args.batch:
        last_by_domain = {str(row["domain"]): row for row in selected}
        held = list(last_by_domain.values())
        selected = [row for row in selected if row not in held]

    replayed: list[dict[str, Any]] = []

    def record(row: dict[str, Any]) -> None:
        domain = str(row["domain"])
        simulation = json.loads(
            _simulation_path(
                source_dir, "teacher", domain, int(row["task_index"]), int(row["trial"])
            ).read_text()
        )
        recorder = TrajectoryRecorder(
            client,
            agent_uuid,
            REPLAY_FAMILIES[domain],
            f"Complete the user's {domain} request.",
            REPLAY_ENVIRONMENTS[domain],
            {
                "harness": "tau2-bench-skills",
                "stage": "replay",
                "arm": args.arm,
                "domain": domain,
                "task_index": str(row["task_index"]),
                "trial": str(row["trial"]),
            },
            redactor=Redactor.from_task(tasks[domain][int(row["task_index"])], domain)
            if domain in tasks
            else Redactor(),
            stage="replay",
            run_id=args.run_id,
            domain=domain,
            task_index=int(row["task_index"]),
            trial=int(row["trial"]),
            objective_from_request=True,
            discoverable_tools=discoverable_tool_names(domain),
        )
        succeeded = bool(row.get("succeeded"))
        with recorder:
            replay_events(recorder, simulation.get("messages") or [])
            recorder.set_result(succeeded, succeeded)
        replayed.append(
            {
                "domain": domain,
                "task_index": row["task_index"],
                "trial": row["trial"],
                "succeeded": succeeded,
                "trajectory_uuid": recorder.trajectory_uuid,
            }
        )
        if args.pace_seconds:
            time.sleep(args.pace_seconds)

    for row in selected:
        record(row)
    summaries: dict[str, Any] | None = None
    if args.batch:
        summaries = wait_for_summaries(
            client,
            agent_uuid,
            [str(item["trajectory_uuid"]) for item in replayed],
            timeout=args.summary_timeout,
        )
        current = client.agent.get(agent_uuid)
        client.agent.update(
            agent_uuid,
            expected_revision=int(value(current, "revision")),
            memory_settings=cast(
                Any,
                memory_settings(
                    args.approval,
                    "on_close",
                    args.minimum_families,
                    args.approval_required_tools,
                    args.provisional_skill_limit,
                ),
            ),
        )
        for row in held:
            record(row)
    result = {
        "arm": args.arm,
        "agent_uuid": agent_uuid,
        "approval": args.approval,
        "minimum_families": args.minimum_families,
        "approval_required_tools": args.approval_required_tools,
        "provisional_skill_limit": args.provisional_skill_limit,
        "task_split": args.task_split,
        "batch": args.batch,
        "max_successes_per_domain": args.max_successes_per_domain,
        "summaries_before_batch": summaries,
        "seed": DEFAULT_SEED,
        "source_run_id": args.source_run_id,
        "trajectories": replayed,
    }
    output = Path(args.results_root) / args.run_id / f"replay-{args.arm}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-run-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--arm", required=True)
    parser.add_argument("--approval", choices=["auto", "manual"], default="auto")
    parser.add_argument("--minimum-families", type=int)
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Hold compilation until every Trajectory is recorded, then enable scheduled mode",
    )
    parser.add_argument("--pace-seconds", type=float, default=0.0)
    parser.add_argument("--task-split", default="base")
    parser.add_argument("--approval-required-tools", nargs="*")
    parser.add_argument("--provisional-skill-limit", type=int)
    parser.add_argument(
        "--max-successes-per-domain",
        type=int,
        help="Replay only the first N successful Trajectories in each domain.",
    )
    parser.add_argument("--summary-timeout", type=float, default=1800.0)
    parser.add_argument(
        "--domains", nargs="+", choices=list(REPLAY_FAMILIES), default=list(FAMILY_BY_DOMAIN)
    )
    parser.add_argument("--results-root", default=str(Path(__file__).resolve().parent / "results"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = load_config()
    client = zep_client(config.api_key, config.api_url)
    print(json.dumps(replay(args, client), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
