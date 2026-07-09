"""
Tests for out-of-band Zep resource provisioning (zep_langgraph.provisioning).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from zep_langgraph.provisioning import (
    ensure_thread,
    ensure_thread_sync,
    ensure_user,
    ensure_user_sync,
)


class _ConflictError(Exception):
    def __init__(self, message: str = "already exists", status_code: int = 409) -> None:
        super().__init__(message)
        self.status_code = status_code


def _make_async_client() -> MagicMock:
    client = MagicMock()
    client.user = MagicMock()
    client.user.add = AsyncMock()
    client.thread = MagicMock()
    client.thread.create = AsyncMock()
    return client


def _make_sync_client() -> MagicMock:
    client = MagicMock()
    client.user = MagicMock()
    client.user.add = MagicMock()
    client.thread = MagicMock()
    client.thread.create = MagicMock()
    return client


class TestEnsureUser:
    @pytest.mark.asyncio
    async def test_ensure_user_created_signal(self) -> None:
        client = _make_async_client()
        created = await ensure_user(client, user_id="u1")
        assert created is True
        client.user.add.assert_awaited_once_with(
            user_id="u1", first_name=None, last_name=None, email=None
        )

    @pytest.mark.asyncio
    async def test_on_created_not_fired_when_exists(self) -> None:
        client = _make_async_client()
        client.user.add.side_effect = _ConflictError()
        hook = AsyncMock()
        created = await ensure_user(client, user_id="u1", on_created=hook)
        assert created is False
        hook.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_on_created_fires_when_new(self) -> None:
        client = _make_async_client()
        hook = AsyncMock()
        created = await ensure_user(client, user_id="u1", on_created=hook)
        assert created is True
        hook.assert_awaited_once_with(client, "u1")

    @pytest.mark.asyncio
    async def test_ensure_user_propagates_genuine_errors(self) -> None:
        client = _make_async_client()
        client.user.add.side_effect = RuntimeError("boom")
        with pytest.raises(RuntimeError, match="boom"):
            await ensure_user(client, user_id="u1")

    @pytest.mark.asyncio
    async def test_hook_error_propagates(self) -> None:
        client = _make_async_client()
        hook = AsyncMock(side_effect=ValueError("hook failed"))
        with pytest.raises(ValueError, match="hook failed"):
            await ensure_user(client, user_id="u1", on_created=hook)

    @pytest.mark.asyncio
    async def test_400_already_exists_treated_as_conflict(self) -> None:
        client = _make_async_client()
        client.user.add.side_effect = _ConflictError("Bad Request: already exists", 400)
        created = await ensure_user(client, user_id="u1")
        assert created is False

    @pytest.mark.asyncio
    async def test_genuine_500_not_treated_as_conflict(self) -> None:
        client = _make_async_client()
        client.user.add.side_effect = _ConflictError("server error, conflict mentioned", 500)
        with pytest.raises(_ConflictError):
            await ensure_user(client, user_id="u1")


class TestEnsureThread:
    @pytest.mark.asyncio
    async def test_ensure_thread_created_signal(self) -> None:
        client = _make_async_client()
        created = await ensure_thread(client, thread_id="t1", user_id="u1")
        assert created is True
        client.thread.create.assert_awaited_once_with(thread_id="t1", user_id="u1")

    @pytest.mark.asyncio
    async def test_ensure_thread_already_exists(self) -> None:
        client = _make_async_client()
        client.thread.create.side_effect = _ConflictError()
        created = await ensure_thread(client, thread_id="t1", user_id="u1")
        assert created is False

    @pytest.mark.asyncio
    async def test_ensure_thread_propagates_genuine_errors(self) -> None:
        client = _make_async_client()
        client.thread.create.side_effect = RuntimeError("boom")
        with pytest.raises(RuntimeError, match="boom"):
            await ensure_thread(client, thread_id="t1", user_id="u1")


class TestEnsureUserSync:
    def test_ensure_user_sync_created_signal(self) -> None:
        client = _make_sync_client()
        created = ensure_user_sync(client, user_id="u1")
        assert created is True
        client.user.add.assert_called_once_with(
            user_id="u1", first_name=None, last_name=None, email=None
        )

    def test_ensure_user_sync_already_exists(self) -> None:
        client = _make_sync_client()
        client.user.add.side_effect = _ConflictError()
        hook = MagicMock()
        created = ensure_user_sync(client, user_id="u1", on_created=hook)
        assert created is False
        hook.assert_not_called()

    def test_ensure_user_sync_on_created_fires(self) -> None:
        client = _make_sync_client()
        hook = MagicMock()
        created = ensure_user_sync(client, user_id="u1", on_created=hook)
        assert created is True
        hook.assert_called_once_with(client, "u1")

    def test_ensure_user_sync_propagates_genuine_errors(self) -> None:
        client = _make_sync_client()
        client.user.add.side_effect = RuntimeError("boom")
        with pytest.raises(RuntimeError, match="boom"):
            ensure_user_sync(client, user_id="u1")

    def test_ensure_user_sync_hook_error_propagates(self) -> None:
        client = _make_sync_client()
        hook = MagicMock(side_effect=ValueError("hook failed"))
        with pytest.raises(ValueError, match="hook failed"):
            ensure_user_sync(client, user_id="u1", on_created=hook)


class TestEnsureThreadSync:
    def test_ensure_thread_sync_created_signal(self) -> None:
        client = _make_sync_client()
        created = ensure_thread_sync(client, thread_id="t1", user_id="u1")
        assert created is True
        client.thread.create.assert_called_once_with(thread_id="t1", user_id="u1")

    def test_ensure_thread_sync_already_exists(self) -> None:
        client = _make_sync_client()
        client.thread.create.side_effect = _ConflictError()
        created = ensure_thread_sync(client, thread_id="t1", user_id="u1")
        assert created is False

    def test_ensure_thread_sync_propagates_genuine_errors(self) -> None:
        client = _make_sync_client()
        client.thread.create.side_effect = RuntimeError("boom")
        with pytest.raises(RuntimeError, match="boom"):
            ensure_thread_sync(client, thread_id="t1", user_id="u1")
