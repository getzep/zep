from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import random
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

OLD = "9c6f78464234987bb4ab12d36efbceb4f569e96a"
NEW = "dbe3b4ab6638dde5a9d4757482b477c8af30afa6"
LIFECYCLE = "8bbfbb9659b0eb55da56296d588b753659aaeb38"
SOURCES = (OLD, NEW, LIFECYCLE)
SNAPSHOT_HASH = "8ae9e45031726d3fea93d4533ad2243913b561caee90b081f6d1a68c8484324a"
SEED = 300
REPLICATES = 100_000
POOLED_REPLICATES = 10_000
ORDER = {"luna": 0, "haiku": 1, "deepseek": 2}
EFFORT_ORDER = {"low": 0, "medium": 1, "max": 2}


def load_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def teacher_file(root, explicit=None):
    if explicit is not None:
        return explicit
    matches = sorted((root.parent / "results").glob("*/teacher.jsonl"))
    if len(matches) != 1:
        raise ValueError(
            "Specify --teacher when the results directory has no unique teacher.jsonl."
        )
    return matches[0]


def key(row):
    return (row["domain"], int(row["task_index"]), int(row["trial"]))


def passed(row):
    return bool(row["succeeded"]) and not (row.get("error") or row.get("empty_response"))


def validate_rows(rows, expected):
    counts = Counter(key(row) for row in rows)
    if set(counts) != expected or any(count != 1 for count in counts.values()):
        raise ValueError("The episode keys are incomplete, unexpected, or duplicated.")
    for row in rows:
        if row.get("git_sha") not in SOURCES:
            raise ValueError("An episode has an unknown source commit.")
        if bool(row["succeeded"]) != (float(row["reward"]) == 1):
            raise ValueError("The saved success flag and reward disagree.")
        if (row.get("error") or row.get("empty_response")) and row["succeeded"]:
            raise ValueError("An error episode has a saved success flag.")


def bootstrap_gain(no_skills, skills, seed=SEED, replicates=REPLICATES):
    a, b = np.asarray(no_skills, dtype=float), np.asarray(skills, dtype=float)
    if a.shape != b.shape or a.ndim != 2 or a.shape[1] != 4 or not len(a):
        raise ValueError("The paired inputs must have the same nonempty task-by-four shape.")
    differences = (b - a).mean(axis=1)
    rng = np.random.default_rng(seed)
    draws = []
    for start in range(0, replicates, 2000):
        size = min(2000, replicates - start)
        indexes = rng.integers(0, len(a), size=(size, len(a)))
        draws.append(differences[indexes].mean(axis=1))
    lower, upper = np.quantile(np.concatenate(draws), [0.025, 0.975])
    return {"gain": float(differences.mean()), "ci_low": float(lower), "ci_high": float(upper)}


def paired_randomization_p(no_skills, skills, seed=SEED, replicates=REPLICATES):
    a, b = np.asarray(no_skills, dtype=float), np.asarray(skills, dtype=float)
    if a.shape != b.shape or a.ndim != 2 or a.shape[1] != 4 or not len(a):
        raise ValueError("The paired inputs must have the same nonempty task-by-four shape.")
    differences = (b - a).mean(axis=1)
    differences = differences[differences != 0]
    if not len(differences):
        return 1.0
    observed = abs(float(differences.sum()))
    if len(differences) <= 16:
        extreme = total = 0
        for signs in itertools.product((-1, 1), repeat=len(differences)):
            extreme += abs(float(np.dot(signs, differences))) >= observed - 1e-12
            total += 1
        return extreme / total
    rng = np.random.default_rng(seed)
    extreme = 0
    for start in range(0, replicates, 2000):
        size = min(2000, replicates - start)
        signs = rng.integers(0, 2, size=(size, len(differences)), dtype=np.int8) * 2 - 1
        extreme += int(np.count_nonzero(np.abs(signs @ differences) >= observed - 1e-12))
    return (extreme + 1) / (replicates + 1)


def pooled_gain(task_cell_gains, seed=0, replicates=POOLED_REPLICATES):
    values = np.asarray(task_cell_gains, dtype=float)
    if values.ndim != 2 or not all(values.shape) or not np.isfinite(values).all() or replicates < 1:
        raise ValueError("Pooled gains must have a nonempty finite task-by-cell shape.")
    rng = random.Random(seed)

    def interval(data):
        data = list(data)
        draws = sorted(sum(rng.choices(data, k=len(data))) / len(data) for _ in range(replicates))
        return draws[int(replicates * 0.025)], draws[min(int(replicates * 0.975), replicates - 1)]

    per_task = values.mean(axis=1)
    lower, upper = interval(per_task)
    widths = []
    for column in values.T:
        low, high = interval(column)
        widths.append(high - low)
    correlations = []
    undefined = 0
    for a, b in itertools.combinations(values.T, 2):
        if np.ptp(a) == 0 or np.ptp(b) == 0:
            undefined += 1
        else:
            correlations.append(float(np.corrcoef(a, b)[0, 1]))
    return {
        "gain": float(per_task.mean()),
        "ci_low": float(lower),
        "ci_high": float(upper),
        "ci_width": float(upper - lower),
        "mean_per_cell_ci_width": float(np.mean(widths)),
        "mean_pairwise_correlation": float(np.mean(correlations)) if correlations else None,
        "correlation_pairs": len(correlations),
        "undefined_correlation_pairs": undefined,
        "tasks": len(values),
        "cells": values.shape[1],
    }


