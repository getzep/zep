from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import scripts.make_noop_baseline as baseline
from eog.dataset import Task
from eog.errors import InfrastructureError


def _task(task_id: str) -> Task:
    return Task(
        task_id=task_id,
        domain="itsm",
        system_prompt="",
        user_prompt="",
        selected_tools=(),
        restricted_tools=(),
        mcp_endpoint="/mcp",
        number_of_runs=1,
        reset_database_between_runs=True,
        gym_servers_config=(
            {
                "mcp_server_name": "gym-itsm-mcp",
                "mcp_server_url": "http://localhost:8006",
                "seed_database_file": "seed.sql",
                "context": {},
            },
        ),
        verifiers=(),
    )


def test_noop_baseline_accepts_concurrency_and_gym_set(
    monkeypatch,
    tmp_path: Path,
) -> None:
    tasks = (_task("task-b"), _task("task-a"))
    monkeypatch.setattr(baseline, "_tasks_in_baseline_order", lambda _: tasks)
    data_root = tmp_path / "dbs"
    data_root.mkdir()
    (data_root / "seed.sql").write_text("SELECT 1;")

    class State:
        active: ClassVar[int] = 0
        maximum_active: ClassVar[int] = 0
        urls: ClassVar[list[str]] = []
        lock: ClassVar[threading.Lock] = threading.Lock()
        barrier: ClassVar[threading.Barrier] = threading.Barrier(2)

    class FakeGymClient:
        def __init__(self, url: str, **_: object) -> None:
            self.url = url
            with State.lock:
                State.urls.append(url)

        def seed(self, *_: object) -> None:
            with State.lock:
                State.active += 1
                State.maximum_active = max(State.maximum_active, State.active)
            State.barrier.wait(timeout=3)

        def verify(self, *_: object) -> SimpleNamespace:
            return SimpleNamespace(passed=(True,))

        def delete(self, *_: object) -> None:
            with State.lock:
                State.active -= 1

        def close(self) -> None:
            pass

    output_path = tmp_path / "baseline.jsonl"
    error_path = output_path.with_name(f"{output_path.name}.errors.jsonl")
    error_path.write_text('{"category":"connection_refused"}\n')
    report = baseline.make_noop_baseline(
        tmp_path / "overlay",
        output_path,
        data_root,
        concurrency=2,
        gym_set=2,
        client_factory=FakeGymClient,
    )

    assert report.total_tasks == 2
    assert report.successful_tasks == 2
    assert report.errors == ()
    assert not error_path.exists()
    assert State.maximum_active == 2
    assert State.urls == ["http://localhost:8206", "http://localhost:8206"]
    assert [json.loads(line)["task_id"] for line in output_path.read_text().splitlines()] == ["task-b", "task-a"]


def test_noop_baseline_records_connection_refusal_without_replacing_output(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(baseline, "_tasks_in_baseline_order", lambda _: (_task("task-a"),))
    data_root = tmp_path / "dbs"
    data_root.mkdir()
    (data_root / "seed.sql").write_text("SELECT 1;")
    output_path = tmp_path / "baseline.jsonl"
    output_path.write_text("existing row\n")

    class RefusingGymClient:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        def seed(self, *_: object) -> None:
            cause = ConnectionRefusedError(111, "connection refused")
            raise InfrastructureError("Gym seed failed") from cause

        def delete(self, *_: object) -> None:
            pass

        def close(self) -> None:
            pass

    report = baseline.make_noop_baseline(
        tmp_path / "overlay",
        output_path,
        data_root,
        client_factory=RefusingGymClient,
    )

    assert report.total_tasks == 1
    assert report.successful_tasks == 0
    assert report.count_errors("connection_refused") == 1
    assert report.error_path is not None
    assert json.loads(report.error_path.read_text())["category"] == "connection_refused"
    assert output_path.read_text() == "existing row\n"


def test_noop_baseline_cli_parses_concurrency_and_gym_set() -> None:
    args = baseline._parser().parse_args(
        [
            "--overlay-dir",
            "overlay",
            "--output",
            "baseline.jsonl",
            "--concurrency",
            "18",
            "--gym-set",
            "2",
        ]
    )

    assert args.concurrency == 18
    assert args.gym_set == 2
