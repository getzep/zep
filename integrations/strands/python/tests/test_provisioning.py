"""
Tests for out-of-band Zep resource provisioning: ``ensure_user``,
``ensure_thread``, and the ``on_created`` hook contract.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from zep_strands.provisioning import (
    _is_already_exists_error,
    ensure_thread,
    ensure_user,
)


def _make_mock_client() -> MagicMock:
    client = MagicMock()
    client.user = MagicMock()
    client.user.add = AsyncMock()
    client.thread = MagicMock()
    client.thread.create = AsyncMock()
    return client


class _ApiError(Exception):
    """Minimal stand-in for a typed Zep SDK error exposing ``status_code``."""

    def __init__(self, status_code: int, message: str = "error") -> None:
        self.status_code = status_code
        super().__init__(message)


class TestIsAlreadyExistsError:
    def test_409_status_code_is_conflict(self) -> None:
        assert _is_already_exists_error(_ApiError(409)) is True

    def test_400_with_already_exists_message_is_conflict(self) -> None:
        assert _is_already_exists_error(_ApiError(400, "user already exists")) is True

    def test_400_without_already_exists_message_is_genuine(self) -> None:
        assert _is_already_exists_error(_ApiError(400, "invalid payload")) is False

    def test_404_is_genuine_failure(self) -> None:
        assert _is_already_exists_error(_ApiError(404, "not found")) is False

    def test_500_with_conflict_wording_is_genuine_failure(self) -> None:
        assert _is_already_exists_error(_ApiError(500, "conflict while saving")) is False

    def test_untyped_already_exists_message_is_conflict(self) -> None:
        assert _is_already_exists_error(Exception("resource already exists")) is True

    def test_untyped_conflict_message_is_conflict(self) -> None:
        assert _is_already_exists_error(Exception("409 conflict")) is True

    def test_untyped_unrelated_message_is_genuine_failure(self) -> None:
        assert _is_already_exists_error(Exception("network timeout")) is False


class TestEnsureUser:
    @pytest.mark.asyncio
    async def test_ensure_user_created_signal(self) -> None:
        client = _make_mock_client()

        created = await ensure_user(client, user_id="u1")
        assert created is True
        client.user.add.assert_called_once_with(
            user_id="u1", first_name=None, last_name=None, email=None
        )

        client.user.add.side_effect = Exception("already exists")
        already_existed = await ensure_user(client, user_id="u1")
        assert already_existed is False

    @pytest.mark.asyncio
    async def test_ensure_user_propagates_genuine_failure(self) -> None:
        client = _make_mock_client()
        client.user.add.side_effect = _ApiError(500, "boom")
        with pytest.raises(_ApiError):
            await ensure_user(client, user_id="u1")

    @pytest.mark.asyncio
    async def test_on_created_runs_only_on_new_user(self) -> None:
        client = _make_mock_client()
        hook = AsyncMock()

        await ensure_user(client, user_id="u1", on_created=hook)
        hook.assert_awaited_once_with(client, "u1")

        hook.reset_mock()
        client.user.add.side_effect = Exception("already exists")
        await ensure_user(client, user_id="u1", on_created=hook)
        hook.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_on_created_failure_propagates(self) -> None:
        client = _make_mock_client()
        hook = AsyncMock(side_effect=RuntimeError("hook failed"))
        with pytest.raises(RuntimeError, match="hook failed"):
            await ensure_user(client, user_id="u1", on_created=hook)


class TestEnsureThread:
    @pytest.mark.asyncio
    async def test_ensure_thread_created_signal(self) -> None:
        client = _make_mock_client()

        created = await ensure_thread(client, thread_id="t1", user_id="u1")
        assert created is True
        client.thread.create.assert_called_once_with(thread_id="t1", user_id="u1")

        client.thread.create.side_effect = Exception("already exists")
        already_existed = await ensure_thread(client, thread_id="t1", user_id="u1")
        assert already_existed is False

    @pytest.mark.asyncio
    async def test_ensure_thread_propagates_genuine_failure(self) -> None:
        client = _make_mock_client()
        client.thread.create.side_effect = _ApiError(401, "unauthorized")
        with pytest.raises(_ApiError):
            await ensure_thread(client, thread_id="t1", user_id="u1")
