import json

import pytest

from report import _print_combined, latency_by_outcome, main, retrieval_after_skill


def test_latencies_are_split_by_reward():
    rows = [
        {"reward": 1.0, "model_s": 10, "duration_s": 20},
        {"reward": 1.0, "model_s": 30, "duration_s": 60},
        {"reward": 0.0, "error": "TimeoutError", "model_s": 2, "duration_s": 3},
    ]
    stats = latency_by_outcome(rows)
    assert stats["passed"]["episode_count"] == 2
    assert stats["passed"]["model_s"] == {"count": 2, "p50": 20, "mean": 20, "p95": 29}
    assert stats["passed"]["duration_s"]["p95"] == 58
    assert stats["failed"]["model_s"] == {"count": 1, "p50": 2, "mean": 2, "p95": 2}


def _audit_row(tools):
    return {
        "tool_call_sequence": [
            {
                "tool": tool,
                "tool_call_index": index,
                "requestor": "assistant",
                **(
                    {"skill_read": {"name": "procedure", "found": True}}
                    if tool == "read_skill"
                    else {}
                ),
            }
            for index, tool in enumerate(tools, 1)
        ]
    }


def test_first_retrieval_counts_each_episode_once_after_the_first_read():
    stats = retrieval_after_skill(
        [
            _audit_row(
                [
                    "shell",
                    "read_skill",
                    "get_current_time",
                    "KB_search_dense",
                    "read_skill",
                    "shell",
                ]
            ),
            _audit_row(["read_skill", "shell", "KB_search_bm25"]),
            _audit_row(["read_skill", "get_current_time"]),
            _audit_row(["shell"]),
            {},
        ]
    )
    assert stats == {
        "audited_episodes": 4,
        "episodes_with_read": 3,
        "shell": 1,
        "kb_search": 1,
        "none": 1,
        "shell_share": 1 / 3,
        "kb_search_share": 1 / 3,
    }


@pytest.mark.parametrize("tools", [[], ["shell"]])
def test_no_read_has_no_share(tools):
    row = _audit_row(tools)
    stats = retrieval_after_skill([row])
    assert stats["shell_share"] is None
    assert stats["kb_search_share"] is None


@pytest.mark.parametrize("name,found", [("none", True), ("missing", False)])
def test_all_read_attempts_count_but_user_retrieval_is_not_counted(name, found):
    row = _audit_row(["read_skill", "shell"])
    row["tool_call_sequence"][0]["skill_read"] = {"name": name, "found": found}
    assert retrieval_after_skill([row])["episodes_with_read"] == 1
    assert retrieval_after_skill([row])["shell_share"] == 1
    row["tool_call_sequence"][1]["requestor"] = "user"
    assert retrieval_after_skill([row])["none"] == 1


def test_report_displays_retrieval_shares_per_arm(tmp_path, monkeypatch, capsys):
    for stage, arm, tools in (
        ("heldout-skills", "skills", ["read_skill", "shell"]),
        ("heldout-none", "none", ["KB_search_bm25"]),
    ):
        (tmp_path / f"{stage}.json").write_text(json.dumps({"stage": stage}))
        (tmp_path / f"{stage}.jsonl").write_text(
            json.dumps(
                {
                    **_audit_row(tools),
                    "domain": "banking_knowledge",
                    "task_id": "t",
                    "trial": 1,
                    "agent_model": "luna",
                    "arm": arm,
                    "reward": 1,
                }
            )
            + "\n"
        )
    monkeypatch.setattr("sys.argv", ["report.py", str(tmp_path)])
    main()
    report = capsys.readouterr().out
    assert "| luna | skills | 1 | 1 | 1 | 0 | 0 | 1.000 | 0.000 |" in report
    assert "| luna | none | 1 | 0 | 0 | 0 | 0 | N/A | N/A |" in report


@pytest.mark.parametrize("rows", [[], [{"reward": 0, "error": "TimeoutError", "model_s": None}]])
def test_missing_latencies_are_not_zero(rows):
    stats = latency_by_outcome(rows)
    for outcome in ("passed", "failed"):
        for field in ("model_s", "duration_s"):
            assert stats[outcome][field] == {"count": 0, "p50": None, "mean": None, "p95": None}


