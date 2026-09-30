"""
Tests for out-of-band Zep resource provisioning (zep_langgraph.provisioning).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from zep_langgraph.provisioning import (
    create_thread,
    create_thread_sync,
    create_user,
    create_user_sync,
)

USER_UUID = "11111111-1111-1111-1111-111111111111"
GRAPH_UUID = "22222222-2222-2222-2222-222222222222"
THREAD_UUID = "33333333-3333-3333-3333-333333333333"


def _user() -> MagicMock:
    user = MagicMock()
    user.uuid_ = USER_UUID
    user.graph_uuid = GRAPH_UUID
    return user


def _thread() -> MagicMock:
    thread = MagicMock()
    thread.uuid_ = THREAD_UUID
    thread.user_uuid = USER_UUID
    return thread


def _make_async_client() -> MagicMock:
    client = MagicMock()
    client.user = MagicMock()
    client.user.create = AsyncMock(return_value=_user())
    client.thread = MagicMock()
    client.thread.create = AsyncMock(return_value=_thread())
    return client


def _make_sync_client() -> MagicMock:
    client = MagicMock()
    client.user = MagicMock()
    client.user.create = MagicMock(return_value=_user())
    client.thread = MagicMock()
    client.thread.create = MagicMock(return_value=_thread())
    return client


class TestCreateUser:
    @pytest.mark.asyncio
    async def test_returns_created_user_with_uuids(self) -> None:
        client = _make_async_client()
        user = await create_user(client, first_name="Alice", last_name="Smith")
        assert user.uuid_ == USER_UUID
        assert user.graph_uuid == GRAPH_UUID
        client.user.create.assert_awaited_once_with(
            first_name="Alice", last_name="Smith", email=None
        )

    @pytest.mark.asyncio
    async def test_no_legacy_user_id_is_sent(self) -> None:
        client = _make_async_client()
        await create_user(client)
        assert "user_id" not in client.user.create.await_args.kwargs

    @pytest.mark.asyncio
    async def test_on_created_receives_the_user(self) -> None:
        client = _make_async_client()
        hook = AsyncMock()
        user = await create_user(client, on_created=hook)
        hook.assert_awaited_once_with(client, user)

    @pytest.mark.asyncio
    async def test_sdk_error_propagates(self) -> None:
        client = _make_async_client()
        client.user.create.side_effect = RuntimeError("boom")
        with pytest.raises(RuntimeError, match="boom"):
            await create_user(client)

    @pytest.mark.asyncio
    async def test_hook_error_propagates(self) -> None:
        client = _make_async_client()
        hook = AsyncMock(side_effect=ValueError("hook failed"))
        with pytest.raises(ValueError, match="hook failed"):
            await create_user(client, on_created=hook)


class TestCreateThread:
    @pytest.mark.asyncio
    async def test_returns_created_thread(self) -> None:
        client = _make_async_client()
        thread = await create_thread(client, user_uuid=USER_UUID)
        assert thread.uuid_ == THREAD_UUID
        client.thread.create.assert_awaited_once_with(user_uuid=USER_UUID)

    @pytest.mark.asyncio
    async def test_sdk_error_propagates(self) -> None:
        client = _make_async_client()
        client.thread.create.side_effect = RuntimeError("boom")
        with pytest.raises(RuntimeError, match="boom"):
            await create_thread(client, user_uuid=USER_UUID)


class TestCreateUserSync:
    def test_returns_created_user_with_uuids(self) -> None:
        client = _make_sync_client()
        user = create_user_sync(client, email="alice@example.com")
        assert user.uuid_ == USER_UUID
        assert user.graph_uuid == GRAPH_UUID
        client.user.create.assert_called_once_with(
            first_name=None, last_name=None, email="alice@example.com"
        )

    def test_on_created_receives_the_user(self) -> None:
        client = _make_sync_client()
        hook = MagicMock()
        user = create_user_sync(client, on_created=hook)
        hook.assert_called_once_with(client, user)

    def test_sdk_error_propagates(self) -> None:
        client = _make_sync_client()
        client.user.create.side_effect = RuntimeError("boom")
        with pytest.raises(RuntimeError, match="boom"):
            create_user_sync(client)

    def test_hook_error_propagates(self) -> None:
        client = _make_sync_client()
        hook = MagicMock(side_effect=ValueError("hook failed"))
        with pytest.raises(ValueError, match="hook failed"):
            create_user_sync(client, on_created=hook)


class TestCreateThreadSync:
    def test_returns_created_thread(self) -> None:
        client = _make_sync_client()
        thread = create_thread_sync(client, user_uuid=USER_UUID)
        assert thread.uuid_ == THREAD_UUID
        client.thread.create.assert_called_once_with(user_uuid=USER_UUID)

    def test_sdk_error_propagates(self) -> None:
        client = _make_sync_client()
        client.thread.create.side_effect = RuntimeError("boom")
        with pytest.raises(RuntimeError, match="boom"):
            create_thread_sync(client, user_uuid=USER_UUID)
