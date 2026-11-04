from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from eog import preflight
from eog.agent import _tool_results, run_agent
from eog.dataset import load_tasks
from eog.errors import ContextOverflowError, InfrastructureError
from eog.gym import GymClient, VerifierResult
from eog.prices import cost_usd
from eog.providers.base import ModelTurn, ToolCall, Usage
from eog.runner import freeze_manifest


def _task(domain: str = "calendar"):
    return next(task for task in load_tasks() if task.domain == domain)


def _tools(task):
    return [{"name": name, "inputSchema": {"type": "object"}} for name in task.selected_tools]


def _turn(text="", calls=(), usage=None):
    return ModelTurn(text, tuple(calls), {}, usage or Usage(input=100, output=10), (), 1.0)


class _Route:
    model = "glm-5.3-flash"

    def __init__(self, turns):
        self.turns = list(turns)
        self.calls = []

    def complete(self, system, messages, tools, **kwargs):
        self.calls.append([dict(message) for message in messages])
        turn = self.turns.pop(0)
        if isinstance(turn, BaseException):
            raise turn
        return turn


class _MCP:
    def call_tool(self, name, arguments):
        return {"content": [{"type": "text", "text": "ok"}], "isError": False}


def test_context_overflow_during_model_turn_returns_context_overflow_agent_result():
    task = _task()
    tool = ToolCall("c", task.selected_tools[0], {})
    route = _Route([_turn(calls=[tool]), ContextOverflowError("context length exceeded")])

    result = run_agent(task, route, _MCP(), _tools(task), effort="low")

    assert result.finish_reason == "context_overflow"
    assert result.usage == {"input": 100, "cache_read": 0, "cache_write": 0, "output": 10, "reasoning": 0}
    assert sum(event["type"] == "turn" for event in result.events) == 1
    assert {"type": "context_overflow", "turn": 2} in result.events
    assert len(route.calls) == 2


def test_context_overflow_during_summarization_returns_context_overflow_agent_result():
    task = _task()
    tool = ToolCall("c", task.selected_tools[0], {})
    route = _Route(
        [
            _turn(calls=[tool], usage=Usage(input=180_000, output=10)),
            ContextOverflowError("context length exceeded"),
        ]
    )

    result = run_agent(task, route, _MCP(), _tools(task), effort="low")

    assert result.finish_reason == "context_overflow"
    assert result.usage["input"] == 180_000
    assert sum(event["type"] == "turn" for event in result.events) == 1
    assert {"type": "context_overflow", "turn": 1} in result.events
    assert not any(event["type"] == "context_summarization" for event in result.events)
    assert len(route.calls) == 2


def test_text_only_turn_has_no_continue_message():
    task = _task()
    route = _Route([_turn(text="thinking aloud"), _turn(calls=[ToolCall("f", "finish", {"reason": "done"})])])
    run_agent(task, route, _MCP(), _tools(task), effort="low")
    assert any(message.get("content") == "Please continue the task" for message in route.calls[1])


def test_turn_warning_lost_when_summarization_runs():
    task = _task()
    tool = ToolCall("c", task.selected_tools[0], {})
    turns = [_turn(calls=[tool]) for _ in range(84)]
    turns.extend(
        (
            _turn(calls=[tool], usage=Usage(input=180_000, output=10)),
            _turn(text="summary"),
            _turn(calls=[ToolCall("f", "finish", {"reason": "done"})]),
        )
    )
    route = _Route(turns)
    run_agent(task, route, _MCP(), _tools(task), effort="low")
    after_summary = route.calls[-1]
    assert any("turns remaining" in str(message.get("content", "")).lower() for message in after_summary)


def test_tool_result_text_is_json_envelope():
    task = _task()
    turn = _turn(calls=[ToolCall("c", task.selected_tools[0], {})])
    messages, *_ = _tool_results(turn, _MCP(), lambda: 0.0)
    assert messages[0]["content"] == "ok"


