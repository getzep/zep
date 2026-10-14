"""Tests for Zep v4 user and thread provisioning."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from zep_adk.provisioning import create_thread, create_user


def _make_client() -> MagicMock:
    client = MagicMock()
    client.user.create = AsyncMock(return_value=MagicMock(uuid_="user-uuid"))
    client.thread.create = AsyncMock(return_value=MagicMock(uuid_="thread-uuid"))
    return client


@pytest.mark.asyncio
async def test_create_user_returns_server_generated_user() -> None:
    client = _make_client()

    user = await create_user(
        client,
        first_name="Jane",
        last_name="Smith",
        email="jane@example.com",
    )

    assert user.uuid_ == "user-uuid"
    client.user.create.assert_awaited_once_with(
        first_name="Jane",
        last_name="Smith",
        email="jane@example.com",
    )


@pytest.mark.asyncio
async def test_create_thread_uses_user_uuid_and_returns_generated_thread() -> None:
    client = _make_client()

    thread = await create_thread(client, user_uuid="user-uuid")

    assert thread.uuid_ == "thread-uuid"
    client.thread.create.assert_awaited_once_with(user_uuid="user-uuid")


@pytest.mark.asyncio
async def test_create_user_propagates_sdk_errors() -> None:
    client = _make_client()
    client.user.create.side_effect = RuntimeError("creation failed")

    with pytest.raises(RuntimeError, match="creation failed"):
        await create_user(client)
