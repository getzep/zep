from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

import eog.run as run_module
import eog.runner as runner_module
from eog.agent import SKILLS_HEADER, run_agent, selected_skills_block
from eog.dataset import Task, load_tasks
from eog.errors import InfrastructureError, PolicyFlagError
from eog.gym import GymClient
from eog.ledger import CostLedger
from eog.mcp_client import MCPClient
from eog.providers.base import ModelTurn, ToolCall, Usage
from eog.run import CompositeMCPClient
from eog.runner import RunManifest, RunStore, run_concurrent
from eog.stirrup_constants import FINISH_TOOL_NAME


class FakeGym:
    def __init__(self, reset_success: bool = True) -> None:
        self.reset_success = reset_success
        self.baseline_path: Path | None = None

    def seed(self, *args: Any) -> None:
        pass

    def check_reset(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
        self.baseline_path = kwargs.get("baseline_path")
        return SimpleNamespace(success=self.reset_success, passed=(self.reset_success,))

    def verify(self, *args: Any) -> SimpleNamespace:
        return SimpleNamespace(success=True, passed=("passed",))

    def delete(self, *args: Any) -> None:
        pass

    def close(self) -> None:
        pass


class FakeMCP:
    def close(self) -> None:
        pass


class FakeCompositeMCP(FakeMCP):
    def start(self, task: Any) -> list[dict[str, Any]]:
        return []


def test_mcp_tool_timeout_is_an_infrastructure_error() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise TimeoutError("The read operation timed out")

    client = MCPClient("http://mcp.test", database_id="db", transport=httpx.MockTransport(timeout))
    try:
        with pytest.raises(InfrastructureError, match="MCP transport failed") as error:
            client.call_tool("search", {})
        assert isinstance(error.value.__cause__, TimeoutError)
    finally:
        client.close()


def test_gym_verifier_timeout_is_an_infrastructure_error() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise TimeoutError("The read operation timed out")

    gym = GymClient("http://gym.test", name="gym", transport=httpx.MockTransport(timeout))
    task = SimpleNamespace(
        task_id="timeout-repro",
        verifiers=[{"gym_name": "gym", "validation_config": {"query": "SELECT 1", "expected_value": 1}}],
        gym_servers_config=[],
    )
    try:
        with pytest.raises(InfrastructureError, match="Gym SQL query failed") as error:
            gym.verify(task, {"gym": "db"})
        assert isinstance(error.value.__cause__, TimeoutError)
    finally:
        gym.client.close()


def test_container_image_digests_only_include_task_servers(monkeypatch: pytest.MonkeyPatch) -> None:
    containers = []
    monkeypatch.setattr(run_module, "image_digest", lambda container: containers.append(container) or "image-sha256")
    task = SimpleNamespace(gym_servers_config=({"mcp_server_name": "gym-itsm-mcp"},))

    assert run_module._container_image_digests((task,)) == {"itsm": "image-sha256"}
    assert containers == ["eog-itsm-opened-by-patch"]

    containers.clear()
    assert run_module._container_image_digests((task,), gym_set=2) == {"itsm": "image-sha256"}
    assert containers == ["eog-set2-itsm"]


def test_executor_keeps_an_explicit_task_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    task = next(item for item in load_tasks() if not item.known_defect)
    monkeypatch.setattr(
        run_module,
        "sample_score_tasks",
        lambda *_args, **_kwargs: pytest.fail("Explicit task selections must not be sampled."),
    )

    assert run_module._executor_tasks((task,), {}, explicit_selection=True) == (task,)


def test_overlay_split_allows_unmatched_revised_ids_and_rejects_unknown_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base_tasks = (
        SimpleNamespace(task_id="teach-task", text="base teach text"),
        SimpleNamespace(task_id="score-task", text="base score text"),
    )
    overlay_tasks = (
        SimpleNamespace(task_id="teach-task", text="changed teach text"),
        SimpleNamespace(task_id="score-task", text="changed score text"),
    )
    monkeypatch.setattr(run_module, "load_tasks", lambda: base_tasks)
    monkeypatch.setattr(
        run_module,
        "make_split",
        lambda tasks: {
            "tasks": {
                task.task_id: {"side": "teach" if task.text.startswith("base teach") else "score"} for task in tasks
            }
        },
    )
    overlay_dir = tmp_path / "overlay"
    overlay_dir.mkdir()
    revised_dir = tmp_path / "data" / "revised"
    revised_csm_dir = revised_dir / "csm"
    revised_csm_dir.mkdir(parents=True)
    unmatched_revised_id = "revised-orphan"
    (revised_csm_dir / f"{unmatched_revised_id}.json").write_text(json.dumps({"task_id": unmatched_revised_id}))
    (overlay_dir / f"{unmatched_revised_id}.json").write_text(json.dumps({"task_id": unmatched_revised_id}))

    split = run_module._split_for_tasks(overlay_tasks, overlay_dir, revised_dir=revised_dir)

    assert {task_id: value["side"] for task_id, value in split["tasks"].items()} == {
        "teach-task": "teach",
        "score-task": "score",
    }
    assert [task.text for task in overlay_tasks] == ["changed teach text", "changed score text"]
    (overlay_dir / "unknown.json").write_text(json.dumps({"task_id": "unknown"}))
    with pytest.raises(SystemExit, match="Overlay task IDs are missing from the base split"):
        run_module._split_for_tasks(overlay_tasks, overlay_dir, revised_dir=revised_dir)


def test_write_split_uses_base_tasks_with_overlay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def task(task_id: str, user_prompt: str, verifiers: tuple[dict[str, Any], ...] = ()) -> Task:
        return Task(
            task_id=task_id,
            domain="teams",
            system_prompt="",
            user_prompt=user_prompt,
            selected_tools=(),
            restricted_tools=(),
            mcp_endpoint="/mcp",
            number_of_runs=1,
            reset_database_between_runs=True,
            gym_servers_config=(),
            verifiers=verifiers,
        )

    base_tasks = (
        task("task-1", "Find the approved calendar"),
        task("task-2", "Update the office equipment"),
    )
    overlay_tasks = (
        task("task-1", "Find the approved calendar", ({"name": "opened_by"},)),
        base_tasks[1],
    )
    overlay_dir = tmp_path / "overlay"
    overlay_dir.mkdir()
    written_splits: list[dict[str, Any]] = []
    written_tasks: list[tuple[Task, ...]] = []
    monkeypatch.setattr(
        run_module,
        "load_tasks",
        lambda *, overlay_dir=None: overlay_tasks if overlay_dir is not None else base_tasks,
    )
    monkeypatch.setattr(run_module, "load_task_sample", lambda tasks, _selection: tasks)
    monkeypatch.setattr(
        run_module,
        "write_split",
        lambda tasks, _path: (
            written_tasks.append(tuple(tasks)),
            written_splits.append(run_module.make_split(tasks)),
        ),
    )
    monkeypatch.setattr(run_module, "_resolve_run_config", lambda *_args: ("gpt-6-luna", "low", 1))
    monkeypatch.setattr(sys, "argv", ["eog.run", "--overlay-dir", str(overlay_dir), "--write-split"])

    run_module._run_main(run_module._RunSignalState())

    base_split = run_module.make_split(base_tasks)
    assert written_splits == [base_split]
    assert written_tasks == [base_tasks]
    assert overlay_tasks[0].verifiers != base_tasks[0].verifiers


def test_run_rejects_non_verifier_overlay_change_before_writing_split(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = next(item for item in load_tasks() if not item.known_defect)
    overlay_dir = tmp_path / "overlay"
    overlay_dir.mkdir()
    monkeypatch.setattr(
        run_module,
        "load_tasks",
        lambda *, overlay_dir=None: (
            (replace(task, user_prompt="changed prompt"),) if overlay_dir is not None else (task,)
        ),
    )
    monkeypatch.setattr(run_module, "_resolve_run_config", lambda *_args: ("gpt-6-luna", "low", 1))
    monkeypatch.setattr(sys, "argv", ["eog.run", "--overlay-dir", str(overlay_dir), "--write-split"])
    monkeypatch.setattr(
        run_module,
        "write_split",
        lambda *_args: pytest.fail("The run must reject changed task fields before writing the split."),
    )

    with pytest.raises(ValueError, match=r"non-verifier fields.*user_prompt"):
        run_module._run_main(run_module._RunSignalState())


def test_reproduction_report_excludes_external_overlay_task(tmp_path: Path) -> None:
    overlay_dir = tmp_path / "overlay"
    overlay_dir.mkdir()
    external_task_id = "external-overlay-task"
    (overlay_dir / f"{external_task_id}.json").write_text(json.dumps({"task_id": external_task_id}))
    tasks = (
        SimpleNamespace(task_id=external_task_id),
        SimpleNamespace(task_id="unchanged-task"),
    )

    report = run_module.reproduction_report(
        [
            {"cell": "reproduce", "task_id": external_task_id, "status": "valid", "reward": 1},
            {"cell": "reproduce", "task_id": "unchanged-task", "status": "valid", "reward": 1},
        ],
        overlay_task_ids=run_module._reproduction_overlay_ids(tasks, overlay_dir),
        known_defect_ids=set(),
    )

    assert report["without_overlay"] == {"n": 1, "passed": 1, "p_hat": 1.0}
    assert report["overlay_task_ids"] == [external_task_id]


def test_run_requires_noop_baseline_for_overlay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    overlay_dir = tmp_path / "overlay"
    overlay_dir.mkdir()
    monkeypatch.setattr(sys, "argv", ["eog.run", "--overlay-dir", str(overlay_dir)])
    monkeypatch.setattr(run_module, "_resolve_run_config", lambda *args: ("gpt-6-luna", "low", 1))
    monkeypatch.setattr(run_module, "load_tasks", lambda **kwargs: ())
    monkeypatch.setattr(run_module, "load_task_sample", lambda available, path: available)

    with pytest.raises(SystemExit, match="No-op baseline file does not exist:"):
        run_module._run_main(run_module._RunSignalState())


def test_unpriced_cost_limit_fails_before_task_loading(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "eog.run",
            "--stage",
            "executor",
            "--model",
            "deepseek-v4.1-flash-1017",
            "--effort",
            "low",
            "--max-usd",
            "1",
        ],
    )
    monkeypatch.setattr(
        run_module,
        "load_tasks",
        lambda **kwargs: pytest.fail("The run must reject the unpriced model before loading tasks."),
    )
    monkeypatch.setattr(
        run_module,
        "_route",
        lambda *args, **kwargs: pytest.fail("The run must reject the unpriced model before a model call."),
    )

    with pytest.raises(SystemExit, match=r"unpriced model deepseek-v4\.1-flash-1017"):
        run_module._run_main(run_module._RunSignalState())


def test_explicit_noop_baseline_overrides_overlay_default(tmp_path: Path) -> None:
    baseline_path = tmp_path / "custom.jsonl"
    baseline_path.write_text("{}\n")
    args = SimpleNamespace(noop_baseline=baseline_path, overlay_dir=tmp_path / "overlay")

    assert run_module._resolve_noop_baseline_path(args) == baseline_path.resolve()


def _execute_arm_a_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_run_agent: Any,
    gym_instance: FakeGym | None = None,
    noop_baseline_path: Path | None = None,
    task_override: Task | None = None,
    original_task: Task | None = None,
    excluded_task_reasons: dict[str, str] | None = None,
) -> dict[str, Any]:
    task = task_override or next(item for item in load_tasks() if item.domain == "calendar")
    seed = tmp_path / "seed.sql"
    seed.write_text("SELECT 1;")
    gym = gym_instance or FakeGym()
    monkeypatch.setattr(
        run_module,
        "_gym_clients",
        lambda current_task, ids: {current_task.gym_servers_config[0]["mcp_server_name"]: gym},
    )
    monkeypatch.setattr(run_module, "_seed_path", lambda name: seed)
    monkeypatch.setattr(run_module, "MCPClient", lambda *args, **kwargs: FakeMCP())
    monkeypatch.setattr(run_module, "CompositeMCPClient", lambda clients: FakeCompositeMCP())
    monkeypatch.setattr(run_module, "_route", lambda *args, **kwargs: object())
    monkeypatch.setattr(run_module, "run_agent", fake_run_agent)
    return run_module._execute_row(
        {"task_id": task.task_id, "trial": 1, "arm": "none", "cell": "teacher"},
        task_by_id={task.task_id: task},
        model="test-model",
        effort="low",
        run_id="run",
        store=RunStore(tmp_path / "run"),
        cost_ledger=CostLedger(tmp_path / "cost.jsonl", max_usd=20),
        capture_dir=None,
        skills_block=None,
        skills_hash="",
        skills=[],
        zep=None,
        agent_uuid=None,
        noop_baseline_path=noop_baseline_path,
        original_task_by_id={task.task_id: original_task} if original_task is not None else None,
        excluded_task_reasons=excluded_task_reasons,
    )


