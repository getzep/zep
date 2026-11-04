from __future__ import annotations

import hashlib
import json
import os
import random
import subprocess
import tempfile
import threading
import time
from collections import Counter, deque
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .dataset import Task, manifest
from .errors import (
    BudgetExceeded,
    InfrastructureError,
    NonRetryableProviderError,
    PolicyFlagError,
    RetryExhaustedError,
)
from .ledger import JsonlLedger
from .prices import price_manifest
from .retry import failure_details

ROW_CLASSES = ("valid", "partial", "stopped", "invalid", "policy_flagged", "infra_failed", "superseded")


# Notes that a person adds to a written manifest (the snapshot name and its description); a resume keeps them
# and does not read them as a change of the frozen manifest.
MANIFEST_ANNOTATIONS = ("snapshot_label", "snapshot_note")


def _overlay_directory_sha256(overlay_dir: Path | None) -> str:
    if overlay_dir is None:
        return ""
    digest = hashlib.sha256()
    files = sorted(
        (path for path in overlay_dir.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(overlay_dir).as_posix(),
    )
    for path in files:
        relative_path = path.relative_to(overlay_dir).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative_path).to_bytes(8, "big"))
        digest.update(relative_path)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class RunManifest:
    git_commit: str
    dataset: dict[str, Any]
    image_digests: dict[str, str]
    split_sha256: str
    skills_snapshot_sha256: str
    price_table_sha256: str
    seed_sql_sha256: dict[str, str] = field(default_factory=dict)
    noop_baseline_sha256: str = ""
    noop_baseline_path: str = ""
    cells: tuple[dict[str, str], ...] = ()
    zep_base_url: str | None = None
    teams_townhall_patch_sha256: str = ""
    itsm_opened_by_patch_sha256: str = ""
    teacher_harness: str | None = None
    claude_agent_sdk_version: str | None = None
    claude_cli_version: str | None = None
    claude_agent_teams_enabled: bool | None = None
    claude_agent_teams_env: dict[str, str] = field(default_factory=dict)
    claude_cli_env: dict[str, str] = field(default_factory=dict)
    claude_sdk_argument_validation_enabled: bool | None = None
    force_first_read: bool = False
    skill_name_enum: bool = False
    skills_multi_read: bool = False
    skill_short_ids: bool = False
    claude_init_tools_by_row: dict[str, list[str]] = field(default_factory=dict)
    compile_failures_allowed: list[str] | None = None
    compile_failures: dict[str, int] | None = None
    system_preamble: str | None = None
    system_preamble_sha256: str | None = None
    task_scope: str = "split"
    matrix_settings: list[dict[str, str]] = field(default_factory=list)
    skills_snapshot_file_sha256: str = ""
    skills_snapshot_content_sha256: str = ""
    overlay_dir: str = ""
    overlay_sha256: str = ""
    gym_set_index: int = 0
    excluded_tasks_path: str = ""
    excluded_tasks_sha256: str = ""
    family_agents: dict[str, str] | None = None


def _row_key(row: dict[str, Any]) -> tuple[str, int, str, str, str, str]:
    return (
        row["task_id"],
        int(row["trial"]),
        row.get("model", row.get("route", "")),
        row.get("effort", ""),
        row["arm"],
        row.get("stage", row.get("cell", "")),
    )


