from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Self, cast

from zep_cloud import Zep
from zep_cloud.errors.conflict_error import ConflictError
from zep_cloud.types.add_agent_skill_use_outcome_request import AddAgentSkillUseOutcomeRequest
from zep_cloud.types.agent_literal_policy_classes import AgentLiteralPolicyClasses
from zep_cloud.types.agent_literal_policy_values import AgentLiteralPolicyValues
from zep_cloud.types.agent_trace_close_verifier import AgentTraceCloseVerifier
from zep_cloud.types.agent_trace_event_context import AgentTraceEventContext
from zep_cloud.types.agent_trace_resource_ref import AgentTraceResourceRef

from redaction import Redactor

MAX_EVENT_CHARS = 20_000
# The compiler rejects an event append when the Trace objective exceeds 8 KiB.
MAX_OBJECTIVE_BYTES = 8 * 1024
MAX_TOOL_RESULT_EVENT_CHARS = 20_000
EventSink = Callable[[str, dict[str, Any]], None]
LOGGER = logging.getLogger(__name__)


def fit_objective(objective: str) -> str:
    encoded = objective.encode()
    if len(encoded) <= MAX_OBJECTIVE_BYTES:
        return objective
    return encoded[:MAX_OBJECTIVE_BYTES].decode(errors="ignore")


LOAD_SKILL_TOOL_NAME = "load_skill"
LOAD_SKILL_TOOL: dict[str, Any] = {
    "type": "function",
    "name": LOAD_SKILL_TOOL_NAME,
    "description": (
        "Load the full procedure of one Skill from the Available skills list. "
        "Call it only when the Skill matches the current request."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "skill_id": {"type": "string", "description": "The skill_id from the list."}
        },
        "required": ["skill_id"],
        "additionalProperties": False,
    },
}
SKILL_CATALOG_HEADER = (
    "## Available skills\n"
    "These Skills were compiled from earlier successful runs. The list shows only the name, "
    "the description, and the use conditions of each Skill. If a Skill matches the current "
    "request, call load_skill with its skill_id to read the full procedure before you act. "
    "Do not load a Skill that does not match the request. You can load no Skill. "
    "Follow the policy and the task instructions when a Skill conflicts with them.\n\n"
)
_USE_WHEN = re.compile(r"Use this Skill when:\n(.*?)(?:\n## |\Z)", re.DOTALL)
_HTML_COMMENT = re.compile(r"\s*<!--.*?-->", re.DOTALL)


