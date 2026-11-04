from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import eog.ledger as ledger_module
from eog.agent import build_agent_config
from eog.dataset import (
    CONTEXT_TOKEN_NOT_IN_SEED_TASK_IDS,
    MISSING_SELECTED_TOOL_TASK_IDS,
    TYPE_MISMATCH_GOLD_TASK_IDS,
    load_tasks,
    task_list_hash,
    validate_verifier_only_overlay,
)
from eog.errors import (
    BudgetExceeded,
    ContextOverflowError,
    InfrastructureError,
    NonRetryableProviderError,
    PolicyFlagError,
    RetryExhaustedError,
)
from eog.gym import GymClient
from eog.ledger import CostLedger, JsonlLedger
from eog.mcp_client import MCPClient
from eog.prices import cost_usd, prompt_token_count
from eog.providers.anthropic_route import AnthropicRoute
from eog.providers.base import Usage
from eog.providers.openai_route import OpenAIRoute
from eog.providers.zai_route import ZAIRoute
from eog.retry import BASE_DELAY, MAX_ATTEMPTS, MAX_DELAY, call_with_retry, classify_error, failure_details
from eog.run import CompositeMCPClient, _claude_task_fields, _teacher_reward
from eog.runner import (
    RunManifest,
    RunStore,
    execute_episode,
    freeze_manifest,
    interleave_arms,
    run_concurrent,
    task_row_count,
)
from eog.split import _clusters, assert_skill_sources, make_split
from eog.stirrup_constants import (
    BASE_SYSTEM_PROMPT_TEMPLATE,
    FINISH_TOOL_SCHEMA,
    STIRRUP_COMMIT,
    TURNS_REMAINING_WARNING_THRESHOLD,
    turns_remaining_warning,
)


def task() -> object:
    return next(item for item in load_tasks() if item.domain == "calendar")


def test_dataset_manifest_and_known_defects() -> None:
    tasks = load_tasks()
    assert len(tasks) == 649
    assert len(task_list_hash(tasks)) == 64
    assert sum(item.known_defect for item in tasks) == 24
    assert sum(item.known_defect for item in tasks if item.type_mismatch_gold) == 19
    assert all(item.type_mismatch_gold for item in tasks if item.task_id in TYPE_MISMATCH_GOLD_TASK_IDS)
    assert all(
        item.task_id.endswith(("cd2083a8", "8e21a82f"))
        for item in tasks
        if item.known_defect_reasons == ("verifier_schema",)
    )
    assert set(CONTEXT_TOKEN_NOT_IN_SEED_TASK_IDS) == {
        item.task_id for item in tasks if item.known_defect_reasons == ("context_token_not_in_seed",)
    }


def test_load_tasks_layers_corrected_overlay_on_revised_tasks(tmp_path: Path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "task_id": "task_revised_only",
                    "domain": "calendar",
                    "system_prompt": "original revised-only prompt",
                    "user_prompt": "task",
                    "selected_tools": "[]",
                    "restricted_tools": "[]",
                    "mcp_endpoint": "/mcp",
                    "number_of_runs": 1,
                    "reset_database_between_runs": True,
                    "gym_servers_config": "[]",
                    "verifiers": '[{"check":"original"}]',
                },
                {
                    "task_id": "task_in_both",
                    "domain": "calendar",
                    "system_prompt": "original shared prompt",
                    "user_prompt": "task",
                    "selected_tools": "[]",
                    "restricted_tools": "[]",
                    "mcp_endpoint": "/mcp",
                    "number_of_runs": 1,
                    "reset_database_between_runs": True,
                    "gym_servers_config": "[]",
                    "verifiers": '[{"check":"original"}]',
                },
            ]
        ),
        data_dir / "tasks.parquet",
    )
    revised_dir = tmp_path / "revised"
    revised_dir.mkdir()
    (revised_dir / "task_revised_only.json").write_text(
        json.dumps({"task_id": "task_revised_only", "system_prompt": "revised-only prompt"})
    )
    (revised_dir / "task_in_both.json").write_text(
        json.dumps(
            {
                "task_id": "task_in_both",
                "system_prompt": "revised shared prompt",
                "verifiers": [{"check": "revised"}],
            }
        )
    )
    overlay_dir = tmp_path / "overlay"
    overlay_dir.mkdir()
    (overlay_dir / "task_in_both.json").write_text(
        json.dumps(
            {
                "task_id": "task_in_both",
                "verifiers": [{"check": "corrected"}],
            }
        )
    )

    original_tasks = load_tasks(data_dir=data_dir, revised_dir=revised_dir)
    corrected_tasks = load_tasks(data_dir=data_dir, overlay_dir=overlay_dir, revised_dir=revised_dir)
    original_by_id = {task.task_id: task for task in original_tasks}
    corrected_by_id = {task.task_id: task for task in corrected_tasks}

    assert original_by_id["task_revised_only"].system_prompt == "revised-only prompt"
    assert corrected_by_id["task_revised_only"].system_prompt == "revised-only prompt"
    assert corrected_by_id["task_in_both"].system_prompt == "revised shared prompt"
    assert original_by_id["task_in_both"].verifiers == ({"check": "revised"},)
    assert corrected_by_id["task_in_both"].verifiers == ({"check": "corrected"},)
    validate_verifier_only_overlay(original_tasks, corrected_tasks)


def test_missing_selected_tool_task_is_known_defect() -> None:
    task_id = "task_20251128_120055_076_8e9e30d7_e5dcfa22"
    task = next(item for item in load_tasks() if item.task_id == task_id)

    assert frozenset({task_id}) == MISSING_SELECTED_TOOL_TASK_IDS
    assert task.known_defect
    assert task.known_defect_reasons == ("selected_tool_missing",)
    assert make_split(load_tasks())["tasks"][task_id]["side"] == "score"


def test_type_mismatch_gold_list_has_eight_teach_and_eleven_score_tasks() -> None:
    tasks = {item.task_id: item for item in load_tasks()}
    split = make_split(tuple(tasks.values()))
    assert len(TYPE_MISMATCH_GOLD_TASK_IDS) == 19
    assert {tasks[task_id].known_defect_reasons for task_id in TYPE_MISMATCH_GOLD_TASK_IDS} == {("type_mismatch_gold",)}
    sides = [split["tasks"][task_id]["side"] for task_id in TYPE_MISMATCH_GOLD_TASK_IDS]
    assert sides.count("teach") == 8
    assert sides.count("score") == 11


