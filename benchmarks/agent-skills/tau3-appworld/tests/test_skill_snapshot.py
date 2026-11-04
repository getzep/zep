from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from appworld_run import (
    READ_SKILL_TOOL_NAME,
    TOOL,
    AppWorldResult,
    _load_episode_skill_reader,
    _prepare_skill_catalog,
    _record_skill_reader_usage,
    appworld_api_names,
    should_add_read_skill_nudge,
)
from skill_snapshot import (
    LEARNING_RUNS_PAGE_SIZE,
    SKILL_EVIDENCE_LIST_PAGE_SIZE,
    SKILL_LIST_PAGE_SIZE,
    SKILL_VERSION_LIST_PAGE_SIZE,
    FrozenSkillReader,
    SnapshotExportError,
    _write_teacher_export_error,
    compile_snapshot,
    freeze_manifest,
    wait_for_compilation,
    write_funnel,
)
from zep_capture import TrajectoryRecorder, fit_objective


def _snapshot_skill(skill_id, family, name, description):
    markdown = f"---\nname: {name}\ndescription: {description}\n---\n\nUse this procedure."
    return {
        "skill_id": skill_id,
        "task_family": family,
        "full_markdown": markdown,
    }


def test_read_skill_repairs_names_and_returns_index_on_miss(tmp_path):
    markdown = "---\nname: Book flights\ndescription: Book air travel.\n---\n\nUse the booking API."
    path = tmp_path / "snapshot.json"
    path.write_text(
        json.dumps(
            {
                "skills": [
                    {
                        "skill_id": "s1",
                        "task_family": "tool.appworld",
                        "full_markdown": markdown,
                    }
                ]
            }
        )
    )
    reader = FrozenSkillReader.from_snapshot(path, task_family="tool.appworld")

    text, found, resolved, repaired = reader.read(" book fligts ")
    assert found is True
    assert resolved == "Book flights"
    assert repaired == " book fligts "
    assert text == markdown

    text, found, resolved, repaired = reader.read("unrelated")
    assert found is False
    assert resolved is None
    assert repaired is None
    assert "- Book flights: Book air travel." in text


def test_frozen_skill_reader_filters_prompt_and_rejects_other_family_reads(tmp_path):
    path = tmp_path / "snapshot.json"
    path.write_text(
        json.dumps(
            {
                "skills": [
                    _snapshot_skill("skill-a", "family.a", "Book flights", "Book air travel."),
                    _snapshot_skill("skill-b", "family.b", "Export invoices", "Export invoices."),
                ]
            }
        )
    )

    reader = FrozenSkillReader.from_snapshot(path, task_family="family.a")

    assert [entry.name for entry in reader.index.entries] == ["Book flights"]
    assert "Book flights" in reader.index.prompt_block()
    assert "Export invoices" not in reader.index.prompt_block()
    for requested in ("Export invoices", "Exprot invoices"):
        text, found, resolved, repaired = reader.read(requested)
        assert found is False
        assert resolved is None
        assert repaired is None
        assert "- Export invoices: Export invoices." not in text


@pytest.mark.parametrize("family_value", [None, ""])
def test_frozen_skill_reader_rejects_missing_task_family(tmp_path, family_value):
    record = _snapshot_skill("skill-a", "family.a", "Book flights", "Book air travel.")
    if family_value is None:
        record.pop("task_family")
    else:
        record["task_family"] = family_value
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps({"skills": [record]}))

    with pytest.raises(ValueError, match="task_family"):
        FrozenSkillReader.from_snapshot(path, task_family="family.a")


def test_frozen_skill_reader_checks_duplicate_names_only_in_filtered_family(tmp_path):
    path = tmp_path / "snapshot.json"
    path.write_text(
        json.dumps(
            {
                "skills": [
                    _snapshot_skill("skill-a", "family.a", "Shared name", "First."),
                    _snapshot_skill("skill-b", "family.b", "Shared name", "Second."),
                ]
            }
        )
    )

    reader = FrozenSkillReader.from_snapshot(path, task_family="family.a")

    assert [entry.skill_id for entry in reader.index.entries] == ["skill-a"]


