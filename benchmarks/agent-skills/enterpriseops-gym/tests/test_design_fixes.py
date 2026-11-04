from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path

import pytest

from eog.agent import SkillIndex, build_agent_config, run_agent
from eog.analysis import (
    date_sensitive_task_ids,
    date_sensitivity_report,
    executor_cell_rows,
    paired_analysis,
    reproduction_gate,
    reproduction_report,
    score_summary,
)
from eog.dataset import TOWNHALL_FIXED_TASK_IDS, load_tasks, overlay_task_ids
from eog.effort_preflight import evaluate_effort_preflight
from eog.errors import (
    BudgetExceeded,
    InfrastructureError,
    NonRetryableProviderError,
    PolicyFlagError,
    RetryExhaustedError,
)
from eog.providers.base import ModelTurn, ToolCall, Usage
from eog.run import (
    ZAI_ROUTE_CAP,
    _report_with_reproduction_gate,
    _require_reproduction_report,
    _skill_metadata,
    _task_families,
)
from eog.runner import (
    RunStore,
    classify_episode,
    execute_episode,
    ramp_can_advance,
    ramp_steps,
    run_concurrent,
    sample_score_tasks,
)
from eog.skills import (
    COMMON_PROSE_LITERALS,
    TrajectoryRecorder,
    _task_verifier_literals,
    assert_no_score_literals,
    date_literals,
)
from eog.split import make_split


def _task():
    return next(item for item in load_tasks() if item.domain == "calendar")


def test_score_literal_gate_and_date_scan() -> None:
    item = replace(
        _task(),
        task_id="score-task",
        verifiers=(
            {
                "validation_config": {
                    "query": "SELECT name FROM rooms WHERE name = 'Conference Room A'",
                    "expected_value": "Conference Room A",
                }
            },
        ),
    )
    split = {"tasks": {item.task_id: {"side": "score"}}}
    with pytest.raises(ValueError, match="contains a score-task literal"):
        assert_no_score_literals(
            [{"skill_id": "fake", "markdown": "Use the Conference Room A for the meeting."}],
            split,
            [item],
        )
    assert date_literals("Next 2026-01-02, Tuesday. Also February 3, 2026.") == [
        "2026-01-02",
        "February 3, 2026",
        "Tuesday",
    ]


def test_score_literal_matches_require_alphanumeric_boundaries() -> None:
    item = replace(
        _task(),
        task_id="score-task",
        verifiers=({"validation_config": {"query": "SELECT ticket FROM cases WHERE ticket = 229"}},),
    )
    split = {"tasks": {item.task_id: {"side": "score"}}}
    assert_no_score_literals(
        [{"skill_id": "skill", "markdown": "name: agent-skill-725fd9649e018ae0a492295aa4fe427e"}],
        split,
        [item],
    )
    with pytest.raises(ValueError, match="contains a score-task literal"):
        assert_no_score_literals([{"skill_id": "skill", "markdown": "Set the quantity to 229."}], split, [item])


def test_score_literal_inside_an_identifier_is_not_a_match() -> None:
    item = replace(
        _task(),
        task_id="score-task",
        verifiers=({"validation_config": {"query": "SELECT id FROM calendar_list WHERE name = 'calendar_list'"}},),
    )
    split = {"tasks": {item.task_id: {"side": "score"}}}
    assert_no_score_literals(
        [{"skill_id": "skill", "markdown": "- gym-calendar__get_calendar_list; version: *"}], split, [item]
    )
    with pytest.raises(ValueError, match="contains a score-task literal"):
        assert_no_score_literals([{"skill_id": "skill", "markdown": "Read the calendar_list table."}], split, [item])


def test_analysis_excludes_reproduction_rows_from_executor_comparison() -> None:
    with pytest.raises(ValueError, match="Reproduction rows"):
        executor_cell_rows([{"cell": "reproduce", "status": "valid"}])
    assert executor_cell_rows([{"cell": "executor", "arm": "none"}, {"cell": "reproduce", "arm": "none"}][0:1]) == [
        {"cell": "executor", "arm": "none"}
    ]
    assert reproduction_gate(0.332)
    assert reproduction_gate(0.282)
    assert reproduction_gate(0.382)
    assert not reproduction_gate(0.281)


