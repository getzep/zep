from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

from .dataset import Task
from .gym import GymClient, image_digest
from .gym_sets import SERVER_NAMES
from .mcp_client import _TOOL_NAME_ALIASES, MCPClient
from .prices import PRICES

EXPECTED_TOOL_COUNTS = {
    "csm": 89,
    "teams": 70,
    "calendar": 37,
    "email": 79,
    "itsm": 93,
    "hr": 89,
    "drive": 55,
}

TOWNHALL_PATCH_MARKER_PREFIX = "EOG_TEAMS_TOWNHALL_PATCH_ACTIVE:"
ITSM_OPENED_BY_PATCH_MARKER_PREFIX = "EOG_ITSM_OPENED_BY_PATCH_ACTIVE:"
REQUIRED_OPEN_FILE_LIMIT = 65536


def check_keys() -> dict[str, bool]:
    return {
        name: bool(os.getenv(name))
        for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY", "Z_AI_KEY", "ZEP_API_KEY")
    }


def teams_townhall_patch_sha256() -> str:
    patch_file = Path(__file__).resolve().parents[2] / "containers" / "teams-townhall-patch" / "sitecustomize.py"
    return hashlib.sha256(patch_file.read_bytes()).hexdigest()


def preflight_townhall_patch(container_name: str) -> str:
    expected_sha256 = teams_townhall_patch_sha256()
    logs = subprocess.run(
        ["docker", "logs", container_name],
        check=False,
        capture_output=True,
        text=True,
    )
    marker = f"{TOWNHALL_PATCH_MARKER_PREFIX}{expected_sha256}"
    if logs.returncode != 0 or marker not in logs.stdout + logs.stderr:
        raise RuntimeError(f"The Teams townhall patch is not active on {container_name}")
    return expected_sha256


def itsm_opened_by_patch_sha256() -> str:
    patch_file = Path(__file__).resolve().parents[2] / "containers" / "itsm-opened-by-patch" / "sitecustomize.py"
    return hashlib.sha256(patch_file.read_bytes()).hexdigest()


def preflight_itsm_opened_by_patch(container_name: str) -> str:
    expected_sha256 = itsm_opened_by_patch_sha256()
    logs = subprocess.run(
        ["docker", "logs", container_name],
        check=False,
        capture_output=True,
        text=True,
    )
    marker = f"{ITSM_OPENED_BY_PATCH_MARKER_PREFIX}{expected_sha256}"
    if logs.returncode != 0 or marker not in logs.stdout + logs.stderr:
        raise RuntimeError(f"The ITSM opened_by patch is not active on {container_name}")
    return expected_sha256


def _stderr_tail(stderr: str | bytes | None) -> str:
    if isinstance(stderr, bytes):
        stderr = stderr.decode(errors="replace")
    return (stderr or "")[-500:]


def open_file_limit(container: str) -> int:
    command = ["docker", "exec", container, "sh", "-c", "ulimit -n"]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"Could not read the open-file limit for {container}: command timed out. stderr: {_stderr_tail(exc.stderr)}"
        ) from exc

    stderr = _stderr_tail(result.stderr)
    if result.returncode != 0:
        raise RuntimeError(
            f"Could not read the open-file limit for {container}: docker exec exited with {result.returncode}. "
            f"stderr: {stderr}"
        )
    output = (result.stdout or "").strip()
    if output == "unlimited":
        return sys.maxsize
    try:
        return int(output)
    except ValueError as exc:
        raise RuntimeError(
            f"Could not read the open-file limit for {container}: expected an integer, got {output!r}. stderr: {stderr}"
        ) from exc


