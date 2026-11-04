import json
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from openai import OpenAI
from tau2.data_model.message import UserMessage

from agent import ZepResponsesAgent
from config import agent_client
from protocol import (
    ALLTOOLS,
    allowed_tools,
    assert_tools,
    json_object,
    recording_families,
    trial_seeds,
    user_args,
    validate_mode,
)


class Recorder:
    def __init__(self, *args, **kwargs):
        from redaction import Redactor

        self.trajectory_uuid = "trace"
        self.redactor = Redactor()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def emit(self, *args):
        pass

    def set_result(self, *args):
        pass


def mode(**kwargs):
    return Namespace(
        **{
            "stage": "heldout-skills",
            "arm": "skills",
            "skill_snapshot": "snapshot.json",
            "skill_index_scope": "all",
            "retrieval_config": "alltools",
            "agent_model": "gpt-6-luna",
            "reasoning_effort": "low",
            **kwargs,
        }
    )


def test_upstream_seed_schedule_is_local_and_repeatable():
    assert trial_seeds(4) == [626729, 373753, 361454, 1567]
    assert trial_seeds(1) == trial_seeds(4)[:1]
    with pytest.raises(ValueError):
        trial_seeds(0)


def test_user_json_replaces_temperature():
    assert user_args(Namespace(user_llm_args=json_object('{"reasoning_effort":"low"}'))) == {
        "reasoning_effort": "low"
    }
    assert user_args(Namespace(user_temperature=0.0)) == {"temperature": 0.0}
    assert user_args(Namespace(user_llm_args={})) == {}
    for value in ["[]", "null", "{", '{"reasoning_effort":"low","temperature":0}']:
        with pytest.raises((ValueError, TypeError)):
            json_object(value)


@pytest.mark.parametrize("retrieval", ["golden_retrieval", "full_kb", "terminal_use_write"])
def test_oracle_and_writable_retrieval_is_forbidden(retrieval):
    with pytest.raises(ValueError, match="Unsafe retrieval"):
        validate_mode(mode(retrieval_config=retrieval))


@pytest.mark.parametrize(
    "changes",
    [
        {"skill_snapshot": None},
        {"skill_index_scope": "family"},
        {"arm": "r-skill"},
        {"arm": "injected"},
        {"stage": "student-skills"},
        {"task_family_map": "task_types.json"},
        {"task_family_override": "gold"},
    ],
)
def test_executors_reject_live_and_family_conditioned_paths(changes):
    with pytest.raises(ValueError):
        validate_mode(mode(**changes))


def test_frozen_and_no_skills_modes_are_allowed():
    validate_mode(mode())
    validate_mode(mode(arm="none", skill_snapshot=None))


def test_deepseek_accepts_low_and_max_but_not_medium():
    for effort in ("low", "max"):
        validate_mode(mode(agent_model="deepseek-v4.1-flash-1017", reasoning_effort=effort))
    with pytest.raises(ValueError, match="does not accept medium"):
        validate_mode(mode(agent_model="deepseek-v4.1-flash-1017", reasoning_effort="medium"))


def test_gate_rejects_missing_extra_and_duplicate_tools():
    expected = allowed_tools(True)
    tools = [{"name": name} for name in expected]
    assert len(ALLTOOLS) == 17
    assert_tools(tools, expected)
    for invalid in ([], tools[:-1], tools + [{"name": "web_search"}], tools[:-1] + tools[:1]):
        with pytest.raises(ValueError, match="tool gate"):
            assert_tools(invalid, expected)


def test_gate_stops_before_model_request():
    client = MagicMock()
    agent = ZepResponsesAgent(
        [],
        "policy",
        openai_client=client,
        agent_model="test",
        reasoning_effort="low",
        recorder=MagicMock(),
        allowed_tool_names=allowed_tools(False),
    )
    with pytest.raises(ValueError, match="tool gate"):
        agent.generate_next_message(UserMessage.text("hello"), agent.get_init_state())
    client.responses.create.assert_not_called()


def test_recording_map_contains_97_tasks_and_23_types(tmp_path):
    path = Path(__file__).resolve().parents[1] / "task_types.json"
    families = recording_families(str(path))
    assert len(families) == 97
    assert len(set(families.values())) == 23
    assert recording_families(None) == {}
    invalid = tmp_path / "map.json"
    for data in ({}, {"task": 5}, []):
        invalid.write_text(json.dumps(data))
        with pytest.raises(ValueError):
            recording_families(str(invalid))


def test_custom_deepseek_responses_route_preserves_effort(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_BEARER_TOKEN", "placeholder")
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "r", "object": "response", "output": []})

    monkeypatch.setattr(
        "config.openai_http_client", lambda *_: httpx.Client(transport=httpx.MockTransport(handler))
    )
    client = agent_client(
        OpenAI(api_key="placeholder"),
        "https://api.example.test/v1",
        "DEEPSEEK_BEARER_TOKEN",
        None,
    )
    for effort in ("low", "max"):
        client.responses.create(
            model="deepseek-v4.1-flash-1017", input="test", reasoning={"effort": effort}
        )
    assert [r["reasoning"]["effort"] for r in requests] == ["low", "max"]


