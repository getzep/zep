import json
from pathlib import Path
from types import SimpleNamespace

from zep_cloud.core.pagination import SyncPager

from appworld_run import (
    AppWorldResult,
    _instructions,
    next_teacher_attempt,
    resumed_teacher_attempts,
    select_tasks,
    startup_jobs,
    summarize,
    task_family_for,
)
from readtime import BM25, RawCorpus, ReadTimeCurator, format_events, format_messages
from redaction import Redactor


class Hit:
    def __init__(self, **data: object) -> None:
        self.data = data

    def model_dump(self, **_: object) -> dict:
        return dict(self.data)


class Responses:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return SimpleNamespace(
            output=[],
            output_text="1. Similar cases: case 1.",
            usage=SimpleNamespace(input_tokens=100, output_tokens=20),
        )


MESSAGES = [
    {"role": "user", "content": "I want to cancel my reservation"},
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": "c1", "name": "get_reservation", "arguments": {"id": "<R>"}}],
    },
    {"role": "tool", "id": "c1", "content": "status: booked"},
    {"role": "assistant", "content": "Your reservation is cancelled."},
]


def _write_teacher(root: Path) -> None:
    run = root / "teacher-run"
    (run / "simulations").mkdir(parents=True)
    rows = [
        {"domain": "airline", "task_index": 0, "trial": 1, "succeeded": True},
        {"domain": "airline", "task_index": 1, "trial": 1, "succeeded": False},
        {"domain": "retail", "task_index": 0, "trial": 1, "succeeded": True},
    ]
    (run / "teacher.jsonl").write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    for row in rows:
        name = f"teacher-{row['domain']}-{row['task_index']}-{row['trial']}.json"
        (run / "simulations" / name).write_text(json.dumps({"messages": MESSAGES}))


def test_bm25_ranks_matching_documents_first() -> None:
    index = BM25(["cancel a flight", "exchange an item", "cancel the order now"])
    assert index.top("cancel flight", 2) == [0, 2]
    assert index.top("unrelated words", 3) == []


def test_format_messages_names_tool_results() -> None:
    text = format_messages(MESSAGES)
    assert "User: I want to cancel my reservation" in text
    assert 'Agent calls get_reservation({"id": "<R>"})' in text
    assert "Result of get_reservation: status: booked" in text


def test_format_messages_names_user_device_results() -> None:
    text = format_messages(
        [
            {"role": "user", "tool_calls": [{"id": "u1", "name": "check_status_bar"}]},
            {"role": "tool", "id": "u1", "content": "no signal"},
        ]
    )
    assert "User runs check_status_bar on the user device" in text
    assert "Result of user device check_status_bar: no signal" in text


def test_format_events_orders_by_sequence() -> None:
    events = [
        SimpleNamespace(sequence=2, event_type="decision", content="second"),
        SimpleNamespace(sequence=1, event_type="observation", content="first"),
    ]
    assert format_events(events).splitlines() == ["[observation] first", "[decision] second"]


def test_raw_corpus_loads_only_successful_rows_for_the_domain(tmp_path: Path) -> None:
    _write_teacher(tmp_path)
    corpus = RawCorpus.load(tmp_path, "teacher-run", "airline")
    assert corpus.keys == ["airline:0:1"]
    assert corpus.retrieve("cancel reservation")[0][0] == "airline:0:1"


def test_raw_curator_sends_only_the_briefing_inputs(tmp_path: Path) -> None:
    _write_teacher(tmp_path)
    responses = Responses()
    curator = ReadTimeCurator(
        arm="r-raw",
        domain="airline",
        openai_client=SimpleNamespace(responses=responses),
        model="curator-model",
        reasoning_effort="medium",
        corpus=RawCorpus.load(tmp_path, "teacher-run", "airline"),
    )
    briefing = curator.brief("cancel my reservation")
    assert briefing == "1. Similar cases: case 1."
    assert curator.record["source_keys"] == ["airline:0:1"]
    assert curator.record["curator_input_tokens"] == 100
    prompt = responses.calls[0]["input"][0]["content"]
    assert "Earlier successful run 1:" in prompt
    assert "Admitted Skill" not in prompt
    assert responses.calls[0]["store"] is False


