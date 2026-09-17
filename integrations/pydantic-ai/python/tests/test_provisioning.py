"""
Tests for out-of-band Zep resource provisioning: ``create_user`` and
``create_thread``.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from zep_pydantic_ai.provisioning import create_thread, create_user


def _make_mock_client() -> MagicMock:
    client = MagicMock()
    client.user = MagicMock()
    client.user.create = AsyncMock(
        return_value=MagicMock(uuid_="user-uuid-1", graph_uuid="graph-uuid-1")
    )
    client.thread = MagicMock()
    client.thread.create = AsyncMock(return_value=MagicMock(uuid_="thread-uuid-1"))
    return client


class _ApiError(Exception):
    """Minimal stand-in for a typed Zep SDK error exposing ``status_code``."""

    def __init__(self, status_code: int, message: str = "error") -> None:
        self.status_code = status_code
        super().__init__(message)


class TestCreateUser:
    @pytest.mark.asyncio
    async def test_returns_created_user(self) -> None:
        client = _make_mock_client()

        user = await create_user(client)

        assert user.uuid_ == "user-uuid-1"
        assert user.graph_uuid == "graph-uuid-1"
        client.user.create.assert_called_once_with(first_name=None, last_name=None, email=None)

    @pytest.mark.asyncio
    async def test_passes_identity_fields(self) -> None:
        client = _make_mock_client()

        await create_user(client, first_name="Jane", last_name="Smith", email="jane@example.com")

        client.user.create.assert_called_once_with(
            first_name="Jane", last_name="Smith", email="jane@example.com"
        )

    @pytest.mark.asyncio
    async def test_does_not_send_a_user_id(self) -> None:
        """v4 addresses a user by UUID; a create call sends no identifier."""
        client = _make_mock_client()

        await create_user(client, first_name="Jane")

        kwargs = client.user.create.call_args.kwargs
        assert "user_id" not in kwargs

    @pytest.mark.asyncio
    async def test_propagates_errors(self) -> None:
        client = _make_mock_client()
        client.user.create.side_effect = _ApiError(401, "unauthorized")

        with pytest.raises(_ApiError):
            await create_user(client)


class TestCreateThread:
    @pytest.mark.asyncio
    async def test_returns_created_thread(self) -> None:
        client = _make_mock_client()

        thread = await create_thread(client, user_uuid="user-uuid-1")

        assert thread.uuid_ == "thread-uuid-1"
        client.thread.create.assert_called_once_with(user_uuid="user-uuid-1")

    @pytest.mark.asyncio
    async def test_propagates_errors(self) -> None:
        client = _make_mock_client()
        client.thread.create.side_effect = _ApiError(500, "internal error")

        with pytest.raises(_ApiError):
            await create_thread(client, user_uuid="user-uuid-1")
