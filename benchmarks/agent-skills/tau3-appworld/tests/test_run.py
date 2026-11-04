import hashlib
import json
import os
import subprocess
import sys
from argparse import Namespace
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from tau2.data_model.tasks import Task
from zep_cloud.core.api_error import ApiError
from zep_cloud.core.pagination import SyncPager

import config
from compile import (
    audit_skills,
    audit_task_answers,
    export_to_fresh_agent,
    wait_for_compilation,
    wait_for_learning_idle,
    wait_for_published_skills,
    wait_for_summaries,
)
from redaction import Redactor
from run import (
    _check_readiness,
    _completed_keys,
    _conditions,
    _episode,
    _latest_rows,
    _metrics,
    _pass_k_metrics,
    _pending_jobs,
    _redact_data,
    _redact_simulation,
    compile_teacher,
    ensure_reward_verifier_binding,
    freeze_compilation,
    literal_tool_names,
    parse_args,
    run_stage,
    task_family_for,
    validate_publication_destination,
)
from zep_capture import (
    SkillToolkit,
    TrajectoryRecorder,
    _idempotency_key,
    ensure_agent,
    skill_use_when,
)


@pytest.fixture
def offline_run_config(monkeypatch: pytest.MonkeyPatch) -> config.Config:
    loaded = config.Config(
        api_url="https://api.example.com",
        api_key="test-zep-key",
        openai_api_key="test-openai-key",
        concurrency=4,
    )
    monkeypatch.setattr("run.load_config", lambda **kwargs: loaded)
    return loaded


def test_benchmark_suppresses_upstream_payload_logs() -> None:
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            """import sys
sys.argv = ['run.py', '--help']
from run import main
from loguru import logger
try:
    main()
except SystemExit as error:
    assert error.code == 0
logger.warning('private task payload')
""",
        ],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "TAU2_DATA_DIR": "private task payload"},
    )
    assert "private task payload" not in process.stderr


def test_resume_key_set_skips_completed_jobs() -> None:
    jobs = [
        ("airline", 0, 1, SimpleNamespace(id="airline-0")),
        ("airline", 0, 2, SimpleNamespace(id="airline-0")),
        ("retail", 1, 1, SimpleNamespace(id="retail-1")),
    ]
    completed = _completed_keys(
        [
            {"domain": "airline", "task_index": 0, "trial": 1},
            {"domain": "retail", "task_index": 1, "trial": 1},
        ]
    )

    pending = _pending_jobs(jobs, completed)

    assert [(domain, index, trial) for domain, index, trial, _ in pending] == [("airline", 0, 2)]


def test_resume_retries_errors_and_counts_only_the_latest_attempt() -> None:
    failed = {
        "domain": "airline",
        "task_index": 0,
        "trial": 1,
        "reward": 0,
        "error": "TimeoutError",
    }
    succeeded = {"domain": "airline", "task_index": 0, "trial": 1, "reward": 1, "error": None}
    job = ("airline", 0, 1, SimpleNamespace(id="airline-0"))

    assert _pending_jobs([job], _completed_keys([failed])) == [job]
    rows = _latest_rows([failed, succeeded])
    assert rows == [succeeded]
    assert _pending_jobs([job], _completed_keys(rows)) == []
    assert _metrics(rows, trials=1)["success_count"] == 1
    assert _metrics(rows, trials=1)["error_count"] == 0


def test_trajectory_close_error_invalidates_scored_trial(monkeypatch: Any) -> None:
    recorder = MagicMock()
    recorder.trajectory_uuid = "trajectory"
    recorder.__exit__.side_effect = RuntimeError("close failed")
    task = _task_for_redaction()
    task.id = "airline-0"
    monkeypatch.setattr("run.TrajectoryRecorder", lambda *args, **kwargs: recorder)
    monkeypatch.setattr(
        "run.run_single_task",
        lambda *args, **kwargs: SimpleNamespace(
            reward_info=SimpleNamespace(reward=1.0), get_messages=list
        ),
    )

    result = _episode(
        MagicMock(),
        MagicMock(),
        stage="teacher",
        run_id="pilot",
        domain="airline",
        task=task,
        task_index=0,
        trial=1,
        agent_uuid="teacher",
        agent_model="gpt-6-sol",
        user_model="gpt-4.1-2025-04-14",
        user_temperature=0.0,
        reasoning_effort="xhigh",
        max_steps=200,
        task_split="base",
        inject_skills=False,
    )

    assert result.error == "RuntimeError"
    assert result.reward == 0.0
    assert result.succeeded is False
    assert result.simulation is None


def test_agent_create_replay_has_the_same_identity() -> None:
    client = MagicMock()
    client.agent.create.return_value = SimpleNamespace(uuid_="agent-uuid")

    assert ensure_agent(client, "teacher", run_id="pilot", compilation_mode="scheduled") == (
        ensure_agent(client, "teacher", run_id="pilot", compilation_mode="scheduled")
    )
    first, second = client.agent.create.call_args_list
    assert first.kwargs == second.kwargs
    assert first.kwargs["agent_id"].startswith("tau2-bench-skills-teacher-")


