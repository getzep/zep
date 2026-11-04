from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REJECTION_CLASSES = ("curator_rejected", "scanner_rejected")
COUNT_FIELDS = (
    "windows",
    "window_uuids",
    "proposals",
    "add_proposals",
    "rejected_adds",
    "no_change",
    "skills",
    "passed_traces",
    "passed_traces_cited",
    "passed_traces_uncited",
    "traces_cited",
)
UNKNOWN_WITHOUT_TRACES = ("passed_traces_cited", "passed_traces_uncited")


def _report_path(output_dir: Path, family: str) -> Path:
    return output_dir / family.replace(".", "_") / "compile_report.json"


def _trace_rows(traces_path: Path | None) -> list[dict[str, Any]]:
    """Read a Trace ledger (traces.jsonl) or an exported replay file (lines with trajectory, events, run_row)."""
    if traces_path is None or not traces_path.exists():
        return []
    rows = []
    for line in traces_path.read_text().splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if "trajectory" in record:
            trajectory = record["trajectory"]
            run_row = record.get("run_row") or {}
            record = {
                "trace_uuid": trajectory.get("uuid"),
                "task_family": trajectory.get("task_family") or run_row.get("task_family"),
                "passed": run_row.get("passed", trajectory.get("outcome") == "succeeded"),
                **{key: value for key, value in run_row.items() if key not in {"trace_uuid", "task_family", "passed"}},
            }
        rows.append(record)
    return rows


def _cited_traces(lineage: Any) -> set[str]:
    cited: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"trace_uuid", "trajectory_id"} and isinstance(item, str):
                    cited.add(item)
                else:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(lineage.get("evidence", []))
    return cited


def family_funnel(
    report: dict[str, Any],
    snapshot_skills: list[dict[str, Any]],
    passed_trace_ids: set[str],
    *,
    traces_known: bool = True,
) -> dict[str, Any]:
    outcomes = [outcome for outcome in report.get("compile_outcomes", []) if isinstance(outcome, dict)]
    rejections = {name: 0 for name in REJECTION_CLASSES}
    rejections["other"] = 0
    proposals = 0
    add_proposals = 0
    rejected_adds = 0
    no_change = 0
    for outcome in outcomes:
        count = int(outcome.get("proposal_count") or 0)
        rejected = int(outcome.get("rejected_add_count") or 0)
        proposals += count
        rejected_adds += rejected
        if outcome.get("decision") == "no_change":
            no_change += 1
        elif outcome.get("decision") == "produced":
            add_proposals += max(count - rejected, 0)
        if outcome.get("decision") == "rejected" or outcome.get("rejection_class"):
            rejection_class = str(outcome.get("rejection_class") or "")
            rejections[rejection_class if rejection_class in REJECTION_CLASSES else "other"] += 1
    cited: set[str] = set()
    tools_per_skill = {}
    date_literals = {}
    for skill in snapshot_skills:
        lineage = skill.get("lineage") or {}
        cited.update(_cited_traces(lineage))
        skill_record = lineage.get("skill") or {}
        tools = skill_record.get("tools")
        if tools is None:
            tools = (skill_record.get("applicability") or {}).get("tools") or []
        tools_per_skill[str(skill["skill_id"])] = sorted(
            str(tool.get("name") if isinstance(tool, dict) else tool) for tool in tools
        )
        date_literals[str(skill["skill_id"])] = list(skill.get("date_literals") or [])
    return {
        "task_family": report.get("task_family"),
        "windows": len(outcomes),
        "window_uuids": len(
            {uuid for uuid in (outcome.get("compilation_window_uuid") for outcome in outcomes) if uuid}
        ),
        "proposals": proposals,
        "add_proposals": add_proposals,
        "rejected_adds": rejected_adds,
        "no_change": no_change,
        "rejections": rejections,
        "skills": len(snapshot_skills),
        "passed_traces": len(passed_trace_ids) if traces_known else int(report.get("passed_traces") or 0),
        "passed_traces_cited": len(passed_trace_ids & cited) if traces_known else None,
        "passed_traces_uncited": len(passed_trace_ids - cited) if traces_known else None,
        "traces_cited": len(cited),
        "tools_per_skill": tools_per_skill,
        "date_literals": date_literals,
    }


