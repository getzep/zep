"""Run AppWorld tasks through the Zep Skill arms.

Each AppWorld environment server holds one task at a time, so the runner uses
one server port for each worker. Start the servers from the AppWorld data root:

    env -i HOME=$HOME PATH=$PATH LANG=$LANG <venv>/bin/appworld serve environment --port <port> --root <root> --no-show-usage

Keep the server environment clean because agent code can read its variables.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import logging
import queue
import re
import shlex
import statistics
import subprocess
import tempfile
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import ExitStack
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from openai import OpenAI
from tenacity import (
    retry,
    retry_if_exception,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from zep_cloud import Zep

from agent import (
    AgentContextLimitError,
    AgentEmptyResponseError,
    _raise_on_failed_response,
    call_model,
    is_transient_model_error,
)
from anthropic_client import reports_reasoning_tokens
from appworld_guard import (
    EXPECTED_ALLOWED_MODULES,
    EXPECTED_DISALLOWED_FUNCTION_PATHS,
    GUARD_PREFIX,
    check_agent_code,
    env_probe_code,
    guard_state_code,
    parse_guard_state,
    parse_secret_env_names,
    runtime_guard_matches,
)
from call_ledger import (
    CURRENT_LEDGER,
    CallLedger,
    error_detail,
    is_infrastructure_error,
    ledger_client,
    openai_http_client,
)
from compile import audit_skills, wait_for_learning_idle
from config import agent_client, load_config, zep_client
from readtime import RawCorpus, ReadTimeCurator
from redaction import Redactor
from run import add_price_arguments, cost_usd, freeze_compilation
from skill_snapshot import (
    READ_SKILL_NUDGE,
    READ_SKILL_TOOL,
    READ_SKILL_TOOL_NAME,
    FrozenSkillReader,
    SnapshotExportError,
    compile_snapshot,
    snapshot_sha256,
    wait_for_compilation,
    write_funnel,
)
from tracing import CURRENT_TRACE, TRACER
from zep_capture import (
    EventSink,
    TrajectoryRecorder,
    ensure_agent,
)

LOGGER = logging.getLogger("appworld-skills")
EFFORTS_BY_MODEL = {
    "gpt-6-luna": {"low", "medium", "high", "xhigh", "max"},
    "claude-haiku-5-5": {"low", "medium", "high", "xhigh", "max"},
    "claude-opus-5-5": {"low", "medium", "high", "xhigh", "max"},
    "deepseek-v4.1-flash-1017": {"none", "low", "high", "xhigh", "max"},
}


def validate_model_effort(model: str, effort: str) -> None:
    if model not in EFFORTS_BY_MODEL:
        raise ValueError(f"Unknown model {model!r}")
    if effort not in EFFORTS_BY_MODEL[model]:
        raise ValueError(f"Invalid reasoning effort {effort!r} for model {model!r}")


def _validate_model_arguments(
    model: str,
    reasoning_effort: str,
    arm: str | None,
    curator_model: str | None,
    curator_reasoning_effort: str,
) -> None:
    validate_model_effort(model, reasoning_effort)
    if arm in {"r-skill", "r-raw"}:
        validate_model_effort(curator_model or model, curator_reasoning_effort)


def _require_clean_repository() -> None:
    root = Path(__file__).resolve().parents[2]
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if status:
        raise RuntimeError("Refusing to start because the repository is dirty.")


def _should_export_teacher(stage: str, stage_stop_reason: str | None) -> bool:
    return stage == "teacher" and stage_stop_reason is None


def _schedule_teacher_retry(
    result: Any,
    attempt: int | None,
    completed: set[tuple[str, int]],
    scheduled: set[tuple[str, int]],
    queue: list[tuple[int, str, int, str | None]],
) -> bool:
    if attempt is None:
        return False
    key = (result.task_id, attempt)
    if key in completed or key in scheduled:
        return False
    scheduled.add(key)
    queue.append((result.task_index, result.task_id, attempt, result.trajectory_uuid))
    return True


DOMAIN = "appworld"
FAMILY = "tool.appworld"
ENVIRONMENT = "appworld"
VERIFIER_ID = "appworld-evaluation"
APPWORLD_API_RE = re.compile(r"apis\.([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)")


def reasoning_request(effort: str, summary: bool) -> dict[str, bool | str]:
    reasoning: dict[str, bool | str] = (
        {"enabled": effort == "on"} if effort in {"on", "off"} else {"effort": effort}
    )
    if summary:
        reasoning["summary"] = "auto"
    return reasoning


def appworld_api_names(code: str) -> list[str]:
    names: list[str] = []
    for app, api in APPWORLD_API_RE.findall(code):
        name = f"{app}.{api}"
        if not names or names[-1] != name:
            names.append(name)
            if len(names) == 40:
                break
    return names or ["execute_python"]


class AppWorldServerStateError(RuntimeError):
    def __init__(self, url: str, probe_s: float = 0.0) -> None:
        self.url = url
        self.probe_s = probe_s
        super().__init__(
            f"AppWorld server state changed at {url}. Restart that server process before "
            "you run the stage again."
        )


class AppWorldServerSecretsError(RuntimeError):
    pass


class EpisodeScoreError(RuntimeError):
    def __init__(self, error_type: str, message: str) -> None:
        self.error_type = error_type
        super().__init__(message)


def _set_missing_end_reason(result: AppWorldResult) -> None:
    if result.end_reason is None:
        result.end_reason = (
            result.stop_reason
            or result.error_type
            or ("error" if result.error or result.trace_error else "max_steps")
        )


class AppWorldServerPatchError(RuntimeError):
    def __init__(self, url: str) -> None:
        patch_path = shlex.quote(str(Path(__file__).with_name("appworld-star-import.patch")))
        super().__init__(
            f"AppWorld server {url} needs the star-import fix for "
            "`appworld/common/code_parsing.py` in `parse_code_function_paths`.\n"
            "Run this command from the AppWorld venv's site-packages directory:\n"
            f"patch -p0 -i {patch_path}"
        )


def _is_appworld_5xx(error: Exception, server: AppWorldServer) -> bool:
    if not isinstance(error, httpx.HTTPStatusError) or error.response.status_code < 500:
        return False
    request_url = error.request.url
    server_url = httpx.URL(server.url)
    return (
        request_url.scheme == server_url.scheme
        and request_url.host == server_url.host
        and request_url.port == server_url.port
    )


def _record_episode_error(
    result: AppWorldResult,
    server: AppWorldServer,
    task_id: str,
    run_id: str,
    error: Exception,
    redactor: Redactor | None = None,
    *,
    preserve_evaluation: bool = False,
) -> None:
    result.error = type(error).__name__
    result.error_detail = error_detail(error, redactor)
    if not preserve_evaluation:
        result.succeeded = False
        result.reward = 0.0
    if isinstance(error, EpisodeScoreError):
        result.error_type = error.error_type
    if _is_appworld_5xx(error, server):
        result.health_probe = server.health_probe(task_id, run_id)
        result.error_type = (
            "agent_broke_environment"
            if result.health_probe == "passed"
            else "appworld_server_error"
        )


def _close_appworld(
    result: AppWorldResult,
    server: AppWorldServer,
    task_id: str,
    run_id: str,
    redactor: Redactor | None = None,
    *,
    preserve_evaluation: bool = False,
) -> None:
    try:
        server.call("close", task_id=task_id)
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("AppWorld close failed for %s", task_id)
        _record_episode_error(
            result,
            server,
            task_id,
            run_id,
            exc,
            redactor,
            preserve_evaluation=preserve_evaluation,
        )
        result.error_type = "appworld_close_error"


def _stage_stop_reason(result: AppWorldResult) -> str | None:
    if result.error or result.trace_error:
        return result.error_type or result.error or result.trace_error or "episode_error"
    return None


def _select_task_ids(
    available_task_ids: list[str], requested: str | None, limit: int | None
) -> list[str]:
    task_ids = available_task_ids
    if requested:
        requested_task_ids = [task_id.strip() for task_id in requested.split(",")]
        if not all(requested_task_ids) or len(requested_task_ids) != len(set(requested_task_ids)):
            raise ValueError("--task-ids must contain unique, non-empty task IDs")
        available = set(available_task_ids)
        unknown = [task_id for task_id in requested_task_ids if task_id not in available]
        if unknown:
            raise ValueError(f"Unknown selected task IDs: {', '.join(unknown)}")
        task_ids = requested_task_ids
    return task_ids[:limit]


def _compilation_mode(stage: str, requested: str | None) -> str:
    return requested or ("manual" if stage == "heldout" else "on_close")


def _validate_snapshot_agent(snapshot: dict[str, Any], stage: str, agent_uuid: str | None) -> None:
    if stage == "heldout" and agent_uuid and agent_uuid == snapshot.get("agent_uuid"):
        raise ValueError("The heldout Agent UUID must differ from the snapshot teacher Agent")


def _skill_read_artifact(
    markdown: str,
    requested: object,
    found: bool,
    resolved_name: str | None,
    repaired_from: str | None,
    redactor: Redactor,
) -> tuple[dict[str, Any], str]:
    return (
        {
            "name": resolved_name or str(requested or ""),
            "sha256": hashlib.sha256(markdown.encode()).hexdigest(),
            "found": found,
            "resolved_name": resolved_name,
            "repaired_from": repaired_from,
        },
        redactor.redact(markdown),
    )


def _decode_function_arguments(
    raw_arguments: str, result: AppWorldResult
) -> tuple[dict[str, Any], str | None]:
    try:
        return json.loads(raw_arguments), None
    except json.JSONDecodeError as exc:
        result.malformed_tool_calls += 1
        return {}, f"Invalid JSON arguments: {exc}"


def _apply_evaluation(result: AppWorldResult, evaluation: dict[str, Any]) -> None:
    passes = evaluation.get("passes")
    failures = evaluation.get("failures")
    result.pass_count = len(passes) if isinstance(passes, list) else 0
    result.failures = failures if isinstance(failures, list) else []
    result.num_tests = int(evaluation.get("num_tests", 0) or 0)
    if result.num_tests <= 0 or result.pass_count + len(result.failures) != result.num_tests:
        raise EpisodeScoreError(
            "missing_score", "AppWorld evaluation did not return a complete score"
        )
    # AppWorld scores an exception in a test block as a failed test. The exception comes
    # from the end state or the answer of the agent, for example float("$12.50"), so it is
    # a task failure. The count lets the audit compare these tasks with passed rows.
    result.non_assertion_failures = sum(
        1
        for failure in result.failures
        if isinstance(failure, dict)
        and failure.get("trace")
        and "AssertionError" not in str(failure.get("trace"))
    )
    result.succeeded = bool(evaluation.get("success", result.pass_count == result.num_tests))
    result.reward = 1.0 if result.succeeded else 0.0


def _record_ledger(result: AppWorldResult, ledger: CallLedger) -> None:
    result.failed_calls = ledger.rows()
    result.call_attempts = ledger.attempts()
    result.zep_s = sum(
        float(attempt.get("latency_s") or 0)
        for attempt in result.call_attempts
        if attempt.get("service") == "zep"
    )
    result.appworld_s = sum(
        float(attempt.get("latency_s") or 0)
        for attempt in result.call_attempts
        if attempt.get("service") == "appworld"
    )


def _record_guard_markers(
    result: AppWorldResult, server: AppWorldServer, redactor: Redactor | None = None
) -> None:
    result.guard_marker_hits = getattr(server, "guard_marker_hits", 0)
    result.guard_marker_code = [
        redactor.redact(code) if redactor else code
        for code in getattr(server, "guard_marker_code", [])
    ]


class AppWorldServer:
    def __init__(self, url: str) -> None:
        self.url = url.rstrip("/")
        self.http = ledger_client("appworld", timeout=300)
        self.blocked_codes = 0
        self.guard_marker_hits = 0
        self.guard_marker_code: list[str] = []
        self.redactor: Redactor | None = None
        self.frozen_guard_state: str | None = None
        self.last_guard_probe_s = 0.0

    def begin_episode(self) -> None:
        self.blocked_codes = 0
        self.guard_marker_hits = 0
        self.guard_marker_code = []
        self.redactor = None

    def _check_runtime_code(self, code: str) -> str | None:
        markers = runtime_guard_matches(code)
        if not markers:
            return None
        marker = markers[0]
        self.guard_marker_hits += 1
        self.guard_marker_code.append(self.redactor.redact(code) if self.redactor else code)
        return f"{GUARD_PREFIX}Usage of the following code marker is not allowed: {marker}."

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=2, max=16),
        retry=retry_if_exception_type(httpx.HTTPError),
        reraise=True,
    )
    def _send(self, method: str, body: dict[str, Any]) -> Any:
        if method == "execute":
            code = str(body.get("code") or "")
            if message := self._check_runtime_code(code):
                self.blocked_codes += 1
                return message
        response = self.http.post(f"{self.url}/{method}", json=body)
        response.raise_for_status()
        return response.json()["output"]

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=2, max=16),
        retry=retry_if_exception_type(httpx.HTTPError),
        reraise=True,
    )
    def _send_preflight_execute(self, task_id: str, code: str) -> Any:
        response = self.http.post(
            f"{self.url}/execute",
            json={"task_id": task_id, "code": code},
        )
        response.raise_for_status()
        return response.json()["output"]

    def call(self, method: str, **body: Any) -> Any:
        if method == "execute":
            code = str(body.get("code") or "")
            if message := self._check_runtime_code(code):
                self.blocked_codes += 1
                return message
            message = check_agent_code(code)
            if message is not None:
                self.blocked_codes += 1
                return message
        return self._send(method, body)

    def _probe(self, task_id: str, code: str) -> str:
        return str(self._send("execute", {"task_id": task_id, "code": code}))

    def _validated_guard_state(self, output: str, probe_s: float = 0.0) -> str:
        try:
            fingerprint = parse_guard_state(output)
            state = json.loads(fingerprint)
            allowed = frozenset(state["allowed"])
            disallowed = frozenset(state["disallowed"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AppWorldServerStateError(self.url, probe_s) from exc
        if allowed != EXPECTED_ALLOWED_MODULES or disallowed != EXPECTED_DISALLOWED_FUNCTION_PATHS:
            raise AppWorldServerStateError(self.url, probe_s)
        return fingerprint

    def _probe_star_import(
        self,
        task_id: str,
        code: str,
        expected_output_contains: str | None = None,
    ) -> None:
        try:
            output = self._send_preflight_execute(task_id, code)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500:
                raise AppWorldServerPatchError(self.url) from exc
            raise
        if expected_output_contains is not None and expected_output_contains not in str(output):
            raise AppWorldServerPatchError(self.url)

    def preflight(self, task_id: str, run_id: str) -> list[str]:
        self.call(
            "initialize",
            task_id=task_id,
            experiment_name=f"preflight-{run_id}",
            random_seed=100,
        )
        try:
            self.frozen_guard_state = self._validated_guard_state(
                self._probe(task_id, guard_state_code())
            )
            secret_names = parse_secret_env_names(self._probe(task_id, env_probe_code()))
            self._probe_star_import(task_id, "from math import *")
            self._probe_star_import(
                task_id,
                "from os import *\nsystem('true')",
                "Usage of the following function is not allowed: os.system.",
            )
            return secret_names
        finally:
            self.call("close", task_id=task_id)

    def verify_guard_state(self, task_id: str) -> float:
        started = time.monotonic()
        try:
            output = self._probe(task_id, guard_state_code())
        finally:
            elapsed = time.monotonic() - started
            self.last_guard_probe_s = elapsed
        actual = self._validated_guard_state(output, elapsed)
        if self.frozen_guard_state is None:
            self.frozen_guard_state = actual
        elif actual != self.frozen_guard_state:
            raise AppWorldServerStateError(self.url, elapsed)
        return elapsed

    def health_probe(self, task_id: str, run_id: str) -> str:
        passed = True
        try:
            self.call("close", task_id=task_id)
            self.call(
                "initialize",
                task_id=task_id,
                experiment_name=f"health-{run_id}-{time.time_ns()}",
                random_seed=100,
            )
            self.call("execute", task_id=task_id, code="print(1)")
            self.call("evaluate", task_id=task_id, suppress_errors=True)
        except Exception:  # noqa: BLE001
            passed = False
        finally:
            try:
                self.call("close", task_id=task_id)
            except Exception:  # noqa: BLE001
                passed = False
        return "passed" if passed else "failed"


def task_family_for(task_id: str, scope: str) -> str:
    """Return the task family for an AppWorld task.

    AppWorld task IDs have the form ``<scenario>_<variant>``. The variants of one
    scenario share a workflow and differ in users and values.
    """
    return FAMILY if scope == "domain" else f"{FAMILY}.s{task_id.split('_', 1)[0]}"


def split_task_ids(root: Path, splits: str, variants: str, scenarios: int | None) -> list[str]:
    task_ids: list[str] = []
    for split in dict.fromkeys(part.strip() for part in splits.split(",") if part.strip()):
        task_ids.extend(
            select_tasks(
                (root / "data" / "datasets" / f"{split}.txt").read_text().split(),
                variants,
                scenarios,
            )
        )
    return list(dict.fromkeys(task_ids))


def should_add_read_skill_nudge(
    skill_reader: FrozenSkillReader | None,
    first_assistant_turn: bool,
    tool_calls: list[dict[str, Any]],
) -> bool:
    return bool(
        skill_reader
        and skill_reader.index.entries
        and first_assistant_turn
        and any(call["name"] != READ_SKILL_TOOL_NAME for call in tool_calls)
    )


def select_tasks(task_ids: list[str], variants: str, scenarios: int | None) -> list[str]:
    """Keep the listed variants of the first scenarios, in dataset order."""
    wanted = {part.strip() for part in variants.split(",")} if variants != "all" else None
    order: list[str] = []
    for task_id in task_ids:
        scenario = task_id.split("_", 1)[0]
        if scenario not in order:
            order.append(scenario)
    kept = set(order[:scenarios] if scenarios is not None else order)
    return [
        task_id
        for task_id in task_ids
        if task_id.split("_", 1)[0] in kept
        and (wanted is None or task_id.split("_", 1)[-1] in wanted)
    ]


MAX_OUTPUT_CHARS = 8_000
TOOL = {
    "type": "function",
    "name": "execute_python",
    "description": "Run Python code in the AppWorld shell and return the printed output.",
    "parameters": {
        "type": "object",
        "properties": {"code": {"type": "string", "description": "The Python code to run."}},
        "required": ["code"],
        "additionalProperties": False,
    },
}
SYSTEM_PROMPT = """You complete tasks for your supervisor in AppWorld. You write Python code
that calls app APIs through the execute_python tool. The shell keeps its state between calls.

