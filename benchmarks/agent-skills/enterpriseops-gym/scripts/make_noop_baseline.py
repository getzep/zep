from __future__ import annotations

import argparse
import errno
import json
import os
import tempfile
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from uuid import uuid4

import httpx

from eog.dataset import Task, load_tasks
from eog.gym import GymClient
from eog.gym_sets import task_for_gym_set


@dataclass(frozen=True, slots=True)
class TaskResult:
    index: int
    task_id: str
    row: dict | None
    errors: tuple[dict[str, str], ...]


@dataclass(frozen=True, slots=True)
class NoopBaselineReport:
    total_tasks: int
    attempted_tasks: int
    successful_tasks: int
    errors: tuple[dict[str, str], ...]
    error_path: Path | None

    def count_errors(self, category: str) -> int:
        return sum(error["category"] == category for error in self.errors)


def _tasks_in_baseline_order(overlay_dir: Path) -> tuple[Task, ...]:
    tasks = {task.task_id: task for task in load_tasks(overlay_dir=overlay_dir) if not task.known_defect}
    baseline_path = Path(__file__).resolve().parents[1] / "data" / "noop_baseline.jsonl"
    baseline_ids = [str(json.loads(line)["task_id"]) for line in baseline_path.read_text().splitlines() if line.strip()]
    missing_task_ids = sorted(set(tasks) - set(baseline_ids))
    if missing_task_ids:
        raise ValueError(f"Tasks are missing from the base no-op baseline: {', '.join(missing_task_ids)}")
    return tuple(tasks[task_id] for task_id in baseline_ids if task_id in tasks)


def _exception_chain(error: BaseException):
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _error_category(error: BaseException) -> str:
    chain = tuple(_exception_chain(error))
    if any(
        isinstance(item, ConnectionRefusedError) or getattr(item, "errno", None) == errno.ECONNREFUSED for item in chain
    ):
        return "connection_refused"
    if any(isinstance(item, httpx.TransportError | TimeoutError) for item in chain):
        return "other_transport"
    if any(isinstance(item, httpx.HTTPStatusError) and 500 <= item.response.status_code < 600 for item in chain):
        return "server_error"
    return "other_error"


def _error_record(task_id: str, stage: str, error: BaseException) -> dict[str, str]:
    return {
        "task_id": task_id,
        "stage": stage,
        "category": _error_category(error),
        "exception_class": type(error).__name__,
    }


def _run_task(
    indexed_task: tuple[int, Task],
    *,
    data_root: Path,
    gym_set: int,
    client_factory: type[GymClient],
) -> TaskResult:
    index, original_task = indexed_task
    errors: list[dict[str, str]] = []
    seeded_servers: list[str] = []
    stage = "select_gym_set"
    clients: dict[str, GymClient] = {}
    database_ids: dict[str, str] = {}
    row: dict | None = None
    try:
        task = task_for_gym_set(original_task, gym_set)
        database_ids = {server["mcp_server_name"]: str(uuid4()) for server in task.gym_servers_config}
        for server in task.gym_servers_config:
            server_name = server["mcp_server_name"]
            clients[server_name] = client_factory(
                server["mcp_server_url"],
                name=server_name,
                context=server.get("context", {}),
            )
        for server in task.gym_servers_config:
            stage = f"seed:{server['mcp_server_name']}"
            seed_path = data_root / server["seed_database_file"]
            if not seed_path.is_file():
                raise FileNotFoundError(f"Database seed file does not exist: {seed_path}")
            server_name = server["mcp_server_name"]
            clients[server_name].seed(database_ids[server_name], seed_path.read_text())
            seeded_servers.append(server_name)
        stage = "verify"
        result = next(iter(clients.values())).verify(task, database_ids)
        row = {"task_id": task.task_id, "passes": list(result.passed)}
    except Exception as error:
        errors.append(_error_record(original_task.task_id, stage, error))
    finally:
        for server_name in seeded_servers:
            try:
                clients[server_name].delete(database_ids[server_name])
            except Exception as error:
                errors.append(_error_record(original_task.task_id, f"delete:{server_name}", error))
        for client in clients.values():
            try:
                client.close()
            except Exception as error:
                errors.append(_error_record(original_task.task_id, "close", error))
    if errors:
        row = None
    return TaskResult(index=index, task_id=original_task.task_id, row=row, errors=tuple(errors))


