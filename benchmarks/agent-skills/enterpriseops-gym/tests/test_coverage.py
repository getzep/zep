from __future__ import annotations

from eog.coverage import coverage_flags, coverage_record, skill_tools
from eog.dataset import load_tasks


def _skill(skill_id: str, family: str, tools: list[str]) -> dict[str, str]:
    lines = "\n".join(f"- gym-calendar__{tool}; version: *" for tool in tools)
    return {
        "id": skill_id,
        "task_family": family,
        "markdown": (
            f"---\nname: {skill_id}\ndescription: d\n---\n\n## Procedure\n\n1. Go.\n\n"
            f"## Tools\n\n{lines}\n- finish; version: *\n"
        ),
    }


def test_skill_tools_strips_the_server_prefix_and_finish() -> None:
    assert skill_tools(_skill("s", "eog.calendar", ["list_events", "patch_event"])["markdown"]) == {
        "list_events",
        "patch_event",
    }
    assert skill_tools("# No tools section\n") == frozenset()
    assert skill_tools("## Tools\n\n- finish; version: *\n") == frozenset()


def test_coverage_record_applies_r2_and_r3_within_the_index_families() -> None:
    task = next(t for t in load_tasks() if t.domain == "calendar" and len(t.selected_tools) >= 3)
    a, b, c = task.selected_tools[:3]
    skills = [
        _skill("subset", "eog.calendar", [a, b]),
        _skill("half", "eog.calendar", [c, "not_a_task_tool"]),
        _skill("third", "eog.calendar", [a, "x", "y"]),
        _skill("other-family", "eog.email", [a, b]),
        _skill("empty", "eog.calendar", []),
    ]
    record = coverage_record(task, skills, ("eog.calendar",))
    assert record["covered"] is True and record["covered_skills"] == ["subset"]
    assert record["covered_half"] is True and record["covered_half_skills"] == ["subset", "half"]
    uncovered = coverage_record(task, skills[1:], ("eog.calendar",))
    assert uncovered["covered"] is False and uncovered["covered_half"] is True
    none = coverage_record(task, skills[2:], ("eog.calendar",))
    assert none == {
        "task_id": task.task_id,
        "families": ["eog.calendar"],
        "covered": False,
        "covered_half": False,
        "covered_skills": [],
        "covered_half_skills": [],
    }


def test_coverage_flags_uses_the_index_families_of_each_task() -> None:
    all_tasks = load_tasks()
    tasks = [t for t in all_tasks if t.domain == "calendar"][:3] + [t for t in all_tasks if t.domain == "hybrid"][:3]
    hybrid = next(t for t in tasks if t.domain == "hybrid")
    tool = hybrid.selected_tools[0]
    flags = coverage_flags(tasks, [_skill("h", "eog.hybrid", [tool])])
    assert set(flags) == {t.task_id for t in tasks}
    assert flags[hybrid.task_id]["covered"] is True
    assert "eog.hybrid" in flags[hybrid.task_id]["families"]
    assert all(flags[t.task_id]["covered"] is False for t in tasks if t.domain == "calendar")


def test_coverage_flags_script_records_the_executor_manifest_hash(tmp_path) -> None:
    import json
    import subprocess
    import sys
    from pathlib import Path

    from eog.skills import load_snapshot_details

    snapshot = Path("results/recompile/arm-b-resumed/skills")
    if not (snapshot / "snapshot.json").exists():
        import pytest

        pytest.skip("the arm-b-resumed snapshot is not checked out")
    out = tmp_path / "coverage.json"
    subprocess.run(
        [sys.executable, "scripts/coverage_flags.py", "--snapshot", str(snapshot), "--out", str(out)],
        check=True,
        capture_output=True,
    )
    record = json.loads(out.read_text())
    assert record["skills_snapshot_sha256"] == load_snapshot_details(snapshot / "snapshot.json")[1]
    assert record["skills"] == 67 and len(record["tasks"]) == 649