def teacher_transfer_audit(teacher, skills):
    traces = {}
    tasks = {}
    for row in teacher:
        trace = row["trajectory_uuid"]
        if not trace or trace in traces:
            raise ValueError("Teacher Trace identifiers must be present and unique.")
        traces[trace] = row
        task = tasks.setdefault((row["domain"], int(row["task_index"])), {})
        if int(row["trial"]) in task:
            raise ValueError("A teacher task has duplicate trials.")
        task[int(row["trial"])] = trace
    if any(set(task) != {1, 2, 3, 4} for task in tasks.values()):
        raise ValueError("Each teacher task must have four trials for the citation audit.")
    zero = {
        task: set(trials.values())
        for task, trials in tasks.items()
        if not any(passed(traces[trace]) for trace in trials.values())
    }
    cited_by_skill = []
    source_by_skill = []
    for skill in skills:
        summaries = set(
            re.findall(r"<!--\s*zep-evidence:\s*([^<>\s]+)\s*-->", skill["full_markdown"])
        )
        evidence = skill["lineage"]["evidence"]
        cited = set()
        for summary in summaries:
            matching = [item for item in evidence if item.get("summary_version_uuid") == summary]
            ids = {item.get("trace_uuid") for item in matching}
            if len(ids) != 1 or None in ids:
                raise ValueError("A snapshot citation cannot be resolved to one Trace.")
            cited.update(ids)
        if cited != set(skill["cited_trace_uuids"]):
            raise ValueError("Snapshot citation metadata disagrees with its Markdown and evidence.")
        source = set(skill["source_trace_uuids"]) | {
            item["trace_uuid"] for item in evidence if item.get("trace_uuid")
        }
        if not (source | cited) <= traces.keys():
            raise ValueError("A snapshot Trace cannot be matched to a teacher episode.")
        if any(not passed(traces[trace]) for trace in cited):
            raise ValueError("An admitted Skill cites a failed teacher Trace.")
        cited_by_skill.append(cited)
        source_by_skill.append(source)
    cited = set().union(*cited_by_skill)
    source = set().union(*source_by_skill)
    zero_traces = set().union(*zero.values())
    per_task = [
        {
            "domain": task[0],
            "task_index": task[1],
            "teacher_trials": len(ids),
            "cited_traces": len(ids & cited),
            "skills_citing": sum(bool(ids & refs) for refs in cited_by_skill),
            "source_lineage_traces": len(ids & source),
        }
        for task, ids in sorted(zero.items())
    ]
    return {
        "admitted_skills": len(skills),
        "teacher_zero_tasks": len(zero),
        "teacher_zero_trials": len(zero_traces),
        "cited_teacher_traces": len(cited),
        "source_teacher_traces": len(source),
        "zero_task_citations": len(zero_traces & cited),
        "zero_task_source_traces": len(zero_traces & source),
        "zero_tasks_with_source_lineage": sum(
            bool(row["source_lineage_traces"]) for row in per_task
        ),
        "skills_with_zero_task_lineage": sum(bool(zero_traces & refs) for refs in source_by_skill),
        "tasks": per_task,
    }


def empty_response_sensitivity(no_skills, skills, seed=SEED, replicates=REPLICATES):
    arms = [{key(row): row for row in rows} for rows in (no_skills, skills)]
    if any(len(arm) != len(rows) for arm, rows in zip(arms, (no_skills, skills))) or set(
        arms[0]
    ) != set(arms[1]):
        raise ValueError("The sensitivity inputs must have matching unique episode keys.")
    retained = [k for k in sorted(arms[0]) if not any(arm[k].get("empty_response") for arm in arms)]
    successes = [sum(passed(arm[k]) for k in retained) for arm in arms]
    result = {
        "retained_pairs": len(retained),
        "dropped_pairs": len(arms[0]) - len(retained),
        "passes": dict(zip(("none", "skills"), successes)),
        "pass_rates": dict(
            zip(("none", "skills"), [v / len(retained) if retained else None for v in successes])
        ),
        "tasks": 0,
        "gain": None,
        "ci_low": None,
        "ci_high": None,
    }
    if not retained:
        return result
    clusters = {}
    for k in retained:
        counts = clusters.setdefault(k[:2], [0, 0])
        counts[0] += 1
        counts[1] += int(passed(arms[1][k])) - int(passed(arms[0][k]))
    counts, differences = np.asarray(list(clusters.values())).T
    rng = np.random.default_rng(seed)
    draws = []
    for start in range(0, replicates, 2000):
        size = min(2000, replicates - start)
        indexes = rng.integers(0, len(counts), size=(size, len(counts)))
        draws.append(differences[indexes].sum(axis=1) / counts[indexes].sum(axis=1))
    lower, upper = np.quantile(np.concatenate(draws), [0.025, 0.975])
    result.update(
        tasks=len(counts),
        gain=float(differences.sum() / counts.sum()),
        ci_low=float(lower),
        ci_high=float(upper),
    )
    return result


def holm(p_values):
    if not p_values or any(not 0 <= p <= 1 for p in p_values):
        raise ValueError("The p-values must be a nonempty list between zero and one.")
    adjusted = [None] * len(p_values)
    previous = 0.0
    for rank, index in enumerate(sorted(range(len(p_values)), key=p_values.__getitem__)):
        previous = max(previous, min(1.0, (len(p_values) - rank) * p_values[index]))
        adjusted[index] = previous
    return adjusted


def latency(values):
    if not values:
        return {"n": 0, "p50": None, "mean": None, "p95": None}
    return {
        "n": len(values),
        "p50": float(np.quantile(values, 0.5)),
        "mean": float(np.mean(values)),
        "p95": float(np.quantile(values, 0.95)),
    }


def skill_stats(rows):
    reads = with_read = missing = invalid = repaired = shell = kb = no_retrieval = clock = 0
    for row in rows:
        calls = row.get("tool_call_sequence", [])
        positions = row.get("skill_read_positions", [])
        read_calls = row.get("skill_read_calls", [])
        if len(positions) != len(read_calls):
            raise ValueError("The ordered Skill reads do not match the read audit.")
        reads += len(read_calls)
        invalid += int(row.get("skill_invalid_loads", 0))
        missing += sum(read.get("found") is False for read in read_calls)
        repaired += sum(bool(read.get("repaired")) for read in read_calls)
        clock += any(
            call.get("tool") == "get_current_time" and call.get("requestor") == "assistant"
            for call in calls
        )
        if not positions:
            continue
        with_read += 1
        first = min(read["tool_call_index"] for read in positions)
        next_retrieval = next(
            (
                call["tool"]
                for call in calls
                if call["tool_call_index"] > first
                and call.get("requestor") == "assistant"
                and (call["tool"] == "shell" or call["tool"].startswith("KB_search_"))
            ),
            None,
        )
        shell += next_retrieval == "shell"
        kb += bool(next_retrieval and next_retrieval.startswith("KB_search_"))
        no_retrieval += next_retrieval is None
    return {
        "episodes": len(rows),
        "reads": reads,
        "reads_per_episode": reads / len(rows) if rows else None,
        "with_read": with_read,
        "missing": missing,
        "invalid": invalid,
        "repaired": repaired,
        "shell": shell,
        "kb": kb,
        "no_retrieval": no_retrieval,
        "clock": clock,
    }