def test_compile_requires_the_configured_publication_destination() -> None:
    client = MagicMock()
    client.agent.get.side_effect = [
        SimpleNamespace(security_domain="tau2-bench-skills"),
        SimpleNamespace(security_domain="tau2-bench-skills"),
    ]
    assert validate_publication_destination(client, "teacher", "published") == "published"
    assert [call.args[0] for call in client.agent.get.call_args_list] == [
        "teacher",
        "published",
    ]

    client.agent.get.reset_mock()
    with pytest.raises(ValueError, match="--student-agent-uuid"):
        validate_publication_destination(client, "teacher", None)
    with pytest.raises(ValueError, match="distinct"):
        validate_publication_destination(client, "teacher", "teacher")
    client.agent.get.assert_not_called()


def test_compile_rejects_a_destination_in_another_domain() -> None:
    client = MagicMock()
    client.agent.get.side_effect = [
        SimpleNamespace(security_domain="tau2-bench-skills"),
        SimpleNamespace(security_domain="other"),
    ]
    with pytest.raises(ValueError, match="security domain"):
        validate_publication_destination(client, "teacher", "other")


def test_teacher_rejects_a_shared_agent_before_any_api_call() -> None:
    args = Namespace(stage="teacher", agent_uuid="shared-agent")
    client = MagicMock()

    with pytest.raises(ValueError, match="fresh Agent"):
        run_stage(args, client, MagicMock())

    client.assert_not_called()


def test_existing_stage_requires_explicit_resume_before_any_api_call(tmp_path: Any) -> None:
    output_dir = tmp_path / "pilot"
    output_dir.mkdir()
    (output_dir / "teacher.jsonl").write_text("{}\n")
    args = Namespace(
        stage="teacher", agent_uuid=None, results_root=tmp_path, run_id="pilot", resume=False
    )
    client = MagicMock()

    with pytest.raises(ValueError, match="use --resume"):
        run_stage(args, client, MagicMock())

    client.assert_not_called()


def test_skill_search_reads_only_the_requested_page() -> None:
    client = MagicMock()
    get_next = MagicMock(side_effect=AssertionError("Another page was fetched"))
    pager = SyncPager(
        has_next=True,
        items=[{"uuid": "skill-1"}, {"uuid": "skill-2"}],
        response={"search_id": "search-1"},
        get_next=get_next,
    )
    client.agent.skill.search.return_value = pager
    toolkit = SkillToolkit(client, "agent-uuid", "tool.airline")

    assert toolkit.search_skills("Visible user request", limit=1) == [{"uuid": "skill-1"}]
    get_next.assert_not_called()


def test_skill_use_when_reads_conditions_without_evidence_comments() -> None:
    markdown = (
        "## Invocation\n\nUse this Skill when:\n\n- A holds. <!-- zep-evidence: e1 -->\n"
        "- B holds.\n\n## Procedure\n\n- Not a condition.\n"
    )
    assert skill_use_when(markdown) == ["A holds.", "B holds."]
    assert skill_use_when("# Skill") == []


def test_skill_toolkit_records_uses_only_for_loaded_skills() -> None:
    client = MagicMock()
    client.agent.skill.search.return_value = SyncPager(
        has_next=False,
        items=[{"uuid": "skill-1", "markdown": "one"}, {"uuid": "skill-2", "markdown": "two"}],
        get_next=None,
        response={"search_id": "search-1"},
    )
    toolkit = SkillToolkit(client, "agent-uuid", "tool.airline")
    toolkit.search_skills("Cancel a reservation")

    assert "skill-1" in toolkit.catalog() and "one" not in toolkit.catalog()
    assert toolkit.load_skill("skill-2") == "two"
    assert toolkit.load_skill("skill-2") == "two"
    assert [skill["uuid"] for skill in toolkit.loaded] == ["skill-2"]


def test_saved_simulation_redacts_strings_without_changing_scores() -> None:
    redactor = Redactor.from_task(_task_for_redaction(), "airline")
    simulation = {"messages": [{"text": "Contact mia.li3818@example.com"}], "reward": 1.0}
    result = _redact_data(simulation, redactor)

    assert "mia.li3818@example.com" not in json.dumps(result)
    assert result["reward"] == 1.0


def test_saved_simulation_redacts_identifier_keys() -> None:
    redactor = Redactor()
    result = _redact_data({"AB1234": {"value": "AB1234"}}, redactor)

    assert result == {"<RESERVATION_1>": {"value": "<RESERVATION_1>"}}


def test_saved_simulation_keeps_task_identity_outside_redaction() -> None:
    task_id = "[mobile_data_issue]user_abroad_roaming_disabled_on"
    task = SimpleNamespace(
        id=task_id,
        model_dump=lambda: {
            "id": task_id,
            "user_scenario": {"persona": "Mia Li, mia.li3818@example.com"},
        },
    )
    simulation = {
        "task_id": task_id,
        "trial": None,
        "messages": [{"text": f"{task_id} mia.li3818@example.com"}],
    }
    result = _redact_simulation(simulation, cast(Task, task), "telecom", 3)

    assert result["task_id"] == task_id and result["trial"] == 3
    assert task_id not in json.dumps(result["messages"]) and "mia.li3818" not in json.dumps(result)


def _task_for_redaction() -> SimpleNamespace:
    return SimpleNamespace(
        model_dump=lambda: {"user_scenario": {"persona": "Mia Li, mia.li3818@example.com"}}
    )


def test_pass_k_metrics_groups_rows_by_task_index() -> None:
    rows = [
        {"task_index": 0, "reward": 1.0},
        {"task_index": 0, "reward": 0.0},
        {"task_index": 1, "reward": 1.0},
        {"task_index": 1, "reward": 1.0},
    ]

    metrics = _pass_k_metrics(rows, trials=2)

    assert metrics == {"pass^1": 0.75, "pass^2": 0.5}


