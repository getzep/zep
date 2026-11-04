from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import eog.run as run_module
import eog.skills as skills_module
from eog.skills import assert_skill_sources, compile_snapshot, ensure_agent


def test_skill_lineage_requires_teach_trace() -> None:
    split = {"tasks": {"teacher": {"side": "teach"}, "score": {"side": "score"}}}
    with pytest.raises(ValueError, match="no source trace IDs"):
        assert_skill_sources({"skill": {"markdown": "procedure"}}, split, {})
    trace_rows = {
        "teach-trace": {"task_id": "teacher", "side": "teach", "passed": True},
        "failed-teach-trace": {"task_id": "teacher", "side": "teach", "passed": False},
        "score-trace": {"task_id": "score", "side": "score", "passed": True},
    }
    assert assert_skill_sources({"evidence": [{"trace_uuid": "teach-trace"}]}, split, trace_rows) == ["teach-trace"]
    assert assert_skill_sources({"evidence": [{"trace_uuid": "failed-teach-trace"}]}, split, trace_rows) == [
        "failed-teach-trace"
    ]
    with pytest.raises(ValueError, match="teach-side traces"):
        assert_skill_sources({"evidence": [{"trace_uuid": "score-trace"}]}, split, trace_rows)
    assert assert_skill_sources({"evidence": [{"trace_uuid": "score-trace"}]}, split, trace_rows, task_scope="all") == [
        "score-trace"
    ]
    with pytest.raises(ValueError, match="sides assigned by the split"):
        assert_skill_sources(
            {"evidence": [{"trace_uuid": "score-trace"}]},
            split,
            {**trace_rows, "score-trace": {"task_id": "score", "side": "teach"}},
            task_scope="all",
        )
    with pytest.raises(ValueError, match="teach-side traces"):
        assert_skill_sources({"evidence": [{"trace_uuid": "unknown-trace"}]}, split, trace_rows)


def test_compile_snapshot_accepts_sources_with_matching_split_sides_in_all_scope(tmp_path: Path) -> None:
    trace_path = tmp_path / "traces.jsonl"
    trace_path.write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                {
                    "trace_uuid": "failed-teach-trace",
                    "task_id": "failed-teacher",
                    "side": "teach",
                    "domain": "email",
                    "task_family": "eog.email",
                    "passed": False,
                },
                {
                    "trace_uuid": "passed-teach-trace",
                    "task_id": "passed-teacher",
                    "side": "teach",
                    "domain": "email",
                    "task_family": "eog.email",
                    "passed": True,
                },
                {
                    "trace_uuid": "score-teacher-trace",
                    "task_id": "score-teacher",
                    "side": "score",
                    "domain": "email",
                    "task_family": "eog.email",
                    "passed": True,
                },
            )
        )
        + "\n"
    )

    class FakeSkill:
        def __init__(self, skill_id: str, markdown: str) -> None:
            self.skill_id = skill_id
            self.markdown = markdown

        def model_dump(self, **kwargs: Any) -> dict[str, str]:
            return {"skill_id": self.skill_id, "markdown": self.markdown}

    skills = [
        FakeSkill("failed-skill", "Do the failed task."),
        FakeSkill("passed-skill", "Do the passed task."),
        FakeSkill("score-skill", "Do the score-side task."),
    ]
    trace_by_skill = {
        "failed-skill": "failed-teach-trace",
        "passed-skill": "passed-teach-trace",
        "score-skill": "score-teacher-trace",
    }

    client = SimpleNamespace(
        agent=SimpleNamespace(
            learning=SimpleNamespace(
                get=lambda *args, **kwargs: SimpleNamespace(
                    model_dump=lambda **dump_kwargs: {"compilation_pending": False}
                ),
                list_runs=lambda *args, **kwargs: [],
            ),
            skill=SimpleNamespace(
                list=lambda *args, **kwargs: skills,
                evidence=SimpleNamespace(
                    list=lambda agent_uuid, skill_id, page_size: [
                        SimpleNamespace(model_dump=lambda **dump_kwargs: {"trace_uuid": trace_by_skill[skill_id]})
                    ]
                ),
            ),
        )
    )
    output_dir = tmp_path / "compiled"
    split = {
        "tasks": {
            "failed-teacher": {"side": "teach"},
            "passed-teacher": {"side": "teach"},
            "score-teacher": {"side": "score"},
        }
    }
    failed_hash = hashlib.sha256(skills[0].markdown.encode()).hexdigest()
    passed_hash = hashlib.sha256(skills[1].markdown.encode()).hexdigest()
    output_dir.mkdir()
    (output_dir / f"{failed_hash}.md").write_text(skills[0].markdown)

    snapshot = compile_snapshot(
        client,
        "agent",
        ["eog.email"],
        split,
        output_dir,
        traces_path=trace_path,
        task_scope="all",
    )

    report = json.loads((output_dir / "eog_email" / "compile_report.json").read_text())
    expected_skill_ids = ["failed-skill", "passed-skill", "score-skill"]
    assert [skill["skill_id"] for skill in snapshot["skills"]] == expected_skill_ids
    assert [skill["skill_id"] for skill in json.loads((output_dir / "snapshot.json").read_text())["skills"]] == (
        expected_skill_ids
    )
    assert report["skills_count"] == 3
    assert report["failures"] == []
    assert (output_dir / f"{failed_hash}.md").read_text() == skills[0].markdown
    assert (output_dir / f"{passed_hash}.md").read_text() == skills[1].markdown


