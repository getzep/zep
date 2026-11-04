"""Write the deterministic coverage flags of every task against a Skill snapshot.

Usage: uv run python scripts/coverage_flags.py --snapshot results/recompile/arm-b-resumed/skills \
    --out results/matrix/coverage.json [--rows results/matrix/none-gpt-6-luna-low/rows.jsonl]

The `--rows` file limits the printed counts to the tasks of that cell. The JSON holds every task and
`skills_snapshot_sha256` (the same hash as the executor manifest field of that name), so that
scripts/matrix_report.py applies the flags only to cells that ran on that snapshot.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eog.coverage import coverage_flags
from eog.dataset import load_tasks
from eog.skills import load_snapshot_details


def load_snapshot_skills(snapshot_dir: Path) -> list[dict[str, str]]:
    snapshot = json.loads((snapshot_dir / "snapshot.json").read_text())
    return [
        {
            "id": str(skill["skill_id"]),
            "task_family": str(skill["task_family"]),
            "markdown": (snapshot_dir / skill["path"]).read_text(),
        }
        for skill in snapshot["skills"]
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True, help="Directory with snapshot.json and the Skill files")
    parser.add_argument("--out", type=Path, default=Path("results/matrix/coverage.json"))
    parser.add_argument("--rows", type=Path, help="A rows.jsonl whose task IDs limit the printed counts")
    args = parser.parse_args()
    skills = load_snapshot_skills(args.snapshot)
    flags = coverage_flags(load_tasks(), skills)
    task_ids = set(flags)
    if args.rows:
        task_ids = {json.loads(line)["task_id"] for line in args.rows.read_text().splitlines() if line.strip()}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    _, skills_snapshot_sha256, _ = load_snapshot_details(args.snapshot / "snapshot.json")
    args.out.write_text(
        json.dumps(
            {
                "snapshot": str(args.snapshot),
                "snapshot_file_sha256": hashlib.sha256((args.snapshot / "snapshot.json").read_bytes()).hexdigest(),
                "skills_snapshot_sha256": skills_snapshot_sha256,
                "skills": len(skills),
                "tasks": flags,
            },
            indent=2,
        )
    )
    n = len(task_ids)
    covered = sum(1 for t in task_ids if flags[t]["covered"])
    half = sum(1 for t in task_ids if flags[t]["covered_half"])
    print(f"tasks {n}; covered (R2) {covered}; covered_half (R3) {half}; written {args.out}")


if __name__ == "__main__":
    main()
