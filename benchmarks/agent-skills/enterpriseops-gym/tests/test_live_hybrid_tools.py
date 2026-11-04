from __future__ import annotations

import os
from uuid import uuid4

import pytest

from eog.dataset import load_tasks
from eog.mcp_client import MCPClient
from eog.run import CompositeMCPClient

pytestmark = pytest.mark.skipif(
    os.getenv("EOG_RUN_LIVE_GYM") != "1",
    reason="Set EOG_RUN_LIVE_GYM=1 to test live gym MCP tools.",
)


def test_live_mcp_tool_loading_on_ten_hybrid_tasks() -> None:
    tasks = [task for task in load_tasks() if task.domain == "hybrid"][:10]
    assert len(tasks) == 10

    for task in tasks:
        clients = {
            server["mcp_server_name"]: MCPClient(
                server["mcp_server_url"].rstrip("/") + task.mcp_endpoint,
                database_id=str(uuid4()),
                context=server.get("context", {}),
            )
            for server in task.gym_servers_config
        }
        composite = CompositeMCPClient(clients)
        try:
            tools = composite.start(task)
            assert [tool["name"] for tool in tools] == list(composite.selected_tools)
            assert all(name.partition("__")[0] in clients for name in composite.selected_tools)
            assert len(composite.selected_tools) >= len(task.selected_tools)
        finally:
            composite.close()