def test_raw_curator_skips_the_call_without_sources(tmp_path: Path) -> None:
    _write_teacher(tmp_path)
    responses = Responses()
    curator = ReadTimeCurator(
        arm="r-raw",
        domain="airline",
        openai_client=SimpleNamespace(responses=responses),
        model="curator-model",
        reasoning_effort="medium",
        corpus=RawCorpus.load(tmp_path, "teacher-run", "airline"),
    )
    assert curator.brief("zzz") == ""
    assert responses.calls == []


def test_skill_curator_reads_admitted_evidence_trajectories() -> None:
    responses = Responses()
    skill = SyncPager(
        has_next=False,
        items=[Hit(uuid="s1", name="Cancel", markdown="# Cancel\nCheck status.")],
        get_next=None,
        response=SimpleNamespace(search_id="q1"),
    )
    evidence = SimpleNamespace(items=[SimpleNamespace(trace_uuid="t1")])
    events = [SimpleNamespace(sequence=1, event_type="observation", content="user: cancel")]
    evidence_calls: list[dict] = []
    event_calls: list[dict] = []

    def list_evidence(*_args: object, **kwargs: object) -> SimpleNamespace:
        evidence_calls.append(kwargs)
        return evidence

    def list_events(*_args: object, **kwargs: object) -> list[SimpleNamespace]:
        event_calls.append(kwargs)
        return events

    zep = SimpleNamespace(
        agent=SimpleNamespace(
            skill=SimpleNamespace(
                search=lambda *args, **kwargs: skill,
                evidence=SimpleNamespace(list=list_evidence),
            ),
            trace=SimpleNamespace(list_events=list_events),
        )
    )
    curator = ReadTimeCurator(
        arm="r-skill",
        domain="airline",
        openai_client=SimpleNamespace(responses=responses),
        model="curator-model",
        reasoning_effort="medium",
        client=zep,
        memory_agent_uuid="agent",
        task_family="tool.airline",
    )
    curator.brief("cancel")
    assert evidence_calls == [{"page_size": 20}]
    assert event_calls == [{"page_size": 100}]
    assert curator.record["source_keys"] == ["t1"]
    assert curator.record["skill_uuids"] == ["s1"]
    prompt = responses.calls[0]["input"][0]["content"]
    assert "Admitted Skill:\n# Cancel" in prompt
    assert "[observation] user: cancel" in prompt


def test_redactor_from_values_replaces_supervisor_values() -> None:
    redactor = Redactor.from_values({"first_name": "Glenn", "email": "glenn@example.com"})
    assert redactor.redact("Glenn uses glenn@example.com") == "<FIRST_NAME> uses <EMAIL>"


def test_appworld_instructions_add_skill_catalog_and_briefing() -> None:
    text = _instructions("## Available skills\n- skill_id: skill-1", "Use the plan.")
    assert "## Available skills" in text and "skill-1" in text
    assert "## Task briefing" in text and "Use the plan." in text
    assert "## Task briefing" not in _instructions("", "")
    assert "## Available skills" not in _instructions("", "")


def test_appworld_summary_counts_task_pass_all() -> None:
    rows = [
        {"task_id": "a", "reward": 1.0, "pass_count": 2, "num_tests": 2},
        {"task_id": "a", "reward": 0.0, "pass_count": 1, "num_tests": 2},
        {"task_id": "b", "reward": 1.0, "pass_count": 1, "num_tests": 1},
    ]
    summary = summarize(rows)
    assert summary["task_count"] == 2
    assert summary["task_pass_all"] == 0.5
    assert abs(summary["success_rate"] - 2 / 3) < 1e-9


def test_appworld_select_tasks_keeps_variants_of_first_scenarios() -> None:
    ids = ["a1_1", "a1_2", "a1_3", "b2_1", "b2_2", "c3_1"]
    assert select_tasks(ids, "1", 2) == ["a1_1", "b2_1"]
    assert select_tasks(ids, "2,3", None) == ["a1_2", "a1_3", "b2_2"]
    assert select_tasks(ids, "all", 1) == ["a1_1", "a1_2", "a1_3"]