def test_ramp_gate_stops_on_429_or_infrastructure_failure() -> None:
    assert ramp_can_advance([{"status": "valid", "http_status": 200}])
    assert not ramp_can_advance([{"status": "valid", "http_status": 429}])
    assert not ramp_can_advance([{"status": "infra_failed", "http_status": 200}])
    assert not ramp_can_advance([{"status": "valid", "model_call_attempts": 20, "http_429_attempts": 1}])


def test_glm_ramp_never_exceeds_zai_route_cap() -> None:
    model = "glm-5.3-flash"
    route_cap = ZAI_ROUTE_CAP if model.startswith("glm-") else None
    rows = [{"task_id": str(index), "route": model} for index in range(40)]

    results = run_concurrent(
        rows,
        lambda row: {**row, "status": "valid", "model_call_attempts": 1, "http_429_attempts": 0},
        ramp=ramp_steps(16, "1,2,4,8,16"),
        route_cap=route_cap,
    )

    ramp_rows = [row for row in results if row.get("status") == "ramp"]
    assert [row["concurrency"] for row in ramp_rows[:4]] == [1, 2, 4, 8]
    assert max(row["concurrency"] for row in ramp_rows) == ZAI_ROUTE_CAP


def test_429_share_above_five_percent_reduces_ramp_step() -> None:
    model = "glm-5.3-flash"

    def execute(row: dict[str, str]) -> dict[str, str | int]:
        rate_limited = row["task_id"] == "1"
        return {
            **row,
            "status": "valid",
            "model_call_attempts": 1,
            "http_429_attempts": int(rate_limited),
        }

    results = run_concurrent(
        [{"task_id": str(index), "route": model} for index in range(8)],
        execute,
        ramp=ramp_steps(8, "1,2,4,8"),
        route_cap=ZAI_ROUTE_CAP,
    )

    ramp_rows = [row for row in results if row.get("status") == "ramp"]
    assert any(row["http_429_share"] > 0.05 and row["next_concurrency"] < row["concurrency"] for row in ramp_rows)


def test_route_stops_after_three_consecutive_infrastructure_failures() -> None:
    calls = []
    stopped = []

    def execute(row):
        calls.append(row["task_id"])
        return {**row, "status": "infra_failed"}

    results = run_concurrent(
        [{"task_id": str(index), "route": "route-a"} for index in range(5)],
        execute,
        ramp=(1,),
        checkpoint=stopped.append,
    )
    assert calls == ["0", "1", "2"]
    assert [row["stop_reason"] for row in stopped] == ["three_consecutive_infra_failed"] * 2
    assert all(row["status"] == "stopped" for row in results if row.get("stop_reason"))


def test_resume_skips_policy_flagged_rows_and_keeps_them_separate(tmp_path) -> None:
    store = RunStore(tmp_path, resume=True)
    flagged = {"task_id": "flagged", "trial": 1, "arm": "none", "status": "policy_flagged"}
    store.checkpoint(flagged)
    planned = [
        {"task_id": "flagged", "trial": 1, "arm": "none"},
        {"task_id": "retry", "trial": 1, "arm": "none"},
    ]
    assert store.policy_flagged.path.exists()
    assert store.policy_flagged.path.read_text().strip()
    assert {row["task_id"] for row in store.rows_to_run(planned)} == {"retry"}