def test_context_token_defects_have_the_expected_ids_and_reasons() -> None:
    tasks = {item.task_id: item for item in load_tasks()}
    assert {
        "task_20251205_032409_945_0a0bf089_61f5be29",
        "task_20260112_125407_584_1628b966_f5dd38b6",
    } == CONTEXT_TOKEN_NOT_IN_SEED_TASK_IDS
    assert {tasks[task_id].domain for task_id in CONTEXT_TOKEN_NOT_IN_SEED_TASK_IDS} == {"calendar", "hybrid"}
    assert {tasks[task_id].known_defect_reasons for task_id in CONTEXT_TOKEN_NOT_IN_SEED_TASK_IDS} == {
        ("context_token_not_in_seed",)
    }
    assert all(tasks[task_id].known_defect for task_id in CONTEXT_TOKEN_NOT_IN_SEED_TASK_IDS)


def test_stirrup_constants_match_pinned_checkout() -> None:
    root = Path.home() / "work" / "Stirrup"
    if not root.exists():
        pytest.skip("Stirrup source checkout is not available")
    assert os.popen(f"git -C {root} rev-parse HEAD").read().strip() == STIRRUP_COMMIT
    assert (root / "src/stirrup/prompts/base_system_prompt.txt").read_text() == BASE_SYSTEM_PROMPT_TEMPLATE
    assert "properties" in FINISH_TOOL_SCHEMA
    assert TURNS_REMAINING_WARNING_THRESHOLD == 20
    assert turns_remaining_warning(1) == "This is the last turn. Please finish the task by calling a finish tool."


def test_no_skills_arm_is_identical_to_reproduction_config() -> None:
    tasks = [next(item for item in load_tasks() if item.domain == domain) for domain in ("calendar", "csm", "teams")]
    for item in tasks:
        tools = [{"name": name, "inputSchema": {"type": "object"}} for name in item.selected_tools]
        reproduction = build_agent_config(item, tools)
        none_arm = build_agent_config(item, tools, arm="none")
        assert none_arm == reproduction
        with_skills = build_agent_config(item, tools, arm="skills_decided", skills_block="Reusable procedure.")
        assert with_skills.system == f"{none_arm.system}\n\nReusable procedure."
        empty_skills = build_agent_config(item, tools, arm="skills", skills_block="")
        assert empty_skills.system == none_arm.system
        assert empty_skills.tools == none_arm.tools
        assert with_skills.tools == none_arm.tools
        assert (none_arm.max_turns, none_arm.model_timeout, none_arm.episode_timeout) == (100, 600, 2700)


def test_oracle_tool_filter_and_missing_tool() -> None:
    item = task()
    selected = [{"name": name, "inputSchema": {}} for name in item.selected_tools]
    selected.append({"name": "admin_delete_everything", "inputSchema": {}})
    config = build_agent_config(item, selected)
    assert [tool["name"] for tool in config.tools] == ["finish", *item.selected_tools]
    with pytest.raises(InfrastructureError):
        build_agent_config(item, [])


def test_verifier_sql_error_is_a_failed_verifier_without_raw_body() -> None:
    item = task()
    server = item.gym_servers_config[0]
    body = "bad verifier query; secret task payload"
    transport = httpx.MockTransport(lambda request: httpx.Response(400, text=body))
    gym = GymClient(server["mcp_server_url"], name=server["mcp_server_name"], transport=transport)
    result = gym.verify(item, {server["mcp_server_name"]: "db"})
    assert not result.success
    assert (
        result.errors[0]
        == f"status=400 error_class=HTTPStatusError body_sha256={hashlib.sha256(body.encode()).hexdigest()}"
    )
    assert "secret task payload" not in result.errors[0]


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 429])
def test_non_sql_verifier_4xx_is_an_infrastructure_failure(status_code: int) -> None:
    item = task()
    server = item.gym_servers_config[0]
    transport = httpx.MockTransport(lambda request: httpx.Response(status_code, text="request failed"))
    gym = GymClient(server["mcp_server_url"], name=server["mcp_server_name"], transport=transport)
    with pytest.raises(InfrastructureError, match="Gym SQL query failed"):
        gym.verify(item, {server["mcp_server_name"]: "db"})


def test_verifier_transport_error_is_an_infrastructure_failure() -> None:
    item = task()
    server = item.gym_servers_config[0]

    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    gym = GymClient(server["mcp_server_url"], name=server["mcp_server_name"], transport=httpx.MockTransport(fail))
    with pytest.raises(InfrastructureError, match="Gym SQL query failed"):
        gym.verify(item, {server["mcp_server_name"]: "db"})


@pytest.mark.parametrize("status_code", [500, 503])
def test_verifier_sql_5xx_is_infrastructure_failure(status_code: int) -> None:
    item = task()
    server = item.gym_servers_config[0]
    transport = httpx.MockTransport(lambda request: httpx.Response(status_code))
    gym = GymClient(server["mcp_server_url"], name=server["mcp_server_name"], transport=transport)
    with pytest.raises(InfrastructureError, match="Gym SQL query failed"):
        gym.verify(item, {server["mcp_server_name"]: "db"})


def test_http_retry_and_failure_ledger_use_real_http_client(tmp_path: Path) -> None:
    attempts = []
    ledger = JsonlLedger(tmp_path / "failures.jsonl")
    for code, should_retry in ((1302, True), (1113, False), (500, True), (400, False)):
        attempts.clear()

        def handler(request: httpx.Request, code: int = code) -> httpx.Response:
            attempts.append(request)
            return httpx.Response(429 if code in {1302, 1113} else code, json={"error": {"code": code}})

        client = httpx.Client(transport=httpx.MockTransport(handler))

        def request(client: httpx.Client = client) -> httpx.Response:
            response = client.get("https://provider.test")
            response.raise_for_status()
            return response

        if should_retry:
            with pytest.raises(RetryExhaustedError):
                call_with_retry(
                    request, route="test", ledger=ledger, max_attempts=2, sleep=lambda _: None, uniform=lambda *_: 0
                )
            assert len(attempts) == 2
        else:
            with pytest.raises(NonRetryableProviderError):
                call_with_retry(request, route="test", ledger=ledger)
            assert len(attempts) == 1
        client.close()
    failures = [json.loads(line) for line in (tmp_path / "failures.jsonl").read_text().splitlines()]
    assert all({"http_status", "provider_code", "error_message"} <= failure.keys() for failure in failures)


