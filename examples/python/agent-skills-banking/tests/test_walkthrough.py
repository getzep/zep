import json
from contextlib import nullcontext
from decimal import Decimal

import pytest

from banking import expected_database, make_case
from walkthrough import check_leakage, installed_versions, money_patterns, register_attempt


def test_leakage_catches_currency_formats_and_new_report_fields():
    case = make_case(54)
    report = expected_database(case).reports[0]
    for text in (
        case.case_id,
        f"${case.savings[0].statement_balance:,.2f}",
        f"{report.expected_interest:.2f}",
        f"{report.net_interest_before_run:.2f}",
        f"The expected interest is {report.expected_interest:.2f}.",
    ):
        result = check_leakage([{"markdown": text}], [54])
        assert result["exact_heldout_identifiers_balances_or_corrections"]
    assert not check_leakage([{"markdown": "Apply the two caps before choosing a package."}], [54])[
        "exact_heldout_identifiers_balances_or_corrections"
    ]
    import re

    assert any(re.search(pattern, "$11,631.60") for pattern in money_patterns(Decimal("11631.6")))
    assert any(re.search(pattern, "$11,631.60.") for pattern in money_patterns(Decimal("11631.6")))


def test_verification_attempts_are_append_only_and_reject_overlap(tmp_path):
    register_attempt(tmp_path, tmp_path / "first", [10, 11], "first-hash")
    with pytest.raises(ValueError, match="overlap"):
        register_attempt(tmp_path, tmp_path / "second", [11, 12], "second-hash")
    register_attempt(tmp_path, tmp_path / "third", [12, 13], "third-hash")
    rows = [
        json.loads(line)
        for line in (tmp_path / "verification-attempts.jsonl").read_text().splitlines()
    ]
    assert len(rows) == 2
    assert rows[0]["seeds"] == [10, 11]


def test_installed_dependency_versions_are_recorded():
    assert set(installed_versions()) == {
        "pydantic",
        "pydantic-ai-slim",
        "anthropic",
        "openai",
        "httpx",
        "zep-cloud",
    }
    assert all(installed_versions().values())


@pytest.mark.parametrize(
    ("options", "expected"),
    [([], "claude-haiku-5-5"), (["--student", "anthropic:custom-model"], "anthropic:custom-model")],
)
def test_walkthrough_cli_selects_the_student_model(tmp_path, monkeypatch, options, expected):
    import walkthrough

    selected = []

    async def fake_run_demo(args):
        selected.append(args.student)

    monkeypatch.setattr(walkthrough, "run_demo", fake_run_demo)
    monkeypatch.setattr("sys.argv", ["walkthrough.py", "run", "--output", str(tmp_path), *options])
    walkthrough.main()
    assert selected == [expected]


def test_walkthrough_defaults_use_different_optimal_allocations(tmp_path, monkeypatch):
    import walkthrough

    selected = []

    async def fake_run_demo(args):
        selected.append((args.seed, args.teacher_seed))

    monkeypatch.setattr(walkthrough, "run_demo", fake_run_demo)
    monkeypatch.setattr("sys.argv", ["walkthrough.py", "run", "--output", str(tmp_path)])
    walkthrough.main()
    assert selected == [(10134, 22)]
    assert walkthrough.allocation_signature(22) != walkthrough.allocation_signature(10134)


@pytest.mark.parametrize("teacher_effort", ["none", "max"])
def test_run_uses_and_records_the_selected_teacher_effort(tmp_path, monkeypatch, teacher_effort):
    import argparse
    import asyncio

    import walkthrough

    calls = []

    async def fake_stage(output, name, seed, model, effort, source, skills=None, teaching=False):
        calls.append((name, model, effort, teaching))
        return {"passed": False, "termination": "final_answer"}

    monkeypatch.setattr(walkthrough, "stage", fake_stage)
    monkeypatch.setattr(walkthrough, "Hosted", lambda path: nullcontext(None))
    args = argparse.Namespace(
        output=str(tmp_path / "run"),
        resume=False,
        student="claude-haiku-5-5",
        effort="none",
        teacher="anthropic:custom-teacher",
        teacher_effort=teacher_effort,
        seed=404,
        teacher_seed=22,
    )
    with pytest.raises(RuntimeError, match="teacher did not pass"):
        asyncio.run(walkthrough.run_demo(args))
    assert calls == [
        ("without-skill", args.student, "none", False),
        ("teacher", args.teacher, teacher_effort, True),
    ]
    config = json.loads((tmp_path / "run" / "config.json").read_text())
    assert config["teacher_effort"] == teacher_effort


