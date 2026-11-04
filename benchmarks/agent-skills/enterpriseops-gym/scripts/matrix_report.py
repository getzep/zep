"""Summarize the executor matrix cells.

For each model row and arm: the pass rate with a Wilson interval, the paired difference against ``none``
(by task and trial) with a 95% CI, the difference per task family, the ``read_skill`` call rate, the
latency and token columns, the failed calls by type, the spend, and the Langfuse reconciliation.

Usage: uv run --frozen python scripts/matrix_report.py --matrix results/matrix --out results/matrix/report.md
"""

from __future__ import annotations

import argparse
import base64
import collections
import glob
import json
import math
import os
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ARMS = ("none", "skills", "skills_decided")


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def paired_diff(a: dict[tuple, int], b: dict[tuple, int]) -> tuple[float, float, float, int]:
    keys = sorted(set(a) & set(b))
    diffs = [b[k] - a[k] for k in keys]
    n = len(diffs)
    if n == 0:
        return (0.0, 0.0, 0.0, 0)
    mean = sum(diffs) / n
    var = sum((d - mean) ** 2 for d in diffs) / max(1, n - 1)
    se = math.sqrt(var / n)
    return (mean, mean - 1.96 * se, mean + 1.96 * se, n)


def p90(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(0.9 * len(ordered)) - 1)]


def arm_of(key: tuple[str, str, str]) -> str:
    """The arm of a cell key; the label after `@` names the Skill snapshot of a skills cell."""
    return key[2].split("@", 1)[0]


def cell_label(matrix: Path, run_id: str, arm: str) -> str:
    """`none` for the baseline; `<arm>@<snapshot label>:<snapshot sha256 prefix>` for a skills cell, so that
    the runs of two snapshots or two harness versions never merge into one row. Runs with the same label and
    the same snapshot are the parts of one cell."""
    if arm == "none":
        return arm
    manifest = matrix / run_id / "manifest.executor.json"
    record = json.loads(manifest.read_text()) if manifest.exists() else {}
    label = record.get("snapshot_label") or run_id.split(f"-{arm}-", 1)[0]
    digest = str(record.get("skills_snapshot_sha256") or "")[:8] or "nohash"
    return f"{arm}@{label}:{digest}"


def _read_dropped_arms(matrix: Path) -> tuple[dict[str, set[str]], list[dict[str, Any]]]:
    arms_by_run: dict[str, set[str]] = collections.defaultdict(set)
    records_by_run_arm: dict[tuple[str, str], dict[str, Any]] = {}
    for dropped_path in sorted(glob.glob(str(matrix / "*" / "dropped_arms.jsonl"))):
        path = Path(dropped_path)
        run_id = path.parent.name
        manifest_path = path.with_name("manifest.executor.json")
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
        manifest_cells = {cell.get("arm"): cell for cell in manifest.get("cells", [])}
        rows_path = path.with_name("rows.jsonl")
        rows = (
            [json.loads(line) for line in rows_path.read_text().splitlines() if line.strip()]
            if rows_path.exists()
            else []
        )
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            arm = record["arm"]
            arms_by_run[run_id].add(arm)
            cell = manifest_cells.get(arm, {})
            row = next((item for item in rows if item.get("arm") == arm), {})
            status_counts = record.get("rows_kept_by_status") or {}
            model = cell.get("model") or row.get("model") or "unknown"
            effort = cell.get("effort")
            if effort is None:
                effort = row.get("effort", "unknown")
            records_by_run_arm[(run_id, arm)] = {
                "run_id": run_id,
                "model": model,
                "effort": str(effort),
                "arm": arm,
                "kept_valid_rows": int(status_counts.get("valid", 0)),
                "reason": record.get("reason", ""),
            }
    records = sorted(
        records_by_run_arm.values(),
        key=lambda record: (record["model"], record["effort"], record["arm"], record["run_id"]),
    )
    return dict(arms_by_run), records


