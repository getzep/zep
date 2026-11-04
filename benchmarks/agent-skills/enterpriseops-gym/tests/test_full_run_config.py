import json

import pytest

from eog.run import (
    _compile_failure_message,
    _compile_failures,
    _cost_ledger_path,
    _executor_compile_preflight,
    _parse_compile_failure_families,
    _resolve_run_config,
    _should_record_trajectory,
)


def test_teacher_defaults_to_fable_once_at_max_and_keeps_opus_available() -> None:
    assert _resolve_run_config("teacher", "stirrup", None, None, None) == ("claude-fable-5-1", "max", 1)
    assert _resolve_run_config("teacher", "stirrup", "claude-opus-5-5", None, None) == (
        "claude-opus-5-5",
        "max",
        1,
    )
    assert _resolve_run_config("teacher", "claude_code", None, None, None) == ("claude-opus-5-5", "max", 1)
    assert _resolve_run_config("reproduce", "stirrup", None, None, None) == ("claude-opus-5-5", "max", 3)


def test_each_run_stage_and_cell_uses_a_separate_cost_ledger(tmp_path) -> None:
    teacher = _cost_ledger_path(tmp_path, "teacher", "claude-fable-5-1", "max")
    executor = _cost_ledger_path(tmp_path, "executor", "claude-fable-5-1", "max")
    another_cell = _cost_ledger_path(tmp_path, "executor", "gpt-6-luna", "low")

    assert len({teacher, executor, another_cell}) == 3


def test_executor_ignores_compile_rejections_but_reports_compile_failures(tmp_path) -> None:
    report_dir = tmp_path / "eog_csm"
    report_dir.mkdir()
    report_path = report_dir / "compile_report.json"
    report_path.write_text('{"compile_rejections": [{"outcome": {"decision": "rejected"}}], "failures": []}')

    assert _compile_failure_message(tmp_path, ["eog.csm"]) is None

    report_path.write_text('{"compile_rejections": [], "failures": [{"error": "timeout"}]}')

    assert _compile_failure_message(tmp_path, ["eog.csm"]) == "Skill compilation failed: eog.csm: timeout"


def test_compile_failure_message_skips_families_without_reports(tmp_path, capsys) -> None:
    families = [
        "eog.calendar",
        "eog.csm",
        "eog.drive",
        "eog.email",
        "eog.hr",
        "eog.hybrid",
        "eog.itsm",
        "eog.teams",
    ]
    for family in ("eog.calendar", "eog.teams"):
        report_dir = tmp_path / family.replace(".", "_")
        report_dir.mkdir()
        (report_dir / "compile_report.json").write_text('{"failures": []}')

    assert _compile_failure_message(tmp_path, families) is None

    stderr = capsys.readouterr().err
    for family in families:
        if family not in ("eog.calendar", "eog.teams"):
            assert f"No compile report for {family}:" in stderr


def test_compile_failure_message_returns_present_failure_when_other_reports_are_missing(tmp_path) -> None:
    families = [
        "eog.calendar",
        "eog.csm",
        "eog.drive",
        "eog.email",
        "eog.hr",
        "eog.hybrid",
        "eog.itsm",
        "eog.teams",
    ]
    report_dir = tmp_path / "eog_calendar"
    report_dir.mkdir()
    (report_dir / "compile_report.json").write_text('{"failures": [{"error": "timeout"}]}')

    assert _compile_failure_message(tmp_path, families) == "Skill compilation failed: eog.calendar: timeout"


def _write_compile_report(tmp_path, family: str, failures: list[dict[str, str]]) -> None:
    report_dir = tmp_path / family.replace(".", "_")
    report_dir.mkdir()
    (report_dir / "compile_report.json").write_text(json.dumps({"failures": failures}))


def test_compile_failure_families_are_sorted_and_trimmed() -> None:
    assert _parse_compile_failure_families(" eog.hr, ,eog.email, eog.hr ") == [
        "eog.email",
        "eog.hr",
        "eog.hr",
    ]


def test_executor_preflight_allows_failures_in_named_families(tmp_path, capsys) -> None:
    _write_compile_report(tmp_path, "eog.email", [{"error": "compile_failed"} for _ in range(4)])

    failures = _executor_compile_preflight(tmp_path, ["eog.email"], ["eog.email"])

    assert failures == {"eog.email": {"compile_failed": 4}}
    assert capsys.readouterr().err == ("Compile failures allowed by --allow-compile-failures: eog.email: 4\n")
    assert _compile_failures(tmp_path, ["eog.email"]) == failures


def test_executor_preflight_reports_all_failures_when_one_family_is_not_allowed(tmp_path) -> None:
    _write_compile_report(tmp_path, "eog.email", [{"error": "timeout"}])
    _write_compile_report(tmp_path, "eog.hr", [{"error": "compile_failed"}])

    with pytest.raises(SystemExit) as error:
        _executor_compile_preflight(tmp_path, ["eog.email", "eog.hr"], ["eog.email"])

    assert str(error.value) == "Skill compilation failed: eog.email: timeout; eog.hr: compile_failed"


def test_executor_preflight_rejects_failures_when_flag_is_missing(tmp_path) -> None:
    _write_compile_report(tmp_path, "eog.csm", [{"error": "timeout"}])

    with pytest.raises(SystemExit) as error:
        _executor_compile_preflight(tmp_path, ["eog.csm"], None)

    assert str(error.value) == "Skill compilation failed: eog.csm: timeout"


def test_trajectory_recorder_is_enabled_only_for_teacher() -> None:
    zep = object()

    assert _should_record_trajectory({"stage": "teacher"}, zep, "agent")
    assert not _should_record_trajectory({"stage": "executor"}, zep, "agent")
