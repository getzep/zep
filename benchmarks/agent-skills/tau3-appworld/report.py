from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

REPORT_STAGES = ("teacher", "student-skills", "student-plain", "heldout-none", "heldout-skills")


def _latest_attempts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[Any, dict[str, Any]] = {}
    for index, row in enumerate(rows):
        key = (
            (row["domain"], row["task_index"], row["trial"])
            if {"domain", "task_index", "trial"} <= row.keys()
            else index
        )
        latest[key] = row
    return list(latest.values())


def _rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for domain, data in summary.get("domains", {}).items():
        rows.append({"stage": summary.get("stage"), "domain": domain, **data})
    if not rows:
        rows.append({"stage": summary.get("stage"), "domain": "overall", **summary})
    return rows


def _cell(row: dict[str, Any], key: str) -> str:
    value = row.get(key)
    if value is None:
        return "N/A"
    return f"{value:.3f}" if isinstance(value, float) else str(value)


def _successful_mean(rows: list[dict[str, Any]], field: str) -> str:
    successful = [row for row in rows if row.get("reward") == 1.0]
    if not successful:
        return "N/A"
    if field == "tokens":
        values = [
            int(row.get("prompt_tokens", 0) or 0) + int(row.get("completion_tokens", 0) or 0)
            for row in successful
        ]
    else:
        values = [int(row.get("steps", 0) or 0) for row in successful]
    return f"{statistics.fmean(values):.1f}"


def latency_by_outcome(rows: list[dict[str, Any]]) -> dict[str, Any]:
    outcomes = {}
    for outcome, passed in (("passed", True), ("failed", False)):
        selected = [row for row in rows if (row.get("reward") == 1.0) == passed]
        metrics: dict[str, Any] = {"episode_count": len(selected)}
        for field in ("model_s", "duration_s"):
            values = [float(row[field]) for row in selected if row.get(field) is not None]
            percentiles = (
                statistics.quantiles(values, n=100, method="inclusive") if len(values) > 1 else None
            )
            metrics[field] = {
                "count": len(values),
                "p50": statistics.median(values) if values else None,
                "mean": statistics.fmean(values) if values else None,
                "p95": percentiles[94] if percentiles else values[0] if values else None,
            }
        outcomes[outcome] = metrics
    return outcomes