def test_pass_k_metrics_keeps_domains_separate() -> None:
    rows = [
        {"domain": "airline", "task_index": 0, "reward": 1.0},
        {"domain": "airline", "task_index": 0, "reward": 1.0},
        {"domain": "retail", "task_index": 0, "reward": 0.0},
        {"domain": "retail", "task_index": 0, "reward": 0.0},
    ]
    assert _pass_k_metrics(rows, trials=2) == {"pass^1": 0.5, "pass^2": 0.5}


def test_skill_audit_requires_teacher_maps_and_finds_identifiers() -> None:
    skills = [{"uuid": "skill-1", "markdown": "Cancel reservation AB1234."}]
    with pytest.raises(ValueError, match="teacher task identifier maps"):
        audit_skills(skills, [])
    with pytest.raises(ValueError, match="teacher task identifier maps"):
        audit_skills(skills, [{}])
    assert audit_skills(skills, [{"<RESERVATION_1>": "AB1234"}]) == [
        {"uuid": "skill-1", "name": None, "hits": ["AB1234"]}
    ]


def test_metrics_keep_failed_episodes_in_success_denominator() -> None:
    rows = [
        {
            "domain": "airline",
            "task_index": 0,
            "reward": 1,
            "steps": 2,
            "prompt_tokens": 80,
            "completion_tokens": 20,
            "cached_tokens": 10,
        },
        {
            "domain": "airline",
            "task_index": 1,
            "reward": 0,
            "steps": 4,
            "prompt_tokens": 160,
            "completion_tokens": 40,
            "cached_tokens": 20,
        },
    ]
    metrics = _metrics(rows, trials=1)
    assert metrics["pass^1"] == 0.5
    assert metrics["total_tokens"] == 300
    assert metrics["tokens_per_success"] == 300
    assert metrics["steps_per_success"] == 6
    assert metrics["mean_successful_tokens"] == 100
    assert metrics["mean_cached_tokens"] == 15
    assert metrics["actual_skill_use_rate"] == 0.0


def test_readiness_rejects_changed_task_ids_and_student_model(
    tmp_path: Any, monkeypatch: Any, offline_run_config: config.Config
) -> None:
    output_dir = tmp_path / "pilot"
    output_dir.mkdir()
    manifest = {
        "ready": True,
        "conditions": {},
        "seed": 300,
        "families": {
            "tool.airline": {
                "attached": [
                    {
                        "source_uuid": "original",
                        "destination_uuid": "imported",
                        "source_version": 1,
                        "destination_version": 1,
                        "source_version_uuid": "source-version",
                        "destination_version_uuid": "destination-version",
                        "publication_status": "succeeded",
                        "lineage_status": "verified",
                        "audit_status": "passed",
                        "answer_audit_status": "passed",
                        "audited_markdown_sha256": hashlib.sha256(
                            b"Check the policy first"
                        ).hexdigest(),
                        "audit_hit_count": 0,
                        "policy_uuid": "policy",
                        "policy_revision": 1,
                        "admission_status": "admitted",
                        "attachment_status": "active",
                    }
                ]
            }
        },
        "task_indices": {"airline": [0]},
        "task_ids": {"airline": ["expected"]},
        "teacher_agent_uuid": "teacher",
        "student_agent_uuid": "fresh",
    }
    args = Namespace(
        readiness_file=None,
        results_root=str(tmp_path),
        run_id="pilot",
        domains=["airline"],
        tasks="0",
        stage="student-skills",
        seed=300,
        agent_model="gpt-6-luna",
        agent_uuid=None,
        trials=2,
        reasoning_effort="xhigh",
        user_model="gpt-4.1-2025-04-14",
        user_temperature=0.0,
        max_steps=200,
        task_split="base",
        input_price_per_million=None,
        cached_input_price_per_million=None,
        output_price_per_million=None,
    )
    manifest["conditions"] = {**_conditions(args), "agent_model": "gpt-6-sol"}
    teacher_rows = [{"trajectory_uuid": "original-trajectory"}]
    (output_dir / "teacher.jsonl").write_text(json.dumps(teacher_rows[0]) + "\n")
    manifest["teacher_rows_sha256"] = hashlib.sha256(
        json.dumps(teacher_rows, sort_keys=True).encode()
    ).hexdigest()
    (output_dir / "compile.json").write_text(json.dumps(manifest))
    tasks = {"airline": [SimpleNamespace(id="different")]}
    with pytest.raises(RuntimeError, match="task IDs"):
        _check_readiness(args, MagicMock(), tasks)
    tasks["airline"][0].id = "expected"
    args.agent_model = "gpt-6-sol"
    with pytest.raises(RuntimeError, match="a teacher model and a different student model"):
        _check_readiness(args, MagicMock(), tasks)
    args.agent_model = "gpt-6-luna"
    monkeypatch.setattr(
        "run.publication_lineage",
        lambda *args: {
            "source_agent_uuid": "teacher",
            "source_skill_uuid": "original",
            "source_version_uuid": "source-version",
            "source_version": 1,
            "destination_skill_uuid": "imported",
            "destination_version_uuid": "destination-version",
            "policy_uuid": "policy",
            "policy_revision": 1,
        },
    )
    monkeypatch.setattr(
        "run.Redactor.from_task",
        lambda *args: SimpleNamespace(replacement_map={"<TASK_IDENTIFIER>": "TASK123"}),
    )
    monkeypatch.setattr("run._skill_markdown", lambda *args: "Check the policy first")
    client = MagicMock()
    client.agent.skill.get.return_value = SimpleNamespace(
        status="active", current_version=1, task_family="tool.airline", searchable=True
    )
    client.agent.skill.version.get.return_value = SimpleNamespace(version=1)
    assert _check_readiness(args, client, tasks) == manifest
    monkeypatch.setattr("run.publication_lineage", lambda *args: None)
    with pytest.raises(RuntimeError, match="lineage changed"):
        _check_readiness(args, client, tasks)
    (output_dir / "teacher.jsonl").write_text(
        json.dumps({"trajectory_uuid": "later-trajectory"}) + "\n"
    )
    with pytest.raises(RuntimeError, match="trajectories differ"):
        _check_readiness(args, client, tasks)
    (output_dir / "teacher.jsonl").write_text(json.dumps(teacher_rows[0]) + "\n")
    args.stage = "student-plain"
    args.agent_uuid = "teacher"
    with pytest.raises(RuntimeError, match="distinct Agent"):
        _check_readiness(args, client, tasks)