def token_totals(rows):
    fields = ["prompt_tokens", "cached_tokens", "cache_creation_tokens", "completion_tokens"]
    totals = {field: sum(int(row.get(field, 0)) for row in rows) for field in fields}
    reasoning = [row.get("reasoning_tokens") for row in rows]
    totals["reasoning_tokens"] = (
        sum(reasoning) if reasoning and all(v is not None for v in reasoning) else None
    )
    totals["total_tokens"] = totals["prompt_tokens"] + totals["completion_tokens"]
    successes = sum(passed(row) for row in rows)
    totals["tokens_per_passed_episode"] = totals["total_tokens"] / successes if successes else None
    successful_total = sum(
        row["prompt_tokens"] + row["completion_tokens"] for row in rows if passed(row)
    )
    totals["mean_tokens_in_successful_episode"] = (
        successful_total / successes if successes else None
    )
    return totals


def config_summary(manifest, rows):
    tasks = {}
    for row in rows:
        tasks.setdefault(int(row["task_index"]), {})[int(row["trial"])] = int(passed(row))
    successes = sum(passed(row) for row in rows)
    errors_by_sha = {}
    for sha in SOURCES:
        selected = [row for row in rows if row["git_sha"] == sha]
        errors_by_sha[sha] = {
            "episodes": len(selected),
            "empty": sum(bool(row.get("empty_response")) for row in selected),
            "other": sum(
                bool(row.get("error")) and not row.get("empty_response") for row in selected
            ),
        }
    return {
        "run_id": manifest["run_id"],
        "provider": manifest["provider"],
        "model": manifest["model"],
        "effort": manifest["effort"],
        "arm": manifest["arm"],
        "n": len(rows),
        "passes": successes,
        "pass_rate": successes / len(rows),
        "pass4": sum(all(trials.values()) and len(trials) == 4 for trials in tasks.values()),
        "task_count": len(tasks),
        "tasks": tasks,
        "tokens": token_totals(rows),
        "skill": skill_stats(rows),
        "errors_by_sha": errors_by_sha,
        "latency": {
            group: {
                field: latency([float(row[field]) for row in rows if passed(row) == success])
                for field in ("model_s", "duration_s")
            }
            for group, success in [("passed", True), ("failed", False)]
        },
    }


def pct(value):
    return "N/A" if value is None else f"{value * 100:.2f}%"


def number(value):
    return "N/A" if value is None else f"{value:,.0f}"


def gain_text(result):
    return f"{result['gain'] * 100:+.2f} [{result['ci_low'] * 100:+.2f}, {result['ci_high'] * 100:+.2f}]"


def table(headers, rows):
    return "\n".join(
        ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
        + ["| " + " | ".join(str(v) for v in row) + " |" for row in rows]
    )


def combined_table(configs, gains):
    rows = []
    for c in configs:
        gain = gains[(c["model"], c["effort"])]
        values = [gain_text(gain["overall"]), f"{gain['p_holm']:.5f}"] + [
            gain_text(gain[cohort]) for cohort in ("all", "some", "none")
        ]
        empty = sum(v["empty"] for v in c["errors_by_sha"].values())
        rows.append(
            [
                c["model"],
                c["effort"],
                c["arm"],
                f"{c['passes']}/388 ({pct(c['pass_rate'])})",
                f"{c['pass4']}/97 ({pct(c['pass4'] / 97)})",
                f"{empty}/388 ({pct(empty / 388)})",
            ]
            + (values if c["arm"] == "skills" else ["—"] * 5)
        )
    rows.append(
        [
            "claude-opus-5-5",
            "max",
            "teacher reference",
            "222/388 (57.22%)",
            "40/97 (41.24%)",
            "0/388 (0.00%)",
        ]
        + ["—"] * 5
    )
    return table(
        [
            "Model",
            "Effort",
            "Arm",
            "Passes / episodes",
            "pass⁴ / tasks",
            "Empty responses / episodes",
            "Skills Δ pp [95% CI]",
            "Holm p",
            "Teacher all: Δ pp [95% CI]",
            "Teacher some: Δ pp [95% CI]",
            "Teacher none: Δ pp [95% CI]",
        ],
        rows,
    )


def difficulty_transfer_section(gains, pooled_zero, audit):
    labels = {"all": "4/4", "some": "1–3", "none": "0/4"}
    highest_some = sum(
        gain["some"]["gain"] > max(gain["all"]["gain"], gain["none"]["gain"])
        for gain in gains.values()
    )
    above_all = sum(gain["some"]["gain"] > gain["all"]["gain"] for gain in gains.values())
    lines = [
        "## Gain by teacher difficulty and transfer",
        "",
        "The strata use the teacher results on each task: 4/4, 1–3, or 0/4 passed trials.",
        "Each cell has equal task weights within a stratum. The cell intervals use the same paired task bootstrap as the primary table.",
        "These strata describe teacher performance. They are not an independent measurement of task difficulty.",
        "",
        table(
            [
                "Model",
                "Effort",
                "Teacher 4/4: gain pp [95% CI]",
                "Teacher 1–3: gain pp [95% CI]",
                "Teacher 0/4: gain pp [95% CI]",
            ],
            [
                [model, effort] + [gain_text(gain[name]) for name in labels]
                for (model, effort), gain in gains.items()
            ],
        ),
        "",
        f"The 1–3 stratum has the strictly highest measured gain in {highest_some} of {len(gains)} cells. Its gain exceeds the 4/4 gain in {above_all} of {len(gains)} cells.",
    ]
    for (model, effort), gain in gains.items():
        relation = (
            "higher than"
            if gain["some"]["gain"] > gain["all"]["gain"]
            else "lower than"
            if gain["some"]["gain"] < gain["all"]["gain"]
            else "equal to"
        )
        highest = max(gain[name]["gain"] for name in labels)
        groups = " and ".join(
            label for name, label in labels.items() if gain[name]["gain"] == highest
        )
        values = "; ".join(
            f"{label}: {gain[name]['gain'] * 100:+.2f} pp" for name, label in labels.items()
        )
        lines.append(
            f"- {model} {effort}: {values}. The 1–3 gain is {relation} the 4/4 gain. The highest measured group is {groups}."
        )
    includes_zero = pooled_zero["ci_low"] <= 0 <= pooled_zero["ci_high"]
    lines += [
        "",
        f"The pooled teacher-0/4 gain across all eight cells is {gain_text(pooled_zero)} pp. Its 95% CI {'includes 0' if includes_zero else 'does not include 0'}.",
        "This pooled interval uses 10,000 task resamples. Each draw keeps all trials and all cells of a selected task together.",
        f"The snapshot check matched {audit['cited_teacher_traces']} cited Traces to teacher episodes and found {audit['zero_task_citations']} citations of the {audit['teacher_zero_trials']} Traces from {audit['teacher_zero_tasks']} teacher-0/4 tasks across {audit['admitted_skills']} admitted Skills.",
        "The check resolves the Markdown evidence markers through the snapshot lineage and verifies that they match cited_trace_uuids. Every resolved citation is a passed teacher Trace.",
        "Thus, no Skill cites a passed Trace of the same teacher-0/4 task. A gain in that stratum is consistent with a possible cross-task transfer effect from procedures supported by other tasks.",
    ]
    if audit["zero_task_source_traces"]:
        lines += [
            f"However, the broader source lineage contains {audit['zero_task_source_traces']} failed Traces from {audit['zero_tasks_with_source_lineage']} teacher-0/4 tasks in {audit['skills_with_zero_task_lineage']} admitted Skills. Those Traces are not cited.",
            "The citation check does not exclude same-task failure evidence in the compilation lineage. It therefore does not establish that any gain came only from Skills compiled from other tasks.",
        ]
    else:
        lines.append("The broader source lineage also contains no Trace from a teacher-0/4 task.")
    lines += [
        "This is not a controlled estimate of transfer to new tasks. The suite reuses the teacher tasks, and it does not randomize Skill provenance.",
        "",
        "The per-task citation check is saved in teacher-transfer-check.json. Task indices are source indices; no task instructions or content appear in that check.",
    ]
    return "\n".join(lines)