@pytest.mark.parametrize(
    ("status_code", "body", "expected"),
    (
        (
            400,
            {"error": {"code": "context_length_exceeded", "message": "context length exceeded"}},
            (False, "context_overflow", 400, "context_length_exceeded"),
        ),
        (
            400,
            {"error": {"code": 1261, "message": "Prompt too long"}},
            (False, "context_overflow", 400, 1261),
        ),
        (
            400,
            {
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": "prompt is too long: 210000 tokens > 200000 maximum",
                },
            },
            (False, "context_overflow", 400, None),
        ),
        (
            400,
            {
                "error": {
                    "message": (
                        "Upstream error from DeepInfra: This model's maximum context length is 262144 tokens. "
                        "However, you requested 32768 output tokens and your prompt contains at least "
                        "229377 input tokens."
                    )
                }
            },
            (False, "context_overflow", 400, None),
        ),
        (
            400,
            {"error": {"message": "Invalid tool schema"}},
            (False, "http_nonretryable", 400, None),
        ),
        (
            500,
            {
                "error": {
                    "message": (
                        "Upstream error from DeepInfra: This model's maximum context length is 262144 tokens. "
                        "However, you requested 32768 output tokens and your prompt contains at least "
                        "229377 input tokens."
                    )
                }
            },
            (True, "http_retryable", 500, None),
        ),
    ),
    ids=(
        "openai-context-code",
        "zai-context-code",
        "anthropic-context-message",
        "deepinfra-context-message",
        "generic-400",
        "context-text-500",
    ),
)
def test_classify_context_overflow_and_http_errors(
    status_code: int,
    body: dict,
    expected: tuple[bool, str, int, int | str | None],
) -> None:
    request = httpx.Request("POST", "https://provider.test")
    response = httpx.Response(status_code, json=body, request=request)
    error = httpx.HTTPStatusError(str(status_code), request=request, response=response)

    assert classify_error(error) == expected


def test_context_overflow_stops_after_one_attempt_and_records_failure(tmp_path: Path) -> None:
    request = httpx.Request("POST", "https://provider.test")
    response = httpx.Response(
        400,
        json={"error": {"code": "context_length_exceeded", "message": "context length exceeded"}},
        request=request,
    )
    error = httpx.HTTPStatusError("400", request=request, response=response)
    attempts = 0
    ledger = CostLedger(tmp_path / "context-overflow.jsonl")

    def fail() -> None:
        nonlocal attempts
        attempts += 1
        raise error

    with pytest.raises(ContextOverflowError, match="context length"):
        call_with_retry(fail, route="openrouter", ledger=ledger, max_attempts=5)

    assert attempts == 1
    failure = ledger.rows()[0]
    assert failure["error_class"] == "context_overflow"
    assert failure["http_status"] == 400


def test_zai_context_overflow_code_1261_raises_context_overflow_error() -> None:
    body = {"error": {"code": "1261", "message": "Prompt too long"}}
    transport = httpx.MockTransport(lambda _: httpx.Response(400, json=body))

    with httpx.Client(transport=transport) as client:
        route = ZAIRoute(
            "glm-5.2",
            api_key="test-key",
            client=client,
            endpoint="https://provider.test",
        )
        with pytest.raises(ContextOverflowError):
            route.complete("system", [], [], effort="low", task_id="context-overflow", attempt_limit=1)


def test_failure_details_redact_keys_and_task_payload() -> None:
    request = httpx.Request("POST", "https://provider.test")
    response = httpx.Response(
        400,
        json={
            "error": {
                "code": "invalid_request",
                "message": ("Request failed; prompt=confidential task text; authorization=Bearer sk-test-secret-value"),
            }
        },
        request=request,
    )
    details = failure_details(httpx.HTTPStatusError("400", request=request, response=response))
    assert details["http_status"] == 400
    assert details["provider_code"] == "invalid_request"
    assert "confidential task text" not in details["error_message"]
    assert "sk-test-secret-value" not in details["error_message"]
    assert len(details["error_message"]) <= 300


def test_retry_exhaustion_records_attempts_and_uses_thirty_attempt_policy() -> None:
    assert (MAX_ATTEMPTS, BASE_DELAY, MAX_DELAY) == (30, 2.0, 120.0)
    attempts = []
    context = {"attempt_failures": [], "model_call_latencies": []}

    def fail() -> None:
        attempts.append(1)
        raise httpx.HTTPStatusError(
            "rate limited",
            request=httpx.Request("POST", "https://provider.test"),
            response=httpx.Response(429, request=httpx.Request("POST", "https://provider.test")),
        )

    with pytest.raises(RetryExhaustedError):
        call_with_retry(
            fail,
            route="test",
            ledger=None,
            context=context,
            max_attempts=3,
            sleep=lambda _: None,
            uniform=lambda *_: 0,
        )
    assert len(attempts) == 3
    assert len(context["attempt_failures"]) == 3
    assert len(context["model_call_latencies"]) == 3
    assert all(row["http_status"] == 429 for row in context["attempt_failures"])


def test_retry_after_cost_ledger_and_sdk_retries(tmp_path: Path) -> None:
    delay = []
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            return httpx.Response(429, headers={"retry-after": "3"}, json={"error": "slow down"})
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))

    def request() -> httpx.Response:
        response = client.get("https://provider.test")
        response.raise_for_status()
        return response

    response = call_with_retry(
        request,
        route="test",
        ledger=None,
        sleep=delay.append,
    )
    assert response.status_code == 200 and delay == [3.0]
    assert AnthropicRoute("claude-opus-5-5", api_key="test").client.max_retries == 0
    assert OpenAIRoute(api_key="test").client.max_retries == 0
    assert ZAIRoute("glm-5.2", api_key="test").client.timeout.connect == 600
    assert cost_usd("gpt-6-luna", input_tokens=1000, cache_read_tokens=1000, cache_write_tokens=1000) > 0
    ledger = CostLedger(tmp_path / "cost.jsonl", max_usd=0)
    with pytest.raises(BudgetExceeded):
        ledger.append_call({"model": "gpt-6-luna", "input_tokens": 1})