def test_export_failure_records_separate_stage_outcomes() -> None:
    client = MagicMock()
    client.agent.skill.export.create.return_value = SimpleNamespace(uuid_="task")
    client.task.get.return_value = {"status": "failed", "error": "customer payload archive error"}
    result = export_to_fresh_agent(
        client,
        "teacher",
        [{"uuid": "original", "current_version": 1}],
        "student",
    )
    assert len(result) == 1
    assert result[0]["export_status"] == "not_started"
    assert result[0]["import_status"] == "not_started"
    assert result[0]["attachment_status"] == "not_started"
    assert result[0]["error"] == "RuntimeError"
    assert "customer payload" not in json.dumps(result)
    client.agent.skill.import_package.assert_not_called()


def test_export_attempts_for_different_student_agents_use_different_keys() -> None:
    client = MagicMock()
    client.agent.skill.export.create.return_value = SimpleNamespace(uuid_="task")
    client.task.get.return_value = {"status": "failed", "error": "archive error"}
    skill = [{"uuid": "original", "current_version": 1}]

    export_to_fresh_agent(client, "teacher", skill, "student-one")
    export_to_fresh_agent(client, "teacher", skill, "student-two")

    first, second = client.agent.skill.export.create.call_args_list
    assert first.kwargs["idempotency_key"] == _idempotency_key(
        "export:student-one:teacher:original:1"
    )
    assert second.kwargs["idempotency_key"] == _idempotency_key(
        "export:student-two:teacher:original:1"
    )
    assert first.kwargs["idempotency_key"] != second.kwargs["idempotency_key"]


def test_import_uses_the_completed_task_skill_uuid() -> None:
    client = MagicMock()
    client.agent.skill.export.create.return_value = SimpleNamespace(uuid_="export-task")
    client.agent.skill.export.get.return_value = [b"archive"]
    client.agent.skill.import_package.return_value = SimpleNamespace(uuid_="import-task")
    client.task.get.side_effect = [
        {"status": "succeeded"},
        {"status": "succeeded", "result": {"skill_uuid": "imported"}},
    ]
    client.agent.skill.get.side_effect = [
        SimpleNamespace(task_family="tool.airline", current_version=1),
        SimpleNamespace(status="active", searchable=True, task_family="tool.airline"),
    ]
    client.agent.skill.approve.return_value = SimpleNamespace(decision="admitted")
    result = export_to_fresh_agent(
        client,
        "teacher",
        [{"uuid": "original", "current_version": 1, "task_family": "tool.airline"}],
        "student",
    )
    assert result[0]["export_status"] == "succeeded"
    assert result[0]["import_status"] == "succeeded"
    assert result[0]["admission_status"] == "admitted"
    assert result[0]["attachment_status"] == "active"
    assert result[0]["destination_uuid"] == "imported"
    client.agent.skill.export.create.assert_called_once_with(
        "teacher",
        "original",
        1,
        idempotency_key=_idempotency_key("export:student:teacher:original:1"),
    )
    client.agent.skill.import_package.assert_called_once_with(
        "student",
        request=b"archive",
        idempotency_key=_idempotency_key("import:student:original:1"),
    )
    client.agent.skill.approve.assert_called_once_with(
        "student",
        "imported",
        expected_version=1,
        idempotency_key=_idempotency_key("approve:student:imported:1"),
    )
    client.agent.skill.list.assert_not_called()


def test_import_without_skill_uuid_fails_closed() -> None:
    client = MagicMock()
    client.agent.skill.export.create.return_value = SimpleNamespace(uuid_="export-task")
    client.agent.skill.export.get.return_value = [b"archive"]
    client.agent.skill.import_package.return_value = SimpleNamespace(uuid_="import-task")
    client.task.get.side_effect = [{"status": "succeeded"}, {"status": "succeeded"}]
    result = export_to_fresh_agent(
        client,
        "teacher",
        [{"uuid": "original", "current_version": 1}],
        "student",
    )
    assert result[0]["export_status"] == "succeeded"
    assert result[0]["import_status"] == "not_started"
    assert result[0]["error"] == "RuntimeError"
    client.agent.skill.approve.assert_not_called()


