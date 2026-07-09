"""
Tests for explicit, out-of-band Zep provisioning helpers (``zep_adk.provisioning``).

``ensure_user`` / ``ensure_thread`` idempotently provision Zep resources
out-of-band (before the first agent turn), returning whether the resource was
newly created and raising on genuine failures.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from zep_cloud.core.api_error import ApiError
from zep_cloud.errors import BadRequestError, ConflictError, NotFoundError

from zep_adk.provisioning import ensure_thread, ensure_user


def _make_client() -> MagicMock:
    client = MagicMock()
    client.user = MagicMock()
    client.user.add = AsyncMock()
    client.thread = MagicMock()
    client.thread.create = AsyncMock()
    return client


def _conflict_error(message: str = "User already exists") -> ConflictError:
    return ConflictError(body={"message": message})


def _bad_request_already_exists(message: str = "user already exists") -> BadRequestError:
    return BadRequestError(body={"message": message})


def _server_error(message: str = "internal error") -> ApiError:
    return ApiError(status_code=500, body={"message": message})


class TestEnsureUser:
    """ensure_user: idempotent, create-then-catch-conflict, out-of-band."""

    @pytest.mark.asyncio
    async def test_ensure_user_created(self) -> None:
        """New user: returns True; user.add called with the given fields."""
        client = _make_client()

        result = await ensure_user(
            client,
            user_id="user-1",
            first_name="Jane",
            last_name="Smith",
            email="jane@example.com",
        )

        assert result is True
        client.user.add.assert_called_once_with(
            user_id="user-1",
            first_name="Jane",
            last_name="Smith",
            email="jane@example.com",
        )

    @pytest.mark.asyncio
    async def test_ensure_user_created_defaults(self) -> None:
        """Optional fields default to None when omitted."""
        client = _make_client()

        result = await ensure_user(client, user_id="user-1")

        assert result is True
        client.user.add.assert_called_once_with(
            user_id="user-1",
            first_name=None,
            last_name=None,
            email=None,
        )

    @pytest.mark.asyncio
    async def test_ensure_user_already_exists_conflict_409(self) -> None:
        """409 ConflictError shape -> returns False, does not raise."""
        client = _make_client()
        client.user.add.side_effect = _conflict_error()

        result = await ensure_user(client, user_id="user-1")

        assert result is False

    @pytest.mark.asyncio
    async def test_ensure_user_already_exists_400_message(self) -> None:
        """400 'already exists' message shape -> returns False, does not raise."""
        client = _make_client()
        client.user.add.side_effect = _bad_request_already_exists()

        result = await ensure_user(client, user_id="user-1")

        assert result is False

    @pytest.mark.asyncio
    async def test_ensure_user_genuine_failure_raises(self) -> None:
        """Genuine failure (e.g. 500) propagates -- provisioning must be loud."""
        client = _make_client()
        client.user.add.side_effect = _server_error()

        with pytest.raises(ApiError):
            await ensure_user(client, user_id="user-1")

    @pytest.mark.asyncio
    async def test_ensure_user_generic_exception_raises(self) -> None:
        """Non-Zep exceptions (auth, network) are not treated as conflicts."""
        client = _make_client()
        client.user.add.side_effect = RuntimeError("network timeout")

        with pytest.raises(RuntimeError, match="network timeout"):
            await ensure_user(client, user_id="user-1")

    @pytest.mark.asyncio
    async def test_on_created_called_once_when_created(self) -> None:
        """on_created runs exactly once, awaited before ensure_user returns."""
        client = _make_client()
        calls: list[tuple[object, str]] = []

        async def hook(c: object, user_id: str) -> None:
            calls.append((c, user_id))

        result = await ensure_user(client, user_id="user-1", on_created=hook)

        assert result is True
        assert calls == [(client, "user-1")]

    @pytest.mark.asyncio
    async def test_on_created_not_called_when_already_exists(self) -> None:
        """on_created must NOT fire for users that already existed."""
        client = _make_client()
        client.user.add.side_effect = _conflict_error()
        hook = AsyncMock()

        result = await ensure_user(client, user_id="user-1", on_created=hook)

        assert result is False
        hook.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_created_exception_propagates(self) -> None:
        """A failing on_created hook must propagate to the caller (no swallow)."""
        client = _make_client()
        hook = AsyncMock(side_effect=RuntimeError("setup failed"))

        with pytest.raises(RuntimeError, match="setup failed"):
            await ensure_user(client, user_id="user-1", on_created=hook)

        # The user.add call itself succeeded -- only the hook failed.
        client.user.add.assert_called_once()


class TestEnsureThread:
    """ensure_thread: idempotent, create-then-catch-conflict, out-of-band."""

    @pytest.mark.asyncio
    async def test_ensure_thread_created(self) -> None:
        """New thread: returns True; thread.create called with given fields."""
        client = _make_client()

        result = await ensure_thread(client, thread_id="thread-1", user_id="user-1")

        assert result is True
        client.thread.create.assert_called_once_with(thread_id="thread-1", user_id="user-1")

    @pytest.mark.asyncio
    async def test_ensure_thread_already_exists_conflict_409(self) -> None:
        """409 ConflictError shape -> returns False, does not raise."""
        client = _make_client()
        client.thread.create.side_effect = _conflict_error("Thread already exists")

        result = await ensure_thread(client, thread_id="thread-1", user_id="user-1")

        assert result is False

    @pytest.mark.asyncio
    async def test_ensure_thread_already_exists_400_message(self) -> None:
        """400 'already exists' message shape -> returns False, does not raise."""
        client = _make_client()
        client.thread.create.side_effect = _bad_request_already_exists("thread already exists")

        result = await ensure_thread(client, thread_id="thread-1", user_id="user-1")

        assert result is False

    @pytest.mark.asyncio
    async def test_ensure_thread_genuine_failure_raises(self) -> None:
        """Genuine failure (e.g. 500) propagates -- provisioning must be loud."""
        client = _make_client()
        client.thread.create.side_effect = _server_error()

        with pytest.raises(ApiError):
            await ensure_thread(client, thread_id="thread-1", user_id="user-1")


class TestIsAlreadyExistsErrorHeuristic:
    """The private heuristic must recognise both typed and message-based shapes."""

    def test_recognises_conflict_error_type(self) -> None:
        from zep_adk.provisioning import _is_already_exists_error

        assert _is_already_exists_error(_conflict_error()) is True

    def test_recognises_409_status_code(self) -> None:
        from zep_adk.provisioning import _is_already_exists_error

        assert _is_already_exists_error(ApiError(status_code=409, body={})) is True

    def test_recognises_400_already_exists_message(self) -> None:
        from zep_adk.provisioning import _is_already_exists_error

        assert _is_already_exists_error(_bad_request_already_exists()) is True

    def test_recognises_already_exists_substring(self) -> None:
        from zep_adk.provisioning import _is_already_exists_error

        assert _is_already_exists_error(RuntimeError("Resource already exists")) is True

    def test_recognises_conflict_substring(self) -> None:
        from zep_adk.provisioning import _is_already_exists_error

        assert _is_already_exists_error(RuntimeError("conflict detected")) is True

    def test_does_not_recognise_genuine_failure(self) -> None:
        from zep_adk.provisioning import _is_already_exists_error

        assert _is_already_exists_error(_server_error()) is False
        assert _is_already_exists_error(RuntimeError("network timeout")) is False

    def test_does_not_recognise_generic_404(self) -> None:
        """A NotFound error is not an already-exists conflict."""
        from zep_adk.provisioning import _is_already_exists_error

        assert _is_already_exists_error(NotFoundError(body={"message": "not found"})) is False

    def test_typed_non_conflict_status_ignores_message_heuristic(self) -> None:
        """A typed error with a known non-conflict status code is a genuine
        failure even when its message mentions "conflict" or "already
        exists" -- the substring fallback applies to untyped shapes only."""
        from zep_adk.provisioning import _is_already_exists_error

        assert (
            _is_already_exists_error(
                ApiError(status_code=500, body={"message": "transaction conflict, please retry"})
            )
            is False
        )
        assert (
            _is_already_exists_error(
                ApiError(status_code=500, body={"message": "user already exists (index rebuild)"})
            )
            is False
        )


class TestUserSetupHookExport:
    """UserSetupHook now lives in provisioning.py (re-exported from __init__)."""

    def test_user_setup_hook_importable_from_provisioning(self) -> None:
        from zep_adk.provisioning import UserSetupHook

        assert UserSetupHook is not None

    def test_user_setup_hook_importable_from_package(self) -> None:
        from zep_adk import UserSetupHook

        assert UserSetupHook is not None