def load_cells(matrix: Path) -> tuple[dict[tuple[str, str, str], list[dict]], dict[tuple[str, str, str], set[str]]]:
    """The valid rows and the run directories of each cell. The run directories of one cell must share one
    harness commit: two harness versions never merge into one cell, so the report raises on a mix."""
    cells: dict[tuple[str, str, str], list[dict]] = collections.defaultdict(list)
    runs: dict[tuple[str, str, str], set[str]] = collections.defaultdict(set)
    raw_cells: dict[tuple[str, str, str], list[tuple[str, dict]]] = collections.defaultdict(list)
    commits: dict[tuple[tuple[str, str, str], str], str] = {}
    dropped_by_run, _ = _read_dropped_arms(matrix)
    for path in sorted(glob.glob(str(matrix / "*" / "rows.jsonl"))):
        run_id = os.path.basename(os.path.dirname(path))
        if run_id.startswith("smoke-"):
            continue
        manifest = Path(path).with_name("manifest.executor.json")
        commit = str(json.loads(manifest.read_text()).get("git_commit", "")) if manifest.exists() else ""
        for line in Path(path).read_text().splitlines():
            row = json.loads(line)
            if row.get("status") != "valid":
                continue
            key = (row["model"], str(row.get("effort")), cell_label(matrix, run_id, row["arm"]))
            raw_cells[key].append((run_id, row))
            runs[key].add(run_id)
            commits[(key, run_id)] = commit
    for key, contributions in raw_cells.items():
        dropped_runs = {run_id for run_id in runs[key] if arm_of(key) in dropped_by_run.get(run_id, set())}
        retained = [(run_id, row) for run_id, row in contributions if run_id not in dropped_runs]
        if not retained:
            del runs[key]
            continue
        cells[key] = [row for _, row in retained]
        runs[key] = {run_id for run_id, _ in retained}
        by_run = {run_id: commits[(key, run_id)] for run_id in runs[key]}
        if len(set(by_run.values())) > 1:
            raise ValueError(f"The cell {key} holds runs of more than one harness commit: {sorted(by_run.items())}")
    return cells, runs


def spend(matrix: Path) -> tuple[dict[tuple[str, str, str], float], dict[tuple[str, str, str], int]]:
    usd: dict[tuple[str, str, str], float] = collections.defaultdict(float)
    count: dict[tuple[str, str, str], int] = collections.defaultdict(int)
    dropped_by_run, _ = _read_dropped_arms(matrix)
    for path in glob.glob(str(matrix / "*" / "costs.executor.*.jsonl")):
        run_id = os.path.basename(os.path.dirname(path))
        if run_id.startswith("smoke-"):
            continue
        for line in Path(path).read_text().splitlines():
            r = json.loads(line)
            if not r.get("model") or not r.get("arm"):
                continue
            if r["arm"] in dropped_by_run.get(run_id, set()):
                continue
            key = (r["model"], str(r.get("request_effort") or r.get("effort")), cell_label(matrix, run_id, r["arm"]))
            usd[key] += float(r.get("usd") or 0)
            count[key] += 1
    return usd, count


