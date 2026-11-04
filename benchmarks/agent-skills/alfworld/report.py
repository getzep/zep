"""Summarize ALFWorld result files: success by split, arm, and task type, with paired CIs."""

from __future__ import annotations

import argparse
import json
import random
import statistics
from collections import defaultdict
from pathlib import Path


def load(path: str) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def by_trial(rows: list[dict], split: str) -> dict[tuple[str, int], int]:
    return {
        (r["game"], r["seed"]): int(r["won"])
        for r in rows
        if r["split"] == split and "error" not in r
    }


def paired_ci(
    a: dict[tuple[str, int], int], b: dict[tuple[str, int], int], n: int = 10000
) -> tuple[float, float, float] | None:
    games: dict[str, list[int]] = defaultdict(list)
    for key in sorted(set(a) & set(b)):
        games[key[0]].append(b[key] - a[key])
    diffs = [statistics.mean(v) for v in games.values()]
    if not diffs:
        return None
    rng = random.Random(0)
    boots = sorted(statistics.mean(rng.choices(diffs, k=len(diffs))) for _ in range(n))
    return (
        100 * statistics.mean(diffs),
        100 * boots[int(0.025 * n)],
        100 * boots[int(0.975 * n)],
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--treat", default="")
    args = parser.parse_args()
    files = {"base": load(args.base)}
    if args.treat:
        files["treat"] = load(args.treat)
    for split in ("valid_seen", "valid_unseen"):
        print(f"## {split}")
        for name, rows in files.items():
            rows = [r for r in rows if r["split"] == split]
            if not rows:
                continue
            seeds = defaultdict(list)
            for r in rows:
                seeds[r["seed"]].append(int(r.get("won", False)))
            per_seed = {s: round(100 * statistics.mean(v), 1) for s, v in sorted(seeds.items())}
            ok = [r for r in rows if "error" not in r]
            if not ok:
                print(f"{name}: n={len(rows)} errors={len(rows)}")
                continue
            calls = [c for r in ok for c in r["calls"]]
            print(
                f"{name}: n={len(rows)} success={100 * statistics.mean(int(r.get('won', False)) for r in rows):.1f}"
                f" seeds={per_seed} errors={sum('error' in r for r in rows)}"
                f" invalid_format={sum(r['invalid_format'] for r in ok)}"
                f" turns={statistics.mean(r['turns'] for r in ok):.1f}"
                f" prompt_tok/ep={statistics.mean(r['prompt_tokens'] for r in ok):.0f}"
                f" cached_tok/ep={statistics.mean(r.get('cached_tokens', 0) for r in ok):.0f}"
                f" completion_tok/ep={statistics.mean(r['completion_tokens'] for r in ok):.0f}"
                f" calls={len(calls)} retried={sum(r['retried_calls'] for r in ok)}"
                f" call_p50_s={statistics.median(c['latency_s'] for c in calls):.2f}"
            )
            kinds = defaultdict(list)
            for r in rows:
                kinds[r.get("task_type", "unknown")].append(int(r.get("won", False)))
            print(
                "   ",
                {
                    k: f"{100 * statistics.mean(v):.0f}% ({len(v)})"
                    for k, v in sorted(kinds.items())
                },
            )
        if "treat" in files:
            ci = paired_ci(by_trial(files["base"], split), by_trial(files["treat"], split))
            if ci is None:
                continue
            d, lo, hi = ci
            print(f"treat - base: {d:+.1f} pts, 95% paired bootstrap CI [{lo:+.1f}, {hi:+.1f}]")


if __name__ == "__main__":
    main()