def test_compile_snapshot_includes_only_named_pending_families(tmp_path: Path) -> None:
    families = ("eog.csm", "eog.email", "eog.calendar")
    trace_rows = [
        {
            "trace_uuid": f"{family}-trace",
            "task_id": f"{family}-task",
            "side": "teach",
            "domain": family.removeprefix("eog."),
            "task_family": family,
            "passed": True,
        }
        for family in families
    ]
    traces_path = tmp_path / "traces.jsonl"
    traces_path.write_text("\n".join(json.dumps(row) for row in trace_rows) + "\n")
    pending = {"eog.csm": True, "eog.email": True, "eog.calendar": False}
    listed_families: list[str] = []
    failed_run = SimpleNamespace(
        model_dump=lambda **kwargs: {
            "decision": "failed",
            "rejection_class": "curator_attempts_exhausted",
        }
    )

    class FakeSkill:
        def __init__(self, family: str) -> None:
            self.skill_id = f"{family}-skill"
            self.markdown = f"Use the {family} procedure."
            self.family = family

        def model_dump(self, **kwargs: Any) -> dict[str, str]:
            return {"skill_id": self.skill_id, "markdown": self.markdown, "task_family": self.family}

    def make_client() -> SimpleNamespace:
        def list_skills(agent_uuid: str, *, task_family: str, page_size: int) -> list[FakeSkill]:
            listed_families.append(task_family)
            return [FakeSkill(task_family)]

        def list_evidence(agent_uuid: str, skill_id: str, page_size: int) -> list[SimpleNamespace]:
            family = skill_id.removesuffix("-skill")
            return [
                SimpleNamespace(
                    model_dump=lambda **kwargs: {"trace_uuid": f"{family}-trace"},
                )
            ]

        return SimpleNamespace(
            agent=SimpleNamespace(
                learning=SimpleNamespace(
                    get=lambda agent_uuid, *, task_family: SimpleNamespace(
                        model_dump=lambda **kwargs: {"compilation_pending": pending[task_family]}
                    ),
                    list_runs=lambda *args, task_family, **kwargs: [failed_run] if task_family == "eog.csm" else [],
                ),
                skill=SimpleNamespace(
                    list=list_skills,
                    evidence=SimpleNamespace(list=list_evidence),
                ),
            )
        )

    split = {"tasks": {row["task_id"]: {"side": "teach"} for row in trace_rows}}
    default_snapshot = compile_snapshot(
        make_client(),
        "agent",
        list(families),
        split,
        tmp_path / "default",
        traces_path=traces_path,
    )

    assert listed_families == ["eog.calendar"]
    assert [skill["task_family"] for skill in default_snapshot["skills"]] == ["eog.calendar"]
    assert json.loads((tmp_path / "default/eog_csm/compile_report.json").read_text())["pending_family_skipped"]

    listed_families.clear()
    included_snapshot = compile_snapshot(
        make_client(),
        "agent",
        list(families),
        split,
        tmp_path / "included",
        traces_path=traces_path,
        include_pending_families=["eog.csm"],
    )

    assert listed_families == ["eog.csm", "eog.calendar"]
    assert [skill["task_family"] for skill in included_snapshot["skills"]] == ["eog.csm", "eog.calendar"]
    included_report = json.loads((tmp_path / "included/eog_csm/compile_report.json").read_text())
    assert included_report["pending_family_included"]
    assert included_report["failures"] == [
        {
            "error": "compile_failed",
            "outcome": {
                "decision": "failed",
                "rejection_class": "curator_attempts_exhausted",
            },
        }
    ]
    assert json.loads((tmp_path / "included/eog_email/compile_report.json").read_text())["pending_family_skipped"]
    assert (tmp_path / "included/snapshot.json").exists()


