from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import DATASET_REVISION, OVERLAY_COMMIT

DATASET_ID = "ServiceNow-AI/EnterpriseOps-Gym"
KNOWN_DEFECT_SUFFIXES = ("cd2083a8", "8e21a82f")
CONTEXT_TOKEN_NOT_IN_SEED_TASK_IDS = frozenset(
    {
        "task_20251205_032409_945_0a0bf089_61f5be29",
        "task_20260112_125407_584_1628b966_f5dd38b6",
    }
)
MISSING_SELECTED_TOOL_TASK_IDS = frozenset({"task_20251128_120055_076_8e9e30d7_e5dcfa22"})
TOWNHALL_FIXED_TASK_IDS = frozenset(
    {
        "task_20251121_102744_757_7ebc1127_dadb0c94",
        "task_20251125_113335_636_7ebc1127_a4ebffe8",
        "task_20251205_150335_071_464ee3e0_19d263cd",
        "task_20251205_192805_864_464ee3e0_91f2c02f",
        "task_20260106_104903_639_0154326e_81f6a68a",
        "task_20260108_172650_003_8e9e30d7_a1ff8588",
        "task_20260108_194921_186_8e9e30d7_a087d6f8",
        "task_20260109_004703_994_7ebc1127_6f97f3e9",
        "task_20260114_164939_471_4d9df647_2aaff95f",
    }
)
TYPE_MISMATCH_GOLD_TASK_IDS = frozenset(
    {
        "task_20260106_071510_179_911d75d7_0ae789ae",
        "task_20260107_162554_472_accab84d_41d2e871",
        "task_20260107_181520_716_64a4ebb9_ec0278c5",
        "task_20260108_041838_219_df0ecdeb_3f8c0642",
        "task_20260108_160928_699_391f1eff_905701cb",
        "task_20260108_165812_020_4d9df647_eeacd121",
        "task_20260109_155926_434_df0ecdeb_405562a2",
        "task_20260113_152354_165_9a1e131b_fc5e2cac",
        "task_20260114_124106_138_1628b966_ddc2587a",
        "task_20260115_154856_907_1628b966_275e920f",
        "task_20260116_080150_885_1628b966_5a58d8e3",
        "task_20260116_125857_450_1628b966_25b7eec5",
        "task_20260117_084409_361_4d9df647_9892db8c",
        "task_20260121_143201_590_1afb323a_5a03bf03",
        "task_20260121_144251_116_38405923_9438dabd",
        "task_20260121_145409_648_1afb323a_4e203caa",
        "task_20260121_150447_596_1afb323a_0a1e4085",
        "task_20260121_161936_323_cbcf38f8_50ccd164",
        "task_20260121_163208_054_1afb323a_7f40ee66",
    }
)
LOCAL_DATA = Path.home() / ".cache" / "enterpriseops-gym" / "data" / "oracle"


@dataclass(frozen=True, slots=True)
class Task:
    task_id: str
    domain: str
    system_prompt: str
    user_prompt: str
    selected_tools: tuple[str, ...]
    restricted_tools: tuple[str, ...]
    mcp_endpoint: str
    number_of_runs: int
    reset_database_between_runs: bool
    gym_servers_config: tuple[dict[str, Any], ...]
    verifiers: tuple[dict[str, Any], ...]
    context_normalizations: tuple[dict[str, str], ...] = ()
    known_defect: bool = False
    known_defect_reasons: tuple[str, ...] = ()
    type_mismatch_gold: bool = False


def validate_verifier_only_overlay(
    original_tasks: tuple[Task, ...],
    corrected_tasks: tuple[Task, ...],
) -> None:
    original_by_id = {task.task_id: task for task in original_tasks}
    corrected_by_id = {task.task_id: task for task in corrected_tasks}
    if original_by_id.keys() != corrected_by_id.keys():
        raise ValueError("The corrected overlay task set differs from the original task set.")

    for task_id in sorted(original_by_id):
        original_fields = asdict(original_by_id[task_id])
        corrected_fields = asdict(corrected_by_id[task_id])
        original_fields.pop("verifiers")
        corrected_fields.pop("verifiers")
        changed = [name for name in original_fields if original_fields[name] != corrected_fields[name]]
        if changed:
            names = ", ".join(changed)
            raise ValueError(f"The corrected overlay changes non-verifier fields for task {task_id}: {names}.")


def _decode(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _rows_from_parquet(directory: Path) -> list[dict[str, Any]]:
    import pyarrow.parquet as pq

    rows: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.parquet")):
        rows.extend(pq.read_table(path).to_pylist())
    return rows


def _rows_from_hub() -> list[dict[str, Any]]:
    from datasets import load_dataset

    dataset = load_dataset(DATASET_ID, "oracle", revision=DATASET_REVISION)
    return [row for split in dataset.values() for row in split]


def _overlay_files(directory: Path) -> dict[str, dict[str, Any]]:
    overlays: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.rglob("*.json")):
        payload = json.loads(path.read_text())
        task_id = str(payload.get("task_id", path.stem))
        payload["task_id"] = task_id
        overlays[task_id] = payload
    return overlays