def test_provider_prompt_size_and_cached_input_costs() -> None:
    assert prompt_token_count("claude-haiku-5-5", input_tokens=80_000, cache_read_tokens=20_000) == 100_000
    assert prompt_token_count("gpt-6-luna", input_tokens=80_000, cache_read_tokens=70_000) == 80_000
    assert prompt_token_count("glm-5.2", input_tokens=80_000, cache_read_tokens=70_000) == 80_000
    assert cost_usd("gpt-6-luna", input_tokens=100_000, cache_read_tokens=90_000) == pytest.approx(0.0019)
    assert cost_usd("glm-5.2", input_tokens=100_000, cache_read_tokens=90_000) == pytest.approx(0.0374)


def test_invalid_prompt_attempt_not_in_failure_ledger() -> None:
    failures = []
    ledger = SimpleNamespace(append_failure=failures.append)
    request = httpx.Request("POST", "https://provider.test")
    response = httpx.Response(400, json={"error": {"code": "invalid_prompt"}}, request=request)
    with pytest.raises(PolicyFlagError, match="invalid_prompt"):
        call_with_retry(
            lambda: (_ for _ in ()).throw(httpx.HTTPStatusError("400", request=request, response=response)),
            route="test",
            ledger=ledger,
        )
    assert failures[0]["error_class"] == "invalid_prompt"


def test_503_with_balancer_text_is_not_retried() -> None:
    attempts = 0
    request = httpx.Request("POST", "https://provider.test")
    response = httpx.Response(
        503,
        json={"error": {"message": "upstream load balancer unavailable"}},
        request=request,
    )
    error = httpx.HTTPStatusError("503", request=request, response=response)
    from eog.retry import call_with_retry

    def call() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise error
        return "ok"

    assert (
        call_with_retry(
            call,
            route="test",
            ledger=None,
            max_attempts=2,
            sleep=lambda _: None,
            uniform=lambda *_: 0,
        )
        == "ok"
    )
    assert attempts == 2


def test_deadline_timeout_is_recorded_as_unbilled(monkeypatch: pytest.MonkeyPatch) -> None:
    import eog.retry as retry_module

    now = 0.0
    monkeypatch.setattr(retry_module.time, "monotonic", lambda: now)
    failures = []
    attempts = 0
    ledger = SimpleNamespace(append_failure=failures.append)
    context = {"attempt_failures": []}

    def call() -> None:
        nonlocal attempts, now
        attempts += 1
        now = 1.0
        raise httpx.TimeoutException("timed out")

    def uniform(*_args: float) -> float:
        nonlocal now
        now = 5.0
        return 0.0

    with pytest.raises(RetryExhaustedError, match="wall-clock limit"):
        call_with_retry(
            call,
            route="test",
            ledger=ledger,
            context=context,
            max_attempts=2,
            uniform=uniform,
            sleep=lambda _: None,
            deadline_monotonic=5,
        )

    assert attempts == 1
    assert failures[-1]["error_class"] == "unbilled_timeout"
    assert failures[-1]["billing_status"] == "unbilled_timeout"
    assert failures[-1]["cost_usd"] is None


def test_retry_uses_remaining_deadline_for_each_request(monkeypatch: pytest.MonkeyPatch) -> None:
    import eog.retry as retry_module

    now = 0.0
    monkeypatch.setattr(retry_module.time, "monotonic", lambda: now)
    timeouts = []
    calls = 0

    def request(timeout: float) -> str:
        nonlocal calls
        calls += 1
        timeouts.append(timeout)
        if calls == 1:
            raise httpx.TimeoutException("timed out")
        return "ok"

    def uniform(*_args: float) -> float:
        nonlocal now
        now = 3.0
        return 0.0

    assert (
        call_with_retry(
            request,
            route="test",
            ledger=None,
            max_attempts=2,
            sleep=lambda _: None,
            uniform=uniform,
            deadline_monotonic=5.0,
            model_timeout=600.0,
        )
        == "ok"
    )
    assert timeouts == [5.0, 2.0]


def test_retry_does_not_start_request_with_less_than_one_second(monkeypatch: pytest.MonkeyPatch) -> None:
    import eog.retry as retry_module

    monkeypatch.setattr(retry_module.time, "monotonic", lambda: 0.0)
    failures = []
    calls = []
    row = execute_episode(
        task(),
        lambda: call_with_retry(
            lambda timeout: calls.append(timeout),
            route="test",
            ledger=SimpleNamespace(append_failure=failures.append),
            deadline_monotonic=0.5,
            model_timeout=600.0,
        ),
    )
    assert calls == []
    assert failures[-1]["reason"] == "wall_clock_limit"
    assert row["status"] == "infra_failed"
    assert row["reason"] == "wall_clock_limit"


def test_capture_redacts_secret_fields(tmp_path: Path) -> None:
    route = AnthropicRoute("claude-opus-5-5", api_key="fake-secret", capture_dir=tmp_path)
    from eog.providers.base import capture_body

    capture_body(tmp_path, "test", {"api_key": "fake-secret", "authorization": "Bearer fake-secret"})
    payload = (tmp_path / "test.json").read_text()
    assert "fake-secret" not in payload
    assert route.client.max_retries == 0