def test_compile_failure_message_ignores_failed_runs_for_included_pending_family(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    report_dir = skills_dir / "eog_csm"
    report_dir.mkdir(parents=True)
    (report_dir / "compile_report.json").write_text(
        json.dumps(
            {
                "pending_family_included": True,
                "failures": [{"error": "compile_failed"}],
            }
        )
    )

    assert (
        run_module._compile_failure_message(skills_dir, ["eog.csm"])
        == "Skill compilation failed: eog.csm: compile_failed"
    )
    assert (
        run_module._compile_failure_message(
            skills_dir,
            ["eog.csm"],
            include_pending_families=["eog.csm"],
        )
        is None
    )


def test_ensure_agent_uses_on_close_compilation() -> None:
    captured: dict[str, Any] = {}

    class AgentAPI:
        def create(self, **kwargs: Any) -> SimpleNamespace:
            captured.update(kwargs)
            return SimpleNamespace(uuid="agent-uuid")

    result = ensure_agent(SimpleNamespace(agent=AgentAPI()), "test-run")

    assert result == {"agent_id": "eog-skills-test-run", "agent_uuid": "agent-uuid"}
    assert captured["memory_settings"]["compilation_mode"] == "on_close"


def test_trajectory_recorder_marks_failed_tool_results() -> None:
    captured: list[dict[str, Any]] = []
    trace_api = SimpleNamespace(append_event=lambda *args, **kwargs: captured.append(kwargs))
    recorder = skills_module.TrajectoryRecorder(
        SimpleNamespace(agent=SimpleNamespace(trace=trace_api)),
        "agent-uuid",
        SimpleNamespace(domain="calendar"),
        "run",
        1,
    )
    recorder.uuid = "trace-uuid"

    recorder.add_event(
        "tool_result",
        "Calendar x not found",
        tool="gym-calendar__get_event",
        metadata={"is_error": True},
    )
    recorder.add_event(
        "tool_result",
        "Event found",
        tool="gym-calendar__get_event",
        metadata={"is_error": False},
    )

    assert captured[0]["metadata"] == {"tool_name": "gym-calendar__get_event", "error": True}
    assert captured[1]["metadata"] == {"tool_name": "gym-calendar__get_event"}


def test_trajectory_recorder_persists_score_metadata(tmp_path: Path) -> None:
    trace_api = SimpleNamespace(close=lambda *args, **kwargs: None)
    recorder = skills_module.TrajectoryRecorder(
        SimpleNamespace(agent=SimpleNamespace(trace=trace_api)),
        "agent-uuid",
        SimpleNamespace(task_id="task-1", domain="calendar"),
        "run",
        1,
        trace_path=tmp_path / "traces.jsonl",
    )
    recorder.uuid = "trace-uuid"
    score_metadata = {
        "original": {"reward": 1, "status": "valid", "verifier_passes": [True]},
        "corrected": {"reward": 0, "status": "partial", "verifier_passes": [False]},
    }

    recorder.close(True, verified=True, score_metadata=score_metadata)

    record = json.loads((tmp_path / "traces.jsonl").read_text().splitlines()[0])
    assert record["score_metadata"] == score_metadata


@pytest.mark.parametrize(
    ("event_type", "content", "tool", "is_error", "expected_metadata"),
    [
        (
            "tool_result",
            "Calendar adrian_burton-team not found with timezone America/New_York",
            "gym-calendar__list_events",
            False,
            {"tool_name": "gym-calendar__list_events", "error": True},
        ),
        (
            "tool_result",
            '{"id": "abc", "status": "created", "note": "user not found earlier"}',
            "gym-calendar__create_event",
            False,
            {"tool_name": "gym-calendar__create_event"},
        ),
        (
            "tool_result",
            "Message sent to channel general",
            "gym-teams-mcp__send_channel_message",
            True,
            {"tool_name": "gym-teams-mcp__send_channel_message", "error": True},
        ),
        (
            "tool_call",
            "Calendar adrian_burton-team not found with timezone America/New_York",
            "gym-calendar__list_events",
            False,
            None,
        ),
    ],
)
def test_trajectory_recorder_captures_content_and_mcp_errors(
    event_type: str,
    content: str,
    tool: str,
    is_error: bool,
    expected_metadata: dict[str, Any] | None,
) -> None:
    captured: list[dict[str, Any]] = []
    trace_api = SimpleNamespace(append_event=lambda *args, **kwargs: captured.append(kwargs))
    recorder = skills_module.TrajectoryRecorder(
        SimpleNamespace(agent=SimpleNamespace(trace=trace_api)),
        "agent-uuid",
        SimpleNamespace(domain="calendar"),
        "run",
        1,
    )
    recorder.uuid = "trace-uuid"

    recorder.add_event(
        event_type,
        content,
        tool=tool,
        metadata={"is_error": is_error},
    )

    assert captured[0]["metadata"] == expected_metadata


def test_score_literal_gate_ignores_evidence_comments() -> None:
    task = SimpleNamespace(
        task_id="score-task",
        verifiers=({"validation_config": {"query": "SELECT id FROM cases WHERE id = 4805"}},),
    )

    skills_module.assert_no_score_literals(
        [
            {
                "skill_id": "skill",
                "markdown": "<!-- zep-evidence: 00000000-0000-4000-8000-000000004805 -->",
            }
        ],
        {"tasks": {"score-task": {"side": "score"}}},
        [task],
    )


def test_score_literal_gate_rejects_literal_in_prose_with_evidence_comment() -> None:
    task = SimpleNamespace(
        task_id="score-task",
        verifiers=({"validation_config": {"query": "SELECT id FROM cases WHERE id = 4805"}},),
    )

    with pytest.raises(ValueError, match="contains a score-task literal"):
        skills_module.assert_no_score_literals(
            [
                {
                    "skill_id": "skill",
                    "markdown": ("Review case 4805.\n\n<!-- zep-evidence: 00000000-0000-4000-8000-000000004805 -->"),
                }
            ],
            {"tasks": {"score-task": {"side": "score"}}},
            [task],
        )


def test_score_literal_gate_does_not_ignore_non_uuid_evidence_comment() -> None:
    task = SimpleNamespace(
        task_id="score-task",
        verifiers=({"validation_config": {"query": "SELECT id FROM cases WHERE id = 4805"}},),
    )

    with pytest.raises(ValueError, match="contains a score-task literal"):
        skills_module.assert_no_score_literals(
            [{"skill_id": "skill", "markdown": "<!-- zep-evidence: use case 4805 -->"}],
            {"tasks": {"score-task": {"side": "score"}}},
            [task],
        )


def test_compile_reports_capture_rejections_and_dates(tmp_path: Path) -> None:
    trace_path = tmp_path / "traces.jsonl"
    trace_path.write_text(
        json.dumps(
            {
                "trace_uuid": "teach-trace",
                "task_id": "teacher",
                "trial": 1,
                "side": "teach",
                "domain": "calendar",
                "task_family": "eog.calendar",
                "passed": True,
            }
        )
        + "\n"
    )

    class FakeSkill:
        skill_id = "skill-1"
        markdown = ""

        @staticmethod
        def model_dump(**kwargs: Any) -> dict[str, str]:
            return {"skill_id": "skill-1", "markdown": FakeSkill.markdown}

    class FakeRunPager:
        def __iter__(self):
            yield SimpleNamespace(
                model_dump=lambda **kwargs: {
                    "decision": "rejected",
                    "rejection_class": "calendar-rejection",
                }
            )
            yield SimpleNamespace(
                model_dump=lambda **kwargs: {
                    "decision": "accepted",
                    "rejection_class": None,
                }
            )

        def __len__(self) -> int:
            raise AssertionError("The SDK pager must be iterated, not counted.")

    run_page_sizes: list[int] = []
    skill_page_sizes: list[int] = []
    version_page_sizes: list[int] = []
    evidence_page_sizes: list[int] = []

    def list_runs(agent_uuid: str, task_family: str, page_size: int) -> FakeRunPager:
        run_page_sizes.append(page_size)
        return FakeRunPager()

    def list_skills(agent_uuid: str, task_family: str, page_size: int) -> list[FakeSkill]:
        skill_page_sizes.append(page_size)
        return [FakeSkill()] if task_family == "eog.calendar" else []

    def list_versions(agent_uuid: str, skill_id: str, page_size: int) -> list[SimpleNamespace]:
        version_page_sizes.append(page_size)
        return [SimpleNamespace(markdown="Use 2026-01-02 on Monday.")]

    def list_evidence(agent_uuid: str, skill_id: str, page_size: int) -> list[SimpleNamespace]:
        evidence_page_sizes.append(page_size)
        return [SimpleNamespace(model_dump=lambda **kwargs: {"trace_uuid": "teach-trace"})]

    learning = SimpleNamespace(
        get=lambda agent_uuid, task_family: SimpleNamespace(model_dump=lambda **kwargs: {"compilation_pending": False}),
        list_runs=list_runs,
    )
    skill_api = SimpleNamespace(
        list=list_skills,
        evidence=SimpleNamespace(list=list_evidence),
        version=SimpleNamespace(list=list_versions),
    )
    client = SimpleNamespace(agent=SimpleNamespace(learning=learning, skill=skill_api))

    snapshot = compile_snapshot(
        client,
        "agent",
        ["eog.csm", "eog.calendar"],
        {"tasks": {"teacher": {"side": "teach"}}},
        tmp_path / "compiled",
        traces_path=trace_path,
    )

    csm = json.loads((tmp_path / "compiled/eog_csm/compile_report.json").read_text())
    calendar = json.loads((tmp_path / "compiled/eog_calendar/compile_report.json").read_text())
    assert csm["skills_count"] == 0 and csm["failures"] == []
    assert calendar["traces_captured"] == 1 and calendar["passed_traces"] == 1
    assert calendar["skills_count"] == 1
    assert calendar["skills"] == [{"id": "skill-1", "sha256": snapshot["skills"][0]["sha256"]}]
    assert calendar["date_literals"]["skill-1"] == ["2026-01-02", "Monday"]
    assert calendar["compile_rejections"] == [
        {"outcome": {"decision": "rejected", "rejection_class": "calendar-rejection"}}
    ]
    assert calendar["failures"] == []
    assert [row["decision"] for row in calendar["compile_outcomes"]] == ["rejected", "accepted"]
    assert (tmp_path / "compiled" / "snapshot.json").exists()
    assert run_page_sizes == [100, 100]
    assert skill_page_sizes == [20, 20]
    assert version_page_sizes == [100]
    assert evidence_page_sizes == [100]


@pytest.mark.parametrize(
    ("outcome", "compile_rejections", "failures", "snapshot_written"),
    [
        pytest.param(
            {"decision": "rejected", "rejection_class": "quality_gate"},
            [{"outcome": {"decision": "rejected", "rejection_class": "quality_gate"}}],
            [],
            True,
            id="rejected",
        ),
        pytest.param(
            {"decision": "failed", "rejection_class": "compile_error"},
            [],
            [{"error": "compile_failed", "outcome": {"decision": "failed", "rejection_class": "compile_error"}}],
            False,
            id="failed",
        ),
    ],
)
def test_compile_outcome_classification_controls_snapshot(
    tmp_path: Path,
    outcome: dict[str, str],
    compile_rejections: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    snapshot_written: bool,
) -> None:
    compile_outcome = SimpleNamespace(model_dump=lambda **kwargs: outcome)
    client = SimpleNamespace(
        agent=SimpleNamespace(
            learning=SimpleNamespace(
                get=lambda *args, **kwargs: SimpleNamespace(
                    model_dump=lambda **dump_kwargs: {"compilation_pending": False}
                ),
                list_runs=lambda *args, **kwargs: [compile_outcome],
            ),
            skill=SimpleNamespace(list=lambda *args, **kwargs: []),
        )
    )
    output_dir = tmp_path / "compiled"

    compile_snapshot(
        client,
        "agent",
        ["eog.calendar"],
        {"tasks": {}},
        output_dir,
    )

    report = json.loads((output_dir / "eog_calendar" / "compile_report.json").read_text())
    assert report["compile_outcomes"] == [outcome]
    assert report["compile_rejections"] == compile_rejections
    assert report["failures"] == failures
    assert (output_dir / "snapshot.json").exists() is snapshot_written


def test_compile_stage_waits_before_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    baseline_path = tmp_path / "noop_baseline.jsonl"
    baseline_path.touch()
    monkeypatch.setattr(run_module, "_resolve_noop_baseline_path", lambda _args: baseline_path)
    task = SimpleNamespace(domain="hybrid")
    run_dir = tmp_path / "runs"
    root = run_dir / "compile-test"
    root.mkdir(parents=True)
    (root / "agent.json").write_text(json.dumps({"agent_uuid": "agent-uuid"}))
    wait_result = {"families": {}}
    calls: list[tuple] = []

    preexisting_run_ids = {"eog.hybrid": ["old-run"]}
    (root / "compile-wait-preexisting-run-ids.json").write_text(json.dumps(preexisting_run_ids))

    def fake_wait(client, agent_uuid, closed_traces, *, ceiling_seconds, preexisting_run_ids):
        calls.append(("wait", agent_uuid, closed_traces, ceiling_seconds, preexisting_run_ids))
        return wait_result

    def fake_snapshot(
        client,
        agent_uuid,
        task_families,
        split,
        output_dir,
        *,
        traces_path,
        wait,
        task_scope,
        include_pending_families,
    ):
        calls.append(("snapshot", wait, task_scope, include_pending_families))
        return {"skills": []}

    monkeypatch.setattr(run_module, "load_tasks", lambda: (task,))
    monkeypatch.setattr(run_module, "load_task_sample", lambda all_tasks, task_path: (task,))
    monkeypatch.setattr(run_module, "make_split", lambda all_tasks: {"tasks": {}})
    monkeypatch.setattr(run_module, "_task_families", lambda item: ("eog.hybrid",))
    monkeypatch.setattr(run_module, "zep_client", lambda: object())
    monkeypatch.setattr(run_module, "wait_for_compilation", fake_wait)
    monkeypatch.setattr(run_module, "compile_snapshot", fake_snapshot)
    monkeypatch.setattr(run_module, "write_compile_funnel", lambda *args, **kwargs: {"total": {}})
    monkeypatch.setattr(run_module, "_compile_failure_message", lambda *args, **kwargs: None)
    monkeypatch.setenv("ZEP_API_KEY", "test-key")
    monkeypatch.setenv("ZEP_BASE_URL", "https://api.example.com/api/v4")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "eog.run",
            "--stage",
            "compile",
            "--run-id",
            "compile-test",
            "--run-dir",
            str(run_dir),
            "--compile-wait-minutes",
            "3.5",
            "--include-pending-family",
            "eog.csm",
        ],
    )

    run_module.main()

    assert calls == [
        ("wait", "agent-uuid", {"eog.hybrid": 0}, 210.0, {"eog.hybrid": {"old-run"}}),
        ("snapshot", wait_result, "split", ["eog.csm"]),
    ]