def test_arm_a_row_counts_successful_model_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run_agent(*args: Any, **kwargs: Any) -> SimpleNamespace:
        kwargs["context"]["model_call_latencies"].extend([0.1, 0.2, 0.3])
        return SimpleNamespace(
            finish_reason="finished",
            final_text="Task complete.",
            turns=2,
            usage={},
            events=(),
            active_seconds=1.0,
        )

    result = _execute_arm_a_row(tmp_path, monkeypatch, fake_run_agent)

    assert result["model_calls"] == 3
    assert result["model_call_attempts"] == 3
    assert "corrected_reward" not in result
    assert len(result["latencies"]["verifier_calls_seconds"]) == 1


@pytest.mark.parametrize(
    ("original_verifiers", "corrected_verifiers", "expected_reward", "expected_corrected_reward"),
    [
        (({"name": "removed_check"},), (), 1, 1),
        ((), ({"name": "opened_by"},), 1, 0),
    ],
)
def test_overlay_verifies_original_and_corrected_scores_before_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    original_verifiers: tuple[dict[str, str], ...],
    corrected_verifiers: tuple[dict[str, str], ...],
    expected_reward: int,
    expected_corrected_reward: int,
) -> None:
    base = next(item for item in load_tasks() if item.domain == "calendar" and not item.known_defect)
    original = replace(base, verifiers=original_verifiers)
    corrected = replace(base, verifiers=corrected_verifiers)
    events: list[tuple[str, tuple[str, ...]]] = []

    class ScoringGym(FakeGym):
        def verify(self, task: Task, _database_ids: dict[str, str]) -> SimpleNamespace:
            names = tuple(verifier["name"] for verifier in task.verifiers)
            events.append(("verify", names))
            passed = (False,) if names == ("opened_by",) else (True,) if names else ()
            return SimpleNamespace(
                success=all(passed),
                passed=passed,
                actual=tuple("actual" for _ in passed),
                expected=tuple("expected" for _ in passed),
                errors=tuple(None for _ in passed),
            )

        def delete(self, *_args: Any) -> None:
            events.append(("delete", ()))

    observed_agent_tasks: list[Task] = []

    def fake_run_agent(task: Task, *args: Any, **kwargs: Any) -> SimpleNamespace:
        observed_agent_tasks.append(task)
        kwargs["context"]["model_call_latencies"].append(0.1)
        return SimpleNamespace(
            finish_reason="finished",
            final_text="Task complete.",
            turns=1,
            usage={},
            events=(),
            active_seconds=1.0,
        )

    result = _execute_arm_a_row(
        tmp_path,
        monkeypatch,
        fake_run_agent,
        gym_instance=ScoringGym(),
        task_override=corrected,
        original_task=original,
    )

    assert observed_agent_tasks == [corrected]
    assert events == [
        ("verify", tuple(verifier["name"] for verifier in original_verifiers)),
        ("verify", tuple(verifier["name"] for verifier in corrected_verifiers)),
        ("delete", ()),
    ]
    assert result["reward"] == expected_reward
    assert result["corrected_reward"] == expected_corrected_reward
    assert result["verifier_passes"] == ([True] if original_verifiers else [])
    assert result["corrected_verifier_passes"] == ([False] if corrected_verifiers == ({"name": "opened_by"},) else [])
    assert len(result["latencies"]["verifier_calls_seconds"]) == 2