def test_published_candidate_requires_lineage_and_leakage_audit(monkeypatch: Any) -> None:
    client = MagicMock()
    source = SimpleNamespace(status="active", searchable=True, current_version=1)
    candidate = SimpleNamespace(status="candidate", current_version=1, task_family="tool.airline")
    active = SimpleNamespace(
        status="active", current_version=1, task_family="tool.airline", searchable=True
    )
    client.agent.skill.get.side_effect = [source, candidate, active]
    client.agent.skill.version.get.side_effect = [
        SimpleNamespace(version=1),
        SimpleNamespace(version=1),
    ]
    client.agent.skill.approve.return_value = SimpleNamespace(decision="admitted")
    monkeypatch.setattr(
        "compile.find_publication_lineage",
        lambda *args: {
            "source_agent_uuid": "teacher",
            "source_skill_uuid": "original",
            "source_version_uuid": "source-version",
            "source_version": 1,
            "destination_agent_uuid": "student",
            "destination_skill_uuid": "destination",
            "destination_version_uuid": "destination-version",
            "policy_uuid": "policy",
            "policy_revision": 1,
        },
    )
    monkeypatch.setattr("compile._skill_markdown", lambda *args: "Check the policy first")
    monkeypatch.setattr("compile.audit_task_answers", lambda *args: True)
    result = wait_for_published_skills(
        client,
        "teacher",
        "student",
        [{"uuid": "original", "current_version": 1, "task_family": "tool.airline"}],
        [{"<TASK_IDENTIFIER>": "TASK123"}],
        [MagicMock()],
        MagicMock(),
        timeout=1,
        poll_interval=0,
    )
    assert result[0]["publication_status"] == "succeeded"
    assert result[0]["lineage_status"] == "verified"
    assert result[0]["audit_status"] == "passed"
    assert result[0]["answer_audit_status"] == "passed"
    assert result[0]["admission_status"] == "admitted"
    assert result[0]["attachment_status"] == "active"
    client.agent.skill.approve.assert_called_once()

    client.reset_mock()
    client.agent.skill.get.side_effect = [source, candidate]
    client.agent.skill.version.get.side_effect = [SimpleNamespace(version=1)]
    monkeypatch.setattr("compile.audit_task_answers", lambda *args: False)
    result = wait_for_published_skills(
        client,
        "teacher",
        "student",
        [{"uuid": "original", "current_version": 1, "task_family": "tool.airline"}],
        [{"<TASK_IDENTIFIER>": "TASK123"}],
        [MagicMock()],
        MagicMock(),
        timeout=1,
        poll_interval=0,
    )
    assert result[0]["audit_status"] == "passed"
    assert result[0]["answer_audit_status"] == "not_started"
    assert result[0]["error"] == "RuntimeError"
    client.agent.skill.approve.assert_not_called()

    client.reset_mock()
    client.agent.skill.get.side_effect = [source, candidate]
    client.agent.skill.version.get.side_effect = [SimpleNamespace(version=1)]
    monkeypatch.setattr("compile._skill_markdown", lambda *args: "Use TASK123")
    result = wait_for_published_skills(
        client,
        "teacher",
        "student",
        [{"uuid": "original", "current_version": 1, "task_family": "tool.airline"}],
        [{"<TASK_IDENTIFIER>": "TASK123"}],
        [MagicMock()],
        MagicMock(),
        timeout=1,
        poll_interval=0,
    )
    assert result[0]["audit_status"] == "not_started"
    assert result[0]["audit_hit_count"] > 0
    client.agent.skill.approve.assert_not_called()


@pytest.mark.parametrize(
    ("decision", "safe"),
    [
        ('{"leaks_task_answer": false}', True),
        ('{"leaks_task_answer": true}', False),
    ],
)
def test_task_answer_audit_blocks_leaked_answers(decision: str, safe: bool) -> None:
    client = MagicMock()
    client.chat.completions.create.return_value.choices = [
        SimpleNamespace(message=SimpleNamespace(content=decision))
    ]
    task = MagicMock()
    task.model_dump.return_value = {"description": "Resolve the request"}
    assert audit_task_answers(client, "Check the policy first", [task]) is safe
    sent = client.chat.completions.create.call_args.kwargs["messages"]
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "gpt-6-luna"
    assert kwargs["reasoning_effort"] == "low"
    assert "temperature" not in kwargs
    assert "Resolve the request" in sent[1]["content"]
    assert "Check the policy first" in sent[1]["content"]


def test_task_answer_audit_rejects_invalid_response() -> None:
    client = MagicMock()
    client.chat.completions.create.return_value.choices = [
        SimpleNamespace(message=SimpleNamespace(content='{"leaks_task_answer": "false"}'))
    ]
    task = MagicMock()
    task.model_dump.return_value = {"description": "Resolve the request"}
    with pytest.raises(ValueError, match="invalid decision"):
        audit_task_answers(client, "Check the policy first", [task])


def test_compilation_waits_for_produced_outcome_after_rejection() -> None:
    client = MagicMock()
    client.agent.learning.list_runs.side_effect = [
        [{"decision": "rejected", "created_at": "2026-01-01T00:00:00+00:00"}],
        [
            {"decision": "rejected", "created_at": "2026-01-01T00:00:00+00:00"},
            {
                "decision": "produced",
                "created_at": "2026-01-01T00:01:00+00:00",
                "reason_text": "private task data",
            },
        ],
    ]

    result = wait_for_compilation(
        client, "teacher", "tool.airline", None, timeout=5, poll_interval=0
    )

    assert result["outcome"]["decision"] == "produced"
    assert result["timed_out"] is False
    assert "private task data" not in json.dumps(result)
    assert client.agent.learning.list_runs.call_count == 2


