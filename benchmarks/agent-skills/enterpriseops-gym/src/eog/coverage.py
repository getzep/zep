"""Deterministic coverage flags: does the family index hold a Skill whose tool set fits a score task?

A Skill covers a task (flag `covered`, rule R2) when the Skill belongs to one of the task's index families
and every tool of its `## Tools` section is in the task's `selected_tools`. The looser flag `covered_half`
(rule R3) needs at least half of the Skill's tools in `selected_tools`.

The flags read the tool sets only. They say that the index holds a skill whose tools fit the task; they do not
judge whether the skill fits the task.
"""

from __future__ import annotations

import re
from typing import Any

TOOLS_SECTION = re.compile(r"^## Tools\n(.*?)(?=^## |\Z)", re.S | re.M)
TOOL_LINE = re.compile(r"^- (?P<tool>[A-Za-z0-9_.-]+)(?:;.*)?$")
FINISH_TOOL = "finish"


def skill_tools(markdown: str) -> frozenset[str]:
    """The tool names of the `## Tools` section, without the server prefix and without `finish`."""
    section = TOOLS_SECTION.search(markdown)
    if section is None:
        return frozenset()
    tools = set()
    for line in section.group(1).splitlines():
        match = TOOL_LINE.match(line.strip())
        if match is None:
            continue
        name = match.group("tool").split("__")[-1]
        if name != FINISH_TOOL:
            tools.add(name)
    return frozenset(tools)


def coverage_record(task: Any, skills: list[dict[str, Any]], families: tuple[str, ...] | set[str]) -> dict[str, Any]:
    """The coverage flags of one task against the Skills of its index families."""
    selected = set(task.selected_tools)
    covered: list[str] = []
    covered_half: list[str] = []
    for skill in skills:
        if skill["task_family"] not in families:
            continue
        tools = skill_tools(skill["markdown"])
        if not tools:
            continue
        shared = len(tools & selected)
        if shared == len(tools):
            covered.append(str(skill["id"]))
        if shared * 2 >= len(tools):
            covered_half.append(str(skill["id"]))
    return {
        "task_id": task.task_id,
        "families": sorted(families),
        "covered": bool(covered),
        "covered_half": bool(covered_half),
        "covered_skills": covered,
        "covered_half_skills": covered_half,
    }


def coverage_flags(tasks: list[Any] | tuple[Any, ...], skills: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    from eog.run import _task_families

    return {task.task_id: coverage_record(task, skills, _task_families(task)) for task in tasks}
