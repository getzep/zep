from __future__ import annotations

import math
import random
import re
from collections.abc import Iterable
from decimal import Decimal
from statistics import mean
from typing import Any

from scipy.stats import ttest_1samp

RELATIVE_DATE = re.compile(
    r"\b(?:today|tomorrow|yesterday|tonight|"
    r"next\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|week|month|day|business day)|"
    r"this\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
    r"in\s+\d+\s+days?)\b",
    re.IGNORECASE,
)
RELATIVE_DATE_EXAMPLE = re.compile(
    r"""\b(?:avoid|do not use|don't use)\b.{0,80}\brelative\s+phrases?\s+such as\s*(?:["']|\u201c|\u2018)?\s*$""",
    re.IGNORECASE,
)
WALL_CLOCK = re.compile(
    r"\b(?:datetime|date)\s*\(\s*['\"]now['\"](?:\s*,[^)]*)?\)",
    re.IGNORECASE,
)
SCORE_ROW_KEY_FIELDS = ("task_id", "trial", "cell", "model", "effort", "arm")


def exact_mcnemar_p_value(a_only: int, b_only: int) -> float:
    discordant = a_only + b_only
    if discordant == 0:
        return 1.0
    tail = sum(math.comb(discordant, count) for count in range(min(a_only, b_only) + 1))
    return min(1.0, 2 * tail / (2**discordant))


def reproduction_gate(p_hat: float) -> bool:
    return abs(Decimal(str(p_hat)) - Decimal("0.332")) <= Decimal("0.05")


def executor_cell_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = list(rows)
    if any(row.get("cell") == "reproduce" for row in selected):
        raise ValueError("Reproduction rows cannot be used in executor cell comparisons")
    return [row for row in selected if row.get("cell") == "executor"]


def score_summary(
    rows: Iterable[dict[str, Any]],
    *,
    excluded_task_ids: set[str] | None = None,
    include_zero_task_ids: set[str] | None = None,
) -> dict[str, int | float]:
    excluded = excluded_task_ids or set()
    include_zero = include_zero_task_ids or set()
    scored_by_key: dict[tuple[tuple[str, Any], ...], dict[str, Any]] = {}
    for row in rows:
        task_id = row.get("task_id")
        status = row.get("status")
        if task_id in excluded or row.get("cell") != "reproduce":
            continue
        if status not in {"valid", "policy_flagged"} and not (task_id in include_zero and status == "invalid"):
            continue
        key = tuple((field, row[field]) for field in SCORE_ROW_KEY_FIELDS if field in row)
        scored_by_key[key] = row
    scored = list(scored_by_key.values())
    passed = sum(int(row.get("reward", 0) or 0) for row in scored)
    return {"n": len(scored), "passed": passed, "p_hat": passed / len(scored) if scored else 0.0}


def reproduction_report(
    rows: Iterable[dict[str, Any]],
    *,
    overlay_task_ids: set[str],
    known_defect_ids: set[str],
    type_mismatch_gold_ids: set[str] | None = None,
    townhall_fixed_task_ids: set[str] | None = None,
) -> dict[str, Any]:
    materialized = list(rows)
    type_mismatch_gold = type_mismatch_gold_ids or set()
    townhall_fixed = townhall_fixed_task_ids or set()
    return {
        "all": score_summary(
            materialized,
            include_zero_task_ids=known_defect_ids | type_mismatch_gold,
        ),
        "headline": score_summary(materialized, excluded_task_ids=type_mismatch_gold),
        "including_type_mismatch_gold": score_summary(materialized, include_zero_task_ids=type_mismatch_gold),
        "without_overlay": score_summary(materialized, excluded_task_ids=overlay_task_ids),
        "without_known_defects": score_summary(materialized, excluded_task_ids=known_defect_ids),
        "without_type_mismatch_gold": score_summary(materialized, excluded_task_ids=type_mismatch_gold),
        "without_overlay_and_known_defects": score_summary(
            materialized, excluded_task_ids=overlay_task_ids | known_defect_ids
        ),
        "without_townhall_fixed_tasks": score_summary(materialized, excluded_task_ids=townhall_fixed),
        "overlay_task_ids": sorted(overlay_task_ids),
        "known_defect_task_ids": sorted(known_defect_ids),
        "type_mismatch_gold_task_ids": sorted(type_mismatch_gold),
        "townhall_fixed_task_ids": sorted(townhall_fixed),
    }


def date_sensitive_task_ids(tasks: Iterable[Any]) -> tuple[set[str], set[str]]:
    relative_dates = set()
    wall_clock = set()
    for task in tasks:
        relative_match = RELATIVE_DATE.search(task.user_prompt)
        if relative_match and not RELATIVE_DATE_EXAMPLE.search(
            task.user_prompt[max(0, relative_match.start() - 120) : relative_match.start()]
        ):
            relative_dates.add(task.task_id)
        verifier_text = "\n".join(
            str(verifier.get("validation_config", {}).get("query", "")) for verifier in task.verifiers
        )
        if WALL_CLOCK.search(verifier_text):
            wall_clock.add(task.task_id)
    return relative_dates, wall_clock


