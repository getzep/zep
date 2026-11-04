from __future__ import annotations

import argparse
import hashlib
import importlib
import inspect
import json
import logging
import os
import pkgutil
import statistics
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from loguru import logger as tau2_logger

tau2_logger.remove()

_local_tau2_data = Path(__file__).resolve().parents[3] / "tau2-bench" / "data"
if "TAU2_DATA_DIR" not in os.environ and _local_tau2_data.is_dir():
    os.environ["TAU2_DATA_DIR"] = str(_local_tau2_data)

import tau2.config
from openai import OpenAI
from tau2.data_model.simulation import Results, SimulationRun, TextRunConfig
from tau2.data_model.tasks import Task
from tau2.environment.toolkit import DISCOVERABLE_ATTR, TOOL_ATTR
from tau2.metrics.agent_metrics import compute_metrics, get_tasks_pass_hat_k, pass_hat_k
from tau2.registry import registry
from tau2.runner.batch import run_single_task
from tau2.runner.helpers import get_info, get_tasks
from zep_cloud import Zep
from zep_cloud.core.api_error import ApiError
from zep_cloud.types import AgentVerifierPrincipalBindingInput

from agent import (
    CURRENT_EPISODE,
    RETRY_LIMIT,
    AgentEmptyResponseError,
    create_zep_responses_agent,
    hash_prompt_cache_key,
)
from anthropic_client import reports_reasoning_tokens
from braintrust_tracing import BraintrustLogger, BraintrustRecorder
from call_ledger import (
    CURRENT_LEDGER,
    CallLedger,
    error_detail,
    is_infrastructure_error,
    openai_http_client,
)
from compile import (
    _skill_markdown,
    audit_skills,
    audit_task_answers,
    candidate_reviews,
    dump_skills,
    model_dict,
    publication_lineage,
    wait_for_compilation,
    wait_for_learning_idle,
    wait_for_published_skills,
    wait_for_summaries,
)
from config import agent_client, load_config, zep_client
from episode_audit import EpisodeAudit
from protocol import (
    allowed_tools,
    git_sha,
    json_object,
    recording_families,
    trial_seeds,
    user_args,
    validate_mode,
)
from readtime import RawCorpus, ReadTimeCurator
from redaction import Redactor
from report import latency_by_outcome
from sandbox_guard import sandbox_lifecycle
from skill_index import SnapshotSkillToolkit, load_snapshot_skills
from skill_snapshot import compile_snapshot, write_funnel
from skill_snapshot import wait_for_compilation as wait_for_trace_coverage
from tracing import CURRENT_TRACE, TRACER, instrument_tau2_user
from zep_capture import (
    DISCOVERABLE_TOOL_ARGUMENTS,
    EventSink,
    SkillToolkit,
    TrajectoryRecorder,
    ensure_agent,
    get_agent,
    set_literal_policy,
    skill_uuid,
    value,
)

LOGGER = logging.getLogger("tau2-bench-skills")
FAMILY_BY_DOMAIN = {
    "airline": "tool.airline",
    "retail": "tool.retail",
    "telecom": "tool.telecom",
    "banking_knowledge": "tool.banking_knowledge",
}
KNOWLEDGE_DOMAINS = {"banking_knowledge"}