def _normalize_context(configs: list[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    changes: list[dict[str, Any]] = []
    for server in configs:
        context = server.get("context", {})
        normalized: dict[str, Any] = {}
        for key, value in context.items():
            cleaned = key.strip()
            cleaned = re.sub(r"^x-\s*x-", "x-", cleaned, flags=re.IGNORECASE)
            normalized[cleaned] = value
            if cleaned != key:
                changes.append({"server": server.get("mcp_server_name", ""), "from": key, "to": cleaned})
        server["context"] = normalized
    return tuple(changes)


def _task(row: dict[str, Any]) -> Task:
    task_id = str(row["task_id"])
    verifier_schema_defect = any(task_id.endswith(suffix) for suffix in KNOWN_DEFECT_SUFFIXES)
    type_mismatch_gold = task_id in TYPE_MISMATCH_GOLD_TASK_IDS
    context_token_not_in_seed = task_id in CONTEXT_TOKEN_NOT_IN_SEED_TASK_IDS
    missing_selected_tool = task_id in MISSING_SELECTED_TOOL_TASK_IDS
    known_defect = verifier_schema_defect or type_mismatch_gold or context_token_not_in_seed or missing_selected_tool
    reasons = tuple(
        reason
        for condition, reason in (
            (verifier_schema_defect, "verifier_schema"),
            (type_mismatch_gold, "type_mismatch_gold"),
            (context_token_not_in_seed, "context_token_not_in_seed"),
            (missing_selected_tool, "selected_tool_missing"),
        )
        if condition
    )
    servers = list(_decode(row["gym_servers_config"]) or [])
    normalizations = _normalize_context(servers)
    return Task(
        task_id=task_id,
        domain=str(row["domain"]),
        system_prompt=str(row.get("system_prompt") or ""),
        user_prompt=str(row.get("user_prompt") or ""),
        selected_tools=tuple(_decode(row.get("selected_tools")) or ()),
        restricted_tools=tuple(_decode(row.get("restricted_tools")) or ()),
        mcp_endpoint=str(row.get("mcp_endpoint") or "/mcp"),
        number_of_runs=int(row.get("number_of_runs") or 1),
        reset_database_between_runs=bool(row.get("reset_database_between_runs", True)),
        gym_servers_config=tuple(servers),
        verifiers=tuple(_decode(row.get("verifiers")) or ()),
        context_normalizations=normalizations,
        known_defect=known_defect,
        known_defect_reasons=reasons,
        type_mismatch_gold=type_mismatch_gold,
    )


def load_tasks(
    *,
    data_dir: Path | None = None,
    overlay_dir: Path | None = None,
    revised_dir: Path | None = None,
) -> tuple[Task, ...]:
    local_data = data_dir or LOCAL_DATA
    rows = _rows_from_parquet(local_data) if local_data.exists() else _rows_from_hub()
    default_revised_dir = Path(__file__).parents[2] / "data" / "revised"
    revised_overlays = _overlay_files(revised_dir or default_revised_dir)
    overlays = _overlay_files(overlay_dir) if overlay_dir is not None else {}
    by_id = {str(row["task_id"]): row for row in rows}
    tasks = []
    for task_id, row in by_id.items():
        tasks.append(_task({**row, **revised_overlays.get(task_id, {}), **overlays.get(task_id, {})}))
    return tuple(sorted(tasks, key=lambda task: task.task_id))


def task_list_hash(tasks: tuple[Task, ...] | list[Task]) -> str:
    payload = json.dumps([task.task_id for task in tasks], separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def overlay_task_ids(
    tasks: tuple[Task, ...] | list[Task],
    *,
    overlay_dir: Path | None = None,
    include_unmatched: bool = False,
) -> list[str]:
    overlays = set(_overlay_files(overlay_dir or Path(__file__).parents[2] / "data" / "revised"))
    if include_unmatched:
        return sorted(overlays)
    return sorted(overlays & {task.task_id for task in tasks})


def manifest(tasks: tuple[Task, ...] | list[Task]) -> dict[str, Any]:
    overlays = _overlay_files(Path(__file__).parents[2] / "data" / "revised")
    task_ids = {task.task_id for task in tasks}
    return {
        "dataset_id": DATASET_ID,
        "dataset_revision": DATASET_REVISION,
        "overlay_commit": OVERLAY_COMMIT,
        "unmatched_overlay_task_ids": sorted(set(overlays) - task_ids),
        "task_count": len(tasks),
        "task_list_sha256": task_list_hash(tasks),
    }


def task_to_dict(task: Task) -> dict[str, Any]:
    return asdict(task)