def test_excluded_task_keeps_corrected_scores_null(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = next(item for item in load_tasks() if item.domain == "calendar" and not item.known_defect)
    original = replace(base, verifiers=())
    corrected = replace(base, verifiers=({"name": "opened_by"},))
    excluded_reason = "excluded from corrected scoring"
    verify_calls: list[tuple[str, ...]] = []

    class ScoringGym(FakeGym):
        def verify(self, task: Task, _database_ids: dict[str, str]) -> SimpleNamespace:
            verify_calls.append(tuple(verifier["name"] for verifier in task.verifiers))
            passed = tuple(False for _ in task.verifiers)
            return SimpleNamespace(
                success=all(passed),
                passed=passed,
                actual=tuple("actual" for _ in passed),
                expected=tuple("expected" for _ in passed),
                errors=tuple(None for _ in passed),
            )

    result = _execute_arm_a_row(
        tmp_path,
        monkeypatch,
        lambda *args, **kwargs: SimpleNamespace(
            finish_reason="finished",
            final_text="Task complete.",
            turns=1,
            usage={},
            events=(),
            active_seconds=1.0,
        ),
        gym_instance=ScoringGym(),
        task_override=corrected,
        original_task=original,
        excluded_task_reasons={base.task_id: excluded_reason},
    )

    assert result["corrected_reward"] is None
    assert result["corrected_verifier_passes"] is None
    assert result["corrected_status"] is None
    assert result["corrected_excluded_reason"] == excluded_reason
    assert verify_calls == [()]


def test_corrected_verifier_failure_preserves_original_scores(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = next(item for item in load_tasks() if item.domain == "calendar" and not item.known_defect)
    original = replace(base, verifiers=({"name": "original_check"},))
    corrected = replace(base, verifiers=({"name": "opened_by"},))
    verify_calls: list[tuple[str, ...]] = []
    fake_bearer_token = "fake-token-123456"
    sql_fragment = "SELECT account FROM incidents"
    score_metadata_records: list[dict[str, Any]] = []

    class ScoringGym(FakeGym):
        def verify(self, task: Task, _database_ids: dict[str, str]) -> SimpleNamespace:
            names = tuple(verifier["name"] for verifier in task.verifiers)
            verify_calls.append(names)
            if names == ("opened_by",):
                raise RuntimeError(f"Bearer {fake_bearer_token} content={sql_fragment}")
            return SimpleNamespace(
                success=True,
                passed=(True,),
                actual=("original-actual",),
                expected=("original-expected",),
                errors=(None,),
            )

    class RecordingRecorder:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def __enter__(self) -> RecordingRecorder:
            return self

        def __exit__(self, *_args: Any) -> None:
            pass

        def close(self, *_args: Any, **kwargs: Any) -> None:
            score_metadata_records.append(kwargs["score_metadata"])

    monkeypatch.setattr(run_module, "classify_episode", lambda *_args: "valid")
    monkeypatch.setattr(run_module, "_should_record_trajectory", lambda *_args: True)
    monkeypatch.setattr(run_module, "TrajectoryRecorder", RecordingRecorder)
    result = _execute_arm_a_row(
        tmp_path,
        monkeypatch,
        lambda *args, **kwargs: SimpleNamespace(
            finish_reason="finished",
            final_text="Task complete.",
            turns=1,
            usage={},
            events=(),
            active_seconds=1.0,
        ),
        gym_instance=ScoringGym(),
        task_override=corrected,
        original_task=original,
    )

    assert verify_calls == [("original_check",), ("opened_by",)]
    assert result["reward"] == 1
    assert result["verifier_passes"] == [True]
    assert result["status"] == "valid"
    assert result["corrected_reward"] is None
    assert result["corrected_verifier_passes"] is None
    assert result["corrected_status"] == "infra_failed"
    assert result["corrected_error"] == {
        "error_class": "RuntimeError",
        "error_message": "[redacted] content=[redacted]",
    }
    assert len(score_metadata_records) == 1
    assert score_metadata_records[0]["corrected"]["error"] == result["corrected_error"]
    assert fake_bearer_token not in json.dumps(result)
    assert sql_fragment not in json.dumps(result)
    assert fake_bearer_token not in json.dumps(score_metadata_records)
    assert sql_fragment not in json.dumps(score_metadata_records)
    assert len(result["latencies"]["verifier_calls_seconds"]) == 2


def test_excluded_task_loader_validates_task_ids_and_reasons(tmp_path: Path) -> None:
    path = tmp_path / "excluded_tasks.json"
    path.write_text(json.dumps([{"task_id": "task-1", "reason": "bad corrected checks"}]))
    tasks = (SimpleNamespace(task_id="task-1"),)

    assert run_module._load_excluded_task_reasons(path, tasks) == {"task-1": "bad corrected checks"}


def test_row_reset_uses_selected_noop_baseline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gym = FakeGym()
    baseline_path = tmp_path / "overlay" / "noop_baseline.jsonl"

    _execute_arm_a_row(
        tmp_path,
        monkeypatch,
        lambda *args, **kwargs: SimpleNamespace(
            finish_reason="finished",
            final_text="Task complete.",
            turns=1,
            usage={},
            events=(),
            active_seconds=1.0,
        ),
        gym_instance=gym,
        noop_baseline_path=baseline_path,
    )

    assert gym.baseline_path == baseline_path


def test_gym_delete_error_keeps_the_row_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gym = FakeGym()

    def fail_delete(*args: Any) -> None:
        raise OSError("gym cleanup failed")

    gym.delete = fail_delete

    def fake_run_agent(*args: Any, **kwargs: Any) -> SimpleNamespace:
        kwargs["context"]["model_call_latencies"].append(0.1)
        return SimpleNamespace(
            finish_reason="finished",
            final_text="Task complete.",
            turns=1,
            usage={},
            events=(),
            active_seconds=1.0,
        )

    result = _execute_arm_a_row(tmp_path, monkeypatch, fake_run_agent, gym_instance=gym)

    assert result["status"] == "valid"
    assert result["cleanup_error"][0]["error_class"] == "OSError"
    assert result["cleanup_error"][0]["error_message"] == "gym cleanup failed"


def test_arm_a_row_keeps_final_text_separate_from_finish_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run_agent(*args: Any, **kwargs: Any) -> SimpleNamespace:
        kwargs["context"]["model_call_latencies"].append(0.1)
        return SimpleNamespace(
            finish_reason="finished",
            final_text="The task is complete.",
            turns=1,
            usage={},
            events=(),
            active_seconds=1.0,
        )

    result = _execute_arm_a_row(tmp_path, monkeypatch, fake_run_agent)

    assert result["finish_reason"] == "finished"
    assert result["claude_final_text"] == "The task is complete."


def test_finish_tool_reason_is_final_text_not_finish_reason() -> None:
    task = next(item for item in load_tasks() if item.domain == "calendar")

    class FinishRoute:
        model = "claude-opus-5-5"

        def complete(self, *args: Any, **kwargs: Any) -> ModelTurn:
            return ModelTurn(
                text="",
                tool_calls=(
                    ToolCall(
                        id="finish-1",
                        name=FINISH_TOOL_NAME,
                        arguments={"reason": "The final answer.", "paths": []},
                    ),
                ),
                raw_usage={},
                usage=Usage(),
                latency_seconds=0.1,
            )

    result = run_agent(task, FinishRoute(), FakeMCP(), [], effort="max", selected_tools=[])

    assert result.finish_reason == "finished"
    assert result.final_text == "The final answer."


@pytest.mark.parametrize(
    ("policy_failures", "expected_status", "expected_attempts"), [(1, "valid", 2), (2, "policy_flagged", 2)]
)
def test_policy_error_retries_once_and_records_second_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    policy_failures: int,
    expected_status: str,
    expected_attempts: int,
) -> None:
    task = next(item for item in load_tasks() if item.domain == "calendar")
    seed = tmp_path / "seed.sql"
    seed.write_text("SELECT 1;")
    gym = FakeGym()
    calls = 0
    latency_lists: list[list[float]] = []

    def fake_run_agent(*args: Any, **kwargs: Any) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        latency_list = kwargs["context"]["model_call_latencies"]
        latency_lists.append(latency_list)
        latency_list.append(0.1)
        if calls <= policy_failures:
            raise PolicyFlagError("provider policy flag")
        return SimpleNamespace(
            finish_reason="finished",
            turns=1,
            usage={"input": 0, "cache_read": 0, "output": 0, "reasoning": None},
            events=(),
            active_seconds=1.0,
        )

    monkeypatch.setattr(
        run_module, "_gym_clients", lambda task, ids: {task.gym_servers_config[0]["mcp_server_name"]: gym}
    )
    monkeypatch.setattr(run_module, "_seed_path", lambda name: seed)
    monkeypatch.setattr(run_module, "MCPClient", lambda *args, **kwargs: FakeMCP())
    monkeypatch.setattr(run_module, "CompositeMCPClient", lambda clients: FakeCompositeMCP())
    monkeypatch.setattr(run_module, "_route", lambda *args, **kwargs: object())
    monkeypatch.setattr(run_module, "run_agent", fake_run_agent)

    store = RunStore(tmp_path / "run")
    result = run_module._execute_row(
        {"task_id": task.task_id, "trial": 1, "arm": "none", "cell": "executor"},
        task_by_id={task.task_id: task},
        model="test-model",
        effort="low",
        run_id="run",
        store=store,
        cost_ledger=CostLedger(tmp_path / "cost.jsonl", max_usd=20),
        capture_dir=None,
        skills_block=None,
        skills_hash="",
        skills=[],
        zep=None,
        agent_uuid=None,
    )
    assert calls == expected_attempts
    assert latency_lists[0] is not latency_lists[1]
    assert result["status"] == expected_status
    if expected_status == "policy_flagged":
        assert result["reward"] == 0
        assert store.policy_flagged.path.exists()
        assert not store.rows.path.exists()
    else:
        assert result["reward"] == 1
        assert result["model_calls"] == 1
        assert store.rows.path.exists()


def test_reset_check_accepts_expected_verifier_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = next(item for item in load_tasks() if item.domain == "calendar")
    seed = tmp_path / "seed.sql"
    seed.write_text("SELECT 1;")
    gym = FakeGym(reset_success=False)

    def fake_run_agent(*args: Any, **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(
            finish_reason="finished",
            turns=1,
            usage={"input": 0, "cache_read": 0, "output": 0, "reasoning": None},
            events=(),
            active_seconds=1.0,
        )

    monkeypatch.setattr(
        run_module, "_gym_clients", lambda task, ids: {task.gym_servers_config[0]["mcp_server_name"]: gym}
    )
    monkeypatch.setattr(run_module, "_seed_path", lambda name: seed)
    monkeypatch.setattr(run_module, "MCPClient", lambda *args, **kwargs: FakeMCP())
    monkeypatch.setattr(run_module, "CompositeMCPClient", lambda clients: FakeCompositeMCP())
    monkeypatch.setattr(run_module, "_route", lambda *args, **kwargs: object())
    monkeypatch.setattr(run_module, "run_agent", fake_run_agent)

    result = run_module._execute_row(
        {"task_id": task.task_id, "trial": 1, "arm": "none", "cell": "executor"},
        task_by_id={task.task_id: task},
        model="test-model",
        effort="low",
        run_id="run",
        store=RunStore(tmp_path / "run"),
        cost_ledger=CostLedger(tmp_path / "cost.jsonl", max_usd=20),
        capture_dir=None,
        skills_block=None,
        skills_hash="",
        skills=[],
        zep=None,
        agent_uuid=None,
    )

    assert result["status"] == "valid"
    assert result["reward"] == 1


def test_known_defect_is_invalid_before_reset_check(tmp_path: Path) -> None:
    task = next(item for item in load_tasks() if item.known_defect_reasons == ("verifier_schema",))

    class UnusedGym:
        def __init__(self) -> None:
            raise AssertionError("The invalid task must not connect to a gym.")

    result = run_module._execute_row(
        {"task_id": task.task_id, "trial": 1, "arm": "none", "cell": "reproduce"},
        task_by_id={task.task_id: task},
        model="test-model",
        effort="low",
        run_id="run",
        store=RunStore(tmp_path / "run"),
        cost_ledger=CostLedger(tmp_path / "cost.jsonl", max_usd=20),
        capture_dir=None,
        skills_block=None,
        skills_hash="",
        skills=[],
        zep=None,
        agent_uuid=None,
    )

    assert result["status"] == "invalid"
    assert result["reward"] == 0
    assert result["known_defect_reasons"] == ["verifier_schema"]


def test_missing_selected_tool_is_invalid_without_model_calls(tmp_path: Path) -> None:
    task_id = "task_20251128_120055_076_8e9e30d7_e5dcfa22"
    task = next(item for item in load_tasks() if item.task_id == task_id)
    assert task.known_defect
    assert task.known_defect_reasons == ("selected_tool_missing",)

    result = run_module._execute_row(
        {"task_id": task.task_id, "trial": 1, "arm": "none", "cell": "reproduce"},
        task_by_id={task.task_id: task},
        model="test-model",
        effort="low",
        run_id="run",
        store=RunStore(tmp_path / "run"),
        cost_ledger=CostLedger(tmp_path / "cost.jsonl", max_usd=20),
        capture_dir=None,
        skills_block=None,
        skills_hash="",
        skills=[],
        zep=None,
        agent_uuid=None,
    )

    assert result["status"] == "invalid"
    assert result["reward"] == 0
    assert result["error_class"] == "KnownDefect"
    assert result["known_defect_reasons"] == ["selected_tool_missing"]
    assert result["model_calls"] == 0


def test_zep_stages_require_only_the_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ZEP_BASE_URL", raising=False)
    monkeypatch.delenv("ZEP_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="Set ZEP_API_KEY"):
        run_module._require_zep_environment("compile")
    monkeypatch.setenv("ZEP_API_KEY", "test-key")
    run_module._require_zep_environment("compile")


@pytest.mark.parametrize(
    ("stage", "expected", "task_scope"),
    (
        ("executor", True, "split"),
        ("executor", True, "all"),
        ("reproduce", False, "split"),
        ("reproduce", False, "all"),
    ),
)
def test_skills_multi_read_cli_flag_is_recorded_only_for_executor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    expected: bool,
    task_scope: str,
) -> None:
    baseline_path = tmp_path / "noop_baseline.jsonl"
    baseline_path.touch()
    monkeypatch.setattr(run_module, "_resolve_noop_baseline_path", lambda _args: baseline_path)
    task = next(item for item in load_tasks() if not item.known_defect)
    tasks = (task,)
    run_dir = tmp_path / "runs"
    snapshot_dir = tmp_path / "skills"
    snapshot_dir.mkdir()
    snapshot = snapshot_dir / "snapshot.json"
    family_agents = {"eog.calendar": "source-agent"}
    if stage == "executor":
        merged_snapshot = {"sha256": "snapshot-content-sha256", "skills": []}

        def fake_merge(_sources: Any, _families: list[str], output_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "snapshot.json").write_text(json.dumps(merged_snapshot))
            return merged_snapshot, {"eog.calendar": {"agent_uuid": "source-agent"}}

        monkeypatch.setattr(run_module, "merge_compile_outputs", fake_merge)
        monkeypatch.setattr(run_module, "git_commit", lambda _repo: "harness-commit")
        run_module._run_merge(
            SimpleNamespace(
                family_source=[("eog.calendar", "source-dir", "source-commit")],
                task_family=[],
                run_id="merged-run",
            ),
            tmp_path,
        )
        assert json.loads((tmp_path / "agent.json").read_text()) == {
            "agent_uuid": "source-agent",
            "merged": True,
            "family_agents": family_agents,
        }
    else:
        snapshot.write_text(json.dumps({"sha256": "snapshot-content-sha256"}))
        (tmp_path / "agent.json").write_text(json.dumps({"agent_uuid": "test-agent"}))
    seed_root = tmp_path / "seeds"
    for server in task.gym_servers_config:
        seed = seed_root / server["seed_database_file"]
        seed.parent.mkdir(parents=True, exist_ok=True)
        seed.write_text("-- seed\n")
    monkeypatch.setenv("EOG_DATA_ROOT", str(seed_root))

    argv = [
        "eog.run",
        "--stage",
        stage,
        "--tasks",
        str(tmp_path / "tasks.json"),
        "--run-dir",
        str(run_dir),
        "--run-id",
        "multi-read",
        "--skills-multi-read",
    ]
    if task_scope == "all":
        argv.extend(("--task-scope", task_scope))
    if stage == "executor":
        argv.extend(
            (
                "--skills-snapshot",
                str(snapshot),
                "--arms",
                "none",
                "--allow-compile-failures",
                "eog.calendar",
            )
        )
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(run_module, "_resolve_run_config", lambda *args: ("gpt-6-luna", "low", 1))
    monkeypatch.setattr(run_module, "load_tasks", lambda: tasks)
    monkeypatch.setattr(run_module, "load_task_sample", lambda available, path: available)
    monkeypatch.setattr(
        run_module,
        "make_split",
        lambda available: {"tasks": {item.task_id: {"side": "score"} for item in available}},
    )
    monkeypatch.setattr(run_module, "_task_families", lambda current: ("eog.calendar",))
    monkeypatch.setattr(
        run_module,
        "_executor_compile_preflight",
        lambda *args: {"eog.calendar": {"compile_failed": 2}},
    )
    monkeypatch.setattr(run_module, "load_snapshot_details", lambda *args, **kwargs: (None, "snapshot-hash", {}))
    literal_gate_calls = []
    monkeypatch.setattr(run_module, "assert_no_score_literals", lambda *args: literal_gate_calls.append(args))
    monkeypatch.setattr(run_module, "image_digest", lambda container: "image-sha256")
    monkeypatch.setattr(run_module, "teams_townhall_patch_sha256", lambda: "")
    monkeypatch.setattr(run_module, "itsm_opened_by_patch_sha256", lambda: "")
    monkeypatch.setattr(run_module, "_zep_base_url", lambda: None)

    def fake_subprocess_run(command: list[str], **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(stdout="" if command[1] == "status" else "commit-sha\n")

    monkeypatch.setattr(runner_module.subprocess, "run", fake_subprocess_run)
    original_store = run_module.RunStore

    class StopAfterManifest(Exception):
        pass

    class ManifestStore(original_store):
        def write_manifest(self, current, *, allow_commit_change: bool = False) -> None:
            super().write_manifest(current, allow_commit_change=allow_commit_change)
            raise StopAfterManifest

    monkeypatch.setattr(run_module, "RunStore", ManifestStore)

    with pytest.raises(StopAfterManifest):
        run_module._run_main(run_module._RunSignalState())

    manifest_path = run_dir / "multi-read" / f"manifest.{stage}.json"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["skills_multi_read"] is expected
    assert manifest["task_scope"] == task_scope
    assert bool(literal_gate_calls) is (stage == "executor" and task_scope == "split")
    if stage == "executor":
        assert manifest["compile_failures_allowed"] == ["eog.calendar"]
        assert manifest["compile_failures"] == {"eog.calendar": 2}
        assert manifest["family_agents"] == family_agents
    else:
        assert "compile_failures_allowed" not in manifest
        assert "compile_failures" not in manifest


def test_task_scope_split_selection_matches_existing_sample() -> None:
    tasks = load_tasks()
    split = run_module.make_split(tasks)

    teacher = run_module._select_stage_tasks(tasks, split, "teacher")
    score_tasks = tuple(
        task for task in tasks if split["tasks"][task.task_id]["side"] == "score" and not task.known_defect
    )
    executor = run_module._select_stage_tasks(tasks, split, "executor")

    assert len(teacher) == 184
    assert all(split["tasks"][task.task_id]["side"] == "teach" and not task.known_defect for task in teacher)
    assert executor == run_module.sample_score_tasks(score_tasks, split)
    assert len(executor) == 152


def test_task_scope_all_selects_every_non_defect_task_without_sampling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tasks = load_tasks()
    split = run_module.make_split(tasks)
    monkeypatch.setattr(
        run_module,
        "sample_score_tasks",
        lambda *_args, **_kwargs: pytest.fail("all scope must not sample score tasks"),
    )

    teacher = run_module._select_stage_tasks(tasks, split, "teacher", task_scope="all")
    executor = run_module._select_stage_tasks(tasks, split, "executor", task_scope="all")
    sides = [split["tasks"][task.task_id]["side"] for task in teacher]

    assert len(teacher) == len(executor) == 625
    assert teacher == executor
    assert sides.count("teach") == 184
    assert sides.count("score") == 441
    assert all(not task.known_defect for task in teacher)


def test_executor_literal_gate_is_skipped_only_for_all_scope(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = []
    monkeypatch.setattr(run_module, "assert_no_score_literals", lambda *args: calls.append(args))

    run_module._check_executor_score_literals([], {}, (), task_scope="all")
    assert calls == []
    assert capsys.readouterr().out.strip() == "Skipping the score-literal gate because of --task-scope all."

    run_module._check_executor_score_literals([], {}, (), task_scope="split")
    assert len(calls) == 1


def test_resume_manifest_defaults_missing_skills_multi_read_to_false(tmp_path: Path) -> None:
    manifest = RunManifest("commit", {}, {}, "split", "snapshot", "prices")
    store = RunStore(tmp_path)
    store.write_manifest(manifest)
    stored = json.loads(store.manifest_path.read_text())
    stored.pop("skills_multi_read")
    stored.pop("task_scope")
    store.manifest_path.write_text(json.dumps(stored))

    RunStore(tmp_path, resume=True).write_manifest(manifest)


def test_task_scope_manifest_is_recorded_and_checked_on_resume(tmp_path: Path) -> None:
    all_scope = RunManifest("commit", {}, {}, "split", "snapshot", "prices", task_scope="all")
    store = RunStore(tmp_path / "all")
    store.write_manifest(all_scope)
    assert json.loads(store.manifest_path.read_text())["task_scope"] == "all"
    RunStore(tmp_path / "all", resume=True).write_manifest(all_scope)

    split_scope = RunManifest("commit", {}, {}, "split", "snapshot", "prices", task_scope="split")
    with pytest.raises(ValueError, match="The frozen run manifest changed"):
        RunStore(tmp_path / "all", resume=True).write_manifest(split_scope)


def test_compile_failure_manifest_fields_are_optional_and_checked_on_resume(tmp_path: Path) -> None:
    without_flag = RunManifest("commit", {}, {}, "split", "snapshot", "prices")
    legacy_store = RunStore(tmp_path / "legacy")
    legacy_store.write_manifest(without_flag)
    legacy_manifest = json.loads(legacy_store.manifest_path.read_text())
    assert "compile_failures_allowed" not in legacy_manifest
    assert "compile_failures" not in legacy_manifest
    RunStore(tmp_path / "legacy", resume=True).write_manifest(without_flag)

    allowed = RunManifest(
        "commit",
        {},
        {},
        "split",
        "snapshot",
        "prices",
        compile_failures_allowed=["eog.email", "eog.hr"],
        compile_failures={"eog.email": 4, "eog.hr": 1},
    )
    allowed_store = RunStore(tmp_path / "allowed")
    allowed_store.write_manifest(allowed)
    saved_manifest = json.loads(allowed_store.manifest_path.read_text())
    assert saved_manifest["compile_failures_allowed"] == ["eog.email", "eog.hr"]
    assert saved_manifest["compile_failures"] == {"eog.email": 4, "eog.hr": 1}
    RunStore(tmp_path / "allowed", resume=True).write_manifest(allowed)

    different_allowlist = RunManifest(
        "commit",
        {},
        {},
        "split",
        "snapshot",
        "prices",
        compile_failures_allowed=["eog.email"],
        compile_failures={"eog.email": 4, "eog.hr": 1},
    )
    with pytest.raises(ValueError, match="The frozen run manifest changed"):
        RunStore(tmp_path / "allowed", resume=True).write_manifest(different_allowlist)


def test_zep_client_uses_environment_and_normalizes_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}
    monkeypatch.setenv("ZEP_API_KEY", "local-test-key")
    monkeypatch.setenv("ZEP_BASE_URL", "http://api.example.com:8000/api/v4")
    monkeypatch.setattr(run_module, "Zep", lambda **kwargs: captured.update(kwargs) or "client")

    assert run_module.zep_client() == "client"
    assert captured == {"api_key": "local-test-key", "base_url": "http://api.example.com:8000/api/v4"}
    http_url = run_module._zep_base_url()
    assert http_url == "http://api.example.com:8000/api/v4"
    monkeypatch.setenv("ZEP_BASE_URL", "https://api.example.com:8000/api/v4")
    https_url = run_module._zep_base_url()
    assert https_url == "https://api.example.com:8000/api/v4"
    assert https_url != http_url
    monkeypatch.setenv("ZEP_BASE_URL", "http://user:pass@example.test:1/x/?q=1#fragment")
    assert run_module._zep_base_url() == "http://example.test:1/x"


def test_unknown_model_tool_returns_a_tool_error() -> None:
    result = CompositeMCPClient({}).call_tool("not_a_tool", {})
    assert result["isError"] is True
    assert result["content"][0]["text"] == "not_a_tool is not a valid tool"


def test_active_time_limit_row_can_score_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = next(item for item in load_tasks() if item.domain == "calendar")
    seed = tmp_path / "seed.sql"
    seed.write_text("SELECT 1;")
    gym = FakeGym()
    monkeypatch.setattr(
        run_module,
        "_gym_clients",
        lambda current_task, ids: {current_task.gym_servers_config[0]["mcp_server_name"]: gym},
    )
    monkeypatch.setattr(run_module, "_seed_path", lambda name: seed)
    monkeypatch.setattr(run_module, "MCPClient", lambda *args, **kwargs: FakeMCP())
    monkeypatch.setattr(run_module, "CompositeMCPClient", lambda clients: FakeCompositeMCP())
    monkeypatch.setattr(run_module, "_route", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        run_module,
        "run_agent",
        lambda *args, **kwargs: SimpleNamespace(
            finish_reason="active_time_limit",
            turns=1,
            usage={},
            events=(),
            active_seconds=2700.0,
        ),
    )

    result = run_module._execute_row(
        {"task_id": task.task_id, "trial": 1, "arm": "none", "cell": "executor"},
        task_by_id={task.task_id: task},
        model="test-model",
        effort="low",
        run_id="run",
        store=RunStore(tmp_path / "run"),
        cost_ledger=CostLedger(tmp_path / "cost.jsonl", max_usd=20),
        capture_dir=None,
        skills_block=None,
        skills_hash="",
        skills=[],
        zep=None,
        agent_uuid=None,
    )

    assert result["status"] == "valid"
    assert result["reward"] == 0


def test_resume_key_includes_model_effort_arm_and_stage(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "run")
    completed = {
        "task_id": "task",
        "trial": 1,
        "model": "model-a",
        "effort": "low",
        "arm": "none",
        "stage": "executor",
        "cell": "executor",
        "status": "valid",
    }
    store.checkpoint(completed)
    resumed = RunStore(tmp_path / "run", resume=True)
    planned = [{**completed, "model": "model-b", "status": None}]

    assert resumed.rows_to_run(planned) == planned


def test_skip_arms_requires_resume() -> None:
    with pytest.raises(SystemExit, match="--skip-arms requires --resume"):
        run_module._resolve_skip_arms("executor", False, ("none", "skills_decided"), "skills_decided", "reason")


def test_skip_arms_must_be_in_resolved_arms() -> None:
    with pytest.raises(SystemExit, match="included in --arms"):
        run_module._resolve_skip_arms("executor", True, ("none", "skills"), "skills_decided", "reason")


def test_skip_arms_requires_a_reason() -> None:
    with pytest.raises(SystemExit, match="--skip-reason must be non-empty"):
        run_module._resolve_skip_arms("executor", True, ("none", "skills_decided"), "skills_decided", "  ")


def test_skip_arms_are_only_valid_for_executor() -> None:
    with pytest.raises(SystemExit, match="only valid with --stage executor"):
        run_module._resolve_skip_arms("teacher", True, ("none", "skills_decided"), "skills_decided", "reason")


def test_resume_skip_keeps_manifest_and_completed_rows(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_root = tmp_path / "run"
    cells = tuple(
        {"model": "model", "effort": "low", "arm": arm, "stage": "executor", "cell": "executor"}
        for arm in ("none", "skills", "skills_decided")
    )
    manifest = RunManifest("commit", {}, {}, "split", "snapshot", "prices", cells=cells)
    store = RunStore(run_root)
    store.write_manifest(manifest)

    def row(task_id: str, arm: str, status: str) -> dict[str, Any]:
        return {
            "task_id": task_id,
            "trial": 1,
            "model": "model",
            "effort": "low",
            "arm": arm,
            "stage": "executor",
            "cell": "executor",
            "status": status,
        }

    completed = row("completed", "skills_decided", "valid")
    store.checkpoint(completed)
    store.checkpoint(row("retry", "skills_decided", "infra_failed"))
    store.checkpoint(row("retry", "skills_decided", "partial"))

    resumed = RunStore(run_root, resume=True)
    resumed.write_manifest(manifest)
    planned = [
        {**row("completed", "skills_decided", ""), "status": None},
        {**row("retry", "skills_decided", ""), "status": None},
        {**row("remaining", "none", ""), "status": None},
    ]
    pending = resumed.rows_to_run(planned)
    filtered = run_module._drop_pending_arms(
        run_root,
        resumed,
        pending,
        ("skills_decided",),
        "The lead decided to drop this arm.",
    )

    assert [item["task_id"] for item in filtered] == ["remaining"]
    saved_rows = [json.loads(line) for line in resumed.rows.path.read_text().splitlines() if line.strip()]
    assert completed in saved_rows
    record = json.loads((run_root / "dropped_arms.jsonl").read_text())
    assert record["arm"] == "skills_decided"
    assert record["reason"] == "The lead decided to drop this arm."
    assert record["rows_dropped"] == 1
    assert record["rows_kept_by_status"] == {"partial": 1, "valid": 1}
    assert record["dropped_at"].endswith("+00:00")
    assert capsys.readouterr().out == "skip-arms: skills_decided dropped 1 pending rows\n"


def test_infra_failure_closes_trace_as_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = next(item for item in load_tasks() if item.domain == "calendar")
    seed = tmp_path / "seed.sql"
    seed.write_text("SELECT 1;")
    trace = SimpleNamespace(closed=[], abandoned=[])
    trace_api = SimpleNamespace(
        create=lambda *args, **kwargs: SimpleNamespace(uuid="trace-1"),
        append_event=lambda *args, **kwargs: None,
        close=lambda *args, **kwargs: trace.closed.append(kwargs),
        abandon=lambda *args, **kwargs: trace.abandoned.append(kwargs),
    )
    zep = SimpleNamespace(agent=SimpleNamespace(trace=trace_api))
    gym = FakeGym()
    monkeypatch.setattr(
        run_module,
        "_gym_clients",
        lambda current_task, ids: {current_task.gym_servers_config[0]["mcp_server_name"]: gym},
    )
    monkeypatch.setattr(run_module, "_seed_path", lambda name: seed)
    monkeypatch.setattr(run_module, "MCPClient", lambda *args, **kwargs: FakeMCP())
    monkeypatch.setattr(run_module, "CompositeMCPClient", lambda clients: FakeCompositeMCP())
    monkeypatch.setattr(run_module, "_route", lambda *args, **kwargs: object())

    def fail(*args: Any, **kwargs: Any) -> None:
        raise InfrastructureError("MCP tool call failed")

    monkeypatch.setattr(run_module, "run_agent", fail)
    result = run_module._execute_row(
        {"task_id": task.task_id, "trial": 1, "arm": "none", "cell": "teacher", "stage": "teacher"},
        task_by_id={task.task_id: task},
        model="test-model",
        effort="low",
        run_id="run",
        store=RunStore(tmp_path / "run"),
        cost_ledger=CostLedger(tmp_path / "cost.jsonl", max_usd=20),
        capture_dir=None,
        skills_block=None,
        skills_hash="",
        skills=[],
        zep=zep,
        agent_uuid="agent-1",
        trace_path=tmp_path / "traces.jsonl",
    )
    assert result["status"] == "infra_failed"
    assert trace.closed == []
    assert len(trace.abandoned) == 1
    trace_row = json.loads((tmp_path / "traces.jsonl").read_text())
    assert trace_row["infra_failed"] is True


def test_policy_flag_threshold_stops_after_sixth_row() -> None:
    calls = []
    rows = [{"task_id": str(index), "route": "route-a"} for index in range(8)]

    def execute(row: dict[str, str]) -> dict[str, str]:
        calls.append(row["task_id"])
        return {**row, "status": "policy_flagged"}

    results = run_concurrent(rows, execute, ramp=(1,), initial_policy_flags=0)
    assert calls == ["0", "1", "2", "3", "4", "5"]
    assert sum(row.get("status") == "policy_flagged" for row in results) == 6
    assert run_concurrent(rows, execute, ramp=(1,), initial_policy_flags=6) == []


def test_hybrid_composite_start_raises_missing_tools() -> None:
    class StubClient:
        def __init__(self, names: list[str]) -> None:
            self.names = names
            self.calls = []

        def initialize(self) -> None:
            pass

        def tools_for(self, task: SimpleNamespace) -> list[dict[str, str]]:
            return [
                {"name": name, "inputSchema": {"type": "object"}} for name in task.selected_tools if name in self.names
            ]

        def call_tool(self, name: str, arguments: dict) -> dict:
            self.calls.append((name, arguments))
            return {"content": [], "isError": False}

    task = SimpleNamespace(selected_tools=["calendar_event", "search_cases"])
    calendar = StubClient(["calendar_event"])
    csm = StubClient(["search_cases"])
    composite = CompositeMCPClient(
        {
            "calendar": calendar,
            "csm": csm,
        }
    )
    expected = ["calendar__calendar_event", "csm__search_cases"]
    assert [tool["name"] for tool in composite.start(task)] == expected
    assert composite.selected_tools == tuple(expected)
    composite.call_tool("csm__search_cases", {})
    assert csm.calls == [("search_cases", {})]

    missing = CompositeMCPClient({"calendar": StubClient(["calendar_event"])})
    with pytest.raises(InfrastructureError, match="search_cases"):
        missing.start(task)


def test_composite_passes_object_arguments_unchanged_and_rejects_non_objects() -> None:
    class StubClient:
        def __init__(self) -> None:
            self.calls = []

        def initialize(self) -> None:
            pass

        def tools_for(self, task: SimpleNamespace) -> list[dict]:
            return [
                {
                    "name": "set_count",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"count": {"type": "integer"}},
                        "required": ["count"],
                    },
                }
            ]

        def call_tool(self, name: str, arguments: dict) -> dict:
            self.calls.append((name, arguments))
            return {"content": [], "isError": False}

    client = StubClient()
    composite = CompositeMCPClient({"calendar": client})
    task = SimpleNamespace(selected_tools=["set_count"])
    composite.start(task)

    arguments = {"count": "not-an-integer"}
    composite.call_tool("calendar__set_count", arguments)
    assert client.calls == [("set_count", arguments)]
    assert client.calls[0][1] is arguments

    invalid = {
        "content": [{"type": "text", "text": "Tool arguments are not valid"}],
        "isError": True,
    }
    assert composite.call_tool("calendar__set_count", "{") == invalid
    assert composite.call_tool("calendar__set_count", ["not", "an", "object"]) == invalid
    assert client.calls == [("set_count", arguments)]


def test_composite_preserves_union_schemas() -> None:
    class StubClient:
        def __init__(self) -> None:
            self.calls = []

        def initialize(self) -> None:
            pass

        def tools_for(self, task: SimpleNamespace) -> list[dict]:
            return [
                {
                    "name": "create_draft",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "message": {
                                "type": "object",
                                "properties": {
                                    "raw": {"type": "string"},
                                    "payload": {"type": "object"},
                                },
                                "oneOf": [{"required": ["raw"]}, {"required": ["payload"]}],
                            }
                        },
                        "required": ["message"],
                    },
                }
            ]

        def call_tool(self, name: str, arguments: dict) -> dict:
            self.calls.append((name, arguments))
            return {"content": [], "isError": False}

    client = StubClient()
    composite = CompositeMCPClient({"email": client})
    tools = composite.start(SimpleNamespace(selected_tools=["create_draft"]))
    assert tools[0]["inputSchema"]["properties"]["message"]["oneOf"] == [
        {"required": ["raw"]},
        {"required": ["payload"]},
    ]

    result = composite.call_tool("email__create_draft", {"message": {"raw": "encoded-message"}})
    assert result == {"content": [], "isError": False}
    assert client.calls == [("create_draft", {"message": {"raw": "encoded-message"}})]