def test_repeated_indices_are_reserved_before_calls_and_cannot_be_reused(tmp_path):
    register_attempt(tmp_path, tmp_path / "first", [3, 3], "first", [0, 1])
    with pytest.raises(ValueError, match="indices overlap"):
        register_attempt(tmp_path, tmp_path / "second", [3, 3], "second", [1, 2])
    register_attempt(tmp_path, tmp_path / "third", [3, 3], "third", [2, 3])
    rows = [json.loads(s) for s in (tmp_path / "repeated-attempts.jsonl").read_text().splitlines()]
    assert [r["run_indices"] for r in rows] == [[0, 1], [2, 3]]


def test_repeat_mode_makes_six_calls_for_three_pairs_instead_of_reusing_results(
    tmp_path, monkeypatch
):
    import argparse
    import asyncio

    import walkthrough
    from runner import source_hashes, write_json

    learned = tmp_path / "learned"
    learned.mkdir()
    write_json(
        learned / "config.json",
        {
            "source": source_hashes(),
            "installed_versions": installed_versions(),
            "student": "test-model",
            "effort": "none",
            "seed": 3,
            "teacher_seed": 22,
            "screening_seeds": [0, 1, 2, 3],
        },
    )
    write_json(learned / "agent.json", {"uuid": "example"})
    write_json(learned / "skill-pin.json", [])
    skills = [{"markdown": "An invented package comparison procedure."}]
    calls = []

    async def fake_episode(path, case, model, effort, skills=None, teaching=False):
        path.mkdir()
        calls.append(path.name)
        row = {"passed": bool(skills)}
        write_json(path / "tool-calls.json", [{"invocation": len(calls)}])
        write_json(path / "database.json", {"passed": bool(skills)})
        write_json(path / "result.json", row)
        return row

    monkeypatch.setattr(walkthrough, "episode", fake_episode)
    monkeypatch.setattr(walkthrough, "Hosted", lambda path: nullcontext(None))
    monkeypatch.setattr(walkthrough, "live_skills", lambda *args: skills)
    args = argparse.Namespace(
        learned_run=str(learned),
        output=str(tmp_path / "check"),
        count=3,
        repeat_case=True,
        first_run=0,
        first_seed=10000,
    )
    asyncio.run(walkthrough.verify(args))
    assert len(calls) == len(set(calls)) == 6
    assert calls[0:2] == ["without-skill-seed3-run0", "with-skill-seed3-run0"]
    assert calls[2:4] == ["with-skill-seed3-run1", "without-skill-seed3-run1"]
    summary = json.loads((tmp_path / "check" / "summary.json").read_text())
    assert summary["accepted"] and summary["distinct_cases"] == 1
    assert summary["distinct_tool_sequences"] == {"with-skill": 3, "without-skill": 3}
    assert summary["distinct_final_databases"] == {"with-skill": 1, "without-skill": 1}
    args.output = str(tmp_path / "duplicate")
    with pytest.raises(ValueError, match="indices overlap"):
        asyncio.run(walkthrough.verify(args))
    assert len(calls) == 6