def test_trajectory_recorder_closes_only_verified_results(tmp_path) -> None:
    class FakeTraceAPI:
        def __init__(self) -> None:
            self.closed = []
            self.abandoned = []
            self.events = []

        def create(self, *args, **kwargs):
            return type("Trace", (), {"uuid": "trace-1"})()

        def append_event(self, *args, **kwargs):
            self.events.append(kwargs)

        def close(self, *args, **kwargs):
            self.closed.append(kwargs)

        def abandon(self, *args, **kwargs):
            self.abandoned.append(kwargs)

    class FakeClient:
        def __init__(self):
            self.trace = FakeTraceAPI()
            self.agent = type("Agent", (), {"trace": self.trace})()

    trace_path = tmp_path / "traces.jsonl"
    item = _task()
    client = FakeClient()
    recorder = TrajectoryRecorder(client, "agent-1", item, "run-1", 1, trace_path=trace_path)
    with recorder:
        recorder.add_event("tool_call", '{"query":"select 1"}', tool="query", tool_call_id="call_1")
        recorder.add_event("tool_result", "ok", tool="query", tool_call_id="call_1")
        recorder.add_event("tool_call", '{"reason":"done"}', tool="finish", tool_call_id="call_2")
        recorder.add_event("tool_result", "orphan", tool="", tool_call_id="call_9")
        recorder.add_event("context_overflow", "Prompt exceeded the context length.")
        recorder.close(True, verified=True)
    trace_row = json.loads(trace_path.read_text())
    call, result, finish, orphan, overflow = client.trace.events
    assert call["event_type"] == "tool_call"
    assert call["context"] == {"tools": [{"name": "query"}]}
    assert result["context"] == {"tools": [{"name": "query"}]}
    assert finish["context"] == {"tools": [{"name": "finish"}]}
    assert orphan["context"] is None
    assert "tool_name" not in (call["metadata"] or {})
    assert [event["tool_call_id"] for event in client.trace.events[:4]] == ["call_1", "call_1", "call_2", "call_9"]
    assert all("tool_call_id" not in (event["metadata"] or {}) for event in client.trace.events)
    assert overflow["tool_call_id"] is None
    assert overflow["event_type"] == "checkpoint"
    assert client.trace.closed[0]["outcome"] == "succeeded"
    assert client.trace.closed[0]["verification"] == "external"
    assert client.trace.closed[0]["verifier"].verifier_id == "eog-verifier"
    assert client.trace.closed[0]["verifier"].class_ == "harness"
    assert client.trace.closed[0]["verifier"].outcome == "succeeded"
    assert trace_row["passed"] is True and trace_row["verified"] is True

    client = FakeClient()
    recorder = TrajectoryRecorder(client, "agent-1", item, "run-1", 2, trace_path=trace_path)
    with recorder:
        recorder.close(False, verified=True)
    assert client.trace.closed[0]["outcome"] == "failed"
    assert client.trace.closed[0]["verifier"].outcome == "failed"
    assert json.loads(trace_path.read_text().splitlines()[-1])["passed"] is False

    client = FakeClient()
    recorder = TrajectoryRecorder(client, "agent-1", item, "run-1", 3, trace_path=trace_path)
    with recorder:
        recorder.close(False, infra_failed=True)
    failure_row = json.loads(trace_path.read_text().splitlines()[-1])
    assert client.trace.abandoned[0]["reason"] == "infra_failed"
    assert failure_row["infra_failed"] is True and failure_row["passed"] is False


def test_trajectory_exception_abandons_unverified_trace(tmp_path) -> None:
    class FakeTraceAPI:
        def __init__(self) -> None:
            self.closed = []
            self.abandoned = []

        def create(self, *args, **kwargs):
            return type("Trace", (), {"uuid": "trace-1"})()

        def close(self, *args, **kwargs):
            self.closed.append(kwargs)

        def abandon(self, *args, **kwargs):
            self.abandoned.append(kwargs)

    class FakeClient:
        def __init__(self):
            self.trace = FakeTraceAPI()
            self.agent = type("Agent", (), {"trace": self.trace})()

    client = FakeClient()
    recorder = TrajectoryRecorder(client, "agent-1", _task(), "run-1", 1, trace_path=tmp_path / "traces.jsonl")
    with pytest.raises(RuntimeError, match="episode failed"), recorder:
        raise RuntimeError("episode failed")
    row = json.loads((tmp_path / "traces.jsonl").read_text())
    assert client.trace.abandoned[0]["reason"] == "infra_failed"
    assert client.trace.closed == []
    assert row["passed"] is False and row["infra_failed"] is True


def test_effort_preflight_requires_higher_median_reasoning_tokens() -> None:
    rows = [
        {"effort": level, "reasoning_tokens": value}
        for level, values in (
            ("low", (2, 3, 4)),
            ("medium", (3, 4, 5)),
            ("high", (4, 5, 6)),
            ("max", (5, 6, 7)),
        )
        for value in values
    ]
    assert evaluate_effort_preflight("claude-opus-5-5", rows)["passed"]
    for row in rows:
        if row["effort"] == "max":
            row["reasoning_tokens"] = 1
    assert not evaluate_effort_preflight("claude-opus-5-5", rows)["passed"]


def test_episode_active_and_wall_clock_limits() -> None:
    item = _task()
    tools = [{"name": name, "inputSchema": {}} for name in item.selected_tools]

    class SlowRoute:
        model = "claude-opus-5-5"

        def complete(self, *args, **kwargs):
            return ModelTurn(
                "",
                (ToolCall("tool-1", item.selected_tools[0], {}),),
                {},
                Usage(),
                latency_seconds=2699,
            )

    class SlowTool:
        def call_tool(self, name, arguments):
            return {"ok": True}

    active_ticks = iter((0, 1, 1, 2, 3, 3))
    result = run_agent(
        item,
        SlowRoute(),
        SlowTool(),
        tools,
        effort="max",
        clock=lambda: next(active_ticks),
    )
    assert result.finish_reason == "active_time_limit"
    assert result.active_seconds == 2700
    turn_event = next(event for event in result.events if event["type"] == "turn")
    assert turn_event["tool_latencies_seconds"] == [1]

    class FastRoute:
        model = "claude-opus-5-5"

        def complete(self, *args, **kwargs):
            return ModelTurn("", (), {}, Usage(), latency_seconds=1)

    ticks = iter((0, 1, 14_400))
    with pytest.raises(InfrastructureError, match="wall-clock limit"):
        run_agent(item, FastRoute(), SlowTool(), tools, effort="max", clock=lambda: next(ticks))


def test_skill_metadata_and_hybrid_families() -> None:
    task = type(
        "HybridTask",
        (),
        {
            "domain": "hybrid",
            "gym_servers_config": (
                {"mcp_server_name": "gym-calendar"},
                {"mcp_server_name": "sn-hr-internal"},
            ),
        },
    )()
    assert _task_families(task) == ("eog.calendar", "eog.hr", "eog.hybrid")
    skills = [{"id": "skill-1", "sha256": "abc"}]
    assert _skill_metadata("skills", skills, "block-hash") == {
        "skills": skills,
        "skills_count": 1,
        "skill_block_sha256": "block-hash",
    }
    assert _skill_metadata("none", skills, "block-hash") == {
        "skills": skills,
        "skills_count": 1,
        "skill_block_sha256": "",
    }


def test_date_sensitivity_and_overlay_task_counts() -> None:
    tasks = load_tasks()
    relative_date_ids, wall_clock_ids = date_sensitive_task_ids(tasks)
    assert len(relative_date_ids) == 64
    assert len(wall_clock_ids) == 18
    assert len(overlay_task_ids(tasks)) == 10
    report = date_sensitivity_report([], tasks)
    assert report["all"]["none"]["n"] == 0
    assert report["skills_rows_without_family_skills"]["n"] == 0


def test_sample_152_score_tasks_and_ramp_order() -> None:
    tasks = load_tasks()
    split = make_split(tasks)
    sample = sample_score_tasks(tasks, split)
    assert len(sample) == 152
    assert sample == sample_score_tasks(tasks, split)
    assert all(split["tasks"][task.task_id]["side"] == "score" for task in sample)
    assert ramp_steps(4, "1,2,4,8") == (1, 2, 4)


def test_ramp_order_with_concurrency_flag() -> None:
    assert ramp_steps(4, "1,2,4,8") == (1, 2, 4)


def test_skills_arm_without_family_skills_raises_value_error() -> None:
    task = _task()
    tools = [{"name": name, "inputSchema": {"type": "object"}} for name in task.selected_tools]
    agent = build_agent_config(task, tools, arm="skills", skills_block="", skill_index=SkillIndex(()))
    assert agent.arm == "skills"
    assert agent.system
    assert [tool["name"] for tool in agent.tools] == ["finish", *task.selected_tools]


