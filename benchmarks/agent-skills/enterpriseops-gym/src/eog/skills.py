from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from zep_cloud import Zep
from zep_cloud.types.agent_trace_close_verifier import AgentTraceCloseVerifier

from .agent import SkillIndex, SkillIndexEntry
from .dataset import Task
from .ledger import JsonlLedger
from .recompile import tool_result_content_is_error
from .split import assert_skill_sources

SKILL_LIST_PAGE_SIZE = 20
SKILL_VERSION_LIST_PAGE_SIZE = 100
SKILL_EVIDENCE_LIST_PAGE_SIZE = 100
LEARNING_RUNS_LIST_PAGE_SIZE = 100
TERMINAL_DECISIONS = frozenset({"produced", "no_change", "rejected", "failed"})
# 20 minutes is longer than the 15-minute retry hold of a deferred compile window, so a held window
# writes a new outcome before the quiet time ends.
COMPILE_WAIT_QUIET_SECONDS = 20 * 60
DEFAULT_COMPILE_WAIT_CEILING_SECONDS = 90 * 60

DATE_PATTERNS = (
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(
        r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
        r"\s+\d{1,2}(?:st|nd|rd|th)?(?:,\s+\d{4})?\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b", re.IGNORECASE),
)
TRACE_EVENT_TYPES = {
    "observation": "observation",
    "decision": "decision",
    "tool_call": "tool_call",
    "tool_result": "tool_result",
    "turn": "checkpoint",
    "turn_limit": "checkpoint",
    "context_overflow": "checkpoint",
    "context_summarization": "reflection",
    "read_skill_nudge": "checkpoint",
}
QUOTED_LITERAL = re.compile(r"'((?:[^']|'')*)'")
LONG_NUMBER = re.compile(r"(?<!\w)-?\d{3,}(?:\.\d+)?(?!\w)")
ZEP_EVIDENCE_COMMENT = re.compile(
    r"<!--\s*zep-evidence:\s*"
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"(?:\s*,\s*[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})*"
    r"\s*-->"
)


FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", re.DOTALL)


def skill_frontmatter(skill_markdown: str) -> dict[str, str]:
    """Return the scalar fields of the Skill frontmatter (for example name and description)."""
    match = FRONTMATTER.match(skill_markdown)
    if match is None:
        return {}
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, separator, value = line.partition(":")
        if not separator or not key or key[0].isspace():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            quote = value[0]
            value = value[1:-1]
            value = value.replace('\\"', '"').replace("\\\\", "\\") if quote == '"' else value.replace("''", "'")
        fields[key.strip()] = value
    return fields


def build_skill_index(
    snapshot_skills: dict[str, dict[str, str]], families: tuple[str, ...] | list[str], *, short_ids: bool = False
) -> SkillIndex:
    """Build the Skill index of the snapshot Skills of the given task families, in snapshot order.

    With short_ids, the index and read_skill name each Skill by the first 8 hex characters of its UUID,
    not by its name.
    """
    wanted = set(families)
    entries = []
    for skill in snapshot_skills.values():
        if skill["task_family"] not in wanted:
            continue
        fields = skill_frontmatter(skill["markdown"])
        name = fields.get("name", "").strip()
        if not name:
            raise ValueError(f"Skill {skill['id']} has no name in its frontmatter")
        entries.append(
            SkillIndexEntry(
                id=str(skill["id"]),
                sha256=str(skill["sha256"]),
                name=name,
                description=fields.get("description", "").strip(),
                markdown=skill["markdown"],
            )
        )
    names = [entry.name for entry in entries]
    if len(set(names)) != len(names):
        raise ValueError("Two Skills in the snapshot have the same name")
    if short_ids:
        for entry in entries:
            try:
                uuid.UUID(entry.id)
            except ValueError as error:
                raise ValueError(f"Skill {entry.id} has no UUID id, so it has no short id") from error
    index = SkillIndex(tuple(entries), short_ids=short_ids)
    handles = [index.handle(entry) for entry in entries]
    if len(set(handles)) != len(handles):
        raise ValueError("Two Skills in the snapshot have the same short id")
    return index


def date_literals(skill_markdown: str) -> list[str]:
    return sorted({match.group(0) for pattern in DATE_PATTERNS for match in pattern.finditer(skill_markdown)})