def test_compile_stage_fails_on_family_api_error_without_publishing_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline_path = tmp_path / "noop_baseline.jsonl"
    baseline_path.touch()
    monkeypatch.setattr(run_module, "_resolve_noop_baseline_path", lambda _args: baseline_path)
    task = SimpleNamespace(domain="hybrid")
    run_dir = tmp_path / "runs"
    root = run_dir / "compile-test"
    root.mkdir(parents=True)
    (root / "agent.json").write_text(json.dumps({"agent_uuid": "agent-uuid"}))
    skills_dir = root / "skills"
    skills_dir.mkdir()
    (skills_dir / "snapshot.json").write_text(json.dumps({"skills": [{"skill_id": "stale"}]}))

    def fail_skill_list(*args: Any, **kwargs: Any) -> list[Any]:
        raise RuntimeError("response body must not appear in the exit message")

    client = SimpleNamespace(
        agent=SimpleNamespace(
            learning=SimpleNamespace(
                get=lambda *args, **kwargs: SimpleNamespace(
                    model_dump=lambda **dump_kwargs: {"compilation_pending": False}
                ),
                list_runs=lambda *args, **kwargs: [],
            ),
            skill=SimpleNamespace(list=fail_skill_list),
        )
    )
    monkeypatch.setenv("ZEP_API_KEY", "test-key")
    monkeypatch.setenv("ZEP_BASE_URL", "https://api.example.com/api/v4")
    monkeypatch.setattr(run_module, "load_tasks", lambda: (task,))
    monkeypatch.setattr(run_module, "load_task_sample", lambda all_tasks, task_path: (task,))
    monkeypatch.setattr(run_module, "make_split", lambda all_tasks: {"tasks": {}})
    monkeypatch.setattr(run_module, "_task_families", lambda item: ("eog.hybrid",))
    monkeypatch.setattr(run_module, "zep_client", lambda: client)
    monkeypatch.setattr(
        sys,
        "argv",
        ["eog.run", "--stage", "compile", "--run-id", "compile-test", "--run-dir", str(run_dir)],
    )

    with pytest.raises(SystemExit) as error:
        run_module.main()

    report_path = root / "skills" / "eog_hybrid" / "compile_report.json"
    snapshot_path = root / "skills" / "snapshot.json"
    assert json.loads(report_path.read_text())["failures"][0]["error"] == "RuntimeError"
    assert not snapshot_path.exists()
    assert "eog.hybrid" in str(error.value)
    assert "RuntimeError" in str(error.value)
    assert "response body" not in str(error.value)
