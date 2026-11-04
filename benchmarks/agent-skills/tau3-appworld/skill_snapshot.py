from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from zep_cloud import Zep

SKILL_LIST_PAGE_SIZE = 20
SKILL_VERSION_LIST_PAGE_SIZE = 100
SKILL_EVIDENCE_LIST_PAGE_SIZE = 100
LEARNING_RUNS_PAGE_SIZE = 100
TERMINAL_DECISIONS = frozenset({"produced", "no_change", "rejected"})
COMPILE_WAIT_QUIET_SECONDS = 20 * 60
DEFAULT_COMPILE_WAIT_CEILING_SECONDS = 90 * 60
READ_SKILL_TOOL_NAME = "read_skill"
READ_SKILL_NAME_DISTANCE = 3
READ_SKILL_NUDGE = (
    "A reference procedure may match this task. Read it with read_skill, or continue."
)
SKILLS_HEADER = (
    "Reference procedures. The list below names procedures that other runs used. "
    "Before your first tool call, use the read_skill tool to read the procedure whose description matches "
    "the task. Adapt its steps to the task; the procedure is a guide, not the task. "
    "When a tool returns an error that a description names, read that procedure. "
    "The task is only in the user message."
)
READ_SKILL_TOOL: dict[str, Any] = {
    "type": "function",
    "name": READ_SKILL_TOOL_NAME,
    "description": "Read one reference procedure from the Reference procedures list.",
    "parameters": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The name of the procedure, as the Reference procedures list gives it.",
            }
        },
        "required": ["name"],
        "additionalProperties": False,
    },
}


class SnapshotExportError(RuntimeError):
    def __init__(self, failures: list[dict[str, Any]]) -> None:
        self.failures = failures
        super().__init__("Skill snapshot export failed: " + json.dumps(failures, sort_keys=True))


def _record_export_failure(
    report: dict[str, Any],
    export_failures: list[dict[str, Any]],
    family: str,
    failure: dict[str, Any],
) -> None:
    report["failures"].append(failure)
    report["export_errors"].append(failure)
    export_failures.append({"task_family": family, **failure})


