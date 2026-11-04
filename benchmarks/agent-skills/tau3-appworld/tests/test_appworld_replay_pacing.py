from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from appworld_replay import (
    _CaptureClient,
    _planned_time_summary,
    _run_replay,
    _source_time_offsets,
    build_replay_episode,
    latest_rows,
)

FIXTURE = Path(__file__).parent / "data" / "appworld_replay_episode.json"


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _timed_episodes(
    starts: list[int],
    finishes: list[int],
) -> list:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    anchor = datetime(2025, 1, 1, tzinfo=UTC)
    rows = [
        {
            "task_id": f"scenario_demo_{index + 2}",
            "task_index": index,
            "trial": 1,
            "started_at": (anchor + timedelta(seconds=started)).isoformat(),
            "finished_at": (anchor + timedelta(seconds=finished)).isoformat(),
            "succeeded": True,
        }
        for index, (started, finished) in enumerate(zip(starts, finishes, strict=True))
    ]
    time_offsets = _source_time_offsets(rows)
    return [
        build_replay_episode(
            row,
            fixture["messages"],
            planned_open_offset_seconds=time_offsets[(row["task_id"], row["trial"])][0],
            planned_close_offset_seconds=time_offsets[(row["task_id"], row["trial"])][1],
        )
        for row in latest_rows(rows)
    ]


def test_original_pace_closes_traces_at_planned_offsets() -> None:
    episodes = _timed_episodes([0, 3, 7], [0, 5, 9])
    clock = _FakeClock()

    results = _run_replay(
        _CaptureClient(),
        episodes,
        agent_uuid="replay-agent",
        source_run_id="source-run",
        run_id="replay-run",
        concurrency=1,
        pace="original",
        clock=clock.monotonic,
        sleep_fn=clock.sleep,
    )

    assert [
        (
            result["planned_close_offset_seconds"],
            result["actual_close_offset_seconds"],
        )
        for result in results
    ] == [(0.0, 0.0), (5.0, 5.0), (9.0, 9.0)]


def test_planned_time_summary_counts_overlapping_families() -> None:
    summary = _planned_time_summary(_timed_episodes([0, 3, 8], [5, 7, 9]))

    assert summary == {
        "planned_first_open_offset_seconds": 0.0,
        "planned_last_close_offset_seconds": 9.0,
        "planned_offset_span_seconds": 9.0,
        "families_with_overlapping_traces": 1,
        "overlapping_task_families": ["tool.appworld.sscenario"],
    }


def test_source_time_offsets_require_finished_at() -> None:
    with pytest.raises(ValueError, match="no finished_at timestamp"):
        _source_time_offsets(
            [
                {
                    "task_id": "scenario_demo_2",
                    "trial": 1,
                    "started_at": "2025-01-01T00:00:00Z",
                }
            ]
        )
