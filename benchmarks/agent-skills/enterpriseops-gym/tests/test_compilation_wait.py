"""Tests for per-family compilation pending and quiet-time waits."""

import json
from pathlib import Path
from types import SimpleNamespace

from eog import skills
from eog.run import _closed_trace_counts, _compile_failure_message


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class _Pager:
    def __init__(self, outcomes: list[dict]) -> None:
        self.outcomes = outcomes

    def __iter__(self):
        for outcome in self.outcomes:
            yield SimpleNamespace(model_dump=lambda outcome=outcome, **_: outcome)

    def __len__(self) -> int:
        raise AssertionError("The SDK pager must be iterated, not counted.")


class _Learning:
    def __init__(self, outcomes: dict[str, list[list[dict]]], pending: dict[str, list[object]]) -> None:
        self.outcomes = outcomes
        self.pending = pending
        self.run_calls: dict[str, int] = {}
        self.get_calls: dict[str, int] = {}
        self.run_arguments: list[tuple[str, int]] = []

    def list_runs(self, agent_uuid: str, *, task_family: str, page_size: int) -> _Pager:
        index = self.run_calls.get(task_family, 0)
        self.run_calls[task_family] = index + 1
        self.run_arguments.append((task_family, page_size))
        responses = self.outcomes.get(task_family, [[]])
        return _Pager(responses[min(index, len(responses) - 1)])

    def get(self, agent_uuid: str, *, task_family: str) -> SimpleNamespace:
        index = self.get_calls.get(task_family, 0)
        self.get_calls[task_family] = index + 1
        responses = self.pending.get(task_family, [False])
        pending = responses[min(index, len(responses) - 1)]
        return SimpleNamespace(model_dump=lambda **_: {"compilation_pending": pending})


def _client(
    outcomes: dict[str, list[list[dict]]],
    pending: dict[str, list[object]],
    *,
    skill_list=None,
) -> SimpleNamespace:
    learning = _Learning(outcomes, pending)
    return SimpleNamespace(
        agent=SimpleNamespace(
            learning=learning,
            skill=SimpleNamespace(list=skill_list or (lambda *args, **kwargs: [])),
        )
    )


def _wait(client, closed_traces: dict[str, int], clock: _Clock, **kwargs):
    return skills.wait_for_compilation(
        client,
        "agent",
        closed_traces,
        clock=clock.monotonic,
        sleep=clock.sleep,
        **kwargs,
    )


def _outcome(uuid: str, decision: str, trace_count: int | None, attempts: int = 1) -> dict:
    return {
        "uuid": uuid,
        "decision": decision,
        "attempts": attempts,
        "trace_count": trace_count,
    }


def test_wait_finishes_with_low_reported_trace_count_after_twenty_quiet_minutes() -> None:
    clock = _Clock()
    client = _client(
        {"eog.hr": [[_outcome("run-1", "produced", 1)]]},
        {"eog.hr": [False]},
    )

    result = _wait(
        client,
        {"eog.hr": 2},
        clock,
        ceiling_seconds=1200,
        quiet_seconds=1200,
        poll_interval=600,
    )

    assert result["timed_out"] is False
    assert result["seconds"] == 1200
    assert result["polls"] == 3
    assert result["families"]["eog.hr"] == {
        "closed": 2,
        "preexisting_outcomes": 0,
        "reported_trace_count": 1,
        "failed_outcomes": 0,
        "pending": False,
        "minutes_since_change": 20.0,
        "done": True,
    }
    assert client.agent.learning.run_arguments == [("eog.hr", skills.LEARNING_RUNS_LIST_PAGE_SIZE)] * 3


def test_list_learning_run_ids_uses_run_uuid_fallback_and_learning_pager() -> None:
    client = _client(
        {"eog.hr": [[_outcome("run-1", "produced", 1), {"run_uuid": "run-2"}]]},
        {},
    )

    assert skills.list_learning_run_ids(client, "agent", ["eog.hr"]) == {"eog.hr": {"run-1", "run-2"}}
    assert client.agent.learning.run_arguments == [("eog.hr", skills.LEARNING_RUNS_LIST_PAGE_SIZE)]


def test_preexisting_terminal_outcome_does_not_finish_wait() -> None:
    clock = _Clock()
    old_outcome = _outcome("old-run", "produced", 5)
    client = _client({"eog.hr": [[old_outcome]]}, {"eog.hr": [False]})

    result = _wait(
        client,
        {"eog.hr": 1},
        clock,
        ceiling_seconds=10,
        quiet_seconds=0,
        poll_interval=5,
        preexisting_run_ids={"eog.hr": {"old-run"}},
    )

    assert result["timed_out"] is True
    assert result["seconds"] == 10
    assert result["families"]["eog.hr"]["preexisting_outcomes"] == 1
    assert result["families"]["eog.hr"]["reported_trace_count"] == 0
    assert result["families"]["eog.hr"]["failed_outcomes"] == 0
    assert result["families"]["eog.hr"]["done"] is False