def test_cost_rows_copy_only_bounded_call_context() -> None:
    from eog.providers.base import record_call

    class Ledger:
        row = None

        def append_call(self, row: dict) -> None:
            self.row = row

    ledger = Ledger()
    record_call(
        ledger,
        model="glm-5.2",
        route="zai",
        usage=Usage(input=3, output=4),
        latency=0.2,
        context={
            "cell": "executor",
            "arm": "none",
            "task": "task-1",
            "trial": 1,
            "attempt": 2,
            "model_call_latencies": [0.2],
            "attempt_failures": [{"http_status": 429}],
        },
    )
    assert {key: ledger.row[key] for key in ("cell", "arm", "task", "trial", "attempt")} == {
        "cell": "executor",
        "arm": "none",
        "task": "task-1",
        "trial": 1,
        "attempt": 2,
    }
    assert "model_call_latencies" not in ledger.row
    assert "attempt_failures" not in ledger.row


def test_anthropic_usage_keeps_missing_reasoning_tokens_and_preflight_uses_output() -> None:
    from types import SimpleNamespace

    from eog.effort_preflight import evaluate_effort_preflight

    class FakeMessages:
        def __init__(self) -> None:
            self.usage = {
                "input_tokens": 12,
                "output_tokens": 8,
                "cache_read_input_tokens": 3,
                "cache_creation_input_tokens": 0,
            }

        def create(self, **kwargs):
            return SimpleNamespace(
                content=[],
                usage=SimpleNamespace(model_dump=lambda: self.usage),
            )

        @property
        def with_raw_response(self) -> SimpleNamespace:
            return SimpleNamespace(
                create=lambda **kwargs: SimpleNamespace(status_code=200, parse=lambda: self.create(**kwargs))
            )

    messages = FakeMessages()
    route = AnthropicRoute("claude-opus-5-5", client=SimpleNamespace(messages=messages))
    turn = route.complete(
        "System prompt",
        [{"role": "user", "content": "ready"}],
        [],
        effort="low",
        task_id="anthropic-usage",
    )
    assert turn.usage.reasoning is None
    assert turn.usage.output == 8
    messages.usage = {
        "input_tokens": 12,
        "output_tokens": 8,
        "cache_read_input_tokens": 3,
        "cache_creation_input_tokens": 0,
        "output_tokens_details": {"thinking_tokens": 5},
    }
    turn_with_reasoning = route.complete(
        "System prompt",
        [{"role": "user", "content": "ready"}],
        [],
        effort="high",
        task_id="anthropic-usage",
    )
    assert turn_with_reasoning.usage.reasoning == 5
    rows = [
        {"effort": level, "reasoning_tokens": None, "output_tokens": output}
        for level, values in (
            ("low", (5, 6, 7)),
            ("medium", (8, 9, 10)),
            ("high", (11, 12, 13)),
            ("max", (14, 15, 16)),
        )
        for output in values
    ]
    report = evaluate_effort_preflight("claude-opus-5-5", rows)
    assert report["passed"]
    assert report["medians"]["max"] == 15


def test_cost_ledger_updates_and_checks_limit_under_one_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class LockCheckingLedger(CostLedger):
        check_lock = False

        @property
        def total_usd(self) -> float:
            if self.check_lock:
                assert self._lock.locked()
            return self._total_usd

        @total_usd.setter
        def total_usd(self, value: float) -> None:
            if self.check_lock:
                assert self._lock.locked()
            self._total_usd = value

    monkeypatch.setattr(ledger_module, "cost_usd", lambda *args, **kwargs: 0.01)
    ledger = LockCheckingLedger(tmp_path / "concurrent-costs.jsonl", max_usd=0.1)
    ledger.check_lock = True

    def append_call(index: int) -> None:
        with suppress(BudgetExceeded):
            ledger.append_call({"model": "test", "call": index})

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(append_call, range(20)))

    ledger.check_lock = False
    assert len(ledger.rows()) == 20
    assert ledger.total_usd == pytest.approx(0.2)


def test_mcp_tools_for_returns_only_tools_on_that_server() -> None:
    client = MCPClient("https://gym.test/mcp", database_id="db")
    client.list_tools = lambda: [{"name": "search_cases"}, {"name": "create_event"}]
    task = SimpleNamespace(selected_tools=["create_event", "search_cases", "missing_elsewhere"])
    assert [tool["name"] for tool in client.tools_for(task)] == ["create_event", "search_cases"]
    client.close()


def test_mcp_tool_alias_keeps_public_name_and_calls_remote_name() -> None:
    client = MCPClient("https://gym.test/mcp", database_id="db")
    client.initialize = lambda: None
    client.list_tools = lambda: [
        {"name": "list_teams_apps", "inputSchema": {"type": "object", "properties": {"_filter": {"type": "string"}}}}
    ]
    calls = []
    client.call_tool = lambda name, arguments: (
        calls.append((name, arguments))
        or {
            "content": [{"type": "text", "text": "apps"}],
            "isError": False,
        }
    )
    arguments = {"_filter": "distributionMethod eq 'organization'"}
    composite = CompositeMCPClient({"teams": client})
    tools = composite.start(SimpleNamespace(selected_tools=["list_team_apps"]))
    assert tools == [
        {
            "name": "teams__list_team_apps",
            "inputSchema": {"type": "object", "properties": {"_filter": {"type": "string"}}},
        }
    ]
    assert composite.call_tool("teams__list_team_apps", arguments)["isError"] is False
    assert calls == [("list_teams_apps", arguments)]
    client.close()


def test_cli_infra_failure_details_are_bounded_redacted_and_written_to_ledgers(tmp_path: Path) -> None:
    cause = ValueError("ANTHROPIC_API_KEY=sk-ant-testsecret123456")
    error = InfrastructureError("Claude SDK startup failed")
    error.__cause__ = cause
    details = failure_details(
        error,
        cli_stderr_tail="Bearer sk-ant-testsecret123456 " + ("x" * 5000),
    )
    assert details["underlying_exception_class"] == "ValueError"
    assert "testsecret" not in details["underlying_exception_message"]
    assert len(details["underlying_exception_message"]) <= 300
    assert "testsecret" not in details["cli_stderr_tail"]
    assert len(details["cli_stderr_tail"]) <= 4000

    store = RunStore(tmp_path)
    store.checkpoint({"task_id": "task-1", "status": "infra_failed", **details})
    for filename in ("rows.jsonl", "infra_failed.jsonl"):
        row = json.loads((tmp_path / filename).read_text())
        assert row["underlying_exception_class"] == "ValueError"
        assert "testsecret" not in row["cli_stderr_tail"]