def test_unknown_tool_name_raises_key_error() -> None:
    class StubClient:
        def __init__(self) -> None:
            self.calls = []

        def call_tool(self, name: str, arguments: dict) -> dict:
            self.calls.append(name)
            return {}

    client = StubClient()
    composite = CompositeMCPClient({"calendar": client})
    result = composite.call_tool("unselected_tool", {})
    assert result == {
        "content": [{"type": "text", "text": "unselected_tool is not a valid tool"}],
        "isError": True,
    }
    assert client.calls == []


def test_run_concurrent_duplicates_results() -> None:
    rows = [{"task_id": str(index), "route": "test"} for index in range(4)]
    results = run_concurrent(rows, lambda row: {**row, "status": "valid"}, ramp=(4,))
    episode_rows = [row for row in results if row.get("task_id")]
    assert [row["task_id"] for row in episode_rows] == ["0", "1", "2", "3"]
    assert len([row for row in results if row.get("status") == "ramp"]) == 1


def test_executor_skill_selection_preserves_live_search_order() -> None:
    task = next(item for item in load_tasks() if item.domain == "hybrid")
    families = run_module._task_families(task)
    snapshot = {
        f"skill-{index}": {
            "id": f"skill-{index}",
            "sha256": f"sha-{index}",
            "markdown": f"Procedure {index}",
            "task_family": family,
        }
        for index, family in enumerate(families)
    }
    selection = {(task.task_id, family): [f"skill-{index}"] for index, family in enumerate(families)}
    selected = run_module._select_snapshot_skills(task, snapshot, selection)
    assert [item["id"] for item in selected] == [f"skill-{i}" for i in range(len(families))]


