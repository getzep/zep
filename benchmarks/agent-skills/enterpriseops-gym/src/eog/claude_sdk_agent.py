from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    HookMatcher,
    ResultMessage,
    SystemMessage,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    create_sdk_mcp_server,
    query,
    tool,
)

from .agent import EPISODE_TIMEOUT, WALL_CLOCK_TIMEOUT, AgentResult
from .dataset import Task
from .errors import AbortRequestedError, BudgetExceeded, InfrastructureError, NonRetryableProviderError
from .ledger import CostLedger, JsonlLedger
from .prices import cost_usd
from .providers.base import check_abort_requested
from .retry import MAX_ATTEMPTS, failure_details, safe_diagnostic_text

MODEL = "claude-opus-5-5"
CLI_PATH = shutil.which("claude") or "claude"
TASK_PLANNING_TOOLS = ("TaskCreate", "TaskUpdate", "TaskList", "TaskGet")
AGENT_TEAMS_ENV = "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"
AGENT_TEAMS_DISABLED_VALUE = ""
CLAUDE_CODE_MAX_RETRIES = "29"
CLAUDE_CODE_MAX_OUTPUT_TOKENS = "128000"
CLAUDE_STREAM_IDLE_TIMEOUT_MS = "600000"
CLAUDE_BYTE_STREAM_IDLE_TIMEOUT_MS = "600000"
CLI_ENV_VALUES = {
    "MCP_TOOL_TIMEOUT": "60000",
    AGENT_TEAMS_ENV: AGENT_TEAMS_DISABLED_VALUE,
    "CLAUDE_CODE_MAX_OUTPUT_TOKENS": CLAUDE_CODE_MAX_OUTPUT_TOKENS,
    "CLAUDE_CODE_MAX_RETRIES": CLAUDE_CODE_MAX_RETRIES,
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "CLAUDE_STREAM_IDLE_TIMEOUT_MS": CLAUDE_STREAM_IDLE_TIMEOUT_MS,
    "CLAUDE_BYTE_STREAM_IDLE_TIMEOUT_MS": CLAUDE_BYTE_STREAM_IDLE_TIMEOUT_MS,
    "CLAUDE_CODE_SDK_READS_SESSION_STATE": "1",
}
CLI_ENV_ALLOWLIST = (
    "PATH",
    "HOME",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    *CLI_ENV_VALUES,
)
DISALLOWED_BUILTIN_TOOLS = [
    "Agent",
    "AskUserQuestion",
    "Bash",
    "BashOutput",
    "Config",
    "Edit",
    "EnterPlanMode",
    "ExitPlanMode",
    "Glob",
    "Grep",
    "KillShell",
    "LS",
    "LSP",
    "MultiEdit",
    "NotebookEdit",
    "Read",
    "Skill",
    "Task",
    "TaskOutput",
    "TaskStop",
    "TodoRead",
    "WebFetch",
    "WebSearch",
    "Write",
]
_SECRET_BODY_KEYS = {
    "api_key",
    "apikey",
    "anthropic_api_key",
    "x_api_key",
    "x_apikey",
    "authorization",
    "token",
    "secret",
    "password",
}
_HOP_HEADERS = {
    "accept-encoding",
    "connection",
    "content-encoding",
    "content-length",
    "host",
    "keep-alive",
    "proxy-authorization",
    "transfer-encoding",
    "upgrade",
}
_USAGE_KEYS = {
    "inputTokens": "input_tokens",
    "outputTokens": "output_tokens",
    "cacheReadInputTokens": "cache_read_input_tokens",
    "cacheCreationInputTokens": "cache_creation_input_tokens",
}
_CLI_CRASH_MARKERS = (
    "panic",
    "fatal error",
    "uncaught exception",
    "process exited with code",
    "cli connection error",
)
_sdk_validation_install_lock = threading.Lock()
_sdk_validation_bypass_installed = False


def cli_environment(base_url: str) -> dict[str, str]:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise InfrastructureError("ANTHROPIC_API_KEY is required for the Claude SDK harness")
    return {
        "PATH": os.getenv("PATH", ""),
        "HOME": os.getenv("HOME", ""),
        "ANTHROPIC_API_KEY": api_key,
        "ANTHROPIC_BASE_URL": base_url,
        **CLI_ENV_VALUES,
    }


def cli_attempt_count() -> int:
    attempt_count = int(CLAUDE_CODE_MAX_RETRIES) + 1
    if attempt_count != MAX_ATTEMPTS:
        raise InfrastructureError(f"Claude CLI retry setting gives {attempt_count} attempts; arm A uses {MAX_ATTEMPTS}")
    return attempt_count


def cli_environment_manifest() -> dict[str, str]:
    values = {
        "PATH": os.getenv("PATH", ""),
        "HOME": os.getenv("HOME", ""),
        "ANTHROPIC_API_KEY": "[redacted]",
        "ANTHROPIC_BASE_URL": "http://127.0.0.1:<ephemeral>",
        **CLI_ENV_VALUES,
    }
    return values


def write_cli_env_wrapper(path: Path) -> Path:
    keys = json.dumps(list(CLI_ENV_ALLOWLIST))
    script = (
        f"#!{os.sys.executable}\n"
        "import os\n"
        "import sys\n"
        f"keys = {keys}\n"
        "env = {key: os.environ[key] for key in keys if key in os.environ}\n"
        f"cli = {CLI_PATH!r}\n"
        "os.execve(cli, [cli, *sys.argv[1:]], env)\n"
    )
    path.write_text(script)
    path.chmod(0o700)
    return path