def test_task_row_count_ignores_ramp_records() -> None:
    assert task_row_count([{"status": "valid"}, {"status": "ramp", "concurrency": 1}]) == 1


def test_mcp_timeout_is_infrastructure_error_without_retry() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ReadTimeout("timeout", request=request)

    client = MCPClient("https://gym.test/mcp", database_id="db", transport=httpx.MockTransport(handler))
    with pytest.raises(InfrastructureError):
        client.call_tool("mutate", {})
    assert len(calls) == 1


def test_mcp_json_rpc_tool_error_is_an_error_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "error": {"code": -32602, "message": "Invalid parameters"},
            },
        )

    client = MCPClient("https://gym.test/mcp", database_id="db", transport=httpx.MockTransport(handler))
    result = client.call_tool("update_record", {})
    assert result == {
        "content": [{"type": "text", "text": "Error: Invalid parameters"}],
        "isError": True,
    }
    client.close()


@pytest.mark.parametrize("method", ["initialize", "tools/list"])
def test_mcp_json_rpc_errors_before_tool_calls_are_infrastructure_errors(method: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "Invalid parameters"}},
        )

    client = MCPClient("https://gym.test/mcp", database_id="db", transport=httpx.MockTransport(handler))
    with pytest.raises(InfrastructureError, match="MCP protocol error"):
        if method == "initialize":
            client.initialize()
        else:
            client.list_tools()
    client.close()


@pytest.mark.parametrize("status_code", [404, 500])
def test_mcp_http_failures_are_infrastructure_errors(status_code: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code)

    client = MCPClient("https://gym.test/mcp", database_id="db", transport=httpx.MockTransport(handler))
    with pytest.raises(InfrastructureError, match="MCP transport failed"):
        client.call_tool("mutate", {})
    client.close()


def test_split_clusters_and_source_leakage() -> None:
    tasks = load_tasks()
    split = make_split(tasks)
    by_suffix = {item.task_id[-8:]: item.task_id for item in tasks}
    clusters = _clusters(tasks)
    cluster_sizes = {}
    for cluster in clusters.values():
        cluster_sizes[cluster] = cluster_sizes.get(cluster, 0) + 1
    assert (len(cluster_sizes), sum(size > 1 for size in cluster_sizes.values()), max(cluster_sizes.values())) == (
        626,
        21,
        3,
    )
    for left, right in (
        ("c6edcf9f", "abce7315"),
        ("627386b2", "320d5ca4"),
        ("71415006", "c572f123"),
        ("eef4d0c4", "9562f6db"),
    ):
        assert split["tasks"][by_suffix[left]]["cluster"] == split["tasks"][by_suffix[right]]["cluster"]
    for cluster in {row["cluster"] for row in split["tasks"].values()}:
        sides = {row["side"] for row in split["tasks"].values() if row["cluster"] == cluster}
        assert len(sides) == 1
    score_task = next(key for key, row in split["tasks"].items() if row["side"] == "score")
    with pytest.raises(ValueError):
        assert_skill_sources([{"source_task_id": score_task}], split)


def test_resume_manifest_and_interleave(tmp_path: Path) -> None:
    store = RunStore(tmp_path, resume=True)
    store.rows.append({"task_id": "a", "trial": 1, "arm": "none", "status": "valid"})
    store.rows.append({"task_id": "invalid", "trial": 1, "arm": "none", "status": "invalid"})
    store.rows.append({"task_id": "b", "trial": 1, "arm": "none", "status": "infra_failed"})
    store.rows.append({"task_id": "c", "trial": 1, "arm": "none", "status": "partial"})
    store.rows.append({"task_id": "d", "trial": 1, "arm": "none", "status": "stopped"})
    assert ("a", 1, "", "", "none", "") in store.completed_task_trials()
    assert ("invalid", 1, "", "", "none", "") in store.completed_task_trials()
    assert ("b", 1, "", "", "none", "") not in store.completed_task_trials()
    planned = [{"task_id": key, "trial": 1, "arm": "none"} for key in ("a", "invalid", "b", "c", "d")]
    assert {row["task_id"] for row in store.rows_to_run(planned)} == {"b", "c", "d"}
    store.checkpoint({"task_id": "b", "trial": 1, "arm": "none", "status": "valid"})
    assert store.superseded.path.exists()
    assert json.loads(store.superseded.path.read_text().splitlines()[0])["status"] == "superseded"
    frozen = RunManifest("a", {}, {}, "x", "", "p")
    store.write_manifest(frozen)
    with pytest.raises(ValueError):
        store.write_manifest(RunManifest("b", {}, {}, "x", "", "p"))
    annotated = json.loads(store.manifest_path.read_text())
    annotated.update({"snapshot_label": "arm-b-resumed-fixes", "snapshot_note": "a note"})
    store.manifest_path.write_text(json.dumps(annotated))
    store.write_manifest(frozen)
    assert json.loads(store.manifest_path.read_text())["snapshot_label"] == "arm-b-resumed-fixes"
    rows = [{"task_id": "a", "trial": 1, "arm": arm} for arm in ("none", "skills")] * 4
    order = interleave_arms(rows, seed=7)
    assert all({row["arm"] for row in order[i : i + 2]} == {"none", "skills"} for i in range(0, len(order), 2))


@pytest.mark.parametrize("teacher_harness", ("stirrup", "claude_code"))
def test_resume_manifest_accepts_json_normalized_tuple_cells(
    tmp_path: Path,
    teacher_harness: str,
) -> None:
    run_dir = tmp_path / teacher_harness
    run_dir.mkdir()
    manifest = RunManifest(
        "commit",
        {"tasks": 1},
        {},
        "split",
        "skills",
        "prices",
        cells=({"teacher_harness": teacher_harness},),
        teacher_harness=teacher_harness,
    )
    assert isinstance(manifest.cells, tuple)

    RunStore(run_dir).write_manifest(manifest)
    resumed_store = RunStore(run_dir, resume=True)
    resumed_store.write_manifest(replace(manifest))
    with pytest.raises(ValueError, match="The frozen run manifest changed"):
        resumed_store.write_manifest(replace(manifest, git_commit="changed"))
    resumed_store.write_manifest(replace(manifest, git_commit="changed"), allow_commit_change=True)
    record = json.loads((run_dir / "resume_commits.jsonl").read_text())
    assert (record["frozen_git_commit"], record["resume_git_commit"]) == ("commit", "changed")
    with pytest.raises(ValueError, match="The frozen run manifest changed"):
        resumed_store.write_manifest(
            replace(manifest, git_commit="changed", price_table_sha256="other"), allow_commit_change=True
        )


