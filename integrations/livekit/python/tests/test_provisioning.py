from unittest.mock import AsyncMock, MagicMock

import pytest

from zep_livekit.provisioning import create_thread, create_user


def make_client() -> MagicMock:
    client = MagicMock()
    client.user.create = AsyncMock()
    client.thread.create = AsyncMock()
    return client


@pytest.mark.asyncio
async def test_create_user_returns_sdk_user_and_passes_identity_fields() -> None:
    client = make_client()
    resource = MagicMock(uuid_="user-uuid")
    client.user.create.return_value = resource

    result = await create_user(
        client,
        first_name="Jane",
        last_name="Smith",
        email="jane@example.com",
    )

    assert result is resource
    client.user.create.assert_awaited_once_with(
        first_name="Jane",
        last_name="Smith",
        email="jane@example.com",
    )


@pytest.mark.asyncio
async def test_create_user_runs_hook_with_server_uuid() -> None:
    client = make_client()
    client.user.create.return_value = MagicMock(uuid_="server-uuid")
    hook = AsyncMock()

    result = await create_user(client, on_created=hook)

    assert result.uuid_ == "server-uuid"
    hook.assert_awaited_once_with(client, "server-uuid")


@pytest.mark.asyncio
async def test_create_user_propagates_sdk_errors() -> None:
    client = make_client()
    client.user.create.side_effect = RuntimeError("create failed")

    with pytest.raises(RuntimeError, match="create failed"):
        await create_user(client)


@pytest.mark.asyncio
async def test_create_user_propagates_hook_errors() -> None:
    client = make_client()
    client.user.create.return_value = MagicMock(uuid_="server-uuid")

    async def hook(_client: MagicMock, _uuid: str) -> None:
        raise RuntimeError("setup failed")

    with pytest.raises(RuntimeError, match="setup failed"):
        await create_user(client, on_created=hook)


@pytest.mark.asyncio
async def test_create_thread_uses_server_uuid_and_returns_sdk_thread() -> None:
    client = make_client()
    resource = MagicMock(uuid_="thread-uuid")
    client.thread.create.return_value = resource

    result = await create_thread(client, user_uuid="user-uuid")

    assert result is resource
    client.thread.create.assert_awaited_once_with(user_uuid="user-uuid")


@pytest.mark.asyncio
async def test_create_thread_propagates_sdk_errors() -> None:
    client = make_client()
    client.thread.create.side_effect = RuntimeError("create failed")

    with pytest.raises(RuntimeError, match="create failed"):
        await create_thread(client, user_uuid="user-uuid")