def test_report_displays_unavailable_reasoning_counts(tmp_path, monkeypatch, capsys):
    (tmp_path / "teacher.json").write_text(
        json.dumps(
            {
                "stage": "teacher",
                "domains": {"banking_knowledge": {"mean_reasoning_tokens": None}},
            }
        )
    )
    monkeypatch.setattr("sys.argv", ["report.py", str(tmp_path)])
    main()
    assert (
        "| teacher | banking_knowledge | 0 | 0.000 | 0.0 | 0.0 | 0.0 | N/A | 0 |"
        in capsys.readouterr().out
    )


@pytest.mark.parametrize("prefix", ["student", "heldout"])
def test_report_groups_latencies_by_model_and_arm(tmp_path, monkeypatch, capsys, prefix):
    for stage, model, arm, reward in (
        ("teacher", "opus", "none", 1),
        (f"{prefix}-{'plain' if prefix == 'student' else 'none'}", "luna", "none", 1),
        (f"{prefix}-skills", "luna", "skills", 0),
    ):
        (tmp_path / f"{stage}.json").write_text(
            json.dumps({"stage": stage, "conditions": {"agent_model": model, "arm": arm}})
        )
        (tmp_path / f"{stage}.jsonl").write_text(
            json.dumps(
                {
                    "domain": "banking_knowledge",
                    "task_id": "t",
                    "reward": reward,
                    "arm": arm,
                    "model_s": 2,
                    "duration_s": 3,
                }
            )
            + "\n"
        )
    monkeypatch.setattr("sys.argv", ["report.py", str(tmp_path)])
    main()
    output = capsys.readouterr().out
    assert (
        "| luna | none | passed | 1 | 1 | 2.000 | 2.000 | 2.000 | 1 | 3.000 | 3.000 | 3.000 |"
        in output
    )
    assert "| luna | skills | failed | 1 |" in output
    assert "| opus | none | failed | 0 | 0 | N/A | N/A | N/A | 0 | N/A | N/A | N/A |" in output


def test_combined_report_keeps_efforts_separate(tmp_path, monkeypatch, capsys):
    for effort, duration in (("low", 10), ("max", 40)):
        directory = tmp_path / effort
        directory.mkdir()
        (directory / "heldout-skills.json").write_text(
            json.dumps(
                {"conditions": {"agent_model": "luna", "reasoning_effort": effort, "arm": "skills"}}
            )
        )
        (directory / "heldout-skills.jsonl").write_text(
            json.dumps(
                {
                    **_audit_row(["read_skill", "shell"]),
                    "reward": 1,
                    "prompt_tokens": 100,
                    "skill_read_calls": [{"found": True}],
                    "duration_s": duration,
                    "model_s": duration - 1,
                }
            )
            + "\n"
        )
    monkeypatch.setattr("sys.argv", ["report.py", str(tmp_path)])
    main()
    output = capsys.readouterr().out
    assert "| luna low | skills | 1 | 1 | 0 | 100 | 1 | 10.000 |" in output
    assert "| luna max | skills | 1 | 1 | 0 | 100 | 1 | 40.000 |" in output
    assert "| luna low | skills | passed | 1 |" in output
    assert "| luna max | skills | passed | 1 |" in output
    assert "| luna low | skills | 1 | 1 | 1 | 0 | 0 | 1.000 | 0.000 |" in output


def test_combined_report_handles_empty_rows(tmp_path, capsys):
    (tmp_path / "heldout-none.jsonl").write_text("")
    _print_combined([tmp_path])
    assert "| unknown | heldout-none | 0 | 0 | 0 | 0 | 0 | N/A |" in capsys.readouterr().out


def test_combined_report_counts_errors_and_missing_timings(tmp_path, capsys):
    (tmp_path / "heldout-none.jsonl").write_text(
        json.dumps({"reward": 0, "recording_error": "failure", "duration_s": None}) + "\n"
    )
    _print_combined([tmp_path])
    output = capsys.readouterr().out
    assert "| unknown | heldout-none | 1 | 0 | 1 | 0 | 0 | N/A |" in output
    assert "| unknown | heldout-none | failed | 1 | 0 | N/A | N/A | N/A |" in output


def test_combined_report_counts_only_the_latest_attempt(tmp_path, capsys):
    rows = [
        {"domain": "banking", "task_index": 1, "trial": 1, "reward": 0, "error": "timeout"},
        {"domain": "banking", "task_index": 1, "trial": 1, "reward": 1.0, "duration_s": 2.0},
    ]
    (tmp_path / "heldout-none.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    _print_combined([tmp_path])
    assert "| unknown | heldout-none | 1 | 1 | 0 | 0 | 0 | 2.000 |" in capsys.readouterr().out