def test_compilation_summary_does_not_save_trajectory_text() -> None:
    client = MagicMock()
    client.agent.trace.get_summary.return_value = {"summary": "private task data"}

    result = wait_for_summaries(client, "teacher", ["trajectory"], timeout=5)

    assert result == {"summary_count": 1, "pending": [], "timed_out": False}
    assert "private task data" not in json.dumps(result)


def test_compilation_summary_waits_for_missing_summary() -> None:
    client = MagicMock()
    client.agent.trace.get_summary.side_effect = [
        ApiError(status_code=404),
        {"summary": "private task data"},
    ]

    result = wait_for_summaries(client, "teacher", ["trajectory"], timeout=5, poll_interval=0)

    assert result == {"summary_count": 1, "pending": [], "timed_out": False}
    assert client.agent.trace.get_summary.call_count == 2


def test_compilation_summary_fails_on_authentication_error() -> None:
    client = MagicMock()
    client.agent.trace.get_summary.side_effect = ApiError(status_code=401)

    with pytest.raises(ApiError) as error:
        wait_for_summaries(client, "teacher", ["trajectory"], timeout=5, poll_interval=0)

    assert error.value.status_code == 401
    client.agent.trace.get_summary.assert_called_once_with("teacher", "trajectory")


def test_reward_verifier_binding_preserves_existing_bindings() -> None:
    class Binding:
        def __init__(self, principal_id: str, strength: str) -> None:
            self.principal_type = "project-api-key"
            self.principal_id = principal_id
            self.effective_strength = strength

    class Verifier:
        def __init__(self) -> None:
            self.uuid_ = "verifier-uuid"
            self.revision = 3
            self.principal_bindings = [Binding("old-key", "customer_verified")]

    class VerifierClient:
        def list(self, agent_uuid: str, *, verifier_id: str, page_size: int) -> Any:
            assert agent_uuid == "agent-uuid"
            assert verifier_id == "tau2-bench-reward"
            assert page_size == 1
            return type("Page", (), {"items": [Verifier()]})()

        def update(self, agent_uuid: str, verifier_uuid: str, **kwargs: Any) -> None:
            assert agent_uuid == "agent-uuid"
            assert verifier_uuid == "verifier-uuid"
            assert kwargs["expected_revision"] == 3
            bindings = kwargs["principal_bindings"]
            assert [(item.principal_id, item.effective_strength) for item in bindings] == [
                ("old-key", "customer_verified"),
                ("new-key", "tool_verified"),
            ]

    client = type("Client", (), {"agent": type("Agent", (), {"verifier": VerifierClient()})()})()
    ensure_reward_verifier_binding(client, "agent-uuid", "new-key")


class _VerifierClient:
    def __init__(self, items: list[Any]) -> None:
        self.items = items
        self.updates = 0

    def list(self, agent_uuid: str, *, verifier_id: str, page_size: int) -> Any:
        assert page_size == 1
        return type("Page", (), {"items": self.items})()

    def update(self, *_: Any, **__: Any) -> None:
        self.updates += 1


def _verifier_client(items: list[Any]) -> Any:
    verifier = _VerifierClient(items)
    return type("Client", (), {"agent": type("Agent", (), {"verifier": verifier})()})(), verifier


def test_reward_verifier_binding_waits_for_inline_registration() -> None:
    client, verifier = _verifier_client([])
    ensure_reward_verifier_binding(client, "agent-uuid", None)
    assert verifier.updates == 0


def test_reward_verifier_binding_requires_a_principal_for_a_registered_verifier() -> None:
    registered = type("Verifier", (), {"uuid": "v", "revision": 1, "principal_bindings": []})()
    client, verifier = _verifier_client([registered])
    with pytest.raises(ValueError, match="--zep-api-key-uuid"):
        ensure_reward_verifier_binding(client, "agent-uuid", None)
    ensure_reward_verifier_binding(client, "agent-uuid", None, allow_unverified=True)
    assert verifier.updates == 0


def test_wait_for_learning_idle_polls_until_no_run_is_pending() -> None:
    states = iter([{"compilation_pending": True}, {"compilation_pending": False}])
    learning = MagicMock()
    learning.get.side_effect = lambda *_, **__: next(states)
    client = MagicMock()
    client.agent.learning = learning

    wait_for_learning_idle(client, "agent-uuid", ["tool.airline"], timeout=5, poll_interval=0)

    assert learning.get.call_count == 2


def test_wait_for_learning_idle_rejects_a_server_without_the_state() -> None:
    client = MagicMock()
    client.agent.learning.get.return_value = {"task_family": "tool.airline"}
    with pytest.raises(RuntimeError, match="compilation_pending"):
        wait_for_learning_idle(client, "agent-uuid", ["tool.airline"], timeout=5, poll_interval=0)


def test_task_family_scope_selects_domain_or_task_family() -> None:
    assert task_family_for("retail", 7, "domain") == "tool.retail"
    assert task_family_for("retail", 7, "task") == "tool.retail.task007"


def test_freeze_compilation_sets_manual_mode_with_the_current_revision() -> None:
    client = MagicMock()
    client.agent.get.return_value = SimpleNamespace(
        revision=3, memory_settings={"compilation_mode": "on_close", "approval": "auto"}
    )
    freeze_compilation(client, "agent-1")
    client.agent.update.assert_called_once_with(
        "agent-1",
        expected_revision=3,
        memory_settings={"compilation_mode": "manual", "approval": "auto"},
    )