def test_cli_teacher_uses_recording_map_and_user_args(monkeypatch):
    from run import parse_args

    monkeypatch.setattr(
        "sys.argv",
        [
            "run.py",
            "--stage",
            "teacher",
            "--domains",
            "banking_knowledge",
            "--user-model",
            "gpt-5.2",
            "--user-llm-args",
            '{"reasoning_effort":"low"}',
            "--concurrency",
            "1",
        ],
    )
    args = parse_args()
    assert args.task_family_map.endswith("task_types.json")
    assert args.compilation_mode == "on_close"
    assert args.retrieval_config == "alltools"
    assert user_args(args) == {"reasoning_effort": "low"}


def test_published_user_defaults_reach_tau2_config(monkeypatch):
    from run import _run_config, parse_args

    monkeypatch.setattr(
        "sys.argv",
        ["run.py", "--stage", "teacher", "--domains", "banking_knowledge", "--concurrency", "1"],
    )
    args = parse_args()
    assert args.user_model == "gpt-5.2"
    assert user_args(args) == {"reasoning_effort": "low"}
    config = _run_config(args, "banking_knowledge")
    assert config.llm_user == "gpt-5.2"
    assert config.llm_args_user == {"reasoning_effort": "low"}


def test_executor_capture_and_simulation_never_read_evaluator_fields(monkeypatch):
    from types import SimpleNamespace

    from run import _episode, _redact_simulation

    monkeypatch.setattr(
        "run.Redactor.from_task", lambda *a: pytest.fail("Evaluator fields were read")
    )
    monkeypatch.setattr("run.TrajectoryRecorder", Recorder)
    seeds = []

    def simulate(*args, seed):
        seeds.append(seed)
        return SimpleNamespace(reward_info=None, get_messages=list)

    monkeypatch.setattr("run.run_single_task", simulate)
    task = SimpleNamespace(id="task_001")
    result = _episode(
        MagicMock(),
        MagicMock(),
        stage="heldout-none",
        run_id="test",
        domain="banking_knowledge",
        task=task,
        task_index=0,
        trial=1,
        agent_uuid="student",
        agent_model="gpt-6-luna",
        user_model="gpt-5.2",
        user_temperature=0.0,
        reasoning_effort="low",
        max_steps=200,
        task_split="base",
        inject_skills=False,
        arm="none",
    )
    assert result.error is None
    assert seeds == [626729]
    assert (
        _redact_simulation({}, task, "banking_knowledge", 1, recording=False)["task_id"] == task.id
    )


def test_gate_errors_are_recorded_in_episode_results(monkeypatch):
    from types import SimpleNamespace

    from run import _episode

    monkeypatch.setattr("run.TrajectoryRecorder", Recorder)

    def rejected(*a, **kw):
        assert_tools([], allowed_tools(False))

    monkeypatch.setattr("run.run_single_task", rejected)
    result = _episode(
        MagicMock(),
        MagicMock(),
        stage="heldout-none",
        run_id="test",
        domain="banking_knowledge",
        task=SimpleNamespace(id="task_001"),
        task_index=0,
        trial=1,
        agent_uuid="student",
        agent_model="gpt-6-luna",
        user_model="gpt-5.2",
        user_temperature=0.0,
        reasoning_effort="low",
        max_steps=200,
        task_split="base",
        inject_skills=False,
        arm="none",
    )
    assert result.error == "ValueError"
    assert result.succeeded is False


@pytest.mark.parametrize("failed", [False, True])
def test_episode_rows_persist_audits_on_success_and_failure(monkeypatch, failed):
    from types import SimpleNamespace

    from agent import CURRENT_EPISODE
    from run import _episode

    monkeypatch.setattr("run.TrajectoryRecorder", Recorder)

    def simulate(*args, **kwargs):
        context = CURRENT_EPISODE.get()
        assert context["reasoning_summary"] is True
        audit = context["episode_audit"]
        turn = audit.start_turn()
        request = audit.start_request(turn, "requested")
        audit.record_response(
            turn,
            request,
            [
                {"type": "reasoning", "summary": [{"text": "Check the procedure."}]},
                {"type": "function_call", "name": "read_skill", "call_id": "read"},
            ],
        )
        audit.record_skill_read("read", "procedure", {"found": True, "repaired": False})
        if failed:
            raise ValueError("Episode failed")
        return SimpleNamespace(reward_info=None, get_messages=list)

    monkeypatch.setattr("run.run_single_task", simulate)
    result = _episode(
        MagicMock(),
        MagicMock(),
        stage="heldout-none",
        run_id="test",
        domain="banking_knowledge",
        task=SimpleNamespace(id="task_001"),
        task_index=0,
        trial=1,
        agent_uuid="student",
        agent_model="gpt-6-luna",
        user_model="gpt-5.2",
        user_temperature=0,
        reasoning_effort="low",
        max_steps=200,
        task_split="base",
        inject_skills=False,
        arm="none",
    )
    row = json.loads(json.dumps(result.as_dict()))
    assert (
        row["assistant_turns"][0]["requests"][0]["reasoning"][0]["text"] == "Check the procedure."
    )
    assert row["skill_read_positions"][0]["tool_call_index"] == 1
    assert row["skill_read_positions"][0]["repaired"] is False
    assert row["tool_call_sequence"][0]["tool"] == "read_skill"
    assert result.tool_calls == 0
    assert result.error == ("ValueError" if failed else None)