def test_non_object_tool_arguments_return_tool_errors():
    task = _task()
    turn = _turn(
        calls=[
            ToolCall("invalid-tool", task.selected_tools[0], ["not", "an", "object"]),
            ToolCall("invalid-finish", "finish", "not an object"),
        ]
    )
    messages, finished, reason, latencies = _tool_results(turn, _MCP(), lambda: 0.0)
    assert [message["content"] for message in messages] == [
        "Error: Invalid parameters",
        "Error: Invalid parameters",
    ]
    assert all(message["is_error"] for message in messages)
    assert not finished
    assert not reason
    assert not latencies


def test_summarization_trigger_double_counts_cached_tokens():
    task = _task()
    tool = ToolCall("c", task.selected_tools[0], {})
    usage = Usage(input=100_000, cache_read=90_000, output=1_000)
    route = _Route(
        [_turn(calls=[tool], usage=usage), _turn(text="summary"), _turn(calls=[ToolCall("f", "finish", {})])]
    )
    result = run_agent(task, route, _MCP(), _tools(task), effort="low")
    assert not any(event["type"] == "context_summarization" for event in result.events)


def test_openai_cost_double_counts_cached_tokens():
    billed = cost_usd("gpt-6-luna", input_tokens=100_000, cache_read_tokens=90_000)
    assert billed == 0.0019


def test_verifier_sql_error_becomes_infra_failed():
    task = _task()
    config = task.gym_servers_config[0]
    gym = GymClient(
        config["mcp_server_url"],
        name=config["mcp_server_name"],
        transport=httpx.MockTransport(lambda _: httpx.Response(400, json={"detail": "invalid SQL"})),
    )
    result = gym.verify(task, {item["mcp_server_name"]: "db" for item in task.gym_servers_config})
    assert result.success is False
    assert result.passed and not any(result.passed)


def test_untracked_run_files_block_manifest(tmp_path: Path):
    package = Path(__file__).resolve().parents[1]
    ignore = package / ".gitignore"
    (tmp_path / ".gitignore").write_text(ignore.read_text())
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", ".gitignore"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-q",
            "-m",
            "initial",
        ],
        check=True,
    )
    run_dir = tmp_path / "results" / "l2" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "agent.json").write_text("{}")
    baseline_path = tmp_path.parent / f"{tmp_path.name}-noop-baseline.jsonl"
    baseline_path.write_text("")
    manifest = freeze_manifest(repo=tmp_path, tasks=(), image_digests={}, split={}, noop_baseline_path=baseline_path)
    assert manifest.git_commit


def test_teams_patch_preflight_requires_the_active_patch_marker(monkeypatch):
    patch_sha256 = preflight.teams_townhall_patch_sha256()
    marker = f"{preflight.TOWNHALL_PATCH_MARKER_PREFIX}{patch_sha256}"
    monkeypatch.setattr(
        preflight.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout=marker, stderr=""),
    )
    assert preflight.preflight_townhall_patch("eog-teams") == patch_sha256

    monkeypatch.setattr(
        preflight.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout="", stderr=""),
    )
    with pytest.raises(RuntimeError, match="not active"):
        preflight.preflight_townhall_patch("eog-teams")


def test_itsm_patch_preflight_requires_the_active_patch_marker(monkeypatch):
    patch_sha256 = preflight.itsm_opened_by_patch_sha256()
    marker = f"{preflight.ITSM_OPENED_BY_PATCH_MARKER_PREFIX}{patch_sha256}"
    monkeypatch.setattr(
        preflight.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout=marker, stderr=""),
    )
    assert preflight.preflight_itsm_opened_by_patch("eog-itsm-opened-by-patch") == patch_sha256

    monkeypatch.setattr(
        preflight.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout="", stderr=""),
    )
    with pytest.raises(RuntimeError, match="not active"):
        preflight.preflight_itsm_opened_by_patch("eog-itsm-opened-by-patch")


