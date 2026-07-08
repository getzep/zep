"""
Tests for out-of-band Zep resource provisioning: ``ensure_user``,
``ensure_thread``, and the ``on_created`` hook contract.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from zep_livekit.provisioning import (
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
        """A typed error with a non-conflict status code is a genuine failure
        no matter what its message says."""
        assert _is_already_exists_error(_ApiError(500, "conflict while saving")) is False

    def test_untyped_already_exists_message_is_conflict(self) -> None:
        assert _is_already_exists_error(Exception("resource already exists")) is True

    def test_untyped_unrelated_message_is_genuine_failure(self) -> None:
        assert _is_already_exists_error(Exception("network timeout")) is False


class TestEnsureUser:
    @pytest.mark.asyncio
    async def test_ensure_user_created_signal(self) -> None:
        """True on genuine creation, False when the user already exists."""
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
    async def test_passes_identity_fields(self) -> None:
        client = _make_mock_client()

        await ensure_user(
            client, user_id="u1", first_name="Jane", last_name="Smith", email="jane@example.com"
        )

        client.user.add.assert_called_once_with(
            user_id="u1", first_name="Jane", last_name="Smith", email="jane@example.com"
        )

    @pytest.mark.asyncio
    async def test_on_created_not_fired_when_exists(self) -> None:
        client = _make_mock_client()
        client.user.add.side_effect = _ApiError(409, "already exists")
        hook = AsyncMock()

        created = await ensure_user(client, user_id="u1", on_created=hook)

        assert created is False
        hook.assert_not_called()

    @pytest.mark.asyncio
    async def test_ensure_user_propagates_genuine_errors(self) -> None:
        """A known non-conflict status code (e.g. 401) must raise, regardless
        of message text."""
        client = _make_mock_client()
        client.user.add.side_effect = _ApiError(401, "unauthorized")

        with pytest.raises(_ApiError):
            await ensure_user(client, user_id="u1")

    @pytest.mark.asyncio
    async def test_on_created_fires_on_actual_creation(self) -> None:
        client = _make_mock_client()
        hook = AsyncMock()

        created = await ensure_user(client, user_id="u1", on_created=hook)

        assert created is True
        hook.assert_called_once_with(client, "u1")

    @pytest.mark.asyncio
    async def test_hook_error_propagates_from_ensure_user(self) -> None:
        client = _make_mock_client()

        async def _failing_hook(_client: MagicMock, _user_id: str) -> None:
            raise RuntimeError("setup failed")

        with pytest.raises(RuntimeError, match="setup failed"):
            await ensure_user(client, user_id="u1", on_created=_failing_hook)


class TestEnsureThread:
    @pytest.mark.asyncio
    async def test_returns_true_on_actual_creation(self) -> None:
        client = _make_mock_client()

        created = await ensure_thread(client, thread_id="t1", user_id="u1")

        assert created is True
        client.thread.create.assert_called_once_with(thread_id="t1", user_id="u1")

    @pytest.mark.asyncio
    async def test_returns_false_when_already_exists(self) -> None:
        client = _make_mock_client()
        client.thread.create.side_effect = _ApiError(409, "already exists")

        created = await ensure_thread(client, thread_id="t1", user_id="u1")

        assert created is False

    @pytest.mark.asyncio
    async def test_propagates_genuine_errors(self) -> None:
        client = _make_mock_client()
        client.thread.create.side_effect = _ApiError(500, "internal error")

        with pytest.raises(_ApiError):
            await ensure_thread(client, thread_id="t1", user_id="u1")