def value(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def model_dict(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return item
    if hasattr(item, "model_dump"):
        return item.model_dump(mode="json", by_alias=True, exclude_none=True)
    if hasattr(item, "dict"):
        return item.dict(by_alias=True, exclude_none=True)
    return {"value": str(item)}


def skill_uuid(item: Any) -> str | None:
    return value(item, "uuid") or value(item, "uuid_")


def skill_use_when(markdown: str) -> list[str]:
    match = _USE_WHEN.search(markdown)
    if not match:
        return []
    return [
        _HTML_COMMENT.sub("", line[2:]).strip()
        for line in match.group(1).splitlines()
        if line.startswith("- ")
    ]


TOOL_EVENT_TYPES = frozenset({"tool_call", "tool_result"})


DISCOVERABLE_TOOL_ARGUMENTS = ("agent_tool_name", "discoverable_tool_name", "user_tool_name")


def context_tool_name(name: str, arguments: Any, discoverable_tools: frozenset[str]) -> str:
    """Name the discoverable tool that a discoverable-tool call acts on.

    A call such as ``call_discoverable_agent_tool`` holds the real tool name only
    in an argument value, so the compiler masks it as ``<string>``. The event
    context names ``call_discoverable_agent_tool:<real tool name>`` instead.
    Only a name in ``discoverable_tools`` (the tool names that the domain
    defines) goes into the context, so an argument value from the conversation,
    such as a customer identifier, never does.
    """
    if "discoverable" not in name or not isinstance(arguments, dict):
        return name
    for key in DISCOVERABLE_TOOL_ARGUMENTS:
        target = arguments.get(key)
        if isinstance(target, str) and target in discoverable_tools:
            return f"{name}:{target}"
    return name


TRUNCATED_MARKER = "\n...[truncated]"


def bounded(text: str) -> str:
    return " ".join(str(text).split())[:MAX_EVENT_CHARS]


def bounded_tool_event(text: str) -> str:
    """Keep the whitespace and line breaks of tool code and output, and cut long text."""
    text = str(text)
    if len(text) > MAX_TOOL_RESULT_EVENT_CHARS:
        return text[: MAX_TOOL_RESULT_EVENT_CHARS - len(TRUNCATED_MARKER)] + TRUNCATED_MARKER
    return text


def _idempotency_key(prefix: str) -> str:
    digest = bytearray(hashlib.sha256(f"tau2-bench-skills:{prefix}".encode()).digest()[:16])
    digest[6] = (digest[6] & 0x0F) | 0x40
    digest[8] = (digest[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(digest)))


def ensure_agent(
    client: Zep,
    label: str,
    *,
    run_id: str,
    compilation_mode: str,
    approval: str = "auto",
    task_families: list[str] | None = None,
    agent_id_prefix: str = "tau2-bench-skills-",
) -> dict[str, str]:
    suffix = uuid.uuid5(uuid.NAMESPACE_URL, f"tau2-bench-skills:{run_id}:{label}").hex[:12]
    agent_id = f"{agent_id_prefix}{label}-{suffix}"
    agent = client.agent.create(
        agent_id=agent_id,
        name=agent_id,
        security_domain="tau2-bench-skills",
        version="1.0.0",
        memory_settings=cast(
            Any,
            {
                "compilation_mode": compilation_mode,
                "approval": approval,
                "auto_approval_task_families": (
                    ["tool.airline", "tool.retail", "tool.telecom", "tool.banking_knowledge"]
                    if task_families is None
                    else task_families
                ),
            },
        ),
        idempotency_key=_idempotency_key(f"agent:{run_id}:{label}"),
    )
    agent_uuid = value(agent, "uuid") or value(agent, "uuid_")
    if not agent_uuid:
        raise RuntimeError("Created Agent did not include a UUID")
    return {"agent_id": agent_id, "agent_uuid": str(agent_uuid)}


MAX_LITERAL_POLICY_VALUES = 256


def set_literal_policy(
    client: Zep,
    agent_uuid: str,
    *,
    tools: list[str],
    environments: list[str],
    idempotency_prefix: str,
) -> None:
    """Allow the benchmark tool and environment names in the Skills of the Agent.

    With no policy, the compiler scanner must judge each tool name in a rendered
    Skill, and a name with a numeric suffix looks like an opaque identifier. One
    rejected Skill file rejects each add in its compile group.
    """
    for kind, values in (("tools", tools), ("environments", environments)):
        if len(values) > MAX_LITERAL_POLICY_VALUES:
            raise ValueError(
                f"The literal policy allows at most {MAX_LITERAL_POLICY_VALUES} {kind}"
            )
    policy = {
        "default": "reject",
        "allowlisted_classes": {"tools": ["command_name"], "environments": ["environment_name"]},
        "allowlisted_values": {"tools": sorted(tools), "environments": sorted(environments)},
    }
    current = client.agent.literal_policy.get(agent_uuid)
    state = model_dict(current)
    if all(state.get(key) == expected for key, expected in policy.items()):
        return
    revision = value(current, "revision")
    if not isinstance(revision, int):
        raise TypeError("The Agent literal policy did not include a revision")
    client.agent.literal_policy.update(
        agent_uuid,
        default="reject",
        allowlisted_classes=AgentLiteralPolicyClasses(
            tools=["command_name"],
            environments=["environment_name"],
        ),
        allowlisted_values=AgentLiteralPolicyValues(
            tools=sorted(tools),
            environments=sorted(environments),
        ),
        expected_revision=revision,
        idempotency_key=_idempotency_key(f"policy:{idempotency_prefix}:{revision}"),
    )


def get_agent(client: Zep, agent_uuid: str) -> Any:
    return client.agent.get(agent_uuid)


class TrajectoryRecorder:
    def __init__(
        self,
        client: Zep,
        agent_uuid: str,
        task_family: str,
        objective: str,
        environment: str,
        metadata: dict[str, Any],
        *,
        redactor: Redactor,
        stage: str,
        run_id: str,
        domain: str,
        task_index: int,
        trial: int,
        verifier_id: str = "tau2-bench-reward",
        objective_from_request: bool = False,
        parent_trajectory_uuid: str | None = None,
        on_event: EventSink | None = None,
        discoverable_tools: frozenset[str],
    ) -> None:
        self.client = client
        # Each caller must pass the set: run.py and replay.py pass
        # discoverable_tool_names(domain), and harnesses with no discoverable
        # tools pass an empty set.
        self.discoverable_tools = discoverable_tools
        self.parent_trajectory_uuid = parent_trajectory_uuid
        self.objective_from_request = objective_from_request
        self.on_event = on_event
        self._pending: list[tuple[str, str, str | None, str | None, list[str] | None]] = []
        self._call_tools: dict[str, str] = {}
        self.verifier_id = verifier_id
        self.agent_uuid = agent_uuid
        self.task_family = task_family
        self.objective = objective
        self.environment = environment
        self.metadata = metadata
        self.redactor = redactor
        self.trajectory_uuid: str | None = None
        self.close_result: Any | None = None
        self._succeeded = False
        self._verified = False
        self._event_sequence = 0
        self._stage = stage
        self._run_id = run_id
        self._domain = domain
        self._task_index = task_index
        self._trial = trial
        self._create_idempotency_key = _idempotency_key(
            f"trajectory:{self._stage}:{self._domain}:{self._task_index}:"
            f"{self._trial}:{self._run_id}:{uuid.uuid4()}"
        )

    def emit(self, kind: str, data: dict[str, Any]) -> None:
        if self.on_event is None:
            return
        try:
            self.on_event(kind, data)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Event sink failed for %s: %s", kind, type(exc).__name__)

    def __enter__(self) -> Self:
        if not self.objective_from_request:
            self._create()
        return self

    def _create(self) -> None:
        trajectory = self.client.agent.trace.create(
            self.agent_uuid,
            objective=fit_objective(self.redactor.redact(self.objective)),
            task_family=self.task_family,
            metadata=self.metadata,
            parent_trace_uuid=self.parent_trajectory_uuid,
            idempotency_key=self._create_idempotency_key,
        )
        self.trajectory_uuid = str(value(trajectory, "uuid") or value(trajectory, "uuid_"))
        if not self.trajectory_uuid:
            raise RuntimeError("Created Trajectory did not include a UUID")
        self.emit("trajectory", {"uuid": self.trajectory_uuid})
        pending, self._pending = self._pending, []
        for event_type, content, tool, call_id, context_tools in pending:
            self._append(
                event_type,
                content,
                tool=tool,
                call_id=call_id,
                context_tools=context_tools,
            )

    def _append(
        self,
        event_type: str,
        content: str,
        *,
        tool: str | None = None,
        call_id: str | None = None,
        context_tools: list[str] | None = None,
    ) -> None:
        if not self.trajectory_uuid:
            if not self.objective_from_request:
                raise RuntimeError("TrajectoryRecorder is not active")
            self._pending.append((event_type, content, tool, call_id, context_tools))
            return
        self._event_sequence += 1
        context = AgentTraceEventContext(
            environments=[AgentTraceResourceRef(name=self.environment)],
            tools=(
                [AgentTraceResourceRef(name=name) for name in context_tools]
                if context_tools is not None
                else [AgentTraceResourceRef(name=tool)]
                if tool
                else None
            ),
        )
        self.client.agent.trace.append_event(
            self.agent_uuid,
            self.trajectory_uuid,
            event_type=event_type,
            content=(
                bounded_tool_event(self.redactor.redact(content))
                if event_type in TOOL_EVENT_TYPES
                else bounded(self.redactor.redact(content))
            ),
            context=context,
            tool_call_id=call_id,
            occurred_at=datetime.now(UTC).isoformat(),
            idempotency_key=_idempotency_key(
                f"event:{self.trajectory_uuid}:{self._event_sequence}"
            ),
        )

    def record_message(self, role: str, text: str) -> None:
        event_type = "observation" if role == "user" else "decision"
        self._append(event_type, f"{role}: {text}")
        self.emit("message", {"role": role, "text": text})
        if role == "user" and not self.trajectory_uuid and text.strip():
            self.objective = text
            self._create()

    def record_tool_call(
        self,
        name: str,
        arguments: Any,
        *,
        call_id: str | None = None,
        context_tools: list[str] | None = None,
    ) -> None:
        tool = context_tool_name(name, arguments, self.discoverable_tools)
        if call_id:
            self._call_tools[call_id] = tool
        self._append(
            "tool_call",
            json.dumps(arguments, sort_keys=True),
            tool=tool,
            call_id=call_id,
            context_tools=context_tools,
        )
        data = {"name": name, "arguments": arguments, "call_id": call_id}
        if context_tools is not None:
            data["context_tools"] = context_tools
        self.emit("tool_call", data)

    def record_tool_result(
        self,
        name: str,
        result: Any,
        *,
        call_id: str | None = None,
        context_tools: list[str] | None = None,
    ) -> None:
        result_text = str(result)
        tool = self._call_tools.get(call_id, name) if call_id else name
        self._append(
            "tool_result",
            result_text,
            tool=tool,
            call_id=call_id,
            context_tools=context_tools,
        )
        data = {"name": name, "result": bounded_tool_event(result_text), "call_id": call_id}
        if context_tools is not None:
            data["context_tools"] = context_tools
        self.emit("tool_result", data)

    def set_result(self, succeeded: bool, verified: bool) -> None:
        self._succeeded = succeeded
        self._verified = verified

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        if not self.trajectory_uuid and self._pending:
            self._create()
        if not self.trajectory_uuid:
            return
        success = exc_type is None and self._succeeded
        verified = exc_type is None and self._verified
        outcome = "succeeded" if success else "failed"
        if exc_type is not None:
            self.client.agent.trace.abandon(
                self.agent_uuid,
                self.trajectory_uuid,
                highest_accepted_sequence=self._event_sequence,
                reason="infrastructure failure",
                idempotency_key=_idempotency_key(f"abandon:{self.trajectory_uuid}"),
            )
            return
        close_kwargs: dict[str, Any] = {
            "outcome": outcome,
            "verification": "external" if verified else "none",
            "idempotency_key": _idempotency_key(f"close:{self.trajectory_uuid}"),
        }
        if verified:
            close_kwargs["verifier"] = AgentTraceCloseVerifier(
                verifier_id=self.verifier_id,
                class_="harness",
                outcome=outcome,
                evidence_reference=self.trajectory_uuid,
            )
        try:
            self.close_result = self.client.agent.trace.close(
                self.agent_uuid, self.trajectory_uuid, **close_kwargs
            )
        except ConflictError as error:
            if (
                error.status_code != 409
                or "verifier with that verifier_id already exists" not in str(error)
            ):
                raise
            LOGGER.warning("Retrying Trace close after a verifier registration conflict")
            retry_kwargs = {
                **close_kwargs,
                "idempotency_key": _idempotency_key(f"close-retry:{self.trajectory_uuid}"),
            }
            self.close_result = self.client.agent.trace.close(
                self.agent_uuid, self.trajectory_uuid, **retry_kwargs
            )
        self.emit(
            "trajectory_closed",
            {
                "outcome": outcome,
                "verification": "external" if verified else "none",
            },
        )


class SkillToolkit:
    def __init__(
        self,
        client: Zep,
        agent_uuid: str,
        task_family: str,
        *,
        on_event: EventSink | None = None,
    ) -> None:
        self.client = client
        self.agent_uuid = agent_uuid
        self.task_family = task_family
        self.on_event = on_event
        self.last_search: dict[str, Any] | None = None
        self.findings: list[str] = []
        self.loaded: list[dict[str, Any]] = []
        self.invalid_loads = 0

    def search_skills(self, query: str, limit: int = 3) -> list[dict[str, Any]]:
        override = os.environ.get("SKILLS_OVERRIDE_FILE")
        if override:
            with open(override, encoding="utf-8") as handle:
                skills = json.load(handle).get(self.task_family, [])
            hits = [dict(skill) for skill in skills[:limit]]
            self.last_search = {"search_id": None, "hits": hits}
            self._emit_search(query, hits)
            return hits
        terms = re.findall(r"[^\W_]+", query, flags=re.UNICODE)[:64]
        query = " ".join(terms).encode("utf-8")[:512].decode("utf-8", errors="ignore")
        response = self.client.agent.skill.search(
            self.agent_uuid,
            query=query,
            task_family=self.task_family,
            include_markdown=True,
            page_size=max(1, min(limit, 20)),
        )
        hits = [model_dict(hit) for hit in (response.items or [])[:limit]]
        self.last_search = {
            "search_id": value(response.response, "search_id"),
            "hits": hits,
        }
        self._emit_search(query, hits)
        return hits

    def _emit_search(self, query: str, hits: list[dict[str, Any]]) -> None:
        if self.on_event is None:
            return
        normalized = [
            {
                "uuid": str(skill_uuid(hit) or ""),
                "skill_id": str(value(hit, "skill_id") or skill_uuid(hit) or ""),
                "name": value(hit, "name"),
                "description": value(hit, "description"),
            }
            for hit in hits
        ]
        try:
            self.on_event("skill_search", {"query": query, "hits": normalized})
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Event sink failed for skill_search: %s", type(exc).__name__)

    @property
    def hits(self) -> list[dict[str, Any]]:
        return list(self.last_search["hits"]) if self.last_search else []

    def catalog(self) -> str:
        entries = []
        for hit in self.hits:
            lines = [
                f"- skill_id: {skill_uuid(hit)}",
                f"  name: {hit.get('name') or ''}",
                f"  description: {hit.get('description') or ''}",
            ]
            conditions = skill_use_when(str(hit.get("markdown") or ""))
            if conditions:
                lines.append("  use when: " + " ".join(conditions))
            entries.append("\n".join(lines))
        return SKILL_CATALOG_HEADER + "\n".join(entries) if entries else ""

    def load_skill(self, skill_id: str) -> str:
        for hit in self.hits:
            if skill_uuid(hit) == skill_id.strip():
                if all(skill_uuid(item) != skill_id.strip() for item in self.loaded):
                    self.loaded.append(hit)
                self._emit_load(skill_id.strip(), value(hit, "name"), True)
                return str(hit.get("markdown") or "")
        self.invalid_loads += 1
        self._emit_load(skill_id.strip(), None, False)
        return "Unknown skill_id. Use a skill_id from the Available skills list."

    def _emit_load(self, skill_id: str, name: str | None, found: bool) -> None:
        if self.on_event is None:
            return
        try:
            self.on_event(
                "skill_load",
                {"skill_id": skill_id, "name": name, "found": found},
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Event sink failed for skill_load: %s", type(exc).__name__)

    def record_use(
        self,
        trajectory_uuid: str,
        skill: dict[str, Any],
        usage: str,
        outcome: str,
    ) -> dict[str, Any]:
        if not self.last_search:
            raise RuntimeError("record_use requires a Skill search")
        search_id = self.last_search.get("search_id")
        skill_id = skill_uuid(skill)
        if not search_id or not skill_id:
            raise RuntimeError("Skill search did not return a search ID and Skill UUID")
        return model_dict(
            self.client.agent.skill.use.create(
                self.agent_uuid,
                skill_id,
                search_id=search_id,
                trace_uuid=trajectory_uuid,
                usage=usage,
                outcome=AddAgentSkillUseOutcomeRequest(outcome=outcome),
                reason_code="tau2_bench_skills",
                idempotency_key=_idempotency_key(f"use:{trajectory_uuid}:{skill_id}"),
            )
        )