def _write_jsonl(path: Path, rows: tuple[dict, ...]) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as output:
            temporary_path = Path(output.name)
            for row in rows:
                output.write(json.dumps(row) + "\n")
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def make_noop_baseline(
    overlay_dir: Path,
    output_path: Path,
    data_root: Path,
    *,
    concurrency: int = 1,
    gym_set: int = 0,
    client_factory: type[GymClient] = GymClient,
) -> NoopBaselineReport:
    if concurrency < 1:
        raise ValueError("Concurrency must be at least 1")
    if gym_set < 0:
        raise ValueError("Gym set index must not be negative")
    tasks = _tasks_in_baseline_order(overlay_dir)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    error_path = output_path.with_name(f"{output_path.name}.errors.jsonl")
    temporary_path: Path | None = None
    results_by_index: dict[int, TaskResult] = {}
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as output:
            temporary_path = Path(output.name)
            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                task_iter = iter(enumerate(tasks, start=1))
                futures: dict[Future[TaskResult], int] = {}

                def submit_next() -> bool:
                    try:
                        indexed_task = next(task_iter)
                    except StopIteration:
                        return False
                    future = executor.submit(
                        partial(
                            _run_task,
                            data_root=data_root,
                            gym_set=gym_set,
                            client_factory=client_factory,
                        ),
                        indexed_task,
                    )
                    futures[future] = indexed_task[0]
                    return True

                for _ in range(min(concurrency, len(tasks))):
                    submit_next()
                stop_after_failure = False
                while futures:
                    completed, _ = wait(tuple(futures), return_when=FIRST_COMPLETED)
                    for future in completed:
                        del futures[future]
                        result = future.result()
                        results_by_index[result.index] = result
                        status = "error" if result.errors else "ok"
                        print(f"{result.index}/{len(tasks)} {result.task_id} {status}", flush=True)
                        stop_after_failure = stop_after_failure or bool(result.errors)
                    if stop_after_failure:
                        for future in futures:
                            future.cancel()
                        for future in futures:
                            if not future.cancelled():
                                result = future.result()
                                results_by_index[result.index] = result
                                status = "error" if result.errors else "ok"
                                print(f"{result.index}/{len(tasks)} {result.task_id} {status}", flush=True)
                        break
                    while len(futures) < concurrency and submit_next():
                        pass
            completed_results = tuple(results_by_index[index] for index in sorted(results_by_index))
            errors = tuple(error for result in completed_results for error in result.errors)
            successful_rows = tuple(result.row for result in completed_results if result.row is not None)
            for row in successful_rows:
                output.write(json.dumps(row) + "\n")
        if errors:
            _write_jsonl(error_path, errors)
        else:
            os.replace(temporary_path, output_path)
            temporary_path = None
            error_path.unlink(missing_ok=True)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return NoopBaselineReport(
        total_tasks=len(tasks),
        attempted_tasks=len(results_by_index),
        successful_tasks=len(successful_rows),
        errors=errors,
        error_path=error_path if errors else None,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate no-op verifier results from live Gym servers.")
    parser.add_argument("--overlay-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path(os.getenv("EOG_DATA_ROOT", str(Path.home() / ".cache" / "enterpriseops-gym" / "dbs"))),
    )
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--gym-set", type=int, default=0)
    return parser


def main() -> None:
    parser = _parser()
    args = parser.parse_args()
    if args.concurrency < 1:
        parser.error("--concurrency must be at least 1")
    if args.gym_set < 0:
        parser.error("--gym-set must not be negative")
    report = make_noop_baseline(
        args.overlay_dir,
        args.output,
        args.data_root,
        concurrency=args.concurrency,
        gym_set=args.gym_set,
    )
    if report.errors:
        print(
            f"Stopped after {report.attempted_tasks} attempts from {report.total_tasks} tasks; "
            f"{report.successful_tasks} tasks completed without errors. "
            f"Connection refused: {report.count_errors('connection_refused')}; "
            f"other transport: {report.count_errors('other_transport')}; "
            f"server errors: {report.count_errors('server_error')}; "
            f"other errors: {report.count_errors('other_error')}. "
            f"Details: {report.error_path}"
        )
        raise SystemExit(1)
    print(f"Wrote {report.total_tasks} no-op baseline rows to {args.output}")


if __name__ == "__main__":
    main()