def model_dict(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return item
    if hasattr(item, "model_dump"):
        return item.model_dump(mode="json", exclude_none=True)
    if hasattr(item, "dict"):
        return item.dict(by_alias=True, exclude_none=True)
    return dict(vars(item))


def value(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def skill_uuid(item: Any) -> str | None:
    identifier = value(item, "uuid") or value(item, "uuid_") or value(item, "skill_id")
    return str(identifier) if identifier else None


def skill_frontmatter(markdown: str) -> dict[str, str]:
    if not markdown.startswith("---\n"):
        return {}
    _, body = markdown.split("---\n", 1)
    if "\n---" not in body:
        return {}
    frontmatter, _ = body.split("\n---", 1)
    fields: dict[str, str] = {}
    for line in frontmatter.splitlines():
        key, separator, value = line.partition(":")
        if separator and key and not key[0].isspace():
            fields[key.strip()] = value.strip().strip("\"'")
    return fields


def _edit_distance(left: str, right: str) -> int:
    if abs(len(left) - len(right)) > READ_SKILL_NAME_DISTANCE:
        return READ_SKILL_NAME_DISTANCE + 1
    previous = list(range(len(right) + 1))
    for row, left_char in enumerate(left, start=1):
        current = [row]
        for column, right_char in enumerate(right, start=1):
            current.append(
                min(
                    previous[column] + 1,
                    current[column - 1] + 1,
                    previous[column - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


@dataclass(frozen=True)
class SkillIndexEntry:
    skill_id: str
    task_family: str
    name: str
    description: str
    markdown: str
    sha256: str


class SkillIndex:
    def __init__(self, entries: list[SkillIndexEntry]) -> None:
        self.entries = tuple(entries)

    def lines(self) -> str:
        return "\n".join(f"- {entry.name}: {entry.description}" for entry in self.entries)

    def prompt_block(self) -> str:
        if not self.entries:
            return ""
        return f"{SKILLS_HEADER}\n\n{self.lines()}"

    def resolve(self, requested: object) -> tuple[SkillIndexEntry | None, str | None]:
        if not isinstance(requested, str):
            return None, None
        wanted = requested.strip()
        exact = next(
            (entry for entry in self.entries if entry.name.casefold() == wanted.casefold()), None
        )
        if exact is not None:
            return exact, None
        nearest: SkillIndexEntry | None = None
        best = READ_SKILL_NAME_DISTANCE + 1
        for entry in self.entries:
            distance = _edit_distance(wanted.casefold(), entry.name.casefold())
            if distance < best:
                nearest = entry
                best = distance
        return (nearest, requested) if nearest is not None else (None, None)


class FrozenSkillReader:
    def __init__(self, index: SkillIndex, agent_uuid: str | None = None) -> None:
        self.index = index
        self.agent_uuid = agent_uuid
        self.loaded_markdown: list[str] = []
        self.loaded_skill_ids: list[str] = []

    @classmethod
    def from_snapshot(cls, path: Path, *, task_family: str) -> FrozenSkillReader:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        if snapshot.get("complete") is False:
            raise ValueError("The Skills arm will not use an incomplete snapshot")
        entries: list[SkillIndexEntry] = []
        for skill in snapshot.get("skills", []):
            record_family = skill.get("task_family")
            if not isinstance(record_family, str) or not record_family.strip():
                raise ValueError("A snapshot Skill has no task_family")
            markdown = str(skill.get("full_markdown") or skill.get("markdown") or "")
            fields = skill_frontmatter(markdown)
            name = fields.get("name", "").strip()
            if not name:
                raise ValueError("A snapshot Skill has no name in its frontmatter")
            if record_family != task_family:
                continue
            description = fields.get("description", "").strip()
            digest = str(skill.get("sha256") or hashlib.sha256(markdown.encode()).hexdigest())
            entries.append(
                SkillIndexEntry(
                    skill_id=str(skill.get("skill_id") or skill.get("uuid") or ""),
                    task_family=record_family,
                    name=name,
                    description=description,
                    markdown=markdown,
                    sha256=digest,
                )
            )
        if len({entry.name.casefold() for entry in entries}) != len(entries):
            raise ValueError("The snapshot contains duplicate Skill names")
        reader = cls(SkillIndex(entries), agent_uuid=snapshot.get("agent_uuid"))
        assert all(entry.task_family == task_family for entry in reader.index.entries)
        return reader

    def read(self, requested: object) -> tuple[str, bool, str | None, str | None]:
        entry, repaired_from = self.index.resolve(requested)
        if entry is None:
            return (
                f'No procedure is named "{requested}". The Reference procedures are:\n{self.index.lines()}',
                False,
                None,
                None,
            )
        self.loaded_markdown.append(entry.markdown)
        if entry.skill_id and entry.skill_id not in self.loaded_skill_ids:
            self.loaded_skill_ids.append(entry.skill_id)
        return entry.markdown, True, entry.name, repaired_from


def snapshot_sha256(path: Path | None) -> str:
    if path is None:
        return hashlib.sha256(b"[]").hexdigest()
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _outcome_fingerprint(outcome: dict[str, Any]) -> tuple[Any, ...]:
    return (
        outcome.get("uuid") or outcome.get("run_uuid"),
        outcome.get("decision"),
        outcome.get("attempts"),
        outcome.get("trace_count"),
    )


def wait_for_compilation(
    client: Any,
    agent_uuid: str,
    closed_traces: dict[str, int],
    *,
    ceiling_seconds: float = DEFAULT_COMPILE_WAIT_CEILING_SECONDS,
    quiet_seconds: float = COMPILE_WAIT_QUIET_SECONDS,
    poll_interval: float = 30,
    clock: Any = time.monotonic,
    sleep: Any = time.sleep,
) -> dict[str, Any]:
    started = clock()
    last_change = {family: started for family in closed_traces}
    fingerprints: dict[str, tuple[tuple[Any, ...], ...]] = {}
    states = {
        family: {
            "closed": closed,
            "covered": 0,
            "pending": closed > 0,
            "minutes_since_change": 0.0,
            "done": closed == 0,
        }
        for family, closed in closed_traces.items()
    }
    polls = 0

    def result(now: float, timed_out: bool) -> dict[str, Any]:
        for family, state in states.items():
            state["minutes_since_change"] = round((now - last_change[family]) / 60, 1)
        return {
            "seconds": round(now - started, 1),
            "polls": polls,
            "timed_out": timed_out,
            "coverage_method": "trace_count for produced, no_change, and rejected runs",
            "families": states,
        }

    while True:
        active = [family for family, state in states.items() if not state["done"]]
        if not active:
            return result(clock(), False)
        polls += 1
        for family in active:
            outcomes = [
                model_dict(item)
                for item in client.agent.learning.list_runs(
                    agent_uuid,
                    task_family=family,
                    page_size=LEARNING_RUNS_PAGE_SIZE,
                )
            ]
            fingerprint = tuple(sorted((_outcome_fingerprint(item) for item in outcomes), key=repr))
            if family not in fingerprints:
                fingerprints[family] = fingerprint
            elif fingerprints[family] != fingerprint:
                fingerprints[family] = fingerprint
                last_change[family] = clock()
            learning = model_dict(client.agent.learning.get(agent_uuid, task_family=family))
            now = clock()
            covered = sum(
                int(item.get("trace_count") or 0)
                for item in outcomes
                if item.get("decision") in TERMINAL_DECISIONS
            )
            pending = learning.get("compilation_pending") is not False
            state = states[family]
            state.update(
                {
                    "covered": covered,
                    "pending": pending,
                    "minutes_since_change": round((now - last_change[family]) / 60, 1),
                    "done": (
                        covered >= state["closed"]
                        and not pending
                        and now - last_change[family] >= quiet_seconds
                    ),
                }
            )
        now = clock()
        if all(state["done"] for state in states.values()):
            return result(now, False)
        if now - started >= ceiling_seconds:
            return result(now, True)
        sleep(poll_interval)


def _trace_ids(value_to_visit: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value_to_visit, dict):
        for key, value_item in value_to_visit.items():
            if key in {"trace_uuid", "trajectory_id"} and isinstance(value_item, str):
                found.add(value_item)
            else:
                found.update(_trace_ids(value_item))
    elif isinstance(value_to_visit, list):
        for value_item in value_to_visit:
            found.update(_trace_ids(value_item))
    return found


EVIDENCE_CITATION_RE = re.compile(r"<!--\s*zep-evidence:\s*([^<>\s]+)\s*-->")


def _skill_evidence(skill: dict[str, Any]) -> list[dict[str, Any]]:
    lineage = skill.get("lineage")
    evidence = lineage.get("evidence", []) if isinstance(lineage, dict) else []
    return (
        [item for item in evidence if isinstance(item, dict)] if isinstance(evidence, list) else []
    )


def _cited_trace_ids(skill: dict[str, Any]) -> set[str]:
    citations = set(EVIDENCE_CITATION_RE.findall(str(skill.get("full_markdown") or "")))
    return {
        str(item.get("trace_uuid") or item.get("trace_id") or item.get("source_id"))
        for item in _skill_evidence(skill)
        if (item.get("summary_version_uuid") or item.get("source_id")) in citations
        and (item.get("trace_uuid") or item.get("trace_id") or item.get("source_id"))
    }


def filter_snapshot_skills(
    skills: list[dict[str, Any]],
    traces: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, list[str]]]:
    trace_statuses: dict[str, bool | None] = {}
    for trace in traces:
        trace_id = trace.get("trace_uuid")
        if not trace_id:
            continue
        status = trace.get("passed") if isinstance(trace.get("passed"), bool) else None
        trace_id = str(trace_id)
        if trace_id in trace_statuses and trace_statuses[trace_id] != status:
            trace_statuses[trace_id] = None
        else:
            trace_statuses[trace_id] = status

    kept: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    lineage_failed: dict[str, set[str]] = {}
    for skill in skills:
        markdown = str(skill.get("full_markdown") or "")
        citations = set(EVIDENCE_CITATION_RE.findall(markdown))
        evidence = _skill_evidence(skill)
        evidence_by_summary: dict[str, list[dict[str, Any]]] = {}
        lineage_trace_ids: set[str] = set()
        for item in evidence:
            summary_id = item.get("summary_version_uuid")
            if not summary_id and item.get("source_kind") == "summary":
                summary_id = item.get("source_id")
            trace_id = item.get("trace_uuid") or item.get("trace_id")
            if not trace_id and item.get("source_kind") == "trace":
                trace_id = item.get("source_id")
            if summary_id:
                evidence_by_summary.setdefault(str(summary_id), []).append(item)
            if trace_id:
                lineage_trace_ids.add(str(trace_id))

        cited_trace_ids: set[str] = set()
        unresolved = False
        cites_failed_trace = False
        supporting_failed: set[str] = set()
        neutral_failed: set[str] = set()
        failed_lineage_by_polarity = {
            polarity: set() for polarity in ("supporting", "neutral", "contradicting", "none")
        }
        for item in evidence:
            trace_id = item.get("trace_uuid") or item.get("trace_id")
            if not trace_id and item.get("source_kind") == "trace":
                trace_id = item.get("source_id")
            if not trace_id or trace_statuses.get(str(trace_id)) is not False:
                continue
            polarity = str(item.get("polarity") or item.get("relationship") or "").lower()
            if polarity not in failed_lineage_by_polarity:
                polarity = "none"
            failed_lineage_by_polarity[polarity].add(str(trace_id))
            if polarity == "supporting":
                supporting_failed.add(str(trace_id))
            elif polarity == "neutral":
                neutral_failed.add(str(trace_id))
        for summary_id in citations:
            matching_evidence = evidence_by_summary.get(summary_id)
            if not matching_evidence:
                unresolved = True
                continue
            if any(not item.get("trace_uuid") for item in matching_evidence):
                unresolved = True
            resolved_ids = {
                str(item["trace_uuid"]) for item in matching_evidence if item.get("trace_uuid")
            }
            if not resolved_ids:
                unresolved = True
                continue
            if len(resolved_ids) > 1:
                unresolved = True
            cited_trace_ids.update(resolved_ids)
            for trace_id in resolved_ids:
                status = trace_statuses.get(trace_id)
                if trace_id not in trace_statuses or status is None:
                    unresolved = True
                elif status is False:
                    cites_failed_trace = True

        family = skill.get("task_family")
        if family:
            failed_lineage = {
                trace_id for trace_id in lineage_trace_ids if trace_statuses.get(trace_id) is False
            }
            lineage_failed.setdefault(str(family), set()).update(failed_lineage)

        if cites_failed_trace or unresolved or supporting_failed:
            full_skill = skill_frontmatter(markdown)
            excluded.append(
                {
                    "skill_id": (
                        skill.get("skill_id") or skill.get("uuid") or skill.get("id") or ""
                    ),
                    "name": skill.get("name") or full_skill.get("name", ""),
                    "task_family": family,
                    "reason": (
                        "cites_failed_trace"
                        if cites_failed_trace
                        else "unresolved_citation"
                        if unresolved
                        else "supporting_failed_lineage"
                    ),
                    "trace_uuids": sorted(
                        cited_trace_ids
                        | supporting_failed
                        | (neutral_failed if cites_failed_trace else set())
                    ),
                    "failed_lineage_trace_uuids_by_polarity": {
                        polarity: sorted(trace_ids)
                        for polarity, trace_ids in failed_lineage_by_polarity.items()
                    },
                }
            )
            continue

        kept.append(
            {
                **skill,
                "cited_trace_uuids": sorted(cited_trace_ids),
                "no_citation": not bool(citations),
                "neutral_failed_lineage_trace_uuids": sorted(neutral_failed),
                "failed_lineage_trace_uuids_by_polarity": {
                    polarity: sorted(trace_ids)
                    for polarity, trace_ids in failed_lineage_by_polarity.items()
                },
            }
        )

    return (
        kept,
        excluded,
        {family: sorted(trace_ids) for family, trace_ids in lineage_failed.items()},
    )


def compile_snapshot(
    client: Zep,
    agent_uuid: str,
    task_families: list[str],
    output_dir: Path,
    *,
    traces_path: Path | None = None,
    wait: dict[str, Any] | None = None,
) -> dict[str, Any]:
    traces = []
    if traces_path and traces_path.exists():
        traces = [json.loads(line) for line in traces_path.read_text().splitlines() if line.strip()]
    output_dir.mkdir(parents=True, exist_ok=True)
    skills: list[dict[str, Any]] = []
    excluded_skills: list[dict[str, Any]] = []
    lineage_failed_traces: dict[str, list[str]] = {}
    reports: dict[str, dict[str, Any]] = {}
    export_failures: list[dict[str, Any]] = []
    for family in task_families:
        family_traces = [row for row in traces if row.get("task_family") == family]
        report: dict[str, Any] = {
            "task_family": family,
            "traces_captured": len(family_traces),
            "passed_traces": sum(bool(row.get("passed")) for row in family_traces),
            "compile_outcomes": [],
            "skills": [],
            "failures": [],
            "export_errors": [],
        }
        family_skills: list[dict[str, Any]] = []

        family_wait = (wait or {}).get("families", {}).get(family)
        wait_timed_out = bool((wait or {}).get("timed_out"))
        family_not_done = family_wait is not None and not family_wait.get("done", False)
        family_timed_out = wait_timed_out and (family_wait is None or family_not_done)
        if family_timed_out or family_not_done:
            _record_export_failure(
                report,
                export_failures,
                family,
                {
                    "error": (
                        "compile_wait_timeout" if family_timed_out else "compile_wait_incomplete"
                    ),
                    "closed_traces": (family_wait or {}).get("closed", 0),
                    "covered_traces": (family_wait or {}).get("covered", 0),
                },
            )
        try:
            report["learning_state"] = model_dict(
                client.agent.learning.get(agent_uuid, task_family=family)
            )
            report["compile_outcomes"] = [
                model_dict(item)
                for item in client.agent.learning.list_runs(
                    agent_uuid,
                    task_family=family,
                    page_size=LEARNING_RUNS_PAGE_SIZE,
                )
            ]
        except Exception as exc:  # noqa: BLE001
            _record_export_failure(
                report,
                export_failures,
                family,
                {"error": type(exc).__name__, "message": str(exc)},
            )
        for outcome in report["compile_outcomes"]:
            if outcome.get("decision") == "failed":
                report["failures"].append({"error": "compile_failed", "outcome": outcome})
        reports[family] = report

        try:
            skill_hits = list(
                client.agent.skill.list(
                    agent_uuid,
                    task_family=family,
                    page_size=SKILL_LIST_PAGE_SIZE,
                )
            )
        except Exception as exc:  # noqa: BLE001
            _record_export_failure(
                report,
                export_failures,
                family,
                {"error": type(exc).__name__, "message": str(exc)},
            )
            skill_hits = []
        for hit in skill_hits:
            skill_id = skill_uuid(hit)
            if not skill_id:
                _record_export_failure(
                    report, export_failures, family, {"error": "missing_skill_id"}
                )
                continue
            try:
                skill = model_dict(client.agent.skill.get(agent_uuid, skill_id))
            except Exception as exc:  # noqa: BLE001
                _record_export_failure(
                    report,
                    export_failures,
                    family,
                    {"error": type(exc).__name__, "message": str(exc), "skill_id": skill_id},
                )
                continue
            agent_view = str(skill.get("markdown") or value(hit, "markdown") or "")
            current_version = skill.get("current_version") or value(hit, "current_version")
            try:
                versions = list(
                    client.agent.skill.version.list(
                        agent_uuid,
                        skill_id,
                        page_size=SKILL_VERSION_LIST_PAGE_SIZE,
                        markdown_format="full",
                    )
                )
            except Exception as exc:  # noqa: BLE001
                _record_export_failure(
                    report,
                    export_failures,
                    family,
                    {"error": type(exc).__name__, "message": str(exc), "skill_id": skill_id},
                )
                continue
            full_markdown = ""
            version_matched = current_version is None
            for version in versions:
                version_data = model_dict(version)
                if current_version is not None and version_data.get("version") == current_version:
                    version_matched = True
                    full_markdown = str(version_data.get("markdown") or "")
                    break
            if not version_matched:
                _record_export_failure(
                    report,
                    export_failures,
                    family,
                    {
                        "error": "current_version_not_found",
                        "skill_id": skill_id,
                        "current_version": current_version,
                    },
                )
                continue
            if current_version is None and not full_markdown and versions:
                full_markdown = str(model_dict(versions[-1]).get("markdown") or "")
            if not full_markdown:
                full_markdown = agent_view
            if not full_markdown:
                _record_export_failure(
                    report,
                    export_failures,
                    family,
                    {"error": "missing_skill_content", "skill_id": skill_id},
                )
                continue
            try:
                evidence = [
                    model_dict(item)
                    for item in client.agent.skill.evidence.list(
                        agent_uuid,
                        skill_id,
                        page_size=SKILL_EVIDENCE_LIST_PAGE_SIZE,
                    )
                ]
            except Exception as exc:  # noqa: BLE001
                _record_export_failure(
                    report,
                    export_failures,
                    family,
                    {"error": type(exc).__name__, "message": str(exc), "skill_id": skill_id},
                )
                continue
            digest = hashlib.sha256(full_markdown.encode()).hexdigest()
            record = {
                "skill_id": skill_id,
                "task_family": family,
                "name": skill.get("name") or value(hit, "name"),
                "current_version": current_version,
                "full_markdown": full_markdown,
                "markdown": full_markdown,
                "agent_view": agent_view,
                "sha256": digest,
                "lineage": {"skill": skill, "evidence": evidence},
                "source_trace_uuids": sorted(_trace_ids(evidence)),
            }
            family_skills.append(record)

        kept_skills, family_excluded, family_lineage_failed = filter_snapshot_skills(
            family_skills,
            traces,
        )
        skills.extend(kept_skills)
        excluded_skills.extend(family_excluded)
        lineage_failed_traces[family] = family_lineage_failed.get(family, [])
        report["excluded_skills"] = family_excluded
        report["lineage_failed_traces"] = len(lineage_failed_traces[family])
        report["lineage_failed_trace_uuids"] = lineage_failed_traces[family]
        for skill in kept_skills:
            digest = skill["sha256"]
            (output_dir / f"{digest}.md").write_text(
                skill["full_markdown"] + "\n",
                encoding="utf-8",
            )
            report["skills"].append({"skill_id": skill["skill_id"], "sha256": digest})
        report["skills_count"] = len(report["skills"])
        report_path = output_dir / family.replace(".", "_") / "compile_report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    if export_failures:
        (output_dir / "snapshot.json").unlink(missing_ok=True)
        raise SnapshotExportError(export_failures)

    snapshot = {
        "agent_uuid": agent_uuid,
        "complete": True,
        "skills": skills,
        "excluded_skills": excluded_skills,
        "lineage_failed_traces": lineage_failed_traces,
        "compile_reports": reports,
    }
    snapshot_path = output_dir / "snapshot.json"
    snapshot_path.write_text(
        json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    snapshot["sha256"] = hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
    return snapshot


def _funnel_for_family(
    family: str,
    report: dict[str, Any],
    skills: list[dict[str, Any]],
    passed_traces: set[str],
    excluded_skills: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    outcomes = [item for item in report.get("compile_outcomes", []) if isinstance(item, dict)]
    rejected: dict[str, int] = {}
    proposals = 0
    rejected_add_count = 0
    decisions = {"produced": 0, "no_change": 0, "rejected": 0, "failed": 0}
    windows = {
        str(outcome["compilation_window_uuid"])
        for outcome in outcomes
        if outcome.get("compilation_window_uuid")
    }
    for outcome in outcomes:
        proposal_count = int(outcome.get("proposal_count") or 0)
        rejected_count = int(outcome.get("rejected_add_count") or 0)
        proposals += proposal_count
        rejected_add_count += rejected_count
        decision = str(outcome.get("decision") or "unknown")
        decisions[decision] = decisions.get(decision, 0) + 1
        if rejected_count:
            label = str(outcome.get("rejection_class") or "other")
            rejected[label] = rejected.get(label, 0) + rejected_count
    skills = [skill for skill in skills if skill.get("task_family") == family]
    cited = {trace_id for skill in skills for trace_id in _cited_trace_ids(skill)}
    polarities = ("supporting", "neutral", "contradicting", "none")
    failed_lineage_skills_by_polarity = dict.fromkeys(polarities, 0)
    failed_lineage_trace_ids_by_polarity = {polarity: set() for polarity in polarities}
    for skill in skills:
        lineage = skill.get("failed_lineage_trace_uuids_by_polarity") or {}
        if not lineage and skill.get("neutral_failed_lineage_trace_uuids"):
            lineage = {"neutral": skill["neutral_failed_lineage_trace_uuids"]}
        for polarity in polarities:
            trace_ids = set(lineage.get(polarity) or [])
            if trace_ids:
                failed_lineage_skills_by_polarity[polarity] += 1
                failed_lineage_trace_ids_by_polarity[polarity].update(trace_ids)
    for skill in excluded_skills or []:
        if skill.get("task_family") != family:
            continue
        lineage = skill.get("failed_lineage_trace_uuids_by_polarity") or {}
        for polarity in polarities:
            failed_lineage_trace_ids_by_polarity[polarity].update(lineage.get(polarity) or [])
    return {
        "task_family": family,
        "compile_outcomes": outcomes,
        "windows": len(windows),
        "proposals": proposals,
        "admitted_additions": max(proposals - rejected_add_count, 0),
        "unique_admitted_skills": len(
            {
                skill["skill_id"]
                for skill in skills + (excluded_skills or []) + report.get("excluded_skills", [])
                if skill.get("task_family") == family
            }
        ),
        "rejected_add_count": rejected_add_count,
        "rejected_additions": rejected,
        "decisions": decisions,
        "no_change": decisions["no_change"],
        "skills": skills,
        "passed_traces": sorted(passed_traces),
        "passed_traces_cited": sorted(passed_traces & cited),
        "passed_traces_uncited": sorted(passed_traces - cited),
        "failed_neutral_lineage_skills": failed_lineage_skills_by_polarity["neutral"],
        "failed_lineage_skills_by_polarity": failed_lineage_skills_by_polarity,
        "failed_lineage_traces_by_polarity": {
            polarity: len(trace_ids)
            for polarity, trace_ids in failed_lineage_trace_ids_by_polarity.items()
        },
        "skills_without_citation": sum(bool(skill.get("no_citation")) for skill in skills),
    }


def write_funnel(
    output_dir: Path,
    task_families: list[str],
    snapshot: dict[str, Any],
    traces_path: Path | None,
) -> dict[str, Any]:
    traces = (
        [json.loads(line) for line in traces_path.read_text().splitlines() if line.strip()]
        if traces_path and traces_path.exists()
        else []
    )
    skills = snapshot.get("skills", [])
    lineage_failed_by_family = snapshot.get("lineage_failed_traces", {})
    families = {}
    for family in task_families:
        report_path = output_dir / family.replace(".", "_") / "compile_report.json"
        report = json.loads(report_path.read_text()) if report_path.exists() else {}
        passed = {
            str(row["trace_uuid"])
            for row in traces
            if row.get("task_family") == family and row.get("passed") and row.get("trace_uuid")
        }
        family_funnel = _funnel_for_family(
            family,
            report,
            skills,
            passed,
            snapshot.get("excluded_skills", []),
        )
        failed_lineage = lineage_failed_by_family.get(family, [])
        family_funnel["lineage_failed_traces"] = len(failed_lineage)
        family_funnel["lineage_failed_trace_uuids"] = sorted(failed_lineage)
        families[family] = family_funnel
    excluded_skills = snapshot.get("excluded_skills", [])
    funnel = {
        "families": families,
        "excluded_skills": excluded_skills,
        "total": {
            "windows": sum(item["windows"] for item in families.values()),
            "proposals": sum(item["proposals"] for item in families.values()),
            "admitted_additions": sum(item["admitted_additions"] for item in families.values()),
            "unique_admitted_skills": sum(
                item["unique_admitted_skills"] for item in families.values()
            ),
            "rejected_add_count": sum(item["rejected_add_count"] for item in families.values()),
            "no_change": sum(item["no_change"] for item in families.values()),
            "decisions": {
                decision: sum(item["decisions"].get(decision, 0) for item in families.values())
                for decision in ("produced", "no_change", "rejected", "failed")
            },
            "skills": sum(len(item["skills"]) for item in families.values()),
            "failed_neutral_lineage_skills": sum(
                item["failed_neutral_lineage_skills"] for item in families.values()
            ),
            "failed_lineage_skills_by_polarity": {
                polarity: sum(
                    item["failed_lineage_skills_by_polarity"][polarity]
                    for item in families.values()
                )
                for polarity in ("supporting", "neutral", "contradicting", "none")
            },
            "failed_lineage_traces_by_polarity": {
                polarity: sum(
                    item["failed_lineage_traces_by_polarity"][polarity]
                    for item in families.values()
                )
                for polarity in ("supporting", "neutral", "contradicting", "none")
            },
            "skills_without_citation": sum(
                item["skills_without_citation"] for item in families.values()
            ),
            "lineage_failed_traces": sum(
                item["lineage_failed_traces"] for item in families.values()
            ),
            "passed_traces_cited": sorted(
                {trace_id for item in families.values() for trace_id in item["passed_traces_cited"]}
            ),
        },
    }
    (output_dir / "funnel.json").write_text(json.dumps(funnel, indent=2, sort_keys=True) + "\n")
    lines = [
        "# Compile funnel",
        "",
        "| Family | Windows | Proposals | Admitted proposals | Unique admitted Skills | Rejected additions | produced | no_change | failed | Skills | Failed lineage skills by polarity | No citation | Passed traces cited |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for family, item in sorted(families.items()):
        lines.append(
            f"| {family} | {item['windows']} | {item['proposals']} | {item['admitted_additions']} | "
            f"{item['unique_admitted_skills']} | "
            f"{item['rejected_add_count']} | {item['decisions']['produced']} | "
            f"{item['decisions']['no_change']} | {item['decisions']['failed']} | "
            f"{len(item['skills'])} | "
            f"{json.dumps(item['failed_lineage_skills_by_polarity'], sort_keys=True)} | "
            f"{item['skills_without_citation']} | {len(item['passed_traces_cited'])} |"
        )
    lines.extend(["", "## Rejected additions", ""])
    for family, item in sorted(families.items()):
        if item["rejected_additions"]:
            lines.append(f"- `{family}`: {json.dumps(item['rejected_additions'], sort_keys=True)}")
    lines.extend(
        ["", "## Lineage-only failed Traces", "", "| Family | Failed traces |", "|---|---:|"]
    )
    lines.extend(
        f"| {family} | {item['lineage_failed_traces']} |"
        for family, item in sorted(families.items())
    )
    lines.extend(["", "## Excluded Skills", ""])
    lines.extend(f"- {json.dumps(skill, sort_keys=True)}" for skill in excluded_skills)
    (output_dir / "funnel.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return funnel


def _teacher_summary_path(output_dir: Path) -> Path:
    return output_dir.parent / "teacher.json"


def _write_teacher_export_error(output_dir: Path, agent_uuid: str, error: Exception) -> None:
    path = _teacher_summary_path(output_dir)
    summary = (
        json.loads(path.read_text(encoding="utf-8"))
        if path.exists()
        else {"stage": "teacher", "agent_uuid": agent_uuid}
    )
    failure: dict[str, Any] = {"type": type(error).__name__, "message": str(error)}
    if isinstance(error, SnapshotExportError):
        failure["failures"] = error.failures
    summary["snapshot_export_error"] = failure
    path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_teacher_export_result(
    output_dir: Path,
    agent_uuid: str,
    snapshot: dict[str, Any],
    funnel: dict[str, Any],
) -> None:
    path = _teacher_summary_path(output_dir)
    summary = (
        json.loads(path.read_text(encoding="utf-8"))
        if path.exists()
        else {"stage": "teacher", "agent_uuid": agent_uuid}
    )
    summary["agent_uuid"] = agent_uuid
    summary["snapshot_sha256"] = snapshot["sha256"]
    summary["funnel"] = funnel
    summary.pop("snapshot_export_error", None)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export frozen Agent Skill snapshots.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    export_parser = subparsers.add_parser("export", help="Export an existing Agent snapshot.")
    export_parser.add_argument("--agent-uuid", required=True)
    export_parser.add_argument("--family", required=True)
    export_parser.add_argument("--traces", type=Path, required=True)
    export_parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    from config import load_config, zep_client

    config = load_config(require_openai=False)
    client = zep_client(config.api_key, config.api_url)
    families = [family.strip() for family in args.family.split(",") if family.strip()]
    try:
        snapshot = compile_snapshot(
            client,
            args.agent_uuid,
            families,
            args.out,
            traces_path=args.traces,
        )
        funnel = write_funnel(args.out, families, snapshot, args.traces)
    except Exception as exc:
        _write_teacher_export_error(args.out, args.agent_uuid, exc)
        raise
    _write_teacher_export_result(args.out, args.agent_uuid, snapshot, funnel)
    print(
        json.dumps(
            {
                "agent_uuid": args.agent_uuid,
                "snapshot_sha256": snapshot["sha256"],
                "skill_names": [skill.get("name") for skill in snapshot["skills"]],
                "funnel": funnel,
                "snapshot_path": str(args.out / "snapshot.json"),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


def freeze_manifest(
    output_dir: Path,
    *,
    repository: Path,
    model: str,
    effort: str,
    splits: list[str],
    family: str,
    snapshot_sha: str,
) -> dict[str, Any]:
    status = subprocess.run(
        ["git", "-C", str(repository), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if status:
        raise RuntimeError("Refusing the first result row because the repository is dirty.")
    commit = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    manifest = {
        "git_commit": commit,
        "model": model,
        "effort": effort,
        "splits": splits,
        "family": family,
        "snapshot_sha256": snapshot_sha,
        "created_at": datetime.now(UTC).isoformat(),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest
