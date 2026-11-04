from __future__ import annotations

import ast
import hashlib
import json
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Self

import pytest

import appworld_run
from anthropic_client import _response
from call_ledger import CallLedger
from redaction import Redactor
from skill_snapshot import SnapshotExportError, compile_snapshot, wait_for_compilation


def test_f1_harness_import_allowlist() -> None:
    assert appworld_run.check_agent_code("import json") is None
    assert appworld_run.check_agent_code("from collections.abc import Sequence") is None
    for code in (
        "import unittest",
        "from pathlib import Path",
        "import decimal",
        "import statistics",
    ):
        message = appworld_run.check_agent_code(code)
        assert message is not None


def test_n2_deepseek_effort_is_checked_only_for_curator_arms() -> None:
    appworld_run._validate_model_arguments(
        "deepseek-v4.1-flash-1017", "low", "none", None, "medium"
    )
    appworld_run._validate_model_arguments(
        "deepseek-v4.1-flash-1017", "low", "skills", None, "medium"
    )
    with pytest.raises(ValueError, match="Invalid reasoning effort"):
        appworld_run._validate_model_arguments(
            "deepseek-v4.1-flash-1017", "low", "r-skill", None, "medium"
        )


def test_n4_teacher_stop_skips_compile_and_export() -> None:
    assert not appworld_run._should_export_teacher("teacher", "provider_error")
    assert appworld_run._should_export_teacher("teacher", None)
    assert not appworld_run._should_export_teacher("heldout", None)


def test_n6_dirty_repository_refuses_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        appworld_run.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=" M evaluations/file.py\n"),
    )
    with pytest.raises(RuntimeError, match="repository is dirty"):
        appworld_run._require_clean_repository()


