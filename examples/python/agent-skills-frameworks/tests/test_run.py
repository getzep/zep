import asyncio
from inspect import Parameter, signature
from pathlib import Path
from typing import Any

import httpx
import pytest

import adk_agent
import claude_agent_sdk_agent
import langgraph_agent
import run as run_module
from banking import make_case
from episode import EpisodeResult
from run import FRAMEWORKS, build_parser
from tools import BankingTools
from zep_skills import ZepSkills


def test_framework_choices_map_to_adapters() -> None:
    assert FRAMEWORKS == {
        "langgraph": langgraph_agent.run_episode,
        "adk": adk_agent.run_episode,
        "claude-agent-sdk": claude_agent_sdk_agent.run_episode,
    }
    parser = build_parser()
    for framework in FRAMEWORKS:
        args = parser.parse_args(["run", "--framework", framework, "--output", "runs/test"])
        assert args.framework == framework


def test_adapters_share_the_required_episode_signature() -> None:
    for adapter in FRAMEWORKS.values():
        parameters = list(signature(adapter).parameters.values())
        assert [parameter.name for parameter in parameters] == [
            "case",
            "model_name",
            "effort",
            "skills",
            "recorder",
            "teaching",
            "test_model",
        ]
        assert all(parameter.default is Parameter.empty for parameter in parameters[:6])
        assert parameters[6].kind is Parameter.KEYWORD_ONLY
        assert parameters[6].default is None


def test_run_stage_does_not_close_trace_twice_after_poll_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ZEP_API_KEY", "test-key")
    monkeypatch.setenv("ZEP_BASE_URL", "https://api.test")
    requests: list[tuple[str, str]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.method == "POST" and request.url.path.endswith("/traces"):
            response = {"uuid": "trace-uuid", "status": "open"}
        elif request.method == "POST" and request.url.path.endswith("/traces/trace-uuid/close"):
            response = {"task": {"uuid": "task-uuid"}, "trace": {"uuid": "trace-uuid"}}
        elif request.url.path.endswith("/tasks/task-uuid"):
            response = {"uuid": "task-uuid", "status": "failed"}
        else:
            pytest.fail(f"Unexpected request: {request.method} {request.url.path}")
        return httpx.Response(200, json=response)

    zep = ZepSkills(
        tmp_path / "zep-calls.jsonl",
        "test",
        transport=httpx.MockTransport(handle),
    )
    case = make_case(10134)

    async def scripted_episode(*_: Any) -> EpisodeResult:
        return EpisodeResult("Done.", 0, BankingTools(case, []))

    monkeypatch.setitem(run_module.FRAMEWORKS, "test", scripted_episode)
    try:
        with pytest.raises(RuntimeError, match="Trace close Task ended in failed"):
            asyncio.run(
                run_module.run_stage(
                    tmp_path / "teacher",
                    zep,
                    "agent-uuid",
                    case,
                    "test-model",
                    "none",
                    [],
                    True,
                    "test",
                )
            )
    finally:
        zep.close()

    close_requests = [
        request
        for request in requests
        if request[0] == "POST" and request[1].endswith("/traces/trace-uuid/close")
    ]
    assert len(close_requests) == 1