def test_new_terminal_outcome_finishes_after_quiet_window_with_old_outcome_present() -> None:
    clock = _Clock()
    old_outcome = _outcome("old-run", "produced", 5)
    new_outcome = _outcome("new-run", "produced", 1)
    client = _client(
        {"eog.hr": [[old_outcome], [old_outcome], [old_outcome, new_outcome]]},
        {"eog.hr": [False]},
    )

    result = _wait(
        client,
        {"eog.hr": 1},
        clock,
        ceiling_seconds=30,
        quiet_seconds=10,
        poll_interval=5,
        preexisting_run_ids={"eog.hr": {"old-run"}},
    )

    assert result["timed_out"] is False
    assert result["seconds"] == 20
    assert result["families"]["eog.hr"]["preexisting_outcomes"] == 1
    assert result["families"]["eog.hr"]["reported_trace_count"] == 1
    assert result["families"]["eog.hr"]["done"] is True


def test_outcome_change_within_quiet_window_prevents_completion() -> None:
    clock = _Clock()
    first = _outcome("run-1", "produced", 1)
    second = _outcome("run-2", "no_change", 1)
    client = _client(
        {"eog.hr": [[first], [first, second]]},
        {"eog.hr": [False]},
    )

    result = _wait(
        client,
        {"eog.hr": 2},
        clock,
        ceiling_seconds=1200,
        quiet_seconds=1200,
        poll_interval=600,
    )

    assert result["timed_out"] is True
    assert result["seconds"] == 1200
    assert result["polls"] == 3
    assert result["families"]["eog.hr"]["minutes_since_change"] == 10.0
    assert result["families"]["eog.hr"]["done"] is False


def test_pending_compilation_times_out_and_blocks_snapshot_export(tmp_path: Path) -> None:
    clock = _Clock()
    outcome = _outcome("run-1", "produced", 2)

    def fail_skill_export(*args, **kwargs):
        raise AssertionError("A timed-out family must not export Skills.")

    client = _client(
        {"eog.hr": [[outcome]]},
        {"eog.hr": [True]},
        skill_list=fail_skill_export,
    )
    wait = _wait(
        client,
        {"eog.hr": 3},
        clock,
        ceiling_seconds=60,
        quiet_seconds=1200,
        poll_interval=30,
    )
    assert wait["timed_out"] is True
    assert wait["families"]["eog.hr"]["pending"] is True
    assert wait["families"]["eog.hr"]["done"] is False

    output_dir = tmp_path / "compiled"
    skills.compile_snapshot(client, "agent", ["eog.hr"], {"tasks": {}}, output_dir, wait=wait)

    report = json.loads((output_dir / "eog_hr" / "compile_report.json").read_text())
    assert report["reported_trace_count"] == 2
    assert report["failed_outcomes"] == 0
    assert report["failures"] == [
        {
            "error": "compile_wait_timeout",
            "closed_traces": 3,
            "reported_trace_count": 2,
            "failed_outcomes": 0,
        }
    ]
    assert not (output_dir / "snapshot.json").exists()
    assert "compile_wait_timeout" in _compile_failure_message(output_dir, ["eog.hr"])


def test_pending_compilation_stays_incomplete_until_the_ceiling() -> None:
    clock = _Clock()
    client = _client(
        {"eog.hr": [[_outcome("run-1", "produced", 1)]]},
        {"eog.hr": [True]},
    )

    result = _wait(
        client,
        {"eog.hr": 1},
        clock,
        ceiling_seconds=10,
        quiet_seconds=0,
        poll_interval=5,
    )

    assert result["timed_out"] is True
    assert result["polls"] == 3
    assert result["families"]["eog.hr"]["pending"] is True
    assert result["families"]["eog.hr"]["done"] is False
    assert client.agent.learning.get_calls["eog.hr"] == 3


def test_zero_closed_traces_are_done_without_api_calls() -> None:
    clock = _Clock()
    client = _client({}, {})

    result = _wait(client, {"eog.hr": 0}, clock)

    assert result == {
        "seconds": 0.0,
        "polls": 0,
        "timed_out": False,
        "families": {
            "eog.hr": {
                "closed": 0,
                "preexisting_outcomes": 0,
                "reported_trace_count": 0,
                "failed_outcomes": 0,
                "pending": False,
                "minutes_since_change": 0.0,
                "done": True,
            }
        },
    }
    assert client.agent.learning.run_calls == {}
    assert client.agent.learning.get_calls == {}


