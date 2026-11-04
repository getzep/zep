from __future__ import annotations

import json
from pathlib import Path

import pytest

from eog.funnel import compile_funnel, funnel_markdown, write_compile_funnel

FABLE_RUN = Path(__file__).resolve().parents[1] / "results/full-run/runs/fullrun-teacher-fable-5-1"
FAMILIES = [f"eog.{name}" for name in ("calendar", "csm", "drive", "email", "hr", "hybrid", "itsm", "teams")]


def _outcome(
    decision: str,
    proposals: int,
    window: str | None,
    rejection_class: str | None = None,
    rejected_adds: int = 0,
) -> dict:
    outcome = {
        "decision": decision,
        "proposal_count": proposals,
        "rejected_add_count": rejected_adds,
        "rejection_class": rejection_class,
        "candidate_count": 1 if decision == "produced" else 0,
    }
    if window:
        outcome["compilation_window_uuid"] = window
    return outcome


def test_family_funnel_counts_admitted_and_rejected_adds_and_skips_missing_window_uuids() -> None:
    from eog.funnel import family_funnel

    report = {
        "task_family": "eog.calendar",
        "compile_outcomes": [
            _outcome("produced", 2, "w1", rejected_adds=1),
            _outcome("rejected", 1, "w1", "curator_rejected", rejected_adds=1),
            _outcome("no_change", 1, None),
            _outcome("rejected", 1, None, "scanner_rejected"),
        ],
    }
    funnel = family_funnel(report, [], set())
    assert funnel["windows"] == 4 and funnel["window_uuids"] == 1
    assert funnel["proposals"] == 5 and funnel["add_proposals"] == 1 and funnel["rejected_adds"] == 2
    assert funnel["no_change"] == 1
    assert funnel["rejections"] == {"curator_rejected": 1, "scanner_rejected": 1, "other": 0}


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    output_dir = tmp_path / "skills"
    (output_dir / "eog_calendar").mkdir(parents=True)
    (output_dir / "eog_csm").mkdir(parents=True)
    (output_dir / "eog_calendar" / "compile_report.json").write_text(
        json.dumps(
            {
                "task_family": "eog.calendar",
                "passed_traces": 3,
                "compile_outcomes": [
                    _outcome("produced", 2, "w1"),
                    _outcome("no_change", 1, "w1"),
                    _outcome("rejected", 1, "w1", "curator_rejected", rejected_adds=1),
                    _outcome("rejected", 1, "w2", "scanner_rejected"),
                    _outcome("rejected", 1, "w2", "quality_gate"),
                ],
            }
        )
    )
    (output_dir / "eog_csm" / "compile_report.json").write_text(
        json.dumps({"task_family": "eog.csm", "passed_traces": 1, "compile_outcomes": []})
    )
    (output_dir / "snapshot.json").write_text(
        json.dumps(
            {
                "skills": [
                    {
                        "skill_id": "skill-1",
                        "task_family": "eog.calendar",
                        "date_literals": ["Monday"],
                        "lineage": {
                            "skill": {"tools": ["gym-calendar__create_event"]},
                            "evidence": [{"trace_uuid": "t-pass-1"}, {"trace_uuid": "t-fail-1"}],
                        },
                    },
                    {
                        "skill_id": "skill-2",
                        "task_family": "eog.calendar",
                        "date_literals": [],
                        "lineage": {"skill": {"tools": []}, "evidence": [{"trace_uuid": "t-pass-1"}]},
                    },
                ]
            }
        )
    )
    traces_path = tmp_path / "traces.jsonl"
    traces_path.write_text(
        "".join(
            json.dumps(row) + "\n"
            for row in (
                {"trace_uuid": "t-pass-1", "task_family": "eog.calendar", "passed": True},
                {"trace_uuid": "t-pass-2", "task_family": "eog.calendar", "passed": True},
                {"trace_uuid": "t-pass-3", "task_family": "eog.calendar", "passed": True},
                {"trace_uuid": "t-fail-1", "task_family": "eog.calendar", "passed": False},
                {"trace_uuid": "t-csm", "task_family": "eog.csm", "passed": True},
            )
        )
    )
    return output_dir, traces_path


