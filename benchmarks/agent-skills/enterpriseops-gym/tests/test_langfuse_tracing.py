from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import eog.run as run_module
import eog.tracing as tracing_module
from eog.agent import run_agent
from eog.dataset import load_tasks
from eog.errors import ContextOverflowError, PolicyFlagError
from eog.ledger import CostLedger
from eog.providers.base import ModelTurn, ToolCall, Usage
from eog.runner import RunStore, interleave_arms
from eog.stirrup_constants import FINISH_TOOL_NAME
from eog.tracing import RowTrace, TracedMCP, TracedRoute


class FakeObservation:
    def __init__(self, client: FakeLangfuse, **fields: Any) -> None:
        self.client = client
        self.fields = fields
        self.children: list[FakeObservation] = []
        self.ended = False

    def start_observation(self, **fields: Any) -> FakeObservation:
        child = FakeObservation(self.client, **fields)
        self.children.append(child)
        if fields.get("as_type") == "event":
            child.ended = True
        return child

    def update(self, **fields: Any) -> FakeObservation:
        self.fields.update(fields)
        return self

    def end(self, **_kwargs: Any) -> FakeObservation:
        self.ended = True
        if self is self.client.root_context.observation:
            self.client.call_order.append("root_end")
        return self


class FakeContext:
    def __init__(self, observation: FakeObservation) -> None:
        self.observation = observation

    def __enter__(self) -> FakeObservation:
        return self.observation

    def __exit__(self, *_args: Any) -> None:
        if self.observation is self.observation.client.root_context.observation:
            self.observation.client.call_order.append("root_context_exit")
        return None


class FakeLangfuse:
    def __init__(self, *, auth: bool = True, **_kwargs: Any) -> None:
        self.auth = auth
        self.flush_count = 0
        self.scores: list[dict[str, Any]] = []
        self.root_context: FakeContext | None = None
        self.propagated: dict[str, Any] | None = None
        self.call_order: list[str] = []

    def auth_check(self) -> bool:
        return self.auth

    def create_trace_id(self, *, seed: str) -> str:
        return f"trace-{seed}"

    def get_trace_url(self, *, trace_id: str) -> str:
        return f"https://langfuse.test/trace/{trace_id}"

    def start_as_current_observation(self, **fields: Any) -> FakeContext:
        self.call_order.append("root_start")
        root = FakeObservation(self, **fields)
        self.root_context = FakeContext(root)
        return self.root_context

    def create_score(self, **fields: Any) -> None:
        self.scores.append(fields)

    def flush(self) -> None:
        self.flush_count += 1


class FakeRoute:
    model = "test-model"
    route = "test-route"

    def __init__(self, turn: ModelTurn | None = None, error: Exception | None = None) -> None:
        self.turn = turn
        self.error = error
        self.calls = 0

    def complete(self, *_args: Any, **_kwargs: Any) -> ModelTurn:
        self.calls += 1
        if self.error is not None:
            raise self.error
        assert self.turn is not None
        return self.turn