def test_paired_analysis_reports_t_test_bootstrap_ci_and_holm() -> None:
    rows = []
    for task_id, skill_reward in (("a", 1), ("b", 0), ("c", 1)):
        for trial in range(1, 4):
            rows.append(
                {
                    "cell": "executor",
                    "task_id": task_id,
                    "trial": trial,
                    "model": "claude-haiku-5-5",
                    "effort": "low",
                    "arm": "none",
                    "status": "valid",
                    "reward": 0,
                }
            )
            rows.append(
                {
                    "cell": "executor",
                    "task_id": task_id,
                    "trial": trial,
                    "model": "claude-haiku-5-5",
                    "effort": "low",
                    "arm": "skills",
                    "status": "valid",
                    "reward": skill_reward,
                }
            )
    report = paired_analysis(rows)[0]
    assert report["n"] == 3
    assert report["mean_delta"] == pytest.approx(2 / 3)
    assert len(report["bootstrap_95_ci"]) == 2
    assert report["p_value"] is not None
    assert report["p_value_holm"] >= report["p_value"]


@pytest.mark.parametrize(
    ("skill_rewards", "expected_p", "has_note"),
    [((0, 0), 1.0, False), ((1, 0), 0.25, True)],
)
def test_paired_analysis_handles_zero_variance(
    skill_rewards: tuple[int, int], expected_p: float, has_note: bool
) -> None:
    rows = []
    for task_id in ("a", "b", "c"):
        for trial, skill_reward in enumerate(skill_rewards, start=1):
            for arm, reward in (("none", 0), ("skills", skill_reward)):
                rows.append(
                    {
                        "cell": "executor",
                        "task_id": task_id,
                        "trial": trial,
                        "model": "claude-haiku-5-5",
                        "effort": "low",
                        "arm": arm,
                        "status": "valid",
                        "reward": reward,
                    }
                )
    report = paired_analysis(rows)[0]
    assert report["n"] == 3
    assert report["mean_delta"] == (0.5 if has_note else 0.0)
    assert report["p_value"] == expected_p
    assert report["t_statistic"] == (None if has_note else 0.0)
    assert ("note" in report) is has_note
    assert report.get("note") == ("zero_variance" if has_note else None)
    assert report["p_value_holm"] == expected_p
    assert all(
        value is None or math.isfinite(value)
        for value in (report["p_value"], report["p_value_holm"], report["t_statistic"], *report["bootstrap_95_ci"])
    )


def test_reproduction_gate_is_written_and_required(tmp_path: Path) -> None:
    report = {
        "all": {"n": 1947, "p_hat": 0.38},
        "without_overlay_and_known_defects": {"n": 1878, "p_hat": 0.5},
    }
    passed = _report_with_reproduction_gate(report)
    assert passed["gate_passed"] is True
    report_path = tmp_path / "analysis.json"
    report_path.write_text(json.dumps(passed))
    assert _require_reproduction_report(report_path)["gate_passed"] is True

    failed_path = tmp_path / "failed.json"
    failed_path.write_text(
        json.dumps(
            _report_with_reproduction_gate(
                {
                    "all": {"n": 1947, "p_hat": 0.5},
                    "without_overlay_and_known_defects": {"n": 1878, "p_hat": 0.38},
                }
            )
        )
    )
    with pytest.raises(SystemExit, match="did not pass"):
        _require_reproduction_report(failed_path)
    with pytest.raises(SystemExit, match="Pass --reproduction-report"):
        _require_reproduction_report(None)


def test_reproduction_report_excludes_overlay_and_known_defect_tasks() -> None:
    rows = [
        {"cell": "reproduce", "task_id": task_id, "status": "valid", "reward": 1}
        for task_id in ("overlay", "defect", "other")
    ]
    report = reproduction_report(
        rows,
        overlay_task_ids={"overlay"},
        known_defect_ids={"defect"},
    )
    assert report["without_overlay"] == {"n": 2, "passed": 2, "p_hat": 1.0}
    assert report["without_known_defects"] == {"n": 2, "passed": 2, "p_hat": 1.0}
    assert report["without_overlay_and_known_defects"] == {"n": 1, "passed": 1, "p_hat": 1.0}


