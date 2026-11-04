from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import signal
import sys
import tempfile
import threading
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from zep_cloud import Zep

from .agent import SKILL_ARMS, WALL_CLOCK_TIMEOUT, SkillIndex, SkillReader, run_agent, selected_skills_block
from .analysis import date_sensitivity_report, reproduction_gate, reproduction_report
from .claude_sdk_agent import claude_cli_version, cli_environment_manifest, run_claude_sdk_agent
from .dataset import (
    TOWNHALL_FIXED_TASK_IDS,
    Task,
    load_tasks,
    overlay_task_ids,
    validate_verifier_only_overlay,
)
from .decide import SkillDecider
from .errors import (
    AbortRequestedError,
    BudgetExceeded,
    InfrastructureError,
    NonRetryableProviderError,
    PolicyFlagError,
    RetryExhaustedError,
)
from .funnel import write_compile_funnel
from .gym import GymClient, image_digest
from .gym_sets import gym_set_container_names, task_for_gym_set
from .ledger import CostLedger
from .mcp_client import MCPClient
from .preflight import (
    SERVER_NAMES,
    itsm_opened_by_patch_sha256,
    preflight_gym,
    preflight_report,
    preflight_reset,
    teams_townhall_patch_sha256,
)
from .prices import PRICES
from .providers.anthropic_route import AnthropicRoute
from .providers.deepseek_route import DEEPSEEK_EFFORTS, DEEPSEEK_MATRIX_EFFORTS, DEEPSEEK_MODEL, DeepSeekRoute
from .providers.openai_route import OpenAIRoute
from .providers.openrouter_route import OpenRouterRoute
from .providers.zai_route import ZAIRoute
from .recompile import (
    DEFAULT_REPLAY_CONCURRENCY,
    filter_replay_export,
    git_commit,
    load_replay_rows,
    prepare_replay_file,
    replay_manifest,
    replay_traces,
)
from .retry import failure_details
from .runner import (
    RunStore,
    classify_episode,
    freeze_manifest,
    interleave_arms,
    load_task_sample,
    ramp_steps,
    sample_score_tasks,
    task_row_count,
)
from .skills import (
    WAIT_FOR_COMPILATION_RULE,
    TrajectoryRecorder,
    assert_no_score_literals,
    build_skill_index,
    compile_snapshot,
    ensure_agent,
    list_learning_run_ids,
    load_snapshot_details,
    skill_frontmatter,
    wait_for_compilation,
)
from .snapshot_merge import merge_compile_outputs
from .split import make_split, write_split
from .tracing import RowTrace, TracedMCP, TracedRoute, require_langfuse_env

CONTAINERS = gym_set_container_names(0)
ZAI_ROUTE_CAP = 16
EXECUTOR_MATRIX_SETTINGS = [
    {"model": "gpt-6-luna", "effort": "low"},
    {"model": "gpt-6-luna", "effort": "medium"},
    {"model": "gpt-6-luna", "effort": "max"},
    {"model": "claude-haiku-5-5", "effort": "low"},
    {"model": "claude-haiku-5-5", "effort": "medium"},
    {"model": "claude-haiku-5-5", "effort": "max"},
    *({"model": DEEPSEEK_MODEL, "effort": effort} for effort in DEEPSEEK_MATRIX_EFFORTS),
]


def _container_image_digests(tasks: tuple[Task, ...], gym_set: int = 0) -> dict[str, str]:
    used_server_names = {server["mcp_server_name"] for task in tasks for server in task.gym_servers_config}
    known_server_names = set(SERVER_NAMES.values())
    missing_server_names = used_server_names - known_server_names
    if missing_server_names:
        names = ", ".join(sorted(missing_server_names))
        raise RuntimeError(f"No Gym container mapping for server names: {names}")
    containers = gym_set_container_names(gym_set)
    return {name: image_digest(containers[name]) for name in containers if SERVER_NAMES[name] in used_server_names}


class _RunSignalState:
    def __init__(self) -> None:
        self.stop_requested = threading.Event()
        self.abort_requested = threading.Event()
        self.first_signal: int | None = None
        self.scheduling_started = False

    def handle(self, signum: int, _frame: Any) -> None:
        if self.first_signal is None:
            self.first_signal = signum
            if not self.scheduling_started:
                raise KeyboardInterrupt
            self.stop_requested.set()
        else:
            self.abort_requested.set()

    @property
    def signal_name(self) -> str | None:
        return signal.Signals(self.first_signal).name if self.first_signal is not None else None

    @property
    def exit_code(self) -> int:
        return 128 + self.first_signal if self.first_signal is not None else 0


def _install_run_signal_handlers(state: _RunSignalState | None = None) -> _RunSignalState:
    state = _RunSignalState() if state is None else state
    signal.signal(signal.SIGINT, state.handle)
    signal.signal(signal.SIGTERM, state.handle)
    return state


EFFORTS = {
    "claude-opus-5-5": "max",
    "claude-fable-5-1": "max",
    "claude-haiku-5-5": "low",
    "gpt-6-luna": "low",
    "gpt-6.1-sol": "xhigh",
    "gpt-6-astra": "max",
    "glm-5.3-flash": "low",
    "glm-5.2": "xhigh",
    "nemotron-3.5-lightning": "on",
    DEEPSEEK_MODEL: "max",
}


def zep_client() -> Zep:
    options: dict[str, str] = {"api_key": os.environ["ZEP_API_KEY"]}
    if base_url := _zep_base_url():
        options["base_url"] = base_url
    return Zep(**options)


def _require_zep_environment(stage: str) -> None:
    if not os.getenv("ZEP_API_KEY"):
        raise SystemExit(f"Set ZEP_API_KEY before the {stage} stage.")


def _zep_base_url() -> str | None:
    base_url = os.getenv("ZEP_BASE_URL")
    if not base_url:
        return None
    parsed = urlsplit(base_url)
    if not parsed.scheme or not parsed.hostname:
        raise SystemExit("ZEP_BASE_URL must include a host.")
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    netloc = f"{host}:{parsed.port}" if parsed.port else host
    return f"{parsed.scheme}://{netloc}{parsed.path.rstrip('/')}"


class CompositeMCPClient:
    def __init__(self, clients: dict[str, MCPClient]) -> None:
        self.clients = clients
        self.tools: list[dict] = []
        self.by_tool: dict[str, tuple[MCPClient, str]] = {}
        self.selected_tools: tuple[str, ...] = ()

    def start(self, task: object) -> list[dict]:
        available: dict[str, tuple[dict, MCPClient, str]] = {}
        found: set[str] = set()
        for server_name, client in self.clients.items():
            client.initialize()
            for tool in client.tools_for(task):
                tool_name = tool["name"]
                exposed_name = f"{server_name}__{tool_name}"
                remote_tool_name = tool.get("_remote_tool_name", tool_name)
                public_tool = {key: value for key, value in tool.items() if key != "_remote_tool_name"}
                available[exposed_name] = ({**public_tool, "name": exposed_name}, client, remote_tool_name)
                found.add(tool_name)
        missing = [name for name in task.selected_tools if name not in found]
        if missing:
            raise InfrastructureError(f"Selected MCP tools are missing: {missing}")
        self.tools = [item[0] for item in available.values()]
        self.by_tool = {name: (item[1], item[2]) for name, item in available.items()}
        self.selected_tools = tuple(self.by_tool)
        return self.tools

    def call_tool(self, name: str, arguments: Any) -> dict:
        tool = self.by_tool.get(name)
        if tool is None:
            return {"content": [{"type": "text", "text": f"{name} is not a valid tool"}], "isError": True}
        if not isinstance(arguments, dict):
            return {"content": [{"type": "text", "text": "Tool arguments are not valid"}], "isError": True}
        client, original_name = tool
        return client.call_tool(original_name, arguments)

    def close(self) -> None:
        for client in self.clients.values():
            client.close()


def _route(model: str, capture_dir: Path | None, ledger: CostLedger) -> object:
    if model.startswith("claude-"):
        return AnthropicRoute(model, capture_dir=capture_dir, ledger=ledger)
    if model in {"gpt-6-luna", "gpt-6.1-sol", "gpt-6-astra"}:
        return OpenAIRoute(model=model, capture_dir=capture_dir, ledger=ledger)
    if model == DEEPSEEK_MODEL:
        return DeepSeekRoute(model, capture_dir=capture_dir, ledger=ledger)
    if model == "nemotron-3.5-lightning":
        return OpenRouterRoute(model, capture_dir=capture_dir, ledger=ledger)
    return ZAIRoute(model, capture_dir=capture_dir, ledger=ledger)


def _seed_path(filename: str) -> Path:
    root = Path(os.getenv("EOG_DATA_ROOT", str(Path.home() / ".cache" / "enterpriseops-gym" / "dbs")))
    path = root / filename
    if not path.is_file():
        raise FileNotFoundError(f"Database seed file does not exist: {path}")
    return path


def _task_families(task: object) -> tuple[str, ...]:
    families = {f"eog.{task.domain}"}
    server_domains = {
        "sn-csm-server": "csm",
        "gym-teams-mcp": "teams",
        "gym-calendar": "calendar",
        "gym-email-mcp": "email",
        "gym-itsm-mcp": "itsm",
        "sn-hr-internal": "hr",
        "gym-google-drive-mcp": "drive",
    }
    if task.domain == "hybrid":
        families.update(
            f"eog.{server_domains[server['mcp_server_name']]}"
            for server in task.gym_servers_config
            if server["mcp_server_name"] in server_domains
        )
    return tuple(sorted(families))


def _skill_run_fields(result: object | None) -> dict:
    """The read_skill, nudge, and user-index fields of a row; zero values when the episode gave no result."""
    calls = list(getattr(result, "read_skill_calls", ()) or ())
    index_chars = int(getattr(result, "user_index_chars", 0) or 0)
    return {
        "read_skill_calls": calls,
        "read_skill_repaired_name": sum(1 for call in calls if call.get("repaired_from") is not None),
        "nudged": bool(getattr(result, "nudged", False)),
        "forced_first_read": bool(getattr(result, "forced_first_read", False)),
        "user_index_chars": index_chars,
        "user_index_tokens_est": index_chars // 4,
    }


def _skill_metadata(arm: str, skills: list[dict[str, str]], block_hash: str) -> dict:
    selected = [{"id": item["id"], "sha256": item["sha256"]} for item in skills]
    return {
        "skills": selected or "none",
        "skills_count": len(selected),
        "skill_block_sha256": block_hash if arm in SKILL_ARMS else "",
    }


def _teacher_reward(verifier_success: bool, finish_reason: str) -> int:
    return int(
        verifier_success and finish_reason not in {"active_time_limit", "context_overflow", "error_during_execution"}
    )