def test_empty_skill_family_omits_tool_prompt_and_catalog(tmp_path):
    path = tmp_path / "snapshot.json"
    path.write_text(
        json.dumps(
            {
                "skills": [
                    _snapshot_skill("skill-a", "family.a", "Book flights", "Book air travel."),
                    _snapshot_skill("skill-b", "family.b", "Export invoices", "Export invoices."),
                ]
            }
        )
    )
    empty_reader = FrozenSkillReader.from_snapshot(path, task_family="family.empty")
    result = AppWorldResult(domain="tool.appworld", task_id="task_1", task_index=0, trial=1)

    skill_reader = _load_episode_skill_reader("skills", path, "family.empty")
    prompt, tools = _prepare_skill_catalog(result, skill_reader)
    row = asdict(result)

    assert empty_reader.index.entries == ()
    assert skill_reader is None
    assert prompt == ""
    assert [tool["name"] for tool in tools] == [TOOL["name"]]
    assert READ_SKILL_TOOL_NAME not in [tool["name"] for tool in tools]
    assert should_add_read_skill_nudge(skill_reader, True, []) is False
    assert row["skill_catalog_size"] == 0
    assert row["skill_catalog_families"] == []


def test_skill_catalog_result_row_records_family_and_loaded_skill_ids(tmp_path):
    path = tmp_path / "snapshot.json"
    path.write_text(
        json.dumps(
            {
                "skills": [
                    _snapshot_skill("skill-a", "family.a", "Book flights", "Book air travel."),
                    _snapshot_skill("skill-b", "family.b", "Export invoices", "Export invoices."),
                ]
            }
        )
    )
    result = AppWorldResult(domain="tool.appworld", task_id="task_1", task_index=0, trial=1)

    skill_reader = _load_episode_skill_reader("skills", path, "family.a")
    prompt, tools = _prepare_skill_catalog(result, skill_reader)
    assert skill_reader is not None
    skill_reader.read("Book flights")
    _record_skill_reader_usage(result, skill_reader)
    row = asdict(result)

    assert "Book flights" in prompt
    assert READ_SKILL_TOOL_NAME in [tool["name"] for tool in tools]
    assert row["skill_catalog_size"] == 1
    assert row["skill_catalog_families"] == ["family.a"]
    assert row["skill_uuids_loaded"] == ["skill-a"]


def test_teacher_and_none_arms_do_not_add_read_skill_nudge():
    tool_calls = [{"name": "execute_python"}]
    assert should_add_read_skill_nudge(None, True, tool_calls) is False
    assert should_add_read_skill_nudge(None, False, tool_calls) is False


def test_appworld_api_names_preserve_order_and_remove_consecutive_duplicates():
    code = (
        "apis.venmo.login(); apis.venmo.login(); "
        "apis.venmo.show_transactions(); apis.spotify.search(); apis.venmo.login()"
    )
    assert appworld_api_names(code) == [
        "venmo.login",
        "venmo.show_transactions",
        "spotify.search",
        "venmo.login",
    ]


def test_appworld_api_names_use_execute_python_when_no_apis_match():
    assert appworld_api_names("print('no API call')") == ["execute_python"]


def test_appworld_api_names_are_capped_at_40():
    code = "; ".join(f"apis.app_{index}.api_{index}()" for index in range(45))
    expected = [f"app_{index}.api_{index}" for index in range(40)]
    assert appworld_api_names(code) == expected


def test_snapshot_export_page_sizes_match_endpoint_limits():
    assert LEARNING_RUNS_PAGE_SIZE == 100
    assert SKILL_LIST_PAGE_SIZE == 20
    assert SKILL_VERSION_LIST_PAGE_SIZE == 100
    assert SKILL_EVIDENCE_LIST_PAGE_SIZE == 100