def test_reproduction_report_shows_townhall_excluded_score_as_secondary() -> None:
    fixed_task_id = next(iter(TOWNHALL_FIXED_TASK_IDS))
    rows = [
        {"cell": "reproduce", "task_id": fixed_task_id, "status": "valid", "reward": 1},
        *[{"cell": "reproduce", "task_id": f"other-{index}", "status": "valid", "reward": 1} for index in range(9)],
    ]
    report = reproduction_report(
        rows,
        overlay_task_ids=set(),
        known_defect_ids=set(),
        townhall_fixed_task_ids=set(TOWNHALL_FIXED_TASK_IDS),
    )

    assert report["all"] == {"n": 10, "passed": 10, "p_hat": 1.0}
    assert report["without_townhall_fixed_tasks"] == {"n": 9, "passed": 9, "p_hat": 1.0}
    assert report["townhall_fixed_task_ids"] == sorted(TOWNHALL_FIXED_TASK_IDS)


def test_reproduction_report_shows_score_with_and_without_type_mismatch_gold() -> None:
    rows = [
        {"cell": "reproduce", "task_id": task_id, "status": "valid", "reward": reward}
        for task_id, reward in (("other", 1),)
    ]
    rows.append({"cell": "reproduce", "task_id": "type-mismatch", "status": "invalid", "reward": 0})
    report = reproduction_report(
        rows,
        overlay_task_ids=set(),
        known_defect_ids=set(),
        type_mismatch_gold_ids={"type-mismatch"},
    )
    assert report["headline"] == {"n": 1, "passed": 1, "p_hat": 1.0}
    assert report["including_type_mismatch_gold"] == {"n": 2, "passed": 1, "p_hat": 0.5}
    assert report["without_type_mismatch_gold"] == {"n": 1, "passed": 1, "p_hat": 1.0}
    assert report["type_mismatch_gold_task_ids"] == ["type-mismatch"]


def test_reproduction_report_distinguishes_all_known_defects_from_type_mismatches() -> None:
    type_mismatch_ids = {f"type-mismatch-{index}" for index in range(19)}
    schema_defect_ids = {"schema-defect-a", "schema-defect-b"}
    context_token_defect_ids = {"context-token-calendar", "context-token-hybrid"}
    missing_selected_tool_ids = {"selected-tool-missing"}
    known_defect_ids = type_mismatch_ids | schema_defect_ids | context_token_defect_ids | missing_selected_tool_ids
    rows = [
        {"cell": "reproduce", "task_id": "ordinary", "status": "valid", "reward": 1},
        {"cell": "reproduce", "task_id": "overlay", "status": "valid", "reward": 1},
        *[{"cell": "reproduce", "task_id": task_id, "status": "valid", "reward": 1} for task_id in schema_defect_ids],
        *[{"cell": "reproduce", "task_id": task_id, "status": "invalid", "reward": 0} for task_id in type_mismatch_ids],
        *[
            {"cell": "reproduce", "task_id": task_id, "status": "invalid", "reward": 0}
            for task_id in context_token_defect_ids
        ],
        *[
            {"cell": "reproduce", "task_id": task_id, "status": "invalid", "reward": 0}
            for task_id in missing_selected_tool_ids
        ],
    ]

    report = reproduction_report(
        rows,
        overlay_task_ids={"overlay"},
        known_defect_ids=known_defect_ids,
        type_mismatch_gold_ids=type_mismatch_ids,
    )

    assert len(report["known_defect_task_ids"]) == 24
    assert len(report["type_mismatch_gold_task_ids"]) == 19
    assert report["all"] == {"n": 26, "passed": 4, "p_hat": 4 / 26}
    assert report["headline"] == {"n": 4, "passed": 4, "p_hat": 1.0}
    assert report["including_type_mismatch_gold"] == {"n": 23, "passed": 4, "p_hat": 4 / 23}
    assert report["without_type_mismatch_gold"] == {"n": 4, "passed": 4, "p_hat": 1.0}
    assert report["without_known_defects"] == {"n": 2, "passed": 2, "p_hat": 1.0}
    assert report["without_overlay"] == {"n": 3, "passed": 3, "p_hat": 1.0}
    assert report["without_overlay_and_known_defects"] == {"n": 1, "passed": 1, "p_hat": 1.0}