def test_funnel_counts_on_a_fixture_report(tmp_path: Path) -> None:
    output_dir, traces_path = _fixture(tmp_path)
    funnel = write_compile_funnel(output_dir, ["eog.calendar", "eog.csm"], traces_path=traces_path)
    calendar = funnel["families"]["eog.calendar"]
    assert calendar["windows"] == 5 and calendar["window_uuids"] == 2
    assert calendar["proposals"] == 6 and calendar["add_proposals"] == 2 and calendar["rejected_adds"] == 1
    assert calendar["no_change"] == 1
    assert calendar["rejections"] == {"curator_rejected": 1, "scanner_rejected": 1, "other": 1}
    assert calendar["skills"] == 2
    assert (
        calendar["passed_traces"] == 3
        and calendar["passed_traces_cited"] == 1
        and calendar["passed_traces_uncited"] == 2
    )
    assert calendar["traces_cited"] == 2
    assert calendar["tools_per_skill"] == {"skill-1": ["gym-calendar__create_event"], "skill-2": []}
    assert calendar["date_literals"] == {"skill-1": ["Monday"], "skill-2": []}
    csm = funnel["families"]["eog.csm"]
    assert (
        csm["windows"] == 0 and csm["skills"] == 0 and csm["passed_traces"] == 1 and csm["passed_traces_uncited"] == 1
    )
    total = funnel["total"]
    assert total["windows"] == 5 and total["proposals"] == 6 and total["skills"] == 2
    assert total["passed_traces"] == 4 and total["passed_traces_cited"] == 1 and total["passed_traces_uncited"] == 3
    assert total["rejections"] == {"curator_rejected": 1, "scanner_rejected": 1, "other": 1}
    assert total["skills_with_tools"] == 1 and total["skills_with_date_literals"] == 1
    assert json.loads((output_dir / "funnel.json").read_text()) == funnel
    markdown = (output_dir / "funnel.md").read_text()
    assert markdown == funnel_markdown(funnel)
    assert "| eog.calendar | 5 | 2 | 6 | 2 | 1 | 1 | 1 | 1 | 1 | 2 | 3 | 1 | 2 | 1 | 1 |" in markdown
    assert "| total | 5 | 2 | 6 | 2 | 1 | 1 | 1 | 1 | 1 | 2 | 4 | 1 | 3 | 1 | 1 |" in markdown
    assert "| `skill-1` | eog.calendar | gym-calendar__create_event | Monday |" in markdown


def test_funnel_without_a_trace_ledger_reports_unknown_citations(tmp_path: Path) -> None:
    output_dir, _ = _fixture(tmp_path)
    funnel = compile_funnel(output_dir, ["eog.calendar"], traces_path=tmp_path / "missing.jsonl")
    calendar = funnel["families"]["eog.calendar"]
    assert calendar["passed_traces"] == 3
    assert calendar["passed_traces_cited"] is None and calendar["passed_traces_uncited"] is None
    assert funnel["total"]["passed_traces_cited"] is None and funnel["total"]["traces_cited"] == 2


def test_funnel_reads_the_exported_replay_file_shape(tmp_path: Path) -> None:
    output_dir, _ = _fixture(tmp_path)
    replay = tmp_path / "replay.jsonl"
    replay.write_text(
        "".join(
            json.dumps(line) + "\n"
            for line in (
                {
                    "trajectory": {"uuid": "t-pass-1", "task_family": "eog.calendar", "outcome": "succeeded"},
                    "run_row": {"passed": True},
                },
                {
                    "trajectory": {"uuid": "t-fail-1", "task_family": "eog.calendar", "outcome": "failed"},
                    "run_row": {"passed": False},
                },
                {"trajectory": {"uuid": "t-x", "task_family": "eog.calendar", "outcome": "succeeded"}},
            )
        )
    )
    funnel = compile_funnel(output_dir, ["eog.calendar"], traces_path=replay)
    calendar = funnel["families"]["eog.calendar"]
    assert (
        calendar["passed_traces"] == 2
        and calendar["passed_traces_cited"] == 1
        and calendar["passed_traces_uncited"] == 1
    )


@pytest.mark.skipif(
    not (FABLE_RUN / "skills" / "snapshot.json").exists(), reason="The Fable snapshot is not on this checkout."
)
def test_funnel_on_the_fable_teacher_run() -> None:
    funnel = compile_funnel(FABLE_RUN / "skills", FAMILIES, traces_path=FABLE_RUN / "fable-traces.jsonl")
    total = funnel["total"]
    assert total["windows"] == 24 and total["window_uuids"] == 8
    assert total["proposals"] == 27 and total["add_proposals"] == 20 and total["rejected_adds"] == 3
    assert total["no_change"] == 3
    assert total["rejections"] == {"curator_rejected": 3, "scanner_rejected": 1, "other": 0}
    assert total["skills"] == 20
    assert total["passed_traces"] == 101 and total["passed_traces_cited"] == 70 and total["passed_traces_uncited"] == 31
    assert total["traces_cited"] == 109
    assert total["skills_with_tools"] == 0 and total["skills_with_date_literals"] == 0
    counts = json.loads((FABLE_RUN / "fable-compiles.counts.json").read_text())
    assert (total["windows"], total["window_uuids"], total["proposals"]) == (
        counts["compiles"],
        counts["compilation_windows"],
        counts["proposals"],
    )
    assert total["add_proposals"] == counts["accepted"]
    assert (total["skills"], total["rejections"]["curator_rejected"], total["rejections"]["scanner_rejected"]) == (
        counts["accepted"],
        counts["curator_rejected"],
        counts["scanner_rejected"],
    )


def test_compile_funnel_reads_skills_json_when_a_failed_compile_withheld_the_snapshot(tmp_path: Path) -> None:
    output_dir, traces_path = _fixture(tmp_path)
    snapshot = json.loads((output_dir / "snapshot.json").read_text())
    (output_dir / "snapshot.json").unlink()
    (output_dir / "skills.json").write_text(
        json.dumps({**snapshot, "compile_failures": [{"error": "compile_failed", "outcome": {"decision": "failed"}}]})
    )
    funnel = compile_funnel(output_dir, ["eog.calendar", "eog.csm"], traces_path=traces_path)
    assert funnel["total"]["skills"] == 2
    assert funnel["families"]["eog.calendar"]["passed_traces_cited"] == 1