def closed_traces_by_family(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        if row.get("trajectory_uuid") and not row.get("recording_error") and not row.get("error"):
            counts[str(row["task_family"])] += 1
    return dict(sorted(counts.items()))


def _snapshot_sha256(path: str | None) -> str | None:
    if not path:
        return None
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def task_family_for(domain: str, task_index: int, scope: str) -> str:
    family = FAMILY_BY_DOMAIN[domain]
    return family if scope == "domain" else f"{family}.task{task_index:03d}"


ENVIRONMENT_BY_DOMAIN = {
    "airline": "tau2-bench-airline",
    "retail": "tau2-bench-retail",
    "telecom": "tau2-bench-telecom",
    "banking_knowledge": "tau2-bench-banking-knowledge",
}
TAU2_BENCH_COMMIT = "b7ea9074c1cba482b30687fecdb5c8425fd6f619"


def discoverable_tool_names(domain: str) -> frozenset[str]:
    """Return the names of the discoverable tools that the domain toolkits define."""
    module = importlib.import_module(f"tau2.domains.{domain}.tools")
    return frozenset(
        name
        for toolkit in vars(module).values()
        if inspect.isclass(toolkit) and toolkit.__module__ == module.__name__
        for name, method in inspect.getmembers(toolkit, inspect.isfunction)
        if getattr(method, DISCOVERABLE_ATTR, False)
    )


def literal_tool_names(domain: str) -> list[str]:
    """Return each tool name that a Skill of the domain can name.

    These are the agent and user tools of the domain toolkits, and each
    ``<discoverable-tool call>:<discoverable tool>`` name that the recorder
    writes into the event context (see ``context_tool_name``).
    """
    package = importlib.import_module(f"tau2.domains.{domain}")
    names: set[str] = set()
    callers: dict[str, set[str]] = {"agent": set(), "user": set()}
    targets: dict[str, set[str]] = {"agent": set(), "user": set()}
    for info in pkgutil.walk_packages(package.__path__, f"{package.__name__}."):
        module = importlib.import_module(info.name)
        for toolkit in vars(module).values():
            if not inspect.isclass(toolkit) or toolkit.__module__ != module.__name__:
                continue
            for name, method in inspect.getmembers(toolkit, inspect.isfunction):
                if not getattr(method, TOOL_ATTR, False):
                    continue
                names.add(name)
                if getattr(method, DISCOVERABLE_ATTR, False):
                    targets["user" if "User" in toolkit.__name__ else "agent"].add(name)
                elif "discoverable" in name and any(
                    key in inspect.signature(method).parameters
                    for key in DISCOVERABLE_TOOL_ARGUMENTS
                ):
                    callers["user" if "_user_" in name else "agent"].add(name)
    for side, side_callers in callers.items():
        names |= {f"{caller}:{target}" for caller in side_callers for target in targets[side]}
    return sorted(names)


def apply_literal_policy(
    client: Zep,
    agent_uuid: str,
    domains: list[str],
    *,
    idempotency_prefix: str,
) -> None:
    """Set the literal policy for the tau2 domains.

    Only a tau2 domain has a toolkit to read names from. The AppWorld harness
    does not set a literal policy, so an AppWorld replay keeps the default policy.
    """
    domains = [domain for domain in domains if domain in ENVIRONMENT_BY_DOMAIN]
    if not domains:
        return
    set_literal_policy(
        client,
        agent_uuid,
        tools=sorted({name for domain in domains for name in literal_tool_names(domain)}),
        environments=sorted(ENVIRONMENT_BY_DOMAIN[domain] for domain in domains),
        idempotency_prefix=idempotency_prefix,
    )


DEFAULT_SEED = 300
REWARD_VERIFIER_ID = "tau2-bench-reward"
TEACHER_MODELS = frozenset({"gpt-6-sol", "gpt-6.1-sol", "claude-opus-5-5"})


def _run_config(
    args: argparse.Namespace,
    domain: str,
    *,
    user_llm_args: dict[str, Any] | None = None,
) -> TextRunConfig:
    return TextRunConfig.model_validate(
        {
            "domain": domain,
            "task_set_name": None,
            "task_split_name": args.task_split,
            "task_ids": None,
            "num_tasks": None,
            "agent": "zep_responses_agent",
            "llm_agent": args.agent_model,
            "llm_args_agent": {"reasoning_effort": args.reasoning_effort},
            "llm_user": args.user_model,
            "llm_args_user": (user_args(args) if user_llm_args is None else user_llm_args),
            "num_trials": args.trials,
            "max_steps": args.max_steps,
            "seed": DEFAULT_SEED,
            "save_to": None,
            "retrieval_config": (
                getattr(args, "retrieval_config", None) if domain in KNOWLEDGE_DOMAINS else None
            ),
        }
    )


registry.register_agent_factory(create_zep_responses_agent, "zep_responses_agent")


@dataclass
class EpisodeResult:
    domain: str
    task_id: str
    task_index: int
    trial: int
    started_at: str = ""
    finished_at: str = ""
    reward: float = 0.0
    succeeded: bool = False
    steps: int = 0
    tool_calls: int = 0
    prompt_tokens: int = 0
    cached_tokens: int = 0
    cache_creation_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int | None = 0
    duration_s: float = 0.0
    model_s: float = 0.0
    trajectory_uuid: str | None = None
    skill_uuids_injected: list[str] = field(default_factory=list)
    skill_uuids_loaded: list[str] = field(default_factory=list)
    skill_invalid_loads: int = 0
    skill_read_calls: list[dict[str, Any]] = field(default_factory=list)
    assistant_turns: list[dict[str, Any]] = field(default_factory=list)
    tool_call_sequence: list[dict[str, Any]] = field(default_factory=list)
    skill_read_positions: list[dict[str, Any]] = field(default_factory=list)
    skill_search_performed: bool = False
    error: str | None = None
    empty_response: bool = False
    skill_use_findings: list[str] = field(default_factory=list)
    cost_usd: float | None = None
    arm: str | None = None
    memory_source_keys: list[str] = field(default_factory=list)
    memory_skill_uuids: list[str] = field(default_factory=list)
    retrieval_s: float = 0.0
    curator_input_tokens: int = 0
    curator_output_tokens: int = 0
    curator_s: float = 0.0
    briefing: str = ""
    task_family: str = ""
    skill_uses_recorded: int = 0
    skill_use_error: str | None = None
    injected_skill_chars: int = 0
    leakage_hits: int = 0
    recording_error: str | None = None
    error_detail: str | None = None
    failed_calls: list[dict[str, Any]] = field(default_factory=list)
    simulation: Any = field(default=None, repr=False, compare=False)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("simulation", None)
        return data


def parse_tasks(spec: str, count: int) -> list[int]:
    if spec == "all":
        return list(range(count))
    indexes: set[int] = set()
    for part in spec.split(","):
        if "-" in part:
            start, end = part.split("-", 1)
            indexes.update(range(int(start), int(end) + 1))
        else:
            indexes.add(int(part))
    invalid = sorted(index for index in indexes if index < 0 or index >= count)
    if invalid:
        raise ValueError(f"Task indexes are out of range: {invalid}")
    return sorted(indexes)


def ensure_reward_verifier_binding(
    client: Any,
    agent_uuid: str,
    principal_id: str | None,
    *,
    verifier_id: str = REWARD_VERIFIER_ID,
    allow_unverified: bool = False,
) -> None:
    """Bind the benchmark API key to the verifier of the Agent.

    The first verified close registers the verifier and binds its caller, so
    an Agent without the verifier needs no binding. When the verifier exists,
    a caller that is not bound cannot close a Trajectory as verified. A run
    without the API key UUID must then opt in with allow_unverified.
    """
    page = client.agent.verifier.list(agent_uuid, verifier_id=verifier_id, page_size=1)
    verifiers = list(page.items or [])
    if not verifiers:
        return
    if not principal_id:
        if allow_unverified:
            LOGGER.warning("The run can be unverified because no API key UUID was provided")
            return
        raise ValueError(
            f"The {verifier_id} verifier of Agent {agent_uuid} is registered; "
            "pass --zep-api-key-uuid, or pass --allow-unverified"
        )
    verifier = verifiers[0]
    bindings = list(getattr(verifier, "principal_bindings", None) or [])
    if any(getattr(binding, "principal_id", None) == principal_id for binding in bindings):
        return
    binding_inputs = [
        AgentVerifierPrincipalBindingInput(
            principal_type=str(binding.principal_type),
            principal_id=str(binding.principal_id),
            effective_strength=str(binding.effective_strength),
        )
        for binding in bindings
    ]
    binding_inputs.append(
        AgentVerifierPrincipalBindingInput(
            principal_type="project-api-key",
            principal_id=principal_id,
            effective_strength="tool_verified",
        )
    )
    client.agent.verifier.update(
        agent_uuid,
        str(verifier.uuid_),
        expected_revision=int(verifier.revision),
        principal_bindings=binding_inputs,
    )


def _record_episode_failure(
    task_id: object,
    exc: Exception,
    result: EpisodeResult,
    recorder: TrajectoryRecorder | BraintrustRecorder,
) -> None:
    detail = (
        f"status={exc.status_code} body={str(exc.body)[:500]}"
        if isinstance(exc, ApiError)
        else str(exc)[:500]
    )
    LOGGER.warning("Episode %s failed: %s: %s", task_id, type(exc).__name__, detail)
    result.error = type(exc).__name__
    result.error_detail = error_detail(exc, getattr(recorder, "redactor", None))
    result.reward = 0.0
    result.succeeded = False
    result.simulation = None
    recorder.set_result(False, False)


@sandbox_lifecycle()
def _episode(
    client: Zep,
    openai_client: OpenAI,
    *,
    stage: str,
    agent_openai_client: Any = None,
    run_id: str,
    domain: str,
    task: Any,
    task_index: int,
    trial: int,
    agent_uuid: str,
    agent_model: str,
    user_model: str,
    user_temperature: float,
    reasoning_effort: str,
    max_steps: int,
    task_split: str,
    inject_skills: bool,
    retrieval_config: str | None = None,
    arm: str | None = None,
    memory_agent_uuid: str | None = None,
    skill_snapshot: str | None = None,
    skill_index_scope: str = "all",
    corpus: RawCorpus | None = None,
    curator_model: str = "",
    curator_reasoning_effort: str = "medium",
    family_scope: str = "domain",
    record_uses: bool = False,
    injected_skills: list[dict[str, Any]] | None = None,
    task_family_override: str | None = None,
    agent_max_output_tokens: int | None = None,
    on_event: EventSink | None = None,
    reasoning_summary: bool = True,
    user_llm_args: dict[str, Any] | None = None,
    braintrust: BraintrustLogger | None = None,
) -> EpisodeResult:
    started = datetime.now(UTC)
    family = task_family_override or task_family_for(domain, task_index, family_scope)
    executor = stage != "teacher"
    if executor and (
        inject_skills
        or injected_skills
        or task_family_override
        or arm not in {None, "none", "skills"}
    ):
        raise ValueError(
            "Executors require no Skills or a frozen snapshot; live retrieval is forbidden"
        )
    if executor and (skill_index_scope != "all" or (arm == "skills" and not skill_snapshot)):
        raise ValueError("Executors require a frozen snapshot with --skill-index-scope all")
    redactor = Redactor() if executor else Redactor.from_task(task, domain)
    toolkit = None
    if inject_skills:
        toolkit = SkillToolkit(client, agent_uuid, family, on_event=on_event)
    elif arm == "skills" and skill_snapshot:
        toolkit = SnapshotSkillToolkit(
            load_snapshot_skills(
                Path(skill_snapshot), family if skill_index_scope == "family" else None
            ),
            on_event=on_event,
        )
        toolkit.search_skills("")
    elif arm == "skills":
        toolkit = SkillToolkit(client, str(memory_agent_uuid), family, on_event=on_event)
    provider = (
        ReadTimeCurator(
            arm=arm,
            domain=domain,
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
    objective = f"Complete the user's {domain} request."
    recorder: TrajectoryRecorder | BraintrustRecorder
    if braintrust is not None:
        recorder = BraintrustRecorder(
            braintrust,
            name=f"tau2-{domain}-agent",
            metadata={
                "harness": "tau2-bench-skills",
                "run_id": run_id,
                "domain": domain,
                "task_id": str(task.id),
                "task_index": task_index,
                "trial": trial,
                "agent_model": agent_model,
                "task_family": family,
            },
            tags=["tau2", domain, f"task:{task_index}"],
            redactor=redactor,
        )
    else:
        recorder = TrajectoryRecorder(
            client,
            agent_uuid,
            family,
            objective,
            ENVIRONMENT_BY_DOMAIN[domain],
            {
                "harness": "tau2-bench-skills",
                "stage": stage,
                "domain": domain,
                "task_index": str(task_index),
                "trial": str(trial),
                "agent_model": agent_model,
            },
            redactor=redactor,
            stage=stage,
            run_id=run_id,
            domain=domain,
            task_index=task_index,
            trial=trial,
            objective_from_request=True,
            on_event=on_event,
            discoverable_tools=discoverable_tool_names(domain),
        )
    result = EpisodeResult(
        domain=domain,
        task_id=str(task.id),
        task_index=task_index,
        trial=trial,
        started_at=started.isoformat(),
        arm=arm,
        task_family=family,
    )
    composed_prompt_cache_key = f"{run_id}:{stage}:{domain}:{task.id}:{trial}"
    trace = TRACER.start_trace(
        f"tau2-{stage}",
        session_id=f"tau2:{run_id}:{stage}",
        metadata={"domain": domain, "task_id": str(task.id), "trial": trial, "model": agent_model},
    )
    trace_token = CURRENT_TRACE.set(trace)
    usage_totals: dict[str, int] = {}
    ledger = CallLedger()
    ledger_token = CURRENT_LEDGER.set(ledger)
    episode_audit = EpisodeAudit()
    token = CURRENT_EPISODE.set(
        {
            "openai_client": agent_openai_client or openai_client,
            "agent_model": agent_model,
            "reasoning_effort": reasoning_effort,
            "recorder": recorder,
            "reasoning_summary": reasoning_summary,
            "episode_audit": episode_audit,
            "skill_toolkit": toolkit,
            # The injected arm measures a Skill in the agent instructions. The Skills come
            # from the replay compiler, not from a user or a benchmark task.
            "skills": injected_skills,
            "redactor": redactor,
            "prompt_cache_key": hash_prompt_cache_key(composed_prompt_cache_key),
            "memory_provider": provider,
            "max_output_tokens": agent_max_output_tokens,
            "usage_totals": usage_totals,
            "allowed_tool_names": allowed_tools(arm == "skills")
            if domain == "banking_knowledge"
            else None,
        }
    )
    verified = False
    try:
        with recorder:
            result.trajectory_uuid = recorder.trajectory_uuid
            simulation = run_single_task(
                _run_config(
                    argparse.Namespace(
                        task_split=task_split,
                        agent_model=agent_model,
                        reasoning_effort=reasoning_effort,
                        user_model=user_model,
                        user_temperature=user_temperature,
                        trials=1,
                        max_steps=max_steps,
                        retrieval_config=retrieval_config,
                    ),
                    domain,
                    user_llm_args=user_llm_args,
                ),
                task,
                seed=trial_seeds(trial)[trial - 1],
            )
            result.simulation = simulation
            reward = float(simulation.reward_info.reward if simulation.reward_info else 0.0)
            result.reward = reward
            result.succeeded = reward == 1.0
            if isinstance(recorder, BraintrustRecorder):
                recorder.set_reward(reward)
            messages = simulation.get_messages()
            assistant_messages = [
                message for message in messages if getattr(message, "role", "") == "assistant"
            ]
            result.steps = len(assistant_messages)
            result.tool_calls = sum(
                len(getattr(message, "tool_calls", None) or []) for message in assistant_messages
            )
            reasoning_tokens = 0
            for message in assistant_messages:
                usage = getattr(message, "usage", None) or {}
                result.prompt_tokens += int(usage.get("prompt_tokens", 0) or 0)
                result.cached_tokens += int(usage.get("cached_tokens", 0) or 0)
                result.cache_creation_tokens += int(usage.get("cache_creation_tokens", 0) or 0)
                result.completion_tokens += int(usage.get("completion_tokens", 0) or 0)
                reasoning_tokens += int(usage.get("reasoning_tokens", 0) or 0)
            result.reasoning_tokens = reasoning_tokens
            reward_detail: dict[str, Any] = {
                "task_id": str(task.id),
                "task_index": task_index,
                "trial": trial,
            }
            reward_info = simulation.reward_info
            if reward_info is not None and hasattr(reward_info, "model_dump"):
                reward_detail["reward_info"] = reward_info.model_dump(
                    mode="json", exclude_none=True
                )
            if isinstance(recorder, TrajectoryRecorder):
                recorder.emit(
                    "evaluation",
                    {
                        "passed": result.succeeded,
                        "reward": result.reward,
                        "detail": reward_detail,
                    },
                )
            recorder.set_result(result.succeeded, True)
            verified = True
    except AgentEmptyResponseError:
        LOGGER.info("Episode %s ended: the model returned an empty turn", task.id)
        result.empty_response = True
        result.reward = 0.0
        result.succeeded = False
        result.simulation = None
        recorder.set_result(False, False)
    except ApiError as exc:
        if verified:
            LOGGER.warning(
                "Episode %s kept its verified reward; the trajectory close failed: status=%s",
                task.id,
                exc.status_code,
            )
            result.recording_error = f"{type(exc).__name__}:{exc.status_code}"
        else:
            _record_episode_failure(task.id, exc, result, recorder)
    except Exception as exc:  # noqa: BLE001
        _record_episode_failure(task.id, exc, result, recorder)
    finally:
        audit = episode_audit.export(
            result.simulation.get_messages() if result.simulation is not None else None
        )
        result.assistant_turns = audit["assistant_turns"]
        result.tool_call_sequence = audit["tool_call_sequence"]
        result.skill_read_positions = audit["skill_read_positions"]
        if result.error or result.empty_response:
            result.prompt_tokens = usage_totals.get("prompt_tokens", 0)
            result.cached_tokens = usage_totals.get("cached_tokens", 0)
            result.cache_creation_tokens = usage_totals.get("cache_creation_tokens", 0)
            result.completion_tokens = usage_totals.get("completion_tokens", 0)
            result.reasoning_tokens = usage_totals.get("reasoning_tokens", 0)
        if not reports_reasoning_tokens(agent_model):
            result.reasoning_tokens = None
        result.model_s = usage_totals.get("model_ms", 0) / 1000
        result.trajectory_uuid = recorder.trajectory_uuid
        CURRENT_EPISODE.reset(token)
        TRACER.end_trace(trace, output={"reward": result.reward, "error": result.error})
        CURRENT_TRACE.reset(trace_token)
        if toolkit and toolkit.last_search is not None:
            result.skill_search_performed = True
            result.skill_uuids_injected = [
                str(skill.get("uuid") or skill.get("uuid_"))
                for skill in toolkit.last_search["hits"]
                if skill.get("uuid") or skill.get("uuid_")
            ]
            result.skill_uuids_loaded = [str(skill_uuid(skill)) for skill in toolkit.loaded]
            result.skill_invalid_loads = toolkit.invalid_loads
            result.skill_read_calls = list(getattr(toolkit, "read_calls", []))
            result.skill_use_findings = list(toolkit.findings)
            if record_uses and result.trajectory_uuid and result.error is None:
                outcome = "succeeded" if result.succeeded else "failed"
                for skill in toolkit.loaded:
                    try:
                        toolkit.record_use(result.trajectory_uuid, skill, "used", outcome)
                        result.skill_uses_recorded += 1
                    except Exception as exc:  # noqa: BLE001
                        result.skill_use_error = type(exc).__name__
        exposed: list[str] = []
        if toolkit and toolkit.last_search is not None:
            loaded = [str(skill.get("markdown") or "") for skill in toolkit.loaded]
            result.injected_skill_chars = sum(len(text) for text in loaded)
            exposed = [toolkit.catalog(), *loaded]
        if provider and provider.record:
            result.memory_source_keys = list(provider.record.get("source_keys") or [])
            result.memory_skill_uuids = list(provider.record.get("skill_uuids") or [])
            result.retrieval_s = float(provider.record.get("retrieval_s") or 0.0)
            result.curator_input_tokens = int(provider.record.get("curator_input_tokens") or 0)
            result.curator_output_tokens = int(provider.record.get("curator_output_tokens") or 0)
            result.curator_s = float(provider.record.get("curator_s") or 0.0)
            result.briefing = str(provider.record.get("briefing") or "")
            exposed = [result.briefing]
        student_map = {} if executor else Redactor.from_task(task, domain).replacement_map
        if exposed and student_map:
            result.leakage_hits = sum(
                len(audit["hits"])
                for audit in audit_skills(
                    [{"uuid": str(index), "markdown": text} for index, text in enumerate(exposed)],
                    [student_map],
                )
            )
        result.failed_calls = ledger.rows()
        CURRENT_LEDGER.reset(ledger_token)
        result.duration_s = (datetime.now(UTC) - started).total_seconds()
        result.finished_at = datetime.now(UTC).isoformat()
    return result


def _metrics(rows: list[dict[str, Any]], trials: int) -> dict[str, Any]:
    successes = [row for row in rows if float(row.get("reward", 0)) == 1.0]
    total_tokens = sum(
        int(row.get("prompt_tokens", 0) or 0) + int(row.get("completion_tokens", 0) or 0)
        for row in rows
    )
    total_steps = sum(int(row.get("steps", 0) or 0) for row in rows)
    priced = [row for row in rows if row.get("cost_usd") is not None]
    metrics = {
        "pass^1": statistics.fmean(float(row.get("reward", 0.0)) for row in rows) if rows else 0.0,
        "episode_count": len(rows),
        "mean_steps": statistics.fmean(float(row.get("steps", 0)) for row in rows) if rows else 0.0,
        "mean_prompt_tokens": statistics.fmean(float(row.get("prompt_tokens", 0)) for row in rows)
        if rows
        else 0.0,
        "mean_cached_tokens": statistics.fmean(float(row.get("cached_tokens", 0)) for row in rows)
        if rows
        else 0.0,
        "total_cached_tokens": sum(int(row.get("cached_tokens", 0) or 0) for row in rows),
        "mean_cache_creation_tokens": statistics.fmean(
            float(row.get("cache_creation_tokens", 0)) for row in rows
        )
        if rows
        else 0.0,
        "total_cache_creation_tokens": sum(
            int(row.get("cache_creation_tokens", 0) or 0) for row in rows
        ),
        "mean_completion_tokens": statistics.fmean(
            float(row.get("completion_tokens", 0)) for row in rows
        )
        if rows
        else 0.0,
        "mean_reasoning_tokens": statistics.fmean(reported)
        if (
            reported := [
                float(row["reasoning_tokens"])
                for row in rows
                if row.get("reasoning_tokens") is not None
            ]
        )
        else None,
        "error_count": sum(bool(row.get("error")) for row in rows),
        "empty_response_count": sum(bool(row.get("empty_response")) for row in rows),
        "success_count": len(successes),
        "total_tokens": total_tokens,
        "total_steps": total_steps,
        "tokens_per_success": total_tokens / len(successes) if successes else None,
        "steps_per_success": total_steps / len(successes) if successes else None,
        "mean_successful_tokens": statistics.fmean(
            int(row.get("prompt_tokens", 0) or 0) + int(row.get("completion_tokens", 0) or 0)
            for row in successes
        )
        if successes
        else None,
        "mean_successful_steps": statistics.fmean(
            int(row.get("steps", 0) or 0) for row in successes
        )
        if successes
        else None,
        "cost_usd": sum(float(row["cost_usd"]) for row in priced)
        if len(priced) == len(rows) and rows
        else None,
        "skill_search_rate": sum(bool(row.get("skill_search_performed")) for row in rows)
        / len(rows)
        if rows
        else 0.0,
        "skill_retrieval_rate": sum(bool(row.get("skill_uuids_injected")) for row in rows)
        / len(rows)
        if rows
        else 0.0,
        "actual_skill_use_rate": sum(bool(row.get("skill_uuids_loaded")) for row in rows)
        / len(rows)
        if rows
        else 0.0,
        "skill_load_rate_when_retrieved": sum(
            bool(row.get("skill_uuids_loaded")) for row in rows if row.get("skill_uuids_injected")
        )
        / retrieved
        if (retrieved := sum(bool(row.get("skill_uuids_injected")) for row in rows))
        else None,
        "skill_use_outcome": None,
        "briefing_rate": sum(bool(row.get("briefing")) for row in rows) / len(rows)
        if rows
        else 0.0,
        "mean_memory_sources": statistics.fmean(
            len(row.get("memory_source_keys") or []) for row in rows
        )
        if rows
        else 0.0,
        "mean_curator_input_tokens": statistics.fmean(
            float(row.get("curator_input_tokens", 0) or 0) for row in rows
        )
        if rows
        else 0.0,
        "mean_curator_output_tokens": statistics.fmean(
            float(row.get("curator_output_tokens", 0) or 0) for row in rows
        )
        if rows
        else 0.0,
        "mean_curator_s": statistics.fmean(float(row.get("curator_s", 0) or 0) for row in rows)
        if rows
        else 0.0,
        "mean_duration_s": statistics.fmean(float(row.get("duration_s", 0) or 0) for row in rows)
        if rows
        else 0.0,
        "mean_model_s": statistics.fmean(float(row.get("model_s", 0) or 0) for row in rows)
        if rows
        else 0.0,
        "leakage_hit_episodes": sum(int(row.get("leakage_hits", 0) or 0) > 0 for row in rows),
    }
    metrics["latency_by_outcome"] = latency_by_outcome(rows)
    metrics.update(_pass_k_metrics(rows, trials))
    return metrics


def _pass_k_metrics(rows: list[dict[str, Any]], trials: int) -> dict[str, float]:
    rewards_by_task: defaultdict[tuple[str, int], list[float]] = defaultdict(list)
    for row in rows:
        if "task_index" not in row:
            continue
        rewards_by_task[(str(row.get("domain", "")), int(row["task_index"]))].append(
            float(row.get("reward", 0.0))
        )
    metrics: dict[str, float] = {}
    for k in range(1, trials + 1):
        values = [
            pass_hat_k(len(rewards), sum(reward == 1.0 for reward in rewards), k)
            for rewards in rewards_by_task.values()
            if len(rewards) >= k
        ]
        metrics[f"pass^{k}"] = statistics.fmean(values) if values else 0.0
    return metrics


def _episode_key(domain: str, task_index: int, trial: int) -> tuple[str, int, int]:
    return domain, task_index, trial


def _completed_keys(rows: list[dict[str, Any]]) -> set[tuple[str, int, int]]:
    """Return the episodes that `--resume` keeps: a success, an agent failure, or an agent error.

    Only an infrastructure error runs again.
    """
    return {
        _episode_key(str(row["domain"]), int(row["task_index"]), int(row["trial"]))
        for row in rows
        if "domain" in row
        and "task_index" in row
        and "trial" in row
        and not is_infrastructure_error(
            row.get("error"), row.get("error_detail"), row.get("error_type")
        )
    }


def _attempt_counts(rows: list[dict[str, Any]]) -> dict[tuple[str, int, int], int]:
    """Return the number of recorded attempts of each episode, also for rows with no attempt field."""
    counts: dict[tuple[str, int, int], int] = defaultdict(int)
    for row in rows:
        if {"domain", "task_index", "trial"} <= row.keys():
            key = _episode_key(str(row["domain"]), int(row["task_index"]), int(row["trial"]))
            counts[key] = max(counts[key] + 1, int(row.get("attempt") or 0))
    return dict(counts)


def add_price_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--input-price-per-million", type=float)
    parser.add_argument("--cached-input-price-per-million", type=float)
    parser.add_argument("--output-price-per-million", type=float)
    parser.add_argument(
        "--cache-write-price-per-million",
        type=float,
        help="Price of Anthropic cache-creation input tokens; defaults to the input price",
    )


def cost_usd(row: dict[str, Any], args: argparse.Namespace) -> float | None:
    """Return the agent model cost of one row, or None when a required price is missing."""
    if None in (
        args.input_price_per_million,
        args.cached_input_price_per_million,
        args.output_price_per_million,
    ):
        return None
    prompt = int(row.get("prompt_tokens", 0) or 0)
    cached = int(row.get("cached_tokens", 0) or 0)
    cache_write = int(row.get("cache_creation_tokens", 0) or 0)
    completion = int(row.get("completion_tokens", 0) or 0)
    write_price = args.cache_write_price_per_million
    if write_price is None:
        write_price = args.input_price_per_million
    return (
        (prompt - cached - cache_write) * args.input_price_per_million
        + cached * args.cached_input_price_per_million
        + cache_write * write_price
        + completion * args.output_price_per_million
    ) / 1_000_000


def _pending_jobs(
    jobs: list[tuple[str, int, int, Any]], completed: set[tuple[str, int, int]]
) -> list[tuple[str, int, int, Any]]:
    return [job for job in jobs if _episode_key(job[0], job[1], job[2]) not in completed]


def _load_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _latest_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[tuple[str, int, int] | int, dict[str, Any]] = {}
    for index, row in enumerate(rows):
        key = (
            _episode_key(str(row["domain"]), int(row["task_index"]), int(row["trial"]))
            if {"domain", "task_index", "trial"} <= row.keys()
            else index
        )
        latest[key] = row
    return list(latest.values())


def _simulation_path(
    output_dir: Path, stage: str, domain: str, task_index: int, trial: int
) -> Path:
    return output_dir / "simulations" / f"{stage}-{domain}-{task_index}-{trial}.json"


def _load_simulation(
    output_dir: Path, stage: str, domain: str, task_index: int, trial: int
) -> SimulationRun | None:
    path = _simulation_path(output_dir, stage, domain, task_index, trial)
    if not path.exists():
        return None
    return SimulationRun.model_validate_json(path.read_text())


def _redact_data(data: Any, redactor: Redactor) -> Any:
    if isinstance(data, str):
        return redactor.redact(data)
    if isinstance(data, list):
        return [_redact_data(item, redactor) for item in data]
    if isinstance(data, dict):
        return {
            redactor.redact(key) if isinstance(key, str) else key: _redact_data(value, redactor)
            for key, value in data.items()
        }
    return data


def _redact_simulation(
    data: dict[str, Any], task: Task, domain: str, trial: int, *, recording: bool = True
) -> dict[str, Any]:
    redactor = Redactor.from_task(task, domain) if recording else Redactor()
    redacted = _redact_data(data, redactor)
    redacted["task_id"] = str(task.id)
    redacted["trial"] = trial
    return redacted


def _conditions(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "git_sha": git_sha(),
        "trial_start": getattr(args, "trial_start", 1),
        "trial_seeds": trial_seeds(args.trials + getattr(args, "trial_start", 1) - 1)[
            getattr(args, "trial_start", 1) - 1 :
        ],
        "allowed_executor_tools": allowed_tools(getattr(args, "arm", None) == "skills"),
        "user_llm_args": user_args(args),
        "agent_model": args.agent_model,
        "agent_base_url": getattr(args, "agent_base_url", None),
        "agent_provider": getattr(args, "agent_provider", None),
        "reasoning_effort": args.reasoning_effort,
        "reasoning_summaries": "auto_when_supported; native_thinking_for_claude",
        "agent_temperature": None,
        "user_model": args.user_model,
        "user_temperature": args.user_temperature,
        "max_steps": args.max_steps,
        "trials": args.trials,
        "retry_limit": RETRY_LIMIT,
        "task_range": args.tasks,
        "task_split": args.task_split,
        "retrieval_config": getattr(args, "retrieval_config", None),
        "tau2_bench_commit": TAU2_BENCH_COMMIT,
        "input_price_per_million": args.input_price_per_million,
        "cached_input_price_per_million": args.cached_input_price_per_million,
        "cache_write_price_per_million": getattr(args, "cache_write_price_per_million", None),
        "output_price_per_million": args.output_price_per_million,
        "user_cache_enabled": False,
        "arm": getattr(args, "arm", None),
        "memory_agent_uuid": getattr(args, "memory_agent_uuid", None),
        "skill_snapshot": getattr(args, "skill_snapshot", None),
        "skill_snapshot_sha256": _snapshot_sha256(getattr(args, "skill_snapshot", None)),
        "skill_index_scope": getattr(args, "skill_index_scope", None),
        "source_run_id": getattr(args, "source_run_id", None),
        "curator_model": getattr(args, "curator_model", None),
        "curator_reasoning_effort": getattr(args, "curator_reasoning_effort", None),
        "family_scope": getattr(args, "family_scope", "domain"),
        "task_family_map_sha256": _snapshot_sha256(getattr(args, "task_family_map", None)),
        "record_uses": getattr(args, "record_uses", False),
        "agent_max_output_tokens": getattr(args, "agent_max_output_tokens", None),
    }


def _check_readiness(
    args: argparse.Namespace, client: Zep, tasks_by_domain: dict[str, list[Any]]
) -> dict[str, Any]:
    path = (
        Path(args.readiness_file)
        if args.readiness_file
        else Path(args.results_root) / args.run_id / "compile.json"
    )
    if not path.is_file():
        raise RuntimeError(f"The student comparison requires a Skill readiness manifest: {path}")
    manifest = json.loads(path.read_text())
    if not manifest.get("ready"):
        raise RuntimeError(f"The Skill readiness gate failed: {manifest.get('failures')}")
    teacher_rows = _latest_rows(_load_rows(Path(args.results_root) / args.run_id / "teacher.jsonl"))
    teacher_digest = hashlib.sha256(json.dumps(teacher_rows, sort_keys=True).encode()).hexdigest()
    if teacher_digest != manifest.get("teacher_rows_sha256"):
        raise RuntimeError("The teacher trajectories differ from the audited Skill set")
    teacher_model = manifest["conditions"]["agent_model"]
    if teacher_model not in TEACHER_MODELS or args.agent_model == teacher_model:
        raise RuntimeError("The comparison requires a teacher model and a different student model")
    conditions = _conditions(args)
    for key in (
        "reasoning_effort",
        "user_model",
        "user_temperature",
        "max_steps",
        "trials",
        "task_range",
        "task_split",
        "tau2_bench_commit",
    ):
        if manifest["conditions"][key] != conditions[key]:
            raise RuntimeError(f"The student condition differs from the teacher condition: {key}")
    if manifest["seed"] != DEFAULT_SEED or set(manifest["families"]) != {
        FAMILY_BY_DOMAIN[domain] for domain in args.domains
    }:
        raise RuntimeError("The seed or domain set differs from the teacher condition")
    for domain in args.domains:
        selected = parse_tasks(args.tasks, len(tasks_by_domain[domain]))
        if selected != manifest["task_indices"][domain]:
            raise RuntimeError(f"The selected {domain} tasks differ from the teacher tasks")
        if [str(tasks_by_domain[domain][index].id) for index in selected] != manifest["task_ids"][
            domain
        ]:
            raise RuntimeError(f"The {domain} task IDs differ from the teacher task IDs")
    if args.stage == "student-skills":
        load_config()
        student_uuid = manifest["student_agent_uuid"]
        if args.agent_uuid and args.agent_uuid != student_uuid:
            raise RuntimeError("The student Agent differs from the audited fresh Agent")
        if student_uuid == manifest["teacher_agent_uuid"]:
            raise RuntimeError("The student and teacher must use different Agents")
        for domain in args.domains:
            attached = manifest["families"][FAMILY_BY_DOMAIN[domain]]["attached"]
            if not attached:
                raise RuntimeError(f"The {domain} domain has no attached Skills")
            for item in attached:
                if any(
                    item.get(key) != expected
                    for key, expected in (
                        ("publication_status", "succeeded"),
                        ("lineage_status", "verified"),
                        ("audit_status", "passed"),
                        ("answer_audit_status", "passed"),
                        ("admission_status", "admitted"),
                        ("attachment_status", "active"),
                    )
                ):
                    raise RuntimeError(f"The attached Skill {item['source_uuid']} failed the gate")
                if item.get("audit_hit_count") != 0:
                    raise RuntimeError("The destination leakage audit failed")
                lineage = publication_lineage(client, student_uuid, item["destination_uuid"])
                if (
                    lineage is None
                    or any(
                        lineage.get(field) != item.get(expected)
                        for field, expected in (
                            ("source_skill_uuid", "source_uuid"),
                            ("source_version_uuid", "source_version_uuid"),
                            ("source_version", "source_version"),
                            ("destination_skill_uuid", "destination_uuid"),
                            ("destination_version_uuid", "destination_version_uuid"),
                            ("policy_uuid", "policy_uuid"),
                            ("policy_revision", "policy_revision"),
                        )
                    )
                    or lineage.get("source_agent_uuid") != manifest["teacher_agent_uuid"]
                ):
                    raise RuntimeError("The destination publication lineage changed")
                source = client.agent.skill.get(manifest["teacher_agent_uuid"], item["source_uuid"])
                if (
                    value(source, "status") != "active"
                    or value(source, "searchable") is not True
                    or value(source, "current_version") != item["source_version"]
                ):
                    raise RuntimeError("The source Skill is no longer admitted")
                skill = client.agent.skill.get(student_uuid, item["destination_uuid"])
                if (
                    skill.status != "active"
                    or skill.current_version is None
                    or skill.task_family != FAMILY_BY_DOMAIN[domain]
                    or skill.searchable is not True
                ):
                    raise RuntimeError(
                        f"The attached Skill {item['destination_uuid']} is not ready"
                    )
                destination_version = client.agent.skill.version.get(
                    student_uuid, item["destination_uuid"], skill.current_version
                )
                if (
                    skill.current_version != item["destination_version"]
                    or value(destination_version, "version") != item["destination_version"]
                ):
                    raise RuntimeError("The published destination version changed")
                maps = [
                    Redactor.from_task(tasks_by_domain[domain][index], domain).replacement_map
                    for index in manifest["task_indices"][domain]
                ]
                markdown = _skill_markdown(client, student_uuid, skill)
                if hashlib.sha256(markdown.encode()).hexdigest() != item.get(
                    "audited_markdown_sha256"
                ):
                    raise RuntimeError("The destination Skill changed after the answer audit")
                if not markdown or any(
                    audit["hits"]
                    for audit in audit_skills(
                        [{"uuid": item["destination_uuid"], "markdown": markdown}], maps
                    )
                ):
                    raise RuntimeError("The destination Skill failed the leakage audit")
    elif args.agent_uuid and args.agent_uuid in {
        manifest.get("teacher_agent_uuid"),
        manifest.get("student_agent_uuid"),
    }:
        raise RuntimeError("The plain student must have a distinct Agent")
    return manifest


def freeze_compilation(client: Zep, agent_uuid: str) -> None:
    """Set manual compilation so that recorded applications do not change the Skills."""
    agent = client.agent.get(agent_uuid)
    settings = model_dict(agent.memory_settings)
    if settings.get("compilation_mode") == "manual":
        return
    if agent.revision is None:
        raise RuntimeError("The Agent does not report a revision")
    settings["compilation_mode"] = "manual"
    client.agent.update(
        agent_uuid,
        expected_revision=agent.revision,
        memory_settings=cast(Any, settings),
    )


@sandbox_lifecycle()
def run_stage(args: argparse.Namespace, client: Zep, openai_client: OpenAI) -> dict[str, Any]:
    validate_mode(args)
    if "banking_knowledge" in getattr(args, "domains", []):
        from sandbox_guard import install_sandbox_guard, sandbox_self_check

        install_sandbox_guard()
        sandbox_self_check()
    if args.stage == "teacher" and args.agent_uuid:
        raise ValueError("The teacher stage requires a fresh Agent for this run ID")
    output_dir = Path(args.results_root) / args.run_id
    jsonl_path = output_dir / f"{args.stage}.jsonl"
    if jsonl_path.exists() and not args.resume:
        raise ValueError(f"The {args.stage} stage already exists; use --resume or a new run ID")
    if args.resume and not jsonl_path.is_file():
        raise ValueError(f"The {args.stage} stage has no result journal to resume")
    tasks_by_domain = {
        domain: get_tasks(domain, task_split_name=args.task_split) for domain in args.domains
    }
    readiness = (
        _check_readiness(args, client, tasks_by_domain)
        if args.stage in {"student-skills", "student-plain"}
        else None
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    braintrust = (
        BraintrustLogger(args.braintrust_project) if args.stage == "braintrust-source" else None
    )
    try:
        return _run_stage_body(
            args,
            client,
            openai_client,
            braintrust,
            tasks_by_domain,
            readiness,
            output_dir,
            jsonl_path,
        )
    finally:
        if braintrust is not None:
            braintrust.close()


def _run_stage_body(
    args: argparse.Namespace,
    client: Zep,
    openai_client: OpenAI,
    braintrust: BraintrustLogger | None,
    tasks_by_domain: dict[str, list[Task]],
    readiness: dict[str, Any] | None,
    output_dir: Path,
    jsonl_path: Path,
) -> dict[str, Any]:
    (output_dir / "run-settings.json").write_text(json.dumps(_conditions(args), indent=2))
    type_families = (
        recording_families(getattr(args, "task_family_map", None))
        if args.stage == "teacher"
        else {}
    )
    if type_families:
        for domain, tasks in tasks_by_domain.items():
            if domain != "banking_knowledge" or any(
                str(task.id) not in type_families for task in tasks
            ):
                raise ValueError("The teacher type map must cover every selected banking task")
    if braintrust is not None:
        agent = {"agent_uuid": ""}
    elif args.agent_uuid:
        agent = {"agent_uuid": args.agent_uuid}
    elif args.stage.startswith("heldout-"):
        agent = ensure_agent(client, args.stage, run_id=args.run_id, compilation_mode="manual")
    elif args.stage == "student-skills":
        if readiness is None:
            raise RuntimeError("The Skill readiness manifest is required")
        agent = {"agent_uuid": readiness["student_agent_uuid"]}
    else:
        agent = ensure_agent(
            client,
            args.stage,
            run_id=args.run_id,
            compilation_mode=args.compilation_mode,
            task_families=(
                sorted({f"tool.banking_knowledge.{family}" for family in type_families.values()})
                if type_families
                else ([] if args.family_scope == "task" else None)
            ),
        )
        apply_literal_policy(
            client,
            str(agent["agent_uuid"]),
            args.domains,
            idempotency_prefix=f"{args.run_id}:{args.stage}",
        )
    agent_uuid = str(agent["agent_uuid"])
    if braintrust is None:
        ensure_reward_verifier_binding(
            client, agent_uuid, args.zep_api_key_uuid, allow_unverified=args.allow_unverified
        )
    jobs = [
        (domain, index, trial, tasks_by_domain[domain][index])
        for domain in args.domains
        for index in parse_tasks(args.tasks, len(tasks_by_domain[domain]))
        for trial in range(args.trial_start, args.trial_start + args.trials)
    ]
    if args.stage == "heldout-r-skill" or (
        args.stage == "heldout-skills" and not args.skill_snapshot
    ):
        wait_for_learning_idle(
            client,
            str(args.memory_agent_uuid),
            sorted(
                {task_family_for(domain, index, args.family_scope) for domain, index, _, _ in jobs}
            ),
            timeout=args.compile_timeout,
            poll_interval=args.compile_poll_interval,
        )
    if args.record_uses:
        freeze_compilation(client, agent_uuid)
    all_rows = _load_rows(jsonl_path) if args.resume else []
    rows = _latest_rows(all_rows)
    attempts = _attempt_counts(all_rows)
    completed = _completed_keys(rows)
    pending_jobs = _pending_jobs(jobs, completed) if args.resume else jobs
    LOGGER.info(
        "resume=%s skipped_jobs=%d pending_jobs=%d",
        args.resume,
        len(jobs) - len(pending_jobs),
        len(pending_jobs),
    )
    inject_skills = args.stage == "student-skills"
    arm = args.arm if args.stage.startswith("heldout-") else None
    if (
        arm in {"skills", "r-skill"}
        and not args.memory_agent_uuid
        and not (arm == "skills" and args.skill_snapshot)
    ):
        raise ValueError(f"The {arm} arm requires --memory-agent-uuid")
    corpora = (
        {
            domain: RawCorpus.load(Path(args.results_root), args.source_run_id, domain)
            for domain in args.domains
        }
        if arm == "r-raw"
        else {}
    )
    simulations_by_key: dict[tuple[str, int, int], Any] = {}
    lock = threading.Lock()
    stage_started = datetime.now(UTC).isoformat()
    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = {
            executor.submit(
                _episode,
                client,
                openai_client,
                stage=args.stage,
                agent_openai_client=agent_client(
                    openai_client,
                    args.agent_base_url,
                    args.agent_api_key_env,
                    args.agent_provider,
                    args.agent_model,
                ),
                run_id=args.run_id,
                domain=domain,
                task=task,
                task_index=index,
                trial=trial,
                agent_uuid=agent_uuid,
                agent_model=args.agent_model,
                user_model=args.user_model,
                user_temperature=args.user_temperature,
                user_llm_args=user_args(args),
                task_family_override=(
                    f"tool.{domain}.{type_families[str(task.id)]}"
                    if type_families
                    else args.task_family_override
                ),
                reasoning_effort=args.reasoning_effort,
                max_steps=args.max_steps,
                task_split=args.task_split,
                inject_skills=inject_skills,
                retrieval_config=args.retrieval_config,
                arm=arm,
                memory_agent_uuid=args.memory_agent_uuid,
                skill_snapshot=args.skill_snapshot,
                skill_index_scope=args.skill_index_scope,
                corpus=corpora.get(domain),
                curator_model=args.curator_model or args.agent_model,
                curator_reasoning_effort=args.curator_reasoning_effort,
                agent_max_output_tokens=args.agent_max_output_tokens,
                family_scope=args.family_scope,
                record_uses=args.record_uses,
                injected_skills=(
                    json.loads(Path(args.injected_skills_file).read_text())
                    if arm == "injected" and args.injected_skills_file
                    else None
                ),
                braintrust=braintrust,
            ): (domain, index, trial)
            for domain, index, trial, task in pending_jobs
        }
        mode = "a" if args.resume else "w"
        with jsonl_path.open(mode) as handle:
            for future in as_completed(futures):
                domain, index, trial = futures[future]
                try:
                    episode = future.result()
                    row = episode.as_dict()
                except Exception as exc:  # noqa: BLE001
                    episode = None
                    row = {
                        "domain": domain,
                        "task_id": str(tasks_by_domain[domain][index].id),
                        "task_index": index,
                        "trial": trial,
                        "reward": 0.0,
                        "error": type(exc).__name__,
                        "error_detail": error_detail(exc),
                    }
                row["agent_uuid"] = agent_uuid
                row["attempt"] = attempts.get(_episode_key(domain, index, trial), 0) + 1
                if (cost := cost_usd(row, args)) is not None:
                    row["cost_usd"] = cost
                if episode is None:
                    row["started_at"] = stage_started
                    row["finished_at"] = datetime.now(UTC).isoformat()
                if episode is not None and episode.simulation is not None:
                    key = (domain, index, trial)
                    simulations_by_key[key] = episode.simulation
                    simulation_path = _simulation_path(output_dir, args.stage, domain, index, trial)
                    simulation_path.parent.mkdir(parents=True, exist_ok=True)
                    simulation_path.write_text(
                        json.dumps(
                            _redact_simulation(
                                episode.simulation.model_dump(mode="json"),
                                tasks_by_domain[domain][index],
                                domain,
                                trial,
                                recording=args.stage == "teacher",
                            )
                        )
                    )
                handle.write(json.dumps(row, sort_keys=True) + "\n")
                handle.flush()
                with lock:
                    rows.append(row)
                    rows = _latest_rows(rows)

    official_by_domain: dict[str, Any] = {}
    for domain in args.domains:
        domain_rows = [row for row in rows if row.get("domain") == domain]
        domain_tasks = []
        simulations = []
        trials = []
        for row in domain_rows:
            key = (domain, int(row["task_index"]), int(row["trial"]))
            simulation = simulations_by_key.get(key) or _load_simulation(
                output_dir, args.stage, domain, key[1], key[2]
            )
            if simulation is not None:
                simulation.task_id = str(tasks_by_domain[domain][key[1]].id)
                simulation.trial = key[2]
                domain_tasks.append(tasks_by_domain[domain][key[1]])
                simulations.append(simulation)
                trials.append(key[2])
        config = _run_config(args, domain)
        results = Results(info=get_info(config), tasks=domain_tasks, simulations=simulations)
        metrics = compute_metrics(results)
        pass_k = get_tasks_pass_hat_k(results) if simulations else None
        official_by_domain[domain] = {
            "avg_reward": metrics.avg_reward,
            "pass^k": {
                str(column).replace("pass^", ""): float(value)
                for column, value in pass_k.mean().items()
            }
            if pass_k is not None and not pass_k.empty
            else {},
            "task_split": args.task_split,
            "results_path": str(output_dir / f"{args.stage}-{domain}-tau2.json"),
        }
        saved_results = results.model_dump(mode="json")
        saved_results["tasks"] = [
            {
                **_redact_data(
                    task,
                    Redactor.from_task(source, domain) if args.stage == "teacher" else Redactor(),
                ),
                "id": str(source.id),
            }
            for task, source in zip(saved_results["tasks"], domain_tasks, strict=True)
        ]
        saved_results["simulations"] = [
            _redact_simulation(simulation, source, domain, trial, recording=args.stage == "teacher")
            for simulation, source, trial in zip(
                saved_results["simulations"], domain_tasks, trials, strict=True
            )
        ]
        (output_dir / f"{args.stage}-{domain}-tau2.json").write_text(
            json.dumps(saved_results, indent=2)
        )
    summary = _metrics(rows, args.trials)
    summary.update(
        {
            "stage": args.stage,
            "run_id": args.run_id,
            "agent_uuid": agent_uuid,
            "agent_model": args.agent_model,
            "user_model": args.user_model,
            "seed": DEFAULT_SEED,
            "conditions": _conditions(args),
            "official_metrics": official_by_domain,
            "readiness_file": str(args.readiness_file or output_dir / "compile.json")
            if readiness
            else None,
            "domains": {
                domain: _metrics([row for row in rows if row.get("domain") == domain], args.trials)
                for domain in args.domains
            },
        }
    )
    (output_dir / f"{args.stage}.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    if args.stage == "teacher" and not args.no_compile:
        compile_teacher(args, client, agent_uuid, output_dir, jobs)
    return summary


def compile_teacher(
    args: argparse.Namespace,
    client: Zep,
    agent_uuid: str,
    output_dir: Path,
    jobs: list[tuple[str, int, int, Any]],
) -> None:
    if args.family_scope == "domain" and not getattr(args, "task_family_map", None):
        run_compile(args, client, agent_uuid, output_dir)
        return
    rows = _latest_rows(_load_rows(output_dir / f"{args.stage}.jsonl"))
    closed = closed_traces_by_family(rows)
    skills_dir = output_dir / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    traces_path = skills_dir / "traces.jsonl"
    traces_path.write_text(
        "".join(
            json.dumps(
                {
                    "task_family": row["task_family"],
                    "trace_uuid": row["trajectory_uuid"],
                    "passed": bool(row.get("succeeded")),
                }
            )
            + "\n"
            for row in rows
            if row.get("trajectory_uuid")
        )
    )
    wait = wait_for_trace_coverage(
        client,
        agent_uuid,
        closed,
        ceiling_seconds=args.compile_timeout,
        poll_interval=args.compile_poll_interval,
    )
    if wait["timed_out"]:
        for state in wait["families"].values():
            state["timed_out"] = not state["done"]
    (skills_dir / "compile_wait.json").write_text(json.dumps(wait, indent=2, sort_keys=True))
    families = sorted(closed)
    snapshot = compile_snapshot(
        client, agent_uuid, families, skills_dir, traces_path=traces_path, wait=wait
    )
    funnel = write_funnel(skills_dir, families, snapshot, traces_path)
    LOGGER.info(
        "skill snapshot: skills=%d timed_out=%s funnel=%s",
        len(snapshot["skills"]),
        wait["timed_out"],
        json.dumps(funnel["total"]["decisions"], sort_keys=True),
    )


def validate_publication_destination(
    client: Zep, source_uuid: str, destination_uuid: str | None
) -> str:
    if not destination_uuid or destination_uuid == source_uuid:
        raise ValueError(
            "Compile requires --student-agent-uuid for a distinct publication destination"
        )
    source = get_agent(client, source_uuid)
    destination = get_agent(client, destination_uuid)
    domain = value(source, "security_domain")
    if not domain or value(destination, "security_domain") != domain:
        raise ValueError("The publication destination must share the source security domain")
    return destination_uuid


def run_compile(
    args: argparse.Namespace, client: Zep, agent_uuid: str, output_dir: Path
) -> dict[str, Any]:
    destination_uuid = validate_publication_destination(client, agent_uuid, args.student_agent_uuid)
    rows = _latest_rows(_load_rows(output_dir / "teacher.jsonl"))
    summary: dict[str, Any] = {
        "agent_uuid": agent_uuid,
        "teacher_agent_uuid": agent_uuid,
        "student_agent_uuid": None,
        "stage": "compile",
        "ready": False,
        "failures": [],
        "conditions": _conditions(args),
        "seed": DEFAULT_SEED,
        "started_at": datetime.now(UTC).isoformat(),
        "trajectory_count": len(rows),
        "teacher_rows_sha256": hashlib.sha256(
            json.dumps(rows, sort_keys=True).encode()
        ).hexdigest(),
        "task_indices": {},
        "task_ids": {},
        "summaries": {},
        "families": {},
    }
    teacher_summary_path = output_dir / "teacher.json"
    if not teacher_summary_path.is_file():
        summary["failures"].append("The teacher summary is missing")
    else:
        teacher_summary = json.loads(teacher_summary_path.read_text())
        if teacher_summary.get("agent_uuid") != agent_uuid or teacher_summary.get(
            "conditions"
        ) != _conditions(args):
            summary["failures"].append(
                "The teacher Agent or conditions differ from the compile settings"
            )
        if teacher_summary.get("agent_model") not in TEACHER_MODELS:
            summary["failures"].append(
                f"The teacher model must be one of {', '.join(sorted(TEACHER_MODELS))}"
            )
    expected_keys: set[tuple[str, int, int]] = set()
    for domain in args.domains:
        tasks = get_tasks(domain, task_split_name=args.task_split)
        indexes = parse_tasks(args.tasks, len(tasks))
        summary["task_indices"][domain] = indexes
        summary["task_ids"][domain] = [str(tasks[index].id) for index in indexes]
        expected_keys.update(
            (domain, index, trial) for index in indexes for trial in range(1, args.trials + 1)
        )
    if _completed_keys(rows) != expected_keys or len(rows) != len(expected_keys):
        summary["failures"].append("The teacher trajectories do not cover the selected task trials")
    if any(
        row.get("task_id")
        != dict(
            zip(
                summary["task_indices"][str(row["domain"])],
                summary["task_ids"][str(row["domain"])],
                strict=True,
            )
        )[int(row["task_index"])]
        for row in rows
        if row.get("domain") in summary["task_ids"]
        and int(row.get("task_index", -1)) in summary["task_indices"][str(row["domain"])]
    ):
        summary["failures"].append("A teacher task ID differs from the selected task data")
    if any(row.get("error") or not row.get("trajectory_uuid") for row in rows):
        summary["failures"].append("A teacher trajectory is missing or has an error")
    if any(row.get("agent_uuid") != agent_uuid for row in rows):
        summary["failures"].append("A teacher trajectory belongs to another Agent")
    if not rows:
        summary["failures"].append("No teacher trajectories were found for this Agent")
    if not summary["failures"]:
        summary["summaries"] = wait_for_summaries(
            client,
            agent_uuid,
            [str(row["trajectory_uuid"]) for row in rows],
            timeout=args.summary_timeout,
        )
        if summary["summaries"]["pending"]:
            summary["failures"].append("Teacher trajectory summaries are still pending")
    for domain in args.domains:
        family = FAMILY_BY_DOMAIN[domain]
        info: dict[str, Any] = {
            "compilation": None,
            "candidate_reviews": [],
            "candidate_count": 0,
            "admitted_count": 0,
            "skills": [],
            "audit": [],
            "attached": [],
            "failures": [],
        }
        summary["families"][family] = info
        if summary["failures"]:
            info["failures"].extend(summary["failures"])
            continue
        try:
            since = min(str(row["started_at"]) for row in rows if row.get("domain") == domain)
            info["compilation"] = wait_for_compilation(
                client,
                agent_uuid,
                family,
                since,
                timeout=args.compile_timeout,
                poll_interval=args.compile_poll_interval,
            )
            wait_for_learning_idle(
                client,
                agent_uuid,
                [family],
                timeout=args.compile_timeout,
                poll_interval=args.compile_poll_interval,
            )
            outcome = info["compilation"]["outcome"] or {}
            if info["compilation"]["timed_out"] or outcome.get("decision") != "produced":
                info["failures"].append(
                    f"Compilation did not produce a candidate: "
                    f"{outcome.get('rejection_class') or outcome.get('decision') or 'timed out'}"
                )
            info["candidate_reviews"] = candidate_reviews(client, agent_uuid, family, since)
            info["candidate_count"] = sum(
                int(review["candidate_count"]) for review in info["candidate_reviews"]
            )
            if not info["candidate_count"]:
                info["failures"].append("The compiler created no candidates")
            approved = [
                review for review in info["candidate_reviews"] if review["status"] == "approved"
            ]
            if not approved:
                reasons = [
                    f"{review['status']}: {review['reason_code']}"
                    for review in info["candidate_reviews"]
                ]
                info["failures"].append(f"No candidates were admitted: {reasons}")
            skills = dump_skills(client, agent_uuid, family, output_dir)
            started = datetime.fromisoformat(since).astimezone(UTC)
            admitted = [
                skill
                for skill in skills
                if skill["status"] == "active"
                and skill.get("created_at")
                and datetime.fromisoformat(str(skill["created_at"])).astimezone(UTC) >= started
            ]
            info["skills"] = admitted
            info["admitted_count"] = len(admitted)
            if not admitted:
                info["failures"].append("No new admitted Skills are active")
            if any(not skill.get("markdown") for skill in admitted):
                info["failures"].append("An admitted Skill has no Markdown body")
            domain_tasks = get_tasks(domain, task_split_name=args.task_split)
            maps = [
                Redactor.from_task(domain_tasks[index], domain).replacement_map
                for index in summary["task_indices"][domain]
            ]
            info["audit"] = audit_skills(admitted, maps)
            if any(item["hits"] for item in info["audit"]):
                info["failures"].append("An admitted Skill contains a teacher task identifier")
            if not info["failures"]:
                auditor = OpenAI(api_key=load_config().openai_api_key)
                selected_tasks = [domain_tasks[index] for index in summary["task_indices"][domain]]
                if any(
                    not audit_task_answers(auditor, skill["markdown"], selected_tasks)
                    for skill in admitted
                ):
                    info["failures"].append("An admitted Skill contains a task-specific answer")
        except Exception as exc:  # noqa: BLE001
            info["failures"].append(type(exc).__name__)
    if not any(info["failures"] for info in summary["families"].values()):
        summary["student_agent_uuid"] = destination_uuid
        config = load_config()
        for domain in args.domains:
            info = summary["families"][FAMILY_BY_DOMAIN[domain]]
            try:
                maps = [
                    Redactor.from_task(
                        get_tasks(domain, task_split_name=args.task_split)[index], domain
                    ).replacement_map
                    for index in summary["task_indices"][domain]
                ]
                info["attached"] = wait_for_published_skills(
                    client,
                    agent_uuid,
                    destination_uuid,
                    info["skills"],
                    maps,
                    [
                        get_tasks(domain, task_split_name=args.task_split)[index]
                        for index in summary["task_indices"][domain]
                    ],
                    OpenAI(api_key=config.openai_api_key),
                    timeout=args.compile_timeout,
                    poll_interval=args.compile_poll_interval,
                )
                info["failures"].extend(
                    f"Skill {item['source_uuid']} publication or admission failed: {item['error']}"
                    for item in info["attached"]
                    if item.get("error")
                )
            except Exception as exc:  # noqa: BLE001
                info["failures"].append(
                    f"Publication or destination admission failed: {type(exc).__name__}"
                )
    summary["failures"].extend(
        f"{family}: {failure}"
        for family, info in summary["families"].items()
        for failure in info["failures"]
    )
    summary["ready"] = not summary["failures"] and bool(summary["student_agent_uuid"])
    (output_dir / "compile.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n"
    )
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--stage",
        choices=[
            "teacher",
            "student-skills",
            "student-plain",
            "compile",
            "publication-setup",
            "heldout",
            "braintrust-source",
        ],
        required=True,
    )
    parser.add_argument("--domains", nargs="+", choices=sorted(FAMILY_BY_DOMAIN), required=True)
    parser.add_argument("--tasks", default="all")
    parser.add_argument("--task-split", default="base")
    parser.add_argument(
        "--retrieval-config",
        default="alltools",
        help="tau2 knowledge retrieval configuration for banking_knowledge.",
    )
    parser.add_argument("--trials", type=int, default=4)
    parser.add_argument("--trial-start", type=int, default=1)
    parser.add_argument("--agent-model", default="gpt-6-sol")
    parser.add_argument("--user-model", default="gpt-5.2")
    parser.add_argument(
        "--agent-base-url",
        help="An OpenAI-compatible Responses endpoint for the agent, for example OpenRouter.",
    )
    parser.add_argument("--agent-api-key-env", default="OPENROUTER_API_KEY")
    parser.add_argument(
        "--agent-provider", help="Send every agent request to this OpenRouter provider."
    )
    parser.add_argument(
        "--reasoning-effort", choices=["low", "medium", "high", "xhigh", "max"], default="xhigh"
    )
    parser.add_argument("--user-temperature", type=float, default=0.0)
    parser.add_argument(
        "--user-llm-args",
        type=json_object,
        default={"reasoning_effort": "low"},
        help="A JSON object; replaces user temperature args (default: reasoning_effort=low)",
    )
    parser.add_argument(
        "--task-family-map", help="A task-ID to type map for teacher recording only"
    )
    parser.add_argument("--max-steps", type=int, default=200)
    parser.add_argument("--agent-uuid")
    parser.add_argument("--student-agent-uuid")
    parser.add_argument("--readiness-file")
    parser.add_argument("--zep-api-key-uuid")
    parser.add_argument(
        "--allow-unverified",
        action="store_true",
        help="Run without the reward verifier binding. No outcome is strongly verified.",
    )
    parser.add_argument(
        "--agent-max-output-tokens",
        type=int,
        help="The output token limit of each agent request. The provider default applies if unset.",
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--concurrency", type=int)
    parser.add_argument("--run-id", default="run")
    parser.add_argument("--results-root", default=str(Path(__file__).resolve().parent / "results"))
    parser.add_argument("--no-compile", action="store_true")
    parser.add_argument(
        "--braintrust-project",
        default="zep-skills-tau2-pilot",
        help="The Braintrust project that receives the braintrust-source traces.",
    )
    parser.add_argument("--arm", choices=["none", "skills", "injected", "r-skill", "r-raw"])
    parser.add_argument("--injected-skills-file")
    parser.add_argument("--task-family-override")
    parser.add_argument("--memory-agent-uuid")
    parser.add_argument(
        "--skill-snapshot",
        help="snapshot.json from skill_snapshot.py; the skills arm then reads a Skill index",
    )
    parser.add_argument(
        "--skill-index-scope",
        choices=["all", "family"],
        default="all",
        help="index every snapshot Skill, or only the Skills of the task family",
    )
    parser.add_argument(
        "--family-scope",
        choices=["domain", "task"],
        default="domain",
        help="Use one task family for each domain, or one task family for each task.",
    )
    parser.add_argument(
        "--record-uses",
        action="store_true",
        help="Record each injected Skill use and its outcome on the Skill-owning Agent.",
    )
    parser.add_argument("--source-run-id")
    parser.add_argument("--curator-model")
    parser.add_argument(
        "--curator-reasoning-effort", choices=["low", "medium", "high", "xhigh"], default="medium"
    )
    parser.add_argument(
        "--compilation-mode", choices=["scheduled", "manual", "on_close"], default="scheduled"
    )
    parser.add_argument("--summary-timeout", type=float, default=600)
    parser.add_argument("--compile-timeout", type=float, default=1800)
    parser.add_argument("--compile-poll-interval", type=float, default=20)
    add_price_arguments(parser)
    args = parser.parse_args()
    if args.stage == "teacher" and args.domains == ["banking_knowledge"]:
        args.task_family_map = args.task_family_map or str(
            Path(__file__).with_name("task_types.json")
        )
        if args.compilation_mode == "scheduled":
            args.compilation_mode = "on_close"
    if args.stage == "heldout":
        if not args.arm:
            parser.error("The heldout stage requires --arm")
        if args.arm == "r-raw" and not args.source_run_id:
            parser.error("The r-raw arm requires --source-run-id")
        args.stage = f"heldout-{args.arm}"
    try:
        validate_mode(args)
    except ValueError as exc:
        parser.error(str(exc))
    if args.family_scope == "task" and args.stage in {
        "compile",
        "publication-setup",
        "student-skills",
    }:
        parser.error(
            f"The {args.stage} stage supports only --family-scope domain; "
            "use the heldout stage with --agent-uuid for task-scoped Skills"
        )
    if (
        args.stage == "teacher"
        and args.family_scope == "task"
        and args.compilation_mode == "scheduled"
    ):
        parser.error(
            "The teacher stage with --family-scope task requires --compilation-mode on_close; "
            "a scheduled window of one task waits for 3 attempt families or 24 hours"
        )
    if args.record_uses and args.skill_snapshot:
        parser.error("--record-uses cannot record a snapshot Skill; omit --skill-snapshot")
    if args.record_uses and (
        args.stage != "heldout-skills"
        or not args.agent_uuid
        or args.agent_uuid != args.memory_agent_uuid
    ):
        parser.error(
            "--record-uses requires the skills arm with --agent-uuid equal to --memory-agent-uuid"
        )
    prices = (
        args.input_price_per_million,
        args.cached_input_price_per_million,
        args.output_price_per_million,
    )
    if any(price is not None for price in prices) and (
        any(price is None for price in prices)
        or any(price < 0 for price in prices if price is not None)
    ):
        parser.error("Provide all three nonnegative token prices, or omit all of them")
    if args.concurrency is None:
        args.concurrency = load_config(require_zep=args.stage != "braintrust-source").concurrency
    return args


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = load_config(require_zep=args.stage != "braintrust-source")
    tau2.config.LLM_CACHE_ENABLED = False
    instrument_tau2_user()
    client = cast(Zep, zep_client(config.api_key, config.api_url) if config.api_key else None)
    openai_client = OpenAI(api_key=config.openai_api_key, http_client=openai_http_client("openai"))
    if args.stage == "publication-setup":
        if not args.agent_uuid:
            raise ValueError("publication-setup requires the teacher --agent-uuid")
        student = ensure_agent(
            client,
            "student-skills",
            run_id=f"{args.run_id}-published",
            compilation_mode="manual",
            approval="auto",
        )
        result = {
            "source_agent_uuid": args.agent_uuid,
            "destination_agent_uuid": student["agent_uuid"],
            "task_families": [FAMILY_BY_DOMAIN[domain] for domain in args.domains],
        }
    elif args.stage == "compile":
        if not args.agent_uuid:
            raise ValueError("compile requires --agent-uuid")
        output_dir = Path(args.results_root) / args.run_id
        output_dir.mkdir(parents=True, exist_ok=True)
        result = run_compile(args, client, args.agent_uuid, output_dir)
    else:
        result = run_stage(args, client, openai_client)
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    TRACER.flush()


if __name__ == "__main__":
    main()
