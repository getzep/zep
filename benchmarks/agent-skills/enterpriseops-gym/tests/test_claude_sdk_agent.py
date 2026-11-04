import asyncio
import hashlib
import io
import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import ClassVar

import httpx
import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, create_sdk_mcp_server

from eog.claude_sdk_agent import (
    CLI_ENV_ALLOWLIST,
    MODEL,
    TASK_PLANNING_TOOLS,
    AnthropicCaptureProxy,
    _install_sdk_validation_bypass,
    _redact_body_value,
    anthropic_provider_schema,
    build_agent_options,
    build_sdk_tools,
    build_task_tool_guard,
    calculate_active_seconds,
    classify_result_message,
    claude_cli_version,
    claude_user_prompt,
    cli_attempt_count,
    cli_environment,
    cli_environment_manifest,
    deduplicate_message_usage,
    proxy_cost_usd,
    proxy_usage_context,
    proxy_usage_totals,
    raise_tool_infrastructure_error,
    record_compaction_event,
    request_evidence,
    request_gate_errors,
    require_main_model_request,
    result_usage,
    validate_init_tools,
    write_cli_env_wrapper,
)
from eog.errors import InfrastructureError
from eog.ledger import CostLedger
from eog.prices import cost_usd
from eog.run import _teacher_reward


def test_init_tool_allowlist_requires_exact_gym_and_task_tool_sets() -> None:
    gym_tool = "mcp__eog__calendar__create_event"
    allowed = {gym_tool, *TASK_PLANNING_TOOLS}
    reported = [gym_tool, *TASK_PLANNING_TOOLS]
    assert validate_init_tools(reported, allowed) == reported
    with pytest.raises(InfrastructureError, match=r"missing=.*TaskGet"):
        validate_init_tools([gym_tool, *TASK_PLANNING_TOOLS[:-1]], allowed)
    for extra in ("Bash", "Task"):
        with pytest.raises(InfrastructureError, match=extra):
            validate_init_tools([*reported, extra], allowed)