def test_funnel_counts_smoke_compilation_outcomes_and_passed_evidence(tmp_path):
    outcomes = [
        {
            "agent_uuid": "agent-demo",
            "attempts": 2,
            "candidate_count": 2,
            "compilation_identity": "compilation-1",
            "compilation_window_uuid": "window-1",
            "created_at": "2025-01-01T00:01:00Z",
            "decision": "produced",
            "proposal_count": 2,
            "rejected_add_count": 0,
            "stage": "admission",
            "task_family": "tool.appworld",
            "trace_count": 2,
            "uuid": "outcome-1",
        },
        {
            "agent_uuid": "agent-demo",
            "attempts": 1,
            "candidate_count": 1,
            "compilation_identity": "compilation-2",
            "compilation_window_uuid": "window-1",
            "created_at": "2025-01-01T00:00:00Z",
            "decision": "produced",
            "proposal_count": 1,
            "rejected_add_count": 0,
            "stage": "admission",
            "task_family": "tool.appworld",
            "trace_count": 1,
            "uuid": "outcome-2",
        },
    ]
    report_dir = tmp_path / "tool_appworld"
    report_dir.mkdir()
    (report_dir / "compile_report.json").write_text(
        json.dumps({"compile_outcomes": outcomes}), encoding="utf-8"
    )
    passed_trace = "trace-1"
    traces_path = tmp_path / "traces.jsonl"
    traces_path.write_text(
        "\n".join(
            json.dumps({"trace_uuid": trace_id, "task_family": "tool.appworld", "passed": True})
            for trace_id in (
                passed_trace,
                "trace-2",
                "trace-3",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    snapshot = {
        "skills": [
            {
                "skill_id": "skill-1",
                "task_family": "tool.appworld",
                "full_markdown": "<!-- zep-evidence: summary-1 -->",
                "lineage": {
                    "evidence": [
                        {"summary_version_uuid": "summary-1", "trace_uuid": passed_trace},
                        {"summary_version_uuid": "summary-1", "trace_uuid": passed_trace},
                        {"trace_uuid": "not-a-passed-trace"},
                    ]
                },
            }
        ]
    }

    funnel = write_funnel(tmp_path, ["tool.appworld"], snapshot, traces_path)
    family = funnel["families"]["tool.appworld"]

    assert family["windows"] == 1
    assert family["proposals"] == 3
    assert family["admitted_additions"] == 3
    assert family["rejected_add_count"] == 0
    assert family["rejected_additions"] == {}
    assert family["decisions"] == {"produced": 2, "no_change": 0, "rejected": 0, "failed": 0}
    assert family["passed_traces_cited"] == [passed_trace]


def test_funnel_sums_rejected_additions_by_class(tmp_path):
    report_dir = tmp_path / "tool_appworld"
    report_dir.mkdir()
    (report_dir / "compile_report.json").write_text(
        json.dumps(
            {
                "compile_outcomes": [
                    {
                        "compilation_window_uuid": "window-1",
                        "decision": "failed",
                        "proposal_count": 5,
                        "rejected_add_count": 2,
                        "rejection_class": "quality",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    funnel = write_funnel(tmp_path, ["tool.appworld"], {"skills": []}, None)
    family = funnel["families"]["tool.appworld"]

    assert family["admitted_additions"] == 3
    assert family["rejected_add_count"] == 2
    assert family["rejected_additions"] == {"quality": 2}
    assert family["decisions"]["failed"] == 1


def test_snapshot_export_iterates_every_endpoint_page(tmp_path):
    class Pager:
        def __init__(self, *pages):
            self.pages = pages

        def __iter__(self):
            for page in self.pages:
                yield from page

    class Learning:
        def get(self, *_args, **_kwargs):
            return {"compilation_pending": False}

        def list_runs(self, *_args, page_size, **_kwargs):
            assert page_size == LEARNING_RUNS_PAGE_SIZE
            return Pager(
                [{"decision": "produced", "trace_count": 1}],
                [{"decision": "produced", "trace_count": 1}],
            )

    class Versions:
        def list(self, _agent_uuid, skill_id, *, page_size, **_kwargs):
            assert page_size == SKILL_VERSION_LIST_PAGE_SIZE
            return Pager(
                [
                    {
                        "version": 1,
                        "markdown": f"---\nname: {skill_id}\ndescription: Test.\n---\n\nFull.",
                    }
                ]
            )

    class Evidence:
        def list(self, _agent_uuid, _skill_id, *, page_size, **_kwargs):
            assert page_size == SKILL_EVIDENCE_LIST_PAGE_SIZE
            return Pager([{"trace_uuid": "trace-1"}])

    class Skills:
        version = Versions()
        evidence = Evidence()

        def list(self, *_args, page_size, **_kwargs):
            assert page_size == SKILL_LIST_PAGE_SIZE
            return Pager([{"uuid": "skill-1"}], [{"uuid": "skill-2"}])

        def get(self, _agent_uuid, skill_id):
            return {"markdown": f"Agent view for {skill_id}", "current_version": 1}

    client = SimpleNamespace(agent=SimpleNamespace(learning=Learning(), skill=Skills()))
    output_dir = tmp_path / "skills"

    snapshot = compile_snapshot(client, "agent", ["tool.appworld"], output_dir)

    assert len(snapshot["compile_reports"]["tool.appworld"]["compile_outcomes"]) == 2
    assert [skill["skill_id"] for skill in snapshot["skills"]] == ["skill-1", "skill-2"]
    assert all(skill["full_markdown"].endswith("Full.") for skill in snapshot["skills"])


def test_snapshot_export_filters_failed_and_unresolved_citations(tmp_path):
    family = "tool.appworld"
    markdown = {
        "failed": (
            "---\nname: Failed citation\ndescription: Test.\n---\n\n"
            "<!-- zep-evidence: summary-failed -->"
        ),
        "kept": (
            "---\nname: Lineage-only failure\ndescription: Test.\n---\n\n"
            "<!-- zep-evidence: summary-passed -->"
        ),
        "unresolved": (
            "---\nname: Unresolved citation\ndescription: Test.\n---\n\n"
            "<!-- zep-evidence: summary-missing -->"
        ),
    }
    evidence = {
        "failed": [{"summary_version_uuid": "summary-failed", "trace_uuid": "trace-failed"}],
        "kept": [
            {"summary_version_uuid": "summary-passed", "trace_uuid": "trace-passed"},
            {"summary_version_uuid": "summary-lineage-only", "trace_uuid": "trace-failed"},
        ],
        "unresolved": [],
    }

    class Learning:
        def get(self, *_args, **_kwargs):
            return {"compilation_pending": False}

        def list_runs(self, *_args, **_kwargs):
            return []

    class Versions:
        def list(self, _agent_uuid, skill_id, **_kwargs):
            return [{"version": 1, "markdown": markdown[skill_id]}]

    class Evidence:
        def list(self, _agent_uuid, skill_id, **_kwargs):
            return evidence[skill_id]

    class Skills:
        version = Versions()
        evidence = Evidence()

        def list(self, *_args, **_kwargs):
            return [{"uuid": skill_id, "name": skill_id} for skill_id in markdown]

        def get(self, _agent_uuid, skill_id):
            return {"markdown": markdown[skill_id], "current_version": 1}

    client = SimpleNamespace(agent=SimpleNamespace(learning=Learning(), skill=Skills()))
    traces_path = tmp_path / "traces.jsonl"
    traces_path.write_text(
        "\n".join(
            json.dumps({"trace_uuid": trace_id, "task_family": family, "passed": passed})
            for trace_id, passed in (("trace-failed", False), ("trace-passed", True))
        )
        + "\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "skills"

    snapshot = compile_snapshot(
        client,
        "agent",
        [family],
        output_dir,
        traces_path=traces_path,
    )
    funnel = write_funnel(output_dir, [family], snapshot, traces_path)

    assert [skill["skill_id"] for skill in snapshot["skills"]] == ["kept"]
    assert (
        snapshot["sha256"]
        == hashlib.sha256((output_dir / "snapshot.json").read_bytes()).hexdigest()
    )
    excluded = {skill["skill_id"]: skill for skill in snapshot["excluded_skills"]}
    assert excluded["failed"]["reason"] == "cites_failed_trace"
    assert excluded["failed"]["trace_uuids"] == ["trace-failed"]
    assert excluded["unresolved"]["reason"] == "unresolved_citation"
    assert excluded["unresolved"]["trace_uuids"] == []
    assert set(excluded["failed"]) == {
        "skill_id",
        "name",
        "task_family",
        "reason",
        "trace_uuids",
        "failed_lineage_trace_uuids_by_polarity",
    }
    assert snapshot["lineage_failed_traces"] == {family: ["trace-failed"]}
    assert funnel["excluded_skills"] == snapshot["excluded_skills"]
    assert funnel["families"][family]["lineage_failed_traces"] == 1
    assert funnel["families"][family]["passed_traces_cited"] == ["trace-passed"]
    funnel_markdown = (output_dir / "funnel.md").read_text(encoding="utf-8")
    assert "cites_failed_trace" in funnel_markdown
    assert "unresolved_citation" in funnel_markdown


def test_snapshot_export_failure_raises_without_writing_empty_snapshot(tmp_path):
    class Learning:
        def get(self, *_args, **_kwargs):
            return {"compilation_pending": False}

        def list_runs(self, *_args, **_kwargs):
            return [{"decision": "produced", "trace_count": 1}]

    class Skills:
        def list(self, *_args, **_kwargs):
            raise RuntimeError("skill list failed")

    client = SimpleNamespace(agent=SimpleNamespace(learning=Learning(), skill=Skills()))
    output_dir = tmp_path / "skills"

    with pytest.raises(SnapshotExportError, match="Skill snapshot export failed"):
        compile_snapshot(client, "agent", ["tool.appworld"], output_dir)

    assert not (output_dir / "snapshot.json").exists()
    report = json.loads(
        (output_dir / "tool_appworld" / "compile_report.json").read_text(encoding="utf-8")
    )
    assert report["export_errors"] == [{"error": "RuntimeError", "message": "skill list failed"}]


def test_snapshot_export_fails_when_current_version_is_not_listed(tmp_path):
    class Learning:
        def get(self, *_args, **_kwargs):
            return {"compilation_pending": False}

        def list_runs(self, *_args, **_kwargs):
            return []

    class Versions:
        def list(self, *_args, **_kwargs):
            return [{"version": 1, "markdown": "Old version"}]

    class Skills:
        version = Versions()
        evidence = SimpleNamespace(list=lambda *_args, **_kwargs: [])

        def list(self, *_args, **_kwargs):
            return [{"uuid": "skill-1"}]

        def get(self, *_args, **_kwargs):
            return {"markdown": "Agent view", "current_version": 2}

    client = SimpleNamespace(agent=SimpleNamespace(learning=Learning(), skill=Skills()))
    output_dir = tmp_path / "skills"

    with pytest.raises(SnapshotExportError):
        compile_snapshot(client, "agent", ["tool.appworld"], output_dir)

    assert not (output_dir / "snapshot.json").exists()
    report = json.loads(
        (output_dir / "tool_appworld" / "compile_report.json").read_text(encoding="utf-8")
    )
    assert report["export_errors"] == [
        {"error": "current_version_not_found", "skill_id": "skill-1", "current_version": 2}
    ]


def test_snapshot_export_error_is_written_to_teacher_json(tmp_path):
    output_dir = tmp_path / "skills"
    output_dir.mkdir()
    teacher_path = tmp_path / "teacher.json"
    teacher_path.write_text(
        json.dumps({"stage": "teacher", "agent_uuid": "agent"}), encoding="utf-8"
    )

    _write_teacher_export_error(output_dir, "agent", RuntimeError("skill list failed"))

    teacher = json.loads(teacher_path.read_text(encoding="utf-8"))
    assert teacher["snapshot_export_error"] == {
        "type": "RuntimeError",
        "message": "skill list failed",
    }
    assert teacher["stage"] == "teacher"


def test_compile_wait_requires_quiet_window_after_outcome_change():
    now = [0.0]

    class Learning:
        def list_runs(self, *_args, **_kwargs):
            return [{"uuid": "run", "decision": "produced", "trace_count": 1}]

        def get(self, *_args, **_kwargs):
            return {"compilation_pending": False}

    client = SimpleNamespace(agent=SimpleNamespace(learning=Learning()))
    result = wait_for_compilation(
        client,
        "agent",
        {"tool.appworld": 1},
        poll_interval=1,
        clock=lambda: now[0],
        sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
    )
    assert result["timed_out"] is False
    assert result["seconds"] >= 20 * 60


def test_manifest_refuses_dirty_repository(tmp_path, monkeypatch):
    def run(command, **_kwargs):
        if command[-2:] == ["status", "--porcelain"]:
            return SimpleNamespace(stdout=" M file.py\n")
        return SimpleNamespace(stdout="head\n")

    monkeypatch.setattr("skill_snapshot.subprocess.run", run)
    with pytest.raises(RuntimeError, match="repository is dirty"):
        freeze_manifest(
            tmp_path / "out",
            repository=tmp_path,
            model="claude-opus-5-5",
            effort="max",
            splits=["train"],
            family="tool.appworld",
            snapshot_sha="sha",
        )


def test_objective_is_bounded_and_tool_call_ids_are_paired():
    class Trace:
        def create(self, *_args, **_kwargs):
            return SimpleNamespace(uuid="trace")

        def append_event(self, *_args, **kwargs):
            calls.append(kwargs)

        def close(self, *_args, **_kwargs):
            return None

    calls = []
    client = SimpleNamespace(agent=SimpleNamespace(trace=Trace()))
    recorder = TrajectoryRecorder(
        client,
        "agent",
        "tool.appworld",
        "x" * 10000,
        "appworld",
        {},
        redactor=SimpleNamespace(redact=lambda value: value),
        stage="teacher",
        run_id="run",
        domain="appworld",
        task_index=0,
        trial=1,
        discoverable_tools=frozenset(),
    )
    with recorder:
        recorder.record_tool_call("execute_python", {"code": "1"}, call_id="call-1")
        recorder.record_tool_result("execute_python", "ok", call_id="call-1")
        recorder.set_result(True, True)
    assert len(fit_objective("x" * 10000).encode()) <= 8 * 1024
    assert [call["tool_call_id"] for call in calls] == ["call-1", "call-1"]
    assert all(call["context"].tools[0].name == "execute_python" for call in calls)
    assert all(call["occurred_at"] for call in calls)


def test_funnel_separates_unique_admitted_skills_from_proposals(tmp_path):
    directory = tmp_path / "tool_appworld"
    directory.mkdir()
    skill = {"skill_id": "admitted", "task_family": "tool.appworld", "full_markdown": ""}
    (directory / "compile_report.json").write_text(
        json.dumps(
            {
                "compile_outcomes": [
                    {"decision": "produced", "proposal_count": 1},
                    {"decision": "no_change", "proposal_count": 1},
                ],
                "excluded_skills": [skill],
            }
        )
    )
    snapshot = {"skills": [skill, skill, {**skill, "skill_id": "other", "task_family": "other"}]}
    funnel = write_funnel(tmp_path, ["tool.appworld"], snapshot, None)
    assert funnel["total"]["proposals"] == 2
    assert funnel["total"]["admitted_additions"] == 2
    assert funnel["total"]["unique_admitted_skills"] == 1
    assert "Unique admitted Skills" in (tmp_path / "funnel.md").read_text()


def test_empty_funnel_has_no_unique_admitted_skills(tmp_path):
    funnel = write_funnel(tmp_path, ["tool.appworld"], {"skills": []}, None)
    assert funnel["total"]["unique_admitted_skills"] == 0
    assert funnel["families"]["tool.appworld"]["unique_admitted_skills"] == 0


def test_funnel_rejects_an_admitted_skill_without_an_id(tmp_path):
    with pytest.raises(KeyError, match="skill_id"):
        write_funnel(
            tmp_path, ["tool.appworld"], {"skills": [{"task_family": "tool.appworld"}]}, None
        )
