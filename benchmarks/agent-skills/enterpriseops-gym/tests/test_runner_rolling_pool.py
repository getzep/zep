import json
import threading
from pathlib import Path
from typing import Any

import pytest

import eog.runner as runner_module
from eog.run import ZAI_ROUTE_CAP
from eog.runner import RunStore, run_concurrent


def test_rolling_pool_starts_pending_rows_while_a_slow_row_runs() -> None:
    slow_started = threading.Event()
    slow_finished = threading.Event()
    release_slow = threading.Event()
    pending_started_while_slow = []
    rows = [{"task_id": str(index), "duration_units": 10 if index == 0 else 1} for index in range(5)]

    def execute(row: dict[str, int | str]) -> dict[str, int | str]:
        task_id = str(row["task_id"])
        if task_id == "0":
            slow_started.set()
            assert release_slow.wait(timeout=3)
        else:
            assert slow_started.wait(timeout=3)
        if task_id == "4":
            pending_started_while_slow.append(not slow_finished.is_set())
        if task_id == "0":
            slow_finished.set()
        elif task_id == "4":
            release_slow.set()
        return {**row, "status": "valid"}

    results = run_concurrent(rows, execute, ramp=(2,))

    assert len([row for row in results if row.get("status") == "valid"]) == 5
    assert pending_started_while_slow == [True]


@pytest.mark.parametrize(
    ("ramp", "route_cap", "expected_max"),
    [
        ((2,), None, 2),
        ((1, 2, 4, 8, 16), ZAI_ROUTE_CAP, ZAI_ROUTE_CAP),
    ],
)
def test_rolling_pool_respects_concurrency_and_route_caps(
    ramp: tuple[int, ...],
    route_cap: int | None,
    expected_max: int,
) -> None:
    lock = threading.Lock()
    release = threading.Event()
    active = 0
    maximum_active = 0
    rows = [{"task_id": str(index)} for index in range(20)]

    def execute(row: dict[str, str]) -> dict[str, str]:
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
            if active == expected_max:
                release.set()
        try:
            assert release.wait(timeout=3)
            return {**row, "status": "valid"}
        finally:
            with lock:
                active -= 1

    results = run_concurrent(rows, execute, ramp=(expected_max,), route_cap=route_cap)

    assert len([row for row in results if row.get("status") == "valid"]) == 20
    assert maximum_active == expected_max


def test_429_window_reduces_concurrency_for_later_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    lock = threading.Lock()
    active = 0
    current_rows_started = threading.Event()
    four_current_rows_started = threading.Event()
    release_window_zero = threading.Event()
    release_current_window = threading.Event()
    ramp_decided = threading.Event()
    start_active_counts = []
    results = []
    errors = []
    rows = [{"task_id": f"warm-{index}", "route": "warm"} for index in range(2)]
    rows += [{"task_id": f"window-{index}", "route": f"window-{index}"} for index in range(5)]
    rows += [{"task_id": "after-0", "route": "after"}]

    original_http_429_share = runner_module._http_429_share

    def observe_ramp_window(window: list[dict[str, object]]) -> float:
        share = original_http_429_share(window)
        if len(window) == 4 and any(row.get("task_id") == "window-1" for row in window):
            ramp_decided.set()
        return share

    monkeypatch.setattr(runner_module, "_http_429_share", observe_ramp_window)

    def execute(row: dict[str, str]) -> dict[str, str | int]:
        nonlocal active
        task_id = row["task_id"]
        with lock:
            active += 1
            start_active_counts.append((task_id, active))
            current_started = sum(name.startswith("window-") and name != "window-0" for name, _ in start_active_counts)
            if current_started == 3:
                current_rows_started.set()
            if current_started == 4:
                four_current_rows_started.set()
        try:
            if task_id == "window-0":
                assert release_window_zero.wait(timeout=3)
            elif task_id.startswith("window-"):
                assert release_current_window.wait(timeout=3)
            elif task_id == "after-0":
                assert ramp_decided.wait(timeout=3)
            is_429 = task_id == "window-1"
            status = "infra_failed" if task_id in {"window-2", "window-3", "window-4"} else "valid"
            return {
                **row,
                "status": status,
                "model_call_attempts": 1,
                "http_429_attempts": int(is_429),
            }
        finally:
            with lock:
                active -= 1

    def run() -> None:
        try:
            results.extend(run_concurrent(rows, execute, ramp=(2, 4)))
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    assert current_rows_started.wait(timeout=3)
    release_window_zero.set()
    assert four_current_rows_started.wait(timeout=3)
    release_current_window.set()
    worker.join(timeout=3)

    assert not worker.is_alive()
    assert not errors
    down_steps = [
        row
        for row in results
        if row.get("status") == "ramp"
        and row["next_concurrency"] is not None
        and row["http_429_share"] > 0.05
        and row["next_concurrency"] < row["concurrency"]
    ]
    assert len(down_steps) == 1
    assert (down_steps[0]["concurrency"], down_steps[0]["next_concurrency"]) == (4, 2)
    after_starts = [(task_id, count) for task_id, count in start_active_counts if task_id.startswith("after-")]
    assert [task_id for task_id, _ in after_starts] == ["after-0"]