def _executor_effect(
    rows: list[dict[str, Any]],
    excluded_task_ids: set[str],
    configured_arms: tuple[str, ...] = ("none", "skills"),
) -> dict[str, Any]:
    selected = [
        row
        for row in rows
        if row.get("cell") == "executor"
        and row.get("task_id") not in excluded_task_ids
        and row.get("status") in {"valid", "policy_flagged"}
    ]
    arm_results = {}
    for arm in ("none", *treatment_arms(configured_arms)):
        arm_rows = [row for row in selected if row.get("arm") == arm]
        passed = sum(bool(row.get("reward")) for row in arm_rows)
        arm_results[arm] = {
            "n": len(arm_rows),
            "passed": passed,
            "p_hat": passed / len(arm_rows) if arm_rows else 0.0,
        }
    effects = {
        f"delta_{arm}": (
            arm_results[arm]["p_hat"] - arm_results["none"]["p_hat"]
            if {"none", arm}.issubset(configured_arms)
            else None
        )
        for arm in treatment_arms(configured_arms)
        if arm != "skills"
    }
    return {
        **arm_results,
        "delta": (
            arm_results["skills"]["p_hat"] - arm_results["none"]["p_hat"]
            if {"none", "skills"}.issubset(configured_arms)
            else None
        ),
        **effects,
    }


def treatment_arms(configured_arms: tuple[str, ...]) -> tuple[str, ...]:
    """The Skills arms of a run. The skills arm is always reported, so that reports keep their shape."""
    arms = ["skills"]
    arms.extend(arm for arm in configured_arms if arm not in {"none", "skills"})
    return tuple(arms)


def _percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _holm_adjust(p_values: list[float | None]) -> list[float | None]:
    valid = sorted(
        (float(value), index)
        for index, value in enumerate(p_values)
        if value is not None and math.isfinite(float(value))
    )
    adjusted: list[float | None] = [None] * len(p_values)
    running_max = 0.0
    total = len(valid)
    for rank, (value, index) in enumerate(valid):
        running_max = max(running_max, min(1.0, (total - rank) * value))
        adjusted[index] = running_max
    return adjusted


def paired_analysis(
    rows: Iterable[dict[str, Any]], *, seed: int = 20260519, treatment: str = "skills"
) -> list[dict[str, Any]]:
    selected = executor_cell_rows(rows)
    grouped: dict[tuple[str, str, str], dict[str, list[float]]] = {}
    for row in selected:
        if row.get("status") not in {"valid", "policy_flagged"} or row.get("arm") not in {"none", treatment}:
            continue
        key = (
            str(row.get("model", row.get("route", ""))),
            str(row.get("effort", "")),
            str(row["task_id"]),
        )
        grouped.setdefault(key, {}).setdefault(str(row["arm"]), []).append(float(bool(row.get("reward"))))
    by_cell: dict[tuple[str, str], list[float]] = {}
    for (model, effort, _task_id), arms in grouped.items():
        if "none" in arms and treatment in arms:
            by_cell.setdefault((model, effort), []).append(mean(arms[treatment]) - mean(arms["none"]))
    reports = []
    for (model, effort), differences in sorted(by_cell.items()):
        interval = None
        p_value = None
        t_statistic = None
        if differences:
            rng = random.Random(f"{seed}:{model}:{effort}")
            bootstrap = [mean([rng.choice(differences) for _ in differences]) for _ in range(10_000)]
            interval = [_percentile(bootstrap, 0.025), _percentile(bootstrap, 0.975)]
        if len(differences) >= 2:
            if all(value == differences[0] for value in differences):
                if differences[0] == 0:
                    t_statistic = 0.0
                    p_value = 1.0
                else:
                    p_value = min(1.0, 2 * 0.5 ** len(differences))
            else:
                test = ttest_1samp(differences, 0.0, alternative="two-sided")
                t_statistic = float(test.statistic)
                p_value = float(test.pvalue)
        report = {
            "model": model,
            "effort": effort,
            "treatment": treatment,
            "n": len(differences),
            "mean_delta": mean(differences) if differences else None,
            "t_statistic": t_statistic,
            "p_value": p_value,
            "bootstrap_95_ci": interval,
        }
        if len(differences) >= 2 and differences[0] != 0 and all(value == differences[0] for value in differences):
            report["note"] = "zero_variance"
        reports.append(report)
    adjusted = _holm_adjust([item["p_value"] for item in reports])
    for report, p_holm in zip(reports, adjusted, strict=True):
        report["p_value_holm"] = p_holm
    return reports


def date_sensitivity_report(
    rows: Iterable[dict[str, Any]],
    tasks: Iterable[Any],
    arms: tuple[str, ...] = ("none", "skills"),
) -> dict[str, Any]:
    materialized = list(rows)
    task_list = list(tasks)
    relative_dates, wall_clock = date_sensitive_task_ids(task_list)
    treatments = treatment_arms(arms)
    empty_skill_rows = [row for row in materialized if row.get("arm") in treatments and row.get("skills_count") == 0]
    extra_paired = {
        f"paired_analysis_{arm}": paired_analysis(materialized, treatment=arm) for arm in treatments if arm != "skills"
    }
    return {
        "all": _executor_effect(materialized, set(), arms),
        "without_relative_date_tasks": _executor_effect(materialized, relative_dates, arms),
        "without_wall_clock_tasks": _executor_effect(materialized, wall_clock, arms),
        "paired_analysis": paired_analysis(materialized),
        **extra_paired,
        "executor_task_ids": sorted({row["task_id"] for row in materialized if row.get("cell") == "executor"}),
        "skills_rows_without_family_skills": {"n": len(empty_skill_rows)},
        "relative_date_task_ids": sorted(relative_dates),
        "wall_clock_task_ids": sorted(wall_clock),
    }
