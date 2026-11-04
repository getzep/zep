"""Run SkillsBench and AFTER tasks in Docker containers through the Zep Skill arms.

The agent gets one bash tool that runs in the task container. The harness runs
the benchmark verifier in the same container after the agent stops.

Arms:
    none      no Skills
    curated   the benchmark Skills (SkillsBench curated Skills, AFTER seed Skills)
    zep       the Zep Skills that the teacher Agent compiled for the task
    combined  the benchmark Skills and the Zep Skills
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import logging
import re
import shutil
import statistics
import subprocess
import tempfile
import time
import tomllib
import uuid
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

import yaml
from openai import APIError, OpenAI
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
from zep_cloud import Zep

from appworld_run import next_teacher_attempt, startup_jobs
from compile import wait_for_learning_idle
from config import load_config, zep_client
from redaction import Redactor
from run import ensure_reward_verifier_binding, freeze_compilation
from tracing import CURRENT_TRACE, TRACER, traced_responses_create
from zep_capture import SkillToolkit, TrajectoryRecorder, ensure_agent, skill_uuid

LOGGER = logging.getLogger("container-skills")
MAX_OUTPUT_CHARS = 8_000
COMMAND_TIMEOUT_S = 600
AFTER_OUTPUT_RULE = (
    "\n\nThe input files are in /task/environment. Write all submitted artifacts under"
    " /task/output/."
)

AGENT_SKILL_DIRS = [
    "/root/.claude/skills",
    "/root/.codex/skills",
    "/root/.opencode/skill",
    "/root/.goose/skills",
    "/root/.factory/skills",
    "/root/.agents/skills",
    "/root/.gemini/skills",
    "/app/skills",
    "/skills",
]
TOOL = {
    "type": "function",
    "name": "bash",
    "description": "Run a bash command in the task container and return stdout and stderr.",
    "parameters": {
        "type": "object",
        "properties": {"command": {"type": "string", "description": "The bash command to run."}},
        "required": ["command"],
        "additionalProperties": False,
    },
}
SYSTEM_PROMPT = """You complete a task in a Linux container. You use the bash tool to run
commands. The shell does not keep state between calls, so use absolute paths or cd in each call.
The container has network access. Install a package only when the task needs it.

Do the complete task. Check your output files before you stop. When the task is complete,
reply with a short summary and do not call a tool."""
SKILL_HEADER = """## Skills
Skills are in {root}. Each Skill is a directory with a SKILL.md file and optional scripts
or references. The list shows the name and the description of each Skill. If a Skill matches
the task, read its SKILL.md with cat before you act. Do not read a Skill that does not
match the task. The task instructions are more important than a Skill.