def test_n8_context_limit_is_scored_and_does_not_stop_the_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Server:
        def __init__(self) -> None:
            self.calls: list[str] = []
            self.blocked_codes = 0
            self.guard_marker_hits = 0
            self.guard_marker_code: list[str] = []

        def begin_episode(self) -> None:
            pass

        def verify_guard_state(self, _task_id: str) -> float:
            return 0.0

        def call(self, method: str, **_kwargs: Any) -> Any:
            self.calls.append(method)
            if method == "initialize":
                return {
                    "instruction": "Do the task",
                    "supervisor": {
                        "first_name": "Alex",
                        "last_name": "Example",
                        "email": "alex@example.test",
                        "phone_number": "555-0100",
                    },
                }
            if method == "evaluate":
                return {
                    "success": False,
                    "passes": [],
                    "failures": [{"requirement": "task", "trace": "AssertionError"}],
                    "num_tests": 1,
                }
            return False

    class Recorder:
        trajectory_uuid = "trace-1"

        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def emit(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def record_message(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def set_result(self, *_args: Any, **_kwargs: Any) -> None:
            pass

    def context_limited(*_args: Any, **_kwargs: Any) -> Any:
        raise appworld_run.AgentContextLimitError("context limit")

    server = Server()
    monkeypatch.setattr(appworld_run, "TrajectoryRecorder", Recorder)
    monkeypatch.setattr(appworld_run, "_completion", context_limited)
    monkeypatch.setattr(appworld_run.TRACER, "start_trace", lambda *_a, **_k: "trace")
    monkeypatch.setattr(appworld_run.TRACER, "end_trace", lambda *_a, **_k: None)

    result, _ = appworld_run.run_episode(
        server=server,
        client=SimpleNamespace(),
        openai_client=SimpleNamespace(),
        agent_uuid="agent",
        stage="teacher",
        run_id="run",
        task_id="task_1",
        task_index=0,
        trial=1,
        model="gpt-6-luna",
        reasoning_effort="low",
        max_steps=2,
        arm="none",
        memory_agent_uuid=None,
        corpus=None,
        curator_model="gpt-6-luna",
        curator_reasoning_effort="medium",
    )

    assert result.end_reason == "context_limit"
    assert result.error is None
    assert result.reward == 0.0
    assert "evaluate" in server.calls
    summary = appworld_run.summarize([result.__dict__])
    assert summary["end_reason_counts_by_arm"]["none"]["context_limit"] == 1


def test_n9_retry_scheduling_uses_task_id_and_trial() -> None:
    result = SimpleNamespace(task_id="task-1", task_index=3, trajectory_uuid="trace")
    completed: set[tuple[str, int]] = set()
    scheduled: set[tuple[str, int]] = set()
    queue: list[tuple[int, str, int, str | None]] = []

    assert appworld_run._schedule_teacher_retry(result, 2, completed, scheduled, queue)
    assert not appworld_run._schedule_teacher_retry(result, 2, completed, scheduled, queue)
    assert scheduled == {("task-1", 2)}
    assert queue == [(3, "task-1", 2, "trace")]


def test_f2_malformed_arguments_are_a_tool_result_and_count() -> None:
    result = appworld_run.AppWorldResult("appworld", "t1", 0, 1)
    arguments, error = appworld_run._decode_function_arguments("{", result)
    assert arguments == {}
    assert error is not None and error.startswith("Invalid JSON arguments: ")
    assert result.malformed_tool_calls == 1
    assert appworld_run._decode_function_arguments('{"code":"1"}', result) == (
        {"code": "1"},
        None,
    )


def test_f2_errors_do_not_affect_scored_metrics_and_stop_stage() -> None:
    rows = [
        {"task_id": "t1", "trial": 1, "reward": 1, "error": None},
        {"task_id": "t2", "trial": 1, "reward": 1, "error": "RuntimeError"},
    ]
    summary = appworld_run.summarize(rows)
    assert summary["success_rate"] == 1.0
    assert summary["error_count"] == {"RuntimeError": 1}
    result = appworld_run.AppWorldResult("appworld", "t2", 1, 1, error="RuntimeError")
    assert appworld_run._stage_stop_reason(result) == "RuntimeError"


def test_f3_keeps_appworld_score_when_trace_close_fails() -> None:
    result = appworld_run.AppWorldResult(
        "appworld", "t1", 0, 1, reward=1.0, succeeded=True, pass_count=2, num_tests=2
    )
    appworld_run._record_episode_error(
        result,
        SimpleNamespace(health_probe=lambda *_: "passed"),
        "t1",
        "r1",
        RuntimeError("trace close failed"),
        preserve_evaluation=True,
    )
    assert result.reward == 1.0 and result.pass_count == 2 and result.num_tests == 2


def test_f4_counts_scored_end_reasons_by_arm() -> None:
    rows = [
        {"task_id": str(i), "trial": 1, "arm": "skills", "reward": 0, "end_reason": reason}
        for i, reason in enumerate(("no_tool_call", "max_steps", "refusal"))
    ]
    assert appworld_run.summarize(rows)["end_reason_counts_by_arm"] == {
        "skills": {"no_tool_call": 1, "max_steps": 1, "refusal": 1}
    }


def test_f5_requires_complete_score_and_keeps_failure_traces() -> None:
    result = appworld_run.AppWorldResult("appworld", "t1", 0, 1)
    with pytest.raises(appworld_run.EpisodeScoreError) as missing:
        appworld_run._apply_evaluation(result, {"passes": [1], "failures": [], "num_tests": 0})
    assert missing.value.error_type == "missing_score"
    trace = '```python\nwith test():\n    test.case(x, "==", y)\n```\n----------\n'
    appworld_run._apply_evaluation(
        result,
        {"passes": [], "failures": [{"trace": trace}], "num_tests": 1, "success": False},
    )
    assert result.failures == [{"trace": trace}]
    assert result.non_assertion_failures == 1
    assert result.succeeded is False
    assert result.reward == 0.0
    assertion = appworld_run.AppWorldResult("appworld", "t1", 0, 1)
    appworld_run._apply_evaluation(
        assertion,
        {
            "passes": [],
            "failures": [{"trace": "x\n----------\nAssertionError: 1 != 2"}],
            "num_tests": 1,
            "success": False,
        },
    )
    assert assertion.non_assertion_failures == 0


def test_f6_attempt_summary_uses_all_raw_rows() -> None:
    rows = [
        {"task_id": "t1", "trial": 1, "error": "RuntimeError", "error_type": "provider"},
        {"task_id": "t1", "trial": 1, "reward": 1, "error": None},
    ]
    summary = appworld_run.summarize(rows, raw_rows=rows)
    assert summary["attempts_total"] == 2
    assert summary["attempts_failed"] == {"provider": 1}
    assert len(summary["attempt_history"]["t1:1"]) == 2


def test_skill_retrieval_metrics_use_searches_and_found_reads() -> None:
    rows = [
        {
            "task_id": "t1",
            "trial": 1,
            "reward": 1,
            "skill_search_performed": True,
            "read_skill_calls": [{"found": True}],
        },
        {
            "task_id": "t2",
            "trial": 1,
            "reward": 0,
            "skill_search_performed": True,
            "read_skill_calls": [{"found": False}],
        },
        {
            "task_id": "t3",
            "trial": 1,
            "reward": 0,
            "skill_search_performed": False,
            "read_skill_calls": [],
        },
    ]

    summary = appworld_run.summarize(rows)

    assert summary["skill_retrieval_rate"] == pytest.approx(2 / 3)
    assert summary["skill_load_rate"] == pytest.approx(1 / 2)


def test_f7_transport_ledger_counts_successes_and_model_metadata() -> None:
    ledger = CallLedger()
    successful = ledger.record_attempt(
        {"service": "model", "operation": "POST /v1/responses", "failed": False}
    )
    ledger.update_attempt(successful, model="gpt-6-luna", effort="high")
    ledger.record_attempt({"service": "model", "operation": "POST /v1/responses", "failed": True})
    result = appworld_run.AppWorldResult("appworld", "t1", 0, 1)
    appworld_run._record_ledger(result, ledger)
    assert len(result.call_attempts) == 2
    assert result.call_attempts[0]["model"] == "gpt-6-luna"
    summary = appworld_run.summarize(
        [{"task_id": "t1", "trial": 1, "reward": 0, "call_attempts": result.call_attempts}]
    )
    assert summary["transport_attempts"]["model:POST /v1/responses"] == {
        "attempts": 2,
        "failed": 1,
    }


def test_f8_compile_timeout_removes_stale_snapshot(tmp_path: Path) -> None:
    class Learning:
        def list_runs(self, *_args, **_kwargs):
            return []

        def get(self, *_args, **_kwargs):
            return {"compilation_pending": True}

    client = SimpleNamespace(
        agent=SimpleNamespace(
            learning=Learning(),
            skill=SimpleNamespace(list=lambda *_args, **_kwargs: []),
        )
    )
    wait = wait_for_compilation(
        client,
        "agent",
        {"tool.appworld": 1},
        ceiling_seconds=0,
        quiet_seconds=0,
        clock=lambda: 0.0,
        sleep=lambda _seconds: None,
    )
    assert wait["timed_out"] is True
    output = tmp_path / "snapshot"
    output.mkdir()
    (output / "snapshot.json").write_text("stale")
    with pytest.raises(SnapshotExportError):
        compile_snapshot(
            client,
            "agent",
            ["tool.appworld"],
            output,
            wait=wait,
        )
    assert not (output / "snapshot.json").exists()
    report = json.loads(
        (output / "tool_appworld" / "compile_report.json").read_text(encoding="utf-8")
    )
    assert any(item["error"] == "compile_wait_timeout" for item in report["failures"])


def test_f9_failed_compile_runs_do_not_count_as_coverage() -> None:
    class Learning:
        def list_runs(self, *_args, **_kwargs):
            return [{"decision": "failed", "trace_count": 5}]

        def get(self, *_args, **_kwargs):
            return {"compilation_pending": False}

    now = [0.0]
    result = wait_for_compilation(
        SimpleNamespace(agent=SimpleNamespace(learning=Learning())),
        "agent",
        {"tool.appworld": 1},
        ceiling_seconds=0,
        quiet_seconds=0,
        clock=lambda: now[0],
        sleep=lambda _seconds: None,
    )
    assert result["families"]["tool.appworld"]["covered"] == 0
    assert "trace_count" in result["coverage_method"]


def test_f9_rejected_compile_runs_still_count_as_coverage() -> None:
    class Learning:
        def list_runs(self, *_args, **_kwargs):
            return [{"decision": "rejected", "trace_count": 3}]

        def get(self, *_args, **_kwargs):
            return {"compilation_pending": False}

    now = [0.0]
    client = SimpleNamespace(agent=SimpleNamespace(learning=Learning()))
    result = wait_for_compilation(
        client,
        "agent",
        {"tool.appworld": 3},
        quiet_seconds=0,
        clock=lambda: now[0],
        sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
    )
    assert result["families"]["tool.appworld"]["covered"] == 3
    assert result["families"]["tool.appworld"]["done"] is True


def test_f10_funnel_counts_uncited_and_failed_neutral_lineage(tmp_path: Path) -> None:
    from skill_snapshot import _funnel_for_family, filter_snapshot_skills

    skills = [
        {
            "skill_id": "supporting",
            "task_family": "tool.appworld",
            "full_markdown": "supporting",
            "lineage": {"evidence": [{"trace_uuid": "trace-1", "polarity": "supporting"}]},
        },
        {
            "skill_id": "neutral",
            "task_family": "tool.appworld",
            "full_markdown": "<!-- zep-evidence: summary-passed -->",
            "lineage": {
                "evidence": [
                    {"summary_version_uuid": "summary-passed", "trace_uuid": "trace-passed"},
                    {"trace_uuid": "trace-1", "polarity": "neutral"},
                ]
            },
        },
        {"skill_id": "uncited", "task_family": "tool.appworld", "full_markdown": "uncited"},
    ]
    kept, excluded, _ = filter_snapshot_skills(
        skills,
        [
            {"trace_uuid": "trace-1", "passed": False},
            {"trace_uuid": "trace-passed", "passed": True},
        ],
    )
    assert {skill["skill_id"] for skill in kept} == {"neutral", "uncited"}
    assert excluded[0]["reason"] == "supporting_failed_lineage"
    funnel = _funnel_for_family("tool.appworld", {}, kept, set())
    assert funnel["skills_without_citation"] == 1
    assert funnel["failed_neutral_lineage_skills"] == 1


def test_n7_funnel_counts_failed_lineage_by_polarity() -> None:
    from skill_snapshot import _funnel_for_family, filter_snapshot_skills

    skills = [
        {
            "skill_id": polarity,
            "task_family": "tool.appworld",
            "full_markdown": polarity,
            "lineage": {
                "evidence": (
                    [{"trace_uuid": f"trace-{polarity}", "polarity": polarity}]
                    if polarity != "none"
                    else [{"trace_uuid": "trace-none"}]
                )
            },
        }
        for polarity in ("supporting", "neutral", "contradicting", "none")
    ]
    kept, excluded, _ = filter_snapshot_skills(
        skills,
        [
            {
                "trace_uuid": f"trace-{polarity}" if polarity != "none" else "trace-none",
                "passed": False,
            }
            for polarity in ("supporting", "neutral", "contradicting", "none")
        ],
    )
    funnel = _funnel_for_family("tool.appworld", {}, kept, set(), excluded)
    assert funnel["failed_lineage_skills_by_polarity"] == {
        "supporting": 0,
        "neutral": 1,
        "contradicting": 1,
        "none": 1,
    }
    assert funnel["failed_lineage_traces_by_polarity"] == {
        "supporting": 1,
        "neutral": 1,
        "contradicting": 1,
        "none": 1,
    }


def test_f11_resume_manifest_only_ignores_resume_flag(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    expected = {"arguments": {"resume": False}, "agent_uuid": "agent"}
    path.write_text(json.dumps(expected))
    assert appworld_run._validate_resume_manifest(path, expected) == expected
    with pytest.raises(ValueError, match="mismatch"):
        appworld_run._validate_resume_manifest(
            path, {"arguments": {"resume": True}, "agent_uuid": "agent"}
        )


def test_f12_stage_lock_rejects_a_second_process(tmp_path: Path) -> None:
    first = ExitStack()
    appworld_run._lock_stage(first, tmp_path, "teacher")
    second = ExitStack()
    try:
        with pytest.raises(RuntimeError, match="already running"):
            appworld_run._lock_stage(second, tmp_path, "teacher")
    finally:
        first.close()
        second.close()


def test_f13_journals_are_keyed_by_task_id_and_trial() -> None:
    jobs = appworld_run.startup_jobs(
        ["t1", "t2"],
        {("t1", 1): {"task_id": "t1", "trial": 1, "error": "RuntimeError"}},
        trials=1,
        max_attempts=1,
    )
    assert [(job[1], job[2]) for job in jobs] == [("t1", 1), ("t2", 1)]


def test_f14_removed_cli_flags_and_task_ids_option() -> None:
    tree = ast.parse(Path(appworld_run.__file__).read_text())
    flags = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_argument"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    }
    assert "--task-ids" in flags
    assert "--zep-api-key-uuid" not in flags
    assert "--allow-unverified" not in flags


def test_f15_effort_table_rejects_unknown_models_and_on_off() -> None:
    for model in appworld_run.EFFORTS_BY_MODEL:
        for effort in appworld_run.EFFORTS_BY_MODEL[model]:
            appworld_run.validate_model_effort(model, effort)
        with pytest.raises(ValueError):
            appworld_run.validate_model_effort(model, "on")
    with pytest.raises(ValueError, match="Unknown model"):
        appworld_run.validate_model_effort("unknown", "high")


def test_f16_skill_read_transcript_has_redacted_text_and_hash() -> None:
    redactor = Redactor.from_values({"secret": "classified"})
    metadata, transcript = appworld_run._skill_read_artifact(
        "classified skill text", "requested", True, "Resolved", None, redactor
    )
    assert metadata["name"] == "Resolved"
    assert metadata["sha256"] == hashlib.sha256(b"classified skill text").hexdigest()
    assert "classified" not in transcript
    assert "<SECRET>" in transcript


def test_s1_missing_anthropic_usage_fields_are_none() -> None:
    response = _response(
        SimpleNamespace(
            content=[SimpleNamespace(model_dump=lambda **_: {"type": "text", "text": "ok"})],
            stop_reason="end_turn",
            usage=SimpleNamespace(
                input_tokens=1,
                output_tokens=1,
                cache_read_input_tokens=None,
                cache_creation_input_tokens=None,
            ),
        )
    )
    assert response.usage.input_tokens_details.cached_tokens is None
    assert response.usage.input_tokens_details.cache_creation_tokens is None


def test_s2_heldout_defaults_to_manual_and_rejects_teacher_agent() -> None:
    assert appworld_run._compilation_mode("heldout", None) == "manual"
    with pytest.raises(ValueError, match="differ"):
        appworld_run._validate_snapshot_agent({"agent_uuid": "teacher"}, "heldout", "teacher")


def test_s3_each_result_stores_service_timings() -> None:
    ledger = CallLedger()
    ledger.record_attempt({"service": "zep", "operation": "trace.close", "latency_s": 1.5})
    ledger.record_attempt({"service": "appworld", "operation": "evaluate", "latency_s": 2.5})
    result = appworld_run.AppWorldResult("appworld", "t1", 0, 1)
    appworld_run._record_ledger(result, ledger)
    assert result.zep_s == 1.5
    assert result.appworld_s == 2.5


def test_s5_failed_close_is_a_stage_stopping_error() -> None:
    class Server:
        def call(self, *_args, **_kwargs):
            raise RuntimeError("close failed")

    result = appworld_run.AppWorldResult("appworld", "t1", 0, 1)
    appworld_run._close_appworld(result, Server(), "t1", "r1")
    assert result.error_type == "appworld_close_error"
    assert appworld_run._stage_stop_reason(result)


def test_s6_selects_exact_task_ids() -> None:
    assert appworld_run._select_task_ids(["a", "b", "c"], "c,a", None) == ["c", "a"]
    with pytest.raises(ValueError, match="Unknown"):
        appworld_run._select_task_ids(["a", "b"], "missing", None)


def test_snapshot_hash_is_based_on_snapshot_file_bytes(tmp_path: Path) -> None:
    from skill_snapshot import snapshot_sha256

    path = tmp_path / "snapshot.json"
    path.write_bytes(b'{"skills":[]}\n')
    assert snapshot_sha256(path) == hashlib.sha256(path.read_bytes()).hexdigest()


def test_f11_resume_manifest_accepts_new_commit_only_when_allowed(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"git_commit": "old", "task_ids": ["t1"]}), encoding="utf-8")
    expected = {"git_commit": "new", "task_ids": ["t1"]}
    with pytest.raises(ValueError, match="git_commit"):
        appworld_run._validate_resume_manifest(path, expected)
    assert appworld_run._validate_resume_manifest(path, expected, True)["git_commit"] == "old"
    with pytest.raises(ValueError, match="task_ids"):
        appworld_run._validate_resume_manifest(path, {**expected, "task_ids": ["t2"]}, True)