def raw_visible_output(payloads):
    terminal = [p["response"] for p in payloads if p.get("type") == "response.completed"]
    if terminal:
        payloads = terminal[-1:]
    text_chars = tool_calls = 0
    for payload in payloads:
        for item in payload.get("output", []):
            tool_calls += item.get("type") == "function_call"
            if item.get("type") == "message":
                text_chars += sum(
                    len(part.get("text") or "")
                    for part in item.get("content", [])
                    if part.get("type") == "output_text"
                )
        blocks = list(payload.get("content", []))
        if payload.get("type") == "message_start":
            blocks += payload.get("message", {}).get("content", [])
        if payload.get("type") == "content_block_start":
            blocks.append(payload["content_block"])
        for block in blocks:
            tool_calls += block.get("type") == "tool_use"
            if block.get("type") == "text":
                text_chars += len(block.get("text") or "")
        if (
            payload.get("type") == "content_block_delta"
            and payload.get("delta", {}).get("type") == "text_delta"
        ):
            text_chars += len(payload["delta"].get("text") or "")
    return {"text_chars": text_chars, "tool_calls": tool_calls}


def raw_response_audit(root, rows_by_config):
    checkpoint = json.loads((root / "empty-policy-restart-checkpoint.json").read_text())
    older = {(item["config"], tuple(item["key"])) for item in checkpoint["empty_results"]}
    stats = {
        "checked": 0,
        "raw_empty": 0,
        "raw_nonempty": 0,
        "pre_capture_missing": 0,
        "unexpected_missing": 0,
        "details": [],
    }
    for name, rows in rows_by_config.items():
        for row in rows:
            if not row.get("empty_response"):
                continue
            artifacts = row.get("raw_response_artifacts", [])
            if not artifacts:
                stats[
                    "pre_capture_missing" if (name, key(row)) in older else "unexpected_missing"
                ] += 1
                continue
            paths = [root / name / path for path in artifacts]
            metadata = json.loads(
                next(path for path in paths if path.name.endswith(".metadata.json")).read_text()
            )
            body = next(path for path in paths if path.suffix == ".httpbody").read_bytes()
            assert hashlib.sha256(body).hexdigest() == metadata["httpbody_sha256"]
            assert metadata["complete"] and not metadata["stream_error"]
            json_path = next(
                path
                for path in paths
                if path.suffix in (".json", ".jsonl") and not path.name.endswith(".metadata.json")
            )
            payloads = (
                load_jsonl(json_path)
                if json_path.suffix == ".jsonl"
                else [json.loads(json_path.read_bytes())]
            )
            visible = raw_visible_output(payloads)
            raw_nonempty = bool(visible["text_chars"] or visible["tool_calls"])
            stats["checked"] += 1
            stats["raw_nonempty" if raw_nonempty else "raw_empty"] += 1
            stats["details"].append(
                {"config": name, "key": key(row), **visible, "artifacts": artifacts}
            )
    return stats


def request_failures(records):
    counts = Counter((row["service"], row["outcome"]) for row in records)
    return [
        {"service": service, "outcome": outcome, "count": count}
        for (service, outcome), count in sorted(counts.items())
    ]


def validate_recorder_recovery(entries, results):
    expected = {entry["id"]: entry for entry in entries}
    actual = {result["ledger_id"]: result for result in results}
    if len(expected) != len(entries) or len(actual) != len(results) or set(expected) != set(actual):
        raise ValueError("The recorder recovery ledger and results do not match uniquely.")
    for entry_id, entry in expected.items():
        result = actual[entry_id]
        if entry["classification"] != "harness_recording_failure":
            raise ValueError("Recovery selected a non-recorder failure.")
        if any(result[field] != entry[field] for field in ("config", "episode_key", "seed")):
            raise ValueError("Recovery changed an episode configuration, key, or seed.")


def validate_inode_resume(plan, rows_by_config, recovery):
    selected = {(result["config"], tuple(result["episode_key"])) for result in recovery}
    verified = 0
    for name, config in plan["configurations"].items():
        current = {key(row): row for row in rows_by_config[name]}
        for episode, sha in config["preserved_row_hashes"]:
            episode = tuple(episode)
            if (name, episode) not in selected:
                row = current.get(episode)
                if (
                    row is None
                    or hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest() != sha
                ):
                    raise ValueError(
                        "A retained inode-recovery result changed without recorder authorization."
                    )
                verified += 1
        for episode in config["to_run_keys"]:
            row = current.get(tuple(episode))
            if row is None or row.get("git_sha") != LIFECYCLE:
                raise ValueError(
                    "An inode-resumed episode is missing or has the wrong source commit."
                )
    return verified