def preflight_gym(tasks: tuple[Task, ...], container_names: dict[str, str]) -> dict[str, Any]:
    by_name = {server["mcp_server_name"]: server for task in tasks for server in task.gym_servers_config}
    report = {}
    available_tools_by_server: dict[str, set[str]] = {}
    for name, expected in EXPECTED_TOOL_COUNTS.items():
        server = by_name.get(SERVER_NAMES[name])
        if not server:
            raise RuntimeError(f"No dataset server config for {name}")
        file_limit = open_file_limit(container_names[name])
        if file_limit < REQUIRED_OPEN_FILE_LIMIT:
            raise RuntimeError(
                f"Container {container_names[name]} has an open-file limit of {file_limit}. "
                "Start it with --ulimit nofile=65536:65536."
            )
        client = GymClient(server["mcp_server_url"], name=name, context=server.get("context", {}))
        health = client.health()
        client.close()
        digest = image_digest(container_names[name])
        endpoint = server.get("mcp_server_url", "").rstrip("/")
        if not endpoint.endswith("/mcp"):
            endpoint += "/mcp"
        mcp = MCPClient(endpoint, database_id="preflight")
        mcp.initialize()
        tools = mcp.list_tools()
        available_tools_by_server[server["mcp_server_name"]] = {tool["name"] for tool in tools}
        tool_count = len(tools)
        mcp.close()
        if tool_count != expected:
            raise RuntimeError(f"Expected {expected} tools for {name}, found {tool_count}")
        report[name] = {
            "healthy": bool(health),
            "image_digest": digest,
            "tool_count": tool_count,
            "expected_tool_count": expected,
            "open_file_limit": file_limit,
        }
        if name == "teams":
            report[name]["townhall_patch_sha256"] = preflight_townhall_patch(container_names[name])
        if name == "itsm":
            report[name]["opened_by_patch_sha256"] = preflight_itsm_opened_by_patch(container_names[name])

    missing_selected_tools = []
    for task in tasks:
        if task.known_defect:
            continue
        task_servers = {server["mcp_server_name"] for server in task.gym_servers_config}
        for selected_tool in task.selected_tools:
            remote_tool = _TOOL_NAME_ALIASES.get(selected_tool, selected_tool)
            if not any(
                selected_tool in available_tools_by_server.get(server_name, set())
                or remote_tool in available_tools_by_server.get(server_name, set())
                for server_name in task_servers
            ):
                missing_selected_tools.append(f"{task.task_id}: {selected_tool}")
    if missing_selected_tools:
        raise RuntimeError("Selected tools are missing from Gym servers: " + "; ".join(missing_selected_tools))

    return report


def preflight_reset(
    tasks: tuple[Task, ...],
    *,
    data_root: Path | None = None,
    baseline_path: Path | None = None,
) -> dict[str, str]:
    root = data_root or Path.home() / ".cache" / "enterpriseops-gym" / "dbs"
    checks: dict[str, str] = {}
    for name in EXPECTED_TOOL_COUNTS:
        server_name = SERVER_NAMES[name]
        task = next(
            (
                item
                for item in tasks
                if not item.known_defect
                and any(server.get("mcp_server_name") == server_name for server in item.gym_servers_config)
            ),
            None,
        )
        if task is None:
            raise RuntimeError(f"No reset-check task for {server_name}")
        database_ids = {server["mcp_server_name"]: str(uuid4()) for server in task.gym_servers_config}
        clients = {
            server["mcp_server_name"]: GymClient(
                server["mcp_server_url"], name=server["mcp_server_name"], context=server.get("context", {})
            )
            for server in task.gym_servers_config
        }
        try:
            for server in task.gym_servers_config:
                seed_path = root / server["seed_database_file"]
                if not seed_path.is_file():
                    raise FileNotFoundError(f"Database seed file does not exist: {seed_path}")
                clients[server["mcp_server_name"]].seed(database_ids[server["mcp_server_name"]], seed_path.read_text())
            next(iter(clients.values())).check_reset(task, database_ids, baseline_path=baseline_path)
            checks[name] = task.task_id
        finally:
            for server_name, client in clients.items():
                try:
                    client.delete(database_ids[server_name])
                finally:
                    client.close()
    return checks


def preflight_report() -> dict[str, Any]:
    return {"keys_present": check_keys(), "prices_loaded": sorted(PRICES)}
