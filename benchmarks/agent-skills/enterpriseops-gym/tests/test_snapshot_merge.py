from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from eog.skills import compile_snapshot
from eog.snapshot_merge import merge_compile_outputs


def _skills_json_compile(root: Path) -> tuple[Path, Path, list[dict[str, str]], dict[str, dict[str, Any]]]:
    skills_dir = root / "skills"
    skills = []
    for family, skill_id in (("eog.calendar", "calendar-skill"), ("eog.csm", "csm-skill")):
        path = f"{family.replace('.', '_')}/{skill_id}.md"
        content = f"Skill for {family}."
        (skills_dir / path).parent.mkdir(parents=True, exist_ok=True)
        (skills_dir / path).write_text(content)
        skills.append(
            {
                "skill_id": skill_id,
                "task_family": family,
                "path": path,
                "sha256": hashlib.sha256(content.encode()).hexdigest(),
            }
        )

    reports = {
        "eog.calendar": {"failures": [], "compile_outcomes": [{"decision": "covered"}]},
        "eog.csm": {
            "failures": [{"error": "compile_wait_timeout"}],
            "compile_outcomes": [{"decision": "failed"}],
        },
    }
    for family, report in reports.items():
        report_path = skills_dir / family.replace(".", "_") / "compile_report.json"
        report_path.write_text(json.dumps(report))

    (root / "agent.json").write_text(json.dumps({"agent_uuid": "source-agent"}))
    (skills_dir / "skills.json").write_text(
        json.dumps(
            {
                "skills": skills,
                "sha256": hashlib.sha256(json.dumps(skills, sort_keys=True).encode()).hexdigest(),
                "compile_failures": [{"task_family": "eog.csm", "error": "compile_wait_timeout"}],
            }
        )
    )
    return root, skills_dir, skills, reports


def test_merge_matches_compile_snapshot_hash_and_records_family_provenance(tmp_path: Path) -> None:
    compile_root = tmp_path / "source-compile"
    skills_dir = compile_root / "skills"
    trace_path = compile_root / "traces.jsonl"
    trace_path.parent.mkdir(parents=True)
    trace_path.write_text(
        json.dumps(
            {
                "trace_uuid": "teacher-trace",
                "task_id": "teacher-task",
                "side": "teach",
                "domain": "calendar",
                "task_family": "eog.calendar",
                "passed": True,
            }
        )
        + "\n"
    )

    class FakeSkill:
        skill_id = "calendar-skill"
        markdown = "---\nname: Calendar\n---\nUse the calendar procedure."

        def model_dump(self, **kwargs: Any) -> dict[str, str]:
            return {"skill_id": self.skill_id, "task_family": "eog.calendar", "markdown": self.markdown}

    client = SimpleNamespace(
        agent=SimpleNamespace(
            learning=SimpleNamespace(
                get=lambda agent_uuid, *, task_family: SimpleNamespace(
                    model_dump=lambda **kwargs: {"compilation_pending": False}
                ),
                list_runs=lambda *args, **kwargs: [],
            ),
            skill=SimpleNamespace(
                list=lambda *args, **kwargs: [FakeSkill()],
                evidence=SimpleNamespace(
                    list=lambda *args, **kwargs: [
                        SimpleNamespace(model_dump=lambda **kwargs: {"trace_uuid": "teacher-trace"})
                    ]
                ),
            ),
        )
    )
    compile_hash = compile_snapshot(
        client,
        "source-agent",
        ["eog.calendar"],
        {"tasks": {"teacher-task": {"side": "teach"}}},
        skills_dir,
        traces_path=trace_path,
    )["sha256"]
    skills_index_path = skills_dir / "skills.json"
    skills_index = json.loads(skills_index_path.read_text())
    skills_index["skills"] = []
    skills_index["sha256"] = hashlib.sha256(json.dumps([], sort_keys=True).encode()).hexdigest()
    skills_index_path.write_text(json.dumps(skills_index))
    (compile_root / "agent.json").write_text(json.dumps({"agent_uuid": "source-agent"}))
    (compile_root / "manifest.compile.json").write_text(json.dumps({"development_commit": "a" * 40}))

    merged_snapshot, provenance = merge_compile_outputs(
        {"eog.calendar": (compile_root, None)},
        ["eog.calendar"],
        tmp_path / "merged" / "skills",
    )

    assert merged_snapshot["sha256"] == compile_hash
    assert hashlib.sha256(json.dumps(merged_snapshot["skills"], sort_keys=True).encode()).hexdigest() == compile_hash
    assert json.loads((tmp_path / "merged/skills/snapshot.json").read_text()) == merged_snapshot
    assert provenance["eog.calendar"] == {
        "source_compile_dir": str(compile_root.resolve()),
        "agent_uuid": "source-agent",
        "development_commit": "a" * 40,
    }
    assert (tmp_path / "merged/skills/eog_calendar/compile_report.json").is_file()