def build(root, teacher_path=None):
    teacher = load_jsonl(teacher_file(root, teacher_path))
    expected = {key(row) for row in teacher}
    assert len(teacher) == len(expected) == 388
    assert sum(passed(row) for row in teacher) == 222
    teacher_passes = Counter()
    for row in teacher:
        teacher_passes[row["task_index"]] += passed(row)
    cohorts = {
        name: sorted(
            task
            for task, count in teacher_passes.items()
            if ("all" if count == 4 else "some" if count else "none") == name
        )
        for name in ("all", "some", "none")
    }
    assert {name: len(tasks) for name, tasks in cohorts.items()} == {
        "all": 40,
        "some": 27,
        "none": 30,
    }
    manifests = [json.loads(path.read_text()) for path in root.glob("*-manifest.json")]
    manifests.sort(key=lambda m: (ORDER[m["provider"]], EFFORT_ORDER[m["effort"]], m["arm"]))
    if len(manifests) != 16:
        raise ValueError("The suite does not have exactly 16 configuration manifests.")
    configs = []
    all_rows = []
    rows_by_config = {}
    evidence_hashes = {}
    for manifest in manifests:
        path = root / manifest["run_id"] / f"heldout-{manifest['arm']}.jsonl"
        rows = load_jsonl(path)
        validate_rows(rows, expected)
        if any(row.get("arm") != manifest["arm"] for row in rows):
            raise ValueError("The episode arm does not match its configuration manifest.")
        if manifest.get("returncode") != 0:
            raise ValueError(f"The worker did not finish successfully: {manifest['run_id']}")
        config = config_summary(manifest, rows)
        failure_path = root / manifest["run_id"] / "failed-attempts.jsonl"
        config["request_failures"] = (
            request_failures(load_jsonl(failure_path)) if failure_path.is_file() else []
        )
        configs.append(config)
        all_rows.extend(rows)
        rows_by_config[manifest["run_id"]] = rows
        evidence_hashes[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    assert len(all_rows) == 6208
    snapshot = json.loads((root.parent / "snapshot/snapshot.json").read_text())
    assert (
        hashlib.sha256(json.dumps(snapshot["skills"], sort_keys=True).encode()).hexdigest()
        == SNAPSHOT_HASH
    )
    tasks = sorted(teacher_passes)
    pairs = {}
    for config in configs:
        pairs.setdefault((config["model"], config["effort"]), {})[config["arm"]] = config
    if len(pairs) != 8 or any(set(pair) != {"none", "skills"} for pair in pairs.values()):
        raise ValueError("The eight matched model-effort pairs are incomplete.")
    gains = {}
    cell_task_gains = {}
    for index, (pair_key, arms) in enumerate(pairs.items()):
        arrays = {
            arm: np.asarray(
                [[config["tasks"][task][trial] for trial in range(1, 5)] for task in tasks]
            )
            for arm, config in arms.items()
        }
        result = {
            "overall": bootstrap_gain(arrays["none"], arrays["skills"], SEED + index * 10),
            "p_raw": paired_randomization_p(
                arrays["none"], arrays["skills"], SEED + index * 10 + 1
            ),
            "sensitivity": empty_response_sensitivity(
                rows_by_config[arms["none"]["run_id"]],
                rows_by_config[arms["skills"]["run_id"]],
                SEED + index * 10 + 9,
            ),
        }
        for offset, (name, cohort) in enumerate(cohorts.items(), 2):
            indexes = [tasks.index(task) for task in cohort]
            result[name] = bootstrap_gain(
                arrays["none"][indexes], arrays["skills"][indexes], SEED + index * 10 + offset
            )
        gains[pair_key] = result
        cell_task_gains[pair_key] = (arrays["skills"] - arrays["none"]).mean(axis=1)
    for pair_key, adjusted in zip(gains, holm([result["p_raw"] for result in gains.values()])):
        gains[pair_key]["p_holm"] = adjusted
    columns = list(cell_task_gains)
    matrix = np.column_stack(list(cell_task_gains.values()))
    pooled = {"All eight cells": pooled_gain(matrix)}
    for model in dict.fromkeys(model for model, effort in columns):
        pooled[model] = pooled_gain(
            matrix[:, [i for i, pair_key in enumerate(columns) if pair_key[0] == model]]
        )
    for name, label in (("all", "4/4"), ("some", "1–3"), ("none", "0/4")):
        pooled[f"Teacher {label} (all cells)"] = pooled_gain(
            matrix[[tasks.index(task) for task in cohorts[name]]]
        )
    transfer = teacher_transfer_audit(teacher, snapshot["skills"])
    arm_skills = {
        arm: skill_stats([row for row in all_rows if row["arm"] == arm])
        for arm in ("none", "skills")
    }
    ledger_path = root / "recorder-failure-ledger.jsonl"
    ledger = load_jsonl(ledger_path) if ledger_path.is_file() else []
    recovery_path = root / "recorder-rerun-results.json"
    recovery = (
        json.loads(recovery_path.read_text()) if recovery_path.is_file() else {"episodes": []}
    )
    validate_recorder_recovery(ledger, recovery["episodes"])
    for result in recovery["episodes"]:
        row = next(
            row
            for row in rows_by_config[result["config"]]
            if key(row) == tuple(result["episode_key"])
        )
        if (
            hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
            != result["rerun_row_sha256"]
        ):
            raise ValueError("The final episode does not match its recorder recovery result.")
    inode_plan = json.loads((root / "inode-resume-plan.json").read_text())
    preserved = validate_inode_resume(inode_plan, rows_by_config, recovery["episodes"])
    for filename, sha in inode_plan["archives"].items():
        if hashlib.sha256((root / filename).read_bytes()).hexdigest() != sha:
            raise ValueError("An inode-stop archive changed after recovery.")
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "configs": configs,
        "gains": gains,
        "pooled": pooled,
        "teacher_transfer_check": transfer,
        "inode_recovery": {
            "totals": inode_plan["totals"],
            "archive": inode_plan["archive"],
            "archives_verified": len(inode_plan["archives"]),
            "preserved_rows_verified": preserved,
        },
        "cohort_sizes": {name: len(cohort) for name, cohort in cohorts.items()},
        "arm_skills": arm_skills,
        "evidence_hashes": evidence_hashes,
        "raw_response_check": raw_response_audit(root, rows_by_config),
        "compiler_fallback": json.loads((root / "compiler-fallback-limit.json").read_text()),
        "executor_recording": json.loads((root / "executor-recording-audit.json").read_text()),
        "recorder_ledger": ledger,
        "recorder_recovery": recovery,
    }


