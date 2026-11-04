from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from eog.dataset import load_tasks
from eog.errors import InfrastructureError
from eog.gym import GymClient
from eog.mcp_client import MCPClient
from eog.run import CompositeMCPClient, _seed_path

pytestmark = pytest.mark.skipif(
    os.getenv("EOG_RUN_LIVE_GYM") != "1",
    reason="Set EOG_RUN_LIVE_GYM=1 to test live gym tools.",
)


def test_live_townhall_create_returns_the_written_row() -> None:
    task = next(item for item in load_tasks() if item.task_id == "task_20251125_113335_636_7ebc1127_a4ebffe8")
    server = next(item for item in task.gym_servers_config if item["mcp_server_name"] == "gym-teams-mcp")
    database_id = str(uuid4())
    gym = GymClient(
        server["mcp_server_url"],
        name="gym-teams-mcp",
        context=server.get("context", {}),
    )
    seeded = False
    try:
        try:
            gym.health()
        except InfrastructureError as error:
            pytest.skip(f"The Teams Gym is down: {type(error).__name__}")

        gym.seed(database_id, _seed_path(server["seed_database_file"]).read_text())
        seeded = True
        client = MCPClient(
            server["mcp_server_url"].rstrip("/") + task.mcp_endpoint,
            database_id=database_id,
            context=server.get("context", {}),
        )
        composite = CompositeMCPClient({"gym-teams-mcp": client})
        try:
            composite.start(task)
            arguments = json.loads((Path(__file__).parent / "fixtures" / "teams_townhall_create.json").read_text())
            result = composite.call_tool("gym-teams-mcp__create_virtual_event_townhall", arguments)
            assert result.get("isError") is False

            verifier = gym.verify(task, {"gym-teams-mcp": database_id})
            assert all(verifier.passed[index] for index in (3, 4, 5))
        finally:
            composite.close()
    finally:
        try:
            if seeded:
                gym.delete(database_id)
        finally:
            gym.close()