def compile_funnel(
    output_dir: Path,
    task_families: list[str],
    *,
    traces_path: Path | None = None,
) -> dict[str, Any]:
    """Count the compile funnel of each family from the compile reports, the snapshot, and the Trace ledger."""
    snapshot_path = output_dir / "snapshot.json"
    if not snapshot_path.exists():
        snapshot_path = output_dir / "skills.json"
    snapshot_skills = json.loads(snapshot_path.read_text())["skills"] if snapshot_path.exists() else []
    trace_rows = _trace_rows(traces_path)
    families = {}
    for family in task_families:
        report_path = _report_path(output_dir, family)
        report = json.loads(report_path.read_text()) if report_path.exists() else {"task_family": family}
        passed = {
            str(row["trace_uuid"])
            for row in trace_rows
            if row.get("passed") and row.get("task_family", f"eog.{row.get('domain', '')}") == family
        }
        family_skills = [skill for skill in snapshot_skills if skill.get("task_family") == family]
        families[family] = family_funnel(report, family_skills, passed, traces_known=bool(trace_rows))
    total: dict[str, Any] = {
        name: (
            sum(item[name] for item in families.values()) if trace_rows or name not in UNKNOWN_WITHOUT_TRACES else None
        )
        for name in COUNT_FIELDS
    }
    total["rejections"] = {
        name: sum(item["rejections"][name] for item in families.values()) for name in (*REJECTION_CLASSES, "other")
    }
    total["tools_per_skill"] = {
        key: value for item in families.values() for key, value in item["tools_per_skill"].items()
    }
    total["date_literals"] = {key: value for item in families.values() for key, value in item["date_literals"].items()}
    total["skills_with_tools"] = sum(bool(tools) for tools in total["tools_per_skill"].values())
    total["skills_with_date_literals"] = sum(bool(dates) for dates in total["date_literals"].values())
    return {
        "snapshot_present": snapshot_path.exists(),
        "traces_file": str(traces_path) if traces_path else None,
        "families": families,
        "total": total,
    }


def funnel_markdown(funnel: dict[str, Any]) -> str:
    header = (
        "| Family | Windows (outcomes) | Window UUIDs | Proposals | Admitted adds | Rejected adds | no_change | "
        "Curator-rejected outcomes | Scanner-rejected outcomes | Other rejected outcomes | Skills | Passed traces | "
        "Passed cited | Passed not cited | Skills with tools | Skills with date literals |"
    )
    lines = [
        "# Compile funnel",
        "",
        header,
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    rows = [*sorted(funnel["families"].items()), ("total", funnel["total"])]
    for label, item in rows:
        rejections = item["rejections"]
        lines.append(
            f"| {label} | {item['windows']} | {item['window_uuids']} | {item['proposals']} | {item['add_proposals']} | "
            f"{item['rejected_adds']} | {item['no_change']} | {rejections['curator_rejected']} | "
            f"{rejections['scanner_rejected']} | "
            f"{rejections['other']} | {item['skills']} | {item['passed_traces']} | {item['passed_traces_cited']} | "
            f"{item['passed_traces_uncited']} | {sum(bool(tools) for tools in item['tools_per_skill'].values())} | "
            f"{sum(bool(dates) for dates in item['date_literals'].values())} |"
        )
    lines.extend(["", "## Skills", "", "| Skill | Family | Tools | Date literals |", "|---|---|---|---|"])
    for family, item in sorted(funnel["families"].items()):
        for skill_id, tools in item["tools_per_skill"].items():
            dates = item["date_literals"].get(skill_id, [])
            lines.append(f"| `{skill_id}` | {family} | {', '.join(tools) or 'none'} | {', '.join(dates) or 'none'} |")
    return "\n".join(lines) + "\n"


def write_compile_funnel(
    output_dir: Path,
    task_families: list[str],
    *,
    traces_path: Path | None = None,
) -> dict[str, Any]:
    funnel = compile_funnel(output_dir, task_families, traces_path=traces_path)
    (output_dir / "funnel.json").write_text(json.dumps(funnel, indent=2, sort_keys=True) + "\n")
    (output_dir / "funnel.md").write_text(funnel_markdown(funnel))
    return funnel