def render(data):
    configs, gains = data["configs"], data["gains"]
    raw = data["raw_response_check"]
    fallback = data["compiler_fallback"]
    lines = [
        "# Full tau3 executor report",
        "",
        f"The report was generated at {data['generated_at']}.",
        "",
        "All 6,208 executor episodes completed: 16 configurations × 97 tasks × four trials.",
        "Every configuration used alltools, the base split, GPT-5.2 low, and a 200-step limit.",
        "The trial seeds were 626729, 373753, 361454, and 1567. The Skills arms used the frozen snapshot with skill-index-scope=all.",
        f"The frozen Skill-list hash is `{SNAPSHOT_HASH}`.",
        "",
        "## Combined results",
        "",
        combined_table(configs, gains),
        "",
        "The gain is Skills minus no-Skills. The gain units are percentage points (pp). The gain columns appear on the Skills rows.",
        "pass⁴ is the share of tasks that passed all four trials. Empty responses and other episode errors count as failures.",
        "The teacher groups contain 40 tasks passed in all four trials, 27 passed in some trials, and 30 passed in no trials.",
        "",
        "### Statistical method",
        "",
        "Each paired bootstrap draw resamples tasks with replacement. Each selected task retains all four trials in both arms.",
        "The report uses 100,000 draws and percentile 95% confidence intervals. The base random seed is 300.",
        "The overall tests use two-sided paired randomization. Each randomization swaps the arm labels for an entire task.",
        "The tests use exact enumeration for at most 16 nonzero task differences. Otherwise, they use 100,000 randomizations and the plus-one p-value correction.",
        "The Holm step-down correction covers the eight overall model-effort tests at a family-wise alpha of 0.05.",
        "The displayed confidence intervals are marginal 95% intervals. Holm adjusts the p-values, not these intervals.",
        "The teacher-group intervals are exploratory. They do not have a separate multiplicity correction.",
        "",
        "### Key findings",
        "",
    ]
    for (model, effort), gain in gains.items():
        direction = (
            "positive"
            if gain["overall"]["gain"] > 0
            else "negative"
            if gain["overall"]["gain"] < 0
            else "zero"
        )
        significant = "passes" if gain["p_holm"] <= 0.05 else "does not pass"
        lines.append(
            f"- {model} {effort} has a {direction} gain of {gain_text(gain['overall'])} pp. The overall test {significant} the Holm threshold (adjusted p={gain['p_holm']:.5f})."
        )
    lines += [
        "",
        "## Pooled gain",
        "",
        "This is a secondary analysis. The eight per-cell Holm-adjusted results remain primary.",
        "For each task, the pooled gain is the mean of the cell gains. Each cell gain is Skills pass share minus no-Skills pass share across the four trials.",
        "Cells have equal weight within each listed pool. The bootstrap resamples tasks and keeps all cells and all four trials of each task together.",
        "Each interval uses 10,000 resamples with Python random.Random(0). The percentile endpoints are sorted draw indices 250 and 9750, as in ds_pool.py.",
        "The mean per-cell CI width uses separate 10,000-resample intervals for the same cells and tasks, with the continuing group RNG. It is not an independent-cell pooled interval.",
        "The correlation is the mean Pearson correlation of per-task gains between cell pairs. Constant gain vectors have undefined correlations and are excluded; the table gives the number of valid and undefined pairs.",
        "Narrower pooled intervals do not imply independent cells. No additional hypothesis tests or Holm claims are made for these pools.",
        "",
        table(
            [
                "Pool",
                "Tasks",
                "Cells",
                "Gain pp [95% CI]",
                "CI width pp",
                "Mean cell CI width pp",
                "Mean pairwise r",
                "Valid / undefined pairs",
            ],
            [
                [
                    name,
                    result["tasks"],
                    result["cells"],
                    gain_text(result),
                    f"{result['ci_width'] * 100:.2f}",
                    f"{result['mean_per_cell_ci_width'] * 100:.2f}",
                    "N/A"
                    if result["mean_pairwise_correlation"] is None
                    else f"{result['mean_pairwise_correlation']:.3f}",
                    f"{result['correlation_pairs']} / {result['undefined_correlation_pairs']}",
                ]
                for name, result in data["pooled"].items()
            ],
        ),
        "",
        difficulty_transfer_section(
            gains, data["pooled"]["Teacher 0/4 (all cells)"], data["teacher_transfer_check"]
        ),
    ]
    lines += [
        "",
        "## Empty-response sensitivity",
        "",
        "The primary results score empty responses as failures, consistent with upstream tau2 AGENT_ERROR.",
        "This sensitivity view removes a task-trial pair from both arms when either arm has an empty response.",
        "Each pass rate uses the same retained pairs in both arms. Other errors remain failures.",
        "The gain uses the pooled retained-episode pass rates. The bootstrap resamples matched tasks with at least one retained trial.",
        "Each selected task retains all its surviving trial pairs. Each draw recomputes the ratio of successes to retained episodes.",
        "The sensitivity intervals are exploratory marginal 95% intervals. They do not add tests to the primary eight-test Holm family.",
        "",
    ]
    sensitivity_rows = []
    for c in configs:
        result = gains[c["model"], c["effort"]]["sensitivity"]
        sensitivity_rows.append(
            [
                c["model"],
                c["effort"],
                c["arm"],
                pct(c["pass_rate"]),
                f"{result['passes'][c['arm']]}/{result['retained_pairs']} ({pct(result['pass_rates'][c['arm']])})",
                result["dropped_pairs"],
                result["tasks"],
                gain_text(result) if c["arm"] == "skills" and result["gain"] is not None else "—",
            ]
        )
    lines += [
        table(
            [
                "Model",
                "Effort",
                "Arm",
                "Primary pass rate",
                "Sensitivity passes / retained pairs",
                "Dropped pairs",
                "Retained tasks",
                "Sensitivity Skills Δ pp [95% CI]",
            ],
            sensitivity_rows,
        ),
        "",
        "## Executor tokens",
        "",
        "The token totals cover executor model calls in the retained episode attempts. They exclude user simulator and evaluator calls.",
        "Total tokens equal prompt plus completion tokens. Cached and cache-write tokens are prompt subsets. Reasoning tokens are a completion subset.",
        "Tokens per passed episode equal all tokens in the configuration divided by its successful episodes. This includes tokens spent on failed episodes.",
        "The final column gives the mean tokens within successful episodes as a separate conditional measure.",
        "Claude does not return a separate reasoning-token count. N/A does not mean zero.",
        "The tables exclude interrupted attempts before the restart and superseded recording-failed attempts. Request logs retain their observed usage; usage without a returned response is unavailable.",
        "",
    ]
    lines.append(
        table(
            [
                "Model",
                "Effort",
                "Arm",
                "Prompt",
                "Cached",
                "Cache-write",
                "Completion",
                "Reasoning",
                "Total",
                "Tokens / passed episode",
                "Mean tokens within passed",
            ],
            [
                [c["model"], c["effort"], c["arm"]]
                + [
                    number(c["tokens"][field])
                    for field in (
                        "prompt_tokens",
                        "cached_tokens",
                        "cache_creation_tokens",
                        "completion_tokens",
                        "reasoning_tokens",
                        "total_tokens",
                        "tokens_per_passed_episode",
                        "mean_tokens_in_successful_episode",
                    )
                ]
                for c in configs
            ],
        )
    )
    lines += [
        "",
        "## Episode latency",
        "",
        "All latency values are seconds. The failed group includes ordinary failures, empty responses, and other errors.",
        "The model_s field measures model time. The duration_s field measures episode duration. Provider lease queue time is excluded.",
        "",
    ]
    latency_rows = []
    for c in configs:
        for group, metrics in c["latency"].items():
            latency_rows.append(
                [c["model"], c["effort"], c["arm"], group, metrics["model_s"]["n"]]
                + [
                    "N/A" if metrics[field][stat] is None else f"{metrics[field][stat]:.2f}"
                    for field in ("model_s", "duration_s")
                    for stat in ("p50", "mean", "p95")
                ]
            )
    lines.append(
        table(
            [
                "Model",
                "Effort",
                "Arm",
                "Outcome",
                "n",
                "model_s p50",
                "model_s mean",
                "model_s p95",
                "duration_s p50",
                "duration_s mean",
                "duration_s p95",
            ],
            latency_rows,
        )
    )
    lines += [
        "",
        "## Skill use and clock checks",
        "",
        "The read rate uses all 388 episodes in each configuration. Missing reads and invalid loads are separate audit fields and can overlap.",
        "The post-read shares use episodes with at least one read, including missing reads. Each episode counts once, after its first read.",
        "Episodes without a later retrieval remain in that denominator. The retrieval tools are shell and KB_search_*.",
        "Clock checks count episodes with an assistant get_current_time call. They do not count user tool calls.",
        "",
    ]
    skill_rows = []
    for c in configs:
        s = c["skill"]
        skill_rows.append(
            [
                c["model"],
                c["effort"],
                c["arm"],
                s["reads"],
                f"{s['reads_per_episode']:.3f}",
                f"{s['with_read']}/388 ({pct(s['with_read'] / 388)})",
                s["missing"],
                s["invalid"],
                s["repaired"],
                pct(s["shell"] / s["with_read"]) if s["with_read"] else "N/A",
                pct(s["kb"] / s["with_read"]) if s["with_read"] else "N/A",
                s["no_retrieval"],
                f"{s['clock']}/388 ({pct(s['clock'] / 388)})",
            ]
        )
    lines.append(
        table(
            [
                "Model",
                "Effort",
                "Arm",
                "Reads",
                "Reads / episode",
                "Episodes with read",
                "Missing reads",
                "Invalid loads",
                "Repaired reads",
                "Post-read shell",
                "Post-read KB_search_*",
                "No later retrieval",
                "get_current_time episodes",
            ],
            skill_rows,
        )
    )
    lines += ["", "### Aggregate arm shares", ""]
    lines.append(
        table(
            [
                "Arm",
                "Episodes",
                "Episodes with read",
                "Post-read shell",
                "Post-read KB_search_*",
                "get_current_time episodes",
            ],
            [
                [
                    arm,
                    s["episodes"],
                    s["with_read"],
                    pct(s["shell"] / s["with_read"]) if s["with_read"] else "N/A",
                    pct(s["kb"] / s["with_read"]) if s["with_read"] else "N/A",
                    f"{s['clock']}/{s['episodes']} ({pct(s['clock'] / s['episodes'])})",
                ]
                for arm, s in data["arm_skills"].items()
            ],
        )
    )
    lines += [
        "",
        "## Errors and source commits",
        "",
        f"The old source commit was `{OLD}`.",
        f"The UTF-8 fixed source commit was `{NEW}`.",
        f"The sandbox-lifecycle fixed source commit was `{LIFECYCLE}`.",
        "Each commit column gives episodes / empty responses / other errors. Other errors exclude empty responses.",
        "The UTF-8 harness interruption and the seven other interrupted Haiku leases were rerun from scratch under the fixed commit.",
        "Seven interrupted Luna leases were also rerun after the stop-policy change. All completed empty-response episodes were kept.",
        "Those interrupted attempts are not additional logical episodes. Ordinary empty responses were not retried.",
        "",
    ]
    error_rows = []
    for c in configs:
        by_sha = c["errors_by_sha"]
        error_rows.append(
            [
                c["model"],
                c["effort"],
                c["arm"],
                sum(v["empty"] for v in by_sha.values()),
                sum(v["other"] for v in by_sha.values()),
            ]
            + [
                f"{by_sha[sha]['episodes']} / {by_sha[sha]['empty']} / {by_sha[sha]['other']}"
                for sha in SOURCES
            ]
        )
    lines.append(
        table(
            [
                "Model",
                "Effort",
                "Arm",
                "Empty responses",
                "Other errors",
                "Old SHA: n / empty / other",
                "UTF-8 SHA: n / empty / other",
                "Lifecycle SHA: n / empty / other",
            ],
            error_rows,
        )
    )
    inode = data["inode_recovery"]
    lines += [
        "",
        "### Inode-stop recovery",
        "",
        "The coordinator stopped after temporary alltools sandboxes exhausted the filesystem inodes. All workers were stopped before cleanup.",
        "The audit found 22,132 sandbox directories and 15,491,713 allowed files. It found no unexpected paths or symlinks. Sampled KB copies matched the source export.",
        "Only those inactive sandbox directories were removed. The post-cleanup filesystem had 15,535,972 free inodes (6% used).",
        "The current journals, manifests, settings, and stop evidence were copied before the canonical journals were rebuilt.",
        f"The resume set kept {inode['totals']['complete']} real worker results. It excluded {inode['totals']['local_placeholders']} local RPC placeholders without git_sha and {inode['totals']['os_errors']} OSError rows.",
        f"Luna and Haiku resumed {inode['totals']['to_run']} missing keys at concurrency 8 per provider on the lifecycle fix, with the same seeds, frozen snapshot, user simulator, and Trace recorder.",
        f"The final check verified {inode['archives_verified']} archive hashes and {inode['preserved_rows_verified']} retained result hashes outside the authorized recorder reruns.",
        "The excluded local failures remain in the archive. They are not model-provider failures or additional logical episodes.",
        "",
    ]
    lines += [
        "",
        "### Failed request attempts",
        "",
        "These counts cover all services in the request ledger, including transient failures and interrupted attempts.",
        "A failed request attempt is not necessarily an episode error. Existing client retries were not changed. No retry was added for an empty response.",
        "",
    ]
    lines.append(
        table(
            ["Model", "Effort", "Arm", "Failed request attempts by service and outcome"],
            [
                [
                    c["model"],
                    c["effort"],
                    c["arm"],
                    "; ".join(
                        f"{v['service']} {v['outcome']}: {v['count']}"
                        for v in c["request_failures"]
                    )
                    or "0",
                ]
                for c in configs
            ],
        )
    )
    lines += [
        "",
        "### Recorder failures and authorized reruns",
        "",
        "Trace request failures are harness recording failures, not model-provider failures.",
        "Each selected episode was rerun from scratch after all original providers finished, with the same configuration, trial seed, and recorder.",
        "The primary tables use the final rerun row for each selected key. The original failed rows and their hashes remain in the recorder ledger and its archive.",
        "Completed empty responses and model-provider errors were not selected for recovery.",
        "",
    ]
    original_by_id = {entry["id"]: entry for entry in data["recorder_ledger"]}
    lines.append(
        table(
            [
                "Configuration",
                "Task index",
                "Trial",
                "Seed",
                "Original error",
                "Rerun passed",
                "Rerun reward",
                "Rerun error",
                "Rerun empty response",
                "Rerun duration_s",
            ],
            [
                [
                    result["config"],
                    result["episode_key"][1],
                    result["episode_key"][2],
                    result["seed"],
                    original_by_id[result["ledger_id"]]["error"],
                    result["rerun_succeeded"],
                    result["rerun_reward"],
                    result["rerun_error"] or "None",
                    result["rerun_empty_response"],
                    f"{result['rerun_duration_s']:.2f}",
                ]
                for result in data["recorder_recovery"]["episodes"]
            ],
        )
    )
    lines += [
        "",
        "The abandon-sequence mismatch is an unverified hypothesis. The recorder increments its local event counter before an append and supplies that counter to abandon after an exception.",
        "The observed failure sequence includes an event POST timeout or HTTP 400, followed by an abandon request with the attempted event counter.",
        "The abandon request returned HTTP 400 with the message `highest_accepted_sequence is required`. These requests target the Trace recorder, not the model provider.",
        "A timed-out append might not have reached storage, so the attempted and highest-accepted sequence values might differ. The failed request bodies and matching backend sequence states were not checked.",
        "The error text does not by itself prove that the request omitted the sequence field.",
        "",
    ]
    lines += [
        "",
        "## Limits",
        "",
        "- Filesystem inode exhaustion interrupted the suite. The final results combine retained episodes with 1,162 missing episodes resumed after a tested sandbox-lifecycle fix. The three source-commit columns record this provenance. The interruption and cleanup can affect wall-clock scheduling and latency comparability.",
        "- The teacher and executors use the same 97 tasks. The results measure same-task reuse, not transfer to unseen tasks.",
        "- The frozen Skills contain discoverable tool names. The experiment does not isolate tool-name exposure from other Skill content.",
        "- The tau2 benchmark is public. Provider pretraining contamination is not measured.",
        "- The harness uses a Responses scaffold. This comparison does not establish equality with each provider's native scaffold.",
        "- Episode duration includes Trace API overhead. The request ledger includes recovered errors from that service as well as model request failures.",
        "- The Trace recorder remained enabled for the original suite and all authorized reruns. Its failures are identified separately from model-provider failures.",
        "- Claude reasoning-token counts are unavailable. Returned thinking text does not supply a separate token count.",
        "- Empty responses count as failed episodes. The harness does not retry them.",
        "- Empty responses do not count toward the 5% stop threshold. Exceptions, systematic HTTP failures, sandbox failures, and gate rejections retain their stop rules.",
        f"- The raw HTTP check found {raw['raw_empty']} empty bodies and {raw['raw_nonempty']} bodies with visible text or tool calls among SDK-empty responses. Captures are unavailable for {raw['pre_capture_missing']} older empty responses. The check cannot exclude changes before the client HTTP boundary.",
        "- `compiler_components.go` rejects any fallback before the allowed-model and allowed-provider checks. That component invocation returns immediately and does not retry the rejected attempt. The scanner returned an allow result, but the route-provenance rule rejected the attempt.",
        "- The observed scanner rejection was due only to the fallback flag. The allowed-model/provider checks did not execute for that attempt. This does not establish that the resolved route would otherwise pass the frozen allow-list.",
        f"- {fallback['compile_outcome_identity_link']} This evidence does not prove that the complete candidate Skill was valid.",
        "- The UTF-8 fix decodes stdout and stderr with replacement. The pinned upstream tau2 shell uses strict text decoding.",
        "- The earlier Luna task_007 smoke note described a Skill leading the agent away from checking the clock. This is an observed instruction-following example, not a measured cause of the full-suite results.",
        "- The teacher-defined task groups are descriptive and exploratory. The report does not claim that teacher success causes a Skills gain.",
        "- The paired bootstrap treats tasks as sampling units. Four trial outcomes from the same task remain together.",
        "",
        "## Evidence",
        "",
        "The report metrics are in final-metrics.json. That file records hashes of all 16 completed episode journals.",
        "The source manifests, episode audits, request logs, gate logs, sandbox checks, and shell scans remain under this executor directory.",
        "",
    ]
    raw = data["raw_response_check"]
    lines += [
        "## Raw HTTP response check",
        "",
        f"The audit checked {raw['checked']} later empty responses from HTTP body JSON or raw SSE JSON frames, not SDK serialization.",
        f"The raw bodies contained no visible text or tool call in {raw['raw_empty']} cases. The raw bodies contained visible text or a tool call in {raw['raw_nonempty']} cases.",
        f"There are {raw['pre_capture_missing']} older empty responses without raw HTTP captures and {raw['unexpected_missing']} unexpected missing captures.",
        "The HTTP body is the response received by the executor client. This check cannot exclude changes before that boundary.",
        "",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--teacher", type=Path)
    args = parser.parse_args()
    data = build(args.root, args.teacher)
    report = render(data)
    serializable = {
        **data,
        "gains": [
            {"model": model, "effort": effort, **result}
            for (model, effort), result in data["gains"].items()
        ],
    }
    (args.root / "final-metrics.json").write_text(json.dumps(serializable, indent=2))
    (args.root / "teacher-transfer-check.json").write_text(
        json.dumps(data["teacher_transfer_check"], indent=2)
    )
    (args.root / "FINAL-REPORT.md").write_text(report)
    print(
        "FINAL REPORT: 16 configurations; 6208 unique episodes; 97 tasks x 4 trials per configuration."
    )
    print(
        "STATISTICS: 100000 task-level bootstrap draws; eight paired overall tests; Holm correction applied."
    )
    print(combined_table(data["configs"], data["gains"]))


if __name__ == "__main__":
    main()
