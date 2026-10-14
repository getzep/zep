"""Tests for explicit V4 user and thread provisioning."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from zep_ms_agent_framework import create_thread, create_user


def _client() -> MagicMock:
    client = MagicMock()
    client.user.create = AsyncMock(return_value=SimpleNamespace(uuid_="server-user"))
    client.thread.create = AsyncMock(return_value=SimpleNamespace(uuid_="server-thread"))
    return client


class TestCreateUser:
    @pytest.mark.asyncio
    async def test_returns_server_generated_user(self) -> None:
        client = _client()

        user = await create_user(
            client,
            first_name="Jane",
            last_name="Smith",
            email="jane@example.com",
        )

        assert user.uuid_ == "server-user"
        client.user.create.assert_awaited_once_with(
            first_name="Jane",
            last_name="Smith",
            email="jane@example.com",
        )

    @pytest.mark.asyncio
    async def test_passes_empty_identity_fields(self) -> None:
        client = _client()

        await create_user(client)

        client.user.create.assert_awaited_once_with(
            first_name=None,
            last_name=None,
            email=None,
        )

    @pytest.mark.asyncio
    async def test_calls_on_created_with_server_uuid(self) -> None:
        client = _client()
        hook = AsyncMock()

        await create_user(client, on_created=hook)

        hook.assert_awaited_once_with(client, "server-user")

    @pytest.mark.asyncio
    async def test_propagates_user_creation_error(self) -> None:
        client = _client()
        client.user.create.side_effect = RuntimeError("create failed")

        with pytest.raises(RuntimeError, match="create failed"):
            await create_user(client)

    @pytest.mark.asyncio
    async def test_propagates_hook_error(self) -> None:
        client = _client()
        hook = AsyncMock(side_effect=RuntimeError("setup failed"))

        with pytest.raises(RuntimeError, match="setup failed"):
            await create_user(client, on_created=hook)

        assert client.user.create.await_count == 1


class TestCreateThread:
    @pytest.mark.asyncio
    async def test_returns_server_generated_thread(self) -> None:
        client = _client()

        thread = await create_thread(client, user_uuid="server-user")

        assert thread.uuid_ == "server-thread"
        client.thread.create.assert_awaited_once_with(user_uuid="server-user")

    @pytest.mark.asyncio
    async def test_propagates_thread_creation_error(self) -> None:
        client = _client()
        client.thread.create.side_effect = RuntimeError("create failed")

        with pytest.raises(RuntimeError, match="create failed"):
            await create_thread(client, user_uuid="server-user")