def _resolve_run_config(
    stage: str,
    teacher_harness: str,
    model: str | None,
    effort: str | None,
    trials: int | None,
) -> tuple[str, str, int]:
    default_model = "claude-fable-5-1" if stage == "teacher" and teacher_harness == "stirrup" else "claude-opus-5-5"
    selected_model = model or default_model
    if selected_model == DEEPSEEK_MODEL:
        if effort is None:
            raise SystemExit("Pass --effort when you run the DeepSeek V4.1 Flash model.")
        if effort not in DEEPSEEK_EFFORTS:
            accepted = ", ".join(DEEPSEEK_EFFORTS)
            raise SystemExit(f"Unsupported DeepSeek effort {effort!r}. Choose one of: {accepted}.")
    selected_effort = effort if effort is not None else EFFORTS[selected_model]
    selected_trials = trials if trials is not None else (1 if stage == "teacher" else 3)
    return selected_model, selected_effort, selected_trials


def _claude_task_fields(agent_context: dict, teacher_harness: str) -> dict:
    if teacher_harness != "claude_code":
        return {}
    return {
        "claude_task_tools_used": list(agent_context.get("claude_task_tools_used", [])),
        "claude_task_update_owner_denials": list(agent_context.get("claude_task_update_owner_denials", [])),
        "claude_cli_stderr_tail": agent_context.get("claude_cli_stderr_tail", ""),
        "claude_cli_attempt_limit": agent_context.get("claude_cli_attempt_limit"),
        "claude_infrastructure_error": agent_context.get("claude_infrastructure_error"),
        "claude_result_subtype": agent_context.get("claude_result_subtype"),
        "claude_request_log_path": agent_context.get("claude_request_log_path"),
        "claude_request_count": agent_context.get("claude_request_count", 0),
        "claude_request_gate_passed": (
            agent_context.get("claude_request_count", 0) > 0
            and not agent_context.get("claude_request_gate_failed", False)
        ),
        "claude_request_gate_errors": agent_context.get("claude_request_gate_errors", []),
        "claude_compaction_events": agent_context.get("claude_compaction_events", []),
        "claude_final_text": agent_context.get("claude_final_text", ""),
        "claude_input_validation_error_count": agent_context.get("claude_input_validation_error_count", 0),
        "claude_tool_infrastructure_error": agent_context.get("claude_tool_infrastructure_error"),
        "model_calls": agent_context.get("model_calls"),
        "cli_num_turns": agent_context.get("cli_num_turns"),
    }


def _executor_tasks(
    tasks: tuple[Task, ...],
    split: dict[str, Any],
    *,
    explicit_selection: bool,
    task_scope: str = "split",
    sample_executor_tasks: bool = True,
) -> tuple[Task, ...]:
    """An explicit --tasks or --task-list runs every named task, teach and score; without one, the executor runs the sampled score tasks."""  # noqa: E501
    if task_scope == "all" or explicit_selection:
        return tuple(task for task in tasks if not task.known_defect)
    score_tasks = tuple(
        task for task in tasks if split["tasks"][task.task_id]["side"] == "score" and not task.known_defect
    )
    return sample_score_tasks(score_tasks, split) if sample_executor_tasks else score_tasks


def _reproduction_overlay_ids(tasks: tuple[Task, ...], overlay_dir: Path | None) -> set[str]:
    overlay_ids = set(overlay_task_ids(tasks))
    if overlay_dir is not None:
        overlay_ids |= set(overlay_task_ids(tasks, overlay_dir=overlay_dir))
    return overlay_ids


def _skill_query(user_prompt: str) -> str:
    terms = re.findall(r"[^\W_]+", user_prompt, flags=re.UNICODE)[:64]
    return " ".join(terms).encode("utf-8")[:512].decode("utf-8", errors="ignore")


def _skill_id(hit: Any) -> str:
    names = ("skill_uuid", "skill_id", "uuid", "uuid_")
    values = hit if isinstance(hit, dict) else {name: getattr(hit, name, None) for name in names}
    return str(next((values.get(name) for name in names if values.get(name)), ""))


def _hit_task_family(hit: Any) -> str | None:
    family = hit.get("task_family") if isinstance(hit, dict) else getattr(hit, "task_family", None)
    return str(family) if family is not None else None


