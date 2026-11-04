from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any


def _compile_paths(directory: Path) -> tuple[Path, Path, Path]:
    snapshot_path = directory / "snapshot.json"
    if snapshot_path.is_file():
        return directory.parent, directory, snapshot_path
    skills_dir = directory / "skills"
    snapshot_path = skills_dir / "snapshot.json"
    if snapshot_path.is_file():
        return directory, skills_dir, snapshot_path
    skills_path = directory / "skills.json"
    if skills_path.is_file():
        return directory.parent, directory, skills_path
    skills_path = skills_dir / "skills.json"
    if skills_path.is_file():
        return directory, skills_dir, skills_path
    raise ValueError(f"The compile directory has no frozen snapshot: {directory}")


def _development_commit(compile_root: Path, explicit_commit: str | None) -> str:
    if explicit_commit:
        return explicit_commit
    for name in ("manifest.compile.json", "manifest.recompile.json"):
        manifest_path = compile_root / name
        if manifest_path.is_file():
            commit = json.loads(manifest_path.read_text()).get("development_commit")
            if commit:
                return str(commit)
    raise ValueError(f"The development commit is missing for compile directory {compile_root}")


def _source_failed(report: dict[str, Any]) -> bool:
    failures = report.get("failures") or []
    if any(row.get("error") in {"compile_wait_timeout", "compile_failed"} for row in failures):
        return True
    outcomes = report.get("compile_outcomes") or []
    return any(row.get("decision") == "failed" for row in outcomes)


def merge_compile_outputs(
    family_sources: dict[str, tuple[Path, str | None]],
    task_families: list[str],
    output_dir: Path,
) -> tuple[dict[str, Any], dict[str, dict[str, str]]]:
    """Merge frozen Skills by task family and record each source compile."""
    if not task_families:
        raise ValueError("Select at least one task family to merge")
    if len(task_families) != len(set(task_families)):
        raise ValueError("Task families must be unique")
    missing_families = set(task_families) - set(family_sources)
    if missing_families:
        raise ValueError(f"No source compile was supplied for task families: {sorted(missing_families)}")
    if output_dir.joinpath("snapshot.json").exists():
        raise ValueError(f"The output directory already has a frozen snapshot: {output_dir}")

    resolved_output = output_dir.resolve()
    skills: list[dict[str, Any]] = []
    compile_failures: list[dict[str, Any]] = []
    provenance: dict[str, dict[str, str]] = {}

    for family in task_families:
        source_directory, explicit_commit = family_sources[family]
        compile_root, skills_dir, snapshot_path = _compile_paths(source_directory.resolve())
        resolved_compile_root = compile_root.resolve()
        if (
            resolved_output == resolved_compile_root
            or resolved_compile_root in resolved_output.parents
            or resolved_output in resolved_compile_root.parents
        ):
            raise ValueError(f"The output directory must not be inside a source compile: {output_dir}")
        agent_path = compile_root / "agent.json"
        if not agent_path.is_file():
            raise ValueError(f"The source compile has no Agent record: {agent_path}")
        agent_uuid = json.loads(agent_path.read_text()).get("agent_uuid")
        if not agent_uuid:
            raise ValueError(f"The source compile has no Agent UUID: {agent_path}")
        commit = _development_commit(compile_root, explicit_commit)

        report_path = skills_dir / family.replace(".", "_") / "compile_report.json"
        if not report_path.is_file():
            raise ValueError(f"The source compile has no report for {family}: {report_path}")
        report = json.loads(report_path.read_text())
        if _source_failed(report):
            raise ValueError(f"The source compile for {family} has a failed outcome or compile wait timeout")
        source_snapshot = json.loads(snapshot_path.read_text())
        source_skills = source_snapshot.get("skills")
        if not isinstance(source_skills, list):
            raise ValueError(f"The source snapshot has no Skills list: {snapshot_path}")
        source_hash = hashlib.sha256(json.dumps(source_skills, sort_keys=True).encode()).hexdigest()
        if source_snapshot.get("sha256") != source_hash:
            raise ValueError(f"The source snapshot hash does not match its Skills: {snapshot_path}")

        selected_skills = [item for item in source_skills if item.get("task_family") == family]
        for skill in selected_skills:
            source_skill = (skills_dir / skill["path"]).resolve()
            if skills_dir.resolve() not in source_skill.parents:
                raise ValueError(f"The source Skill path is outside the compile directory: {skill['path']}")
            content = source_skill.read_bytes()
            if hashlib.sha256(content).hexdigest() != skill.get("sha256"):
                raise ValueError(f"The source Skill hash does not match the snapshot: {source_skill}")
            target_skill = output_dir / skill["path"]
            target_skill.parent.mkdir(parents=True, exist_ok=True)
            if target_skill.exists() and target_skill.read_bytes() != content:
                raise ValueError(f"Two source Skills use the same path with different content: {skill['path']}")
            shutil.copyfile(source_skill, target_skill)
            skills.append(skill)

        target_report = output_dir / family.replace(".", "_") / "compile_report.json"
        target_report.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(report_path, target_report)
        compile_failures.extend(report.get("failures") or [])
        provenance[family] = {
            "source_compile_dir": str(compile_root),
            "agent_uuid": str(agent_uuid),
            "development_commit": commit,
        }

    snapshot = {
        "skills": skills,
        "sha256": hashlib.sha256(json.dumps(skills, sort_keys=True).encode()).hexdigest(),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "snapshot.json").write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
    (output_dir / "skills.json").write_text(
        json.dumps({**snapshot, "compile_failures": compile_failures}, indent=2, sort_keys=True) + "\n"
    )
    return snapshot, provenance