def test_freeze_compilation_skips_an_agent_in_manual_mode() -> None:
    client = MagicMock()
    client.agent.get.return_value = SimpleNamespace(
        revision=3, memory_settings={"compilation_mode": "manual"}
    )
    freeze_compilation(client, "agent-1")
    client.agent.update.assert_not_called()


@pytest.mark.parametrize("succeeded", [True, False])
def test_trajectory_recorder_closes_a_verified_outcome_with_its_verifier(succeeded: bool) -> None:
    client = MagicMock()
    client.agent.trace.create.return_value = SimpleNamespace(uuid="trajectory-uuid")
    recorder = TrajectoryRecorder(
        client,
        "agent-uuid",
        "tool.airline.task000",
        "objective",
        "environment",
        {},
        redactor=Redactor(),
        discoverable_tools=frozenset(),
        stage="heldout",
        run_id="run",
        domain="airline",
        task_index=0,
        trial=1,
    )
    with recorder:
        recorder.set_result(succeeded, True)
    kwargs = client.agent.trace.close.call_args.kwargs
    outcome = "succeeded" if succeeded else "failed"
    assert kwargs["outcome"] == outcome
    assert kwargs["verification"] == "external"


def test_trajectory_recorder_uses_the_first_request_as_the_objective() -> None:
    client = MagicMock()
    client.agent.trace.create.return_value = SimpleNamespace(uuid="trajectory-uuid")
    recorder = TrajectoryRecorder(
        client,
        "agent-uuid",
        "tool.airline.task000",
        "Complete the user's airline request.",
        "environment",
        {},
        redactor=Redactor(),
        discoverable_tools=frozenset(),
        stage="heldout",
        run_id="run",
        domain="airline",
        task_index=0,
        trial=1,
        objective_from_request=True,
    )
    with recorder:
        client.agent.trace.create.assert_not_called()
        recorder.record_message("assistant", "How can I help?")
        recorder.record_message("user", "Cancel my reservation.")
        recorder.record_message("user", "Also change my seat.")
        recorder.set_result(True, True)
    create = client.agent.trace.create.call_args.kwargs
    assert create["objective"] == "Cancel my reservation."
    client.agent.trace.create.assert_called_once()
    contents = [call.kwargs["content"] for call in client.agent.trace.append_event.call_args_list]
    assert contents == [
        "assistant: How can I help?",
        "user: Cancel my reservation.",
        "user: Also change my seat.",
    ]
    assert recorder.trajectory_uuid == "trajectory-uuid"


def test_trajectory_recorder_uses_the_fallback_objective_without_a_request() -> None:
    client = MagicMock()
    client.agent.trace.create.return_value = SimpleNamespace(uuid="trajectory-uuid")
    objective = "Complete the user's airline request. " * 30
    recorder = TrajectoryRecorder(
        client,
        "agent-uuid",
        "tool.airline.task000",
        objective,
        "environment",
        {},
        redactor=Redactor(),
        discoverable_tools=frozenset(),
        stage="heldout",
        run_id="run",
        domain="airline",
        task_index=0,
        trial=1,
        objective_from_request=True,
    )
    with recorder:
        recorder.record_message("assistant", "How can I help?")
    assert client.agent.trace.create.call_args.kwargs["objective"] == objective
    client.agent.trace.close.assert_called_once()


def test_task_scoped_teacher_rejects_a_scheduled_compilation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    offline_run_config: config.Config,
) -> None:
    base = [
        "run.py",
        "--stage",
        "teacher",
        "--run-id",
        "r",
        "--domains",
        "airline",
        "--family-scope",
        "task",
    ]
    monkeypatch.setattr(sys, "argv", base)
    with pytest.raises(SystemExit):
        parse_args()
    assert "--compilation-mode on_close" in capsys.readouterr().err
    monkeypatch.setattr(sys, "argv", [*base, "--compilation-mode", "on_close"])
    args = parse_args()
    assert args.compilation_mode == "on_close"
    assert args.concurrency == offline_run_config.concurrency


def test_braintrust_source_parse_args_without_zep_key_or_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.delenv("ZEP_API_KEY", raising=False)
    monkeypatch.setenv("TAU_BENCH_SKILLS_CONCURRENCY", "3")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run.py",
            "--stage",
            "braintrust-source",
            "--domains",
            "airline",
            "--run-id",
            "parse-test",
        ],
    )

    args = parse_args()

    assert args.stage == "braintrust-source"
    assert args.concurrency == 3