"""


@dataclass
class SkillFile:
    name: str
    description: str
    markdown: str
    source_dir: Path | None = None
    origin: str = "curated"
    uuid: str | None = None


@dataclass
class ContainerTask:
    benchmark: str
    task_id: str
    root: Path
    instruction: str
    image: str
    workdir: str
    agent_timeout_s: float
    verifier_timeout_s: float
    cpus: float = 1.0
    memory_mb: int = 4096
    curated: list[SkillFile] = field(default_factory=list)
    role: str = ""

    @property
    def family(self) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", self.task_id.lower()).strip("-")
        return f"tool.{self.benchmark}.{slug}"


@dataclass
class ContainerResult:
    benchmark: str
    task_id: str
    task_index: int
    trial: int
    arm: str | None = None
    started_at: str = ""
    finished_at: str = ""
    reward: float = 0.0
    succeeded: bool = False
    verifier_exit: int | None = None
    completed: bool = False
    timed_out: bool = False
    steps: int = 0
    tool_calls: int = 0
    prompt_tokens: int = 0
    cached_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    duration_s: float = 0.0
    verifier_s: float = 0.0
    trajectory_uuid: str | None = None
    task_family: str = ""
    skill_names: list[str] = field(default_factory=list)
    skill_origins: list[str] = field(default_factory=list)
    zep_skill_uuids: list[str] = field(default_factory=list)
    skills_read: list[str] = field(default_factory=list)
    skill_chars: int = 0
    error: str | None = None


def _frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            data = yaml.safe_load(parts[1]) or {}
            return (data if isinstance(data, dict) else {}), parts[2].lstrip()
    return {}, text


def _skill_from_dir(path: Path, origin: str) -> SkillFile | None:
    skill_md = path / "SKILL.md"
    if not skill_md.is_file():
        return None
    text = skill_md.read_text(errors="replace")
    meta, _ = _frontmatter(text)
    return SkillFile(
        name=str(meta.get("name") or path.name),
        description=" ".join(str(meta.get("description") or "").split()),
        markdown=text,
        source_dir=path,
        origin=origin,
    )


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "skill"


def _docker(*args: str, timeout: float | None = None, input_text: str | None = None) -> Any:
    return subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        input=input_text,
        check=False,
    )


def _build_lock(image: str) -> Any:
    digest = hashlib.sha256(image.encode()).hexdigest()[:16]
    handle = (Path(tempfile.gettempdir()) / f"container-build-{digest}.lock").open("w")
    fcntl.flock(handle, fcntl.LOCK_EX)
    return handle


def ensure_image(image: str, context: Path, dockerfile: str | None = None) -> None:
    handle = _build_lock(image)
    try:
        if _docker("image", "inspect", image).returncode == 0:
            return
        args = ["build", "-q", "-t", image]
        if dockerfile is not None:
            args += ["-f", "-"]
        result = _docker(*args, str(context), timeout=3600, input_text=dockerfile)
        if result.returncode != 0:
            raise RuntimeError(f"docker build failed for {image}: {result.stderr[-2000:]}")
    finally:
        handle.close()


def _workdir(dockerfile: Path) -> str:
    workdir = "/root"
    for line in dockerfile.read_text().splitlines():
        match = re.match(r"\s*WORKDIR\s+(\S+)", line, flags=re.IGNORECASE)
        if match:
            workdir = match.group(1)
    return workdir


def load_skillsbench(root: Path) -> list[ContainerTask]:
    tasks: list[ContainerTask] = []
    for task_dir in sorted((root / "tasks").iterdir()):
        task_md = task_dir / "task.md"
        if not task_md.is_file():
            continue
        meta, body = _frontmatter(task_md.read_text())
        sandbox = dict(meta.get("sandbox") or {})
        skills_dir = task_dir / "environment" / "skills"
        curated = (
            [
                skill
                for path in sorted(skills_dir.iterdir())
                if path.is_dir() and (skill := _skill_from_dir(path, "curated"))
            ]
            if skills_dir.is_dir()
            else []
        )
        tasks.append(
            ContainerTask(
                benchmark="skillsbench",
                task_id=task_dir.name,
                root=task_dir,
                instruction=body.strip(),
                image=f"skillsbench-{task_dir.name}".lower(),
                workdir=_workdir(task_dir / "environment" / "Dockerfile"),
                agent_timeout_s=float(dict(meta.get("agent") or {}).get("timeout_sec") or 900),
                verifier_timeout_s=float(
                    dict(meta.get("verifier") or {}).get("timeout_sec") or 900
                ),
                cpus=float(sandbox.get("cpus") or 1),
                memory_mb=int(sandbox.get("memory_mb") or 4096),
                curated=curated,
            )
        )
    return tasks


AFTER_DOCKERFILE = """FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git curl build-essential \\
    && rm -rf /var/lib/apt/lists/*
COPY requirements-{role}.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
WORKDIR /task
"""


def load_after(root: Path) -> list[ContainerTask]:
    manifest = json.loads((root / "tasks" / "manifest.json").read_text())
    tasks: list[ContainerTask] = []
    for entry in manifest:
        if not entry.get("included") or entry.get("split") != "test":
            continue
        role = str(entry["role"])
        task_dir = root / "tasks" / role / str(entry["task_id"])
        if not (task_dir / "instruction.md").is_file():
            continue
        config = tomllib.loads((task_dir / "task.toml").read_text())
        topics = list(dict(config.get("task") or {}).get("skills") or entry.get("skills") or [])
        curated = [
            skill
            for topic in topics
            if (skill := _skill_from_dir(root / "skills" / str(topic), "curated"))
        ]
        tasks.append(
            ContainerTask(
                benchmark="after",
                task_id=str(entry["task_id"]),
                root=task_dir,
                instruction=(task_dir / "instruction.md").read_text().strip() + AFTER_OUTPUT_RULE,
                image=f"after-{role}",
                workdir="/task",
                agent_timeout_s=1800,
                verifier_timeout_s=900,
                cpus=2,
                memory_mb=8192,
                curated=curated,
                role=role,
            )
        )
    return tasks


class Container:
    def __init__(self, task: ContainerTask) -> None:
        self.task = task
        self.name = f"cr-{_slug(task.task_id)[:40]}-{uuid.uuid4().hex[:8]}"
        self.hidden = Path(tempfile.mkdtemp(prefix="cr-hidden-"))

    def __enter__(self) -> Self:
        result = _docker(
            "run",
            "-d",
            "--name",
            self.name,
            f"--cpus={self.task.cpus}",
            f"--memory={self.task.memory_mb}m",
            "-w",
            self.task.workdir,
            "--entrypoint",
            "sleep",
            self.task.image,
            "infinity",
            timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(f"docker run failed: {result.stderr[-1000:]}")
        return self

    def __exit__(self, *_: object) -> None:
        _docker("rm", "-f", self.name, timeout=300)
        shutil.rmtree(self.hidden, ignore_errors=True)

    def run(self, command: str, timeout: float = COMMAND_TIMEOUT_S) -> tuple[int, str]:
        try:
            result = _docker(
                "exec", "-w", self.task.workdir, self.name, "bash", "-lc", command, timeout=timeout
            )
        except subprocess.TimeoutExpired:
            return 124, f"[command timed out after {int(timeout)} s]"
        return result.returncode, (result.stdout or "") + (result.stderr or "")

    def copy_in(self, source: Path, destination: str) -> None:
        self.run(f"mkdir -p {Path(destination).parent}")
        result = _docker("cp", str(source), f"{self.name}:{destination}", timeout=600)
        if result.returncode != 0:
            raise RuntimeError(f"docker cp failed: {result.stderr[-1000:]}")

    def copy_out(self, source: str, destination: Path) -> None:
        result = _docker("cp", f"{self.name}:{source}", str(destination), timeout=600)
        if result.returncode != 0:
            raise RuntimeError(f"docker cp failed: {result.stderr[-1000:]}")


def prepare_image(task: ContainerTask, after_root: Path | None) -> None:
    if task.benchmark == "skillsbench":
        ensure_image(task.image, task.root / "environment")
    else:
        assert after_root is not None
        ensure_image(task.image, after_root, AFTER_DOCKERFILE.format(role=task.role))


def setup_workspace(container: Container) -> None:
    task = container.task
    container.run("rm -rf " + " ".join(AGENT_SKILL_DIRS))
    if task.benchmark == "after":
        container.run("rm -rf /task && mkdir -p /task")
        for item in task.root.iterdir():
            container.copy_in(item, f"/task/{item.name}")
        container.run("mkdir -p /task/output /task/environment")
        code, output = container.run("cd /task && python data_generator.py", timeout=900)
        if code != 0:
            raise RuntimeError(f"data_generator.py failed: {output[-1000:]}")
        container.copy_out("/task/tests", container.hidden / "tests")
        visible = {"instruction.md", "environment", "output"}
        hidden = [item.name for item in task.root.iterdir() if item.name not in visible]
        container.run("rm -rf " + " ".join(f"/task/{name}" for name in hidden))


def deploy_skills(container: Container, skills: list[SkillFile]) -> str:
    if not skills:
        return ""
    lines = []
    used: set[str] = set()
    for skill in skills:
        folder = _slug(skill.name)
        while folder in used:
            folder += "-2"
        used.add(folder)
        destination = f"/skills/{folder}"
        if skill.source_dir is not None:
            container.copy_in(skill.source_dir, destination)
        else:
            with tempfile.TemporaryDirectory() as tmp:
                (Path(tmp) / "SKILL.md").write_text(skill.markdown)
                container.copy_in(Path(tmp), destination)
        lines.append(
            f"- {destination}/SKILL.md\n  name: {skill.name}\n  description: {skill.description}"
        )
    return SKILL_HEADER.format(root="/skills") + "\n".join(lines)


def verify(container: Container) -> tuple[int, float]:
    task = container.task
    if task.benchmark == "skillsbench":
        container.copy_in(task.root / "verifier", "/verifier")
        container.run("mkdir -p /logs/verifier")
        code, _ = container.run("bash /verifier/test.sh", timeout=task.verifier_timeout_s)
        _, reward = container.run("cat /logs/verifier/reward.txt")
        try:
            return code, float(reward.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return code, 0.0
    container.run("rm -rf /task/tests")
    container.copy_in(container.hidden / "tests", "/task/tests")
    code, _ = container.run(
        "cd /task && python -m pytest tests -q -p no:cacheprovider", timeout=task.verifier_timeout_s
    )
    return code, 1.0 if code == 0 else 0.0


def run_oracle(container: Container) -> None:
    task = container.task
    if task.benchmark == "skillsbench":
        container.copy_in(task.root / "oracle", "/oracle")
        container.run("bash /oracle/solve.sh", timeout=task.agent_timeout_s)
    else:
        oracle = [
            item
            for item in task.root.iterdir()
            if item.name not in {"tests", "environment", "output"}
        ]
        for item in oracle:
            container.copy_in(item, f"/task/{item.name}")
        container.copy_in(container.hidden / "tests", "/task/tests")
        container.run("cd /task && bash solve.sh", timeout=task.agent_timeout_s)
        names = [item.name for item in oracle if item.name != "instruction.md"]
        container.run("rm -rf /task/tests " + " ".join(f"/task/{name}" for name in names))


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=1, max=16),
    retry=retry_if_exception_type(APIError),
    reraise=True,
)
def _completion(client: OpenAI, kwargs: dict[str, Any]) -> Any:
    return traced_responses_create(client, "container-agent", kwargs)


def _item_dict(item: Any) -> dict[str, Any]:
    return item.model_dump(mode="json", exclude_none=True) if hasattr(item, "model_dump") else item


def zep_skills(client: Zep, memory_agent_uuid: str, task: ContainerTask) -> list[SkillFile]:
    toolkit = SkillToolkit(client, memory_agent_uuid, task.family)
    hits = toolkit.search_skills(task.instruction, limit=3)
    return [
        SkillFile(
            name=str(hit.get("name") or "zep-skill"),
            description=" ".join(str(hit.get("description") or "").split()),
            markdown=str(hit.get("markdown") or ""),
            origin="zep",
            uuid=skill_uuid(hit),
        )
        for hit in hits
        if hit.get("markdown")
    ]


def run_episode(
    *,
    task: ContainerTask,
    client: Zep,
    openai_client: OpenAI,
    agent_uuid: str,
    stage: str,
    run_id: str,
    task_index: int,
    trial: int,
    model: str,
    reasoning_effort: str,
    max_steps: int,
    arm: str | None,
    memory_agent_uuid: str | None,
    after_root: Path | None,
    record: bool,
    parent_trajectory_uuid: str | None = None,
) -> tuple[ContainerResult, list[dict[str, Any]]]:
    started = datetime.now(UTC)
    result = ContainerResult(
        benchmark=task.benchmark,
        task_id=task.task_id,
        task_index=task_index,
        trial=trial,
        arm=arm,
        started_at=started.isoformat(),
        task_family=task.family,
    )
    messages: list[dict[str, Any]] = []
    trace = TRACER.start_trace(
        f"{task.benchmark}-{stage}",
        session_id=f"{task.benchmark}:{run_id}:{stage}",
        metadata={"task_id": task.task_id, "trial": trial, "model": model},
    )
    trace_token = CURRENT_TRACE.set(trace)
    recorder = (
        TrajectoryRecorder(
            client,
            agent_uuid,
            task.family,
            task.instruction,
            task.benchmark,
            {"harness": "container-skills", "stage": stage, "task_index": str(task_index)},
            redactor=Redactor(),
            discoverable_tools=frozenset(),
            stage=stage,
            run_id=run_id,
            domain=task.benchmark,
            task_index=task_index,
            trial=trial,
            verifier_id=f"{task.benchmark}-verifier",
            parent_trajectory_uuid=parent_trajectory_uuid,
        )
        if record
        else None
    )
    try:
        prepare_image(task, after_root)
        with Container(task) as container:
            setup_workspace(container)
            if model == "oracle":
                run_oracle(container)
                result.completed = True
            else:
                skills: list[SkillFile] = []
                if arm in {"curated", "combined"}:
                    skills += task.curated
                if arm in {"zep", "combined"} and memory_agent_uuid:
                    zep = zep_skills(client, memory_agent_uuid, task)
                    result.zep_skill_uuids = [str(skill.uuid) for skill in zep]
                    skills += zep
                result.skill_names = [skill.name for skill in skills]
                result.skill_origins = [skill.origin for skill in skills]
                result.skill_chars = sum(len(skill.markdown) for skill in skills)
                catalog = deploy_skills(container, skills)
                instructions = SYSTEM_PROMPT + (
                    f"\n\nThe working directory is {task.workdir}." if task.workdir else ""
                )
                if catalog:
                    instructions += "\n\n" + catalog
                if recorder:
                    recorder.__enter__()
                    result.trajectory_uuid = recorder.trajectory_uuid
                    recorder.record_message("user", task.instruction)
                result.completed, result.timed_out = _agent_loop(
                    container,
                    openai_client,
                    instructions,
                    result,
                    messages,
                    recorder,
                    model=model,
                    reasoning_effort=reasoning_effort,
                    max_steps=max_steps,
                    cache_key=f"{run_id}:{stage}:{task.task_id}:{trial}",
                )
                result.skills_read = sorted(
                    {
                        match
                        for message in messages
                        for call in message.get("tool_calls") or []
                        for match in re.findall(
                            r"/skills/([a-z0-9-]+)/SKILL\.md",
                            str(call["arguments"].get("command") or ""),
                        )
                    }
                )
            verify_started = time.monotonic()
            result.verifier_exit, result.reward = verify(container)
            result.verifier_s = time.monotonic() - verify_started
            result.succeeded = result.reward >= 1.0
            if recorder:
                recorder.set_result(result.succeeded, True)
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Episode %s failed: %s: %s", task.task_id, type(exc).__name__, exc)
        result.error = type(exc).__name__
        result.succeeded = False
        result.reward = 0.0
    finally:
        if recorder and recorder.trajectory_uuid:
            if result.error:
                recorder.__exit__(RuntimeError, None, None)
            else:
                recorder.__exit__(None, None, None)
        TRACER.end_trace(trace, output={"reward": result.reward, "error": result.error})
        CURRENT_TRACE.reset(trace_token)
        result.duration_s = (datetime.now(UTC) - started).total_seconds()
        result.finished_at = datetime.now(UTC).isoformat()
    return result, messages


def _agent_loop(
    container: Container,
    openai_client: OpenAI,
    instructions: str,
    result: ContainerResult,
    messages: list[dict[str, Any]],
    recorder: TrajectoryRecorder | None,
    *,
    model: str,
    reasoning_effort: str,
    max_steps: int,
    cache_key: str,
) -> tuple[bool, bool]:
    items: list[dict[str, Any]] = [{"role": "user", "content": container.task.instruction}]
    messages.append({"role": "user", "content": container.task.instruction})
    deadline = time.monotonic() + container.task.agent_timeout_s
    for _ in range(max_steps):
        if time.monotonic() > deadline:
            return False, True
        response = _completion(
            openai_client,
            {
                "model": model,
                "instructions": instructions,
                "input": items,
                "tools": [TOOL],
                "store": False,
                "include": ["reasoning.encrypted_content"],
                "reasoning": {"effort": reasoning_effort},
                "prompt_cache_key": hashlib.sha256(cache_key.encode()).hexdigest()[:32],
            },
        )
        result.steps += 1
        usage = getattr(response, "usage", None)
        result.prompt_tokens += int(getattr(usage, "input_tokens", 0) or 0)
        result.cached_tokens += int(
            getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", 0) or 0
        )
        result.completion_tokens += int(getattr(usage, "output_tokens", 0) or 0)
        result.reasoning_tokens += int(
            getattr(getattr(usage, "output_tokens_details", None), "reasoning_tokens", 0) or 0
        )
        output = [_item_dict(item) for item in getattr(response, "output", None) or []]
        items.extend(output)
        calls = [item for item in output if item.get("type") == "function_call"]
        text = str(getattr(response, "output_text", "") or "").strip()
        if not calls:
            messages.append({"role": "assistant", "content": text})
            if recorder:
                recorder.record_message("assistant", text)
            return True, False
        tool_calls = []
        for call in calls:
            try:
                arguments = json.loads(str(call.get("arguments") or "{}"))
            except json.JSONDecodeError:
                arguments = {"command": ""}
            tool_calls.append({"id": call["call_id"], "name": call["name"], "arguments": arguments})
        messages.append({"role": "assistant", "content": text, "tool_calls": tool_calls})
        for call in tool_calls:
            result.tool_calls += 1
            command = str(call["arguments"].get("command") or "")
            if recorder:
                recorder.record_tool_call(call["name"], call["arguments"], call_id=call["id"])
            remaining = max(1.0, min(COMMAND_TIMEOUT_S, deadline - time.monotonic()))
            code, observation = container.run(command, timeout=remaining)
            observation = f"[exit {code}]\n{observation}"
            if len(observation) > MAX_OUTPUT_CHARS:
                observation = (
                    observation[: MAX_OUTPUT_CHARS // 2]
                    + "\n...[output truncated]...\n"
                    + observation[-MAX_OUTPUT_CHARS // 2 :]
                )
            if recorder:
                recorder.record_tool_result(call["name"], observation, call_id=call["id"])
            messages.append({"role": "tool", "id": call["id"], "content": observation})
            items.append(
                {"type": "function_call_output", "call_id": call["id"], "output": observation}
            )
    return False, False


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def mean(key: str) -> float:
        return statistics.fmean(float(row.get(key, 0) or 0) for row in rows) if rows else 0.0

    by_task: dict[str, list[float]] = {}
    for row in rows:
        by_task.setdefault(str(row["task_id"]), []).append(float(row.get("reward", 0.0) >= 1.0))
    return {
        "episode_count": len(rows),
        "task_count": len(by_task),
        "success_rate": statistics.fmean(bool(row.get("succeeded")) for row in rows)
        if rows
        else 0.0,
        "mean_reward": mean("reward"),
        "task_pass_all": statistics.fmean(min(v) for v in by_task.values()) if by_task else 0.0,
        "error_count": sum(bool(row.get("error")) for row in rows),
        "timeout_count": sum(bool(row.get("timed_out")) for row in rows),
        "mean_steps": mean("steps"),
        "mean_prompt_tokens": mean("prompt_tokens"),
        "mean_cached_tokens": mean("cached_tokens"),
        "mean_completion_tokens": mean("completion_tokens"),
        "mean_duration_s": mean("duration_s"),
        "skill_exposure_rate": statistics.fmean(bool(row.get("skill_names")) for row in rows)
        if rows
        else 0.0,
        "skill_read_rate": statistics.fmean(bool(row.get("skills_read")) for row in rows)
        if rows
        else 0.0,
        "zep_hit_rate": statistics.fmean(bool(row.get("zep_skill_uuids")) for row in rows)
        if rows
        else 0.0,
    }


def _latest_rows(path: Path) -> dict[tuple[int, int], dict[str, Any]]:
    latest: dict[tuple[int, int], dict[str, Any]] = {}
    for line in path.read_text().splitlines():
        if line:
            row = json.loads(line)
            latest[(int(row["task_index"]), int(row["trial"]))] = row
    return latest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", choices=["skillsbench", "after"], required=True)
    parser.add_argument("--stage", choices=["teacher", "heldout"], required=True)
    parser.add_argument("--arm", choices=["none", "curated", "zep", "combined"])
    parser.add_argument("--root", required=True, help="The benchmark checkout.")
    parser.add_argument("--tasks", nargs="*", help="Task IDs. The default is all tasks.")
    parser.add_argument("--exclude", nargs="*", default=[], help="Task IDs to leave out.")
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--max-attempts", type=int, default=1)
    parser.add_argument("--model", default="gpt-6-sol", help="A model name, or oracle.")
    parser.add_argument("--reasoning-effort", default="high")
    parser.add_argument("--max-steps", type=int, default=80)
    parser.add_argument("--memory-agent-uuid")
    parser.add_argument(
        "--compilation-mode", choices=["scheduled", "manual", "on_close"], default="on_close"
    )
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--results-root", default=str(Path(__file__).resolve().parent / "results"))
    parser.add_argument("--zep-api-key-uuid")
    parser.add_argument("--allow-unverified", action="store_true")
    parser.add_argument("--compile-timeout", type=float, default=1800)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.stage == "heldout" and not args.arm:
        parser.error("The heldout stage requires --arm")
    if args.arm in {"zep", "combined"} and not args.memory_agent_uuid:
        parser.error(f"The {args.arm} arm requires --memory-agent-uuid")
    if args.max_attempts > 1 and (args.stage != "teacher" or args.trials != 1):
        parser.error("--max-attempts more than 1 requires the teacher stage and --trials 1")
    stage = "teacher" if args.stage == "teacher" else f"heldout-{args.arm}"
    root = Path(args.root)
    after_root = root if args.benchmark == "after" else None
    tasks = load_skillsbench(root) if args.benchmark == "skillsbench" else load_after(root)
    if args.tasks:
        wanted = set(args.tasks)
        tasks = [task for task in tasks if task.task_id in wanted]
    tasks = [task for task in tasks if task.task_id not in set(args.exclude)]
    config = load_config()
    client = zep_client(config.api_key, config.api_url)
    openai_client = OpenAI(api_key=config.openai_api_key)
    output_dir = Path(args.results_root) / args.run_id
    (output_dir / "simulations").mkdir(parents=True, exist_ok=True)
    jsonl_path = output_dir / f"{stage}.jsonl"
    if jsonl_path.exists() and not args.resume:
        raise ValueError(f"The {stage} stage already exists; use --resume or a new run ID")
    record = args.stage == "teacher" and args.model != "oracle"
    agent = ensure_agent(
        client,
        f"{args.benchmark}-{stage}",
        run_id=args.run_id,
        compilation_mode=args.compilation_mode,
        task_families=[],
    )
    agent_uuid = str(agent["agent_uuid"])
    if record:
        ensure_reward_verifier_binding(
            client,
            agent_uuid,
            args.zep_api_key_uuid,
            verifier_id=f"{args.benchmark}-verifier",
            allow_unverified=args.allow_unverified,
        )
    else:
        freeze_compilation(client, agent_uuid)
    if args.arm in {"zep", "combined"}:
        wait_for_learning_idle(
            client,
            str(args.memory_agent_uuid),
            sorted({task.family for task in tasks}),
            timeout=args.compile_timeout,
        )
    firsts = {task.image: task for task in reversed(tasks)}
    with ThreadPoolExecutor(max_workers=min(args.concurrency, 3)) as builder:
        builds = {
            builder.submit(prepare_image, task, after_root): image for image, task in firsts.items()
        }
        for future, image in builds.items():
            if future.exception() is not None:
                LOGGER.warning("Image %s failed to build: %s", image, future.exception())

    def job(
        task_index: int, task_id: str, trial: int, parent: str | None = None
    ) -> tuple[ContainerResult, list[dict[str, Any]]]:
        del task_id
        return run_episode(
            task=tasks[task_index],
            client=client,
            openai_client=openai_client,
            agent_uuid=agent_uuid,
            stage=stage,
            run_id=args.run_id,
            task_index=task_index,
            trial=trial,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            max_steps=args.max_steps,
            arm=args.arm,
            memory_agent_uuid=args.memory_agent_uuid,
            after_root=after_root,
            record=record,
            parent_trajectory_uuid=parent,
        )

    task_ids = [task.task_id for task in tasks]
    latest = _latest_rows(jsonl_path) if args.resume and jsonl_path.exists() else {}
    completed = {key for key, row in latest.items() if not row.get("error")}
    jobs = startup_jobs(task_ids, latest, args.trials, args.max_attempts)
    scheduled = {(index, trial) for index, _, trial, _ in jobs}
    LOGGER.info("resume=%s skipped_jobs=%d pending_jobs=%d", args.resume, len(completed), len(jobs))
    started = time.monotonic()
    rows: list[dict[str, Any]] = []
    with (
        ThreadPoolExecutor(max_workers=args.concurrency) as executor,
        jsonl_path.open("a" if args.resume else "w") as out,
    ):
        pending: set[Future[tuple[ContainerResult, list[dict[str, Any]]]]] = {
            executor.submit(job, index, task_id, trial, parent)
            for index, task_id, trial, parent in jobs
        }
        while pending:
            finished, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in finished:
                result, messages = future.result()
                row = asdict(result)
                row["agent_uuid"] = agent_uuid
                row["model"] = args.model
                row["reasoning_effort"] = args.reasoning_effort
                (
                    output_dir / "simulations" / f"{stage}-{result.task_index}-{result.trial}.json"
                ).write_text(json.dumps({"task_id": result.task_id, "messages": messages}))
                out.write(json.dumps(row, sort_keys=True) + "\n")
                out.flush()
                rows.append(row)
                latest[(result.task_index, result.trial)] = row
                attempt = next_teacher_attempt(result, args.max_attempts)  # type: ignore[arg-type]
                if (
                    attempt is not None
                    and (result.task_index, attempt) not in completed | scheduled
                ):
                    scheduled.add((result.task_index, attempt))
                    pending.add(
                        executor.submit(
                            job, result.task_index, result.task_id, attempt, result.trajectory_uuid
                        )
                    )
                LOGGER.info(
                    "done=%d pending=%d task=%s reward=%.2f error=%s",
                    len(rows),
                    len(pending),
                    result.task_id,
                    result.reward,
                    result.error,
                )
    summary = {
        "stage": stage,
        "agent_uuid": agent_uuid,
        "benchmark": args.benchmark,
        "conditions": {
            "model": args.model,
            "reasoning_effort": args.reasoning_effort,
            "arm": args.arm,
            "memory_agent_uuid": args.memory_agent_uuid,
            "trials": args.trials,
            "max_attempts": args.max_attempts,
            "max_steps": args.max_steps,
            "task_count": len(tasks),
        },
        "wall_s": time.monotonic() - started,
        "metrics": summarize(list(latest.values())),
    }
    (output_dir / f"{stage}.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary["metrics"], indent=2))


if __name__ == "__main__":
    main()
