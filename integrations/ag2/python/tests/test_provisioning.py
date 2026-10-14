from unittest.mock import AsyncMock, MagicMock

import pytest
from zep_cloud.client import AsyncZep

from zep_ag2 import ZepMemoryManager, create_thread, create_user


def _client() -> MagicMock:
    client = MagicMock(spec=AsyncZep)
    client.user = MagicMock()
    client.user.create = AsyncMock(return_value=MagicMock(uuid_="user-1"))
    client.user.get = AsyncMock(
        return_value=MagicMock(uuid_="graph-1", graph_uuid="different-graph")
    )
    client.thread = MagicMock()
    client.thread.create = AsyncMock(return_value=MagicMock(uuid_="thread-1"))
    return client


@pytest.mark.asyncio
async def test_create_user_returns_server_generated_resource() -> None:
    client = _client()

    user = await create_user(
        client,
        first_name="Ada",
        last_name="Lovelace",
        email="ada@example.com",
    )

    assert user.uuid_ == "user-1"
    client.user.create.assert_awaited_once_with(
        first_name="Ada", last_name="Lovelace", email="ada@example.com"
    )


@pytest.mark.asyncio
async def test_create_user_propagates_sdk_errors() -> None:
    client = _client()
    client.user.create = AsyncMock(side_effect=RuntimeError("create failed"))

    with pytest.raises(RuntimeError, match="create failed"):
        await create_user(client)


@pytest.mark.asyncio
async def test_create_user_runs_setup_hook_with_server_uuid() -> None:
    client = _client()
    hook = AsyncMock()

    await create_user(client, on_created=hook)

    hook.assert_awaited_once_with(client, "user-1")


@pytest.mark.asyncio
async def test_create_user_propagates_setup_hook_errors() -> None:
    client = _client()
    hook = AsyncMock(side_effect=RuntimeError("setup failed"))

    with pytest.raises(RuntimeError, match="setup failed"):
        await create_user(client, on_created=hook)

    client.user.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_thread_uses_user_uuid() -> None:
    client = _client()

    thread = await create_thread(client, user_uuid="user-1")

    assert thread.uuid_ == "thread-1"
    client.thread.create.assert_awaited_once_with(user_uuid="user-1")


@pytest.mark.asyncio
async def test_resolve_graph_uuid_reads_user_only_once() -> None:
    client = _client()
    manager = ZepMemoryManager(client, user_uuid="user-1")

    assert await manager.resolve_graph_uuid() == "graph-1"
    assert await manager.resolve_graph_uuid() == "graph-1"
    client.user.get.assert_awaited_once_with("user-1")


@pytest.mark.asyncio
async def test_resolve_graph_uuid_uses_configured_uuid_without_lookup() -> None:
    client = _client()
    manager = ZepMemoryManager(client, user_uuid="user-1", graph_uuid="graph-2")

    assert await manager.resolve_graph_uuid() == "graph-2"
    client.user.get.assert_not_awaited()