def _expected_literals(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value} if value else set()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        rendered = str(value)
        return {rendered} if LONG_NUMBER.fullmatch(rendered) else set()
    if isinstance(value, list):
        return set().union(*(_expected_literals(item) for item in value)) if value else set()
    if isinstance(value, dict):
        return set().union(*(_expected_literals(item) for item in value.values())) if value else set()
    return set()


# Verifier literals that are common English prose words, not task values. A Skill text that uses one of
# them is not a score-task leak, so the gate ignores them.
COMMON_PROSE_LITERALS = frozenset({"desk assignment", "employee offboarding", "establishing", "relocation"})


def _task_verifier_literals(task: Task) -> set[str]:
    literals: set[str] = set()
    for verifier in task.verifiers:
        validation = verifier.get("validation_config", {})
        query = str(validation.get("query", ""))
        literals.update(item.replace("''", "'") for item in QUOTED_LITERAL.findall(query))
        literals.update(LONG_NUMBER.findall(query))
        literals.update(_expected_literals(validation.get("expected_value")))
    return {item for item in literals if item and item.casefold() not in COMMON_PROSE_LITERALS}


def _literal_pattern(literal: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![0-9A-Za-z_]){re.escape(literal.casefold())}(?![0-9A-Za-z_])")


def assert_no_score_literals(
    skills: list[dict[str, Any]] | str,
    split: dict[str, Any],
    tasks: tuple[Task, ...] | list[Task],
) -> None:
    task_by_id = {task.task_id: task for task in tasks}
    sides = {task_id: item["side"] for task_id, item in split["tasks"].items()}
    teach_sources = []
    for task in tasks:
        if sides.get(task.task_id) == "teach":
            teach_sources.extend(
                (
                    task.system_prompt,
                    task.user_prompt,
                    json.dumps(task.verifiers, sort_keys=True, ensure_ascii=False),
                )
            )
    teach_text = "\n".join(teach_sources).casefold()
    skill_items = skills if isinstance(skills, list) else [{"markdown": skills, "skill_id": "snapshot"}]
    for task_id, side in sides.items():
        if side != "score" or task_id not in task_by_id:
            continue
        for literal in _task_verifier_literals(task_by_id[task_id]):
            pattern = _literal_pattern(literal)
            if pattern.search(teach_text):
                continue
            for skill in skill_items:
                markdown = ZEP_EVIDENCE_COMMENT.sub("", str(skill.get("markdown", ""))).casefold()
                if pattern.search(markdown):
                    raise ValueError(
                        f"Skill {skill.get('skill_id', 'unknown')} contains a score-task literal from task {task_id}"
                    )


def ensure_agent(client: Zep, run_id: str, *, suffix: str = "") -> dict[str, str]:
    agent_id = f"eog-skills-{run_id}" + (f"-{suffix}" if suffix else "")
    agent = client.agent.create(
        agent_id=agent_id,
        name=agent_id,
        security_domain="enterpriseops-gym-skills",
        version="1.0.0",
        memory_settings={
            "compilation_mode": "on_close",
            "approval": "auto",
            "auto_approval_task_families": [
                f"eog.{domain}" for domain in ("calendar", "csm", "drive", "email", "hr", "hybrid", "itsm", "teams")
            ],
        },
    )
    agent_uuid = getattr(agent, "uuid", None) or getattr(agent, "uuid_", None)
    if not agent_uuid:
        raise RuntimeError("Zep did not return an Agent UUID")
    return {"agent_id": agent_id, "agent_uuid": str(agent_uuid)}


