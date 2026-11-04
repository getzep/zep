from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "matrix_report.py"
spec = importlib.util.spec_from_file_location("matrix_report", SCRIPT)
matrix_report = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(matrix_report)


def _write_run(
    matrix: Path,
    run_id: str,
    arm: str,
    rewards: list[int],
    label: str | None,
    sha256: str = "abc12345ff",
    commit: str = "c1",
    task_ids: list[str] | None = None,
) -> None:
    run = matrix / run_id
    run.mkdir(parents=True)
    rows = [
        {
            "model": "m",
            "effort": "low",
            "arm": arm,
            "task_id": task_ids[i] if task_ids else f"t{i}",
            "trial": 0,
            "status": "valid",
            "reward": r,
        }
        for i, r in enumerate(rewards)
    ]
    (run / "rows.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    manifest = {
        "skills_snapshot_sha256": sha256,
        "git_commit": commit,
        "cells": [{"model": "m", "effort": "low", "arm": arm}],
    }
    if label is not None:
        manifest["snapshot_label"] = label
    (run / "manifest.executor.json").write_text(json.dumps(manifest))
    (run / "costs.executor.m.low.jsonl").write_text(
        json.dumps({"model": "m", "request_effort": "low", "arm": arm, "usd": 0.5}) + "\n"
    )


def test_cell_label_separates_snapshots(tmp_path: Path) -> None:
    _write_run(tmp_path, "none-m-low", "none", [1, 0], None)
    _write_run(tmp_path, "arm-b-stopped-33-skills-m-low", "skills", [0, 0], "arm-b-stopped-33")
    _write_run(tmp_path, "arm-b-resumed-fixes-skills-m-low", "skills", [1, 1], None)
    _write_run(tmp_path, "other-skills-m-low", "skills", [0, 1], "arm-b-stopped-33", sha256="fedcba9876")
    _write_run(tmp_path, "smoke-c8-skills-m-low", "skills", [1, 1], None)

    assert matrix_report.cell_label(tmp_path, "none-m-low", "none") == "none"
    stopped = matrix_report.cell_label(tmp_path, "arm-b-stopped-33-skills-m-low", "skills")
    assert stopped == "skills@arm-b-stopped-33:abc12345"
    resumed = matrix_report.cell_label(tmp_path, "arm-b-resumed-fixes-skills-m-low", "skills")
    assert resumed == "skills@arm-b-resumed-fixes:abc12345"

    cells, runs = matrix_report.load_cells(tmp_path)
    assert sorted(cells) == [
        ("m", "low", "none"),
        ("m", "low", "skills@arm-b-resumed-fixes:abc12345"),
        ("m", "low", "skills@arm-b-stopped-33:abc12345"),
        ("m", "low", "skills@arm-b-stopped-33:fedcba98"),
    ]
    assert [r["reward"] for r in cells[("m", "low", "skills@arm-b-resumed-fixes:abc12345")]] == [1, 1]
    assert runs[("m", "low", "skills@arm-b-stopped-33:abc12345")] == {"arm-b-stopped-33-skills-m-low"}
    assert matrix_report.skills_cells(cells, "m", "low") == [
        "skills@arm-b-resumed-fixes:abc12345",
        "skills@arm-b-stopped-33:abc12345",
        "skills@arm-b-stopped-33:fedcba98",
    ]
    assert matrix_report.arm_of(("m", "low", "skills@arm-b-stopped-33:abc12345")) == "skills"
    assert matrix_report.cell_label(tmp_path, "unknown-skills-m-low", "skills") == "skills@unknown:nohash"

    usd, count = matrix_report.spend(tmp_path)
    assert usd[("m", "low", "skills@arm-b-resumed-fixes:abc12345")] == 0.5
    assert count[("m", "low", "skills@arm-b-stopped-33:abc12345")] == 1
    assert ("m", "low", "skills") not in usd


def test_load_cells_rejects_two_harness_commits_in_one_cell(tmp_path: Path) -> None:
    _write_run(tmp_path, "arm-b-skills-m-low", "skills", [1, 0], "arm-b", commit="c1")
    _write_run(tmp_path, "arm-b-part2-skills-m-low", "skills", [1, 1], "arm-b", commit="c1")
    _cells, runs = matrix_report.load_cells(tmp_path)
    assert runs[("m", "low", "skills@arm-b:abc12345")] == {"arm-b-skills-m-low", "arm-b-part2-skills-m-low"}
    _write_run(tmp_path, "arm-b-part3-skills-m-low", "skills", [0, 0], "arm-b", commit="c2")
    with pytest.raises(ValueError, match="more than one harness commit"):
        matrix_report.load_cells(tmp_path)


def test_report_excludes_dropped_run_rows_and_lists_dropped_arm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matrix = tmp_path / "matrix"
    snapshot_hash = "snapshot-1234"
    _write_run(
        matrix,
        "none-m-low",
        "none",
        [0, 1, 0, 1],
        None,
        sha256="baseline",
        task_ids=["t0", "t1", "t2", "t3"],
    )
    dropped_run = "arm-b-dropped-skills-m-low"
    _write_run(
        matrix,
        dropped_run,
        "skills",
        [1, 1],
        "snap",
        sha256=snapshot_hash,
        task_ids=["t2", "t3"],
    )
    live_run = "arm-b-live-skills-m-low"
    _write_run(
        matrix,
        live_run,
        "skills",
        [0, 0],
        "snap",
        sha256=snapshot_hash,
        task_ids=["t0", "t1"],
    )
    (matrix / dropped_run / "dropped_arms.jsonl").write_text(
        json.dumps(
            {
                "arm": "skills",
                "dropped_at": "2026-01-01T00:00:00+00:00",
                "reason": "The lead decided to drop this arm.",
                "rows_dropped": 2,
                "rows_kept_by_status": {"valid": 2},
            }
        )
        + "\n"
    )
    coverage_path = tmp_path / "coverage.json"
    coverage_path.write_text(
        json.dumps(
            {
                "skills_snapshot_sha256": snapshot_hash,
                "tasks": {task_id: {"covered": True, "covered_half": True} for task_id in ("t0", "t1", "t2", "t3")},
            }
        )
    )
    out_path = tmp_path / "report.md"
    monkeypatch.setattr(matrix_report, "family_of", lambda _row: "eog.test")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "matrix_report",
            "--matrix",
            str(matrix),
            "--out",
            str(out_path),
            "--coverage",
            str(coverage_path),
            "--no-langfuse",
        ],
    )

    matrix_report.main()

    report = out_path.read_text()
    report_data = json.loads(out_path.with_suffix(".json").read_text())
    cell_key = "m/low/skills@snap:snapshot"
    difference_key = f"{cell_key}-minus-none"
    assert report.count("## Dropped arms") == 1
    assert "| m | low | skills | 2 | The lead decided to drop this arm. |" in report
    assert report_data["cells"][cell_key]["rows"] == 2
    assert report_data["cells"][cell_key]["runs"] == [live_run]
    assert report_data["cells"]["m/low/none"]["rows"] == 4
    assert report_data["differences"][difference_key]["pairs"] == 2
    assert report_data["differences"][difference_key]["by_coverage"]["covered"]["true"]["pairs"] == 2
    assert report_data["cells"][cell_key]["usd"] == 0.5
