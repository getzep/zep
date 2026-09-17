"""
Tests for out-of-band Zep resource provisioning: ``create_user``,
``create_thread``, and the ``on_created`` hook contract.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from zep_strands.provisioning import create_thread, create_user

USER_UUID = "11111111-1111-1111-1111-111111111111"
THREAD_UUID = "22222222-2222-2222-2222-222222222222"


def _make_mock_client() -> MagicMock:
    client = MagicMock()
    client.user = MagicMock()
    client.user.create = AsyncMock(return_value=SimpleNamespace(uuid_=USER_UUID))
    client.thread = MagicMock()
    client.thread.create = AsyncMock(return_value=SimpleNamespace(uuid_=THREAD_UUID))
    return client


class _ApiError(Exception):
    """Minimal stand-in for a typed Zep SDK error exposing ``status_code``."""

    def __init__(self, status_code: int, message: str = "error") -> None:
        self.status_code = status_code
        super().__init__(message)


class TestCreateUser:
    @pytest.mark.asyncio
    async def test_create_user_returns_the_server_uuid(self) -> None:
        client = _make_mock_client()

        user = await create_user(client, user_id="u1", email="ada@example.com")

        assert user.uuid_ == USER_UUID
        client.user.create.assert_awaited_once_with(
            user_id="u1", first_name=None, last_name=None, email="ada@example.com"
        )

    @pytest.mark.asyncio
    async def test_create_user_propagates_failure(self) -> None:
        client = _make_mock_client()
        client.user.create.side_effect = _ApiError(500, "boom")
        with pytest.raises(_ApiError):
            await create_user(client)

    @pytest.mark.asyncio
    async def test_on_created_receives_the_user_uuid(self) -> None:
        client = _make_mock_client()
        hook = AsyncMock()

        await create_user(client, on_created=hook)

        hook.assert_awaited_once_with(client, USER_UUID)

    @pytest.mark.asyncio
    async def test_on_created_failure_propagates(self) -> None:
        client = _make_mock_client()
        hook = AsyncMock(side_effect=RuntimeError("hook failed"))
        with pytest.raises(RuntimeError, match="hook failed"):
            await create_user(client, on_created=hook)


class TestCreateThread:
    @pytest.mark.asyncio
    async def test_create_thread_returns_the_server_uuid(self) -> None:
        client = _make_mock_client()

        thread = await create_thread(client, user_uuid=USER_UUID, thread_id="t1")

        assert thread.uuid_ == THREAD_UUID
        client.thread.create.assert_awaited_once_with(user_uuid=USER_UUID, thread_id="t1")

    @pytest.mark.asyncio
    async def test_create_thread_propagates_failure(self) -> None:
        client = _make_mock_client()
        client.thread.create.side_effect = _ApiError(401, "unauthorized")
        with pytest.raises(_ApiError):
            await create_thread(client, user_uuid=USER_UUID)