class TrajectoryRecorder:
    def __init__(
        self,
        client: Zep,
        agent_uuid: str,
        task: Any,
        run_id: str,
        trial: int,
        *,
        trace_path: Path | None = None,
        side: str = "teach",
    ) -> None:
        self.client = client
        self.agent_uuid = agent_uuid
        self.task = task
        self.run_id = run_id
        self.trial = trial
        self.trace_path = trace_path
        self.side = side
        self.uuid: str | None = None
        self.closed = False
        self.sequence = 0

    def __enter__(self) -> TrajectoryRecorder:
        trajectory = self.client.agent.trace.create(
            self.agent_uuid,
            objective=self.task.user_prompt[:512],
            task_family=f"eog.{self.task.domain}",
            metadata={
                "task_id": self.task.task_id,
                "domain": self.task.domain,
                "run_id": self.run_id,
                "trial": self.trial,
                "side": self.side,
            },
        )
        self.uuid = str(getattr(trajectory, "uuid", None) or getattr(trajectory, "uuid_", ""))
        if not self.uuid:
            raise RuntimeError("Zep did not return a Trajectory UUID")
        return self

    def add_event(
        self,
        event_type: str,
        content: str,
        tool: str | None = None,
        metadata: dict[str, Any] | None = None,
        tool_call_id: str | None = None,
    ) -> None:
        if not self.uuid:
            raise RuntimeError("Trajectory is not active")
        event_type = TRACE_EVENT_TYPES.get(event_type)
        if event_type is None:
            raise ValueError("Unsupported trajectory event type")
        self.sequence += 1
        event_metadata = dict(metadata or {})
        is_error = event_metadata.pop("is_error", False)
        if event_type == "tool_result":
            if tool:
                event_metadata.setdefault("tool_name", tool)
            if is_error or tool_result_content_is_error(content):
                event_metadata["error"] = True
        self.client.agent.trace.append_event(
            self.agent_uuid,
            self.uuid,
            event_type=event_type,
            content=content[:20_000],
            context={"tools": [{"name": tool}]} if tool else None,
            metadata=event_metadata or None,
            tool_call_id=tool_call_id or None,
            sequence=self.sequence,
        )

    def close(
        self,
        success: bool,
        *,
        verified: bool = False,
        infra_failed: bool = False,
        score_metadata: dict[str, Any] | None = None,
    ) -> None:
        if not self.uuid or self.closed:
            return
        self.closed = True
        if infra_failed:
            self.client.agent.trace.abandon(
                self.agent_uuid,
                self.uuid,
                highest_accepted_sequence=self.sequence,
                reason="infra_failed",
                verification="none",
            )
        else:
            outcome = "succeeded" if success and verified else "failed"
            self.client.agent.trace.close(
                self.agent_uuid,
                self.uuid,
                outcome=outcome,
                reason=None if verified else "The episode ended before verification.",
                verification="external" if verified else "none",
                verifier=(
                    AgentTraceCloseVerifier(
                        verifier_id="eog-verifier",
                        class_="harness",
                        outcome=outcome,
                        evidence_reference=self.uuid,
                    )
                    if verified
                    else None
                ),
            )
        if self.trace_path:
            JsonlLedger(self.trace_path).append(
                {
                    "trace_uuid": self.uuid,
                    "run_id": self.run_id,
                    "task_id": self.task.task_id,
                    "trial": self.trial,
                    "side": self.side,
                    "domain": self.task.domain,
                    "task_family": f"eog.{self.task.domain}",
                    "passed": success if verified else False,
                    "verified": verified,
                    "infra_failed": infra_failed,
                    **({"score_metadata": score_metadata} if score_metadata is not None else {}),
                }
            )

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if not self.closed:
            self.close(False, infra_failed=exc_type is not None)


