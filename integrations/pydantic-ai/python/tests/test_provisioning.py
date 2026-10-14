"""Tests for explicit, out-of-band V4 resource creation."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from zep_pydantic_ai.provisioning import create_thread, create_user


@pytest.mark.asyncio
async def test_create_user_returns_server_uuid_and_forwards_identity() -> None:
    client = MagicMock()
    user = MagicMock(uuid_="user-uuid")
    client.user.create = AsyncMock(return_value=user)

    result = await create_user(
        client,
        first_name="Jane",
        last_name="Smith",
        email="jane@example.com",
    )

    assert result is user
    client.user.create.assert_awaited_once_with(
        first_name="Jane",
        last_name="Smith",
        email="jane@example.com",
    )
    assert "user_id" not in client.user.create.call_args.kwargs


@pytest.mark.asyncio
async def test_create_user_runs_setup_hook_with_server_uuid() -> None:
    client = MagicMock()
    client.user.create = AsyncMock(return_value=MagicMock(uuid_="user-uuid"))
    hook = AsyncMock()

    await create_user(client, on_created=hook)

    hook.assert_awaited_once_with(client, "user-uuid")


@pytest.mark.asyncio
async def test_create_user_propagates_setup_hook_errors() -> None:
    client = MagicMock()
    client.user.create = AsyncMock(return_value=MagicMock(uuid_="user-uuid"))
    hook = AsyncMock(side_effect=RuntimeError("setup failed"))

    with pytest.raises(RuntimeError, match="setup failed"):
        await create_user(client, on_created=hook)


@pytest.mark.asyncio
async def test_create_user_rejects_response_without_uuid_when_hook_is_set() -> None:
    client = MagicMock()
    client.user.create = AsyncMock(return_value=MagicMock(uuid_=None))

    with pytest.raises(RuntimeError, match="without a UUID"):
        await create_user(client, on_created=AsyncMock())


@pytest.mark.asyncio
async def test_create_thread_returns_server_uuid() -> None:
    client = MagicMock()
    thread = MagicMock(uuid_="thread-uuid")
    client.thread.create = AsyncMock(return_value=thread)

    result = await create_thread(client, user_uuid="user-uuid")

    assert result is thread
    client.thread.create.assert_awaited_once_with(user_uuid="user-uuid")
    assert "thread_id" not in client.thread.create.call_args.kwargs