def test_verifier_failure_is_a_valid_zero_and_metrics_use_valid_rows() -> None:
    task = replace(_task(), known_defect=False)
    verifier = type("Verifier", (), {"success": False})()
    assert classify_episode(task, object(), verifier) == "valid"
    summary = score_summary(
        [
            {"cell": "reproduce", "task_id": "valid-zero", "status": "valid", "reward": 0},
            {"cell": "reproduce", "task_id": "valid-one", "status": "valid", "reward": 1},
            {"cell": "reproduce", "task_id": "partial", "status": "partial", "reward": 0},
            {"cell": "reproduce", "task_id": "infra-failed", "status": "infra_failed", "reward": 0},
            {"cell": "reproduce", "task_id": "policy-flagged", "status": "policy_flagged", "reward": 0},
        ]
    )
    assert summary == {"n": 3, "passed": 1, "p_hat": 1 / 3}


def test_score_summary_counts_invalid_type_mismatch_after_infra_failure() -> None:
    type_mismatch_key = {
        "task_id": "type-mismatch",
        "trial": 1,
        "cell": "reproduce",
        "model": "claude-haiku-5-5",
        "effort": "low",
        "arm": "none",
    }
    rows = [
        {**type_mismatch_key, "status": "infra_failed", "reward": 1},
        {**type_mismatch_key, "status": "invalid", "reward": 0},
    ]

    assert score_summary(rows, include_zero_task_ids={"type-mismatch"}) == {"n": 1, "passed": 0, "p_hat": 0.0}


def test_score_summary_ignores_partial_and_superseded_and_deduplicates_valid_rows() -> None:
    valid_key = {
        "task_id": "valid",
        "trial": 1,
        "cell": "reproduce",
        "model": "claude-haiku-5-5",
        "effort": "low",
        "arm": "none",
    }
    rows = [
        {
            "task_id": "type-mismatch",
            "trial": 1,
            "cell": "reproduce",
            "model": "claude-haiku-5-5",
            "effort": "low",
            "arm": "none",
            "status": "partial",
            "reward": 1,
        },
        {
            "task_id": "type-mismatch",
            "trial": 1,
            "cell": "reproduce",
            "model": "claude-haiku-5-5",
            "effort": "low",
            "arm": "none",
            "status": "superseded",
            "reward": 1,
        },
        {**valid_key, "status": "valid", "reward": 0},
        {**valid_key, "status": "valid", "reward": 1},
    ]

    assert score_summary(rows, include_zero_task_ids={"type-mismatch"}) == {
        "n": 1,
        "passed": 1,
        "p_hat": 1.0,
    }


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (InfrastructureError("transport"), "infra_failed"),
        (RetryExhaustedError("retry"), "infra_failed"),
        (PolicyFlagError("policy"), "policy_flagged"),
        (BudgetExceeded("budget"), "stopped"),
        (NonRetryableProviderError("provider"), "stopped"),
        (ValueError("defect"), "stopped"),
    ],
)
def test_episode_error_classification(error: Exception, status: str) -> None:
    result = execute_episode(_task(), lambda: (_ for _ in ()).throw(error))
    assert result["status"] == status


def test_common_prose_words_are_not_score_literals() -> None:
    from types import SimpleNamespace

    task = SimpleNamespace(
        verifiers=(
            {
                "validation_config": {
                    "query": (
                        "SELECT 1 FROM channels WHERE purpose = 'establishing' AND name = 'Relocation' "
                        "AND assignment = 'Desk Assignment' AND work_type = 'EMPLOYEE OFFBOARDING' "
                        "AND id = 'CH_7781'"
                    ),
                    "expected_value": {
                        "status": "relocation",
                        "code": "ESTABLISHING",
                        "work_type": "employee offboarding",
                        "ticket": "RLC-0042",
                    },
                }
            },
        )
    )
    assert sorted(COMMON_PROSE_LITERALS) == [
        "desk assignment",
        "employee offboarding",
        "establishing",
        "relocation",
    ]
    assert _task_verifier_literals(task) == {"CH_7781", "RLC-0042"}