def compile_snapshot(
    client: Zep,
    agent_uuid: str,
    task_families: list[str],
    split: dict[str, Any],
    output_dir: Path,
    *,
    traces_path: Path | None = None,
    wait: dict | None = None,
    task_scope: str = "split",
    include_pending_families: list[str] | None = None,
) -> dict[str, Any]:
    included_pending_families = set(include_pending_families or [])
    trace_tasks = {}
    if traces_path and traces_path.exists():
        trace_tasks = {
            row["trace_uuid"]: row
            for row in (json.loads(line) for line in traces_path.read_text().splitlines() if line.strip())
        }
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = output_dir / "snapshot.json"
    snapshot_path.unlink(missing_ok=True)
    for family in task_families:
        family_traces = [
            row for row in trace_tasks.values() if row.get("task_family", f"eog.{row.get('domain', '')}") == family
        ]
        report: dict[str, Any] = {
            "task_family": family,
            "traces_captured": len(family_traces),
            "passed_traces": sum(bool(row.get("passed")) for row in family_traces),
            "skills": [],
            "skills_count": 0,
            "date_literals": {},
            "failures": [],
            "compile_rejections": [],
        }
        try:
            state = client.agent.learning.get(agent_uuid, task_family=family)
            state_data = (
                state.model_dump(mode="json", exclude_none=True) if hasattr(state, "model_dump") else vars(state)
            )
            report["learning_state"] = state_data
            if state_data.get("compilation_pending") is True:
                report[
                    "pending_family_included" if family in included_pending_families else "pending_family_skipped"
                ] = True
        except Exception as error:
            report["failures"].append({"error": type(error).__name__, "message": str(error)})
        family_wait = (wait or {}).get("families", {}).get(family)
        if family_wait is not None:
            report["reported_trace_count"] = family_wait["reported_trace_count"]
            report["failed_outcomes"] = family_wait["failed_outcomes"]
            if family_wait.get("done") is False and family not in included_pending_families:
                report["failures"].append(
                    {
                        "error": "compile_wait_timeout",
                        "closed_traces": family_wait["closed"],
                        "reported_trace_count": family_wait["reported_trace_count"],
                        "failed_outcomes": family_wait["failed_outcomes"],
                    }
                )
        try:
            outcomes = client.agent.learning.list_runs(
                agent_uuid,
                task_family=family,
                page_size=LEARNING_RUNS_LIST_PAGE_SIZE,
            )
            report["compile_outcomes"] = [
                outcome.model_dump(mode="json", exclude_none=True) if hasattr(outcome, "model_dump") else vars(outcome)
                for outcome in outcomes
            ]
            report["failed_outcomes"] = sum(
                outcome.get("decision") == "failed" for outcome in report["compile_outcomes"]
            )
            report["failures"].extend(
                {"error": "compile_failed", "outcome": outcome}
                for outcome in report["compile_outcomes"]
                if outcome.get("decision") == "failed"
            )
            report["compile_rejections"].extend(
                {"outcome": outcome}
                for outcome in report["compile_outcomes"]
                if outcome.get("decision") != "failed"
                and (outcome.get("rejection_class") or outcome.get("decision") == "rejected")
            )
        except Exception as error:
            report["compile_outcomes"] = []
            report["failures"].append({"error": type(error).__name__, "message": str(error)})
        report_path = output_dir / family.replace(".", "_") / "compile_report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    skills = []
    for family in task_families:
        report_path = output_dir / family.replace(".", "_") / "compile_report.json"
        report = json.loads(report_path.read_text())
        family_pending = report.get("learning_state", {}).get("compilation_pending") is True
        if any(row.get("error") == "compile_wait_timeout" for row in report["failures"]) or (
            family_pending and family not in included_pending_families
        ):
            continue
        family_skills = []
        try:
            family_records = client.agent.skill.list(
                agent_uuid,
                task_family=family,
                page_size=SKILL_LIST_PAGE_SIZE,
            )
        except Exception as error:
            report["failures"].append({"error": type(error).__name__, "message": str(error)})
            report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
            continue
        for skill in family_records:
            skill_id = str(
                getattr(skill, "skill_id", None) or getattr(skill, "uuid", None) or getattr(skill, "uuid_", "")
            )
            markdown = str(getattr(skill, "markdown", "") or "")
            if not markdown and skill_id:
                versions = list(
                    client.agent.skill.version.list(
                        agent_uuid,
                        skill_id,
                        page_size=SKILL_VERSION_LIST_PAGE_SIZE,
                    )
                )
                markdown = str(getattr(versions[-1], "markdown", "") or "") if versions else ""
            if not markdown:
                report["failures"].append({"error": "missing_skill_content", "skill_id": skill_id})
                continue
            digest = hashlib.sha256(markdown.encode()).hexdigest()
            path = output_dir / f"{digest}.md"
            skill_data = (
                skill.model_dump(mode="json", exclude_none=True) if hasattr(skill, "model_dump") else dict(skill)
            )
            try:
                evidence = client.agent.skill.evidence.list(
                    agent_uuid,
                    skill_id,
                    page_size=SKILL_EVIDENCE_LIST_PAGE_SIZE,
                )
                evidence_data = [
                    item.model_dump(mode="json", exclude_none=True) if hasattr(item, "model_dump") else vars(item)
                    for item in evidence
                ]
                lineage = {"skill": skill_data, "evidence": evidence_data}
                trace_ids = assert_skill_sources(lineage, split, trace_tasks, task_scope=task_scope)
            except Exception as error:
                report["failures"].append({"error": type(error).__name__, "message": str(error), "skill_id": skill_id})
                continue
            path.write_text(markdown)
            family_skills.append(
                {
                    "skill_id": skill_id,
                    "task_family": family,
                    "path": path.name,
                    "sha256": digest,
                    "lineage": lineage,
                    "source_trace_uuids": trace_ids,
                    "date_literals": date_literals(markdown),
                }
            )
            skills.append(
                {
                    "skill_id": skill_id,
                    "task_family": family,
                    "path": path.name,
                    "sha256": digest,
                    "lineage": lineage,
                    "date_literals": date_literals(markdown),
                }
            )
        report["skills"] = [{"id": item["skill_id"], "sha256": item["sha256"]} for item in family_skills]
        report["skills_count"] = len(family_skills)
        report["date_literals"] = {item["skill_id"]: item["date_literals"] for item in family_skills}
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    snapshot = {"skills": skills, "sha256": hashlib.sha256(json.dumps(skills, sort_keys=True).encode()).hexdigest()}
    compile_errors = []
    blocking_compile_errors = []
    for family in task_families:
        report = json.loads((output_dir / family.replace(".", "_") / "compile_report.json").read_text())
        failures = report.get("failures", [])
        compile_errors.extend(failures)
        blocking_compile_errors.extend(
            failure
            for failure in failures
            if not (
                report.get("pending_family_included") is True
                and failure.get("error") in {"compile_failed", "compile_wait_timeout"}
            )
        )
    (output_dir / "skills.json").write_text(
        json.dumps({**snapshot, "compile_failures": compile_errors}, indent=2, sort_keys=True) + "\n"
    )
    if not blocking_compile_errors:
        snapshot_path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
    return snapshot


