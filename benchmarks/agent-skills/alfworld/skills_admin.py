"""Create the ALFWorld Agent, wait for compiled Skills, and write skills.json."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from itertools import islice
from pathlib import Path
from typing import Any

from zep_cloud import Zep

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tau3-appworld"))
from zep_capture import model_dict, skill_uuid, value

FAMILIES = [
    "pick_and_place_simple",
    "look_at_obj_in_light",
    "pick_clean_then_place_in_recep",
    "pick_heat_then_place_in_recep",
    "pick_cool_then_place_in_recep",
    "pick_two_obj_and_place",
]


def client() -> Zep:
    options: dict[str, Any] = {"api_key": os.environ["ZEP_API_KEY"]}
    if base_url := os.getenv("ZEP_BASE_URL"):
        root = base_url.rstrip("/").removesuffix("/api/v4")
        options["base_url"] = root + "/api/v4"
    return Zep(**options)


def create(args: argparse.Namespace) -> None:
    agent = client().agent.create(
        agent_id=f"alfworld-skills-{args.run_id}",
        name=f"alfworld-skills-{args.run_id}",
        security_domain="alfworld-skills",
        version="1.0.0",
        memory_settings={
            "compilation_mode": "scheduled",
            "approval": "auto",
            "auto_approval_task_families": FAMILIES,
        },
    )
    print(json.dumps({"agent_uuid": str(value(agent, "uuid") or value(agent, "uuid_"))}))


def _markdown(c: Zep, agent_uuid: str, skill: Any) -> str:
    if value(skill, "markdown"):
        return str(value(skill, "markdown"))
    versions = list(
        islice(
            c.agent.skill.version.list(agent_uuid, skill_uuid(skill), page_size=100),
            100,
        )
    )
    current = value(skill, "current_version")
    for item in versions:
        if value(item, "version") == current:
            return str(value(item, "markdown") or "")
    return str(value(versions[-1], "markdown") or "") if versions else ""


def status(args: argparse.Namespace) -> None:
    c = client()
    for family in FAMILIES:
        runs = [
            model_dict(r)
            for r in islice(
                c.agent.learning.list_runs(args.agent_uuid, task_family=family, page_size=100),
                100,
            )
        ]
        skills = list(
            islice(
                c.agent.skill.list(args.agent_uuid, task_family=family, page_size=20),
                20,
            )
        )
        decisions = [str(r.get("decision")) for r in runs]
        print(family, f"runs={len(runs)}", f"skills={len(skills)}", decisions[-5:])


def dump(args: argparse.Namespace) -> None:
    c = client()
    out: dict[str, list[dict[str, Any]]] = {}
    for family in FAMILIES:
        rows = []
        for hit in islice(
            c.agent.skill.list(args.agent_uuid, task_family=family, page_size=20), 20
        ):
            skill = c.agent.skill.get(args.agent_uuid, skill_uuid(hit))
            rows.append(
                {
                    "uuid": skill_uuid(hit),
                    "name": value(skill, "name") or value(hit, "name"),
                    "status": value(skill, "status") or value(hit, "status"),
                    "current_version": value(skill, "current_version"),
                    "markdown": _markdown(c, args.agent_uuid, skill),
                }
            )
        out[family] = rows
    Path(args.out).write_text(json.dumps(out, indent=2) + "\n")
    print({k: len(v) for k, v in out.items()})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["create", "status", "dump"])
    parser.add_argument("--run-id", default=time.strftime("%Y%m%d%H%M"))
    parser.add_argument("--agent-uuid", default="")
    parser.add_argument("--out", default="skills.json")
    args = parser.parse_args()
    {"create": create, "status": status, "dump": dump}[args.command](args)


if __name__ == "__main__":
    main()