@pytest.mark.parametrize(("stdout", "expected"), [("65536\n", 65536), ("unlimited\n", preflight.sys.maxsize)])
def test_open_file_limit_accepts_required_and_unlimited_values(monkeypatch, stdout, expected):
    def fake_run(command, **kwargs):
        assert command == ["docker", "exec", "teams-container", "sh", "-c", "ulimit -n"]
        assert kwargs == {"capture_output": True, "text": True, "check": False, "timeout": 30}
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(preflight.subprocess, "run", fake_run)
    assert preflight.open_file_limit("teams-container") == expected


def test_open_file_limit_rejects_a_limit_below_the_requirement(monkeypatch):
    task = _preflight_selected_tool_fixture(
        monkeypatch,
        selected_tool="list_team_apps",
        available_tools=("list_teams_apps",),
    )
    monkeypatch.setattr(preflight, "open_file_limit", lambda _: 1024)

    with pytest.raises(RuntimeError, match=r"Container teams-container has an open-file limit of 1024"):
        preflight.preflight_gym((task,), {"teams": "teams-container"})


def test_open_file_limit_reports_a_nonzero_exit(monkeypatch):
    monkeypatch.setattr(
        preflight.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, stdout="", stderr="docker exec failed"),
    )

    with pytest.raises(RuntimeError, match=r"teams-container.*docker exec failed"):
        preflight.open_file_limit("teams-container")


def test_open_file_limit_reports_invalid_output(monkeypatch):
    monkeypatch.setattr(
        preflight.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout="unknown", stderr="bad ulimit output"),
    )

    with pytest.raises(RuntimeError, match=r"teams-container.*bad ulimit output"):
        preflight.open_file_limit("teams-container")


def test_open_file_limit_reports_a_timeout(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], timeout=kwargs["timeout"], stderr="docker exec timed out")

    monkeypatch.setattr(preflight.subprocess, "run", timeout)

    with pytest.raises(RuntimeError, match=r"teams-container.*docker exec timed out"):
        preflight.open_file_limit("teams-container")


def _preflight_selected_tool_fixture(monkeypatch, *, selected_tool: str, available_tools: tuple[str, ...]):
    server = {
        "mcp_server_name": "gym-teams-mcp",
        "mcp_server_url": "http://teams/mcp",
    }
    task = SimpleNamespace(
        task_id="preflight-selected-tool",
        selected_tools=(selected_tool,),
        known_defect=False,
        gym_servers_config=(server,),
    )

    class FakeGymClient:
        def __init__(self, *args, **kwargs):
            pass

        def health(self):
            return True

        def close(self):
            pass

    class FakeMCPClient:
        def __init__(self, *args, **kwargs):
            pass

        def initialize(self):
            pass

        def list_tools(self):
            return [{"name": name} for name in available_tools]

        def close(self):
            pass

    monkeypatch.setattr(preflight, "EXPECTED_TOOL_COUNTS", {"teams": len(available_tools)})
    monkeypatch.setattr(preflight, "SERVER_NAMES", {"teams": "gym-teams-mcp"})
    monkeypatch.setattr(preflight, "GymClient", FakeGymClient)
    monkeypatch.setattr(preflight, "MCPClient", FakeMCPClient)
    monkeypatch.setattr(preflight, "image_digest", lambda _: "sha256:test")
    monkeypatch.setattr(preflight, "preflight_townhall_patch", lambda _: "patch-sha256")
    monkeypatch.setattr(preflight, "open_file_limit", lambda _: 65536)
    return task


def test_preflight_rejects_a_missing_selected_tool(monkeypatch):
    task = _preflight_selected_tool_fixture(
        monkeypatch,
        selected_tool="remove_channel_member",
        available_tools=("remove_team_member", "add_channel_member"),
    )

    with pytest.raises(RuntimeError, match=r"preflight-selected-tool.*remove_channel_member"):
        preflight.preflight_gym((task,), {"teams": "teams-container"})