def load_snapshot_details(path: Path, *, task_family: str | None = None) -> tuple[str, str, list[dict[str, str]]]:
    snapshot = json.loads(path.read_text())
    blocks = []
    skills = []
    for item in snapshot["skills"]:
        if task_family is not None and item["task_family"] != task_family:
            continue
        markdown = (path.parent / item["path"]).read_text()
        digest = hashlib.sha256(markdown.encode()).hexdigest()
        if digest != item["sha256"]:
            raise ValueError(f"Skill snapshot hash mismatch: {item['skill_id']}")
        blocks.append(markdown)
        skills.append({"id": str(item["skill_id"]), "sha256": digest, "markdown": markdown})
    block = "\n\n".join(blocks)
    return block, hashlib.sha256(block.encode()).hexdigest(), skills


def load_snapshot(path: Path) -> tuple[str, str]:
    block, digest, _ = load_snapshot_details(path)
    return block, digest


def _learning_run_id(outcome: dict[str, Any]) -> str | None:
    run_id = outcome.get("uuid", outcome.get("run_uuid"))
    return str(run_id) if run_id is not None else None


def list_learning_run_ids(client: Any, agent_uuid: str, families: list[str] | tuple[str, ...]) -> dict[str, set[str]]:
    run_ids: dict[str, set[str]] = {}
    for family in families:
        pager = client.agent.learning.list_runs(
            agent_uuid,
            task_family=family,
            page_size=LEARNING_RUNS_LIST_PAGE_SIZE,
        )
        outcomes = [
            outcome.model_dump(mode="json", exclude_none=True) if hasattr(outcome, "model_dump") else vars(outcome)
            for outcome in pager
        ]
        run_ids[family] = {run_id for outcome in outcomes if (run_id := _learning_run_id(outcome)) is not None}
    return run_ids


WAIT_FOR_COMPILATION_RULE = (
    "The wait tracks compile outcomes for each task family. A family with closed Traces also needs at least one "
    "terminal outcome. compilation_pending must be false, and outcomes must not change for 20 minutes. A family "
    "with no closed Traces is complete. The sum of trace_count is reported for information only, because the "
    "server does not set it on every run. Outcomes that existed before the replay started do not count as terminal "
    "outcomes. The wait ends at the configured ceiling and marks each incomplete family as timed out."
)