def test_run_stage_closes_braintrust_logger_after_stage_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    closed: list[bool] = []

    class FakeBraintrustLogger:
        def __init__(self, _project_name: str) -> None:
            pass

        def close(self) -> None:
            closed.append(True)

    def fail_stage(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("stage failed")

    monkeypatch.setattr("run.BraintrustLogger", FakeBraintrustLogger)
    monkeypatch.setattr("run.get_tasks", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("run._run_stage_body", fail_stage)
    args = Namespace(
        stage="braintrust-source",
        results_root=tmp_path,
        run_id="close-test",
        resume=False,
        domains=["airline"],
        task_split="base",
        braintrust_project="pilot",
    )

    with pytest.raises(RuntimeError, match="stage failed"):
        run_stage(args, MagicMock(), MagicMock())

    assert closed == [True]


def test_task_scoped_teacher_waits_for_trace_coverage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    waited: list[dict[str, int]] = []
    exported: list[int] = []
    monkeypatch.setattr(
        "run.wait_for_trace_coverage",
        lambda _client, _agent, closed, **_kwargs: (
            waited.append(closed) or {"families": {}, "timed_out": False}
        ),
    )
    monkeypatch.setattr(
        "run.compile_snapshot",
        lambda _client, _agent, families, _dir, **kwargs: (
            exported.append(len(kwargs["traces_path"].read_text().splitlines())) or {"skills": []}
        ),
    )
    monkeypatch.setattr(
        "run.write_funnel",
        lambda *_args: {"total": {"decisions": {}}},
    )
    monkeypatch.setattr(
        "run.run_compile", lambda *_args: pytest.fail("run_compile reads domain families")
    )
    rows = [
        {"task_family": "tool.airline.task004", "trajectory_uuid": "t1"},
        {"task_family": "tool.airline.task004", "trajectory_uuid": "t2"},
        {"task_family": "tool.airline.task000", "trajectory_uuid": "t3"},
        {"task_family": "tool.airline.task001", "trajectory_uuid": None},
    ]
    (tmp_path / "teacher.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    args = Namespace(
        family_scope="task", stage="teacher", compile_timeout=5, compile_poll_interval=0
    )

    compile_teacher(args, MagicMock(), "agent-uuid", tmp_path, [])

    assert waited == [{"tool.airline.task000": 1, "tool.airline.task004": 2}]
    assert exported == [3]


def test_trajectory_recorder_sends_v4_trace_requests() -> None:
    import httpx
    from zep_cloud.client import Zep

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        trace = {
            "uuid": "trace-uuid",
            "agent_uuid": "agent-uuid",
            "objective": "Complete the request.",
            "outcome": "succeeded",
            "status": "closed",
        }
        if request.url.path.endswith("/events"):
            return httpx.Response(200, json={"uuid": "event-uuid", "sequence": 1})
        return httpx.Response(200, json=trace)

    client = Zep(
        api_key="test",
        base_url="http://zep.test/api/v4",
        httpx_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    recorder = TrajectoryRecorder(
        client,
        "agent-uuid",
        "tool.airline.task000",
        "Complete the request.",
        "environment",
        {},
        redactor=Redactor(),
        discoverable_tools=frozenset(),
        stage="heldout",
        run_id="run",
        domain="airline",
        task_index=0,
        trial=1,
        parent_trajectory_uuid="parent-uuid",
    )
    with recorder:
        recorder.record_message("user", "Cancel my reservation.")
        recorder.set_result(True, True)

    create, append, close = requests
    assert create.url.path == "/api/v4/agents/agent-uuid/traces"
    body = json.loads(create.content)
    assert body["parent_trace_uuid"] == "parent-uuid"
    assert body["task_family"] == "tool.airline.task000"
    assert "parent_trajectory_uuid" not in body
    assert append.url.path == "/api/v4/agents/agent-uuid/traces/trace-uuid/events"
    assert json.loads(append.content)["content"] == "user: Cancel my reservation."
    assert close.url.path.startswith("/api/v4/agents/agent-uuid/traces/trace-uuid/")
    assert json.loads(close.content)["verifier"]["class"] == "harness"


def test_fit_objective_keeps_the_objective_within_the_compiler_limit() -> None:
    from zep_capture import MAX_OBJECTIVE_BYTES, fit_objective

    short = "Cancel my reservation. " * 30
    assert fit_objective(short) == short
    long = "Rebook the flight to Zürich. " * 400
    fitted = fit_objective(long)
    assert len(fitted.encode()) <= MAX_OBJECTIVE_BYTES
    assert long.startswith(fitted)
    assert len(fitted.encode()) > MAX_OBJECTIVE_BYTES - 4


def test_resumed_teacher_counts_the_latest_attempt_and_marks_timeouts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    waited: list[dict[str, int]] = []
    passed_wait: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "run.wait_for_trace_coverage",
        lambda _client, _agent, closed, **_kwargs: (
            waited.append(closed)
            or {
                "families": {family: {"done": False} for family in closed},
                "timed_out": True,
            }
        ),
    )
    monkeypatch.setattr(
        "run.compile_snapshot",
        lambda *_args, **kwargs: passed_wait.append(kwargs["wait"]) or {"skills": []},
    )
    monkeypatch.setattr("run.write_funnel", lambda *_args: {"total": {"decisions": {}}})
    episode = {"domain": "airline", "task_index": 4, "trial": 1}
    rows = [
        {**episode, "task_family": "tool.airline.task004", "trajectory_uuid": "t1"},
        {**episode, "task_family": "tool.airline.task004", "trajectory_uuid": "t2"},
    ]
    (tmp_path / "teacher.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    args = Namespace(
        family_scope="task", stage="teacher", compile_timeout=5, compile_poll_interval=0
    )

    compile_teacher(args, MagicMock(), "agent-uuid", tmp_path, [])

    assert waited == [{"tool.airline.task004": 1}]
    assert passed_wait[0]["families"]["tool.airline.task004"]["timed_out"] is True


def test_literal_tool_names_include_discoverable_context_names() -> None:
    names = literal_tool_names("banking_knowledge")

    assert len(names) <= 256
    assert "KB_search" in names
    assert "call_discoverable_agent_tool:open_bank_account_4821" in names
    assert "unlock_discoverable_agent_tool:open_bank_account_4821" in names
    assert not any(name.startswith("list_discoverable_agent_tools:") for name in names)