def _print_latencies(groups: dict[tuple[str, str], list[dict[str, Any]]]) -> None:
    print("\n## Latency by model, arm, and outcome")
    print("The percentiles use linear interpolation. Missing timings do not count as zero.")
    print(
        "| Model | Arm | Outcome | Episodes | Model n | Model p50 (s) | Model mean (s) | Model p95 (s) | Duration n | Duration p50 (s) | Duration mean (s) | Duration p95 (s) |"
    )
    print("|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for (model, arm), rows in sorted(groups.items()):
        for outcome, metrics in latency_by_outcome(rows).items():
            cells = [str(metrics["episode_count"])]
            for field in ("model_s", "duration_s"):
                cells.extend(_cell(metrics[field], key) for key in ("count", "p50", "mean", "p95"))
            print(f"| {model} | {arm} | {outcome} | " + " | ".join(cells) + " |")


def retrieval_after_skill(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"audited_episodes": 0, "episodes_with_read": 0, "shell": 0, "kb_search": 0, "none": 0}
    for row in rows:
        if "tool_call_sequence" not in row:
            continue
        counts["audited_episodes"] += 1
        calls = row["tool_call_sequence"]
        first_read = next(
            (call["tool_call_index"] for call in calls if call.get("tool") == "read_skill"),
            None,
        )
        if first_read is None:
            continue
        counts["episodes_with_read"] += 1
        retrieval = next(
            (
                call["tool"]
                for call in calls
                if call["tool_call_index"] > first_read
                and call.get("requestor") == "assistant"
                and (call["tool"] == "shell" or call["tool"].startswith("KB_search_"))
            ),
            None,
        )
        counts[
            "none" if retrieval is None else "shell" if retrieval == "shell" else "kb_search"
        ] += 1
    denominator = counts["episodes_with_read"]
    return {
        **counts,
        "shell_share": counts["shell"] / denominator if denominator else None,
        "kb_search_share": counts["kb_search"] / denominator if denominator else None,
    }


def _print_retrieval_after_skill(groups: dict[tuple[str, str], list[dict[str, Any]]]) -> None:
    print("\n## First retrieval after a Skill read")
    print(
        "Shares use episodes with a read_skill call, including missing Skills and none selections. "
        "Each episode counts once, after its first read. "
        "Episodes without a later retrieval remain in the denominator. "
        "Old episodes without a tool-call audit are excluded."
    )
    print(
        "| Model | Arm | Audited episodes | Episodes with read | Shell | KB_search_* | No later retrieval | Shell share | KB share |"
    )
    print("|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for (model, arm), rows in sorted(groups.items()):
        stats = retrieval_after_skill(rows)
        cells = [
            _cell(stats, key)
            for key in (
                "audited_episodes",
                "episodes_with_read",
                "shell",
                "kb_search",
                "none",
                "shell_share",
                "kb_search_share",
            )
        ]
        print(f"| {model} | {arm} | " + " | ".join(cells) + " |")


def _print_combined(directories: list[Path]) -> None:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    print("## Combined configurations")
    print(
        "| Config | Arm | Episodes | Passed | Errors | Prompt tokens | Skill reads | Mean duration (s) |"
    )
    print("|---|---|---:|---:|---:|---:|---:|---:|")
    for directory in directories:
        for stage in REPORT_STAGES:
            path = directory / f"{stage}.jsonl"
            if not path.exists():
                continue
            summary_path = directory / f"{stage}.json"
            summary = json.loads(summary_path.read_text()) if summary_path.exists() else {}
            settings = summary.get("conditions", {})
            rows = _latest_attempts(
                [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
            )
            model = settings.get("agent_model", "unknown")
            effort = settings.get("reasoning_effort")
            config = f"{model} {effort}" if effort else model
            arm = settings.get("arm") or stage
            groups[(config, arm)].extend(rows)
            durations = [
                float(row["duration_s"]) for row in rows if row.get("duration_s") is not None
            ]
            mean = f"{statistics.fmean(durations):.3f}" if durations else "N/A"
            errors = sum(
                bool(row.get("error") or row.get("recording_error") or row.get("empty_response"))
                for row in rows
            )
            print(
                f"| {config} | {arm} | {len(rows)} | "
                f"{sum(row.get('reward') == 1.0 for row in rows)} | {errors} | "
                f"{sum(int(row.get('prompt_tokens') or 0) for row in rows)} | "
                f"{sum(len(row.get('skill_read_calls') or []) for row in rows)} | {mean} |"
            )
    if groups:
        _print_latencies(groups)
        _print_retrieval_after_skill(groups)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python report.py results/<run-id>/")
    directory = Path(sys.argv[1])
    if not any((directory / f"{stage}.json").exists() for stage in REPORT_STAGES):
        directories = sorted(
            child
            for child in directory.iterdir()
            if child.is_dir()
            and any((child / f"{stage}.jsonl").exists() for stage in REPORT_STAGES)
        )
        if directories:
            _print_combined(directories)
            return
    summaries = []
    for stage in REPORT_STAGES:
        path = directory / f"{stage}.json"
        if path.exists():
            summaries.append(json.loads(path.read_text()))
    if summaries:
        print("## Experimental conditions")
        print(json.dumps(summaries[0].get("conditions", {}), indent=2, sort_keys=True))
    trials = max(
        (int(summary.get("conditions", {}).get("trials", 1)) for summary in summaries),
        default=1,
    )
    pass_columns = " | ".join(f"Pass^{k}" for k in range(1, trials + 1))
    print(
        "| Stage | Domain | Episode count | "
        f"{pass_columns} | Mean steps | Mean prompt tokens | Mean completion tokens | "
        "Mean reasoning tokens | Error count |"
    )
    print("|---|---|---:|" + "---:|" * trials + "---:|---:|---:|---:|---:|")
    for summary in summaries:
        for row in _rows(summary):
            pass_values = " | ".join(
                f"{float(row.get(f'pass^{k}', 0.0)):.3f}" for k in range(1, trials + 1)
            )
            print(
                f"| {row.get('stage', '')} | {row.get('domain', '')} | "
                f"{row.get('episode_count', 0)} | {pass_values} | "
                f"{row.get('mean_steps', 0):.1f} | {row.get('mean_prompt_tokens', 0):.1f} | "
                f"{row.get('mean_completion_tokens', 0):.1f} | "
                f"{_cell(row, 'mean_reasoning_tokens')} | {row.get('error_count', 0)} |"
            )
    print("\n## Success and efficiency")
    print(
        "| Stage | Domain | Successes | Total tokens | Tokens per success | "
        "Turns per success | Mean successful tokens | Mean successful turns | "
        "Cached tokens | Cost (USD) | Retrieval rate | Actual use |"
    )
    print("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    for summary in summaries:
        for row in _rows(summary):
            print(
                f"| {row.get('stage')} | {row.get('domain')} | {_cell(row, 'success_count')} | "
                f"{_cell(row, 'total_tokens')} | {_cell(row, 'tokens_per_success')} | "
                f"{_cell(row, 'steps_per_success')} | {_cell(row, 'mean_successful_tokens')} | "
                f"{_cell(row, 'mean_successful_steps')} | {_cell(row, 'mean_cached_tokens')} | "
                f"{_cell(row, 'cost_usd')} | {_cell(row, 'skill_retrieval_rate')} | "
                f"{_cell(row, 'actual_skill_use_rate')} |"
            )
    latency_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    conditions = {summary.get("stage"): summary.get("conditions", {}) for summary in summaries}
    for stage in REPORT_STAGES:
        path = directory / f"{stage}.jsonl"
        if not path.exists():
            continue
        settings = conditions.get(stage, {})
        for line in path.read_text().splitlines():
            row = json.loads(line)
            model = row.get("agent_model") or settings.get("agent_model", "unknown")
            arm = row.get("arm") or settings.get("arm") or stage
            latency_groups[(model, arm)].append(row)
    if latency_groups:
        _print_latencies(latency_groups)
        _print_retrieval_after_skill(latency_groups)
    paired: dict[tuple[str, str], dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for stage in ("student-skills", "student-plain"):
        path = directory / f"{stage}.jsonl"
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            row = json.loads(line)
            paired[(str(row["domain"]), str(row["task_id"]))][stage].append(row)
    if paired:
        print("\n## Paired student tasks")
        print(
            "| Domain | Task ID | Luna with Skills | Luna alone | Success difference | "
            "Skilled success tokens | Plain success tokens | Skilled success turns | "
            "Plain success turns |"
        )
        print("|---|---|---:|---:|---:|---:|---:|---:|---:|")
        for (domain, task_id), stages in sorted(paired.items()):
            skilled = stages.get("student-skills", [])
            plain = stages.get("student-plain", [])
            skilled_success = sum(row.get("reward") == 1.0 for row in skilled)
            plain_success = sum(row.get("reward") == 1.0 for row in plain)
            difference = (
                f"{skilled_success / len(skilled) - plain_success / len(plain):+.3f}"
                if skilled and plain
                else "N/A"
            )
            print(
                f"| {domain} | {task_id} | {skilled_success}/{len(skilled)} | "
                f"{plain_success}/{len(plain)} | {difference} | "
                f"{_successful_mean(skilled, 'tokens')} | {_successful_mean(plain, 'tokens')} | "
                f"{_successful_mean(skilled, 'steps')} | {_successful_mean(plain, 'steps')} |"
            )
    compile_path = directory / "compile.json"
    if compile_path.exists():
        compile_result = json.loads(compile_path.read_text())
        print(f"\n## Skill readiness: {'passed' if compile_result.get('ready') else 'failed'}")
        print(f"- Gate failures: {compile_result.get('failures', [])}")
        for family, data in compile_result.get("families", {}).items():
            print(f"\n### {family}")
            for key in (
                "compilation",
                "candidate_count",
                "candidate_reviews",
                "admitted_count",
                "audit",
                "attached",
                "failures",
            ):
                print(f"- {key.replace('_', ' ').capitalize()}: {data.get(key)}")


if __name__ == "__main__":
    main()
