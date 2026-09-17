"""
Tests for explicit, out-of-band Zep provisioning helpers (``zep_adk.provisioning``).

``create_user`` / ``create_thread`` provision Zep resources out-of-band (before
the first agent turn) and return the created SDK object, so the caller reads the
server-generated UUID from the response.  Both helpers fail loudly.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from zep_cloud.core.api_error import ApiError
from zep_cloud.errors import ConflictError

from zep_adk.provisioning import create_thread, create_user

USER_UUID = "11111111-1111-1111-1111-111111111111"
GRAPH_UUID = "22222222-2222-2222-2222-222222222222"
THREAD_UUID = "33333333-3333-3333-3333-333333333333"


def _make_client() -> MagicMock:
    user = MagicMock()
    user.uuid_ = USER_UUID
    user.graph_uuid = GRAPH_UUID
    thread = MagicMock()
    thread.uuid_ = THREAD_UUID

    client = MagicMock()
    client.user = MagicMock()
    client.user.create = AsyncMock(return_value=user)
    client.thread = MagicMock()
    client.thread.create = AsyncMock(return_value=thread)
    return client


def _server_error(message: str = "internal error") -> ApiError:
    return ApiError(status_code=500, body={"message": message})


class TestCreateUser:
    """create_user: one call to user.create, returns the created user."""

    @pytest.mark.asyncio
    async def test_create_user_returns_user_with_uuid(self) -> None:
        """The helper returns the created user, which carries the UUIDs."""
        client = _make_client()

        user = await create_user(
            client,
            user_id="user-1",
            first_name="Jane",
            last_name="Smith",
            email="jane@example.com",
        )

        assert user.uuid_ == USER_UUID
        assert user.graph_uuid == GRAPH_UUID
        client.user.create.assert_called_once_with(
            user_id="user-1",
            first_name="Jane",
            last_name="Smith",
            email="jane@example.com",
        )

    @pytest.mark.asyncio
    async def test_create_user_defaults(self) -> None:
        """Every field is optional; Zep generates the UUID."""
        client = _make_client()

        user = await create_user(client)

        assert user.uuid_ == USER_UUID
        client.user.create.assert_called_once_with(
            user_id=None,
            first_name=None,
            last_name=None,
            email=None,
        )

    @pytest.mark.asyncio
    async def test_create_user_conflict_raises(self) -> None:
        """A duplicate user_id is a failure; the helper does not swallow it."""
        client = _make_client()
        client.user.create.side_effect = ConflictError(body={"message": "user already exists"})

        with pytest.raises(ConflictError):
            await create_user(client, user_id="user-1")

    @pytest.mark.asyncio
    async def test_create_user_server_error_raises(self) -> None:
        """A genuine failure propagates -- provisioning must be loud."""
        client = _make_client()
        client.user.create.side_effect = _server_error()

        with pytest.raises(ApiError):
            await create_user(client, user_id="user-1")

    @pytest.mark.asyncio
    async def test_create_user_generic_exception_raises(self) -> None:
        """A network or auth failure propagates unchanged."""
        client = _make_client()
        client.user.create.side_effect = RuntimeError("network timeout")

        with pytest.raises(RuntimeError, match="network timeout"):
            await create_user(client)


class TestCreateThread:
    """create_thread: addresses the owner by UUID, returns the created thread."""

    @pytest.mark.asyncio
    async def test_create_thread_returns_thread_with_uuid(self) -> None:
        """The helper returns the created thread, which carries the UUID."""
        client = _make_client()

        thread = await create_thread(client, user_uuid=USER_UUID, thread_id="thread-1")

        assert thread.uuid_ == THREAD_UUID
        client.thread.create.assert_called_once_with(user_uuid=USER_UUID, thread_id="thread-1")

    @pytest.mark.asyncio
    async def test_create_thread_without_name(self) -> None:
        """The human-readable thread_id is optional."""
        client = _make_client()

        thread = await create_thread(client, user_uuid=USER_UUID)

        assert thread.uuid_ == THREAD_UUID
        client.thread.create.assert_called_once_with(user_uuid=USER_UUID, thread_id=None)

    @pytest.mark.asyncio
    async def test_create_thread_server_error_raises(self) -> None:
        """A genuine failure propagates -- provisioning must be loud."""
        client = _make_client()
        client.thread.create.side_effect = _server_error()

        with pytest.raises(ApiError):
            await create_thread(client, user_uuid=USER_UUID)