def test_executor_arms_select_the_same_skills_and_only_skills_arm_gets_block() -> None:
    task = next(item for item in load_tasks() if item.domain == "hybrid")
    families = run_module._task_families(task)
    snapshot = {
        f"skill-{index}": {
            "id": f"skill-{index}",
            "sha256": f"sha-{index}",
            "markdown": f"Procedure {index}",
            "task_family": family,
        }
        for index, family in enumerate(families)
    }
    selection = {(task.task_id, family): [f"skill-{index}"] for index, family in enumerate(families)}
    for skill in snapshot.values():
        skill["markdown"] = (
            f'---\nname: {skill["id"]}\ndescription: "Procedure {skill["id"]}"\n---\n\n{skill["markdown"]}'
        )
    no_skills = run_module._executor_skill_inputs({"arm": "none"}, task, snapshot, selection)
    skills = run_module._executor_skill_inputs({"arm": "skills"}, task, snapshot, selection)
    decided = run_module._executor_skill_inputs(
        {"arm": "skills_decided"}, task, snapshot, selection, decided_selection=selection
    )
    paired_decided = run_module._executor_skill_inputs(
        {"arm": "none"}, task, snapshot, selection, paired_arm="skills_decided", decided_selection=selection
    )

    assert no_skills[0] is None
    assert skills[0] == skills[3].block()
    assert skills[0].startswith(SKILLS_HEADER)
    assert all(f"- {skill['id']}: Procedure {skill['id']}" in skills[0] for skill in snapshot.values())
    assert no_skills[1:] == skills[1:]
    assert decided[0] == selected_skills_block([item["markdown"] for item in snapshot.values()])
    assert decided[3] is None
    assert paired_decided[0] is None and paired_decided[1:] == decided[1:]
    selected = no_skills[2]
    assert run_module._skill_metadata("none", selected, no_skills[1]) == {
        "skills": selected,
        "skills_count": len(selected),
        "skill_block_sha256": "",
    }
    assert run_module._skill_metadata("skills", skills[2], skills[1]) == {
        "skills": selected,
        "skills_count": len(selected),
        "skill_block_sha256": skills[1],
    }
    assert run_module._skill_metadata("skills_decided", decided[2], decided[1]) == {
        "skills": selected,
        "skills_count": len(selected),
        "skill_block_sha256": decided[1],
    }