def test_ramp_waits_for_current_step_window_before_increasing() -> None:
    rows = [{"task_id": str(index)} for index in range(6)]
    first_two_started = threading.Event()
    release_first = threading.Event()
    release_rest = threading.Event()
    row_two_started = threading.Event()
    lock = threading.Lock()
    active = 0
    completed = 0
    completed_at_start = {}

    def execute(row: dict[str, str]) -> dict[str, str]:
        nonlocal active, completed
        task_id = row["task_id"]
        with lock:
            active += 1
            completed_at_start[task_id] = completed
            if task_id in {"0", "1"} and len(completed_at_start) >= 2:
                first_two_started.set()
        if task_id == "0":
            assert release_first.wait(timeout=3)
        elif task_id == "2":
            row_two_started.set()
            assert release_rest.wait(timeout=3)
        elif task_id != "1":
            assert release_rest.wait(timeout=3)
        else:
            assert release_rest.wait(timeout=3)
        with lock:
            active -= 1
            completed += 1
        return {**row, "status": "valid"}

    results = []
    thread = threading.Thread(target=lambda: results.extend(run_concurrent(rows, execute, ramp=(2, 4))))
    thread.start()
    assert first_two_started.wait(timeout=3)
    release_first.set()
    assert row_two_started.wait(timeout=3)
    assert "3" not in completed_at_start
    release_rest.set()
    thread.join(timeout=3)

    assert not thread.is_alive()
    assert len([row for row in results if row.get("status") == "valid"]) == 6
    assert completed_at_start["3"] >= 2


def test_spend_stop_drains_in_flight_rows_and_starts_no_pending_row() -> None:
    spend = 0.0
    stopped_check = threading.Event()
    started_rows = []
    rows = [{"task_id": str(index)} for index in range(3)]

    def execute(row: dict[str, str]) -> dict[str, str]:
        nonlocal spend
        started_rows.append(row["task_id"])
        if row["task_id"] == "0":
            spend = 2.0
            return {**row, "status": "valid"}
        assert stopped_check.wait(timeout=3)
        return {**row, "status": "stopped"}

    def should_stop() -> bool:
        if spend > 1.0:
            stopped_check.set()
        return spend > 1.0

    results = run_concurrent(rows, execute, ramp=(2,), should_stop=should_stop)

    assert set(started_rows) == {"0", "1"}
    assert {row["task_id"] for row in results if row.get("status") in {"valid", "stopped"}} == {"0", "1"}


def test_worker_exception_is_recorded_and_the_run_continues() -> None:
    rows = [{"task_id": str(index), "trial": 1, "arm": "none"} for index in range(4)]
    checkpointed = []

    def execute(row: dict[str, str | int]) -> dict[str, str | int]:
        if row["task_id"] == "1":
            raise OSError("gym worker failed")
        return {**row, "status": "valid"}

    results = run_concurrent(rows, execute, ramp=(2,), checkpoint=checkpointed.append)

    assert {row["task_id"] for row in results if row.get("status") == "valid"} == {"0", "2", "3"}
    failed = next(row for row in results if row.get("status") == "infra_failed")
    assert failed["error_class"] == "OSError"
    assert "gym worker failed" in failed["error_message"]
    assert checkpointed == [failed]