@pytest.mark.parametrize("stop_after_skill", [False, True])
def test_walkthrough_uses_sdk_for_the_learning_loop_and_resume(
    tmp_path, monkeypatch, stop_after_skill
):
    import argparse
    import asyncio

    import httpx

    import walkthrough
    from hosted import Hosted
    from runner import write_json

    monkeypatch.setenv("ZEP_API_KEY", "local-test-value")
    monkeypatch.delenv("ZEP_BASE_URL", raising=False)
    monkeypatch.setattr("hosted.time.sleep", lambda _: None)
    requests = []
    clients = []
    episodes = []

    def handler(request):
        requests.append((request.method, request.url.path))
        assert request.headers["Authorization"] == "Api-Key local-test-value"
        path = request.url.path
        if path == "/api/v4/agents":
            return httpx.Response(201, json={"uuid": "agent-uuid"})
        if path.endswith("/traces"):
            return httpx.Response(201, json={"uuid": "trace-uuid"})
        if path.endswith("/events"):
            return httpx.Response(201, json={"uuid": "event-uuid"})
        if path.endswith("/close"):
            return httpx.Response(202, json={"task": {"uuid": "close-task"}})
        if path == "/api/v4/tasks/close-task":
            return httpx.Response(200, json={"status": "succeeded"})
        if path.endswith("/traces/trace-uuid"):
            return httpx.Response(200, json={"status": "closed", "verification": "external"})
        if path.endswith("/learning"):
            return httpx.Response(200, json={"compilation_pending": False})
        if path.endswith("/learning-runs"):
            return httpx.Response(200, json={"items": [{"decision": "produced"}]})
        assert path.endswith("/skills/search")
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "uuid": "skill-uuid",
                        "version": 1,
                        "markdown": "Compare every permitted package.",
                    }
                ]
            },
        )

    def hosted_factory(ledger):
        hosted = Hosted(ledger, httpx.MockTransport(handler))
        clients.append(hosted)
        return hosted

    async def fake_episode(path, case, model, effort, skills=None, teaching=False):
        path.mkdir()
        episodes.append((path.name, model))
        row = {
            "passed": bool(skills) or teaching,
            "termination": "final_answer",
            "finish_reason": "stop",
            "tokens": 1,
            "seconds": 0.1,
            "failed_provider_calls": 0,
        }
        write_json(path / "result.json", row)
        return row

    monkeypatch.setattr(walkthrough, "Hosted", hosted_factory)
    monkeypatch.setattr(walkthrough, "episode", fake_episode)
    monkeypatch.setattr(
        walkthrough,
        "trace_events",
        lambda _: [{"event_type": "decision", "content": "Compare every permitted package."}],
    )
    args = argparse.Namespace(
        output=str(tmp_path / "run"),
        resume=False,
        student="claude-haiku-5-5",
        teacher="claude-opus-5-5",
        effort="none",
        teacher_effort="max",
        seed=404,
        teacher_seed=22,
        compile_timeout=1,
        stop_after_skill=stop_after_skill,
    )
    asyncio.run(walkthrough.run_demo(args))
    expected_episodes = [
        ("without-skill", "claude-haiku-5-5"),
        ("teacher", "claude-opus-5-5"),
        ("with-skill", "claude-haiku-5-5"),
    ]
    if stop_after_skill:
        assert episodes == expected_episodes[:2]
        assert not (tmp_path / "run" / "summary.json").exists()
        args.resume = True
        args.stop_after_skill = False
        asyncio.run(walkthrough.run_demo(args))
    assert episodes == expected_episodes
    output = tmp_path / "run"
    assert json.loads((output / "upload.json").read_text())["final_trace"]["status"] == "closed"
    assert json.loads((output / "skill-pin.json").read_text())[0]["uuid"] == "skill-uuid"
    assert (output / "skill-skill-uuid-v1.md").read_text() == "Compare every permitted package."
    assert json.loads((output / "summary.json").read_text())["with_skill"]["passed"]
    before_resume = len(requests)
    args.resume = True
    asyncio.run(walkthrough.run_demo(args))
    assert episodes == expected_episodes
    assert requests[before_resume:] == [("POST", "/api/v4/agents/agent-uuid/skills/search")]
    assert all(client.http_client.is_closed for client in clients)