def test_preflight_accepts_a_selected_tool_alias(monkeypatch):
    task = _preflight_selected_tool_fixture(
        monkeypatch,
        selected_tool="list_team_apps",
        available_tools=("list_teams_apps",),
    )

    report = preflight.preflight_gym((task,), {"teams": "teams-container"})

    assert report["teams"]["tool_count"] == 1
    assert report["teams"]["open_file_limit"] == 65536


def test_gitignore_keeps_preflight_and_backups_visible() -> None:
    package = Path(__file__).resolve().parents[1]

    def ignored(path: str) -> bool:
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "-q", path],
            cwd=package,
            check=False,
        )
        return result.returncode == 0

    assert ignored("results/l2/run-1/agent.json")
    assert not ignored("results/preflight/live.json")
    assert not ignored("results/backups/noop_baseline.jsonl")


def _preflight_reset_fixture(monkeypatch, tmp_path: Path, failure: Exception | None = None):
    task = next(item for item in load_tasks() if item.task_id == "task_20251125_152842_063_b393a327_9622ac64")
    baseline = {"task_id": task.task_id, "passes": [False] * len(task.verifiers)}
    baseline_path = tmp_path / "noop_baseline.jsonl"
    baseline_path.write_text(json.dumps(baseline) + "\n")

    seed_path = tmp_path / task.gym_servers_config[0]["seed_database_file"]
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    seed_path.write_text("seed")
    checked_tasks = []
    checked_baselines = []

    class FakeGymClient:
        def __init__(self, url: str, *, name: str, context: dict):
            self.name = name

        def seed(self, database_id: str, sql: str) -> None:
            assert sql == "seed"

        def check_reset(self, checked_task, database_ids, *, baseline_path=None):
            checked_tasks.append(checked_task)
            checked_baselines.append(baseline_path)
            if failure is not None:
                raise failure
            return VerifierResult(
                passed=tuple(baseline["passes"]),
                actual=(None,) * len(task.verifiers),
                expected=(None,) * len(task.verifiers),
                errors=(None,) * len(task.verifiers),
            )

        def delete(self, database_id: str) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(preflight, "EXPECTED_TOOL_COUNTS", {"csm": 89})
    monkeypatch.setattr(preflight, "SERVER_NAMES", {"csm": "sn-csm-server"})
    monkeypatch.setattr(preflight, "GymClient", FakeGymClient)
    return (replace(task, known_defect=True), task), task, checked_tasks, checked_baselines, baseline_path


def test_preflight_reset_accepts_all_false_results_that_match_baseline(monkeypatch, tmp_path: Path) -> None:
    tasks, task, checked_tasks, _, baseline_path = _preflight_reset_fixture(monkeypatch, tmp_path)

    assert preflight.preflight_reset(tasks, data_root=tmp_path, baseline_path=baseline_path) == {"csm": task.task_id}
    assert checked_tasks == [task]


def test_preflight_reset_passes_noop_baseline_path(monkeypatch, tmp_path: Path) -> None:
    tasks, _, _, checked_baselines, _ = _preflight_reset_fixture(monkeypatch, tmp_path)
    baseline_path = tmp_path / "overlay" / "noop_baseline.jsonl"

    preflight.preflight_reset(tasks, data_root=tmp_path, baseline_path=baseline_path)

    assert checked_baselines == [baseline_path]


def test_preflight_reset_propagates_baseline_mismatch(monkeypatch, tmp_path: Path) -> None:
    tasks, _, _, _, baseline_path = _preflight_reset_fixture(
        monkeypatch, tmp_path, InfrastructureError("baseline mismatch")
    )

    with pytest.raises(InfrastructureError, match="baseline mismatch"):
        preflight.preflight_reset(tasks, data_root=tmp_path, baseline_path=baseline_path)
