from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from autogen_core.memory import MemoryContent, MemoryMimeType
from zep_cloud.client import AsyncZep

from zep_autogen import ZepUserMemory, create_thread, create_user


def _make_client() -> MagicMock:
    client = MagicMock(spec=AsyncZep)
    client.user = MagicMock()
    client.user.create = AsyncMock(return_value=SimpleNamespace(uuid_="user-1"))
    client.thread = MagicMock()
    client.thread.create = AsyncMock(return_value=SimpleNamespace(uuid_="thread-1"))
    client.thread.add_messages = AsyncMock()
    client.graph = MagicMock()
    client.graph.episode = MagicMock()
    client.graph.episode.add = AsyncMock()
    return client


@pytest.mark.asyncio
async def test_create_user_uses_v4_create_and_returns_server_uuid() -> None:
    client = _make_client()

    user = await create_user(
        client,
        first_name="Jane",
        last_name="Smith",
        email="jane@example.com",
    )

    assert user.uuid_ == "user-1"
    client.user.create.assert_awaited_once_with(
        first_name="Jane",
        last_name="Smith",
        email="jane@example.com",
    )


@pytest.mark.asyncio
async def test_create_user_hook_receives_client_and_uuid() -> None:
    client = _make_client()
    hook = AsyncMock()

    await create_user(client, first_name="Jane", on_created=hook)

    hook.assert_awaited_once_with(client, "user-1")


@pytest.mark.asyncio
async def test_create_user_propagates_hook_error() -> None:
    client = _make_client()
    hook = AsyncMock(side_effect=RuntimeError("setup failed"))

    with pytest.raises(RuntimeError, match="setup failed"):
        await create_user(client, on_created=hook)


@pytest.mark.asyncio
async def test_create_user_propagates_sdk_error() -> None:
    client = _make_client()
    client.user.create.side_effect = RuntimeError("create failed")

    with pytest.raises(RuntimeError, match="create failed"):
        await create_user(client)


@pytest.mark.asyncio
async def test_create_thread_uses_server_generated_uuid() -> None:
    client = _make_client()

    thread = await create_thread(client, user_uuid="user-1")

    assert thread.uuid_ == "thread-1"
    client.thread.create.assert_awaited_once_with(user_uuid="user-1")


@pytest.mark.asyncio
async def test_memory_add_swallows_thread_creation_error(caplog) -> None:
    client = _make_client()
    client.thread.create.side_effect = RuntimeError("unavailable")
    memory = ZepUserMemory(client=client, user_uuid="user-1")
    content = MemoryContent(
        content="Hello",
        mime_type=MemoryMimeType.TEXT,
        metadata={"type": "message", "role": "user"},
    )

    await memory.add(content)

    assert "Failed to create Zep thread" in caplog.text
    client.thread.add_messages.assert_not_awaited()


@pytest.mark.asyncio
async def test_memory_add_swallows_message_persistence_error(caplog) -> None:
    client = _make_client()
    client.thread.add_messages.side_effect = RuntimeError("unavailable")
    memory = ZepUserMemory(client=client, user_uuid="user-1", thread_uuid="thread-1")
    content = MemoryContent(
        content="Hello",
        mime_type=MemoryMimeType.TEXT,
        metadata={"type": "message", "role": "user"},
    )

    await memory.add(content)

    assert "Failed to add Zep memory" in caplog.text


@pytest.mark.asyncio
async def test_memory_add_swallows_graph_episode_error(caplog) -> None:
    client = _make_client()
    client.graph.episode.add.side_effect = RuntimeError("unavailable")
    memory = ZepUserMemory(client=client, user_uuid="user-1")
    content = MemoryContent(
        content="A fact",
        mime_type=MemoryMimeType.TEXT,
        metadata={"type": "data"},
    )

    await memory.add(content)

    assert "Failed to add Zep memory" in caplog.text