def test_task_update_owner_is_denied_and_each_denial_is_recorded() -> None:
    context = {}
    guard = build_task_tool_guard(context)

    async def check() -> None:
        denied = await guard({"tool_name": "TaskUpdate", "tool_input": {"owner": "teammate"}}, "id-1", {})
        denied_again = await guard({"tool_name": "TaskUpdate", "tool_input": {"owner": "other"}}, "id-2", {})
        allowed = await guard({"tool_name": "TaskUpdate", "tool_input": {"status": "in_progress"}}, "id-3", {})
        assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert denied_again["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert allowed == {}

    asyncio.run(check())
    assert context["claude_task_update_owner_denials"] == [
        {"tool": "TaskUpdate", "reason": "owner assignment is disabled"},
        {"tool": "TaskUpdate", "reason": "owner assignment is disabled"},
    ]


def test_sdk_tool_preserves_schema_arguments_and_gym_result() -> None:
    schema = {"type": "object", "properties": {"room": {"type": "string"}}}
    arguments = {"room": "A"}

    class GymClient:
        def call_tool(self, name, received):
            assert name == "create_event"
            assert received is arguments
            return {"content": [{"type": "text", "text": "created"}], "isError": True}

    tools = build_sdk_tools(
        GymClient(),
        [{"name": "create_event", "description": "Create an event", "inputSchema": schema}],
        [],
    )
    assert tools[0].input_schema == schema
    result = asyncio.run(tools[0].handler(arguments))
    assert result == {"content": [{"type": "text", "text": "created"}], "is_error": True}


def test_usage_deduplicates_message_ids_and_parses_camel_case_totals() -> None:
    messages = [
        AssistantMessage(
            content=[],
            model=MODEL,
            usage={"input_tokens": 10, "output_tokens": 2, "cache_read_input_tokens": 3},
            message_id="message-1",
        ),
        AssistantMessage(
            content=[],
            model=MODEL,
            usage={"input_tokens": 10, "output_tokens": 9, "cache_read_input_tokens": 3},
            message_id="message-1",
        ),
    ]
    assert deduplicate_message_usage(messages) == {
        "message-1": {
            "input_tokens": 10,
            "output_tokens": 9,
            "cache_read_input_tokens": 3,
            "cache_creation_input_tokens": 0,
        }
    }
    totals, sdk_cost = result_usage(
        SimpleNamespace(
            model_usage={
                MODEL: {
                    "inputTokens": 10,
                    "outputTokens": 4,
                    "cacheReadInputTokens": 3,
                    "cacheCreationInputTokens": 2,
                    "costUSD": 0.25,
                }
            },
            total_cost_usd=0.25,
        )
    )
    assert totals == {
        "input_tokens": 10,
        "output_tokens": 4,
        "cache_read_input_tokens": 3,
        "cache_creation_input_tokens": 2,
    }
    assert sdk_cost == 0.25


def test_options_use_claude_code_preset_and_arm_a_user_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    task = SimpleNamespace(system_prompt="Task system text", user_prompt="Task request")
    task_tool_guard = build_task_tool_guard({})

    def stderr(_text: str) -> None:
        return None

    options = build_agent_options(
        task,
        create_sdk_mcp_server("eog", tools=[]),
        {"mcp__eog__sample", *TASK_PLANNING_TOOLS},
        effort="max",
        base_url="http://127.0.0.1:1234",
        cwd="/tmp/empty",
        stderr=stderr,
        task_tool_guard=task_tool_guard,
    )
    assert options.system_prompt == {
        "type": "preset",
        "preset": "claude_code",
        "append": task.system_prompt,
    }
    assert claude_user_prompt(task) == task.user_prompt
    assert options.effort == "max"
    assert options.thinking == {"type": "adaptive"}
    assert options.setting_sources == []
    assert options.hooks["PreToolUse"][0].matcher == "TaskUpdate"
    assert options.hooks["PreToolUse"][0].hooks == [task_tool_guard]
    assert options.allowed_tools == sorted({"mcp__eog__sample", *TASK_PLANNING_TOOLS})
    assert options.tools == list(TASK_PLANNING_TOOLS)
    assert "Bash" in options.disallowed_tools
    assert options.max_turns == 100
    assert options.stderr is stderr
    assert options.env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:1234"
    assert options.env["MCP_TOOL_TIMEOUT"] == "60000"
    assert options.env["CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"] == ""


def test_cli_version_reads_the_installed_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    from eog import claude_sdk_agent

    def fake_run(command, **kwargs):
        assert command == [claude_sdk_agent.CLI_PATH, "--version"]
        return SimpleNamespace(stdout="2.1.296\n", stderr="")

    monkeypatch.setattr(claude_sdk_agent.subprocess, "run", fake_run)
    assert claude_cli_version() == "2.1.296"


def test_capture_body_redacts_key_fields() -> None:
    assert _redact_body_value(
        {"api_key": "test-secret", "x-api-key": "other-secret", "messages": [{"text": "safe"}]}
    ) == {
        "api_key": "[redacted]",
        "x-api-key": "[redacted]",
        "messages": [{"text": "safe"}],
    }


def test_error_results_use_verifier_only_for_turn_limit_and_plain_execution_failure() -> None:
    turn_limit = ResultMessage(
        subtype="error_max_turns",
        duration_ms=1,
        duration_api_ms=1,
        is_error=True,
        num_turns=100,
        session_id="session-1",
        result="Reached maximum number of turns (100)",
    )
    subtype, finish_reason, should_infra_fail = classify_result_message(turn_limit, [], "")
    assert subtype == "error_max_turns"
    assert finish_reason == "turn_limit"
    assert should_infra_fail is False
    assert _teacher_reward(True, finish_reason) == 1

    plain_error = ResultMessage(
        subtype="error_during_execution",
        duration_ms=1,
        duration_api_ms=1,
        is_error=True,
        num_turns=1,
        session_id="session-2",
        result="some text",
    )
    subtype, finish_reason, should_infra_fail = classify_result_message(plain_error, [], "")
    assert subtype == "error_during_execution"
    assert finish_reason == "error_during_execution"
    assert should_infra_fail is False
    assert _teacher_reward(True, finish_reason) == 0

    failed_proxy = ResultMessage(
        subtype="error_during_execution",
        duration_ms=1,
        duration_api_ms=1,
        is_error=True,
        num_turns=1,
        session_id="session-3",
        result="some text",
    )
    subtype, finish_reason, should_infra_fail = classify_result_message(
        failed_proxy,
        [{"http_status": 503, "error_type": "provider_error"}],
        "",
    )
    assert subtype == "error_during_execution"
    assert finish_reason == "error_during_execution"
    assert should_infra_fail is True

    provider_error, provider_error_finish_reason, provider_error_is_infra = classify_result_message(
        ResultMessage(
            subtype="error_during_execution",
            duration_ms=1,
            duration_api_ms=1,
            is_error=True,
            num_turns=1,
            session_id="session-4",
            result="some text",
        ),
        [{"http_status": 400, "error_type": "provider_error"}],
        "",
    )
    cli_crash, cli_crash_finish_reason, cli_crash_is_infra = classify_result_message(
        ResultMessage(
            subtype="error_during_execution",
            duration_ms=1,
            duration_api_ms=1,
            is_error=True,
            num_turns=1,
            session_id="session-5",
            result="some text",
        ),
        [],
        "fatal error",
    )
    assert (provider_error, provider_error_finish_reason, provider_error_is_infra) == (
        "error_during_execution",
        "error_during_execution",
        False,
    )
    assert (cli_crash, cli_crash_finish_reason, cli_crash_is_infra) == (
        "error_during_execution",
        "error_during_execution",
        True,
    )
    for failed_attempt in (
        {"http_status": 429, "error_type": "rate_limit"},
        {"http_status": 502, "error_type": "ConnectError"},
    ):
        assert classify_result_message(
            ResultMessage(
                subtype="error_during_execution",
                duration_ms=1,
                duration_api_ms=1,
                is_error=True,
                num_turns=1,
                session_id="session-6",
                result="some text",
            ),
            [failed_attempt],
            "",
        )[2]


def test_gym_infrastructure_error_is_raised_after_the_tool_handler_records_it() -> None:
    root_error = TimeoutError("token=secret")

    class GymClient:
        def call_tool(self, _name, _arguments):
            raise InfrastructureError("MCP failed") from root_error

    context = {}
    tools = build_sdk_tools(
        GymClient(),
        [{"name": "lookup", "description": "Lookup", "inputSchema": {"type": "object", "properties": {}}}],
        [],
        context,
    )
    result = asyncio.run(tools[0].handler({}))
    assert result["is_error"] is True
    assert context["_claude_tool_infrastructure_exception"].__cause__ is root_error
    with pytest.raises(InfrastructureError) as caught:
        raise_tool_infrastructure_error(context)
    assert caught.value.__cause__ is context["_claude_tool_infrastructure_exception"]


def test_active_time_uses_successful_model_calls_and_tool_calls_only() -> None:
    assert calculate_active_seconds([1.25, 2.0], [0.5, 0.75]) == 4.5


def test_compaction_events_are_recorded_without_overriding_the_cli_default() -> None:
    context = {}
    record_compaction_event(context, "compact_boundary")
    record_compaction_event(context, "turn_duration")
    record_compaction_event(context, "compact_summary")
    assert context["claude_compaction_events"] == [
        {"subtype": "compact_boundary"},
        {"subtype": "compact_summary"},
    ]


def test_anthropic_schema_removes_only_top_level_union_keys() -> None:
    schema = {
        "type": "object",
        "properties": {"value": {"oneOf": [{"type": "string"}, {"type": "integer"}]}},
        "oneOf": [{"type": "object"}],
        "required": ["value"],
    }
    assert anthropic_provider_schema({"name": "tool", "inputSchema": schema}) == {
        "type": "object",
        "properties": {"value": {"oneOf": [{"type": "string"}, {"type": "integer"}]}},
        "required": ["value"],
    }


def test_sdk_validation_bypass_stays_installed_for_overlapping_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import claude_agent_sdk

    from eog import claude_sdk_agent

    original_validate = claude_agent_sdk.jsonschema.validate
    monkeypatch.setattr(claude_agent_sdk.jsonschema, "validate", original_validate)
    monkeypatch.setattr(claude_sdk_agent, "_sdk_validation_bypass_installed", False)
    first_ready = threading.Event()
    second_ready = threading.Event()
    release_first = threading.Event()
    release_second = threading.Event()

    def run_first_row() -> None:
        _install_sdk_validation_bypass()
        first_ready.set()
        assert release_first.wait(timeout=5)

    def run_second_row() -> None:
        assert first_ready.wait(timeout=5)
        _install_sdk_validation_bypass()
        second_ready.set()
        assert release_second.wait(timeout=5)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run_first_row)
        second = pool.submit(run_second_row)
        try:
            assert first_ready.wait(timeout=5)
            assert second_ready.wait(timeout=5)
            release_first.set()
            first.result(timeout=5)
            assert claude_agent_sdk.jsonschema.validate is claude_sdk_agent._disable_sdk_argument_validation
        finally:
            release_first.set()
            release_second.set()
        second.result(timeout=5)