def test_infrastructure_rows_retry_three_times_and_stop_rows_do_not_retry() -> None:
    row = {"task_id": "task-1", "trial": 1, "arm": "none", "route": "anthropic"}
    attempts = []

    def fail(row_to_run):
        attempts.append(row_to_run.get("infra_retry", 0))
        return {**row_to_run, "status": "infra_failed"}

    results = run_concurrent([row], fail, ramp=(1,), infra_retries=3)
    assert attempts == [0, 1, 2, 3]
    assert [result["status"] for result in results if result.get("task_id")] == ["infra_failed"]

    stop_attempts = []

    def stop(row_to_run):
        stop_attempts.append(row_to_run)
        return {**row_to_run, "status": "infra_failed", "stop_run": True}

    run_concurrent([row], stop, ramp=(1,), infra_retries=3)
    assert len(stop_attempts) == 1


def test_default_runner_does_not_retry_infrastructure_failures() -> None:
    row = {"task_id": "task-1", "trial": 1, "arm": "none", "route": "anthropic"}
    attempts = []

    def fail(row_to_run):
        attempts.append(row_to_run.get("infra_retry", 0))
        return {**row_to_run, "status": "infra_failed"}

    run_concurrent([row], fail, ramp=(1,))

    assert attempts == [0]


def test_teacher_reward_scores_turn_limit_but_not_execution_error() -> None:
    assert _teacher_reward(True, "turn_limit") == 1
    assert _teacher_reward(True, "error_during_execution") == 0
    assert _teacher_reward(True, "active_time_limit") == 0
    assert _teacher_reward(True, "context_overflow") == 0


def test_claude_row_request_gate_fails_when_no_request_was_captured() -> None:
    assert _claude_task_fields({}, "claude_code")["claude_request_gate_passed"] is False
    assert _claude_task_fields({"claude_request_count": 1}, "claude_code")["claude_request_gate_passed"] is True
    fields = _claude_task_fields(
        {"model_calls": 4, "cli_num_turns": 5, "claude_request_count": 1},
        "claude_code",
    )
    assert fields["model_calls"] == 4
    assert fields["cli_num_turns"] == 5
    assert (
        _claude_task_fields(
            {"claude_request_count": 1, "claude_request_gate_failed": True},
            "claude_code",
        )["claude_request_gate_passed"]
        is False
    )


def test_stages_write_separate_run_manifests(tmp_path: Path) -> None:
    teacher = RunStore(tmp_path, manifest_name="manifest.teacher.json")
    executor = RunStore(tmp_path, manifest_name="manifest.executor.json")
    teacher.write_manifest(RunManifest("a", {}, {}, "x", "", "p", cells=({"stage": "teacher"},)))
    matrix_settings = [
        {"model": "gpt-6-luna", "effort": "low"},
        {"model": "gpt-6-luna", "effort": "medium"},
    ]
    executor.write_manifest(
        RunManifest(
            "a",
            {},
            {},
            "x",
            "",
            "p",
            cells=({"stage": "executor"},),
            matrix_settings=matrix_settings,
            skills_snapshot_file_sha256="snapshot-file-sha256",
            skills_snapshot_content_sha256="snapshot-content-sha256",
        )
    )
    assert (tmp_path / "manifest.teacher.json").is_file()
    assert (tmp_path / "manifest.executor.json").is_file()
    executor_manifest = json.loads((tmp_path / "manifest.executor.json").read_text())
    assert executor_manifest["matrix_settings"] == matrix_settings
    assert executor_manifest["skills_snapshot_file_sha256"] == "snapshot-file-sha256"
    assert executor_manifest["skills_snapshot_content_sha256"] == "snapshot-content-sha256"