def langfuse_trace_count(run_id: str, arm: str) -> int | None:
    host = os.environ.get("LANGFUSE_HOST") or os.environ.get("LANGFUSE_BASE_URL")
    pk, sk = os.environ.get("LANGFUSE_PUBLIC_KEY"), os.environ.get("LANGFUSE_SECRET_KEY")
    if not (host and pk and sk):
        return None
    query = urllib.parse.urlencode([("tags", f"run_id:{run_id}"), ("tags", f"arm:{arm}"), ("limit", "1")])
    request = urllib.request.Request(
        f"{host}/api/public/traces?{query}",
        headers={"Authorization": "Basic " + base64.b64encode(f"{pk}:{sk}".encode()).decode()},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return int(json.load(response).get("meta", {}).get("totalItems", 0))
    except Exception:
        return None


def snapshot_hashes(matrix: Path, run_ids: set[str]) -> set[str | None]:
    """The `skills_snapshot_sha256` of each run directory's executor manifest."""
    hashes: set[str | None] = set()
    for run_id in run_ids:
        manifest = matrix / run_id / "manifest.executor.json"
        record = json.loads(manifest.read_text()) if manifest.exists() else {}
        hashes.add(record.get("skills_snapshot_sha256"))
    return hashes


def skills_cells(cells: dict[tuple[str, str, str], list[dict]], model: str, effort: str) -> list[str]:
    """The arm labels of the skills cells of one model and effort, in sorted order."""
    return sorted(k[2] for k in cells if k[0] == model and k[1] == effort and k[2] != "none")


def pass_map(rows: list[dict]) -> dict[tuple[str, int], int]:
    return {(r["task_id"], r["trial"]): int((r.get("reward") or 0) >= 1) for r in rows}


_FAMILIES: dict[str, str] = {}


def family_of(row: dict) -> str:
    if not _FAMILIES:
        from eog.run import _task_families, load_tasks

        for task in load_tasks():
            families = sorted(_task_families(task))
            _FAMILIES[task.task_id] = families[0] if len(families) == 1 else "eog.hybrid"
    return _FAMILIES.get(row["task_id"], "unknown")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", type=Path, default=Path("results/matrix"))
    parser.add_argument("--out", type=Path, default=Path("results/matrix/report.md"))
    parser.add_argument("--no-langfuse", action="store_true")
    parser.add_argument(
        "--coverage",
        type=Path,
        default=Path("results/matrix/coverage.json"),
        help="Coverage flags from scripts/coverage_flags.py; the coverage section is skipped when the file is absent",
    )
    args = parser.parse_args()
    coverage_file = json.loads(args.coverage.read_text()) if args.coverage.exists() else {}
    coverage = coverage_file.get("tasks", {})
    coverage_sha256 = coverage_file.get("skills_snapshot_sha256")
    cells, runs = load_cells(args.matrix)
    _, dropped_arms = _read_dropped_arms(args.matrix)
    usd, cost_rows = spend(args.matrix)

    lines = [
        "# Executor matrix report",
        "",
        "Each cell is 152 score tasks x 3 trials = 456 rows (valid rows only). The paired difference compares the rows",
        "of two arms with the same task and trial; the CI is a normal interval of the mean paired difference.",
        "",
        "## Cells",
        "",
        "A skills arm carries the label of its Skill snapshot after `@` (the manifest `snapshot_label`, or the run",
        "directory prefix) and the first 8 characters of the snapshot sha256, so that cells on two snapshots or two",
        "harness versions stay separate rows.",
        "",
        "| Model | Effort | Arm | Rows (n) | Passed | Pass rate | 95% CI | read_skill rows | read_skill calls per row | Latency mean s | Latency p90 s | Prompt tokens per row | Output tokens per row (reasoning) | Failed calls by type | Spend (USD) |",  # noqa: E501
        "|---|---|---|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---|---:|",
    ]
    summary: dict[str, Any] = {}
    for key in sorted(cells):
        rows = cells[key]
        n = len(rows)
        k = sum(1 for r in rows if (r.get("reward") or 0) >= 1)
        lo, hi = wilson(k, n)
        fails: collections.Counter = collections.Counter()
        for r in rows:
            for f in r.get("attempt_failures") or []:
                fails[f.get("error_class", "unknown") if isinstance(f, dict) else str(f)] += 1
        latencies = [float(r.get("latency_seconds") or 0) for r in rows]
        prompt = [
            sum(float((r.get("usage") or {}).get(x) or 0) for x in ("input", "cache_read", "cache_write")) for r in rows
        ]
        output = [float((r.get("usage") or {}).get("output") or 0) for r in rows]
        reasoning = [float((r.get("usage") or {}).get("reasoning") or 0) for r in rows]
        rs_rows = sum(1 for r in rows if r.get("read_skill_calls"))
        rs_calls = sum(len(r.get("read_skill_calls") or []) for r in rows)
        record = {
            "rows": n,
            "passed": k,
            "pass_rate": k / n,
            "ci95": [lo, hi],
            "read_skill_rows": rs_rows,
            "read_skill_calls": rs_calls,
            "latency_mean_s": sum(latencies) / n,
            "latency_p90_s": p90(latencies),
            "prompt_tokens_per_row": sum(prompt) / n,
            "output_tokens_per_row": sum(output) / n,
            "reasoning_tokens_per_row": sum(reasoning) / n,
            "failed_calls": dict(fails),
            "model_call_attempts": sum(int(r.get("model_call_attempts") or 0) for r in rows),
            "usd": usd.get(key, 0.0),
            "runs": sorted(runs[key]),
        }
        summary["/".join(key)] = record
        rs_cell = f"{rs_rows} ({rs_rows / n:.0%})" if arm_of(key) == "skills" else ""
        rs_per_row = f"{rs_calls / n:.2f}" if arm_of(key) == "skills" else ""
        lines.append(
            f"| {key[0]} | {key[1]} | {key[2]} | {n} | {k} | {k / n:.1%} | [{lo:.1%}, {hi:.1%}] | {rs_cell} | {rs_per_row} | "  # noqa: E501
            f"{record['latency_mean_s']:.0f} | {record['latency_p90_s']:.0f} | {record['prompt_tokens_per_row']:,.0f} | "  # noqa: E501
            f"{record['output_tokens_per_row']:,.0f} ({record['reasoning_tokens_per_row']:,.0f}) | "
            f"{sum(fails.values())} {dict(fails) if fails else ''} | {usd.get(key, 0.0):.2f} |"
        )

    if dropped_arms:
        lines += [
            "",
            "## Dropped arms",
            "",
            "| Model | Effort | Arm | Kept valid rows | Reason |",
            "|---|---|---|---:|---|",
        ]
        for record in dropped_arms:
            reason = str(record["reason"]).replace("|", "\\|").replace("\n", " ").replace("\r", " ")
            lines.append(
                f"| {record['model']} | {record['effort']} | {record['arm']} | {record['kept_valid_rows']} | {reason} |"
            )

    lines += [
        "",
        "## Paired differences against `none` (points of pass rate)",
        "",
        "| Model | Effort | Arm | Arm rows (n) | none rows (n) | Pairs (n) | Difference | 95% CI |",
        "|---|---|---|---:|---:|---:|---:|---|",
    ]
    diffs: dict[str, Any] = {}
    family_lines = [
        "",
        "## Paired differences against `none` per task family (points)",
        "",
        "| Model | Effort | Arm | Family | Pairs (n) | Difference | 95% CI |",
        "|---|---|---|---|---:|---:|---|",
    ]
    for model, effort in sorted({(k[0], k[1]) for k in cells}):
        base = cells.get((model, effort, "none"), [])
        if not base:
            continue
        base_map = pass_map(base)
        for arm in skills_cells(cells, model, effort):
            rows = cells[(model, effort, arm)]
            mean, lo, hi, pairs = paired_diff(base_map, pass_map(rows))
            diffs[f"{model}/{effort}/{arm}-minus-none"] = {
                "diff_points": mean * 100,
                "ci95": [lo * 100, hi * 100],
                "pairs": pairs,
                "arm_rows": len(rows),
                "none_rows": len(base),
            }
            lines.append(
                f"| {model} | {effort} | {arm} | {len(rows)} | {len(base)} | {pairs} | {mean * 100:+.1f} | "
                f"[{lo * 100:+.1f}, {hi * 100:+.1f}] |"
            )
            families = sorted({family_of(r) for r in rows})
            per_family = {}
            for family in families:
                fb = pass_map([r for r in base if family_of(r) == family])
                fa = pass_map([r for r in rows if family_of(r) == family])
                fm, flo, fhi, fn = paired_diff(fb, fa)
                per_family[family] = {"diff_points": fm * 100, "ci95": [flo * 100, fhi * 100], "pairs": fn}
                family_lines.append(
                    f"| {model} | {effort} | {arm} | {family} | {fn} | {fm * 100:+.1f} | [{flo * 100:+.1f}, {fhi * 100:+.1f}] |"  # noqa: E501
                )
            diffs[f"{model}/{effort}/{arm}-minus-none"]["per_family"] = per_family
    lines += family_lines

    if coverage:
        lines += [
            "",
            "## Paired differences against `none` by coverage (points)",
            "",
            "A task is covered (R2) when a Skill of its index families has every tool of its `## Tools` section in the",
            "task's `selected_tools`; covered_half (R3) needs at least half of the Skill's tools there. The flag reads",
            "the tool set only; it does not judge whether the skill fits the task. The section covers only the",
            f"cells whose manifest `skills_snapshot_sha256` is the coverage snapshot (`{coverage_sha256}`);",
            "other cells are listed as skipped.",
            "",
            "| Model | Effort | Arm | Flag | Covered pairs (n) | Difference | 95% CI | Uncovered pairs (n) | Difference | 95% CI |",  # noqa: E501
            "|---|---|---|---|---:|---:|---|---:|---:|---|",
        ]
        for model, effort in sorted({(k[0], k[1]) for k in cells}):
            base = cells.get((model, effort, "none"), [])
            if not base:
                continue
            for arm in skills_cells(cells, model, effort):
                rows = cells[(model, effort, arm)]
                cell_snapshots = snapshot_hashes(args.matrix, runs[(model, effort, arm)])
                if cell_snapshots != {coverage_sha256}:
                    lines.append(
                        f"| {model} | {effort} | {arm} | skipped: cell snapshot "
                        f"{sorted(cell_snapshots, key=str)} is not the coverage snapshot | | | | | | |"
                    )
                    diffs[f"{model}/{effort}/{arm}-minus-none"]["by_coverage"] = {
                        "skipped": "snapshot mismatch",
                        "cell_snapshots": sorted(cell_snapshots, key=str),
                    }
                    continue
                by_flag = {}
                for flag in ("covered", "covered_half"):
                    split = {}
                    for state in (True, False):
                        ids = {t for t, rec in coverage.items() if bool(rec.get(flag)) is state}
                        bm = pass_map([r for r in base if r["task_id"] in ids])
                        am = pass_map([r for r in rows if r["task_id"] in ids])
                        mean, lo, hi, n = paired_diff(bm, am)
                        split[state] = {"diff_points": mean * 100, "ci95": [lo * 100, hi * 100], "pairs": n}
                    by_flag[flag] = split
                    c, u = split[True], split[False]
                    lines.append(
                        f"| {model} | {effort} | {arm} | {flag} | {c['pairs']} | {c['diff_points']:+.1f} | "
                        f"[{c['ci95'][0]:+.1f}, {c['ci95'][1]:+.1f}] | {u['pairs']} | {u['diff_points']:+.1f} | "
                        f"[{u['ci95'][0]:+.1f}, {u['ci95'][1]:+.1f}] |"
                    )
                diffs[f"{model}/{effort}/{arm}-minus-none"]["by_coverage"] = by_flag

    lines += [
        "",
        "## Langfuse reconciliation",
        "",
        "| Model | Effort | Arm | Rows (n) | Rows with a trace ID | Gateway call attempts (rows) | Langfuse generations (rows) | Cost rows | Langfuse traces (server, by tags) |",  # noqa: E501
        "|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    reconciliation: dict[str, Any] = {}
    for key in sorted(cells):
        rows = cells[key]
        attempts = sum(int(r.get("model_call_attempts") or 0) for r in rows)
        generations = sum(int(r.get("langfuse_generations_all_attempts") or 0) for r in rows)
        with_trace = sum(1 for r in rows if r.get("langfuse_trace_id"))
        server = None
        if not args.no_langfuse:
            server = 0
            for run_id in sorted(runs[key]):
                count = langfuse_trace_count(run_id, arm_of(key))
                server = None if count is None or server is None else server + count
        reconciliation["/".join(key)] = {
            "rows": len(rows),
            "rows_with_trace_id": with_trace,
            "model_call_attempts": attempts,
            "langfuse_generations_all_attempts": generations,
            "cost_rows": cost_rows.get(key, 0),
            "langfuse_traces_server": server,
        }
        lines.append(
            f"| {key[0]} | {key[1]} | {key[2]} | {len(rows)} | {with_trace} | {attempts} | {generations} | "
            f"{cost_rows.get(key, 0)} | {'n/a' if server is None else server} |"
        )

    args.out.write_text("\n".join(lines) + "\n")
    args.out.with_suffix(".json").write_text(
        json.dumps({"cells": summary, "differences": diffs, "reconciliation": reconciliation}, indent=2) + "\n"
    )
    print("\n".join(lines))


if __name__ == "__main__":
    main()