def test_skill_selection_error_is_checkpointed_as_stopped(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "run")
    row = {"task_id": "task-1", "trial": 1, "arm": "skills", "stage": "executor"}

    stopped = run_module._record_skill_selection_failure(row, ValueError("bad snapshot"), store)

    assert stopped["status"] == "stopped"
    assert stopped["stop_run"] is True
    assert "bad snapshot" in stopped["stop_reason"]
    recorded = json.loads(store.rows.path.read_text().splitlines()[0])
    assert recorded == stopped


def _replay_row(task_id: str, side: str = "teach", task_family: str = "eog.calendar") -> Any:
    from eog.recompile import ReplayRow

    return ReplayRow(
        source_trace_uuid=f"trace-{task_id}",
        objective="o",
        task_family=task_family,
        metadata={"task_id": task_id, "side": side},
        events=(),
        run_row={"task_id": task_id, "side": side, "passed": True, "verified": True, "infra_failed": False},
        closed_at="t",
        passed=True,
        verified=True,
        infra_failed=False,
    )


def test_recompile_rejects_replay_rows_outside_the_teach_split() -> None:
    tasks = (
        SimpleNamespace(task_id="teach-1", known_defect=False, domain="calendar"),
        SimpleNamespace(task_id="score-1", known_defect=False, domain="calendar"),
        SimpleNamespace(task_id="defect-1", known_defect=True, domain="calendar"),
    )
    split = {"tasks": {"teach-1": {"side": "teach"}, "score-1": {"side": "score"}, "defect-1": {"side": "teach"}}}
    run_module._require_teach_replay_rows([_replay_row("teach-1")], split, tasks)
    for row in (
        _replay_row("score-1", side="score"),
        _replay_row("score-1"),
        _replay_row("defect-1"),
        _replay_row("unknown"),
        _replay_row("teach-1", task_family="eog.crm"),
    ):
        with pytest.raises(SystemExit, match="not teach-side tasks"):
            run_module._require_teach_replay_rows([row], split, tasks)


def test_recompile_all_scope_accepts_matching_sides_and_rejects_invalid_rows() -> None:
    tasks = (
        SimpleNamespace(task_id="teach-1", known_defect=False, domain="calendar"),
        SimpleNamespace(task_id="score-1", known_defect=False, domain="calendar"),
        SimpleNamespace(task_id="defect-1", known_defect=True, domain="calendar"),
    )
    split = {"tasks": {"teach-1": {"side": "teach"}, "score-1": {"side": "score"}, "defect-1": {"side": "teach"}}}

    run_module._require_teach_replay_rows(
        [_replay_row("teach-1"), _replay_row("score-1", side="score")],
        split,
        tasks,
        task_scope="all",
    )
    for row in (
        _replay_row("defect-1"),
        _replay_row("unknown"),
        _replay_row("score-1", side="teach"),
        _replay_row("teach-1", task_family="eog.crm"),
    ):
        with pytest.raises(SystemExit, match="whose task is missing or is a known defect"):
            run_module._require_teach_replay_rows([row], split, tasks, task_scope="all")