def test_appworld_task_family_uses_the_scenario() -> None:
    assert task_family_for("5238afc_2", "domain") == "tool.appworld"
    assert task_family_for("5238afc_2", "scenario") == "tool.appworld.s5238afc"


def test_redactor_from_task_ignores_expected_action_labels() -> None:
    task = {
        "id": "task-with-label",
        "user_scenario": {"instructions": "Help me."},
        "evaluation_criteria": {
            "actions": [{"action_id": "transfer_to_human_agents", "name": "transfer"}]
        },
    }
    redactor = Redactor.from_task(task, "mock")
    assert redactor.audit("Call transfer_to_human_agents.", redactor.audit_maps()) == []
    assert redactor.audit("Task task-with-label.", redactor.audit_maps()) == ["task-with-label"]


def test_next_teacher_attempt_retries_only_verified_failures() -> None:
    def result(trial: int, succeeded: bool = False, error: str | None = None) -> AppWorldResult:
        return AppWorldResult(
            domain="appworld",
            task_id="t",
            task_index=0,
            trial=trial,
            succeeded=succeeded,
            error=error,
        )

    assert next_teacher_attempt(result(1), 3) == 2
    assert next_teacher_attempt(result(2), 3) == 3
    assert next_teacher_attempt(result(3), 3) is None
    assert next_teacher_attempt(result(1, succeeded=True), 3) is None
    assert next_teacher_attempt(result(1, error="HTTPError"), 3) is None
    assert next_teacher_attempt(result(1), 1) is None


def test_resumed_teacher_attempts_continue_after_the_last_verified_failure() -> None:
    latest = {
        ("t0", 1): {"task_index": 0, "trial": 1, "succeeded": False, "trajectory_uuid": "a"},
        ("t1", 1): {"task_index": 1, "trial": 1, "succeeded": False, "trajectory_uuid": "b"},
        ("t1", 2): {"task_index": 1, "trial": 2, "succeeded": True, "trajectory_uuid": "c"},
        ("t2", 1): {"task_index": 2, "trial": 1, "succeeded": False, "trajectory_uuid": "d"},
        ("t2", 2): {"task_index": 2, "trial": 2, "succeeded": False, "error": "HTTPError"},
        ("t3", 1): {"task_index": 3, "trial": 1, "succeeded": False, "trajectory_uuid": "e"},
        ("t3", 2): {"task_index": 3, "trial": 2, "succeeded": False, "trajectory_uuid": "f"},
        ("t3", 3): {"task_index": 3, "trial": 3, "succeeded": False, "trajectory_uuid": "g"},
    }

    assert resumed_teacher_attempts(latest, 3) == [(0, 2, "a")]
    assert resumed_teacher_attempts(latest, 1) == []


def test_startup_jobs_queue_each_owed_attempt_once_with_its_parent() -> None:
    latest = {
        ("t0", 1): {"task_index": 0, "trial": 1, "succeeded": False, "trajectory_uuid": "a"},
        ("t1", 1): {"task_index": 1, "trial": 1, "succeeded": False, "trajectory_uuid": "b"},
        ("t1", 2): {"task_index": 1, "trial": 2, "succeeded": False, "error": "HTTPError"},
        ("t2", 1): {"task_index": 2, "trial": 1, "succeeded": True, "trajectory_uuid": "c"},
        ("t3", 1): {"task_index": 3, "trial": 1, "succeeded": False, "error": "HTTPError"},
    }
    task_ids = ["t0", "t1", "t2", "t3", "t4"]

    jobs = startup_jobs(task_ids, latest, trials=1, max_attempts=3)

    assert sorted(jobs) == [
        (0, "t0", 2, "a"),
        (1, "t1", 2, None),
        (3, "t3", 1, None),
        (4, "t4", 1, None),
    ]
    assert len({(index, trial) for index, _, trial, _ in jobs}) == len(jobs)
    assert sorted(startup_jobs(task_ids, latest, trials=1, max_attempts=1)) == [
        (1, "t1", 2, None),
        (3, "t3", 1, None),
        (4, "t4", 1, None),
    ]