def wait_for_compilation(
    client: Any,
    agent_uuid: str,
    closed_traces: dict[str, int],
    *,
    ceiling_seconds: float = DEFAULT_COMPILE_WAIT_CEILING_SECONDS,
    quiet_seconds: float = COMPILE_WAIT_QUIET_SECONDS,
    poll_interval: float = 30,
    preexisting_run_ids: Mapping[str, set[str]] | None = None,
    clock: Any = time.monotonic,
    sleep: Any = time.sleep,
) -> dict[str, Any]:
    """Wait for quiet outcomes and no pending compilation, with a terminal outcome for closed Traces."""
    start = clock()
    polls = 0
    families = {
        family: {
            "closed": closed,
            "preexisting_outcomes": len((preexisting_run_ids or {}).get(family, set())),
            "reported_trace_count": 0,
            "failed_outcomes": 0,
            "pending": False,
            "minutes_since_change": 0.0,
            "done": closed == 0,
        }
        for family, closed in closed_traces.items()
    }
    last_fingerprints: dict[str, tuple] = {}
    last_change = {family: start for family in closed_traces}

    def result(now: float, timed_out: bool) -> dict[str, Any]:
        for family, state in families.items():
            state["minutes_since_change"] = round((now - last_change[family]) / 60, 1)
        return {
            "seconds": round(now - start, 1),
            "polls": polls,
            "timed_out": timed_out,
            "families": families,
        }

    while True:
        active_families = [family for family, state in families.items() if not state["done"]]
        if not active_families:
            return result(clock(), False)

        polls += 1
        family_outcomes = {}
        family_pending = {}
        for family in active_families:
            pager = client.agent.learning.list_runs(
                agent_uuid,
                task_family=family,
                page_size=LEARNING_RUNS_LIST_PAGE_SIZE,
            )
            outcomes = [
                outcome.model_dump(mode="json", exclude_none=True) if hasattr(outcome, "model_dump") else vars(outcome)
                for outcome in pager
            ]
            family_outcomes[family] = outcomes
            state = client.agent.learning.get(agent_uuid, task_family=family)
            state_data = (
                state.model_dump(mode="json", exclude_none=True) if hasattr(state, "model_dump") else vars(state)
            )
            family_pending[family] = state_data.get("compilation_pending") is not False

        now = clock()
        for family, outcomes in family_outcomes.items():
            fingerprints = [
                (
                    outcome.get("uuid", outcome.get("run_uuid")),
                    outcome.get("decision"),
                    outcome.get("attempts"),
                )
                for outcome in outcomes
            ]
            fingerprint = tuple(sorted(fingerprints, key=repr))
            if family not in last_fingerprints:
                last_fingerprints[family] = fingerprint
            elif fingerprint != last_fingerprints[family]:
                last_fingerprints[family] = fingerprint
                last_change[family] = now

            terminal_outcomes = [
                outcome
                for outcome in outcomes
                if _learning_run_id(outcome) not in (preexisting_run_ids or {}).get(family, set())
            ]
            reported_trace_count = sum(
                (outcome.get("trace_count") or 0)
                for outcome in terminal_outcomes
                if outcome.get("decision") in TERMINAL_DECISIONS
            )
            failed_outcomes = sum(outcome.get("decision") == "failed" for outcome in terminal_outcomes)
            has_terminal_outcome = any(outcome.get("decision") in TERMINAL_DECISIONS for outcome in terminal_outcomes)
            pending = family_pending[family]
            minutes_since_change = round((now - last_change[family]) / 60, 1)
            done = (
                not pending
                and (families[family]["closed"] == 0 or has_terminal_outcome)
                and now - last_change[family] >= quiet_seconds
            )
            families[family].update(
                {
                    "reported_trace_count": reported_trace_count,
                    "failed_outcomes": failed_outcomes,
                    "pending": pending,
                    "minutes_since_change": minutes_since_change,
                    "done": done,
                }
            )
            if not done:
                print(
                    f"compile wait: family={family} reported_trace_count={reported_trace_count} "
                    f"closed={families[family]['closed']} "
                    f"pending={str(pending).lower()} minutes_since_change={minutes_since_change:.1f}",
                    file=sys.stderr,
                )

        if all(state["done"] for state in families.values()):
            return result(now, False)
        if now - start >= ceiling_seconds:
            return result(now, True)
        sleep(min(poll_interval, ceiling_seconds - (now - start)))