def test_nonterminal_outcome_does_not_complete_family_or_add_reported_trace_count() -> None:
    clock = _Clock()
    client = _client(
        {"eog.hr": [[_outcome("run-1", "accepted", 10)]]},
        {"eog.hr": [False]},
    )

    result = _wait(
        client,
        {"eog.hr": 1},
        clock,
        ceiling_seconds=0,
        quiet_seconds=0,
    )

    assert result["timed_out"] is True
    assert result["families"]["eog.hr"]["reported_trace_count"] == 0
    assert result["families"]["eog.hr"]["done"] is False


def test_closed_traces_without_outcomes_time_out_after_quiet_period() -> None:
    clock = _Clock()
    client = _client({"eog.hr": [[]]}, {"eog.hr": [False]})

    result = _wait(
        client,
        {"eog.hr": 1},
        clock,
        ceiling_seconds=10,
        quiet_seconds=5,
        poll_interval=5,
    )

    assert result["timed_out"] is True
    assert result["seconds"] == 10
    assert result["polls"] == 3
    assert result["families"]["eog.hr"]["pending"] is False
    assert result["families"]["eog.hr"]["reported_trace_count"] == 0
    assert result["families"]["eog.hr"]["done"] is False


def test_failed_outcomes_are_counted_in_the_wait_state_and_report(tmp_path: Path) -> None:
    clock = _Clock()
    outcomes = [
        _outcome("run-1", "produced", 1),
        _outcome("run-2", "failed", 0),
    ]
    client = _client({"eog.hr": [outcomes]}, {"eog.hr": [False]})

    wait = _wait(
        client,
        {"eog.hr": 2},
        clock,
        ceiling_seconds=10,
        quiet_seconds=0,
    )

    assert wait["timed_out"] is False
    assert wait["families"]["eog.hr"]["failed_outcomes"] == 1
    assert wait["families"]["eog.hr"]["done"] is True

    output_dir = tmp_path / "compiled"
    skills.compile_snapshot(client, "agent", ["eog.hr"], {"tasks": {}}, output_dir, wait=wait)

    report = json.loads((output_dir / "eog_hr" / "compile_report.json").read_text())
    assert report["failed_outcomes"] == 1
    assert report["reported_trace_count"] == 1


def test_wait_logs_one_line_for_each_incomplete_family(capsys) -> None:
    clock = _Clock()
    client = _client(
        {"eog.csm": [[_outcome("run-1", "produced", 12)]]},
        {"eog.csm": [False]},
    )

    _wait(
        client,
        {"eog.csm": 14},
        clock,
        ceiling_seconds=0,
        quiet_seconds=1200,
    )

    assert capsys.readouterr().err == (
        "compile wait: family=eog.csm reported_trace_count=12 closed=14 pending=false minutes_since_change=0.0\n"
    )


def test_closed_trace_counts_skip_infrastructure_failures_and_abandoned_traces(tmp_path: Path) -> None:
    traces_path = tmp_path / "traces.jsonl"
    traces_path.write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                {"task_family": "eog.hr", "passed": True, "infra_failed": False},
                {"task_family": "eog.hr", "passed": True, "infra_failed": True},
                {"task_family": "eog.hr", "passed": True, "close_action": "abandon"},
                {"task_family": "eog.csm", "passed": True, "close_action": "close"},
                {"task_family": "eog.other", "passed": True, "infra_failed": False},
            )
        )
        + "\n"
    )

    assert _closed_trace_counts(traces_path, ["eog.hr", "eog.csm", "eog.calendar"]) == {
        "eog.hr": 1,
        "eog.csm": 1,
        "eog.calendar": 0,
    }


def test_closed_trace_counts_skip_failed_traces(tmp_path: Path) -> None:
    traces_path = tmp_path / "traces.jsonl"
    traces_path.write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                {"task_family": "eog.hr", "passed": True},
                {"task_family": "eog.hr", "passed": True},
                {"task_family": "eog.hr", "passed": True},
                {"task_family": "eog.hr", "passed": False},
                {"task_family": "eog.hr", "passed": False},
                {"task_family": "eog.hr"},
            )
        )
        + "\n"
    )

    assert _closed_trace_counts(traces_path, ["eog.hr"]) == {"eog.hr": 3}