def _index_skill_selection(
    payload: dict[str, Any],
    tasks: tuple[Task, ...],
    snapshot_skills: dict[str, dict[str, str]],
    snapshot_sha256: str,
) -> dict[tuple[str, str], list[str]]:
    if payload.get("snapshot_sha256") != snapshot_sha256:
        raise SystemExit("The saved Skill selection has a different snapshot SHA-256.")
    entries = payload.get("selections")
    if not isinstance(entries, list):
        raise SystemExit("The saved Skill selection has no selections list.")

    expected_queries = {
        (task.task_id, family): _skill_query(task.user_prompt) for task in tasks for family in _task_families(task)
    }
    selection: dict[tuple[str, str], list[str]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise SystemExit("The saved Skill selection has an invalid entry.")
        task_id = str(entry.get("task_id", ""))
        family = str(entry.get("family", ""))
        key = (task_id, family)
        if key in selection:
            raise SystemExit(f"The saved Skill selection repeats task {task_id} and family {family}.")
        query = entry.get("query")
        skill_ids = entry.get("skill_ids")
        if not isinstance(skill_ids, list) or any(not isinstance(skill_id, str) for skill_id in skill_ids):
            raise SystemExit(f"The saved Skill selection has invalid Skill IDs for task {task_id}, family {family}.")
        for skill_id in skill_ids:
            skill = snapshot_skills.get(skill_id)
            if skill is None or skill["task_family"] != family:
                raise SystemExit(f"Skill ID {skill_id} is outside the frozen snapshot family {family}.")
        if key in expected_queries and query != expected_queries[key]:
            raise SystemExit(f"The saved query differs for task {task_id}, family {family}.")
        selection[key] = skill_ids

    missing = sorted(set(expected_queries) - set(selection))
    if missing:
        task_id, family = missing[0]
        raise SystemExit(f"The saved Skill selection is missing task {task_id}, family {family}.")
    return selection


def _prepare_skill_selection(
    path: Path,
    tasks: tuple[Task, ...],
    snapshot_skills: dict[str, dict[str, str]],
    snapshot_sha256: str,
    agent_uuid: str,
    zep: Zep | None,
    *,
    resume: bool,
    page_size: int = 3,
    decider: SkillDecider | None = None,
    family_agents: Mapping[str, str] | None = None,
) -> dict[tuple[str, str], list[str]]:
    """The Skill selection of one run: the top `page_size` search hits of each task family, or, with a decider,
    the one hit that the Decisions API picks from them (the `skills_decided` arm)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        if path.exists():
            try:
                payload = json.loads(path.read_text())
            except json.JSONDecodeError as error:
                raise SystemExit(f"The saved Skill selection is not valid JSON: {path}") from error
            return _index_skill_selection(payload, tasks, snapshot_skills, snapshot_sha256)
    if resume:
        raise SystemExit(f"Cannot resume without the saved Skill selection: {path}")
    if zep is None:
        raise SystemExit("Set ZEP_API_KEY to create the Skill selection.")

    partial_path = path.with_suffix(".partial.json")
    partial: dict[str, dict[str, Any]] = {}
    if decider is not None:
        partial = _load_partial_decisions(partial_path, snapshot_sha256, decider)
    selections = []
    for task in tasks:
        query = _skill_query(task.user_prompt)
        for family in _task_families(task):
            response = zep.agent.skill.search(
                (family_agents or {}).get(family, agent_uuid),
                query=query,
                page_size=page_size,
                task_family=family,
                include_markdown=True,
            )
            skill_ids = []
            for hit in list(response.items or [])[:page_size]:
                skill_id = _skill_id(hit)
                hit_family = _hit_task_family(hit)
                skill = snapshot_skills.get(skill_id)
                if skill is None or skill["task_family"] != family or (hit_family is not None and hit_family != family):
                    displayed_id = skill_id or "<missing>"
                    raise SystemExit(
                        f"Skill ID {displayed_id} from live search is outside the frozen snapshot family {family}."
                    )
                skill_ids.append(skill_id)
            entry: dict[str, Any] = {
                "task_id": task.task_id,
                "family": family,
                "query": query,
                "skill_ids": skill_ids,
            }
            if decider is not None:
                # `partial` holds only the decisions of this snapshot SHA-256 (`_load_partial_decisions`),
                # so a cached decision read the same Skill descriptions as this run.
                cached = partial.get(f"{task.task_id}|{family}")
                if cached is not None and cached.get("candidates") == skill_ids:
                    entry.update(cached)
                else:
                    entry.update(_decide_skill(decider, task, skill_ids, snapshot_skills))
                    partial[f"{task.task_id}|{family}"] = {
                        key: entry[key] for key in ("skill_ids", "candidates", "probabilities", "refused")
                    }
                    _write_json(
                        partial_path,
                        {
                            "snapshot_sha256": snapshot_sha256,
                            "calls": decider.calls,
                            "usage": dict(decider.usage),
                            "decisions": partial,
                        },
                    )
            selections.append(entry)
    selections.sort(key=lambda entry: (entry["task_id"], entry["family"]))
    payload = {"snapshot_sha256": snapshot_sha256, "selections": selections}
    if decider is not None:
        payload["decision"] = {
            "model": decider.model,
            "candidates_per_family": page_size,
            "calls": decider.calls,
            "usage": dict(decider.usage),
        }
    _write_json(path, payload)
    if decider is not None and partial_path.exists():
        partial_path.unlink()
    return _index_skill_selection(payload, tasks, snapshot_skills, snapshot_sha256)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(temporary_path, path)


def _load_partial_decisions(path: Path, snapshot_sha256: str, decider: SkillDecider) -> dict[str, dict[str, Any]]:
    """The decisions of an interrupted selection run on the same snapshot, keyed by `task_id|family`.

    A restart reuses each decision whose candidates are unchanged, so that no Decisions call is paid twice,
    and it continues the call and token counts of the interrupted run. A partial file of another snapshot
    is ignored, because its decisions read other Skill descriptions.
    """
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict) or payload.get("snapshot_sha256") != snapshot_sha256:
        return {}
    decisions = payload.get("decisions")
    if not isinstance(decisions, dict):
        return {}
    decider.calls += int(payload.get("calls") or 0)
    for key, value in (payload.get("usage") or {}).items():
        if key in decider.usage:
            decider.usage[key] += int(value or 0)
    return decisions


def _decide_skill(
    decider: SkillDecider,
    task: Task,
    candidate_ids: list[str],
    snapshot_skills: dict[str, dict[str, str]],
) -> dict[str, Any]:
    """The Decisions API picks one of the candidate Skills from the task text and the Skill descriptions.

    The result replaces `skill_ids` with the one chosen Skill and keeps the candidates and the probability
    of each one. A task family with no search hit keeps an empty selection. On a refusal (the Decisions API
    selects no candidate) the family gets no Skill, and `refused` records it.
    """
    if not candidate_ids:
        return {"candidates": [], "probabilities": {}, "refused": False}
    candidates = []
    for skill_id in candidate_ids:
        description = skill_frontmatter(snapshot_skills[skill_id]["markdown"]).get("description", "").strip()
        candidates.append((skill_id, description or snapshot_skills[skill_id].get("name", skill_id)))
    choice, probabilities = decider.decide(task.user_prompt, candidates)
    return {
        "skill_ids": [choice] if choice is not None else [],
        "candidates": list(candidate_ids),
        "probabilities": probabilities,
        "refused": choice is None,
    }


def _select_snapshot_skills(
    task: Task,
    snapshot_skills: dict[str, dict[str, str]],
    selection: dict[tuple[str, str], list[str]],
) -> list[dict[str, str]]:
    selected = []
    for family in _task_families(task):
        for skill_id in selection[(task.task_id, family)]:
            skill = snapshot_skills.get(skill_id)
            if skill is None or skill["task_family"] != family:
                raise ValueError(f"Skill ID {skill_id} is outside the frozen snapshot family {family}.")
            selected.append(skill)
    return selected


def _executor_skill_inputs(
    row: dict[str, str],
    task: Task,
    snapshot_skills: dict[str, dict[str, str]],
    selection: dict[tuple[str, str], list[str]],
    paired_arm: str = "skills",
    skill_short_ids: bool = False,
    skill_name_enum: bool = False,
    skills_multi_read: bool = False,
    decided_selection: dict[tuple[str, str], list[str]] | None = None,
) -> tuple[str | None, str, list[dict[str, str]], SkillIndex | None]:
    """The Skills prompt block, its hash, the Skill records, and the index of one executor row.

    The `skills` arm gets the index of all snapshot Skills of the task families and the read_skill tool.
    The `skills_decided` arm gets the body of the one Skill that the Decisions API picked.
    A `none` row records the Skills of its paired treatment arm and gets no block.
    """
    arm = row["arm"] if row["arm"] in SKILL_ARMS else paired_arm
    if arm not in SKILL_ARMS:
        raise ValueError(f"Unknown Skills arm: {arm}")
    skill_index = None
    if arm == "skills":
        skill_index = build_skill_index(snapshot_skills, _task_families(task), short_ids=skill_short_ids)
        block = skill_index.block(decline=skill_name_enum, multi_read=skills_multi_read)
        skill_items = [{"id": entry.id, "sha256": entry.sha256} for entry in skill_index.entries]
    else:
        if arm == "skills_decided":
            if decided_selection is None:
                raise ValueError("The skills_decided arm has no decided Skill selection")
            selection = decided_selection
        selected = _select_snapshot_skills(task, snapshot_skills, selection)
        block = selected_skills_block([item["markdown"] for item in selected])
        skill_items = [{"id": item["id"], "sha256": item["sha256"]} for item in selected]
    block_hash = hashlib.sha256(block.encode()).hexdigest() if block else ""
    if row["arm"] not in SKILL_ARMS:
        return None, block_hash, skill_items, skill_index
    return block or None, block_hash, skill_items, skill_index


def _report_with_reproduction_gate(report: dict) -> dict:
    return {
        **report,
        "gate_passed": reproduction_gate(report["all"]["p_hat"]),
    }


def _require_reproduction_report(path: Path | None) -> dict:
    if path is None:
        raise SystemExit("Pass --reproduction-report with a report whose gate_passed value is true.")
    report = json.loads(path.read_text())
    if report.get("gate_passed") is not True:
        raise SystemExit("The reproduction report did not pass its gate.")
    return report


def _cost_ledger_path(root: Path, stage: str, model: str, effort: str) -> Path:
    return root / f"costs.{stage}.{model}.{effort}.jsonl"


def _validate_cost_limit(model: str, max_usd: float | None) -> None:
    if max_usd is not None and PRICES.get(model) is None:
        raise SystemExit(f"--max-usd cannot be used with unpriced model {model}.")


def _record_skill_selection_failure(row: dict, error: Exception, store: RunStore) -> dict:
    stopped = {
        **row,
        "status": "stopped",
        "stop_run": True,
        "stop_reason": f"skill_selection:{type(error).__name__}: {error}",
        "model_call_http_statuses": [],
    }
    store.checkpoint(stopped)
    return stopped


def _should_record_trajectory(row: dict, zep: Zep | None, agent_uuid: str | None) -> bool:
    return row.get("stage") == "teacher" and zep is not None and agent_uuid is not None


def _closed_trace_counts(traces_path: Path, task_families: list[str]) -> dict[str, int]:
    """Count passed Traces because the compiler selector omits failed Traces past its failure reserve."""
    counts = {family: 0 for family in task_families}
    if not traces_path.exists():
        return counts
    for line in traces_path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if (
            row.get("infra_failed", False)
            or ("close_action" in row and row["close_action"] != "close")
            or row.get("passed") is not True
        ):
            continue
        family = row.get("task_family")
        if family in counts:
            counts[family] += 1
    return counts


def _parse_compile_failure_families(value: str) -> list[str]:
    return sorted(family.strip() for family in value.split(",") if family.strip())


def _compile_failures(
    output_dir: Path,
    task_families: list[str],
    *,
    include_pending_families: list[str] | None = None,
) -> dict[str, dict[str, int]]:
    failures_by_family: dict[str, dict[str, int]] = {}
    included_pending_families = set(include_pending_families or [])
    for family in task_families:
        report_path = output_dir / family.replace(".", "_") / "compile_report.json"
        if not report_path.exists():
            print(
                f"No compile report for {family}: {report_path}. The preflight skips this family.",
                file=sys.stderr,
            )
            continue
        report = json.loads(report_path.read_text())
        errors: dict[str, int] = {}
        for failure in report.get("failures", []):
            error = failure.get("error", "UnknownError")
            if not isinstance(error, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", error):
                error = "UnknownError"
            if (
                family in included_pending_families
                and report.get("pending_family_included") is True
                and error in {"compile_failed", "compile_wait_timeout"}
            ):
                continue
            errors[error] = errors.get(error, 0) + 1
        if errors:
            failures_by_family[family] = errors
    return failures_by_family


def _compile_failure_message_from_failures(failures: dict[str, dict[str, int]]) -> str | None:
    if not failures:
        return None
    details = "; ".join(f"{family}: {', '.join(sorted(errors))}" for family, errors in sorted(failures.items()))
    return f"Skill compilation failed: {details}"


def _compile_failure_message(
    output_dir: Path,
    task_families: list[str],
    *,
    include_pending_families: list[str] | None = None,
) -> str | None:
    return _compile_failure_message_from_failures(
        _compile_failures(output_dir, task_families, include_pending_families=include_pending_families)
    )


def _executor_compile_preflight(
    snapshot_dir: Path,
    task_families: list[str],
    allowed_families: list[str] | None,
) -> dict[str, dict[str, int]]:
    failures = _compile_failures(snapshot_dir, task_families)
    if not failures:
        return failures
    if allowed_families is not None and set(failures).issubset(allowed_families):
        details = ", ".join(f"{family}: {sum(errors.values())}" for family, errors in sorted(failures.items()))
        print(f"Compile failures allowed by --allow-compile-failures: {details}", file=sys.stderr)
        return failures
    raise SystemExit(_compile_failure_message_from_failures(failures))


def _split_for_tasks(
    all_tasks: tuple[Task, ...],
    overlay_dir: Path | None,
    *,
    revised_dir: Path | None = None,
) -> dict[str, Any]:
    base_tasks = load_tasks() if overlay_dir is not None else all_tasks
    split = make_split(base_tasks)
    if overlay_dir is not None:
        overlay_ids = set(overlay_task_ids(all_tasks, overlay_dir=overlay_dir, include_unmatched=True))
        base_task_ids = set(split["tasks"])
        task_ids = {task.task_id for task in all_tasks}
        revised_ids = set(
            overlay_task_ids(
                base_tasks,
                overlay_dir=revised_dir or Path(__file__).resolve().parents[2] / "data" / "revised",
                include_unmatched=True,
            )
        )
        unmatched_revised_ids = revised_ids - base_task_ids
        missing_task_ids = sorted((overlay_ids | task_ids) - base_task_ids - unmatched_revised_ids)
        if missing_task_ids:
            raise SystemExit(f"Overlay task IDs are missing from the base split: {', '.join(missing_task_ids)}")
    return split


def _resolve_noop_baseline_path(args: argparse.Namespace) -> Path:
    if args.noop_baseline is not None:
        baseline_path = args.noop_baseline
    elif args.overlay_dir is not None:
        baseline_path = args.overlay_dir / "noop_baseline.jsonl"
    else:
        baseline_path = Path(__file__).resolve().parents[2] / "data" / "noop_baseline.jsonl"
    baseline_path = baseline_path.resolve()
    if not baseline_path.is_file():
        raise SystemExit(f"No-op baseline file does not exist: {baseline_path}")
    return baseline_path


def _gym_clients(task: object, database_ids: dict[str, str]) -> dict[str, GymClient]:
    return {
        server["mcp_server_name"]: GymClient(
            server["mcp_server_url"],
            name=server["mcp_server_name"],
            context=server.get("context", {}),
        )
        for server in task.gym_servers_config
    }


def _load_excluded_task_reasons(path: Path | None, tasks: tuple[Task, ...]) -> dict[str, str]:
    if path is None:
        return {}
    if not path.is_file():
        raise SystemExit(f"Excluded tasks file does not exist: {path}")
    try:
        entries = json.loads(path.read_text())
    except json.JSONDecodeError as error:
        raise SystemExit(f"Excluded tasks file is not valid JSON: {path}") from error
    if not isinstance(entries, list):
        raise SystemExit("Excluded tasks file must contain a list.")

    task_ids = {task.task_id for task in tasks}
    reasons: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise SystemExit("Excluded tasks entries must be objects.")
        task_id = entry.get("task_id")
        reason = entry.get("reason")
        if not isinstance(task_id, str) or not isinstance(reason, str) or not reason:
            raise SystemExit("Each excluded task must have a task_id and a non-empty reason.")
        if task_id in reasons:
            raise SystemExit(f"Excluded tasks file repeats task {task_id}.")
        if task_id not in task_ids:
            raise SystemExit(f"Excluded task {task_id} is not in the loaded task set.")
        reasons[task_id] = reason
    return reasons


def _corrected_score_fields(
    *,
    dual_scoring: bool,
    excluded_reason: str | None,
    reward: int | None = None,
    verifier_passes: list[bool] | None = None,
    status: str | None = None,
    error: dict[str, str] | None = None,
) -> dict[str, Any]:
    if not dual_scoring:
        return {}
    fields: dict[str, Any] = {
        "corrected_reward": reward,
        "corrected_verifier_passes": verifier_passes,
        "corrected_status": status,
    }
    if excluded_reason is not None:
        fields["corrected_excluded_reason"] = excluded_reason
    if error is not None:
        fields["corrected_error"] = error
    return fields


def _verifier_score_metadata(verifier: Any, reward: int, status: str) -> dict[str, Any]:
    return {
        "reward": reward,
        "status": status,
        "verifier_passes": list(verifier.passed),
        "verifier_actual": list(verifier.actual),
        "verifier_expected": list(verifier.expected),
        "verifier_errors": list(verifier.errors),
    }


def _execute_row(
    row: dict,
    *,
    task_by_id: dict,
    model: str,
    effort: str,
    run_id: str,
    store: RunStore,
    cost_ledger: CostLedger,
    capture_dir: Path | None,
    skills_block: str | None,
    skills_hash: str,
    skills: list[dict[str, str]],
    zep: Zep | None,
    agent_uuid: str | None,
    trace_path: Path | None = None,
    run_dir: Path | None = None,
    noop_baseline_path: Path | None = None,
    teacher_harness: str = "stirrup",
    _claude_task_state: dict[str, list] | None = None,
    _policy_retry: int = 0,
    abort_requested: threading.Event | None = None,
    _cleanup_errors: list[dict] | None = None,
    langfuse_client: Any | None = None,
    _row_trace: RowTrace | None = None,
    _route_instance: Any | None = None,
    skill_index: SkillIndex | None = None,
    force_first_read: bool = False,
    skill_name_enum: bool = False,
    skills_multi_read: bool = False,
    system_preamble: str | None = None,
    original_task_by_id: dict[str, Task] | None = None,
    excluded_task_reasons: dict[str, str] | None = None,
) -> dict:
    result = None
    task = task_by_id[row["task_id"]]
    dual_scoring = original_task_by_id is not None
    original_task = original_task_by_id.get(task.task_id, task) if original_task_by_id is not None else task
    excluded_reason = (excluded_task_reasons or {}).get(task.task_id)
    skill_index_record = skill_index.metadata() if skill_index is not None else []
    row_trace = _row_trace
    route_instance = _route_instance
    if route_instance is None and langfuse_client is not None and teacher_harness == "stirrup":
        route_instance = _route(model, capture_dir, cost_ledger)
    if row_trace is None and langfuse_client is not None and teacher_harness == "stirrup":
        row_trace = RowTrace(
            langfuse_client,
            task=task,
            run_id=run_id,
            trial=row["trial"],
            arm=row["arm"],
            model=model,
            effort=effort,
            route=route_instance.route,
            skills_hash=skills_hash,
            skills=skills,
            run_dir=run_dir or Path("results"),
            skill_index=skill_index_record,
        )
    if task.known_defect:
        output = {
            **row,
            "run_date_utc": datetime.now(UTC).isoformat(),
            "status": "invalid",
            "reward": 0,
            **({"model_calls": 0} if teacher_harness == "stirrup" else {}),
            "error_class": "KnownDefect",
            "known_defect_reasons": list(task.known_defect_reasons),
            "model_call_http_statuses": [],
            **_skill_metadata(row["arm"], skills, skills_hash),
            **_corrected_score_fields(dual_scoring=dual_scoring, excluded_reason=excluded_reason),
        }
        try:
            return output
        finally:
            if row_trace is not None:
                output.update(row_trace.close(output))
            store.checkpoint(output)
    setup_complete = False
    try:
        database_ids = {server["mcp_server_name"]: str(uuid4()) for server in task.gym_servers_config}
        gym_clients = _gym_clients(task, database_ids)
        mcp_clients = {}
        started = time.monotonic()
        model_call_latencies: list[float] = []
        model_call_http_statuses: list[int | None] = []
        tool_call_latencies: list[float] = []
        verifier_call_latencies: list[float] = []
        attempt_failures: list[dict] = []
        cleanup_errors = list(_cleanup_errors or [])
        claude_task_state = _claude_task_state if _claude_task_state is not None else {}
        retrying_policy = False
        output: dict[str, Any] | None = None
        agent_context = {
            "cell": row["cell"],
            "arm": row["arm"],
            "task": task.task_id,
            "trial": row["trial"],
            "model_call_latencies": model_call_latencies,
            "model_call_http_statuses": model_call_http_statuses,
            "tool_call_latencies": tool_call_latencies,
            "attempt_failures": attempt_failures,
            "abort_requested": abort_requested,
            "deadline_monotonic": started + WALL_CLOCK_TIMEOUT,
            "claude_task_tools_used": claude_task_state.setdefault("claude_task_tools_used", []),
            "claude_task_update_owner_denials": claude_task_state.setdefault("claude_task_update_owner_denials", []),
        }
        setup_complete = True
    finally:
        if not setup_complete and row_trace is not None and not row_trace.closed:
            row_trace.close(None)
    try:
        for server in task.gym_servers_config:
            name = server["mcp_server_name"]
            sql = _seed_path(server["seed_database_file"]).read_text()
            gym_clients[name].seed(database_ids[name], sql)
            endpoint = server["mcp_server_url"].rstrip("/") + task.mcp_endpoint
            mcp_clients[name] = MCPClient(
                endpoint,
                database_id=database_ids[name],
                context=server.get("context", {}),
            )
        gym = next(iter(gym_clients.values()))
        gym.check_reset(task, database_ids, baseline_path=noop_baseline_path)
        mcp = CompositeMCPClient(mcp_clients)
        tools = mcp.start(task)
        route = None
        if teacher_harness == "stirrup":
            route = route_instance if route_instance is not None else _route(model, capture_dir, cost_ledger)
        skill_reader = (
            SkillReader(skill_index) if row["arm"] == "skills" and skill_index and skill_index.entries else None
        )
        if row_trace is not None:
            mcp = TracedMCP(mcp, row_trace)
            route = TracedRoute(route, row_trace)
            if skill_reader is not None:
                skill_reader = TracedMCP(skill_reader, row_trace)
        recorder = (
            TrajectoryRecorder(
                zep,
                agent_uuid,
                task,
                run_id,
                row["trial"],
                trace_path=trace_path,
                side=row.get("side", "teach"),
            )
            if _should_record_trajectory(row, zep, agent_uuid)
            else None
        )
        if recorder:
            recorder.__enter__()
        try:
            if teacher_harness == "claude_code":
                capture_root = capture_dir or ((run_dir or Path("results")) / "captures")
                capture_path = capture_root / f"{task.task_id}-trial-{row['trial']}.json"
                result = run_claude_sdk_agent(
                    task,
                    route,
                    mcp,
                    tools,
                    effort=effort,
                    arm=row["arm"],
                    skills_block=skills_block if row["arm"] == "skills" else None,
                    selected_tools=getattr(mcp, "selected_tools", None),
                    context=agent_context,
                    capture_path=capture_path,
                    cost_ledger=cost_ledger,
                )
            else:
                result = run_agent(
                    task,
                    route,
                    mcp,
                    tools,
                    effort=effort,
                    arm=row["arm"],
                    skills_block=skills_block if row["arm"] in SKILL_ARMS else None,
                    selected_tools=getattr(mcp, "selected_tools", None),
                    context=agent_context,
                    skill_reader=skill_reader,
                    force_first_read=force_first_read,
                    skill_name_enum=skill_name_enum,
                    skills_multi_read=skills_multi_read,
                    system_preamble=system_preamble,
                )
            verifier_started = time.monotonic()
            try:
                verifier = gym.verify(original_task, database_ids)
            finally:
                verifier_call_latencies.append(time.monotonic() - verifier_started)
            corrected_verifier = None
            corrected_error = None
            if dual_scoring and excluded_reason is None:
                verifier_started = time.monotonic()
                try:
                    corrected_verifier = gym.verify(task, database_ids)
                except Exception as error:
                    corrected_error = {
                        "error_class": type(error).__name__,
                        "error_message": failure_details(error)["error_message"],
                    }
                finally:
                    verifier_call_latencies.append(time.monotonic() - verifier_started)

            status = classify_episode(original_task, result, verifier)
            reward = _teacher_reward(verifier.success, result.finish_reason)
            corrected_status = None
            corrected_reward = None
            corrected_passes = None
            if corrected_verifier is not None and excluded_reason is None:
                corrected_status = classify_episode(task, result, corrected_verifier)
                corrected_reward = _teacher_reward(corrected_verifier.success, result.finish_reason)
                corrected_passes = list(corrected_verifier.passed)
            elif corrected_error is not None:
                corrected_status = "infra_failed"
            score_metadata = None
            if dual_scoring:
                score_metadata = {
                    "original": _verifier_score_metadata(verifier, reward, status),
                    "corrected": (
                        {
                            "reward": None,
                            "status": None,
                            "verifier_passes": None,
                            "excluded_reason": excluded_reason,
                        }
                        if excluded_reason is not None
                        else (
                            {
                                "reward": None,
                                "status": corrected_status,
                                "verifier_passes": None,
                                "error": corrected_error,
                            }
                            if corrected_error is not None
                            else _verifier_score_metadata(
                                corrected_verifier,
                                corrected_reward,
                                corrected_status,
                            )
                        )
                    ),
                }
            if recorder:
                for event in result.events:
                    recorder.add_event(
                        event["type"],
                        str(event.get("content") or json.dumps(event, sort_keys=True)),
                        tool=event.get("tool"),
                        tool_call_id=event.get("tool_call_id"),
                        metadata={
                            key: value
                            for key, value in event.items()
                            if key not in {"type", "content", "tool", "tool_call_id"}
                        },
                    )
                recorder.close(verifier.success, verified=True, score_metadata=score_metadata)
            output = {
                **row,
                "run_date_utc": datetime.now(UTC).isoformat(),
                "status": status,
                "reward": reward,
                "verifier_passes": list(verifier.passed),
                **_corrected_score_fields(
                    dual_scoring=dual_scoring,
                    excluded_reason=excluded_reason,
                    reward=corrected_reward,
                    verifier_passes=corrected_passes,
                    status=corrected_status,
                    error=corrected_error,
                ),
                "finish_reason": result.finish_reason,
                "claude_final_text": getattr(result, "final_text", ""),
                "skill_index": skill_index_record,
                **_skill_run_fields(result),
                "turns": result.turns,
                "usage": result.usage,
                **_skill_metadata(row["arm"], skills, skills_hash),
                "latencies": {
                    "model_calls_seconds": model_call_latencies,
                    "tool_calls_seconds": tool_call_latencies,
                    "verifier_calls_seconds": verifier_call_latencies,
                },
                "active_seconds": result.active_seconds,
                "model_calls": (result.turns if teacher_harness == "claude_code" else len(model_call_latencies)),
                "cli_num_turns": agent_context.get("cli_num_turns"),
                "model_call_attempts": (
                    agent_context.get("claude_proxy_model_call_count", 0)
                    if teacher_harness == "claude_code"
                    else len(model_call_latencies)
                ),
                "model_call_http_statuses": model_call_http_statuses,
                "proxy_model_call_count": agent_context.get("claude_proxy_model_call_count"),
                "proxy_attempts": agent_context.get("claude_proxy_attempts", []),
                "claude_init_tools": agent_context.get("claude_init_tools"),
                "claude_model_usage": agent_context.get("claude_model_usage"),
                "claude_cost_usd": agent_context.get("claude_cost_usd"),
                "claude_sdk_cost_usd": agent_context.get("claude_sdk_cost_usd"),
                "claude_capture_path": agent_context.get("claude_capture_path"),
                "claude_message_usage": agent_context.get("claude_message_usage"),
                **_claude_task_fields(agent_context, teacher_harness),
                "http_429_attempts": sum(attempt.get("http_status") == 429 for attempt in attempt_failures),
                "attempt_failures": attempt_failures,
                "latency_seconds": time.monotonic() - started,
            }
            if row_trace is not None:
                row_trace.add_scores(
                    reward=output["reward"],
                    verifier=verifier,
                    corrected_reward=corrected_reward,
                    corrected_verifier=corrected_verifier,
                    score_metadata=score_metadata,
                )
        finally:
            mcp.close()
    except PolicyFlagError as error:
        if "recorder" in locals() and recorder:
            recorder.close(False, infra_failed=True)
        if _policy_retry == 0:
            output = None
            retrying_policy = True
        else:
            output = {
                **row,
                "run_date_utc": datetime.now(UTC).isoformat(),
                "status": "policy_flagged",
                **_skill_run_fields(result),
                "reward": 0,
                "error_class": type(error).__name__,
                **failure_details(error),
                **_skill_metadata(row["arm"], skills, skills_hash),
                **_claude_task_fields(agent_context, teacher_harness),
                **_corrected_score_fields(dual_scoring=dual_scoring, excluded_reason=excluded_reason),
                "model_call_http_statuses": model_call_http_statuses,
            }
    except (InfrastructureError, RetryExhaustedError) as error:
        if "recorder" in locals() and recorder:
            recorder.close(False, infra_failed=True)
        output = {
            **row,
            "run_date_utc": datetime.now(UTC).isoformat(),
            "status": "infra_failed",
            **_skill_run_fields(result),
            "error_class": type(error).__name__,
            **failure_details(
                error,
                cli_stderr_tail=(
                    agent_context.get("claude_cli_stderr_tail", "") if teacher_harness == "claude_code" else None
                ),
            ),
            **_skill_metadata(row["arm"], skills, skills_hash),
            **_corrected_score_fields(dual_scoring=dual_scoring, excluded_reason=excluded_reason),
            "latencies": {
                "model_calls_seconds": model_call_latencies,
                "tool_calls_seconds": tool_call_latencies,
                "verifier_calls_seconds": verifier_call_latencies,
            },
            "model_calls": (
                agent_context.get("model_calls") if teacher_harness == "claude_code" else len(model_call_latencies)
            ),
            "cli_num_turns": agent_context.get("cli_num_turns"),
            "model_call_attempts": (
                agent_context.get("claude_proxy_model_call_count", 0)
                if teacher_harness == "claude_code"
                else len(model_call_latencies)
            ),
            "model_call_http_statuses": model_call_http_statuses,
            "http_429_attempts": sum(attempt.get("http_status") == 429 for attempt in attempt_failures),
            "attempt_failures": attempt_failures,
            "claude_init_tools": locals().get("agent_context", {}).get("claude_init_tools"),
            "proxy_attempts": locals().get("agent_context", {}).get("claude_proxy_attempts", []),
            "proxy_model_call_count": locals().get("agent_context", {}).get("claude_proxy_model_call_count", 0),
            "claude_capture_path": locals().get("agent_context", {}).get("claude_capture_path"),
            "usage": agent_context.get("claude_usage"),
            "claude_model_usage": agent_context.get("claude_model_usage"),
            "claude_cost_usd": agent_context.get("claude_cost_usd"),
            "claude_sdk_cost_usd": agent_context.get("claude_sdk_cost_usd"),
            "stop_run": bool(agent_context.get("stop_run")),
            **_claude_task_fields(agent_context, teacher_harness),
            "latency_seconds": time.monotonic() - started,
        }
    except (AbortRequestedError, BudgetExceeded, NonRetryableProviderError) as error:
        if "recorder" in locals() and recorder:
            recorder.close(False, infra_failed=True)
        output = {
            **row,
            "run_date_utc": datetime.now(UTC).isoformat(),
            "status": "stopped",
            **_skill_run_fields(result),
            "stop_run": True,
            "stop_reason": "signal_abort" if isinstance(error, AbortRequestedError) else "budget_or_provider_stop",
            "error_class": type(error).__name__,
            **failure_details(error),
            **_skill_metadata(row["arm"], skills, skills_hash),
            **_corrected_score_fields(dual_scoring=dual_scoring, excluded_reason=excluded_reason),
            "model_call_http_statuses": model_call_http_statuses,
            "attempt_failures": attempt_failures,
            "claude_init_tools": locals().get("agent_context", {}).get("claude_init_tools"),
            "proxy_attempts": locals().get("agent_context", {}).get("claude_proxy_attempts", []),
            "proxy_model_call_count": locals().get("agent_context", {}).get("claude_proxy_model_call_count", 0),
            "claude_capture_path": locals().get("agent_context", {}).get("claude_capture_path"),
            **_claude_task_fields(agent_context, teacher_harness),
            "latency_seconds": time.monotonic() - started,
        }
    except Exception as error:
        if "recorder" in locals() and recorder:
            recorder.close(False, infra_failed=True)
        output = {
            **row,
            "run_date_utc": datetime.now(UTC).isoformat(),
            "status": "partial",
            **_skill_run_fields(result),
            "stop_run": True,
            "error_class": type(error).__name__,
            **failure_details(error),
            **_skill_metadata(row["arm"], skills, skills_hash),
            **_corrected_score_fields(dual_scoring=dual_scoring, excluded_reason=excluded_reason),
            "model_call_http_statuses": model_call_http_statuses,
            **_claude_task_fields(agent_context, teacher_harness),
            "latency_seconds": time.monotonic() - started,
        }
    finally:
        if "recorder" in locals() and recorder:
            recorder.__exit__(None, None, None)
        for name, client in gym_clients.items():
            try:
                client.delete(database_ids[name])
            except Exception as error:
                cleanup_errors.append(
                    {
                        "gym_server": name,
                        "error_class": type(error).__name__,
                        **failure_details(error),
                    }
                )
            finally:
                client.close()
        if row_trace is not None and not retrying_policy:
            trace_fields = row_trace.close(output)
            if output is not None:
                output.update(trace_fields)
    if output is None:
        if row_trace is not None:
            row_trace.attempt = 1
        try:
            return _execute_row(
                row,
                task_by_id=task_by_id,
                model=model,
                effort=effort,
                run_id=run_id,
                store=store,
                cost_ledger=cost_ledger,
                capture_dir=capture_dir,
                skills_block=skills_block,
                skills_hash=skills_hash,
                skills=skills,
                zep=zep,
                agent_uuid=agent_uuid,
                trace_path=trace_path,
                run_dir=run_dir,
                noop_baseline_path=noop_baseline_path,
                teacher_harness=teacher_harness,
                _claude_task_state=claude_task_state,
                _policy_retry=1,
                abort_requested=abort_requested,
                _cleanup_errors=cleanup_errors,
                langfuse_client=langfuse_client,
                _row_trace=row_trace,
                _route_instance=route_instance,
                skill_index=skill_index,
                force_first_read=force_first_read,
                skill_name_enum=skill_name_enum,
                skills_multi_read=skills_multi_read,
                system_preamble=system_preamble,
                original_task_by_id=original_task_by_id,
                excluded_task_reasons=excluded_task_reasons,
            )
        finally:
            if row_trace is not None and not row_trace.closed:
                row_trace.close(None)
    if cleanup_errors:
        output["cleanup_error"] = cleanup_errors
    if teacher_harness == "stirrup":
        output["model_calls"] = len(model_call_latencies)
    store.checkpoint(output)
    return output


def main() -> None:
    signals = _RunSignalState()
    try:
        _install_run_signal_handlers(signals)
        _run_main(signals)
    except KeyboardInterrupt:
        if signals.first_signal is None or signals.scheduling_started:
            raise
        print(
            f"Received {signals.signal_name} during setup; exiting with status {signals.exit_code}.",
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(signals.exit_code) from None


def _resolve_arms(stage: str, requested: str) -> tuple[str, ...] | None:
    if not requested.strip():
        return None
    if stage != "executor":
        raise SystemExit("--arms is only valid with --stage executor.")
    values = tuple(value.strip() for value in requested.split(","))
    if any(value not in {"none", *SKILL_ARMS} for value in values):
        raise SystemExit("--arms must contain only none, skills, and skills_decided.")
    if len(set(values)) != len(values):
        raise SystemExit("--arms must not contain duplicate values.")
    return tuple(arm for arm in ("none", *SKILL_ARMS) if arm in values)


def _resolve_skip_arms(
    stage: str,
    resume: bool,
    arms: tuple[str, ...],
    requested: str,
    reason: str | None,
) -> tuple[str, ...]:
    if not requested.strip():
        if reason is not None:
            raise SystemExit("--skip-reason requires --skip-arms.")
        return ()
    if stage != "executor":
        raise SystemExit("--skip-arms is only valid with --stage executor.")
    if not resume:
        raise SystemExit("--skip-arms requires --resume.")
    values = tuple(value.strip() for value in requested.split(","))
    if any(not value for value in values):
        raise SystemExit("--skip-arms must be a comma-separated list of non-empty arm names.")
    if len(set(values)) != len(values):
        raise SystemExit("--skip-arms must not contain duplicate values.")
    invalid = [value for value in values if value not in arms]
    if invalid:
        raise SystemExit(f"--skip-arms values must be included in --arms: {', '.join(arms)}.")
    if reason is None or not reason.strip():
        raise SystemExit("--skip-reason must be non-empty when --skip-arms is set.")
    return tuple(arm for arm in arms if arm in values)


def _drop_pending_arms(
    root: Path,
    store: RunStore,
    rows: list[dict[str, Any]],
    arms: tuple[str, ...],
    reason: str,
) -> list[dict[str, Any]]:
    if not arms:
        return rows
    dropped_path = root / "dropped_arms.jsonl"
    dropped_at = datetime.now(UTC).isoformat()
    with dropped_path.open("a", encoding="utf-8") as output:
        for arm in arms:
            rows_dropped = sum(row["arm"] == arm for row in rows)
            output.write(
                json.dumps(
                    {
                        "arm": arm,
                        "dropped_at": dropped_at,
                        "reason": reason,
                        "rows_dropped": rows_dropped,
                        "rows_kept_by_status": store.latest_status_counts(arm),
                    },
                    sort_keys=True,
                )
                + "\n"
            )
            print(f"skip-arms: {arm} dropped {rows_dropped} pending rows")
    dropped = set(arms)
    return [row for row in rows if row["arm"] not in dropped]


def _require_teach_replay_rows(
    rows: list,
    split: dict,
    tasks: tuple[Task, ...],
    *,
    task_scope: str = "split",
) -> None:
    """Stop when a replay row is outside the selected task scope."""
    task_by_id = {task.task_id: task for task in tasks}
    invalid = []
    for row in rows:
        task_id = row.run_row.get("task_id", row.metadata.get("task_id"))
        side = row.run_row.get("side", row.metadata.get("side"))
        task = task_by_id.get(task_id)
        expected_side = split["tasks"].get(task_id, {}).get("side")
        if task_scope == "all":
            invalid_row = (
                task is None
                or task.known_defect
                or expected_side not in {"teach", "score"}
                or side != expected_side
                or row.task_family != f"eog.{task.domain}"
            )
        else:
            invalid_row = (
                task is None
                or task.known_defect
                or side != "teach"
                or expected_side != "teach"
                or row.task_family != f"eog.{task.domain}"
            )
        if invalid_row:
            invalid.append(row.source_trace_uuid)
    if invalid:
        if task_scope == "all":
            raise SystemExit(
                f"The replay file has {len(invalid)} Traces whose task is missing or is a known defect, whose side "
                f"does not match the split, or whose family does not match the task domain: {invalid[:5]}"
            )
        raise SystemExit(
            f"The replay file has {len(invalid)} Traces that are not teach-side tasks of the split: {invalid[:5]}"
        )


def _check_executor_score_literals(
    skills: list[dict[str, Any]],
    split: dict[str, Any],
    tasks: tuple[Task, ...] | list[Task],
    *,
    task_scope: str,
) -> None:
    if task_scope == "all":
        print("Skipping the score-literal gate because of --task-scope all.")
        return
    assert_no_score_literals(skills, split, tasks)


def _run_recompile(args: argparse.Namespace, root: Path, split: dict, tasks: tuple[Task, ...]) -> None:
    """Replay exported teacher Traces into a new Agent, compile them, and write the snapshot and the funnel."""
    zep = zep_client()
    agent_path = root / "agent.json"
    selected_path = root / "replay-selected.jsonl"
    transformed_path = root / "replay-transformed.jsonl"
    traces_path = root / "traces.jsonl"
    task_families = list(args.task_family)
    if not task_families:
        raise SystemExit("Pass --task-family once for each family to recompile.")
    if len(task_families) != len(set(task_families)):
        raise SystemExit("Pass each --task-family only once.")
    if set(args.include_pending_family) - set(task_families):
        raise SystemExit("--include-pending-family values must also be selected with --task-family.")
    if any(path.exists() for path in (agent_path, selected_path, transformed_path, traces_path)):
        raise SystemExit(f"The run directory already has a recompile run: {root}. Use a new --run-id.")
    root.mkdir(parents=True, exist_ok=True)
    selected_counts = filter_replay_export(args.replay_file, selected_path, task_families)
    transform, tool_context_source, _ = prepare_replay_file(selected_path, transformed_path)
    rows = load_replay_rows(transformed_path)
    if args.replay_limit is not None:
        if args.replay_limit < 1:
            raise SystemExit("--replay-limit must be at least 1")
        rows = rows[: args.replay_limit]
    if not rows:
        raise SystemExit(f"The replay file has no Traces: {args.replay_file}")
    _require_teach_replay_rows(rows, split, tasks, task_scope=args.task_scope)
    agent_record = ensure_agent(zep, args.run_id, suffix="recompile")
    _write_agent_record(agent_path, agent_record)
    manifest = replay_manifest(
        run_id=args.run_id,
        compiler_label=args.compiler_label.strip(),
        replay_file=args.replay_file,
        selected_replay_file=selected_path,
        rows=rows,
        concurrency=args.replay_concurrency,
        agent_record=agent_record,
        repo=Path.cwd(),
        transform=transform,
        tool_context_source=tool_context_source,
        task_scope=args.task_scope,
        task_families=task_families,
        development_commit=args.development_commit,
    )
    manifest["selected_trace_counts"] = selected_counts
    preexisting_run_ids = list_learning_run_ids(zep, agent_record["agent_uuid"], task_families)
    manifest["replay_limit"] = args.replay_limit
    manifest["replay_limit_rule"] = (
        "The first N Traces in close order. A compile window is eligible when due_at <= now or when the distinct "
        "unconsumed attempt families in the window reach minimum_attempt_families (1 in this Agent), so each close "
        "makes its family window eligible. N is the smallest prefix whose last Trace is a passed Trace, so that one "
        "window compiles with at least 1 passed Trace."
        if args.replay_limit is not None
        else None
    )
    manifest_path = root / "manifest.recompile.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    replay = replay_traces(
        zep,
        agent_record["agent_uuid"],
        rows,
        run_id=args.run_id,
        traces_path=traces_path,
        concurrency=args.replay_concurrency,
    )
    manifest["replay"] = replay
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    if replay["errors"]:
        raise SystemExit(
            f"The replay failed for {len(replay['errors'])} Traces; see {manifest_path}. "
            "The Agent keeps the replayed Traces, so run the stage again with a new --run-id."
        )
    compilation_wait = wait_for_compilation(
        zep,
        agent_record["agent_uuid"],
        _closed_trace_counts(
            traces_path,
            [family for family in task_families if family not in args.include_pending_family],
        ),
        ceiling_seconds=args.compile_wait_minutes * 60,
        preexisting_run_ids=preexisting_run_ids,
    )
    manifest["compilation_wait"] = compilation_wait
    manifest["compilation_wait_rule"] = WAIT_FOR_COMPILATION_RULE
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    snapshot = compile_snapshot(
        zep,
        agent_record["agent_uuid"],
        task_families,
        split,
        root / "skills",
        traces_path=traces_path,
        wait=compilation_wait,
        task_scope=args.task_scope,
        include_pending_families=args.include_pending_family,
    )
    funnel = write_compile_funnel(root / "skills", task_families, traces_path=traces_path)
    manifest["windows"] = funnel["total"]["windows"]
    manifest["window_uuids"] = funnel["total"]["window_uuids"]
    manifest["skills"] = funnel["total"]["skills"]
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {"run_id": args.run_id, "stage": "recompile", "snapshot": snapshot, "funnel": funnel["total"]}, indent=2
        )
    )
    failure_message = _compile_failure_message(
        root / "skills",
        task_families,
        include_pending_families=args.include_pending_family,
    )
    if failure_message:
        raise SystemExit(failure_message)


def _run_merge(args: argparse.Namespace, output_root: Path) -> None:
    if not args.family_source:
        raise SystemExit("Pass --family-source once for each task family to merge.")
    family_sources: dict[str, tuple[Path, str | None]] = {}
    for family, source_dir, development_commit in args.family_source:
        if family in family_sources:
            raise SystemExit(f"Pass --family-source only once for {family}.")
        family_sources[family] = (Path(source_dir), development_commit or None)
    task_families = list(args.task_family) or list(family_sources)
    if len(task_families) != len(set(task_families)):
        raise SystemExit("Pass each --task-family only once.")
    if set(task_families) != set(family_sources):
        raise SystemExit("The selected task families must match the --family-source entries.")
    manifest_path = output_root / "manifest.merge.json"
    if (
        manifest_path.exists()
        or (output_root / "skills" / "snapshot.json").exists()
        or (output_root / "agent.json").exists()
    ):
        raise SystemExit(f"The output directory already has a merged snapshot: {output_root}")
    snapshot, provenance = merge_compile_outputs(family_sources, task_families, output_root / "skills")
    family_agents = {family: provenance[family]["agent_uuid"] for family in task_families}
    manifest = {
        "run_id": args.run_id,
        "stage": "merge",
        "harness_git_commit": git_commit(Path.cwd()),
        "created_utc": datetime.now(UTC).isoformat(),
        "task_families": task_families,
        "sources": provenance,
    }
    output_root.mkdir(parents=True, exist_ok=True)
    agent_record = {
        "agent_uuid": family_agents[args.family_source[0][0]],
        "merged": True,
        "family_agents": family_agents,
    }
    (output_root / "agent.json").write_text(json.dumps(agent_record, indent=2, sort_keys=True) + "\n")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"snapshot": snapshot, "sources": provenance}, indent=2))


def _select_stage_tasks(
    tasks: tuple[Task, ...],
    split: dict[str, Any],
    stage: str,
    *,
    task_scope: str = "split",
    sample_executor_tasks: bool = True,
    explicit_selection: bool = False,
) -> tuple[Task, ...]:
    if stage not in {"teacher", "executor"}:
        return tasks
    if task_scope == "all":
        return tuple(task for task in tasks if not task.known_defect)
    if stage == "executor":
        return _executor_tasks(
            tasks,
            split,
            explicit_selection=explicit_selection,
            task_scope=task_scope,
            sample_executor_tasks=sample_executor_tasks,
        )
    side = "teach" if stage == "teacher" else "score"
    selected = tuple(task for task in tasks if split["tasks"][task.task_id]["side"] == side and not task.known_defect)
    return selected


def _write_agent_record(path: Path, record: dict[str, str]) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as output:
            temporary_path = Path(output.name)
            output.write(json.dumps(record, indent=2) + "\n")
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _run_main(signals: _RunSignalState) -> None:
    parser = argparse.ArgumentParser(description="Run EnterpriseOps-Gym Skills benchmark stages.")
    parser.add_argument(
        "--stage", choices=("teacher", "compile", "recompile", "merge", "executor", "reproduce"), default="reproduce"
    )
    parser.add_argument("--tasks", type=Path)
    parser.add_argument("--task-list", type=Path)
    parser.add_argument("--task-scope", choices=("split", "all"), default="split")
    parser.add_argument("--overlay-dir", type=Path)
    parser.add_argument("--excluded-tasks", type=Path)
    parser.add_argument("--noop-baseline", type=Path)
    parser.add_argument("--arm-order", default="")
    parser.add_argument("--arm-order-seed", type=int, default=20261016)
    parser.add_argument("--teacher-harness", choices=("stirrup", "claude_code"), default="stirrup")
    parser.add_argument("--trials", type=int)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--gym-set", type=int, default=0, help="Select a static Gym set. The default is 0.")
    parser.add_argument("--ramp", default="1,2,4,8")
    parser.add_argument("--max-usd", type=float)
    parser.add_argument("--capture-dir", type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--write-split", action="store_true")
    parser.add_argument("--model", choices=tuple(EFFORTS))
    parser.add_argument("--effort")
    parser.add_argument("--run-id", default=time.strftime("%Y%m%d-%H%M%S"))
    parser.add_argument("--run-dir", type=Path, default=Path("results"))
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--skills-snapshot", type=Path)
    parser.add_argument("--reproduction-report", type=Path)
    parser.add_argument("--arms", default="")
    parser.add_argument("--skip-arms", default="")
    parser.add_argument("--skip-reason")
    parser.add_argument("--langfuse", action="store_true")
    parser.add_argument("--force-first-read", action="store_true")
    parser.add_argument("--skill-name-enum", action="store_true")
    parser.add_argument("--skills-multi-read", action="store_true")
    parser.add_argument("--system-preamble", default=None, metavar="TEXT")
    parser.add_argument("--skill-short-ids", action="store_true")
    parser.add_argument(
        "--allow-compile-failures",
        type=_parse_compile_failure_families,
        default=None,
        metavar="FAMILY[,FAMILY...]",
        help="Allow compile failures for the named task families during executor preflight.",
    )
    parser.add_argument(
        "--decided-candidates",
        type=int,
        default=10,
        help="The number of search hits per task family that the Decisions API selects from in the skills_decided arm.",
    )
    parser.add_argument("--replay-file", type=Path)
    parser.add_argument("--compiler-label")
    parser.add_argument("--development-commit")
    parser.add_argument(
        "--task-family",
        action="append",
        default=[],
        metavar="FAMILY",
        help="Select a task family for recompile or merge. This option can be repeated.",
    )
    parser.add_argument(
        "--family-source",
        action="append",
        nargs=3,
        default=[],
        metavar=("FAMILY", "COMPILE_DIR", "DEVELOPMENT_COMMIT"),
        help="Select one source compile directory and deployed development commit for a merged family.",
    )
    parser.add_argument("--replay-concurrency", type=int, default=DEFAULT_REPLAY_CONCURRENCY)
    parser.add_argument("--replay-limit", type=int, help="Replay only the first N Traces in close order (a pilot).")
    parser.add_argument("--compile-wait-minutes", type=float, default=90.0)
    parser.add_argument(
        "--include-pending-family",
        action="append",
        default=[],
        metavar="FAMILY",
        help="Export the named pending task family without waiting for it. This option can be repeated.",
    )
    args = parser.parse_args()
    if args.gym_set < 0:
        raise SystemExit("--gym-set must not be negative.")
    if args.include_pending_family and args.stage not in {"compile", "recompile"}:
        raise SystemExit("--include-pending-family is only valid with --stage compile or --stage recompile.")
    if args.task_family and args.stage not in {"recompile", "merge"}:
        raise SystemExit("--task-family is only valid with --stage recompile or --stage merge.")
    if args.family_source and args.stage != "merge":
        raise SystemExit("--family-source is only valid with --stage merge.")
    if args.development_commit and args.stage not in {"compile", "recompile"}:
        raise SystemExit("--development-commit is only valid with --stage compile or --stage recompile.")
    if args.output_dir is not None and args.stage not in {"compile", "recompile", "merge"}:
        raise SystemExit("--output-dir is only valid with --stage compile, --stage recompile, or --stage merge.")
    if args.stage == "merge":
        if args.output_dir is None:
            raise SystemExit("Pass --output-dir for the merged snapshot.")
        _run_merge(args, args.output_dir)
        return
    if args.excluded_tasks is not None and args.overlay_dir is None:
        raise SystemExit("--excluded-tasks requires --overlay-dir.")
    requested_arms = _resolve_arms(args.stage, args.arms)
    arms = requested_arms or (("none",) if args.stage in {"teacher", "reproduce"} else ("none", "skills"))
    skip_arms = _resolve_skip_arms(args.stage, args.resume, arms, args.skip_arms, args.skip_reason)
    if args.stage == "recompile":
        if args.replay_file is None:
            raise SystemExit("Pass --replay-file with the exported teacher Traces.")
        if not args.task_family:
            raise SystemExit("Pass --task-family once for each family to recompile.")
        if not args.compiler_label or not args.compiler_label.strip():
            raise SystemExit("Pass --compiler-label with a label of the compiler under test.")
        if args.replay_concurrency < 1:
            raise SystemExit("--replay-concurrency must be at least 1.")
    model, effort, trials = _resolve_run_config(
        args.stage,
        args.teacher_harness,
        args.model,
        args.effort,
        args.trials,
    )
    _validate_cost_limit(model, args.max_usd)
    langfuse_client = require_langfuse_env() if args.langfuse else None
    if args.stage in {"teacher", "compile", "recompile"}:
        _require_zep_environment(args.stage)
    if args.tasks is not None and args.task_list is not None:
        raise SystemExit("Pass only one of --tasks or --task-list.")
    arm_order = [value for value in args.arm_order.split(",") if value]
    if arm_order and sorted(arm_order) != ["claude_code", "stirrup"]:
        raise SystemExit("--arm-order must list stirrup and claude_code once each.")
    if args.overlay_dir is not None:
        original_loaded_tasks = load_tasks()
        loaded_tasks = load_tasks(overlay_dir=args.overlay_dir)
        validate_verifier_only_overlay(original_loaded_tasks, loaded_tasks)
    else:
        original_loaded_tasks = load_tasks()
        loaded_tasks = original_loaded_tasks
    excluded_task_reasons = _load_excluded_task_reasons(args.excluded_tasks, loaded_tasks)
    original_all_tasks = tuple(task_for_gym_set(task, args.gym_set) for task in original_loaded_tasks)
    all_tasks = tuple(task_for_gym_set(task, args.gym_set) for task in loaded_tasks)
    tasks = load_task_sample(all_tasks, args.task_list or args.tasks)
    if args.write_split:
        package_root = Path(__file__).resolve().parents[2]
        write_split(
            original_loaded_tasks if args.overlay_dir is not None else all_tasks,
            package_root / "splits" / "split-v1.json",
        )
        return
    noop_baseline_path = _resolve_noop_baseline_path(args)
    split = _split_for_tasks(all_tasks, args.overlay_dir)
    if args.preflight:
        health = preflight_gym(all_tasks, gym_set_container_names(args.gym_set))
        resets = preflight_reset(all_tasks, baseline_path=noop_baseline_path)
        print(json.dumps({"base": preflight_report(), "gym": health, "reset_checks": resets}, indent=2))
        return
    tasks = _select_stage_tasks(
        tasks,
        split,
        args.stage,
        task_scope=args.task_scope,
        sample_executor_tasks=args.tasks is None and args.task_list is None,
        explicit_selection=args.tasks is not None or args.task_list is not None,
    )
    root = args.output_dir or args.run_dir / args.run_id
    root.mkdir(parents=True, exist_ok=True)
    zep = None
    agent_uuid = None
    agent_record = None
    if args.stage == "compile":
        agent_path = root / "agent.json"
        if not agent_path.exists():
            raise SystemExit(f"Missing teacher Agent record: {agent_path}")
        zep = zep_client()
        agent_record = json.loads(agent_path.read_text())
        agent_uuid = agent_record["agent_uuid"]
        task_families = sorted({family for task in tasks for family in _task_families(task)})
        compile_manifest_path = root / "manifest.compile.json"
        compile_manifest = {
            "run_id": args.run_id,
            "stage": "compile",
            "agent": agent_record,
            "agent_uuid": agent_uuid,
            "development_commit": args.development_commit,
            "harness_git_commit": git_commit(Path.cwd()),
            "created_utc": datetime.now(UTC).isoformat(),
            "task_families": task_families,
        }
        compile_manifest_path.write_text(json.dumps(compile_manifest, indent=2, sort_keys=True) + "\n")
        traces_path = root / "traces.jsonl"
        preexisting_path = root / "compile-wait-preexisting-run-ids.json"
        if preexisting_path.exists():
            preexisting_run_ids = {
                family: set(run_ids) for family, run_ids in json.loads(preexisting_path.read_text()).items()
            }
        else:
            # A teacher run from before this file existed has no baseline, so every outcome counts.
            preexisting_run_ids = None
        compilation_wait = wait_for_compilation(
            zep,
            agent_uuid,
            _closed_trace_counts(
                traces_path,
                [family for family in task_families if family not in args.include_pending_family],
            ),
            ceiling_seconds=args.compile_wait_minutes * 60,
            preexisting_run_ids=preexisting_run_ids,
        )
        snapshot = compile_snapshot(
            zep,
            agent_uuid,
            task_families,
            split,
            root / "skills",
            traces_path=traces_path,
            wait=compilation_wait,
            task_scope=args.task_scope,
            include_pending_families=args.include_pending_family,
        )
        funnel = write_compile_funnel(root / "skills", task_families, traces_path=traces_path)
        compile_manifest["compilation_wait"] = compilation_wait
        compile_manifest["snapshot"] = snapshot
        compile_manifest["funnel"] = funnel["total"]
        compile_manifest_path.write_text(json.dumps(compile_manifest, indent=2, sort_keys=True) + "\n")
        print(json.dumps(snapshot, indent=2))
        failure_message = _compile_failure_message(
            root / "skills",
            task_families,
            include_pending_families=args.include_pending_family,
        )
        if failure_message:
            raise SystemExit(failure_message)
        return
    if args.stage == "recompile":
        _run_recompile(args, root, split, all_tasks)
        return
    skills_hash = ""
    snapshot_file_sha256 = ""
    snapshot_content_sha256 = ""
    snapshot_skills: dict[str, dict[str, str]] = {}
    decided_selection: dict[tuple[str, str], list[str]] = {}
    compile_failures_by_family: dict[str, dict[str, int]] = {}
    family_agents: dict[str, str] | None = None
    executor_preamble = args.system_preamble if args.stage == "executor" else None
    if args.stage == "executor":
        if not args.skills_snapshot:
            raise SystemExit("Pass --skills-snapshot with a frozen snapshot.json file.")
        families = {family for task in all_tasks for family in _task_families(task)}
        compile_failures_by_family = _executor_compile_preflight(
            args.skills_snapshot.parent,
            sorted(families),
            args.allow_compile_failures,
        )
        snapshot_file_sha256 = hashlib.sha256(args.skills_snapshot.read_bytes()).hexdigest()
        snapshot_sha256 = snapshot_file_sha256
        snapshot_content_sha256 = str(json.loads(args.skills_snapshot.read_text())["sha256"])
        _, skills_hash, all_skills = load_snapshot_details(args.skills_snapshot)
        _check_executor_score_literals(all_skills, split, all_tasks, task_scope=args.task_scope)
        for family in sorted(families):
            _, _, family_skills = load_snapshot_details(args.skills_snapshot, task_family=family)
            snapshot_skills.update({item["id"]: {**item, "task_family": family} for item in family_skills})
        if hashlib.sha256(args.skills_snapshot.read_bytes()).hexdigest() != snapshot_file_sha256:
            raise SystemExit("The Skill snapshot changed while the executor loaded it.")
        agent_path = args.skills_snapshot.parent.parent / "agent.json"
        if not agent_path.exists():
            raise SystemExit(f"Missing teacher Agent record: {agent_path}")
        agent_record = json.loads(agent_path.read_text())
        agent_uuid = agent_record["agent_uuid"]
        family_agents = agent_record.get("family_agents")
        if "skills_decided" in arms:
            if args.decided_candidates < 1:
                raise SystemExit("--decided-candidates must be at least 1.")
            decided_path = args.run_dir / f"skill-selection.decided-{args.decided_candidates}.json"
            decider = None
            if not decided_path.exists() and not args.resume:
                _require_zep_environment("executor")
                zep = zep_client()
                decider = SkillDecider()
            decided_selection = _prepare_skill_selection(
                decided_path,
                tasks,
                snapshot_skills,
                snapshot_sha256,
                agent_uuid,
                zep,
                resume=args.resume,
                page_size=args.decided_candidates,
                decider=decider,
                family_agents=family_agents,
            )
        if "skills" in arms:
            try:
                build_skill_index(snapshot_skills, sorted(families), short_ids=bool(args.skill_short_ids))
            except ValueError as error:
                raise SystemExit(f"The Skill snapshot cannot build the Skill index: {error}") from error
    if args.stage == "teacher":
        if args.teacher_harness == "claude_code" and model != "claude-opus-5-5":
            raise SystemExit("The Claude Code teacher harness requires claude-opus-5-5.")
        if args.teacher_harness == "claude_code" and effort != "max":
            raise SystemExit("The Claude Code teacher harness requires max effort.")
        zep = zep_client()
        agent_path = root / "agent.json"
        agent_path_existed = agent_path.exists()
        if agent_path_existed:
            agent_record = json.loads(agent_path.read_text())
        else:
            suffix = "claude-code" if args.teacher_harness == "claude_code" else ""
            agent_record = ensure_agent(zep, args.run_id, suffix=suffix)
            _write_agent_record(agent_path, agent_record)
        agent_uuid = agent_record["agent_uuid"]
        preexisting_path = root / "compile-wait-preexisting-run-ids.json"
        # Only a new teacher Agent gets a baseline. A resumed run that has no baseline counts every outcome.
        if not agent_path_existed and not preexisting_path.exists():
            task_families = sorted({family for task in tasks for family in _task_families(task)})
            preexisting_run_ids = list_learning_run_ids(zep, agent_uuid, task_families)
            preexisting_path.write_text(
                json.dumps({family: sorted(run_ids) for family, run_ids in preexisting_run_ids.items()}, indent=2)
                + "\n"
            )
    digests = _container_image_digests(tasks, args.gym_set)
    paired_arm = next((arm for arm in SKILL_ARMS if arm in arms), "skills")
    cell_name = (
        "teacher-claude-code"
        if args.stage == "teacher" and args.teacher_harness == "claude_code"
        else "teacher"
        if args.stage == "teacher"
        else args.stage
    )
    cells = tuple(
        {"model": model, "effort": effort, "arm": arm, "stage": args.stage, "cell": cell_name} for arm in arms
    )
    current_manifest = freeze_manifest(
        repo=Path.cwd(),
        tasks=all_tasks,
        image_digests=digests,
        split=split,
        skills_snapshot_sha256=skills_hash,
        skills_snapshot_file_sha256=snapshot_file_sha256,
        skills_snapshot_content_sha256=snapshot_content_sha256,
        cells=cells,
        zep_base_url=_zep_base_url(),
        teams_townhall_patch_sha256=teams_townhall_patch_sha256(),
        itsm_opened_by_patch_sha256=itsm_opened_by_patch_sha256(),
        noop_baseline_path=noop_baseline_path,
        teacher_harness=args.teacher_harness if args.stage == "teacher" else None,
        claude_agent_sdk_version=version("claude-agent-sdk") if args.teacher_harness == "claude_code" else None,
        claude_cli_version=claude_cli_version() if args.teacher_harness == "claude_code" else None,
        claude_agent_teams_enabled=False if args.teacher_harness == "claude_code" else None,
        claude_agent_teams_env=(
            {"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": ""} if args.teacher_harness == "claude_code" else {}
        ),
        claude_cli_env=cli_environment_manifest() if args.teacher_harness == "claude_code" else {},
        claude_sdk_argument_validation_enabled=(
            False if args.stage == "teacher" and args.teacher_harness == "claude_code" else None
        ),
        force_first_read=bool(args.force_first_read) if args.stage == "executor" else False,
        skill_name_enum=bool(args.skill_name_enum) if args.stage == "executor" else False,
        skills_multi_read=bool(args.skills_multi_read) if args.stage == "executor" else False,
        system_preamble=executor_preamble,
        system_preamble_sha256=(
            hashlib.sha256(executor_preamble.encode()).hexdigest() if executor_preamble is not None else None
        ),
        skill_short_ids=bool(args.skill_short_ids) if args.stage == "executor" else False,
        compile_failures_allowed=(
            args.allow_compile_failures
            if args.stage == "executor" and args.allow_compile_failures is not None
            else None
        ),
        compile_failures=(
            {family: sum(errors.values()) for family, errors in sorted(compile_failures_by_family.items())}
            if args.stage == "executor" and args.allow_compile_failures is not None
            else None
        ),
        task_scope=args.task_scope,
        matrix_settings=EXECUTOR_MATRIX_SETTINGS if args.stage == "executor" else None,
        overlay_dir=args.overlay_dir,
        excluded_tasks_path=args.excluded_tasks,
        gym_set_index=args.gym_set,
        family_agents=family_agents,
    )
    store = RunStore(
        root,
        resume=args.resume,
        manifest_name=f"manifest.{args.stage}.json",
        ramp_name=f"ramp.{args.stage}.jsonl",
    )
    store.write_manifest(current_manifest, allow_commit_change=bool(skip_arms))
    cost_ledger = CostLedger(_cost_ledger_path(root, args.stage, model, effort), max_usd=args.max_usd)
    rows = [
        {
            "task_id": task.task_id,
            "trial": trial,
            "arm": arm,
            "cell": cell_name,
            "stage": args.stage,
            "model": model,
            "effort": effort,
            "route": model,
            **(
                {"side": split["tasks"][task.task_id]["side"]}
                if args.stage == "teacher" and args.task_scope == "all"
                else {}
            ),
            **({"arm_order": arm_order, "arm_order_seed": args.arm_order_seed} if arm_order else {}),
        }
        for task in tasks
        if args.stage == "reproduce" or not task.known_defect
        for trial in range(1, trials + 1)
        for arm in arms
    ]
    rows = interleave_arms(rows, seed=20260519)
    rows = store.rows_to_run(rows)
    if skip_arms:
        assert args.skip_reason is not None
        rows = _drop_pending_arms(root, store, rows, skip_arms, args.skip_reason.strip())
    ramp = ramp_steps(args.concurrency, args.ramp)
    from .runner import run_concurrent

    task_by_id = {task.task_id: task for task in tasks}
    original_task_by_id = {task.task_id: task for task in original_all_tasks}

    def execute(row: dict, abort_event: threading.Event | None = None) -> dict:
        task = task_by_id[row["task_id"]]
        try:
            skills_block, family_hash, skill_items, skill_index = (
                _executor_skill_inputs(
                    row,
                    task,
                    snapshot_skills,
                    {},
                    paired_arm,
                    skill_short_ids=args.skill_short_ids,
                    skill_name_enum=args.skill_name_enum,
                    skills_multi_read=args.skills_multi_read,
                    decided_selection=decided_selection,
                )
                if args.stage == "executor"
                else (None, "", [], None)
            )
        except Exception as error:
            return _record_skill_selection_failure(row, error, store)
        return _execute_row(
            row,
            task_by_id=task_by_id,
            model=model,
            effort=effort,
            run_id=args.run_id,
            store=store,
            cost_ledger=cost_ledger,
            capture_dir=args.capture_dir,
            skills_block=skills_block,
            skills_hash=family_hash,
            skills=skill_items,
            zep=zep,
            agent_uuid=agent_uuid,
            trace_path=root / "traces.jsonl" if args.stage == "teacher" else None,
            run_dir=root,
            noop_baseline_path=noop_baseline_path,
            teacher_harness=args.teacher_harness if args.stage == "teacher" else "stirrup",
            abort_requested=abort_event,
            langfuse_client=langfuse_client,
            skill_index=skill_index,
            force_first_read=args.force_first_read,
            skill_name_enum=args.skill_name_enum,
            skills_multi_read=args.skills_multi_read if args.stage == "executor" else False,
            system_preamble=executor_preamble,
            original_task_by_id=original_task_by_id if args.overlay_dir is not None else None,
            excluded_task_reasons=excluded_task_reasons if args.overlay_dir is not None else None,
        )

    existing_policy_flags = (
        sum(1 for line in store.policy_flagged.path.read_text().splitlines() if line.strip())
        if store.policy_flagged.path.exists()
        else 0
    )
    signals.scheduling_started = True
    results = run_concurrent(
        rows,
        execute,
        ramp=ramp,
        route_cap=ZAI_ROUTE_CAP if model.startswith("glm-") else None,
        initial_policy_flags=existing_policy_flags,
        checkpoint=store.checkpoint,
        ramp_checkpoint=store.checkpoint_ramp,
        infra_retries=0,
        should_stop=lambda: cost_ledger.max_usd is not None and cost_ledger.total_usd > cost_ledger.max_usd,
        stop_requested=signals.stop_requested,
        abort_requested=signals.abort_requested,
        signal_name=lambda: signals.signal_name,
        execute_with_abort=execute,
    )
    completed_rows = [json.loads(line) for line in store.rows.path.read_text().splitlines() if line.strip()]
    if store.policy_flagged.path.exists():
        completed_rows.extend(
            json.loads(line) for line in store.policy_flagged.path.read_text().splitlines() if line.strip()
        )
    report = None
    if args.stage == "reproduce":
        overlay_ids = _reproduction_overlay_ids(all_tasks, args.overlay_dir)
        known_defect_ids = {task.task_id for task in all_tasks if task.known_defect}
        report = reproduction_report(
            completed_rows,
            overlay_task_ids=overlay_ids,
            known_defect_ids=known_defect_ids,
            type_mismatch_gold_ids={task.task_id for task in all_tasks if task.type_mismatch_gold},
            townhall_fixed_task_ids=set(TOWNHALL_FIXED_TASK_IDS),
        )
        report = _report_with_reproduction_gate(report)
    elif args.stage == "executor":
        report = date_sensitivity_report(completed_rows, all_tasks, arms)
    if report is not None:
        (root / "analysis.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {"run_id": args.run_id, "stage": args.stage, "rows": task_row_count(results), "analysis": report},
            indent=2,
        )
    )
    if signals.first_signal is not None:
        raise SystemExit(signals.exit_code)
    if args.stage == "reproduce" and report is not None and not report["gate_passed"]:
        raise SystemExit("The reproduction gate failed.")


if __name__ == "__main__":
    main()