def anthropic_provider_schema(gym_tool: dict[str, Any]) -> dict[str, Any]:
    schema = gym_tool.get("inputSchema") or {}
    if not isinstance(schema, dict):
        raise InfrastructureError(f"Gym tool {gym_tool.get('name')} has a non-object input schema")
    return {key: value for key, value in schema.items() if key not in {"oneOf", "anyOf", "allOf"}}


def request_evidence(body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    system = payload.get("system")
    system_bytes = json.dumps(system, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    tools = payload.get("tools")
    if not isinstance(tools, list):
        tools = []
    tool_names = [item.get("name") for item in tools if isinstance(item, dict)]
    output_config = payload.get("output_config")
    return {
        "model": payload.get("model"),
        "max_tokens": payload.get("max_tokens"),
        "thinking": payload.get("thinking"),
        "effort": output_config.get("effort") if isinstance(output_config, dict) else payload.get("effort"),
        "output_config": output_config,
        "tool_names": tool_names,
        "system_sha256": hashlib.sha256(system_bytes).hexdigest(),
    }


def request_gate_errors(evidence: dict[str, Any], allowed_tools: set[str]) -> list[str]:
    errors = []
    if evidence.get("model") != MODEL:
        errors.append("model")
    if evidence.get("effort") != "max":
        errors.append("effort")
    thinking = evidence.get("thinking")
    if not isinstance(thinking, dict) or thinking.get("type") != "adaptive":
        errors.append("thinking")
    tool_names = evidence.get("tool_names")
    if (
        not isinstance(tool_names, list)
        or any(not isinstance(name, str) for name in tool_names)
        or len(tool_names) != len(set(tool_names))
        or set(tool_names) != allowed_tools
    ):
        errors.append("tool_names")
    return errors


def require_main_model_request(
    context: dict[str, Any],
    task_id: str,
    trial: int,
    cost_ledger: CostLedger,
) -> None:
    if context.get("claude_request_count", 0) > 0:
        return
    context["claude_request_gate_failed"] = True
    context.setdefault("claude_request_gate_errors", []).append(["no_main_model_request"])
    context["stop_run"] = True
    error = InfrastructureError("Claude CLI sent no main-model request")
    details = failure_details(error, cli_stderr_tail=context.get("claude_cli_stderr_tail", ""))
    context["claude_infrastructure_error"] = details
    cost_ledger.append_failure(
        {
            "task_id": task_id,
            "trial": trial,
            "route": "anthropic_cli",
            "error_type": type(error).__name__,
            **details,
        }
    )
    raise error


def calculate_active_seconds(successful_latencies: list[float], tool_latencies: list[float]) -> float:
    return sum(successful_latencies) + sum(tool_latencies)


def is_cli_crash(stderr: str) -> bool:
    lowered = stderr.casefold()
    return any(marker in lowered for marker in _CLI_CRASH_MARKERS)


def has_failed_proxy_attempt(attempts: list[dict[str, Any]]) -> bool:
    return any(
        attempt.get("http_status") == 429
        or (isinstance(attempt.get("http_status"), int) and attempt["http_status"] >= 500)
        or attempt.get("error_type") in {"client_aborted", "proxy_transport_error", "transport"}
        for attempt in attempts
    )


def classify_result_message(
    result: Any,
    attempts: list[dict[str, Any]],
    stderr: str,
) -> tuple[str, str, bool]:
    subtype = str(getattr(result, "subtype", None) or "error_during_execution")
    if subtype == "error_max_turns":
        return subtype, "turn_limit", False
    if subtype == "error_during_execution":
        return subtype, subtype, has_failed_proxy_attempt(attempts) or is_cli_crash(stderr)
    return subtype, subtype, bool(getattr(result, "is_error", False))


def proxy_usage_totals(attempts: list[dict[str, Any]]) -> dict[str, int]:
    totals = {name: 0 for name in _USAGE_KEYS.values()}
    for attempt in attempts:
        usage = attempt.get("response_usage", {})
        for name in totals:
            totals[name] += int(usage.get(name, 0))
    return totals


def proxy_usage_context(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    totals = proxy_usage_totals(attempts)
    return {
        "claude_model_usage": {
            "inputTokens": totals["input_tokens"],
            "outputTokens": totals["output_tokens"],
            "cacheReadInputTokens": totals["cache_read_input_tokens"],
            "cacheCreationInputTokens": totals["cache_creation_input_tokens"],
            "costUSD": None,
        },
        "claude_cost_usd": proxy_cost_usd(attempts),
        "claude_sdk_cost_usd": None,
        "claude_usage": {
            "input": totals["input_tokens"],
            "cache_read": totals["cache_read_input_tokens"],
            "cache_write": totals["cache_creation_input_tokens"],
            "output": totals["output_tokens"],
            "reasoning": None,
        },
    }


def proxy_cost_usd(attempts: list[dict[str, Any]]) -> float:
    return sum(
        cost_usd(
            attempt["model"],
            input_tokens=attempt["response_usage"]["input_tokens"],
            cache_read_tokens=attempt["response_usage"]["cache_read_input_tokens"],
            cache_write_tokens=attempt["response_usage"]["cache_creation_input_tokens"],
            output_tokens=attempt["response_usage"]["output_tokens"],
        )
        for attempt in attempts
        if attempt.get("model")
    )


def _disable_sdk_argument_validation(*_args: Any, **_kwargs: Any) -> None:
    return None


def _install_sdk_validation_bypass() -> None:
    global _sdk_validation_bypass_installed
    import claude_agent_sdk

    with _sdk_validation_install_lock:
        if _sdk_validation_bypass_installed:
            return
        claude_agent_sdk.jsonschema.validate = _disable_sdk_argument_validation
        _sdk_validation_bypass_installed = True


def claude_cli_version() -> str:
    try:
        result = subprocess.run(
            [CLI_PATH, "--version"],
            capture_output=True,
            check=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise InfrastructureError("The Claude CLI version could not be read") from error
    output = (result.stdout or result.stderr).strip()
    if not output:
        raise InfrastructureError("The Claude CLI did not report its version")
    return output.splitlines()[0].strip()


def _redact_body_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[redacted]" if key.casefold().replace("-", "_") in _SECRET_BODY_KEYS else _redact_body_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_body_value(item) for item in value]
    return value


def _usage_fields(value: Any) -> dict[str, int]:
    if hasattr(value, "model_dump"):
        value = value.model_dump(by_alias=True)
    if not isinstance(value, dict):
        return {name: 0 for name in _USAGE_KEYS.values()}
    return {snake: int(value.get(camel, value.get(snake, 0)) or 0) for camel, snake in _USAGE_KEYS.items()}


def deduplicate_message_usage(messages: list[Any]) -> dict[str, dict[str, int]]:
    usage_by_id: dict[str, dict[str, int]] = {}
    for message in messages:
        if not isinstance(message, AssistantMessage):
            continue
        message_id = message.message_id or message.uuid
        if not message_id:
            continue
        current = usage_by_id.setdefault(str(message_id), {name: 0 for name in _USAGE_KEYS.values()})
        for name, count in _usage_fields(message.usage).items():
            current[name] = max(current[name], count)
    return usage_by_id


def result_usage(result: ResultMessage) -> tuple[dict[str, int], float | None]:
    model_usage = result.model_usage
    if hasattr(model_usage, "model_dump"):
        model_usage = model_usage.model_dump(by_alias=True)
    if not isinstance(model_usage, dict):
        model_usage = {}
    usage = model_usage.get(MODEL)
    if usage is None and len(model_usage) == 1:
        usage = next(iter(model_usage.values()))
    totals = _usage_fields(usage)
    if hasattr(usage, "model_dump"):
        usage = usage.model_dump(by_alias=True)
    sdk_cost = usage.get("costUSD", usage.get("cost_usd")) if isinstance(usage, dict) else None
    if sdk_cost is None:
        sdk_cost = getattr(result, "total_cost_usd", None)
    return totals, float(sdk_cost) if sdk_cost is not None else None


def validate_init_tools(reported_tools: Any, allowed_tools: set[str]) -> list[str]:
    if not isinstance(reported_tools, list) or any(not isinstance(name, str) for name in reported_tools):
        raise InfrastructureError("Claude Code did not report a valid init tools list")
    tools = list(reported_tools)
    actual_tools = set(tools)
    missing = sorted(allowed_tools - actual_tools)
    unexpected = sorted(set(tools) - allowed_tools)
    duplicates = sorted({name for name in tools if tools.count(name) > 1})
    if missing or unexpected or duplicates:
        raise InfrastructureError(
            f"Claude Code init tools did not match the allowlist: "
            f"missing={missing}, unexpected={unexpected}, duplicates={duplicates}"
        )
    return tools


def build_task_tool_guard(context: dict[str, Any]) -> Any:
    denials = context.setdefault("claude_task_update_owner_denials", [])

    async def pre_tool_use(hook_input: dict[str, Any], _tool_use_id: str | None, _context: Any) -> dict:
        tool_name = hook_input.get("tool_name")
        tool_input = hook_input.get("tool_input", {})
        if tool_name == "TaskUpdate" and isinstance(tool_input, dict) and "owner" in tool_input:
            denials.append({"tool": tool_name, "reason": "owner assignment is disabled"})
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "TaskUpdate owner assignment is disabled.",
                }
            }
        return {}

    return pre_tool_use


def raise_tool_infrastructure_error(context: dict[str, Any]) -> None:
    error = context.get("_claude_tool_infrastructure_exception")
    if isinstance(error, BaseException):
        raise InfrastructureError("Gym tool infrastructure failed") from error


def record_compaction_event(context: dict[str, Any], subtype: str) -> None:
    if "compact" in subtype.casefold():
        context.setdefault("claude_compaction_events", []).append({"subtype": subtype})


def build_sdk_tools(
    mcp_client: Any,
    gym_tools: list[dict[str, Any]],
    tool_latencies: list[float],
    context: dict[str, Any] | None = None,
) -> list[Any]:
    tool_context = context if context is not None else {}
    sdk_tools = []
    for gym_tool in gym_tools:
        name = str(gym_tool["name"])
        schema = anthropic_provider_schema(gym_tool)
        description = str(gym_tool.get("description", ""))

        async def handler(arguments: dict[str, Any], *, tool_name: str = name) -> dict[str, Any]:
            started = time.monotonic()
            try:
                result = await asyncio.to_thread(mcp_client.call_tool, tool_name, arguments)
            except InfrastructureError as error:
                tool_context["_claude_tool_infrastructure_exception"] = error
                tool_context["claude_tool_infrastructure_error"] = {
                    "exception_class": type(error).__name__,
                    "message": safe_diagnostic_text(str(error), max_chars=300),
                }
                return {
                    "content": [{"type": "text", "text": "Gym infrastructure failed"}],
                    "is_error": True,
                }
            finally:
                tool_latencies.append(time.monotonic() - started)
            return {
                "content": result.get("content", []),
                "is_error": bool(result.get("isError", False)),
            }

        sdk_tools.append(tool(name, description, schema)(handler))
    return sdk_tools


def build_agent_options(
    task: Task,
    server: Any,
    allowed_tools: set[str],
    *,
    effort: str,
    base_url: str,
    cwd: str | Path,
    cli_path: str = CLI_PATH,
    stderr: Any = None,
    task_tool_guard: Any = None,
) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        model=MODEL,
        effort=effort,
        thinking={"type": "adaptive"},
        system_prompt={
            "type": "preset",
            "preset": "claude_code",
            "append": task.system_prompt,
        },
        tools=list(TASK_PLANNING_TOOLS),
        allowed_tools=sorted(allowed_tools),
        disallowed_tools=DISALLOWED_BUILTIN_TOOLS,
        hooks=(
            {"PreToolUse": [HookMatcher(matcher="TaskUpdate", hooks=[task_tool_guard])]}
            if task_tool_guard is not None
            else None
        ),
        permission_mode="dontAsk",
        setting_sources=[],
        strict_mcp_config=True,
        mcp_servers={"eog": server},
        cwd=cwd,
        stderr=stderr,
        cli_path=cli_path,
        max_turns=100,
        env=cli_environment(base_url),
    )


def claude_user_prompt(task: Task) -> str:
    return task.user_prompt


class AnthropicCaptureProxy:
    def __init__(
        self,
        *,
        capture_path: Path,
        cost_ledger: CostLedger,
        task_id: str,
        trial: int,
        context: dict[str, Any],
        allowed_tools: set[str],
    ) -> None:
        self.capture_path = capture_path
        self.cost_ledger = cost_ledger
        self.task_id = task_id
        self.trial = trial
        self.context = context
        self.allowed_tools = allowed_tools
        self.request_ledger = JsonlLedger(capture_path.with_suffix(".requests.jsonl"))
        self.attempts: list[dict[str, Any]] = []
        self.successful_latencies: list[float] = []
        self._request_hashes: dict[str, int] = {}
        self._request_count = 0
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        if self._server is None:
            raise RuntimeError("The Anthropic capture proxy has not started")
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def start(self) -> None:
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                proxy._forward(self)

            def do_GET(self) -> None:
                proxy._forward(self)

            def log_message(self, format: str, *args: Any) -> None:
                del format, args

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    @staticmethod
    def _response_usage(body: bytes, content_type: str) -> dict[str, int | None]:
        usage: dict[str, int | None] = {name: 0 for name in _USAGE_KEYS.values()}
        usage["reasoning_tokens"] = None
        text = body.decode("utf-8", errors="replace")
        values = []
        if "text/event-stream" in content_type or "event:" in text:
            for line in text.splitlines():
                if line.startswith("data:"):
                    payload = line[5:].strip()
                    if payload and payload != "[DONE]":
                        try:
                            values.append(json.loads(payload))
                        except json.JSONDecodeError:
                            continue
        else:
            with suppress(json.JSONDecodeError):
                values.append(json.loads(text))

        usage_keys = set(_USAGE_KEYS) | set(_USAGE_KEYS.values())

        def add_usage(value: dict[str, Any]) -> None:
            for name, count in _usage_fields(value).items():
                usage[name] = max(int(usage[name] or 0), count)
            output_details = value.get("output_tokens_details")
            if isinstance(output_details, dict):
                reasoning_tokens = output_details.get("thinking_tokens")
                if reasoning_tokens is None:
                    reasoning_tokens = output_details.get("reasoning_tokens")
                if reasoning_tokens is not None:
                    usage["reasoning_tokens"] = max(int(usage["reasoning_tokens"] or 0), int(reasoning_tokens))

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                if usage_keys.intersection(value) or "output_tokens_details" in value:
                    add_usage(value)
                for item in value.values():
                    visit(item)
            elif isinstance(value, list):
                for item in value:
                    visit(item)

        for value in values:
            visit(value)
        return usage

    def _capture_request(self, body: bytes) -> tuple[dict[str, Any], list[str], str]:
        self.capture_path.parent.mkdir(parents=True, exist_ok=True)
        evidence = request_evidence(body)
        request_sha256 = hashlib.sha256(body).hexdigest()
        errors = request_gate_errors(evidence, self.allowed_tools)
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {}
        safe = _redact_body_value(payload)
        with self._lock:
            self.capture_path.write_text(json.dumps(safe, indent=2, sort_keys=True) + "\n")
            self.capture_path.chmod(0o600)
            repeated_request_of = self._request_hashes.get(request_sha256)
            self._request_count += 1
            request_index = self._request_count
            self._request_hashes.setdefault(request_sha256, request_index)
        self.request_ledger.append(
            {
                **evidence,
                "request_index": request_index,
                "request_sha256": request_sha256,
                "repeated_request_of": repeated_request_of,
                "gate_passed": not errors,
                "gate_errors": errors,
            }
        )
        self.context["claude_request_log_path"] = str(self.request_ledger.path)
        self.context["claude_request_count"] = self.context.get("claude_request_count", 0) + 1
        if errors:
            self.context["claude_request_gate_failed"] = True
            self.context.setdefault("claude_request_gate_errors", []).append(errors)
        return evidence, errors, request_sha256

    def _record_attempt(
        self,
        *,
        model: str,
        status: int,
        error_type: str | None,
        usage: dict[str, int | None],
        latency: float,
        request_sha256: str,
        request_effort: str | None,
        request_thinking_type: str | None,
    ) -> None:
        attempt = {
            "task_id": self.task_id,
            "trial": self.trial,
            "model": model,
            "route": "anthropic",
            "http_status": status,
            "error_type": error_type,
            "response_usage": usage,
            "reasoning_tokens": usage.get("reasoning_tokens"),
            "latency_seconds": latency,
            "request_sha256": request_sha256,
            "request_effort": request_effort,
            "request_thinking_type": request_thinking_type,
        }
        with self._lock:
            self.attempts.append(attempt)
            model_latencies = self.context.get("model_call_latencies")
            if status < 400 and error_type is None and model == MODEL and isinstance(model_latencies, list):
                model_latencies.append(latency)
            if status < 400 and error_type is None and model == MODEL:
                self.successful_latencies.append(latency)
            else:
                failures = self.context.get("attempt_failures")
                if isinstance(failures, list):
                    failures.append(attempt.copy())
                self.cost_ledger.append_failure(attempt)
            self.cost_ledger.append_call(
                {
                    **attempt,
                    "model": model,
                    "input_tokens": usage["input_tokens"],
                    "cache_read_tokens": usage["cache_read_input_tokens"],
                    "cache_write_tokens": usage["cache_creation_input_tokens"],
                    "output_tokens": usage["output_tokens"],
                }
            )

    @staticmethod
    def _upstream_url(request_target: str) -> httpx.URL:
        try:
            parsed = urlsplit(request_target)
            query = parsed.query.encode("ascii")
        except (UnicodeEncodeError, ValueError) as error:
            raise ValueError("Invalid Anthropic proxy request target") from error

        if (
            not request_target.startswith("/")
            or request_target.startswith("//")
            or parsed.scheme
            or parsed.netloc
            or not parsed.path.startswith("/")
            or "\\" in parsed.path
            or parsed.fragment
        ):
            raise ValueError("Invalid Anthropic proxy request target")

        return httpx.URL("https://api.anthropic.com").copy_with(path=parsed.path, query=query)

    def _forward(self, handler: BaseHTTPRequestHandler) -> None:
        try:
            upstream_url = self._upstream_url(handler.path)
        except ValueError:
            payload = json.dumps(
                {"type": "error", "error": {"type": "invalid_request_target", "message": "Invalid request target"}}
            ).encode()
            handler.send_response(400)
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Content-Length", str(len(payload)))
            handler.end_headers()
            handler.wfile.write(payload)
            handler.wfile.flush()
            return

        body = b""
        if handler.command in {"POST", "PUT", "PATCH"}:
            body = handler.rfile.read(int(handler.headers.get("Content-Length", "0")))
        path = handler.path.split("?", maxsplit=1)[0]
        is_message_call = path.endswith("/v1/messages")
        request_model = MODEL
        request_sha256 = hashlib.sha256(body).hexdigest()
        gate_errors: list[str] = []
        evidence: dict[str, Any] = {}
        if is_message_call:
            try:
                check_abort_requested(self.context)
                self.cost_ledger.check_budget()
            except (AbortRequestedError, BudgetExceeded) as error:
                self.context["budget_exceeded"] = isinstance(error, BudgetExceeded)
                payload = json.dumps(
                    {"type": "error", "error": {"type": "run_stopped", "message": "The run stopped"}}
                ).encode()
                try:
                    handler.send_response(400)
                    handler.send_header("Content-Type", "application/json")
                    handler.send_header("Content-Length", str(len(payload)))
                    handler.end_headers()
                    handler.wfile.write(payload)
                    handler.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return
            evidence, gate_errors, request_sha256 = self._capture_request(body)
            request_model = evidence.get("model") if isinstance(evidence.get("model"), str) else ""
        request_effort = evidence.get("effort") if isinstance(evidence.get("effort"), str) else None
        thinking = evidence.get("thinking")
        request_thinking_type = thinking.get("type") if isinstance(thinking, dict) else None
        if not isinstance(request_thinking_type, str):
            request_thinking_type = None
        if is_message_call:
            started = time.monotonic()
            if gate_errors:
                payload = json.dumps(
                    {"type": "error", "error": {"type": "request_gate_failed", "message": "Request gate failed"}}
                ).encode()
                self.cost_ledger.append_failure(
                    {
                        "task_id": self.task_id,
                        "trial": self.trial,
                        "model": request_model or MODEL,
                        "route": "anthropic_capture_proxy",
                        "error_type": "request_gate_failed",
                        "gate_errors": gate_errors,
                        "request_sha256": request_sha256,
                        "request_effort": request_effort,
                        "request_thinking_type": request_thinking_type,
                    }
                )
                try:
                    handler.send_response(400)
                    handler.send_header("Content-Type", "application/json")
                    handler.send_header("Content-Length", str(len(payload)))
                    handler.end_headers()
                    handler.wfile.write(payload)
                    handler.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return
        headers = {key: value for key, value in handler.headers.items() if key.casefold() not in _HOP_HEADERS}
        headers["Accept-Encoding"] = "identity"
        try:
            with (
                httpx.Client(timeout=600.0) as client,
                client.stream(
                    handler.command,
                    upstream_url,
                    content=body,
                    headers=headers,
                ) as response,
            ):
                status = response.status_code
                response_headers = response.headers
                chunks: list[bytes] = []
                client_aborted = False
                try:
                    handler.send_response(status)
                    for key, value in response_headers.items():
                        if key.casefold() not in _HOP_HEADERS:
                            handler.send_header(key, value)
                    handler.end_headers()
                    for chunk in response.iter_raw():
                        chunks.append(chunk)
                        handler.wfile.write(chunk)
                        handler.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    client_aborted = True
                response_body = b"".join(chunks)
                if is_message_call:
                    usage = self._response_usage(
                        response_body,
                        response_headers.get("content-type", ""),
                    )
                    error_type = "client_aborted" if client_aborted else None
                    if status >= 400:
                        with suppress(json.JSONDecodeError, AttributeError):
                            error_type = str(json.loads(response_body).get("error", {}).get("type") or "provider_error")
                        if not error_type:
                            error_type = "provider_error"
                    self._record_attempt(
                        model=request_model,
                        status=status,
                        error_type=error_type,
                        usage=usage,
                        latency=time.monotonic() - started,
                        request_sha256=request_sha256,
                        request_effort=request_effort,
                        request_thinking_type=request_thinking_type,
                    )
        except httpx.HTTPError as error:
            status = 502
            if is_message_call:
                self._record_attempt(
                    model=request_model,
                    status=status,
                    error_type=type(error).__name__,
                    usage={**{name: 0 for name in _USAGE_KEYS.values()}, "reasoning_tokens": None},
                    latency=time.monotonic() - started,
                    request_sha256=request_sha256,
                    request_effort=request_effort,
                    request_thinking_type=request_thinking_type,
                )
            payload = json.dumps(
                {"type": "error", "error": {"type": "proxy_transport_error", "message": type(error).__name__}}
            ).encode()
            with suppress(BrokenPipeError, ConnectionResetError):
                handler.send_response(status)
                handler.send_header("Content-Type", "application/json")
                handler.send_header("Content-Length", str(len(payload)))
                handler.end_headers()
                handler.wfile.write(payload)
                handler.wfile.flush()


def _tool_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                texts.append(str(item.get("text", "")))
            elif getattr(item, "type", "") == "text":
                texts.append(str(getattr(item, "text", "")))
        return "\n".join(texts)
    return json.dumps(content, ensure_ascii=False, sort_keys=True)


def run_claude_sdk_agent(
    task: Task,
    route: Any,
    mcp_client: Any,
    available_tools: list[dict[str, Any]],
    *,
    effort: str,
    arm: str = "none",
    skills_block: str | None = None,
    selected_tools: tuple[str, ...] | None = None,
    context: dict[str, Any] | None = None,
    clock: Any = time.monotonic,
    capture_path: Path | None = None,
    cost_ledger: CostLedger | None = None,
) -> AgentResult:
    del route, arm, skills_block, selected_tools
    context = context if context is not None else {}
    context["claude_cli_attempt_limit"] = cli_attempt_count()
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise InfrastructureError("ANTHROPIC_API_KEY is required for the Claude SDK harness")
    if not Path(CLI_PATH).is_file():
        raise InfrastructureError(f"The Claude CLI is missing: {CLI_PATH}")
    if capture_path is None or cost_ledger is None:
        raise InfrastructureError("The Claude SDK harness requires a capture path and cost ledger")

    tool_latencies = context.get("tool_call_latencies")
    if not isinstance(tool_latencies, list):
        tool_latencies = []
        context["tool_call_latencies"] = tool_latencies
    sdk_tools = build_sdk_tools(mcp_client, available_tools, tool_latencies, context)
    server = create_sdk_mcp_server(name="eog", tools=sdk_tools)
    task_tools_used = context.setdefault("claude_task_tools_used", [])
    allowed_tools = {f"mcp__eog__{item['name']}" for item in available_tools} | set(TASK_PLANNING_TOOLS)
    proxy = AnthropicCaptureProxy(
        capture_path=capture_path,
        cost_ledger=cost_ledger,
        task_id=task.task_id,
        trial=int(context.get("trial", 1)),
        context=context,
        allowed_tools=allowed_tools,
    )
    proxy.start()
    started = clock()
    deadline = started + WALL_CLOCK_TIMEOUT
    user_prompt = claude_user_prompt(task)
    events: list[dict[str, Any]] = [{"type": "observation", "content": user_prompt}]
    history: list[dict[str, Any]] = [{"role": "user", "content": user_prompt}]
    messages: list[Any] = []
    tool_names_by_id: dict[str, str] = {}
    result_message: ResultMessage | None = None
    init_tools: list[str] | None = None
    seen_message_ids: set[str] = set()
    turns = 0
    finish_reason = "turn_limit"
    final_text = ""
    infrastructure_error: BaseException | None = None
    stderr_parts: list[str] = []
    stderr_lock = threading.Lock()

    def capture_cli_stderr(text: str) -> None:
        with stderr_lock:
            stderr_parts.append(text)
            combined = "".join(stderr_parts)
            if len(combined) > 16000:
                stderr_parts[:] = [combined[-16000:]]

    try:
        with tempfile.TemporaryDirectory(prefix="eog-claude-code-") as cwd:
            cli_wrapper = write_cli_env_wrapper(Path(cwd) / "claude-allowlisted")
            options = build_agent_options(
                task,
                server,
                allowed_tools,
                effort=effort,
                base_url=proxy.base_url,
                cwd=cwd,
                cli_path=str(cli_wrapper),
                stderr=capture_cli_stderr,
                task_tool_guard=build_task_tool_guard(context),
            )

            async def consume() -> None:
                nonlocal finish_reason, init_tools, result_message, turns, final_text
                stream = query(prompt=user_prompt, options=options).__aiter__()
                next_message: asyncio.Task[Any] | None = None
                try:
                    next_message = asyncio.create_task(stream.__anext__())
                    while True:
                        raise_tool_infrastructure_error(context)
                        if context.get("claude_request_gate_failed"):
                            raise InfrastructureError("Claude request gate failed")
                        wall_remaining = deadline - clock()
                        if wall_remaining <= 0:
                            raise InfrastructureError(f"Episode wall-clock limit reached for {task.task_id}")
                        active_remaining = EPISODE_TIMEOUT - calculate_active_seconds(
                            proxy.successful_latencies, tool_latencies
                        )
                        if active_remaining <= 0:
                            finish_reason = "active_time_limit"
                            break
                        wait_timeout = min(wall_remaining, active_remaining)
                        done, _ = await asyncio.wait({next_message}, timeout=wait_timeout)
                        if not done:
                            wall_remaining = deadline - clock()
                            active_remaining = EPISODE_TIMEOUT - calculate_active_seconds(
                                proxy.successful_latencies, tool_latencies
                            )
                            if wall_remaining <= 0:
                                raise InfrastructureError(f"Episode wall-clock limit reached for {task.task_id}")
                            if active_remaining <= 0:
                                finish_reason = "active_time_limit"
                                break
                            continue
                        try:
                            message = next_message.result()
                        except StopAsyncIteration:
                            break
                        next_message = None
                        messages.append(message)
                        if isinstance(message, SystemMessage) and message.subtype == "init":
                            reported_tools = message.data.get("tools")
                            context["claude_init_tools"] = reported_tools
                            init_tools = validate_init_tools(reported_tools, allowed_tools)
                            context["claude_init_tools"] = init_tools
                        elif isinstance(message, SystemMessage):
                            record_compaction_event(context, message.subtype)
                        elif isinstance(message, AssistantMessage):
                            text_blocks = [
                                str(getattr(block, "text", ""))
                                for block in message.content
                                if getattr(block, "type", "") == "text"
                            ]
                            if text_blocks:
                                final_text = "".join(text_blocks)
                            identifier = str(message.message_id or message.uuid or f"assistant-{turns + 1}")
                            if identifier not in seen_message_ids:
                                seen_message_ids.add(identifier)
                                turns += 1
                                tool_names = []
                                text_blocks = []
                                for block in message.content:
                                    if isinstance(block, ToolUseBlock):
                                        tool_names.append(block.name)
                                        if block.name in TASK_PLANNING_TOOLS:
                                            task_tools_used.append(block.name)
                                        tool_names_by_id[block.id] = block.name
                                        events.append(
                                            {
                                                "type": "tool_call",
                                                "content": json.dumps(block.input, sort_keys=True),
                                                "tool": block.name,
                                                "tool_call_id": block.id,
                                            }
                                        )
                                    elif getattr(block, "type", "") == "text":
                                        text_blocks.append(str(getattr(block, "text", "")))
                                text = "".join(text_blocks)
                                if text:
                                    final_text = text
                                if text:
                                    events.append({"type": "decision", "content": text})
                                events.append(
                                    {
                                        "type": "turn",
                                        "turn": turns,
                                        "tool_names": tool_names,
                                        "finished": False,
                                    }
                                )
                                history.append(
                                    {
                                        "role": "assistant",
                                        "content": [
                                            block.model_dump()
                                            if callable(getattr(block, "model_dump", None))
                                            else dict(block)
                                            if isinstance(block, dict)
                                            else vars(block)
                                            for block in message.content
                                        ],
                                    }
                                )
                        elif isinstance(message, UserMessage):
                            content = message.content if isinstance(message.content, list) else []
                            for block in content:
                                if isinstance(block, ToolResultBlock):
                                    text = _tool_content_text(block.content)
                                    if "Input validation error" in text:
                                        context["claude_input_validation_error_count"] = (
                                            context.get("claude_input_validation_error_count", 0) + 1
                                        )
                                    events.append(
                                        {
                                            "type": "tool_result",
                                            "content": text,
                                            "tool": tool_names_by_id.get(str(block.tool_use_id), ""),
                                            "tool_call_id": str(block.tool_use_id),
                                            "is_error": bool(block.is_error),
                                        }
                                    )
                            history.append({"role": "tool", "content": _tool_content_text(content)})
                        elif isinstance(message, ResultMessage):
                            result_message = message
                            subtype = message.subtype or "finished"
                            context["claude_result_subtype"] = subtype
                            finish_reason = "turn_limit" if subtype == "error_max_turns" else subtype
                            final_text = message.result or final_text
                            context["cli_num_turns"] = int(message.num_turns or 0)
                            if events and events[-1]["type"] == "turn":
                                events[-1]["finished"] = True
                        raise_tool_infrastructure_error(context)
                        if context.get("claude_request_gate_failed"):
                            raise InfrastructureError("Claude request gate failed")
                        next_message = asyncio.create_task(stream.__anext__())
                finally:
                    if next_message is not None and not next_message.done():
                        next_message.cancel()
                        with suppress(asyncio.CancelledError):
                            await next_message
                    await stream.aclose()

            try:
                _install_sdk_validation_bypass()
                asyncio.run(consume())
            except (InfrastructureError, NonRetryableProviderError) as error:
                if context.get("budget_exceeded"):
                    raise BudgetExceeded("The configured model-call budget has been reached") from error
                try:
                    check_abort_requested(context)
                except AbortRequestedError as abort_error:
                    raise abort_error from error
                infrastructure_error = error if isinstance(error, InfrastructureError) else None
                raise
            except Exception as error:
                if context.get("budget_exceeded"):
                    raise BudgetExceeded("The configured model-call budget has been reached") from error
                try:
                    check_abort_requested(context)
                except AbortRequestedError as abort_error:
                    raise abort_error from error
                last_attempt = proxy.attempts[-1] if proxy.attempts else {}
                status = last_attempt.get("http_status")
                if isinstance(status, int) and 400 <= status < 500 and status != 429:
                    provider_error = RuntimeError(
                        f"Anthropic returned HTTP {status} ({last_attempt.get('error_type') or 'provider_error'})"
                    )
                    provider_error.status_code = status
                    provider_error.body = {
                        "error": {
                            "type": last_attempt.get("error_type") or "provider_error",
                            "message": f"Anthropic returned HTTP {status}",
                        }
                    }
                    raise NonRetryableProviderError("Anthropic returned a non-retryable error") from provider_error
                infrastructure_error = InfrastructureError(f"Claude SDK or CLI failed for {task.task_id}")
                raise infrastructure_error from error
    finally:
        with stderr_lock:
            context["claude_cli_stderr_tail"] = safe_diagnostic_text("".join(stderr_parts))
        proxy.close()
        context["claude_proxy_attempts"] = list(proxy.attempts)
        context["claude_proxy_model_call_count"] = len(proxy.attempts)
        context["claude_capture_path"] = str(capture_path)
        context["model_calls"] = turns
        context["claude_final_text"] = final_text
        if result_message is None:
            context.update(proxy_usage_context(proxy.attempts))
        if init_tools is not None:
            context["claude_init_tools"] = init_tools
        if isinstance(infrastructure_error, InfrastructureError):
            details = failure_details(
                infrastructure_error,
                cli_stderr_tail=context.get("claude_cli_stderr_tail", ""),
            )
            context["claude_infrastructure_error"] = details
            cost_ledger.append_failure(
                {
                    "task_id": task.task_id,
                    "trial": int(context.get("trial", 1)),
                    "route": "anthropic_cli",
                    "error_type": type(infrastructure_error).__name__,
                    **details,
                }
            )
            if context.get("claude_request_gate_failed"):
                context["stop_run"] = True

    context["claude_message_usage"] = deduplicate_message_usage(messages)
    require_main_model_request(
        context,
        task.task_id,
        int(context.get("trial", 1)),
        cost_ledger,
    )
    if init_tools is None:
        raise InfrastructureError("Claude Code did not send an init tools list")
    if result_message is None and finish_reason != "active_time_limit":
        raise InfrastructureError("Claude Code did not return a result message")
    if result_message is not None and result_message.is_error:
        last_attempt = proxy.attempts[-1] if proxy.attempts else {}
        status = last_attempt.get("http_status")
        subtype, finish_reason, should_infra_fail = classify_result_message(
            result_message, proxy.attempts, context.get("claude_cli_stderr_tail", "")
        )
        if subtype == "error_during_execution" and should_infra_fail:
            error = InfrastructureError("Claude Code failed during execution")
            details = failure_details(error, cli_stderr_tail=context.get("claude_cli_stderr_tail", ""))
            context["claude_infrastructure_error"] = details
            cost_ledger.append_failure(
                {
                    "task_id": task.task_id,
                    "trial": int(context.get("trial", 1)),
                    "route": "anthropic_cli",
                    "error_type": type(error).__name__,
                    **details,
                }
            )
            raise error
        elif (
            subtype not in {"error_max_turns", "error_during_execution"}
            and isinstance(status, int)
            and 400 <= status < 500
            and status != 429
        ):
            provider_error = RuntimeError(
                f"Anthropic returned HTTP {status} ({last_attempt.get('error_type') or 'provider_error'})"
            )
            provider_error.status_code = status
            provider_error.body = {
                "error": {
                    "type": last_attempt.get("error_type") or "provider_error",
                    "message": f"Anthropic returned HTTP {status}",
                }
            }
            raise NonRetryableProviderError("Anthropic returned a non-retryable error") from provider_error
        elif subtype not in {"error_max_turns", "error_during_execution"}:
            raise InfrastructureError(f"Claude Code returned an error result: {subtype}")
    if result_message is None:
        totals = proxy_usage_totals(proxy.attempts)
        sdk_cost = None
    else:
        totals, sdk_cost = result_usage(result_message)
    computed_cost = proxy_cost_usd(proxy.attempts)
    context["claude_model_usage"] = {
        "inputTokens": totals["input_tokens"],
        "outputTokens": totals["output_tokens"],
        "cacheReadInputTokens": totals["cache_read_input_tokens"],
        "cacheCreationInputTokens": totals["cache_creation_input_tokens"],
        "costUSD": sdk_cost,
    }
    context["claude_cost_usd"] = computed_cost
    context["claude_sdk_cost_usd"] = sdk_cost
    context["claude_final_text"] = final_text
    usage = {
        "input": totals["input_tokens"],
        "cache_read": totals["cache_read_input_tokens"],
        "cache_write": totals["cache_creation_input_tokens"],
        "output": totals["output_tokens"],
        "reasoning": None,
    }
    context["claude_usage"] = usage
    active_seconds_total = calculate_active_seconds(proxy.successful_latencies, tool_latencies)
    if active_seconds_total >= EPISODE_TIMEOUT:
        finish_reason = "active_time_limit"
    context["model_calls"] = turns
    context["cli_num_turns"] = context.get("cli_num_turns", int(getattr(result_message, "num_turns", turns) or turns))
    return AgentResult(
        finish_reason,
        turns,
        tuple(history),
        tuple(events),
        usage,
        active_seconds_total,
    )
