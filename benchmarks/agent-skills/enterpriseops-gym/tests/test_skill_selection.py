import json
import sys
from types import SimpleNamespace

import pytest

from eog import run as run_module
from eog import runner as runner_module
from eog.run import _prepare_skill_selection, _skill_query


class FakeSkillAPI:
    def __init__(self, skill_id: str) -> None:
        self.skill_id = skill_id
        self.calls = []

    def search(self, agent_uuid: str, **kwargs: str | int | bool) -> SimpleNamespace:
        self.calls.append((agent_uuid, kwargs))
        return SimpleNamespace(items=[{"skill_uuid": self.skill_id}])


def _task(task_id: str, domain: str) -> SimpleNamespace:
    return SimpleNamespace(
        task_id=task_id,
        domain=domain,
        user_prompt=f"Complete a sample {domain} task.",
        known_defect=False,
    )


def _inputs(tmp_path, skill_id: str = "skill-calendar"):
    task = _task("task-demo-calendar", "calendar")
    family = run_module._task_families(task)[0]
    skills = {
        "skill-calendar": {
            "id": "skill-calendar",
            "sha256": "skill-sha",
            "markdown": "Calendar steps",
            "task_family": family,
        }
    }
    client = SimpleNamespace(agent=SimpleNamespace(skill=FakeSkillAPI(skill_id)))
    return task, family, skills, client, tmp_path / "runs" / "skill-selection.json"


def test_live_search_saves_query_ordered_ids_and_snapshot_hash(tmp_path) -> None:
    task, family, skills, client, path = _inputs(tmp_path)

    selection = _prepare_skill_selection(
        path,
        (task,),
        skills,
        "snapshot-sha",
        "agent-1",
        client,
        resume=False,
    )

    saved = json.loads(path.read_text())
    assert selection == {(task.task_id, family): ["skill-calendar"]}
    assert saved == {
        "snapshot_sha256": "snapshot-sha",
        "selections": [
            {
                "task_id": task.task_id,
                "family": family,
                "query": _skill_query(task.user_prompt),
                "skill_ids": ["skill-calendar"],
            }
        ],
    }
    api = client.agent.skill
    assert len(api.calls) == 1
    assert api.calls[0] == (
        "agent-1",
        {
            "query": _skill_query(task.user_prompt),
            "page_size": 3,
            "task_family": family,
            "include_markdown": True,
        },
    )


def test_live_search_uses_the_agent_for_each_task_family(tmp_path) -> None:
    tasks = (_task("task-demo-calendar", "calendar"), _task("task-demo-hr", "hr"))
    families = {task.task_id: run_module._task_families(task)[0] for task in tasks}
    skill_ids = {families[tasks[0].task_id]: "calendar-skill", families[tasks[1].task_id]: "hr-skill"}
    skills = {
        skill_id: {
            "id": skill_id,
            "sha256": f"sha-{skill_id}",
            "markdown": f"Steps for {family}",
            "task_family": family,
        }
        for family, skill_id in skill_ids.items()
    }

    class FamilySkillAPI:
        def __init__(self) -> None:
            self.calls = []

        def search(self, agent_uuid: str, **kwargs: str | int | bool) -> SimpleNamespace:
            family = str(kwargs["task_family"])
            self.calls.append((agent_uuid, family))
            return SimpleNamespace(items=[{"skill_uuid": skill_ids[family]}])

    api = FamilySkillAPI()
    client = SimpleNamespace(agent=SimpleNamespace(skill=api))
    family_agents = {
        families[tasks[0].task_id]: "calendar-agent",
        families[tasks[1].task_id]: "hr-agent",
    }

    _prepare_skill_selection(
        tmp_path / "runs" / "skill-selection.json",
        tasks,
        skills,
        "snapshot-sha",
        "first-agent",
        client,
        resume=False,
        family_agents=family_agents,
    )

    assert api.calls == [("calendar-agent", "eog.calendar"), ("hr-agent", "eog.hr")]


@pytest.mark.parametrize(
    ("hit_id", "snapshot_family"),
    [("outside-snapshot", "eog.calendar"), ("skill-calendar", "eog.email")],
)
def test_invalid_live_hit_stops_selection_before_episodes(tmp_path, hit_id: str, snapshot_family: str) -> None:
    task, _family, skills, client, path = _inputs(tmp_path, hit_id)
    skills["skill-calendar"]["task_family"] = snapshot_family

    with pytest.raises(SystemExit, match=hit_id):
        _prepare_skill_selection(
            path,
            (task,),
            skills,
            "snapshot-sha",
            "agent-1",
            client,
            resume=False,
        )

    assert not path.exists()