def freeze_manifest(
    *,
    repo: Path,
    tasks: tuple[Task, ...],
    image_digests: dict[str, str],
    split: dict[str, Any],
    skills_snapshot_sha256: str = "",
    cells: tuple[dict[str, str], ...] = (),
    zep_base_url: str | None = None,
    teams_townhall_patch_sha256: str = "",
    itsm_opened_by_patch_sha256: str = "",
    teacher_harness: str | None = None,
    claude_agent_sdk_version: str | None = None,
    claude_cli_version: str | None = None,
    claude_agent_teams_enabled: bool | None = None,
    claude_agent_teams_env: dict[str, str] | None = None,
    claude_cli_env: dict[str, str] | None = None,
    claude_sdk_argument_validation_enabled: bool | None = None,
    force_first_read: bool = False,
    skill_name_enum: bool = False,
    skills_multi_read: bool = False,
    skill_short_ids: bool = False,
    compile_failures_allowed: list[str] | None = None,
    compile_failures: dict[str, int] | None = None,
    system_preamble: str | None = None,
    system_preamble_sha256: str | None = None,
    task_scope: str = "split",
    matrix_settings: list[dict[str, str]] | None = None,
    skills_snapshot_file_sha256: str = "",
    skills_snapshot_content_sha256: str = "",
    overlay_dir: Path | None = None,
    excluded_tasks_path: Path | None = None,
    noop_baseline_path: Path | None = None,
    gym_set_index: int = 0,
    family_agents: dict[str, str] | None = None,
) -> RunManifest:
    status = subprocess.run(["git", "status", "--porcelain"], cwd=repo, check=True, capture_output=True, text=True)
    if status.stdout.strip():
        raise RuntimeError("The repository must be clean before the first result row")
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    price_hash = hashlib.sha256(json.dumps(price_manifest(), sort_keys=True).encode()).hexdigest()
    split_hash = hashlib.sha256(json.dumps(split, sort_keys=True).encode()).hexdigest()
    seed_root = Path(os.getenv("EOG_DATA_ROOT", str(Path.home() / ".cache" / "enterpriseops-gym" / "dbs")))
    seed_files = sorted({server["seed_database_file"] for task in tasks for server in task.gym_servers_config})
    seed_hashes = {name: hashlib.sha256((seed_root / name).read_bytes()).hexdigest() for name in seed_files}
    baseline_path = (noop_baseline_path or Path(__file__).parents[2] / "data" / "noop_baseline.jsonl").resolve()
    baseline_hash = hashlib.sha256(baseline_path.read_bytes()).hexdigest()
    return RunManifest(
        commit,
        manifest(tasks),
        image_digests,
        split_hash,
        skills_snapshot_sha256,
        price_hash,
        seed_hashes,
        baseline_hash,
        str(baseline_path),
        cells,
        zep_base_url,
        teams_townhall_patch_sha256,
        itsm_opened_by_patch_sha256,
        teacher_harness,
        claude_agent_sdk_version,
        claude_cli_version,
        claude_agent_teams_enabled,
        claude_agent_teams_env or {},
        claude_cli_env or {},
        claude_sdk_argument_validation_enabled,
        force_first_read,
        skill_name_enum,
        skills_multi_read,
        skill_short_ids,
        compile_failures_allowed=compile_failures_allowed,
        compile_failures=compile_failures,
        system_preamble=system_preamble,
        system_preamble_sha256=system_preamble_sha256,
        task_scope=task_scope,
        matrix_settings=matrix_settings or [],
        skills_snapshot_file_sha256=skills_snapshot_file_sha256,
        skills_snapshot_content_sha256=skills_snapshot_content_sha256,
        overlay_dir=str(overlay_dir.resolve()) if overlay_dir is not None else "",
        overlay_sha256=_overlay_directory_sha256(overlay_dir),
        gym_set_index=gym_set_index,
        excluded_tasks_path=str(excluded_tasks_path.resolve()) if excluded_tasks_path is not None else "",
        excluded_tasks_sha256=(
            hashlib.sha256(excluded_tasks_path.read_bytes()).hexdigest() if excluded_tasks_path is not None else ""
        ),
        family_agents=family_agents,
    )