def test_resume_skips_completed_rows_before_scheduling(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    completed = {
        "task_id": "completed",
        "trial": 1,
        "model": "model-a",
        "effort": "max",
        "arm": "skills",
        "stage": "reproduce",
        "cell": "reproduce",
        "status": "valid",
    }
    RunStore(run_dir).checkpoint(completed)
    store = RunStore(run_dir, resume=True)
    pending = [
        {**completed, "status": None},
        {
            **completed,
            "task_id": "pending",
            "status": None,
        },
    ]
    calls = []

    results = run_concurrent(
        store.rows_to_run(pending),
        lambda row: calls.append(row["task_id"]) or {**row, "status": "valid"},
        ramp=(2,),
    )

    assert calls == ["pending"]
    assert {row["task_id"] for row in results if row.get("status") == "valid"} == {"pending"}


def test_route_failure_streak_counts_distinct_task_ids() -> None:
    rows = [
        {"task_id": f"T{task_index:02d}", "trial": trial, "route": "glm"}
        for task_index in range(40)
        for trial in range(1, 4)
    ]

    def execute(row: dict[str, str | int]) -> dict[str, str | int]:
        status = "infra_failed" if row["task_id"] == "T05" else "valid"
        return {**row, "status": status}

    results = run_concurrent(rows, execute, ramp=(1,))

    assert sum(row.get("status") == "valid" for row in results) == 117
    assert sum(row.get("status") == "infra_failed" for row in results) == 3
    assert not any(row.get("status") == "stopped" for row in results)


def test_route_stops_after_three_distinct_task_failures() -> None:
    rows = [
        {"task_id": f"T{task_index:02d}", "trial": trial, "route": "glm"}
        for task_index in range(40)
        for trial in range(1, 4)
    ]
    failing_tasks = {"T05", "T06", "T07"}

    def execute(row: dict[str, str | int]) -> dict[str, str | int]:
        status = "infra_failed" if row["task_id"] in failing_tasks else "valid"
        return {**row, "status": status}

    results = run_concurrent(rows, execute, ramp=(1,))
    failures = [row for row in results if row.get("status") == "infra_failed"]

    assert [(row["task_id"], row["trial"]) for row in failures] == [
        ("T05", 1),
        ("T05", 2),
        ("T05", 3),
        ("T06", 1),
        ("T06", 2),
        ("T06", 3),
        ("T07", 1),
    ]
    assert any(
        row.get("status") == "stopped" and row.get("stop_reason") == "three_consecutive_infra_failed" for row in results
    )


def test_resume_moves_failed_rows_to_end_in_plan_order(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    planned = [
        {
            "task_id": f"T{task_index:02d}",
            "trial": 1,
            "model": "glm-5.3-flash",
            "effort": "max",
            "arm": "none",
            "stage": "reproduce",
            "cell": "reproduce",
        }
        for task_index in range(8)
    ]
    store = RunStore(run_dir)

    for index, status in ((0, "valid"), (1, "infra_failed"), (2, "partial"), (3, "stopped")):
        store.checkpoint({**planned[index], "status": status})
    store.checkpoint({**planned[5], "status": "valid"})
    store.checkpoint({**planned[5], "status": "infra_failed"})
    store.checkpoint({**planned[6], "status": "infra_failed"})
    store.checkpoint({**planned[6], "status": "valid"})

    pending = RunStore(run_dir, resume=True).rows_to_run(planned)

    assert [row["task_id"] for row in pending] == ["T02", "T04", "T07", "T03", "T01", "T05"]
    assert RunStore(run_dir).rows_to_run(planned) == planned


def test_resume_orders_stopped_rows_before_infra_failed_rows(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    planned = [
        {
            "task_id": task_id,
            "trial": trial,
            "model": "glm-5.3-flash",
            "effort": "max",
            "arm": arm,
            "stage": "reproduce",
            "cell": "reproduce",
        }
        for task_id in ("A", "B", "C", "D", "E", "F", "G")
        for trial in range(1, 4)
        for arm in ("sk", "no")
    ]
    saved_statuses = {
        "A": "valid",
        "B": "infra_failed",
        "C": "stopped",
        "D": "stopped",
        "E": "stopped",
    }
    store = RunStore(run_dir)

    for row in planned:
        if row["task_id"] in saved_statuses:
            store.checkpoint({**row, "status": saved_statuses[row["task_id"]]})
        elif row["task_id"] == "G" and row["trial"] == 1 and row["arm"] == "sk":
            store.checkpoint({**row, "status": "partial"})

    pending = RunStore(run_dir, resume=True).rows_to_run(planned)
    first_group = [row for row in planned if row["task_id"] in {"F", "G"}]
    stopped_rows = [row for row in planned if row["task_id"] in {"C", "D", "E"}]
    infra_failed_rows = [row for row in planned if row["task_id"] == "B"]

    assert pending == first_group + stopped_rows + infra_failed_rows
    assert len(pending) == 36


def test_resume_moves_all_rows_of_failing_task_identity_after_healthy_rows(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    planned = [
        {
            "task_id": f"T{task_index}",
            "trial": trial,
            "model": "glm-5.3-flash",
            "effort": "max",
            "arm": arm,
            "stage": "executor",
            "cell": "c",
        }
        for task_index in range(10)
        for trial in range(1, 4)
        for arm in ("skills", "none")
    ]
    store = RunStore(run_dir)

    for row in planned:
        task_id = row["task_id"]
        if task_id in {"T0", "T1", "T2"}:
            status = "valid"
        elif task_id in {"T3", "T4"} or (task_id == "T5" and row["arm"] == "skills" and row["trial"] == 1):
            status = "infra_failed"
        elif task_id in {"T5", "T6", "T7", "T8"}:
            status = "stopped"
        else:
            continue
        store.checkpoint({**row, "status": status})

    pending = RunStore(run_dir, resume=True).rows_to_run(planned)
    unsaved_rows = [row for row in planned if row["task_id"] == "T9"]
    healthy_stopped_rows = [
        row
        for row in planned
        if (row["task_id"] == "T5" and row["arm"] == "none") or row["task_id"] in {"T6", "T7", "T8"}
    ]
    failing_task_rows = [
        row for row in planned if row["task_id"] in {"T3", "T4"} or (row["task_id"] == "T5" and row["arm"] == "skills")
    ]

    assert pending == unsaved_rows + healthy_stopped_rows + failing_task_rows
    assert len(pending) == 42


def _run_resume_after_stop(run_dir: Path, *, signal_stop: bool) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    bad_tasks = {"T2", "T3", "T4"}
    outage_tasks = {"T5", "T6", "T7"}
    planned = [
        {
            "task_id": task_id,
            "trial": trial,
            "model": "glm-5.3-flash",
            "effort": "max",
            "arm": arm,
            "stage": "executor",
            "cell": "executor",
            "route": "glm-5.3-flash",
        }
        for task_id in (f"T{index}" for index in range(8))
        for trial in (1, 2)
        for arm in ("none", "skills")
    ]
    state: dict[str, Any] = {"phase": "run1"}

    def execute(row: dict[str, Any], abort: threading.Event | None = None) -> dict[str, Any]:
        failed = row["task_id"] in bad_tasks or (
            not signal_stop and state["phase"] == "resume1" and row["task_id"] in outage_tasks
        )
        result = (
            {**row, "status": "infra_failed", "error_class": "InfrastructureError"}
            if failed
            else {**row, "status": "valid", "reward": 1}
        )
        state["store"].checkpoint(result)
        return result

    store = RunStore(run_dir)
    state["store"] = store
    run_concurrent(planned, execute, ramp=(1,), checkpoint=store.checkpoint, execute_with_abort=execute)

    store = RunStore(run_dir, resume=True)
    state["store"] = store
    first_resume_rows = store.rows_to_run(planned)
    state["phase"] = "resume1"
    if signal_stop:
        stop_requested = threading.Event()
        calls = 0

        def execute_until_signal(row: dict[str, Any], abort: threading.Event | None = None) -> dict[str, Any]:
            nonlocal calls
            calls += 1
            if calls >= 2:
                stop_requested.set()
            return execute(row, abort)

        run_concurrent(
            first_resume_rows,
            execute_until_signal,
            ramp=(1,),
            checkpoint=store.checkpoint,
            stop_requested=stop_requested,
            signal_name=lambda: "SIGINT",
            execute_with_abort=execute_until_signal,
        )
    else:
        run_concurrent(
            first_resume_rows,
            execute,
            ramp=(1,),
            checkpoint=store.checkpoint,
            execute_with_abort=execute,
        )

    history = [json.loads(line) for line in (run_dir / "rows.jsonl").read_text().splitlines() if line.strip()]
    latest_status = {}
    infra_failed_keys = set()
    for row in history:
        key = row["task_id"], row["trial"], row["arm"]
        latest_status[key] = row["status"]
        if row["status"] == "infra_failed":
            infra_failed_keys.add(key)
    assert any(latest_status[key] == "stopped" for key in infra_failed_keys)

    pending = RunStore(run_dir, resume=True).rows_to_run(planned)
    return pending, history


def _assert_failure_history_rows_are_last(pending: list[dict[str, Any]], history: list[dict[str, Any]]) -> None:
    failing_identities = {(row["task_id"], row["arm"]) for row in history if row["status"] == "infra_failed"}
    failing_positions = [
        index for index, row in enumerate(pending) if (row["task_id"], row["arm"]) in failing_identities
    ]
    healthy_positions = [
        index for index, row in enumerate(pending) if (row["task_id"], row["arm"]) not in failing_identities
    ]

    assert failing_positions
    assert healthy_positions
    assert min(failing_positions) > max(healthy_positions)


def test_resume_keeps_failure_history_after_sigint_stop(tmp_path: Path) -> None:
    pending, history = _run_resume_after_stop(tmp_path / "signal", signal_stop=True)

    _assert_failure_history_rows_are_last(pending, history)


def test_resume_keeps_failure_history_after_second_route_stop(tmp_path: Path) -> None:
    pending, history = _run_resume_after_stop(tmp_path / "route", signal_stop=False)

    _assert_failure_history_rows_are_last(pending, history)


def test_route_capped_ramp_has_no_noop_records() -> None:
    rows = [{"task_id": str(index)} for index in range(12)]

    results = run_concurrent(
        rows,
        lambda row: {**row, "status": "valid"},
        ramp=(1, 2, 4, 8),
        route_cap=4,
    )

    assert len([row for row in results if row.get("status") == "valid"]) == 12
    ramp_records = [row for row in results if row.get("status") == "ramp"]
    assert ramp_records
    assert all(row["next_concurrency"] != row["concurrency"] for row in ramp_records)


def test_ramp_record_is_written_before_run_finishes(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "run")
    ramp_visible = threading.Event()
    release_row = threading.Event()
    run_finished = threading.Event()
    visible_records = []
    errors = []

    def execute(row: dict[str, str]) -> dict[str, str]:
        if row["task_id"] == "1":
            visible_records.extend(
                json.loads(line) for line in store.ramp_records.path.read_text().splitlines() if line.strip()
            )
            ramp_visible.set()
            assert release_row.wait(timeout=3)
        return {**row, "status": "valid"}

    def run() -> None:
        try:
            run_concurrent(
                [{"task_id": "0"}, {"task_id": "1"}],
                execute,
                ramp=(1, 2),
                ramp_checkpoint=store.checkpoint_ramp,
            )
        except BaseException as error:
            errors.append(error)
        finally:
            run_finished.set()

    thread = threading.Thread(target=run)
    thread.start()

    try:
        assert ramp_visible.wait(timeout=3)
        assert not run_finished.is_set()
        assert len(visible_records) == 1
        assert visible_records[0]["status"] == "ramp"
    finally:
        release_row.set()
        thread.join(timeout=3)

    assert not thread.is_alive()
    assert not errors