def test_out_of_snapshot_hit_stops_executor_before_first_episode(tmp_path, monkeypatch) -> None:
    task = _task("task-demo-calendar", "calendar")
    family = run_module._task_families(task)[0]
    snapshot_path = tmp_path / "teacher" / "skills" / "snapshot.json"
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_text(json.dumps({"sha256": "snapshot-sha"}))
    (snapshot_path.parent.parent / "agent.json").write_text(json.dumps({"agent_uuid": "agent-1"}))
    baseline_path = tmp_path / "noop_baseline.jsonl"
    baseline_path.write_text("")
    skills = [
        {
            "id": "skill-calendar",
            "sha256": "skill-sha",
            "markdown": "---\nname: agent-skill-calendar\ndescription: Calendar steps\n---\n\nCalendar steps",
        }
    ]

    class OutOfSnapshotSkillAPI:
        def search(self, agent_uuid: str, **kwargs: str | int | bool) -> SimpleNamespace:
            return SimpleNamespace(items=[{"skill_uuid": "outside-snapshot"}])

    client = SimpleNamespace(agent=SimpleNamespace(skill=OutOfSnapshotSkillAPI()))
    split = {"tasks": {task.task_id: {"side": "score", "cluster": "cluster-1"}}}
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "eog.run",
            "--stage",
            "executor",
            "--model",
            "gpt-6-luna",
            "--effort",
            "low",
            "--skills-snapshot",
            str(snapshot_path),
            "--reproduction-report",
            str(tmp_path / "analysis.json"),
            "--run-id",
            "selection-test",
            "--run-dir",
            str(tmp_path / "runs"),
            "--arms",
            "none,skills_decided",
        ],
    )
    monkeypatch.setenv("ZEP_API_KEY", "test-key")
    monkeypatch.setenv("ZEP_BASE_URL", "https://api.example.com/api/v4")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(run_module, "load_tasks", lambda: (task,))
    monkeypatch.setattr(run_module, "load_task_sample", lambda tasks, task_list: tasks)
    monkeypatch.setattr(run_module, "_resolve_noop_baseline_path", lambda args: baseline_path)
    monkeypatch.setattr(run_module, "sample_score_tasks", lambda tasks, split: tasks)
    monkeypatch.setattr(run_module, "make_split", lambda tasks: split)
    monkeypatch.setattr(run_module, "_compile_failure_message", lambda snapshot_dir, families: None)
    monkeypatch.setattr(
        run_module,
        "load_snapshot_details",
        lambda path, task_family=None: (
            "Calendar steps",
            "block-sha",
            skills if task_family in (None, family) else [],
        ),
    )
    monkeypatch.setattr(run_module, "assert_no_score_literals", lambda skills, split, tasks: None)
    monkeypatch.setattr(run_module, "zep_client", lambda: client)
    monkeypatch.setattr(
        runner_module,
        "run_concurrent",
        lambda *args, **kwargs: pytest.fail("The executor started an episode after a bad Skill hit."),
    )

    with pytest.raises(SystemExit, match="outside-snapshot"):
        run_module.main()


def test_resume_reuses_saved_selection_without_live_search(tmp_path) -> None:
    task, family, skills, client, path = _inputs(tmp_path)
    _prepare_skill_selection(path, (task,), skills, "snapshot-sha", "agent-1", client, resume=False)
    client.agent.skill.calls.clear()

    selection = _prepare_skill_selection(path, (task,), skills, "snapshot-sha", "agent-1", client, resume=True)

    assert selection == {(task.task_id, family): ["skill-calendar"]}
    assert client.agent.skill.calls == []


def test_resume_rejects_selection_for_a_different_snapshot(tmp_path) -> None:
    task, _, skills, client, path = _inputs(tmp_path)
    _prepare_skill_selection(path, (task,), skills, "snapshot-sha", "agent-1", client, resume=False)

    with pytest.raises(SystemExit, match="different snapshot SHA-256"):
        _prepare_skill_selection(path, (task,), skills, "changed-snapshot-sha", "agent-1", client, resume=True)


class FakeDecider:
    model = "fake-decider"

    def __init__(self, choice: str) -> None:
        self.choice = choice
        self.calls = 0
        self.usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        self.requests = []

    def decide(self, task_text: str, candidates: list[tuple[str, str]]) -> tuple[str, dict[str, float]]:
        self.calls += 1
        self.requests.append((task_text, candidates))
        values = [value for value, _ in candidates]
        return self.choice, {value: (0.9 if value == self.choice else 0.1) for value in values}


class FakeManySkillAPI:
    def __init__(self, skill_ids: list[str]) -> None:
        self.skill_ids = skill_ids
        self.calls = []

    def search(self, agent_uuid: str, **kwargs: str | int | bool) -> SimpleNamespace:
        self.calls.append((agent_uuid, kwargs))
        return SimpleNamespace(items=[{"skill_uuid": skill_id} for skill_id in self.skill_ids])


