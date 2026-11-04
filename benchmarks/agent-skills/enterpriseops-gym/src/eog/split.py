from __future__ import annotations

import hashlib
import json
import random
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from .dataset import Task, task_list_hash

SPLIT_SEED = 20260519
TEACH_SHARE = 0.30
SHINGLE_JACCARD = 0.5


def _shingles(text: str) -> set[tuple[str, ...]]:
    words = re.findall(r"\w+", text.lower())
    if len(words) < 5:
        return {tuple(words)} if words else set()
    return {tuple(words[index : index + 5]) for index in range(len(words) - 4)}


def _clusters(tasks: tuple[Task, ...] | list[Task]) -> dict[str, str]:
    parent = {task.task_id: task.task_id for task in tasks}

    def find(item: str) -> str:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(left: str, right: str) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[max(root_left, root_right)] = min(root_left, root_right)

    def verifier_keys(task: Task) -> set[tuple[str, str]]:
        keys = set()
        for verifier in task.verifiers:
            validation = verifier.get("validation_config", {})
            query = " ".join(str(validation.get("query", "")).lower().split())
            expected = json.dumps(validation.get("expected_value"), sort_keys=True)
            keys.add((query, expected))
        return keys

    by_domain: dict[str, list[Task]] = defaultdict(list)
    for task in tasks:
        by_domain[task.domain].append(task)
    for domain_tasks in by_domain.values():
        shingles = {task.task_id: _shingles(task.user_prompt) for task in domain_tasks}
        verifier_sets = {task.task_id: verifier_keys(task) for task in domain_tasks}
        for index, left in enumerate(domain_tasks):
            for right in domain_tasks[index + 1 :]:
                left_prompt = left.user_prompt
                right_prompt = right.user_prompt
                left_shingles = shingles[left.task_id]
                right_shingles = shingles[right.task_id]
                left_verifiers = verifier_sets[left.task_id]
                right_verifiers = verifier_sets[right.task_id]
                prompt_match = left_prompt == right_prompt
                shingle_match = (
                    bool(left_shingles and right_shingles)
                    and len(left_shingles & right_shingles) / len(left_shingles | right_shingles) >= SHINGLE_JACCARD
                )
                verifier_overlap = len(left_verifiers & right_verifiers)
                verifier_match = (
                    bool(verifier_overlap)
                    and verifier_overlap / min(len(left_verifiers), len(right_verifiers)) >= SHINGLE_JACCARD
                )
                if prompt_match or shingle_match or verifier_match:
                    union(left.task_id, right.task_id)
    roots = {task.task_id: find(task.task_id) for task in tasks}
    return {task_id: hashlib.sha256(root.encode()).hexdigest()[:16] for task_id, root in roots.items()}


def make_split(
    tasks: tuple[Task, ...] | list[Task],
    *,
    seed: int = SPLIT_SEED,
    teach_share: float = TEACH_SHARE,
) -> dict[str, Any]:
    clusters = _clusters(tasks)
    members: dict[str, list[Task]] = defaultdict(list)
    for task in tasks:
        members[clusters[task.task_id]].append(task)
    rng = random.Random(seed)
    ordered = list(members.items())
    rng.shuffle(ordered)
    totals: dict[str, int] = defaultdict(int)
    selected: dict[str, int] = defaultdict(int)
    for task in tasks:
        totals[task.domain] += 1
    sides: dict[str, str] = {}
    for cluster_id, group in ordered:
        domains = defaultdict(int)
        for task in group:
            domains[task.domain] += 1
        error_teach = sum(max(0, selected[d] + n - totals[d] * teach_share) for d, n in domains.items())
        error_score = sum(abs(selected[d] - totals[d] * teach_share) for d in domains)
        side = "teach" if error_teach <= error_score else "score"
        sides[cluster_id] = side
        if side == "teach":
            for domain, count in domains.items():
                selected[domain] += count
    return {
        "seed": seed,
        "teach_share": teach_share,
        "task_list_sha256": task_list_hash(tasks),
        "tasks": {
            task.task_id: {"cluster": clusters[task.task_id], "side": sides[clusters[task.task_id]]} for task in tasks
        },
    }


def write_split(tasks: tuple[Task, ...] | list[Task], path: Path) -> dict[str, Any]:
    split = make_split(tasks)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(split, indent=2, sort_keys=True) + "\n")
    return split


def assert_skill_sources(
    skill_lineage: Any,
    split: dict[str, Any],
    trace_tasks: dict[str, dict[str, Any]] | None = None,
    *,
    task_scope: str = "split",
) -> list[str]:
    trace_ids: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"trace_uuid", "trajectory_id"} and isinstance(item, str):
                    trace_ids.add(item)
                else:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(skill_lineage)
    if not trace_ids:
        raise ValueError("Skill has no source trace IDs")
    if trace_tasks is None:
        raise ValueError("Teacher trace mapping is required to validate Skill sources")
    invalid = []
    for trace_id in sorted(trace_ids):
        source = trace_tasks.get(trace_id)
        if not source:
            invalid.append(trace_id)
            continue
        task_id = source.get("task_id")
        split_side = split["tasks"].get(task_id, {}).get("side")
        if task_scope == "all":
            if source.get("side") not in {"teach", "score"} or source.get("side") != split_side:
                invalid.append(trace_id)
        elif source.get("side") != "teach" or split_side != "teach":
            invalid.append(trace_id)
    if invalid:
        if task_scope == "all":
            raise ValueError(f"Skills must use traces with sides assigned by the split: {invalid}")
        raise ValueError(f"Skills must use teach-side traces assigned to teach tasks only: {invalid}")
    return sorted(trace_ids)
