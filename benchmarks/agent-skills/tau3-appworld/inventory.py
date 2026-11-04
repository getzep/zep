"""Collect compilation, admission, and Skill inventory for a replay memory Agent."""

from __future__ import annotations

import argparse
import json
import time
from itertools import islice
from pathlib import Path
from typing import Any

from zep_cloud import Zep

from compile import dump_skills
from config import load_config, zep_client
from zep_capture import model_dict, skill_uuid

TERMINAL = {"produced", "no_change", "rejected", "failed", "skipped", "cancelled"}


def runs(client: Zep, agent_uuid: str, family: str) -> list[dict[str, Any]]:
    return [
        model_dict(item)
        for item in islice(
            client.agent.learning.list_runs(agent_uuid, task_family=family, page_size=100),
            100,
        )
    ]


def settled(items: list[dict[str, Any]]) -> bool:
    return bool(items) and all(
        str(item.get("decision") or item.get("status") or "") in TERMINAL for item in items
    )


def search_hit(client: Zep, agent_uuid: str, skill: dict[str, Any]) -> dict[str, Any]:
    query = str(skill.get("package_name") or skill.get("task_family") or "")
    for item in islice(client.agent.skill.search(agent_uuid, query=query, page_size=20), 20):
        hit = model_dict(item)
        if str(hit.get("skill_uuid") or hit.get("uuid")) == str(skill.get("uuid")):
            return hit
    return {}


def inventory(client: Zep, agent_uuid: str, family: str) -> dict[str, Any]:
    skills = dump_skills(client, agent_uuid, family)
    details = []
    for skill in skills:
        full = model_dict(client.agent.skill.get(agent_uuid, str(skill["uuid"])))
        hit = search_hit(client, agent_uuid, full)
        details.append(
            {key: full.get(key) for key in ("uuid", "package_name", "status", "current_version")}
            | {
                key: hit.get(key)
                for key in (
                    "maturity",
                    "source_attempt_families",
                    "trust",
                    "application_trials",
                    "application_successes",
                )
            }
            | {"markdown": skill.get("markdown")}
        )
    candidates = []
    for item in client.agent.skill.candidate.list(agent_uuid):
        summary = model_dict(item)
        if summary.get("task_family") not in (None, family):
            continue
        review_id = skill_uuid(summary)
        review = (
            model_dict(client.agent.skill.candidate.get(agent_uuid, review_id))
            if review_id
            else summary
        )
        candidates.append(
            {
                key: review.get(key)
                for key in ("uuid", "status", "reason_code", "candidate_count", "task_family")
            }
        )
    return {"runs": runs(client, agent_uuid, family), "skills": details, "candidates": candidates}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent-uuid", required=True)
    parser.add_argument("--families", nargs="+", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--timeout", type=float, default=3600)
    parser.add_argument("--quiet-seconds", type=float, default=600)
    args = parser.parse_args()
    config = load_config()
    client = zep_client(config.api_key, config.api_url)
    deadline = time.monotonic() + args.timeout
    last_count = -1
    quiet_since = time.monotonic()
    while time.monotonic() < deadline:
        current = [runs(client, args.agent_uuid, family) for family in args.families]
        count = sum(len(items) for items in current)
        if count != last_count:
            last_count = count
            quiet_since = time.monotonic()
        if (
            all(settled(items) for items in current)
            and time.monotonic() - quiet_since >= args.quiet_seconds
        ):
            break
        time.sleep(30)
    result = {family: inventory(client, args.agent_uuid, family) for family in args.families}
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True, default=str) + "\n")
    print(
        json.dumps(
            {
                family: {
                    "runs": [
                        (r.get("decision"), r.get("status"), r.get("rejected_add_count"))
                        for r in data["runs"]
                    ],
                    "skills": [
                        (s["status"], s["maturity"], s["source_attempt_families"], s["trust"])
                        for s in data["skills"]
                    ],
                    "candidates": [(c["status"], c["reason_code"]) for c in data["candidates"]],
                }
                for family, data in result.items()
            },
            indent=1,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