def test_merge_reads_skills_json_and_ignores_failures_in_other_families(tmp_path: Path) -> None:
    compile_root, skills_dir, source_skills, reports = _skills_json_compile(tmp_path / "source-compile")
    output_dir = tmp_path / "merged" / "skills"

    merged_snapshot, _ = merge_compile_outputs(
        {"eog.calendar": (skills_dir, "c" * 40)},
        ["eog.calendar"],
        output_dir,
    )

    selected_skills = [skill for skill in source_skills if skill["task_family"] == "eog.calendar"]
    expected_hash = hashlib.sha256(json.dumps(selected_skills, sort_keys=True).encode()).hexdigest()
    assert merged_snapshot["skills"] == selected_skills
    assert merged_snapshot["sha256"] == expected_hash
    assert json.loads((output_dir / "eog_calendar/compile_report.json").read_text()) == reports["eog.calendar"]
    assert (output_dir / selected_skills[0]["path"]).is_file()
    assert not (compile_root / "skills/snapshot.json").exists()


def test_merge_rejects_failed_family_from_skills_json(tmp_path: Path) -> None:
    compile_root, _, _, _ = _skills_json_compile(tmp_path / "source-compile")

    with pytest.raises(ValueError, match="failed outcome or compile wait timeout"):
        merge_compile_outputs(
            {"eog.csm": (compile_root, "c" * 40)},
            ["eog.csm"],
            tmp_path / "merged" / "skills",
        )


def test_merge_rejects_skills_json_with_mismatched_hash(tmp_path: Path) -> None:
    _, skills_dir, _, _ = _skills_json_compile(tmp_path / "source-compile")
    skills_path = skills_dir / "skills.json"
    source_index = json.loads(skills_path.read_text())
    source_index["sha256"] = "0" * 64
    skills_path.write_text(json.dumps(source_index))

    with pytest.raises(ValueError, match="source snapshot hash does not match its Skills"):
        merge_compile_outputs(
            {"eog.calendar": (skills_dir, "c" * 40)},
            ["eog.calendar"],
            tmp_path / "merged" / "skills",
        )


def test_merge_refuses_family_with_a_failed_compile_outcome(tmp_path: Path) -> None:
    compile_root = tmp_path / "failed-compile"
    skills_dir = compile_root / "skills"
    report_dir = skills_dir / "eog_csm"
    report_dir.mkdir(parents=True)
    (compile_root / "agent.json").write_text(json.dumps({"agent_uuid": "failed-agent"}))
    (compile_root / "manifest.compile.json").write_text(json.dumps({"development_commit": "b" * 40}))
    empty_hash = hashlib.sha256(json.dumps([], sort_keys=True).encode()).hexdigest()
    (skills_dir / "snapshot.json").write_text(json.dumps({"skills": [], "sha256": empty_hash}))
    (report_dir / "compile_report.json").write_text(
        json.dumps(
            {
                "failures": [{"error": "compile_failed"}],
                "compile_outcomes": [{"decision": "failed"}],
            }
        )
    )

    with pytest.raises(ValueError, match="failed outcome or compile wait timeout"):
        merge_compile_outputs(
            {"eog.csm": (compile_root, None)},
            ["eog.csm"],
            tmp_path / "merged" / "skills",
        )