def test_decided_selection_keeps_the_one_chosen_skill_of_the_top_five(tmp_path) -> None:
    task = _task("task-demo-calendar", "calendar")
    family = run_module._task_families(task)[0]
    ids = [f"skill-{index}" for index in range(5)]
    skills = {
        skill_id: {
            "id": skill_id,
            "sha256": f"sha-{skill_id}",
            "markdown": f'---\nname: {skill_id}\ndescription: "Procedure {skill_id}"\n---\n\nSteps {skill_id}',
            "task_family": family,
        }
        for skill_id in ids
    }
    client = SimpleNamespace(agent=SimpleNamespace(skill=FakeManySkillAPI(ids)))
    decider = FakeDecider("skill-3")
    path = tmp_path / "runs" / "skill-selection.decided.json"

    selection = _prepare_skill_selection(
        path, (task,), skills, "snapshot-sha", "agent-1", client, resume=False, page_size=5, decider=decider
    )

    assert selection == {(task.task_id, family): ["skill-3"]}
    assert client.agent.skill.calls[0][1]["page_size"] == 5
    assert decider.requests == [(task.user_prompt, [(skill_id, f"Procedure {skill_id}") for skill_id in ids])]
    saved = json.loads(path.read_text())
    entry = saved["selections"][0]
    assert entry["skill_ids"] == ["skill-3"] and entry["candidates"] == ids
    assert entry["probabilities"]["skill-3"] == 0.9
    assert saved["decision"] == {
        "model": "fake-decider",
        "candidates_per_family": 5,
        "calls": 1,
        "usage": decider.usage,
    }

    resumed = _prepare_skill_selection(path, (task,), skills, "snapshot-sha", "agent-1", None, resume=True)
    assert resumed == selection
    inputs = run_module._executor_skill_inputs({"arm": "skills_decided"}, task, skills, {}, decided_selection=selection)
    assert "Steps skill-3" in inputs[0] and "Steps skill-2" not in inputs[0]
    assert inputs[2] == [{"id": "skill-3", "sha256": "sha-skill-3"}] and inputs[3] is None
    with pytest.raises(ValueError):
        run_module._executor_skill_inputs({"arm": "skills_decided"}, task, skills, {})


def test_decided_selection_gives_no_skill_on_a_refusal(tmp_path) -> None:
    task = _task("task-demo-calendar", "calendar")
    family = run_module._task_families(task)[0]
    skills = {
        "skill-0": {"id": "skill-0", "sha256": "sha-0", "markdown": "Steps skill-0", "task_family": family},
    }
    client = SimpleNamespace(agent=SimpleNamespace(skill=FakeManySkillAPI(["skill-0"])))
    decider = FakeDecider("skill-0")
    decider.decide = lambda task_text, candidates: (None, {})
    path = tmp_path / "runs" / "skill-selection.decided.json"

    selection = _prepare_skill_selection(
        path, (task,), skills, "snapshot-sha", "agent-1", client, resume=False, page_size=5, decider=decider
    )

    assert selection == {(task.task_id, family): []}
    entry = json.loads(path.read_text())["selections"][0]
    assert entry["refused"] is True and entry["candidates"] == ["skill-0"]
    inputs = run_module._executor_skill_inputs({"arm": "skills_decided"}, task, skills, {}, decided_selection=selection)
    assert inputs[0] is None and inputs[2] == []


def test_decided_selection_restart_reuses_the_saved_decisions(tmp_path) -> None:
    tasks = (_task("task-demo-calendar-1", "calendar"), _task("task-demo-calendar-2", "calendar"))
    family = run_module._task_families(tasks[0])[0]
    skills = {
        "skill-0": {"id": "skill-0", "sha256": "sha-0", "markdown": "Steps skill-0", "task_family": family},
    }
    client = SimpleNamespace(agent=SimpleNamespace(skill=FakeManySkillAPI(["skill-0"])))
    path = tmp_path / "runs" / "skill-selection.decided.json"

    class FailingDecider(FakeDecider):
        def decide(self, task_text, candidates):
            if self.calls == 1:
                raise RuntimeError("Decisions API HTTP 502")
            return super().decide(task_text, candidates)

    first = FailingDecider("skill-0")
    with pytest.raises(RuntimeError):
        _prepare_skill_selection(
            path, tasks, skills, "snapshot-sha", "agent-1", client, resume=False, page_size=5, decider=first
        )
    partial_path = path.with_suffix(".partial.json")
    partial = json.loads(partial_path.read_text())
    assert len(partial["decisions"]) == 1 and partial["snapshot_sha256"] == "snapshot-sha" and not path.exists()

    other_snapshot = FakeDecider("skill-0")
    with pytest.raises(RuntimeError, match="other"):
        other_snapshot.decide = lambda task_text, candidates: (_ for _ in ()).throw(RuntimeError("other"))
        _prepare_skill_selection(
            path, tasks, skills, "other-sha", "agent-1", client, resume=False, page_size=5, decider=other_snapshot
        )

    second = FakeDecider("skill-0")
    selection = _prepare_skill_selection(
        path, tasks, skills, "snapshot-sha", "agent-1", client, resume=False, page_size=5, decider=second
    )
    assert second.calls == 2, "the saved decision is reused and its call is counted"
    assert len(selection) == sum(len(run_module._task_families(task)) for task in tasks)
    assert not partial_path.exists() and path.exists()
    assert json.loads(path.read_text())["decision"]["calls"] == 2
