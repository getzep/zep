"""Tests for explicit V4 user and thread creation."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from zep_strands import create_thread, create_user


@pytest.mark.asyncio
async def test_create_user_returns_server_uuid_and_runs_hook() -> None:
    client = MagicMock()
    client.user.create = AsyncMock(return_value=SimpleNamespace(uuid_="server-user-uuid"))
    calls: list[str] = []

    async def on_created(_client: object, user_uuid: str) -> None:
        calls.append(user_uuid)

    user = await create_user(
        client,
        first_name="Ada",
        last_name="Lovelace",
        on_created=on_created,
    )

    assert user.uuid_ == "server-user-uuid"
    assert calls == ["server-user-uuid"]
    client.user.create.assert_awaited_once_with(first_name="Ada", last_name="Lovelace", email=None)


@pytest.mark.asyncio
async def test_create_user_propagates_sdk_and_hook_errors() -> None:
    client = MagicMock()
    client.user.create = AsyncMock(side_effect=RuntimeError("zep down"))
    with pytest.raises(RuntimeError, match="zep down"):
        await create_user(client)

    client.user.create = AsyncMock(return_value=SimpleNamespace(uuid_="user-1"))

    async def fail_hook(_client: object, _user_uuid: str) -> None:
        raise RuntimeError("setup failed")

    with pytest.raises(RuntimeError, match="setup failed"):
        await create_user(client, on_created=fail_hook)


@pytest.mark.asyncio
async def test_create_thread_returns_server_uuid() -> None:
    client = MagicMock()
    client.thread.create = AsyncMock(return_value=SimpleNamespace(uuid_="server-thread-uuid"))

    thread = await create_thread(client, user_uuid="user-1")

    assert thread.uuid_ == "server-thread-uuid"
    client.thread.create.assert_awaited_once_with(user_uuid="user-1")
