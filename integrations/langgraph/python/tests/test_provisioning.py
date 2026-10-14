"""Tests for explicit V4 Zep user and thread provisioning."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from zep_langgraph.provisioning import (
    create_thread,
    create_thread_sync,
    create_user,
    create_user_sync,
)


def _make_async_client() -> MagicMock:
    client = MagicMock()
    client.user.create = AsyncMock(return_value=MagicMock(uuid_="user-uuid"))
    client.thread.create = AsyncMock(return_value=MagicMock(uuid_="thread-uuid"))
    return client


def _make_sync_client() -> MagicMock:
    client = MagicMock()
    client.user.create = MagicMock(return_value=MagicMock(uuid_="user-uuid"))
    client.thread.create = MagicMock(return_value=MagicMock(uuid_="thread-uuid"))
    return client


@pytest.mark.asyncio
async def test_create_user_returns_server_generated_user_and_runs_hook() -> None:
    client = _make_async_client()
    hook = AsyncMock()
    user = await create_user(
        client,
        first_name="Jane",
        last_name="Smith",
        email="jane@example.com",
        on_created=hook,
    )
    assert user.uuid_ == "user-uuid"
    client.user.create.assert_awaited_once_with(
        first_name="Jane", last_name="Smith", email="jane@example.com"
    )
    hook.assert_awaited_once_with(client, user)


@pytest.mark.asyncio
async def test_create_user_propagates_sdk_and_hook_errors() -> None:
    client = _make_async_client()
    client.user.create.side_effect = RuntimeError("SDK failure")
    with pytest.raises(RuntimeError, match="SDK failure"):
        await create_user(client)

    client = _make_async_client()
    hook = AsyncMock(side_effect=RuntimeError("hook failure"))
    with pytest.raises(RuntimeError, match="hook failure"):
        await create_user(client, on_created=hook)


@pytest.mark.asyncio
async def test_create_thread_returns_server_generated_thread() -> None:
    client = _make_async_client()
    thread = await create_thread(client, user_uuid="user-uuid")
    assert thread.uuid_ == "thread-uuid"
    client.thread.create.assert_awaited_once_with(user_uuid="user-uuid")


def test_sync_provisioning_returns_server_resources() -> None:
    client = _make_sync_client()
    hook = MagicMock()
    user = create_user_sync(client, first_name="Jane", on_created=hook)
    thread = create_thread_sync(client, user_uuid=user.uuid_)
    assert user.uuid_ == "user-uuid"
    assert thread.uuid_ == "thread-uuid"
    hook.assert_called_once_with(client, user)
    client.thread.create.assert_called_once_with(user_uuid="user-uuid")


def test_sync_provisioning_propagates_sdk_and_hook_errors() -> None:
    client = _make_sync_client()
    client.user.create.side_effect = RuntimeError("SDK failure")
    with pytest.raises(RuntimeError, match="SDK failure"):
        create_user_sync(client)

    client = _make_sync_client()
    hook = MagicMock(side_effect=RuntimeError("hook failure"))
    with pytest.raises(RuntimeError, match="hook failure"):
        create_user_sync(client, on_created=hook)