class FakeMCP:
    selected_tools = ("calendar__find_event",)

    def __init__(self, result: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        self.result = result or {"content": [{"type": "text", "text": "full result"}], "isError": False}
        self.error = error
        self.closed = False

    def call_tool(self, _name: str, _arguments: dict[str, Any]) -> dict[str, Any]:
        if self.error is not None:
            raise self.error
        return self.result

    def close(self) -> None:
        self.closed = True


class FakeExecutionGym:
    def __init__(self, task: Any) -> None:
        self.verifier = SimpleNamespace(
            success=True,
            passed=tuple(True for _ in task.verifiers),
            actual=tuple(None for _ in task.verifiers),
            expected=tuple(None for _ in task.verifiers),
            errors=tuple(None for _ in task.verifiers),
        )

    def seed(self, *_args: Any) -> None:
        return None

    def check_reset(self, *_args: Any, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(success=True, passed=(True,))

    def verify(self, *_args: Any) -> SimpleNamespace:
        return self.verifier

    def delete(self, *_args: Any) -> None:
        return None

    def close(self) -> None:
        return None


def _patch_propagation(monkeypatch: pytest.MonkeyPatch, client: FakeLangfuse) -> None:
    @contextmanager
    def fake_propagate(**fields: Any):
        client.propagated = fields
        client.call_order.append("propagation_enter")
        try:
            yield
        finally:
            client.call_order.append("propagation_exit")

    monkeypatch.setattr(tracing_module, "propagate_attributes", fake_propagate)


def _make_trace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[RowTrace, FakeLangfuse]:
    client = FakeLangfuse()
    _patch_propagation(monkeypatch, client)
    task = next(task for task in load_tasks() if task.domain == "calendar")
    trace = RowTrace(
        client,
        task=task,
        run_id="trace-run",
        trial=1,
        arm="skills",
        model="test-model",
        effort="low",
        route="test-route",
        skills_hash="skill-hash",
        skills=[{"id": "skill-1"}],
        run_dir=tmp_path,
    )
    return trace, client


def _turn(
    *,
    text: str = "done",
    tool_calls: tuple[ToolCall, ...] = (),
    reasoning: str = "think",
) -> ModelTurn:
    return ModelTurn(
        text=text,
        tool_calls=tool_calls,
        raw_usage={},
        usage=Usage(input=10, cache_read=2, cache_write=3, output=4, reasoning=5),
        history_items=({"role": "assistant", "content": text},),
        latency_seconds=0.25,
        reasoning_text=reasoning,
        served_provider="DeepInfra",
    )


@pytest.mark.parametrize("missing", ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL"))
def test_missing_langfuse_environment_stops_before_route(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    for name in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL"):
        monkeypatch.setenv(name, "value")
    monkeypatch.delenv(missing)
    route_called = False

    class FailIfCalledRoute:
        def complete(self, *_args: Any, **_kwargs: Any) -> None:
            nonlocal route_called
            route_called = True
            raise AssertionError("The route must not run.")

    with pytest.raises(SystemExit, match=missing):
        tracing_module.require_langfuse_env()
    assert route_called is False


def test_langfuse_auth_failure_stops_without_returning_a_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL"):
        monkeypatch.setenv(name, "value")
    monkeypatch.setattr(tracing_module, "Langfuse", lambda **_kwargs: FakeLangfuse(auth=False))

    with pytest.raises(SystemExit, match="authentication failed"):
        tracing_module.require_langfuse_env()


def test_traced_route_records_full_generation() -> None:
    client = FakeLangfuse()
    task = next(task for task in load_tasks() if task.domain == "calendar")
    trace = RowTrace(
        client,
        task=task,
        run_id="run",
        trial=1,
        arm="none",
        model="test-model",
        effort="low",
        route="test-route",
        skills_hash="",
        skills=[],
        run_dir=Path("results"),
    )
    inner = FakeRoute(
        _turn(
            tool_calls=(ToolCall("call-1", "calendar__find_event", {"query": "launch"}),),
        )
    )
    messages = [{"role": "user", "content": "Find the launch event."}]
    TracedRoute(inner, trace).complete("full system", messages, [], attempt=1)

    generation = trace.generations[0]
    assert generation["input"] == [{"role": "system", "content": "full system"}, *messages]
    assert generation["output"]["reasoning"] == "think"
    assert generation["output"]["tool_calls"] == [
        {"id": "call-1", "name": "calendar__find_event", "arguments": {"query": "launch"}}
    ]
    assert generation["usage"] == {
        "input": 10,
        "cache_read": 2,
        "cache_write": 3,
        "output": 4,
        "reasoning": 5,
    }
    assert generation["metadata"]["served_provider"] == "DeepInfra"
    trace.close(None)


def test_three_turn_episode_has_one_generation_per_model_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    trace, client = _make_trace(tmp_path, monkeypatch)
    task = next(task for task in load_tasks() if task.domain == "calendar")
    tool_name = task.selected_tools[0]
    calls = 0

    class EpisodeRoute(FakeRoute):
        def complete(self, _system: str, _messages: list[dict[str, Any]], _tools: list[dict[str, Any]], **kwargs: Any):
            nonlocal calls
            calls += 1
            kwargs["context"]["model_call_latencies"].append(0.1)
            if calls == 1:
                return _turn(text="", tool_calls=(ToolCall("call-1", tool_name, {"query": "launch"}),))
            if calls == 2:
                return _turn(text="Continuing.", tool_calls=())
            return _turn(
                text="",
                tool_calls=(ToolCall("finish-1", FINISH_TOOL_NAME, {"reason": "done"}),),
            )

    context = {"model_call_latencies": [], "tool_call_latencies": []}
    result = run_agent(
        task,
        TracedRoute(EpisodeRoute(), trace),
        TracedMCP(FakeMCP(), trace),
        [{"name": name, "description": "", "inputSchema": {"type": "object"}} for name in task.selected_tools],
        effort="low",
        arm="none",
        selected_tools=task.selected_tools,
        context=context,
    )

    assert result.turns == 3
    assert len(context["model_call_latencies"]) == 3
    assert len(trace.generations) == 3
    trace.close({"status": "valid", "finish_reason": result.finish_reason, "turns": result.turns, "model_calls": 3})
    assert client.flush_count == 1


def test_traced_route_records_context_overflow_as_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    trace, _client = _make_trace(Path("results"), monkeypatch)
    route = TracedRoute(FakeRoute(error=ContextOverflowError("too long")), trace)

    with pytest.raises(ContextOverflowError):
        route.complete("system", [], [])

    assert trace.generations == []
    assert len(trace.events) == 1
    assert trace.events[0]["error_class"] == "ContextOverflowError"
    trace.close(None)


def test_traced_mcp_records_full_result_and_error_level(monkeypatch: pytest.MonkeyPatch) -> None:
    trace, client = _make_trace(Path("results"), monkeypatch)
    result = {"content": [{"type": "text", "text": "full"}], "isError": True, "extra": {"value": 1}}
    inner = FakeMCP(result=result)
    mcp = TracedMCP(inner, trace)

    assert mcp.selected_tools == ("calendar__find_event",)
    assert mcp.call_tool("calendar__find_event", {"query": "launch"}) == result

    observation = client.root_context.observation.children[0]
    assert observation.fields["output"] == result
    assert observation.fields["level"] == "ERROR"
    assert trace.tool_calls[0]["result"] == result
    assert trace.tool_calls[0]["is_error"] is True
    mcp.close()
    assert inner.closed is True


def test_traced_mcp_records_and_reraises_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    trace, _client = _make_trace(Path("results"), monkeypatch)
    mcp = TracedMCP(FakeMCP(error=RuntimeError("tool failed")), trace)

    with pytest.raises(RuntimeError, match="tool failed"):
        mcp.call_tool("calendar__find_event", {"query": "launch"})

    assert trace.tool_calls[0]["is_error"] is True
    assert "RuntimeError: tool failed" in trace.tool_calls[0]["error"]
    trace.close(None)


def test_row_trace_records_tags_metadata_scores_transcript_and_flush(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trace, client = _make_trace(tmp_path, monkeypatch)
    verifier = SimpleNamespace(
        passed=(True, False),
        actual=("actual-1", "actual-2"),
        expected=("expected-1", "expected-2"),
        errors=(None, "wrong"),
    )
    corrected_verifier = SimpleNamespace(
        passed=(False,),
        actual=("corrected-actual",),
        expected=("corrected-expected",),
        errors=("corrected-error",),
    )
    score_metadata = {
        "original": {"reward": 0, "status": "valid", "verifier_passes": [True, False]},
        "corrected": {"reward": 1, "status": "valid", "verifier_passes": [False]},
    }
    TracedRoute(FakeRoute(_turn()), trace).complete("system", [{"role": "user", "content": "prompt"}], [])
    TracedMCP(FakeMCP(), trace).call_tool("calendar__find_event", {"query": "launch"})
    trace.add_scores(
        reward=0,
        verifier=verifier,
        corrected_reward=1,
        corrected_verifier=corrected_verifier,
        score_metadata=score_metadata,
    )

    trace.close({"status": "valid", "finish_reason": "finished", "turns": 1, "model_calls": 1, "reward": 0})
    trace.close({"status": "valid"})

    assert client.flush_count == 1
    assert client.call_order.index("propagation_enter") < client.call_order.index("root_start")
    assert client.call_order.index("root_end") < client.call_order.index("root_context_exit")
    assert client.call_order.index("root_context_exit") < client.call_order.index("propagation_exit")
    assert client.propagated == {
        "tags": [
            "run_id:trace-run",
            "family:calendar",
            "arm:skills",
            "model:test-model",
            "effort:low",
            "route:test-route",
        ],
        "trace_name": f"{trace.task_id} skills t1",
    }
    assert client.root_context.observation.fields["metadata"]["selected_skill_ids"] == ["skill-1"]
    assert client.root_context.observation.fields["metadata"]["score_metadata"] == score_metadata
    assert [score["name"] for score in client.scores] == [
        "reward",
        "verifier_0",
        "verifier_1",
        "corrected_reward",
        "corrected_verifier_0",
    ]
    assert "actual-2" in client.scores[2]["comment"]
    transcript = json.loads((tmp_path / "transcripts.jsonl").read_text().splitlines()[0])
    assert transcript["generations"] == trace.generations
    assert transcript["tool_calls"] == trace.tool_calls
    assert transcript["score_metadata"] == score_metadata


def test_row_trace_uses_a_unique_trace_id_per_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first_trace, _ = _make_trace(tmp_path, monkeypatch)
    first_trace.close(None)
    second_trace, _ = _make_trace(tmp_path, monkeypatch)
    second_trace.close(None)

    assert first_trace.execution_id != second_trace.execution_id
    assert first_trace.trace_id != second_trace.trace_id
    assert first_trace.metadata["execution_id"] == first_trace.execution_id
    transcripts = [json.loads(line) for line in (tmp_path / "transcripts.jsonl").read_text().splitlines()]
    assert [item["execution_id"] for item in transcripts] == [
        first_trace.execution_id,
        second_trace.execution_id,
    ]


def test_row_trace_tracks_attempts_and_final_generation_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    trace, client = _make_trace(tmp_path, monkeypatch)
    TracedRoute(FakeRoute(_turn()), trace).complete("system", [], [])
    trace.attempt = 1
    TracedRoute(FakeRoute(_turn()), trace).complete("system", [], [])
    with pytest.raises(ContextOverflowError):
        TracedRoute(FakeRoute(error=ContextOverflowError("too long")), trace).complete("system", [], [])
    TracedMCP(FakeMCP(), trace).call_tool("calendar__find_event", {"query": "launch"})

    fields = trace.close({"status": "valid", "model_calls": 1})

    assert fields["langfuse_generations"] == 1
    assert fields["langfuse_generations_all_attempts"] == 2
    assert [item["attempt"] for item in trace.generations] == [0, 1]
    assert [item["metadata"]["attempt"] for item in trace.generations] == [0, 1]
    assert trace.events[0]["attempt"] == 1
    assert trace.tool_calls[0]["attempt"] == 1
    assert [child.fields["metadata"]["attempt"] for child in client.root_context.observation.children] == [
        0,
        1,
        1,
        1,
    ]
    transcript = json.loads((tmp_path / "transcripts.jsonl").read_text().splitlines()[0])
    assert transcript["attempt"] == 1
    assert transcript["generations"] == trace.generations
    assert transcript["tool_calls"] == trace.tool_calls
    assert transcript["events"] == trace.events


def test_row_trace_flushes_once_when_the_row_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    trace, client = _make_trace(tmp_path, monkeypatch)
    try:
        raise RuntimeError("row failed")
    except RuntimeError:
        pass
    finally:
        trace.close({"status": "partial", "error_class": "RuntimeError"})

    assert client.flush_count == 1


def test_row_trace_closes_when_gym_client_creation_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeLangfuse()
    _patch_propagation(monkeypatch, client)
    task = next(task for task in load_tasks() if task.domain == "calendar" and not task.known_defect)
    route_instances: list[FakeRoute] = []

    def make_route(*_args: Any, **_kwargs: Any) -> FakeRoute:
        route = FakeRoute(_turn())
        route.route = "openrouter"
        route_instances.append(route)
        return route

    def fail_gym_clients(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("Gym client setup failed")

    monkeypatch.setattr(run_module, "_route", make_route)
    monkeypatch.setattr(run_module, "_gym_clients", fail_gym_clients)
    row = {"task_id": task.task_id, "trial": 1, "arm": "skills", "cell": "executor"}

    with pytest.raises(RuntimeError, match="Gym client setup failed"):
        run_module._execute_row(
            row,
            task_by_id={task.task_id: task},
            model="test-model",
            effort="low",
            run_id="trace-run",
            store=RunStore(tmp_path / "run"),
            cost_ledger=CostLedger(tmp_path / "cost.jsonl", max_usd=20),
            capture_dir=None,
            skills_block=None,
            skills_hash="skill-hash",
            skills=[],
            zep=None,
            agent_uuid=None,
            run_dir=tmp_path,
            langfuse_client=client,
        )

    transcript_lines = (tmp_path / "transcripts.jsonl").read_text().splitlines()
    assert client.flush_count == 1
    assert len(transcript_lines) == 1
    assert json.loads(transcript_lines[0])["route"] == "openrouter"
    assert len(route_instances) == 1


def test_policy_retry_reuses_one_trace_and_marks_the_second_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeLangfuse()
    _patch_propagation(monkeypatch, client)
    task = next(task for task in load_tasks() if task.domain == "calendar" and not task.known_defect)
    seed = tmp_path / "seed.sql"
    seed.write_text("SELECT 1;")
    gym = FakeExecutionGym(task)
    route_instances: list[FakeRoute] = []
    seen_route_instances: list[Any] = []
    latency_lists: list[list[float]] = []
    calls = 0

    def make_route(*_args: Any, **_kwargs: Any) -> FakeRoute:
        route = FakeRoute(_turn())
        route.route = "openrouter"
        route_instances.append(route)
        return route

    class FakeComposite:
        selected_tools: tuple[str, ...] = ()

        def start(self, _task: Any) -> list[dict[str, Any]]:
            return []

        def close(self) -> None:
            return None

    def fake_run_agent(_task: Any, route: Any, *_args: Any, **kwargs: Any) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        seen_route_instances.append(route.inner)
        latency_list = kwargs["context"]["model_call_latencies"]
        latency_lists.append(latency_list)
        latency_list.append(0.25)
        route.complete("system", [{"role": "user", "content": "prompt"}], [])
        if calls == 1:
            raise PolicyFlagError("policy retry")
        return SimpleNamespace(
            finish_reason="finished",
            turns=1,
            usage={},
            events=(),
            active_seconds=0.25,
            final_text="done",
        )

    monkeypatch.setattr(run_module, "_route", make_route)
    gym_name = task.gym_servers_config[0]["mcp_server_name"]
    monkeypatch.setattr(run_module, "_gym_clients", lambda _task, _ids: {gym_name: gym})
    monkeypatch.setattr(run_module, "_seed_path", lambda _name: seed)
    monkeypatch.setattr(run_module, "MCPClient", lambda *_args, **_kwargs: FakeMCP())
    monkeypatch.setattr(run_module, "CompositeMCPClient", lambda _clients: FakeComposite())
    monkeypatch.setattr(run_module, "run_agent", fake_run_agent)

    result = run_module._execute_row(
        {"task_id": task.task_id, "trial": 1, "arm": "none", "cell": "executor"},
        task_by_id={task.task_id: task},
        model="test-model",
        effort="low",
        run_id="trace-run",
        store=RunStore(tmp_path / "run"),
        cost_ledger=CostLedger(tmp_path / "cost.jsonl"),
        capture_dir=None,
        skills_block=None,
        skills_hash="",
        skills=[],
        zep=None,
        agent_uuid=None,
        run_dir=tmp_path,
        langfuse_client=client,
    )

    assert calls == 2
    assert len(route_instances) == 1
    assert seen_route_instances[0] is seen_route_instances[1]
    assert latency_lists[0] is not latency_lists[1]
    assert result["model_calls"] == result["langfuse_generations"] == 1
    assert result["langfuse_generations_all_attempts"] == 2
    transcript = json.loads((tmp_path / "transcripts.jsonl").read_text().splitlines()[0])
    assert [generation["attempt"] for generation in transcript["generations"]] == [0, 1]
    assert all(generation["metadata"]["attempt"] == generation["attempt"] for generation in transcript["generations"])
    assert client.flush_count == 1


def test_arms_select_only_skills_and_one_arm_analysis_is_valid() -> None:
    arms = run_module._resolve_arms("executor", "skills")
    assert arms == ("skills",)
    rows = [{"task_id": str(index), "trial": 1, "arm": arm} for index, arm in enumerate(arms)]
    assert {row["arm"] for row in rows} == {"skills"}
    assert interleave_arms(rows, seed=1) == rows
    task = SimpleNamespace(task_id="task", user_prompt="", verifiers=[])
    report = run_module.date_sensitivity_report(
        [{**rows[0], "cell": "executor", "status": "valid", "reward": 1}],
        [task],
        arms=arms,
    )
    assert report["all"]["skills"]["p_hat"] == 1.0
    assert report["all"]["delta"] is None
    assert report["without_relative_date_tasks"]["delta"] is None
    assert report["without_wall_clock_tasks"]["delta"] is None
    assert report["paired_analysis"] == []

    two_arm_report = run_module.date_sensitivity_report(
        [
            {"task_id": "none", "arm": "none", "cell": "executor", "status": "valid", "reward": 0},
            {"task_id": "skills", "arm": "skills", "cell": "executor", "status": "valid", "reward": 1},
        ],
        [task],
    )
    assert two_arm_report["all"]["delta"] == 1.0


@pytest.mark.parametrize("requested", ("bogus", "none,none"))
def test_invalid_arms_are_rejected(requested: str) -> None:
    with pytest.raises(SystemExit, match="arms"):
        run_module._resolve_arms("executor", requested)


def test_arms_are_rejected_outside_executor() -> None:
    with pytest.raises(SystemExit, match="only valid"):
        run_module._resolve_arms("teacher", "skills")