class RunStore:
    def __init__(
        self,
        directory: Path,
        *,
        resume: bool = False,
        manifest_name: str = "manifest.json",
        ramp_name: str = "ramp.jsonl",
    ) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.resume = resume
        self.manifest_path = directory / manifest_name
        self.ramp_records = JsonlLedger(directory / ramp_name)
        self.rows = JsonlLedger(directory / "rows.jsonl")
        self.policy_flagged = JsonlLedger(directory / "policy_flagged.jsonl")
        self.superseded = JsonlLedger(directory / "superseded.jsonl")
        self.class_ledgers = {name: JsonlLedger(directory / f"{name}.jsonl") for name in ROW_CLASSES}
        self._checkpoint_lock = threading.Lock()

    def checkpoint_ramp(self, record: dict[str, Any]) -> None:
        self.ramp_records.append(record)

    def completed_task_trials(self) -> set[tuple[str, int, str, str, str, str]]:
        paths = [self.rows.path, self.policy_flagged.path]
        return {
            _row_key(row)
            for path in paths
            if path.exists()
            for line in path.read_text().splitlines()
            if line
            for row in [json.loads(line)]
            if row.get("status") in {"valid", "invalid", "policy_flagged"}
        }

    def rows_to_run(self, planned: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not self.resume or not (self.rows.path.exists() or self.policy_flagged.path.exists()):
            return planned
        latest_status: dict[tuple[str, int, str, str, str, str], str | None] = {}

        def task_identity(key: tuple[str, int, str, str, str, str]) -> tuple[str, str, str, str, str]:
            task_id, _, model, effort, arm, stage = key
            return task_id, model, effort, arm, stage

        failing_tasks: set[tuple[str, str, str, str, str]] = set()
        for path in (self.rows.path, self.policy_flagged.path):
            if not path.exists():
                continue
            for line in path.read_text().splitlines():
                if line.strip():
                    row = json.loads(line)
                    key = _row_key(row)
                    status = row.get("status")
                    latest_status[key] = status
                    if status == "infra_failed":
                        failing_tasks.add(task_identity(key))

        completed = {key for key, status in latest_status.items() if status in {"valid", "invalid", "policy_flagged"}}
        pending = [row for row in planned if _row_key(row) not in completed]
        first = [
            row
            for row in pending
            if task_identity(_row_key(row)) not in failing_tasks and latest_status.get(_row_key(row)) != "stopped"
        ]
        stopped_rows = [
            row
            for row in pending
            if task_identity(_row_key(row)) not in failing_tasks and latest_status.get(_row_key(row)) == "stopped"
        ]
        failing_rows = [row for row in pending if task_identity(_row_key(row)) in failing_tasks]
        return first + stopped_rows + failing_rows

    def latest_status_counts(self, arm: str) -> dict[str, int]:
        latest_status: dict[tuple[str, int, str, str, str, str], str | None] = {}
        if self.rows.path.exists():
            for line in self.rows.path.read_text().splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("arm") == arm:
                    latest_status[_row_key(row)] = row.get("status")
        return dict(Counter(status for status in latest_status.values() if status is not None))

    def checkpoint(self, row: dict[str, Any]) -> None:
        with self._checkpoint_lock:
            if row.get("status") not in ROW_CLASSES:
                raise ValueError(f"Unknown result class: {row.get('status')}")
            if self.rows.path.exists():
                existing = [json.loads(line) for line in self.rows.path.read_text().splitlines() if line.strip()]
                for old in existing:
                    if _row_key(old) == _row_key(row) and old.get("status") not in {"valid", "invalid", "superseded"}:
                        self.superseded.append({**old, "status": "superseded", "superseded_at": time.time()})
            if row["status"] == "policy_flagged":
                self.policy_flagged.append(row)
            else:
                self.rows.append(row)
            if row["status"] != "policy_flagged":
                self.class_ledgers[row["status"]].append(row)
            init_tools = row.get("claude_init_tools")
            if isinstance(init_tools, list) and self.manifest_path.exists():
                manifest = json.loads(self.manifest_path.read_text())
                key = f"{row['task_id']}:{row['trial']}:{row['arm']}:{row.get('stage', row.get('cell', ''))}"
                manifest.setdefault("claude_init_tools_by_row", {})[key] = init_tools
                self._write_manifest(manifest)

    def _write_manifest(self, manifest: dict[str, Any]) -> None:
        fd, temp_name = tempfile.mkstemp(prefix=f".{self.manifest_path.name}.", suffix=".tmp", dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                output.write(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
            os.replace(temp_name, self.manifest_path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def write_manifest(self, current: RunManifest, *, allow_commit_change: bool = False) -> None:
        """Stop on a changed frozen manifest; with allow_commit_change, accept a new git_commit and record it."""
        value = json.loads(json.dumps(asdict(current), sort_keys=True))
        for key in ("compile_failures_allowed", "compile_failures"):
            if value[key] is None:
                value.pop(key)
        if value["family_agents"] is None:
            value.pop("family_agents")
        if self.manifest_path.exists():
            existing = json.loads(self.manifest_path.read_text())
            runtime_tools = existing.get("claude_init_tools_by_row", {})
            existing.pop("claude_init_tools_by_row", None)
            value.pop("claude_init_tools_by_row", None)
            for annotation in MANIFEST_ANNOTATIONS:
                existing.pop(annotation, None)
            existing.setdefault("force_first_read", False)
            existing.setdefault("skill_name_enum", False)
            existing.setdefault("skills_multi_read", False)
            existing.setdefault("skill_short_ids", False)
            existing.setdefault("system_preamble", None)
            existing.setdefault("system_preamble_sha256", None)
            existing.setdefault("task_scope", "split")
            if existing != value:
                same_but_commit = {k: v for k, v in existing.items() if k != "git_commit"} == {
                    k: v for k, v in value.items() if k != "git_commit"
                }
                if not (allow_commit_change and same_but_commit):
                    raise ValueError("The frozen run manifest changed")
                with (self.directory / "resume_commits.jsonl").open("a", encoding="utf-8") as output:
                    record = {
                        "frozen_git_commit": existing.get("git_commit"),
                        "resume_git_commit": value.get("git_commit"),
                        "resumed_at": datetime.now(UTC).isoformat(),
                    }
                    output.write(json.dumps(record, sort_keys=True) + "\n")
            existing["claude_init_tools_by_row"] = runtime_tools
        else:
            self._write_manifest(value)


def interleave_arms(rows: list[dict[str, Any]], *, seed: int, block_size: int = 2) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    groups: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault((row["task_id"], int(row["trial"])), []).append(row)
    output = []
    for key in sorted(groups):
        group = groups[key]
        rng.shuffle(group)
        by_arm = {arm: [item for item in group if item["arm"] == arm] for arm in {r["arm"] for r in group}}
        arms = list(by_arm)
        rng.shuffle(arms)
        merged = []
        while any(by_arm.values()):
            for arm in arms:
                if by_arm[arm]:
                    merged.append(by_arm[arm].pop())
        output.extend(merged)
    return output


def ramp_steps(concurrency: int, ramp: str) -> tuple[int, ...]:
    if concurrency < 1:
        raise ValueError("Concurrency must be at least one")
    values = {int(value) for value in ramp.split(",") if value.strip()}
    return tuple(sorted({concurrency, *(value for value in values if 0 < value <= concurrency)}))


def task_row_count(results: list[dict[str, Any]]) -> int:
    return sum(result.get("status") != "ramp" for result in results)


def sample_score_tasks(
    tasks: tuple[Task, ...], split: dict[str, Any], *, sample_size: int = 152, seed: int = 20260519
) -> tuple[Task, ...]:
    by_domain: dict[str, list[Task]] = {}
    for task in tasks:
        if split["tasks"][task.task_id]["side"] == "score" and not task.known_defect:
            by_domain.setdefault(task.domain, []).append(task)
    total = sum(len(group) for group in by_domain.values())
    if sample_size > total:
        raise ValueError(f"Requested {sample_size} score tasks, but only {total} are available")
    exact = {domain: sample_size * len(group) / total for domain, group in by_domain.items()}
    quotas = {domain: int(value) for domain, value in exact.items()}
    remaining = sample_size - sum(quotas.values())
    for domain in sorted(exact, key=lambda key: (exact[key] - quotas[key], key), reverse=True)[:remaining]:
        quotas[domain] += 1
    rng = random.Random(seed)
    selected = [
        task
        for domain, group in sorted(by_domain.items())
        for task in rng.sample(sorted(group, key=lambda item: item.task_id), quotas[domain])
    ]
    return tuple(sorted(selected, key=lambda task: task.task_id))


def sample_teach_tasks(
    tasks: tuple[Task, ...], split: dict[str, Any], *, sample_size: int = 57, seed: int = 20261016
) -> tuple[Task, ...]:
    by_domain: dict[str, list[Task]] = {}
    for task in tasks:
        if split["tasks"][task.task_id]["side"] == "teach" and not task.known_defect:
            by_domain.setdefault(task.domain, []).append(task)
    total = sum(len(group) for group in by_domain.values())
    if sample_size > total:
        raise ValueError(f"Requested {sample_size} teach tasks, but only {total} are available")
    exact = {domain: sample_size * len(group) / total for domain, group in by_domain.items()}
    quotas = {domain: int(value) for domain, value in exact.items()}
    remaining = sample_size - sum(quotas.values())
    for domain in sorted(exact, key=lambda key: (exact[key] - quotas[key], key), reverse=True)[:remaining]:
        quotas[domain] += 1
    rng = random.Random(seed)
    selected = [
        task
        for domain, group in sorted(by_domain.items())
        for task in rng.sample(sorted(group, key=lambda item: item.task_id), quotas[domain])
    ]
    return tuple(sorted(selected, key=lambda task: task.task_id))


def seeded_arm_order(task_id: str, *, seed: int = 20261016) -> tuple[str, str]:
    digest = hashlib.sha256(f"{seed}:{task_id}".encode()).digest()
    rng = random.Random(int.from_bytes(digest[:8], "big"))
    order = ["stirrup", "claude_code"]
    rng.shuffle(order)
    return order[0], order[1]


def run_concurrent(
    rows: list[dict[str, Any]],
    execute: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    ramp: tuple[int, ...],
    route_cap: int | None = None,
    initial_policy_flags: int = 0,
    checkpoint: Callable[[dict[str, Any]], None] | None = None,
    ramp_checkpoint: Callable[[dict[str, Any]], None] | None = None,
    infra_retries: int = 0,
    should_stop: Callable[[], bool] | None = None,
    stop_requested: threading.Event | None = None,
    abort_requested: threading.Event | None = None,
    signal_name: Callable[[], str | None] | None = None,
    execute_with_abort: Callable[[dict[str, Any], threading.Event | None], dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    results = []
    pending = deque(rows)
    ramp_index = 0
    ramp_generation = 0
    ramp_window: list[dict[str, Any]] = []
    consecutive_infra_tasks: dict[str, set[str]] = {}
    stopped_routes: set[str] = set()
    policy_flagged = initial_policy_flags
    stop_scheduling = policy_flagged > 5 or (should_stop is not None and should_stop())
    steps = (
        tuple(sorted({min(step, route_cap) for step in ramp})) if route_cap is not None else tuple(sorted(set(ramp)))
    )
    if not steps or steps[0] < 1:
        raise ValueError("The ramp and route cap must allow at least one episode")
    max_workers = steps[-1]

    def effective_concurrency(index: int) -> int:
        return steps[index]

    def execute_with_retries(row: dict[str, Any]) -> tuple[dict[str, Any], float]:
        current = row
        for retry in range(infra_retries + 1):
            result = (
                execute_with_abort(current, abort_requested) if execute_with_abort is not None else execute(current)
            )
            if result.get("status") != "infra_failed" or result.get("stop_run") or retry == infra_retries:
                return result, time.monotonic()
            current = {**row, "infra_retry": retry + 1}
        raise RuntimeError("The retry loop did not return a result")

    def record_stopped(row: dict[str, Any], reason: str, *, signal: str | None = None) -> None:
        stopped = {
            **row,
            "status": "stopped",
            "stop_run": True,
            "stop_reason": reason,
        }
        if signal is not None:
            stopped["stop_reason"] = signal
            stopped["stop_type"] = "signal_stop"
            stopped["signal_name"] = signal
        if checkpoint:
            checkpoint(stopped)
        results.append(stopped)

    active: dict[Any, tuple[dict[str, Any], int, int]] = {}
    ramp_started_at = time.monotonic()
    ramp_rows = 0
    signal_pending_recorded = False

    def record_ramp(
        step_index: int,
        next_step_index: int | None,
        window: list[dict[str, Any]],
        finished_at: float,
    ) -> None:
        nonlocal ramp_started_at, ramp_rows
        attempts = sum(row.get("model_call_attempts", 1 if row.get("http_status") is not None else 0) for row in window)
        rate_limited = sum(row.get("http_429_attempts", int(row.get("http_status") == 429)) for row in window)
        record = {
            "status": "ramp",
            "concurrency": effective_concurrency(step_index),
            "ramp_step": steps[step_index],
            "next_concurrency": (effective_concurrency(next_step_index) if next_step_index is not None else None),
            "next_ramp_step": steps[next_step_index] if next_step_index is not None else None,
            "rows": ramp_rows,
            "http_429_share": rate_limited / attempts if attempts else 0.0,
            "infra_failed_share": (
                sum(row.get("status") == "infra_failed" for row in window) / len(window) if window else 0.0
            ),
            "latency_seconds": max(0.0, finished_at - ramp_started_at),
        }
        if ramp_checkpoint is not None:
            ramp_checkpoint(record)
        results.append(record)
        ramp_started_at = finished_at
        ramp_rows = 0

    def process_result(
        row: dict[str, Any],
        result: dict[str, Any],
        finished_at: float,
        started_step: int,
        started_generation: int,
    ) -> None:
        nonlocal policy_flagged, ramp_index, ramp_generation, ramp_rows, stop_scheduling, pending
        results.append(result)
        ramp_rows += 1

        route = str(result.get("route", row.get("route", "default")))
        if result.get("status") == "infra_failed":
            failed_task_ids = consecutive_infra_tasks.setdefault(route, set())
            failed_task_ids.add(str(row.get("task_id", "")))
            if len(failed_task_ids) >= 3:
                stopped_routes.add(route)
        else:
            consecutive_infra_tasks.pop(route, None)
        if result.get("status") == "policy_flagged":
            policy_flagged += 1
        if result.get("status") == "stopped" or result.get("stop_run") or policy_flagged > 5:
            stop_scheduling = True
        if should_stop is not None and should_stop():
            stop_scheduling = True

        if route in stopped_routes:
            remaining = deque()
            while pending:
                pending_row = pending.popleft()
                if str(pending_row.get("route", "default")) == route:
                    record_stopped(pending_row, "three_consecutive_infra_failed")
                else:
                    remaining.append(pending_row)
            pending = remaining

        if stop_scheduling:
            return

        if started_step != ramp_index or started_generation != ramp_generation:
            return
        ramp_window.append(result)
        current_step = ramp_index
        if len(ramp_window) < steps[current_step]:
            return

        window = list(ramp_window)
        http_429_share = _http_429_share(window)
        next_step = current_step
        if http_429_share > 0.05 and current_step > 0:
            next_step -= 1
        elif ramp_can_advance(window) and current_step < len(steps) - 1:
            next_step += 1
        ramp_window.clear()
        if next_step != current_step:
            ramp_index = next_step
            ramp_generation += 1
            record_ramp(current_step, next_step, window, finished_at)

    pool = ThreadPoolExecutor(max_workers=max_workers)
    try:
        while pending or active:
            if (stop_requested is not None and stop_requested.is_set()) or (
                abort_requested is not None and abort_requested.is_set()
            ):
                stop_scheduling = True
                if not signal_pending_recorded:
                    current_signal = signal_name() if signal_name else None
                    while pending:
                        record_stopped(pending.popleft(), current_signal or "signal_stop", signal=current_signal)
                    signal_pending_recorded = True
            if not stop_scheduling:
                concurrency = effective_concurrency(ramp_index)
                while (
                    pending
                    and len(active) < concurrency
                    and (stop_requested is None or not stop_requested.is_set())
                    and (abort_requested is None or not abort_requested.is_set())
                ):
                    row = pending.popleft()
                    route = str(row.get("route", "default"))
                    if route in stopped_routes:
                        record_stopped(row, "three_consecutive_infra_failed")
                        continue
                    active[pool.submit(execute_with_retries, row)] = (row, ramp_index, ramp_generation)
            if not active:
                break

            done, _ = wait(tuple(active), timeout=1.0, return_when=FIRST_COMPLETED)
            completed = []
            for future in done:
                row, _, _ = active[future]
                try:
                    result, finished_at = future.result()
                except Exception as error:
                    result = {
                        **row,
                        "status": "infra_failed",
                        "error_class": type(error).__name__,
                        **failure_details(error),
                    }
                    finished_at = time.monotonic()
                    if checkpoint:
                        checkpoint(result)
                completed.append((finished_at, future, result))
            for finished_at, future, result in sorted(completed, key=lambda item: item[0]):
                row, started_step, started_generation = active.pop(future)
                process_result(row, result, finished_at, started_step, started_generation)
    finally:
        pool.shutdown(wait=True)

    if ramp_rows:
        record_ramp(ramp_index, None, ramp_window, time.monotonic())
    return results


def _http_429_share(rows: list[dict[str, Any]]) -> float:
    attempts = sum(row.get("model_call_attempts", 1 if row.get("http_status") is not None else 0) for row in rows)
    rate_limited = sum(row.get("http_429_attempts", int(row.get("http_status") == 429)) for row in rows)
    return rate_limited / attempts if attempts else 0.0


def ramp_can_advance(rows: list[dict[str, Any]]) -> bool:
    if not rows:
        return False
    return _http_429_share(rows) < 0.05 and sum(row.get("status") == "infra_failed" for row in rows) == 0


def classify_episode(task: Task, agent_result: Any, verifier_result: Any) -> str:
    if task.known_defect:
        return "invalid"
    if verifier_result is not None:
        return "valid"
    return "partial"


def execute_episode(task: Task, call: Callable[[], Any]) -> dict[str, Any]:
    try:
        return call()
    except (InfrastructureError, RetryExhaustedError) as exc:
        row = {"status": "infra_failed", "error_class": type(exc).__name__, **failure_details(exc)}
        if reason := getattr(exc, "reason", None):
            row["reason"] = reason
        return row
    except PolicyFlagError as exc:
        return {"status": "policy_flagged", "reward": 0, "error_class": type(exc).__name__, **failure_details(exc)}
    except BudgetExceeded as exc:
        return {"status": "stopped", "error_class": type(exc).__name__, **failure_details(exc)}
    except NonRetryableProviderError as exc:
        return {"status": "stopped", "error_class": type(exc).__name__, **failure_details(exc)}
    except Exception as exc:
        return {"status": "stopped", "error_class": type(exc).__name__, **failure_details(exc)}


def load_task_sample(tasks: tuple[Task, ...], path: Path | None) -> tuple[Task, ...]:
    if path is None:
        return tasks
    payload = json.loads(path.read_text())
    ids = set(payload if isinstance(payload, list) else payload.get("task_ids", []))
    return tuple(task for task in tasks if task.task_id in ids)
