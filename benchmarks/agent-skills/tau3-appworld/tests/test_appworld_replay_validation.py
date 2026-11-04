from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import appworld_replay


@pytest.mark.parametrize(
    ("field", "value", "task_id"),
    [
        ("attempt", 2, "retry-task"),
        ("parent_trajectory_uuid", "parent-trace", "parent-task"),
        ("error", "Trace close failed", "error-task"),
    ],
)
def test_source_rows_with_retries_or_errors_fail_before_agent_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    field: str,
    value: object,
    task_id: str,
) -> None:
    results_root = tmp_path / "results"
    source_dir = results_root / "source-run"
    source_dir.mkdir(parents=True)
    row = {"task_id": task_id, field: value}
    (source_dir / "teacher.jsonl").write_text(
        json.dumps(row) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "appworld_replay.py",
            "--source-run-id",
            "source-run",
            "--run-id",
            "replay-run",
            "--results-root",
            str(results_root),
        ],
    )
    agent_creations = []
    monkeypatch.setattr(
        appworld_replay,
        "ensure_agent",
        lambda *args, **kwargs: agent_creations.append((args, kwargs)),
    )

    with pytest.raises(SystemExit) as exc:
        appworld_replay.main()

    assert exc.value.code == 2
    assert task_id in capsys.readouterr().err
    assert agent_creations == []