Use these steps:
1. Call print(apis.api_docs.show_app_descriptions()) to see the apps.
2. Call apis.api_docs.show_api_descriptions(app_name=...) and
   apis.api_docs.show_api_doc(app_name=..., api_name=...) before you use an API.
3. Get the supervisor account passwords with apis.supervisor.show_account_passwords().
   Log in to an app with its login API to get an access token.
4. Read every page of a paginated API with page_index until the result is empty.
5. Do not guess values. Get them from the APIs.
6. When the task is complete, call apis.supervisor.complete_task(answer=...). Give the
   answer when the task asks a question. Otherwise give answer=None.

Print the values that you need to see. Make small steps and check each result."""


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=1, max=16),
    retry=retry_if_exception(is_transient_model_error),
    reraise=True,
)
def _completion(client: OpenAI, kwargs: dict[str, Any]) -> Any:
    return call_model(client, "appworld-agent", kwargs)


@dataclass
class AppWorldResult:
    domain: str
    task_id: str
    task_index: int
    trial: int
    arm: str | None = None
    started_at: str = ""
    finished_at: str = ""
    reward: float = 0.0
    succeeded: bool = False
    pass_count: int = 0
    num_tests: int = 0
    completed: bool = False
    steps: int = 0
    tool_calls: int = 0
    prompt_tokens: int = 0
    cached_tokens: int | None = None
    cache_creation_tokens: int | None = None
    completion_tokens: int = 0
    reasoning_tokens: int | None = None
    duration_s: float = 0.0
    model_s: float = 0.0
    zep_s: float = 0.0
    appworld_s: float = 0.0
    cost_usd: float | None = None
    trajectory_uuid: str | None = None
    skill_search_performed: bool = False
    read_skill_calls: list[dict[str, Any]] = field(default_factory=list)
    failures: list[dict[str, Any]] = field(default_factory=list)
    malformed_tool_calls: int = 0
    non_assertion_failures: int = 0
    end_reason: str | None = None
    stop_reason: str | None = None
    trace_error: str | None = None
    call_attempts: list[dict[str, Any]] = field(default_factory=list)
    skill_uuids_injected: list[str] = field(default_factory=list)
    skill_uuids_loaded: list[str] = field(default_factory=list)
    skill_invalid_loads: int = 0
    injected_skill_chars: int = 0
    memory_source_keys: list[str] = field(default_factory=list)
    memory_skill_uuids: list[str] = field(default_factory=list)
    retrieval_s: float = 0.0
    curator_input_tokens: int = 0
    curator_output_tokens: int = 0
    curator_s: float = 0.0
    briefing: str = ""
    leakage_hits: int = 0
    task_family: str = FAMILY
    skill_catalog_size: int = 0
    skill_catalog_families: list[str] = field(default_factory=list)
    skill_uses_recorded: int = 0
    skill_use_error: str | None = None
    error_detail: str | None = None
    failed_calls: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    error_type: str | None = None
    blocked_code_calls: int = 0
    guard_marker_hits: int = 0
    guard_marker_code: list[str] = field(default_factory=list)
    guard_probe_s: float = 0.0
    health_probe: str | None = None


def _load_episode_skill_reader(
    arm: str | None, skills_snapshot: Path | None, family: str
) -> FrozenSkillReader | None:
    if arm != "skills" or skills_snapshot is None:
        return None
    reader = FrozenSkillReader.from_snapshot(skills_snapshot, task_family=family)
    return reader if reader.index.entries else None


def _prepare_skill_catalog(
    result: AppWorldResult, skill_reader: FrozenSkillReader | None
) -> tuple[str, list[dict[str, Any]]]:
    if skill_reader is None or not skill_reader.index.entries:
        result.skill_catalog_size = 0
        result.skill_catalog_families = []
        return "", [TOOL]
    entries = skill_reader.index.entries
    result.skill_catalog_size = len(entries)
    result.skill_catalog_families = sorted({entry.task_family for entry in entries})
    return skill_reader.index.prompt_block(), [TOOL, READ_SKILL_TOOL]


def _record_skill_reader_usage(
    result: AppWorldResult, skill_reader: FrozenSkillReader | None
) -> None:
    if skill_reader is not None:
        result.skill_uuids_loaded = list(skill_reader.loaded_skill_ids)


def _item_dict(item: Any) -> dict[str, Any]:
    return item.model_dump(mode="json", exclude_none=True) if hasattr(item, "model_dump") else item


def _redact_tool_call_value(value: Any, redactor: Redactor) -> Any:
    if isinstance(value, dict):
        return {key: _redact_tool_call_value(nested, redactor) for key, nested in value.items()}
    if isinstance(value, list):
        return [_redact_tool_call_value(nested, redactor) for nested in value]
    if isinstance(value, str):
        return redactor.redact(value)
    return value


def _instructions(catalog: str, briefing: str) -> str:
    text = SYSTEM_PROMPT
    if catalog:
        text += "\n\n" + catalog
    if briefing:
        text += (
            "\n\n## Task briefing\nA curator prepared this briefing from earlier successful runs "
            "on similar tasks. Get every identifier and value from the APIs, not from the "
            "briefing.\n\n" + briefing
        )
    return text


def run_episode(
    *,
    server: AppWorldServer,
    client: Zep,
    openai_client: OpenAI,
    agent_uuid: str,
    stage: str,
    agent_openai_client: Any = None,
    run_id: str,
    task_id: str,
    task_index: int,
    trial: int,
    model: str,
    reasoning_effort: str,
    max_steps: int,
    arm: str | None,
    memory_agent_uuid: str | None,
    corpus: RawCorpus | None,
    curator_model: str,
    curator_reasoning_effort: str,
    family: str = FAMILY,
    record_uses: bool = False,
    parent_trajectory_uuid: str | None = None,
    on_event: EventSink | None = None,
    reasoning_summary: bool = False,
    skills_snapshot: Path | None = None,
) -> tuple[AppWorldResult, list[dict[str, Any]]]:
    started = datetime.now(UTC)
    result = AppWorldResult(
        domain=DOMAIN,
        task_id=task_id,
        task_index=task_index,
        trial=trial,
        arm=arm,
        started_at=started.isoformat(),
        task_family=family,
    )
    messages: list[dict[str, Any]] = []
    ledger = CallLedger()
    ledger_token = CURRENT_LEDGER.set(ledger)
    server.begin_episode()
    evaluation_recorded = False
    try:
        task = server.call(
            "initialize",
            task_id=task_id,
            experiment_name=f"zep-{run_id}-{stage}-{trial}",
            random_seed=100 + trial,
        )
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("AppWorld initialize failed for %s: %s", task_id, type(exc).__name__)
        _record_episode_error(result, server, task_id, run_id, exc)
        result.blocked_code_calls = server.blocked_codes
        _record_guard_markers(result, server)
        _record_ledger(result, ledger)
        _set_missing_end_reason(result)
        CURRENT_LEDGER.reset(ledger_token)
        return result, messages
    try:
        result.guard_probe_s += server.verify_guard_state(task_id)
    except AppWorldServerStateError as exc:
        result.error = type(exc).__name__
        result.error_detail = str(exc)
        result.error_type = "server_state_changed"
        result.guard_probe_s += exc.probe_s
        result.blocked_code_calls = server.blocked_codes
        _record_guard_markers(result, server)
        _close_appworld(result, server, task_id, run_id)
        _record_ledger(result, ledger)
        result.duration_s = (datetime.now(UTC) - started).total_seconds()
        result.finished_at = datetime.now(UTC).isoformat()
        _set_missing_end_reason(result)
        CURRENT_LEDGER.reset(ledger_token)
        return result, messages
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("AppWorld guard-state probe failed for %s: %s", task_id, type(exc).__name__)
        _record_episode_error(result, server, task_id, run_id, exc)
        result.guard_probe_s += server.last_guard_probe_s
        result.blocked_code_calls = server.blocked_codes
        _record_guard_markers(result, server)
        _close_appworld(result, server, task_id, run_id)
        _record_ledger(result, ledger)
        result.duration_s = (datetime.now(UTC) - started).total_seconds()
        result.finished_at = datetime.now(UTC).isoformat()
        _set_missing_end_reason(result)
        CURRENT_LEDGER.reset(ledger_token)
        return result, messages
    supervisor = dict(task["supervisor"])
    redactor = Redactor.from_values(supervisor)
    server.redactor = redactor
    first = (
        f"Supervisor: {supervisor['first_name']} {supervisor['last_name']}, "
        f"{supervisor['email']}, {supervisor['phone_number']}.\n"
        f"Task: {task['instruction']}"
    )
    trace = TRACER.start_trace(
        f"appworld-{stage}",
        session_id=f"appworld:{run_id}:{stage}",
        metadata={"task_id": task_id, "trial": trial, "model": model},
    )
    trace_token = CURRENT_TRACE.set(trace)
    recorder = TrajectoryRecorder(
        client,
        agent_uuid,
        family,
        str(task["instruction"]),
        ENVIRONMENT,
        {"harness": "appworld-skills", "stage": stage, "task_index": str(task_index)},
        redactor=redactor,
        stage=stage,
        run_id=run_id,
        domain=DOMAIN,
        task_index=task_index,
        trial=trial,
        verifier_id=VERIFIER_ID,
        parent_trajectory_uuid=parent_trajectory_uuid,
        on_event=on_event,
        discoverable_tools=frozenset(),
    )
    skill_reader = _load_episode_skill_reader(arm, skills_snapshot, family)
    skill_prompt, tools = _prepare_skill_catalog(result, skill_reader)
    provider = (
        ReadTimeCurator(
            arm=arm,
            domain=DOMAIN,
            openai_client=openai_client,
            model=curator_model,
            reasoning_effort=curator_reasoning_effort,
            client=client,
            memory_agent_uuid=memory_agent_uuid,
            task_family=family,
            corpus=corpus,
        )
        if arm in {"r-skill", "r-raw"}
        else None
    )
    try:
        with recorder:
            result.trajectory_uuid = recorder.trajectory_uuid
            query = redactor.redact(task["instruction"])
            try:
                briefing = provider.brief(query) if provider else ""
            except AgentContextLimitError:
                result.end_reason = "context_limit"
                briefing = ""
            instructions = _instructions(skill_prompt, briefing)
            items: list[dict[str, Any]] = [{"role": "user", "content": first}]
            messages.append({"role": "user", "content": first})
            recorder.record_message("user", first)
            nudges = 0
            reasoning_tokens = 0
            usage_missing: set[str] = set()
            first_assistant_turn = True
            for _ in range(0 if result.end_reason == "context_limit" else max_steps):
                reasoning = reasoning_request(reasoning_effort, reasoning_summary)
                model_started = time.monotonic()
                try:
                    response = _completion(
                        agent_openai_client or openai_client,
                        {
                            "model": model,
                            "instructions": instructions,
                            "input": items,
                            "tools": tools,
                            "store": False,
                            "include": ["reasoning.encrypted_content"],
                            "reasoning": reasoning,
                            "prompt_cache_key": hashlib.sha256(
                                f"{run_id}:{stage}:{task_id}:{trial}".encode()
                            ).hexdigest()[:32],
                        },
                    )
                    _raise_on_failed_response(response)
                except AgentContextLimitError:
                    result.model_s += time.monotonic() - model_started
                    result.end_reason = "context_limit"
                    break
                result.model_s += time.monotonic() - model_started
                result.steps += 1
                usage = getattr(response, "usage", None)
                result.prompt_tokens += int(getattr(usage, "input_tokens", 0) or 0)
                input_details = getattr(usage, "input_tokens_details", None)
                for field_name in ("cached_tokens", "cache_creation_tokens"):
                    value = getattr(input_details, field_name, None)
                    if value is None:
                        setattr(result, field_name, None)
                        usage_missing.add(field_name)
                    elif field_name not in usage_missing:
                        setattr(
                            result,
                            field_name,
                            (getattr(result, field_name) or 0) + int(value),
                        )
                result.completion_tokens += int(getattr(usage, "output_tokens", 0) or 0)
                provider_reasoning_tokens = getattr(
                    getattr(usage, "output_tokens_details", None), "reasoning_tokens", None
                )
                if provider_reasoning_tokens is None:
                    result.reasoning_tokens = None
                    usage_missing.add("reasoning_tokens")
                elif "reasoning_tokens" not in usage_missing:
                    reasoning_tokens += int(provider_reasoning_tokens)
                    result.reasoning_tokens = reasoning_tokens
                output = [_item_dict(item) for item in getattr(response, "output", None) or []]
                result.stop_reason = getattr(response, "stop_reason", None) or getattr(
                    getattr(response, "incomplete_details", None), "reason", None
                )
                if not output and getattr(usage, "output_tokens", None) == 0:
                    raise AgentEmptyResponseError("The provider returned an empty response")
                items.extend(output)
                recorder.emit(
                    "usage",
                    {
                        "steps": result.steps,
                        "prompt_tokens": result.prompt_tokens,
                        "cached_tokens": result.cached_tokens,
                        "cache_creation_tokens": result.cache_creation_tokens,
                        "completion_tokens": result.completion_tokens,
                        "reasoning_tokens": result.reasoning_tokens,
                    },
                )
                if reasoning_summary:
                    for item in output:
                        if item.get("type") != "reasoning":
                            continue
                        summary = item.get("summary")
                        summaries = summary if isinstance(summary, list) else [summary]
                        for part in summaries:
                            text = (
                                str(part.get("text") or "")
                                if isinstance(part, dict)
                                else str(part or "")
                            )
                            if text:
                                recorder.emit("reasoning", {"text": text})
                calls = [item for item in output if item.get("type") == "function_call"]
                text = str(getattr(response, "output_text", "") or "").strip()
                if result.stop_reason == "refusal":
                    result.end_reason = "refusal"
                    messages.append({"role": "assistant", "content": text})
                    break
                if text:
                    if calls:
                        recorder.emit("message", {"role": "assistant", "text": text})
                    else:
                        recorder.record_message("assistant", text)
                if not calls:
                    messages.append({"role": "assistant", "content": text})
                    nudges += 1
                    if nudges > 2:
                        result.end_reason = "no_tool_call"
                        break
                    nudge = (
                        "Continue with execute_python. Call apis.supervisor.complete_task "
                        "when the task is complete."
                    )
                    items.append({"role": "user", "content": nudge})
                    continue
                tool_calls = []
                for call in calls:
                    raw_arguments = str(call.get("arguments") or "{}")
                    arguments, malformed_error = _decode_function_arguments(raw_arguments, result)
                    tool_call = {
                        "id": call["call_id"],
                        "name": call["name"],
                        "arguments": arguments,
                    }
                    if malformed_error:
                        tool_call.update(
                            {
                                "malformed_error": malformed_error,
                                "raw_arguments": raw_arguments,
                            }
                        )
                    tool_calls.append(tool_call)
                messages.append({"role": "assistant", "content": text, "tool_calls": tool_calls})
                for call in tool_calls:
                    if malformed_error := call.get("malformed_error"):
                        error_output = str(malformed_error)
                        recorder.record_tool_call(
                            call["name"],
                            {"arguments": call["raw_arguments"]},
                            call_id=call["id"],
                        )
                        recorder.record_tool_result(call["name"], error_output, call_id=call["id"])
                        messages.append({"role": "tool", "id": call["id"], "content": error_output})
                        items.append(
                            {
                                "type": "function_call_output",
                                "call_id": call["id"],
                                "output": error_output,
                            }
                        )
                        continue
                    if call["name"] == READ_SKILL_TOOL_NAME and skill_reader:
                        requested = call["arguments"].get("name")
                        markdown, found, resolved_name, repaired_from = skill_reader.read(requested)
                        read_metadata, transcript_text = _skill_read_artifact(
                            markdown,
                            requested,
                            found,
                            resolved_name,
                            repaired_from,
                            redactor,
                        )
                        result.read_skill_calls.append(read_metadata)
                        messages.append(
                            {"role": "tool", "id": call["id"], "content": transcript_text}
                        )
                        items.append(
                            {
                                "type": "function_call_output",
                                "call_id": call["id"],
                                "output": markdown,
                            }
                        )
                        continue
                    if call["name"] != TOOL["name"]:
                        observation = f"Unknown tool: {call['name']}"
                        result.malformed_tool_calls += 1
                        recorder.record_tool_call(
                            call["name"], call["arguments"], call_id=call["id"]
                        )
                        recorder.record_tool_result(call["name"], observation, call_id=call["id"])
                        messages.append({"role": "tool", "id": call["id"], "content": observation})
                        items.append(
                            {
                                "type": "function_call_output",
                                "call_id": call["id"],
                                "output": observation,
                            }
                        )
                        continue
                    result.tool_calls += 1
                    code = str(call["arguments"].get("code") or "")
                    context_tools = appworld_api_names(code)
                    recorder.record_tool_call(
                        call["name"],
                        call["arguments"],
                        call_id=call["id"],
                        context_tools=context_tools,
                    )
                    observation = str(server.call("execute", task_id=task_id, code=code))
                    if len(observation) > MAX_OUTPUT_CHARS:
                        observation = observation[:MAX_OUTPUT_CHARS] + "\n...[output truncated]"
                    recorder.record_tool_result(
                        call["name"],
                        observation,
                        call_id=call["id"],
                        context_tools=context_tools,
                    )
                    messages.append({"role": "tool", "id": call["id"], "content": observation})
                    items.append(
                        {
                            "type": "function_call_output",
                            "call_id": call["id"],
                            "output": observation,
                        }
                    )
                if should_add_read_skill_nudge(skill_reader, first_assistant_turn, tool_calls):
                    items.append({"role": "user", "content": READ_SKILL_NUDGE})
                    messages.append({"role": "user", "content": READ_SKILL_NUDGE})
                    recorder.record_message("user", READ_SKILL_NUDGE)
                first_assistant_turn = False
                if server.call("task_completed", task_id=task_id):
                    result.completed = True
                    result.end_reason = "complete_task"
                    break
            else:
                result.end_reason = result.end_reason or "max_steps"
            evaluation = server.call("evaluate", task_id=task_id, suppress_errors=True)
            _apply_evaluation(result, evaluation)
            evaluation_recorded = True
            recorder.emit(
                "evaluation",
                {
                    "passed": result.succeeded,
                    "reward": result.reward,
                    "detail": {
                        "task_id": task_id,
                        "pass_count": result.pass_count,
                        "num_tests": result.num_tests,
                        "completed": result.completed,
                    },
                },
            )
            recorder.set_result(result.succeeded, True)
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("AppWorld episode %s failed: %s", task_id, type(exc).__name__)
        _record_episode_error(
            result,
            server,
            task_id,
            run_id,
            exc,
            redactor,
            preserve_evaluation=evaluation_recorded,
        )
        if any(
            attempt.get("service") == "zep" and attempt.get("failed")
            for attempt in ledger.attempts()
        ):
            result.trace_error = type(exc).__name__
            result.error_type = "trace_error"
        if isinstance(exc, AgentEmptyResponseError):
            result.error_type = "empty_response"
    finally:
        TRACER.end_trace(trace, output={"reward": result.reward, "error": result.error})
        CURRENT_TRACE.reset(trace_token)
        _close_appworld(
            result,
            server,
            task_id,
            run_id,
            redactor,
            preserve_evaluation=evaluation_recorded,
        )
        exposed: list[str] = []
        if skill_reader is not None:
            result.skill_search_performed = True
            _record_skill_reader_usage(result, skill_reader)
            result.injected_skill_chars = sum(len(text) for text in skill_reader.loaded_markdown)
            exposed = [skill_reader.index.lines(), *skill_reader.loaded_markdown]
        if provider and provider.record:
            record = provider.record
            result.memory_source_keys = list(record.get("source_keys") or [])
            result.memory_skill_uuids = list(record.get("skill_uuids") or [])
            result.retrieval_s = float(record.get("retrieval_s") or 0.0)
            result.curator_input_tokens = int(record.get("curator_input_tokens") or 0)
            result.curator_output_tokens = int(record.get("curator_output_tokens") or 0)
            result.curator_s = float(record.get("curator_s") or 0.0)
            result.briefing = str(record.get("briefing") or "")
            exposed = [result.briefing]
        if exposed and redactor.replacement_map:
            result.leakage_hits = sum(
                len(audit["hits"])
                for audit in audit_skills(
                    [{"uuid": str(index), "markdown": text} for index, text in enumerate(exposed)],
                    [redactor.replacement_map],
                )
            )
        if not reports_reasoning_tokens(model):
            result.reasoning_tokens = None
        result.duration_s = (datetime.now(UTC) - started).total_seconds()
        result.blocked_code_calls = server.blocked_codes
        _record_guard_markers(result, server, redactor)
        _record_ledger(result, ledger)
        _set_missing_end_reason(result)
        CURRENT_LEDGER.reset(ledger_token)
        result.finished_at = datetime.now(UTC).isoformat()
    redacted = [
        {
            key: _redact_tool_call_value(val, redactor) if key == "tool_calls" else val
            for key, val in m.items()
        }
        for m in messages
    ]
    for message in redacted:
        if isinstance(message.get("content"), str):
            message["content"] = redactor.redact(message["content"])
    return result, redacted


def summarize(
    rows: list[dict[str, Any]], raw_rows: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    raw_rows = rows if raw_rows is None else raw_rows
    scored_rows = [row for row in rows if not row.get("error") and not row.get("trace_error")]

    def mean(key: str) -> float:
        return (
            statistics.fmean(float(row.get(key, 0) or 0) for row in scored_rows)
            if scored_rows
            else 0.0
        )

    by_task: dict[str, list[float]] = {}
    for row in scored_rows:
        by_task.setdefault(str(row["task_id"]), []).append(float(row.get("reward", 0.0)))
    errors_by_class: dict[str, int] = {}
    attempt_history: dict[str, list[dict[str, Any]]] = {}
    transport_attempts: dict[str, dict[str, int]] = {}
    for row in raw_rows:
        error_class = row.get("error_type") or row.get("error") or row.get("trace_error")
        if error_class:
            errors_by_class[str(error_class)] = errors_by_class.get(str(error_class), 0) + 1
        key = f"{row.get('task_id')}:{row.get('trial')}"
        attempt_history.setdefault(key, []).append(
            {
                "attempt": row.get("attempt"),
                "error": row.get("error"),
                "error_type": row.get("error_type"),
                "trace_error": row.get("trace_error"),
                "end_reason": row.get("end_reason"),
            }
        )
        for call in row.get("call_attempts") or []:
            call_type = f"{call.get('service')}:{call.get('operation')}"
            counts = transport_attempts.setdefault(call_type, {"attempts": 0, "failed": 0})
            counts["attempts"] += 1
            counts["failed"] += int(bool(call.get("failed")))
    attempts_failed: dict[str, int] = {}
    for row in raw_rows:
        error_class = row.get("error_type") or row.get("error") or row.get("trace_error")
        if error_class:
            attempts_failed[str(error_class)] = attempts_failed.get(str(error_class), 0) + 1
    end_reasons_by_arm: dict[str, dict[str, int]] = {}
    for row in scored_rows:
        end_reason = row.get("end_reason")
        if end_reason in {"no_tool_call", "max_steps", "refusal", "context_limit"}:
            arm = str(row.get("arm") or "none")
            arm_counts = end_reasons_by_arm.setdefault(arm, {})
            arm_counts[str(end_reason)] = arm_counts.get(str(end_reason), 0) + 1
    guard_marker_rows_by_arm: dict[str, int] = {}
    for row in rows:
        if int(row.get("guard_marker_hits") or 0) > 0:
            arm = str(row.get("arm") or "none")
            guard_marker_rows_by_arm[arm] = guard_marker_rows_by_arm.get(arm, 0) + 1
    return {
        "episode_count": len(scored_rows),
        "raw_episode_count": len(raw_rows),
        "task_count": len(by_task),
        "success_rate": (
            statistics.fmean(float(row.get("reward", 0) or 0) for row in scored_rows)
            if scored_rows
            else 0.0
        ),
        "task_pass_all": statistics.fmean(float(min(v)) for v in by_task.values())
        if by_task
        else 0.0,
        "mean_test_pass_fraction": statistics.fmean(
            (int(row.get("pass_count", 0)) / int(row["num_tests"])) if row.get("num_tests") else 0.0
            for row in scored_rows
        )
        if scored_rows
        else 0.0,
        "completion_rate": statistics.fmean(bool(row.get("completed")) for row in scored_rows)
        if scored_rows
        else 0.0,
        "error_count": errors_by_class,
        "attempts_total": len(raw_rows),
        "attempts_failed": attempts_failed,
        "attempt_history": attempt_history,
        "transport_attempts": transport_attempts,
        "guard_marker_rows_by_arm": guard_marker_rows_by_arm,
        "transport_failure_threshold_breached": any(
            counts["attempts"] >= 100 and counts["failed"] / counts["attempts"] > 0.05
            for counts in transport_attempts.values()
        ),
        "end_reason_counts_by_arm": end_reasons_by_arm,
        "malformed_tool_calls": sum(
            int(row.get("malformed_tool_calls") or 0) for row in scored_rows
        ),
        "mean_steps": mean("steps"),
        "mean_prompt_tokens": mean("prompt_tokens"),
        "mean_completion_tokens": mean("completion_tokens"),
        "mean_curator_input_tokens": mean("curator_input_tokens"),
        "mean_curator_output_tokens": mean("curator_output_tokens"),
        "mean_curator_s": mean("curator_s"),
        "mean_duration_s": mean("duration_s"),
        "skill_retrieval_rate": statistics.fmean(
            bool(row.get("skill_search_performed")) for row in scored_rows
        )
        if scored_rows
        else 0.0,
        "skill_load_rate": statistics.fmean(
            any(bool(call.get("found")) for call in row.get("read_skill_calls") or [])
            for row in retrieved
        )
        if (retrieved := [row for row in scored_rows if row.get("skill_search_performed")])
        else None,
        "briefing_rate": statistics.fmean(bool(row.get("briefing")) for row in scored_rows)
        if scored_rows
        else 0.0,
        "leakage_hit_episodes": sum(
            int(row.get("leakage_hits", 0) or 0) > 0 for row in scored_rows
        ),
    }


def next_teacher_attempt(result: AppWorldResult, max_attempts: int) -> int | None:
    """Return the trial number of the next teacher attempt, or None when no attempt is due.

    A failed teacher attempt, also one that ended with an agent error, gets a new attempt.
    An infrastructure error stays for --resume.
    """
    if (
        result.succeeded
        or is_infrastructure_error(result.error, result.error_detail, result.error_type)
        or result.trial >= max_attempts
    ):
        return None
    return result.trial + 1


def resumed_teacher_attempts(
    latest: dict[tuple[str, int], dict[str, Any]], max_attempts: int
) -> list[tuple[int, int, str | None]]:
    """Return (task_index, trial, parent Trajectory UUID) for each attempt that a resumed run owes."""
    last: dict[str, dict[str, Any]] = {}
    for (task_id, trial), row in latest.items():
        if trial <= max_attempts and (task_id not in last or trial > int(last[task_id]["trial"])):
            last[task_id] = row
    owed: list[tuple[int, int, str | None]] = []
    for row in last.values():
        if (
            row.get("error")
            or row.get("trace_error")
            or row.get("succeeded")
            or row["trial"] >= max_attempts
        ):
            continue
        owed.append((int(row["task_index"]), row["trial"] + 1, row.get("trajectory_uuid")))
    return owed


def startup_jobs(
    task_ids: list[str],
    latest: dict[tuple[str, int], dict[str, Any]],
    trials: int,
    max_attempts: int,
) -> list[tuple[int, str, int, str | None]]:
    """Return one (task_index, task_id, trial, parent Trajectory UUID) job for each attempt a run owes at start."""
    jobs: list[tuple[int, str, int, str | None]] = [
        (index, task_id, trial, None)
        for index, task_id in enumerate(task_ids)
        for trial in range(1, trials + 1)
        if (task_id, trial) not in latest or _infrastructure_error(latest[(task_id, trial)])
    ]
    task_indexes = {task_id: index for index, task_id in enumerate(task_ids)}
    for (task_id, trial), row in latest.items():
        if _infrastructure_error(row) and task_id in task_indexes:
            job = (task_indexes[task_id], task_id, trial, None)
            if job not in jobs:
                jobs.append(job)
    if max_attempts > 1:
        jobs += [
            (index, task_ids[index], trial, parent)
            for index, trial, parent in resumed_teacher_attempts(latest, max_attempts)
            if index < len(task_ids)
        ]
    return list(
        {
            (index, trial): (index, task_id, trial, parent)
            for index, task_id, trial, parent in jobs
        }.values()
    )


def _infrastructure_error(row: dict[str, Any]) -> bool:
    return bool(row.get("error") or row.get("trace_error"))


def _attempt_counts(path: Path) -> dict[tuple[str, int], int]:
    """Return the number of recorded attempts of each episode, also for rows with no attempt field."""
    counts: dict[tuple[str, int], int] = {}
    for line in path.read_text().splitlines():
        if line:
            row = json.loads(line)
            key = (str(row["task_id"]), int(row["trial"]))
            counts[key] = max(counts.get(key, 0) + 1, int(row.get("attempt") or 0))
    return counts


def _latest_rows(path: Path) -> dict[tuple[str, int], dict[str, Any]]:
    latest: dict[tuple[str, int], dict[str, Any]] = {}
    for line in path.read_text().splitlines():
        if line:
            row = json.loads(line)
            latest[(str(row["task_id"]), int(row["trial"]))] = row
    return latest


def _lock_server(stack: ExitStack, url: str) -> None:
    """Hold an exclusive lock on one AppWorld server for the life of the run.

    A server keeps one active task world, so two runs that share a server
    corrupt each other's episodes. A second run waits for the lock.
    """
    digest = hashlib.sha256(url.rstrip("/").encode()).hexdigest()[:16]
    handle = stack.enter_context(
        (Path(tempfile.gettempdir()) / f"appworld-server-{digest}.lock").open("w")
    )
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        LOGGER.info("Waiting for another run to release AppWorld server %s", url)
        fcntl.flock(handle, fcntl.LOCK_EX)


def _lock_stage(stack: ExitStack, output_dir: Path, stage: str) -> None:
    handle = stack.enter_context((output_dir / f".{stage}.lock").open("a"))
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise RuntimeError(f"The {stage} stage is already running for this run directory") from exc


def _manifest_arguments(args: argparse.Namespace) -> dict[str, Any]:
    arguments = vars(args).copy()
    arguments["resume"] = False
    arguments.pop("allow_code_change", None)
    return arguments


def _validate_resume_manifest(
    path: Path, expected: dict[str, Any], allow_code_change: bool = False
) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"The stage has no manifest to resume: {path}")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    for key, value in expected.items():
        if key == "agent_uuid":
            continue
        if key == "git_commit" and allow_code_change:
            continue
        if manifest.get(key) != value:
            raise ValueError(f"Resume manifest mismatch for {key}")
    if expected.get("agent_uuid") and manifest.get("agent_uuid") != expected["agent_uuid"]:
        raise ValueError("Resume manifest mismatch for agent_uuid")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=["teacher", "heldout"], required=True)
    parser.add_argument("--arm", choices=["none", "skills", "r-skill", "r-raw"])
    parser.add_argument("--split", default="train")
    parser.add_argument("--skills-snapshot")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=1,
        help="Teacher stage only: run a task again after a failed attempt, up to this total.",
    )
    parser.add_argument("--model", default="gpt-6-luna")
    parser.add_argument(
        "--agent-base-url",
        help="An OpenAI-compatible Responses endpoint for the agent, for example OpenRouter.",
    )
    parser.add_argument("--agent-api-key-env", default="OPENROUTER_API_KEY")
    parser.add_argument(
        "--agent-provider", help="Send every agent request to this OpenRouter provider."
    )
    parser.add_argument("--reasoning-effort", default="high")
    parser.add_argument("--max-steps", type=int, default=40)
    parser.add_argument("--memory-agent-uuid")
    parser.add_argument(
        "--agent-uuid",
        help="Record the Trajectories on this Agent, for example the Agent that owns the Skills.",
    )
    parser.add_argument(
        "--family-scope",
        choices=["domain", "scenario"],
        default="domain",
        help="Use one task family for AppWorld, or one task family for each scenario.",
    )
    parser.add_argument(
        "--variants", default="all", help="Comma-separated scenario variants, for example 1 or 2,3."
    )
    parser.add_argument("--scenarios", type=int, help="Use only the first N scenarios.")
    parser.add_argument("--compilation-mode", choices=["scheduled", "manual", "on_close"])
    parser.add_argument("--task-ids", help="Run these exact comma-separated AppWorld task IDs.")
    parser.add_argument(
        "--record-uses",
        action="store_true",
        help="Record each injected Skill use and its outcome on the Skill-owning Agent.",
    )
    parser.add_argument("--source-run-id")
    parser.add_argument("--curator-model")
    parser.add_argument("--curator-reasoning-effort", default="medium")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--servers", nargs="+", required=True)
    parser.add_argument("--appworld-root", required=True)
    parser.add_argument(
        "--allow-server-secrets",
        action="store_true",
        help="Allow secret-like server environment names for a local debug run.",
    )
    parser.add_argument("--results-root", default=str(Path(__file__).resolve().parent / "results"))
    add_price_arguments(parser)
    parser.add_argument("--compile-timeout", type=float, default=90 * 60)
    parser.add_argument(
        "--allow-code-change",
        action="store_true",
        help="With --resume, accept a new commit. Each row records the commit that ran it.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Run again only the episodes that have no result or that ended with an error.",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        _require_clean_repository()
    except RuntimeError as exc:
        parser.error(str(exc))
    try:
        _validate_model_arguments(
            args.model,
            args.reasoning_effort,
            args.arm,
            args.curator_model,
            args.curator_reasoning_effort,
        )
    except ValueError as exc:
        parser.error(str(exc))
    if args.stage == "heldout" and not args.arm:
        parser.error("The heldout stage requires --arm")
    if args.max_attempts < 1 or (args.max_attempts > 1 and args.stage != "teacher"):
        parser.error("--max-attempts must be 1 or more, and more than 1 only for the teacher stage")
    if args.max_attempts > 1 and args.trials != 1:
        parser.error("--max-attempts requires --trials 1")
    if args.arm == "r-skill" and not args.memory_agent_uuid:
        parser.error(f"The {args.arm} arm requires --memory-agent-uuid")
    if args.arm == "skills" and not args.skills_snapshot:
        parser.error("The skills arm requires --skills-snapshot")
    if args.arm == "r-raw" and not args.source_run_id:
        parser.error("The r-raw arm requires --source-run-id")
    stage = "teacher" if args.stage == "teacher" else f"heldout-{args.arm}"
    args.compilation_mode = _compilation_mode(args.stage, args.compilation_mode)
    task_ids = split_task_ids(Path(args.appworld_root), args.split, args.variants, args.scenarios)
    try:
        task_ids = _select_task_ids(task_ids, args.task_ids, args.limit)
    except ValueError as exc:
        parser.error(str(exc))
    if not task_ids:
        parser.error("The selected AppWorld stage has no tasks.")
    families = sorted({task_family_for(task_id, args.family_scope) for task_id in task_ids})
    snapshot_path = Path(args.skills_snapshot) if args.skills_snapshot else None
    snapshot_data: dict[str, Any] = {}
    if snapshot_path is not None and args.arm == "skills":
        snapshot_data = json.loads(snapshot_path.read_text(encoding="utf-8"))
        if snapshot_data.get("complete") is False:
            parser.error("The skills arm will not use an incomplete snapshot")
        try:
            _validate_snapshot_agent(snapshot_data, args.stage, args.agent_uuid)
            for family in families:
                FrozenSkillReader.from_snapshot(snapshot_path, task_family=family)
        except ValueError as exc:
            parser.error(str(exc))
    output_dir = Path(args.results_root) / args.run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "simulations").mkdir(parents=True, exist_ok=True)
    jsonl_path = output_dir / f"{stage}.jsonl"
    manifest_path = output_dir / f"manifest-{stage}.json"
    if jsonl_path.exists() and not args.resume:
        raise ValueError(f"The {stage} stage already exists; use --resume or a new run ID")
    if args.resume and not jsonl_path.is_file():
        raise ValueError(f"The {stage} stage has no result journal to resume")
    server_locks = ExitStack()
    _lock_stage(server_locks, output_dir, stage)
    for url in args.servers:
        _lock_server(server_locks, url)
    appworld_servers = [AppWorldServer(url) for url in args.servers]
    secret_servers: list[tuple[str, list[str]]] = []
    for server in appworld_servers:
        secret_names = server.preflight(task_ids[0], args.run_id)
        if secret_names:
            secret_servers.append((server.url, secret_names))
    if secret_servers and not args.allow_server_secrets:
        root = str(Path(args.appworld_root))
        commands = []
        for url, names in secret_servers:
            port = urlsplit(url).port or 80
            command = (
                "env -i HOME=$HOME PATH=$PATH LANG=$LANG "
                "<venv>/bin/appworld serve environment "
                f"--port {port} --root {root} --no-show-usage"
            )
            commands.append(
                f"AppWorld server {url} has secret-like environment names {', '.join(names)}.\n"
                f"Start it with:\n{command}"
            )
        for server in appworld_servers:
            server.http.close()
        server_locks.close()
        raise AppWorldServerSecretsError(
            "The AppWorld run stopped before the stage because agent code can read the server "
            "environment.\n" + "\n".join(commands)
        )
    config = load_config()
    client = zep_client(config.api_key, config.api_url)
    openai_client = OpenAI(api_key=config.openai_api_key, http_client=openai_http_client("openai"))
    agent_openai_client = agent_client(
        openai_client, args.agent_base_url, args.agent_api_key_env, args.agent_provider, args.model
    )
    git_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=Path(__file__).resolve().parents[2],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    manifest = {
        "arguments": _manifest_arguments(args),
        "zep_url": config.api_url,
        "agent_base_url": args.agent_base_url
        or (
            "https://api.anthropic.com"
            if args.model.startswith("claude-")
            else "https://api.openai.com/v1"
        ),
        "agent_uuid": args.agent_uuid,
        "family_scope": args.family_scope,
        "task_ids": task_ids,
        "snapshot_sha256": snapshot_sha256(snapshot_path),
        "git_commit": git_commit,
    }
    if args.resume:
        manifest = _validate_resume_manifest(manifest_path, manifest, args.allow_code_change)
        if manifest.get("git_commit") != git_commit:
            manifest.setdefault("previous_git_commits", []).append(manifest.get("git_commit"))
            manifest["git_commit"] = git_commit
            manifest_path.write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        agent = {"agent_uuid": manifest["agent_uuid"]}
    else:
        if manifest_path.exists():
            raise ValueError(f"The {stage} stage already has a manifest; use --resume")
        agent = (
            {"agent_uuid": args.agent_uuid}
            if args.agent_uuid
            else ensure_agent(
                client,
                f"appworld-{stage}",
                run_id=args.run_id,
                compilation_mode=args.compilation_mode,
                task_families=[] if args.family_scope == "scenario" else [FAMILY],
            )
        )
        manifest["agent_uuid"] = agent["agent_uuid"]
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    if args.arm == "r-skill":
        wait_for_learning_idle(
            client, str(args.memory_agent_uuid), families, timeout=args.compile_timeout
        )
    if args.record_uses:
        freeze_compilation(client, str(agent["agent_uuid"]))
    corpus = (
        RawCorpus.load(Path(args.results_root), args.source_run_id, DOMAIN)
        if args.arm == "r-raw"
        else None
    )
    servers: queue.Queue[AppWorldServer] = queue.Queue()
    for server in appworld_servers:
        servers.put(server)

    def job(
        task_index: int, task_id: str, trial: int, parent_trajectory_uuid: str | None = None
    ) -> tuple[AppWorldResult, list[dict[str, Any]]]:
        server = servers.get()
        try:
            return run_episode(
                server=server,
                client=client,
                openai_client=openai_client,
                agent_openai_client=agent_openai_client,
                agent_uuid=agent["agent_uuid"],
                stage=stage,
                run_id=args.run_id,
                task_id=task_id,
                task_index=task_index,
                trial=trial,
                model=args.model,
                reasoning_effort=args.reasoning_effort,
                max_steps=args.max_steps,
                arm=args.arm,
                memory_agent_uuid=args.memory_agent_uuid,
                corpus=corpus,
                curator_model=args.curator_model or args.model,
                curator_reasoning_effort=args.curator_reasoning_effort,
                family=task_family_for(task_id, args.family_scope),
                record_uses=args.record_uses,
                parent_trajectory_uuid=parent_trajectory_uuid,
                skills_snapshot=Path(args.skills_snapshot) if args.skills_snapshot else None,
            )
        finally:
            servers.put(server)

    started = time.monotonic()
    latest = _latest_rows(jsonl_path) if args.resume else {}
    attempts = _attempt_counts(jsonl_path) if args.resume else {}
    raw_rows = (
        [json.loads(line) for line in jsonl_path.read_text().splitlines() if line.strip()]
        if args.resume
        else []
    )
    completed = {key for key, row in latest.items() if not _infrastructure_error(row)}
    jobs = startup_jobs(task_ids, latest, args.trials, args.max_attempts)
    scheduled = {(task_id, trial) for _, task_id, trial, _ in jobs}
    job_queue = list(jobs)
    LOGGER.info("resume=%s skipped_jobs=%d pending_jobs=%d", args.resume, len(completed), len(jobs))
    rows: list[dict[str, Any]] = []
    stage_stop_reason: str | None = None
    with (
        ThreadPoolExecutor(max_workers=len(args.servers)) as executor,
        jsonl_path.open("a" if args.resume else "w") as out,
        (output_dir / f"transcripts-{stage}.jsonl").open(
            "a" if args.resume else "w"
        ) as transcripts,
        (output_dir / f"traces-{stage}.jsonl").open("a") as traces,
    ):
        pending: set[Future[tuple[AppWorldResult, list[dict[str, Any]]]]] = set()

        def fill_pending() -> None:
            while job_queue and len(pending) < len(args.servers) and stage_stop_reason is None:
                index, task_id, trial, parent = job_queue.pop(0)
                pending.add(executor.submit(job, index, task_id, trial, parent))

        fill_pending()
        while pending:
            finished, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in finished:
                result, messages = future.result()
                row = asdict(result)
                row["agent_uuid"] = agent["agent_uuid"]
                row["snapshot_sha256"] = manifest["snapshot_sha256"]
                row["model"] = args.model
                row["effort"] = args.reasoning_effort
                row["family_scope"] = args.family_scope
                row["git_commit"] = manifest["git_commit"]
                attempt_key = (result.task_id, result.trial)
                row["attempt"] = attempts.get(attempt_key, 0) + 1
                attempts[attempt_key] = row["attempt"]
                row["cost_usd"] = cost_usd(row, args)
                (
                    output_dir
                    / "simulations"
                    / f"{stage}-{DOMAIN}-{result.task_index}-{result.trial}.json"
                ).write_text(json.dumps({"task_id": result.task_id, "messages": messages}))
                out.write(json.dumps(row, sort_keys=True) + "\n")
                out.flush()
                transcripts.write(
                    json.dumps({"task_id": result.task_id, "messages": messages}) + "\n"
                )
                transcripts.flush()
                if result.trajectory_uuid:
                    traces.write(
                        json.dumps(
                            {
                                "trace_uuid": result.trajectory_uuid,
                                "task_family": task_family_for(result.task_id, args.family_scope),
                                "passed": bool(
                                    result.succeeded and not result.error and not result.trace_error
                                ),
                                "error": result.error,
                                "error_type": result.error_type,
                            }
                        )
                        + "\n"
                    )
                    traces.flush()
                rows.append(row)
                raw_rows.append(row)
                latest[attempt_key] = row
                if stop_reason := _stage_stop_reason(result):
                    stage_stop_reason = stop_reason
                transport_counts: dict[str, dict[str, int]] = {}
                for raw_row in raw_rows:
                    for call in raw_row.get("call_attempts") or []:
                        call_type = f"{call.get('service')}:{call.get('operation')}"
                        counts = transport_counts.setdefault(
                            call_type, {"attempts": 0, "failed": 0}
                        )
                        counts["attempts"] += 1
                        counts["failed"] += int(bool(call.get("failed")))
                if any(
                    counts["attempts"] >= 100 and counts["failed"] / counts["attempts"] > 0.05
                    for counts in transport_counts.values()
                ):
                    stage_stop_reason = stage_stop_reason or "transport_failure_threshold"
                attempt = next_teacher_attempt(result, args.max_attempts)
                if stage_stop_reason is None:
                    _schedule_teacher_retry(result, attempt, completed, scheduled, job_queue)
                LOGGER.info(
                    "done=%d pending=%d success=%s", len(rows), len(pending), result.succeeded
                )
            fill_pending()
    summary = {
        "stage": stage,
        "agent_uuid": agent["agent_uuid"],
        "conditions": {key: val for key, val in vars(args).items() if key not in {"servers"}},
        "elapsed_s": time.monotonic() - started,
        "metrics": summarize(list(latest.values()), raw_rows),
        "stage_stop_reason": stage_stop_reason,
    }
    if _should_export_teacher(args.stage, stage_stop_reason):
        passed = {
            family: sum(
                bool(row.get("succeeded"))
                for row in latest.values()
                if row.get("task_family", family) == family
            )
            for family in families
        }
        compile_wait = wait_for_compilation(
            client,
            str(agent["agent_uuid"]),
            passed,
            ceiling_seconds=args.compile_timeout,
        )
        summary["compile_wait"] = compile_wait
        try:
            snapshot = compile_snapshot(
                client,
                str(agent["agent_uuid"]),
                families,
                output_dir / "skills",
                traces_path=output_dir / f"traces-{stage}.jsonl",
                wait=compile_wait,
            )
            summary["snapshot_sha256"] = snapshot["sha256"]
            summary["funnel"] = write_funnel(
                output_dir / "skills",
                families,
                snapshot,
                output_dir / f"traces-{stage}.jsonl",
            )
        except Exception as exc:
            failure: dict[str, Any] = {"type": type(exc).__name__, "message": str(exc)}
            if isinstance(exc, SnapshotExportError):
                failure["failures"] = exc.failures
            summary["snapshot_export_error"] = failure
            (output_dir / "teacher.json").write_text(
                json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            print(json.dumps(summary, indent=2, sort_keys=True))
            TRACER.flush()
            raise SystemExit(1) from exc
    (output_dir / f"{stage}.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    TRACER.flush()
    if stage_stop_reason:
        raise RuntimeError(f"The {stage} stage stopped after an error: {stage_stop_reason}")


if __name__ == "__main__":
    main()