def test_manifest_hashes_seed_sql_and_noop_baseline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from eog.runner import subprocess

    tasks = load_tasks()
    seed_root = tmp_path / "seeds"
    seed_names = {server["seed_database_file"] for item in tasks for server in item.gym_servers_config}
    for name in seed_names:
        path = seed_root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"-- {name}\n")
    monkeypatch.setenv("EOG_DATA_ROOT", str(seed_root))

    def fake_run(command, **kwargs):
        return SimpleNamespace(stdout="" if command[1] == "status" else "commit-sha\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    split = make_split(tasks)
    excluded_tasks_path = tmp_path / "excluded_tasks.json"
    excluded_tasks_path.write_text('[{"task_id":"excluded-task","reason":"bad corrected checks"}]\n')
    baseline_path = tmp_path / "noop_baseline.jsonl"
    baseline_path.write_text('{"task_id":"demo","passes":[]}\n')
    manifest = freeze_manifest(
        repo=tmp_path,
        tasks=tasks,
        image_digests={},
        split=split,
        skills_snapshot_file_sha256="snapshot-file-sha256",
        skills_snapshot_content_sha256="snapshot-content-sha256",
        zep_base_url="http://127.0.0.1:8000/api/v4",
        teams_townhall_patch_sha256="townhall-patch-sha256",
        itsm_opened_by_patch_sha256="itsm-opened-by-patch-sha256",
        claude_cli_version="2.1.296",
        claude_agent_teams_enabled=False,
        claude_agent_teams_env={"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": ""},
        claude_cli_env={"PATH": "/usr/bin", "ANTHROPIC_API_KEY": "[redacted]"},
        claude_sdk_argument_validation_enabled=False,
        excluded_tasks_path=excluded_tasks_path,
        gym_set_index=2,
        noop_baseline_path=baseline_path,
    )
    https_manifest = freeze_manifest(
        repo=tmp_path,
        tasks=tasks,
        image_digests={},
        split=split,
        zep_base_url="https://127.0.0.1:8000/api/v4",
        noop_baseline_path=baseline_path,
    )
    assert manifest.seed_sql_sha256 == {
        name: hashlib.sha256((seed_root / name).read_bytes()).hexdigest() for name in sorted(seed_names)
    }
    baseline = baseline_path
    assert manifest.noop_baseline_sha256 == hashlib.sha256(baseline.read_bytes()).hexdigest()
    assert manifest.noop_baseline_path == str(baseline.resolve())
    assert manifest.zep_base_url == "http://127.0.0.1:8000/api/v4"
    assert manifest.teams_townhall_patch_sha256 == "townhall-patch-sha256"
    assert manifest.itsm_opened_by_patch_sha256 == "itsm-opened-by-patch-sha256"
    assert manifest.claude_cli_version == "2.1.296"
    assert manifest.claude_agent_teams_enabled is False
    assert manifest.claude_agent_teams_env == {"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": ""}
    assert manifest.claude_cli_env == {"PATH": "/usr/bin", "ANTHROPIC_API_KEY": "[redacted]"}
    assert manifest.claude_sdk_argument_validation_enabled is False
    assert manifest.gym_set_index == 2
    assert manifest.excluded_tasks_path == str(excluded_tasks_path.resolve())
    assert manifest.excluded_tasks_sha256 == hashlib.sha256(excluded_tasks_path.read_bytes()).hexdigest()
    assert manifest.skills_snapshot_file_sha256 == "snapshot-file-sha256"
    assert manifest.skills_snapshot_content_sha256 == "snapshot-content-sha256"
    assert https_manifest.zep_base_url == "https://127.0.0.1:8000/api/v4"
    assert manifest.zep_base_url != https_manifest.zep_base_url
    assert https_manifest.claude_sdk_argument_validation_enabled is None
    assert https_manifest.gym_set_index == 0
    assert https_manifest.excluded_tasks_path == ""
    assert https_manifest.excluded_tasks_sha256 == ""


def test_manifest_records_overlay_directory_and_hash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from eog.runner import subprocess

    def fake_run(command, **kwargs):
        return SimpleNamespace(stdout="" if command[1] == "status" else "commit-sha\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    overlay_dir = tmp_path / "overlay"
    (overlay_dir / "nested").mkdir(parents=True)
    (overlay_dir / "nested" / "b.json").write_text('{"task_id":"b"}')
    (overlay_dir / "a.json").write_text('{"task_id":"a"}')
    baseline_path = overlay_dir / "noop_baseline.jsonl"
    baseline_path.write_text('{"task_id":"a","passes":[true]}\n')

    expected = hashlib.sha256()
    for path in sorted(overlay_dir.rglob("*"), key=lambda item: item.relative_to(overlay_dir).as_posix()):
        if path.is_file():
            relative_path = path.relative_to(overlay_dir).as_posix().encode("utf-8")
            content = path.read_bytes()
            expected.update(len(relative_path).to_bytes(8, "big"))
            expected.update(relative_path)
            expected.update(len(content).to_bytes(8, "big"))
            expected.update(content)

    manifest = freeze_manifest(
        repo=tmp_path,
        tasks=(),
        image_digests={},
        split={},
        overlay_dir=overlay_dir,
        noop_baseline_path=baseline_path,
    )

    assert manifest.overlay_dir == str(overlay_dir.resolve())
    assert manifest.overlay_sha256 == expected.hexdigest()
    assert manifest.noop_baseline_path == str(baseline_path.resolve())
    assert manifest.noop_baseline_sha256 == hashlib.sha256(baseline_path.read_bytes()).hexdigest()
    default_manifest = RunManifest("commit", {}, {}, "split", "snapshot", "prices")
    assert default_manifest.overlay_sha256 == ""
    assert default_manifest.excluded_tasks_path == ""
    assert default_manifest.excluded_tasks_sha256 == ""


def test_load_tasks_ignores_top_level_jsonl_overlay(tmp_path: Path) -> None:
    empty_overlay_dir = tmp_path / "empty"
    empty_overlay_dir.mkdir()
    jsonl_overlay_dir = tmp_path / "with-jsonl"
    jsonl_overlay_dir.mkdir()

    baseline_tasks = load_tasks(overlay_dir=empty_overlay_dir)
    task = baseline_tasks[0]
    (jsonl_overlay_dir / "noop_baseline.jsonl").write_text(
        json.dumps({"task_id": task.task_id, "system_prompt": "unexpected override"}) + "\n"
    )

    tasks_with_jsonl = load_tasks(overlay_dir=jsonl_overlay_dir)
    task_with_jsonl = next(item for item in tasks_with_jsonl if item.task_id == task.task_id)

    assert [item.task_id for item in tasks_with_jsonl] == [item.task_id for item in baseline_tasks]
    assert task_with_jsonl.system_prompt == task.system_prompt


def test_route_settings_keep_temperature_and_output_sources() -> None:
    from eog.settings import ROUTE_SETTINGS

    expected = {
        "claude-opus-5-5": (128_000, None),
        "claude-fable-5-1": (128_000, None),
        "claude-haiku-5-5": (64_000, None),
        "gpt-6-luna": (128_000, None),
        "gpt-6.1-sol": (128_000, None),
        "gpt-6-astra": (128_000, None),
        "glm-5.3-flash": (131_072, 1.0),
        "glm-5.2": (131_072, 1.0),
        "nemotron-3.5-lightning": (32_768, None),
        "deepseek-v4.1-flash-1017": (65_536, None),
    }
    assert {name: (item.max_output_tokens, item.temperature) for name, item in ROUTE_SETTINGS.items()} == expected
    assert all(item.max_output_source.startswith("https://") for item in ROUTE_SETTINGS.values())
    assert all(item.temperature_source.startswith("https://") for item in ROUTE_SETTINGS.values())


@pytest.mark.parametrize(
    ("actual", "expected", "comparison", "result"),
    [(2, 2, "equals", True), (3, 2, "greater_than", True), (1, 2, "less_than", True), ("abc", "b", "contains", True)],
)
def test_verifier_semantics(actual: object, expected: object, comparison: str, result: bool) -> None:
    from eog.gym import compare_values

    assert compare_values(actual, expected, comparison) is result