def test_request_evidence_records_only_gated_non_message_fields() -> None:
    import json

    allowed = {"mcp__eog__lookup", *TASK_PLANNING_TOOLS}
    body = json.dumps(
        {
            "model": MODEL,
            "max_tokens": 128000,
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": "max"},
            "tools": [{"name": name, "input_schema": {}} for name in sorted(allowed)],
            "system": [{"type": "text", "text": "system"}],
            "messages": [{"role": "user", "content": "private task data"}],
        }
    ).encode()
    evidence = request_evidence(body)
    assert evidence["model"] == MODEL
    assert evidence["max_tokens"] == 128000
    assert evidence["thinking"] == {"type": "adaptive"}
    assert evidence["effort"] == "max"
    assert set(evidence["tool_names"]) == allowed
    system_bytes = json.dumps(
        [{"type": "text", "text": "system"}],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert evidence["system_sha256"] == hashlib.sha256(system_bytes).hexdigest()
    assert "messages" not in evidence
    assert request_gate_errors(evidence, allowed) == []
    assert request_gate_errors({**evidence, "tool_names": ["Bash"]}, allowed) == ["tool_names"]
    assert request_gate_errors({**evidence, "model": "other"}, allowed) == ["model"]
    assert request_gate_errors({**evidence, "effort": "low"}, allowed) == ["effort"]
    assert request_gate_errors({**evidence, "thinking": {"type": "enabled"}}, allowed) == ["thinking"]


def test_missing_main_model_request_fails_the_gate_and_writes_failure(
    tmp_path,
) -> None:
    context = {"claude_cli_stderr_tail": "missing request"}
    ledger = CostLedger(tmp_path / "costs.jsonl")
    with pytest.raises(InfrastructureError, match="no main-model request"):
        require_main_model_request(context, "task-1", 1, ledger)
    assert context["claude_request_gate_failed"] is True
    assert context["stop_run"] is True
    assert context["claude_request_gate_errors"] == [["no_main_model_request"]]
    failure = ledger.rows()[0]
    assert failure["error_type"] == "InfrastructureError"
    assert failure["task_id"] == "task-1"
    assert failure["cli_stderr_tail"] == "missing request"


def test_cli_environment_is_allowlisted_and_retry_count_matches_arm_a(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret-key")
    monkeypatch.setenv("ANTHROPIC_REGION", "unexpected")
    monkeypatch.setenv("CLAUDE_CODE_EXTRA", "unexpected")
    monkeypatch.setenv("DISABLE_WARNING", "unexpected")
    env = cli_environment("http://127.0.0.1:1234")
    assert set(env) == set(CLI_ENV_ALLOWLIST)
    assert not {"ANTHROPIC_REGION", "CLAUDE_CODE_EXTRA", "DISABLE_WARNING"} & env.keys()
    assert env["CLAUDE_CODE_MAX_RETRIES"] == "29"
    assert env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "128000"
    assert env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    assert env["CLAUDE_STREAM_IDLE_TIMEOUT_MS"] == "600000"
    assert env["CLAUDE_BYTE_STREAM_IDLE_TIMEOUT_MS"] == "600000"
    assert cli_attempt_count() == 30
    manifest = cli_environment_manifest()
    assert set(manifest) == set(CLI_ENV_ALLOWLIST)
    assert manifest["ANTHROPIC_API_KEY"] == "[redacted]"
    assert manifest["CLAUDE_CODE_MAX_RETRIES"] == "29"


def test_cli_wrapper_drops_stray_parent_environment(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from eog import claude_sdk_agent

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("CLAUDE_FOO", "1")
    monkeypatch.setattr(claude_sdk_agent, "CLI_PATH", sys.executable)
    wrapper = write_cli_env_wrapper(tmp_path / "claude-wrapper")
    result = subprocess.run(
        [str(wrapper), "-c", "import os; print(os.getenv('CLAUDE_FOO', '<unset>'))"],
        capture_output=True,
        check=True,
        text=True,
    )
    assert result.stdout.strip() == "<unset>"


def test_proxy_usage_fallback_and_cost_use_each_request_model() -> None:
    attempts = [
        {
            "model": "claude-opus-5-5",
            "response_usage": {
                "input_tokens": 1000,
                "output_tokens": 100,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        },
        {
            "model": "claude-haiku-5-5",
            "response_usage": {
                "input_tokens": 2000,
                "output_tokens": 200,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        },
    ]
    assert proxy_usage_totals(attempts) == {
        "input_tokens": 3000,
        "output_tokens": 300,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    assert proxy_cost_usd(attempts) == pytest.approx(
        cost_usd("claude-opus-5-5", input_tokens=1000, output_tokens=100)
        + cost_usd("claude-haiku-5-5", input_tokens=2000, output_tokens=200)
    )
    proxy_context = proxy_usage_context(attempts)
    assert proxy_context["claude_model_usage"] == {
        "inputTokens": 3000,
        "outputTokens": 300,
        "cacheReadInputTokens": 0,
        "cacheCreationInputTokens": 0,
        "costUSD": None,
    }
    assert proxy_context["claude_cost_usd"] == pytest.approx(proxy_cost_usd(attempts))


def test_proxy_streams_raw_chunks_and_records_a_client_abort(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    body = json.dumps(
        {
            "model": MODEL,
            "max_tokens": 128000,
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": "max"},
            "tools": [
                {"name": name, "input_schema": {}} for name in sorted({"mcp__eog__lookup", *TASK_PLANNING_TOOLS})
            ],
            "system": "policy",
            "messages": [{"role": "user", "content": "task"}],
        }
    ).encode()
    response_body = b'{"type":"message","usage":{"input_tokens":3,"output_tokens":4}}'

    class Response:
        status_code = 200
        headers: ClassVar = {"content-type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def iter_raw(self):
            yield response_body[:20]
            yield response_body[20:]

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def stream(self, *_args, **_kwargs):
            return Response()

    class BrokenPipeWriter:
        def __init__(self):
            self.flush_count = 0
            self.write_count = 0

        def write(self, _chunk):
            self.write_count += 1
            if self.write_count == 2:
                raise BrokenPipeError("client closed")

        def flush(self):
            self.flush_count += 1

    class Handler:
        command = "POST"
        path = "/v1/messages"
        headers: ClassVar = {"Host": "localhost", "Content-Length": str(len(body))}

        def __init__(self):
            self.rfile = io.BytesIO(body)
            self.wfile = BrokenPipeWriter()
            self.status = None

        def send_response(self, status):
            self.status = status

        def send_header(self, *_args):
            pass

        def end_headers(self):
            pass

    monkeypatch.setattr(httpx, "Client", Client)
    context = {"attempt_failures": []}
    proxy = AnthropicCaptureProxy(
        capture_path=tmp_path / "capture.json",
        cost_ledger=CostLedger(tmp_path / "costs.jsonl"),
        task_id="task-1",
        trial=1,
        context=context,
        allowed_tools={"mcp__eog__lookup", *TASK_PLANNING_TOOLS},
    )
    handler = Handler()
    proxy._forward(handler)
    assert handler.status == 200
    assert proxy.attempts[0]["error_type"] == "client_aborted"
    assert proxy.attempts[0]["response_usage"]["input_tokens"] == 3
    assert proxy.attempts[0]["response_usage"]["output_tokens"] == 4
    assert handler.wfile.write_count == 2
    assert handler.wfile.flush_count == 1
    request_rows = (tmp_path / "capture.requests.jsonl").read_text().splitlines()
    assert json.loads(request_rows[0])["gate_passed"] is True


def test_proxy_upstream_url_keeps_the_anthropic_host() -> None:
    url = AnthropicCaptureProxy._upstream_url("/v1/messages?beta=a%20b")

    assert str(url) == "https://api.anthropic.com/v1/messages?beta=a%20b"


@pytest.mark.parametrize(
    "request_target",
    [
        "//127.0.0.1:8000/admin",
        "http://127.0.0.1:8000/admin",
        "///127.0.0.1/admin",
        "relative/path",
        "/\\127.0.0.1/admin",
    ],
)
def test_proxy_rejects_non_origin_request_targets(request_target: str) -> None:
    with pytest.raises(ValueError, match="Invalid Anthropic proxy request target"):
        AnthropicCaptureProxy._upstream_url(request_target)
