"""
Tests for out-of-band Zep resource provisioning: ``ensure_user``,
``ensure_thread``, and the ``on_created`` hook contract.

This package is sync-only (CrewAI's storage adapters use the synchronous
``Zep`` client), so ``ensure_user``/``ensure_thread`` are plain sync
functions -- there is no async twin to port.

Zep v4 answers a create conflict with ``409 resource_already_exists`` and
carries the UUID of the existing resource in ``details.uuid`` of the error
body. The helpers read that UUID and fetch the resource with
``user.get``/``thread.get``. Neither helper calls ``lookup``.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from zep_crewai.provisioning import (
    _conflict_uuid,
    ensure_thread,
    ensure_user,
)

USER_UUID = "11111111-1111-1111-1111-111111111111"
THREAD_UUID = "22222222-2222-2222-2222-222222222222"
GRAPH_UUID = "33333333-3333-3333-3333-333333333333"


def _make_mock_client() -> MagicMock:
    client = MagicMock()
    client.user = MagicMock()
    client.user.create = MagicMock(return_value=MagicMock(uuid_=USER_UUID, graph_uuid=GRAPH_UUID))
    client.thread = MagicMock()
    client.thread.create = MagicMock(return_value=MagicMock(uuid_=THREAD_UUID))
    return client


class _ApiError(Exception):
    """Minimal stand-in for a typed Zep SDK error exposing ``status_code``."""

    def __init__(self, status_code: int, message: str = "error") -> None:
        self.status_code = status_code
        super().__init__(message)


class _ConflictError(_ApiError):
    """A 409 whose body carries ``details.uuid`` like the v4 error shape."""

    def __init__(self, uuid: str | None = None) -> None:
        super().__init__(409, "resource already exists")
        details = {"uuid": uuid} if uuid else None
        self.body = SimpleNamespace(error=SimpleNamespace(details=details))


class TestConflictUuid:
    def test_409_with_details_uuid_returns_uuid(self) -> None:
        assert _conflict_uuid(_ConflictError(USER_UUID)) == USER_UUID

    def test_409_without_details_returns_none(self) -> None:
        assert _conflict_uuid(_ConflictError(None)) is None

    def test_409_with_non_dict_details_returns_none(self) -> None:
        exc = _ConflictError(None)
        exc.body = SimpleNamespace(error=SimpleNamespace(details="uuid-string"))
        assert _conflict_uuid(exc) is None

    def test_non_409_returns_none(self) -> None:
        assert _conflict_uuid(_ApiError(400, "already exists")) is None
        assert _conflict_uuid(_ApiError(404, "not found")) is None
        assert _conflict_uuid(_ApiError(500, "conflict while saving")) is None
        assert _conflict_uuid(_ApiError(401, "unauthorized")) is None

    def test_untyped_error_returns_none(self) -> None:
        assert _conflict_uuid(Exception("resource already exists")) is None


class TestEnsureUser:
    def test_ensure_user_created_signal(self) -> None:
        """The user and True on genuine creation, the user and False when the
        user already exists."""
        client = _make_mock_client()

        user, created = ensure_user(client, user_id="u1")
        assert created is True
        assert user.uuid_ == USER_UUID
        client.user.create.assert_called_once_with(user_id="u1")

        client.user.create.side_effect = _ConflictError(USER_UUID)
        client.user.get = MagicMock(return_value=MagicMock(uuid_=USER_UUID))
        existing, already_existed = ensure_user(client, user_id="u1")
        assert already_existed is False
        assert existing.uuid_ == USER_UUID
        client.user.get.assert_called_once_with(USER_UUID)

    def test_conflict_path_never_calls_lookup(self) -> None:
        client = _make_mock_client()
        client.user.create.side_effect = _ConflictError(USER_UUID)
        client.user.get = MagicMock(return_value=MagicMock(uuid_=USER_UUID))

        ensure_user(client, user_id="u1")

        client.user.lookup.assert_not_called()

    def test_conflict_without_uuid_propagates(self) -> None:
        """A 409 that does not carry the UUID of the existing resource is a
        genuine failure and raises."""
        client = _make_mock_client()
        client.user.create.side_effect = _ConflictError(None)

        with pytest.raises(_ConflictError):
            ensure_user(client, user_id="u1")

    def test_passes_identity_fields(self) -> None:
        client = _make_mock_client()

        ensure_user(
            client, user_id="u1", first_name="Jane", last_name="Smith", email="jane@example.com"
        )

        client.user.create.assert_called_once_with(
            user_id="u1", first_name="Jane", last_name="Smith", email="jane@example.com"
        )

    def test_on_created_not_fired_when_exists(self) -> None:
        client = _make_mock_client()
        client.user.create.side_effect = _ConflictError(USER_UUID)
        client.user.get = MagicMock(return_value=MagicMock(uuid_=USER_UUID))
        hook = MagicMock()

        _user, created = ensure_user(client, user_id="u1", on_created=hook)

        assert created is False
        hook.assert_not_called()

    def test_on_created_fires_on_new_user(self) -> None:
        client = _make_mock_client()
        hook = MagicMock()

        user, created = ensure_user(client, user_id="u1", on_created=hook)

        assert created is True
        hook.assert_called_once_with(client, user)

    def test_ensure_user_propagates_genuine_errors(self) -> None:
        """A known non-conflict status code (e.g. 401) must raise, regardless
        of message text."""
        client = _make_mock_client()
        client.user.create.side_effect = _ApiError(401, "unauthorized")

        with pytest.raises(_ApiError):
            ensure_user(client, user_id="u1")

    def test_hook_error_propagates_from_ensure_user(self) -> None:
        client = _make_mock_client()

        def _failing_hook(_client: MagicMock, _user: object) -> None:
            raise RuntimeError("setup failed")

        with pytest.raises(RuntimeError, match="setup failed"):
            ensure_user(client, user_id="u1", on_created=_failing_hook)


class TestEnsureThread:
    def test_returns_thread_on_actual_creation(self) -> None:
        client = _make_mock_client()

        thread, created = ensure_thread(client, thread_id="t1", user_uuid=USER_UUID)

        assert created is True
        assert thread.uuid_ == THREAD_UUID
        client.thread.create.assert_called_once_with(user_uuid=USER_UUID, thread_id="t1")

    def test_returns_false_when_already_exists(self) -> None:
        client = _make_mock_client()
        client.thread.create.side_effect = _ConflictError(THREAD_UUID)
        client.thread.get = MagicMock(return_value=MagicMock(uuid_=THREAD_UUID))

        thread, created = ensure_thread(client, thread_id="t1", user_uuid=USER_UUID)

        assert created is False
        assert thread.uuid_ == THREAD_UUID
        client.thread.get.assert_called_once_with(THREAD_UUID)

    def test_conflict_path_never_calls_lookup(self) -> None:
        client = _make_mock_client()
        client.thread.create.side_effect = _ConflictError(THREAD_UUID)
        client.thread.get = MagicMock(return_value=MagicMock(uuid_=THREAD_UUID))

        ensure_thread(client, thread_id="t1", user_uuid=USER_UUID)

        client.thread.lookup.assert_not_called()

    def test_conflict_without_uuid_propagates(self) -> None:
        client = _make_mock_client()
        client.thread.create.side_effect = _ConflictError(None)

        with pytest.raises(_ConflictError):
            ensure_thread(client, thread_id="t1", user_uuid=USER_UUID)

    def test_propagates_genuine_errors(self) -> None:
        client = _make_mock_client()
        client.thread.create.side_effect = _ApiError(500, "internal error")

        with pytest.raises(_ApiError):
            ensure_thread(client, thread_id="t1", user_uuid=USER_UUID)


class TestStorageDoesNotProvision:
    """The storage adapters take UUIDs, so they never create a resource."""

    def test_user_storage_does_not_create_user_or_thread(self) -> None:
        from zep_cloud.client import Zep

        from zep_crewai import ZepUserStorage

        client = MagicMock(spec=Zep)
        client.user = MagicMock()
        client.thread = MagicMock()
        client.thread.add_messages = MagicMock()

        storage = ZepUserStorage(
            client=client,
            user_uuid=USER_UUID,
            thread_uuid=THREAD_UUID,
            graph_uuid=GRAPH_UUID,
        )
        storage.save("hi", metadata={"type": "message", "role": "user"})

        client.user.create.assert_not_called()
        client.thread.create.assert_not_called()
        client.thread.add_messages.assert_called_once()

    def test_user_storage_rejects_on_created(self) -> None:
        """The storage adapters no longer provision, so ``on_created`` is not
        a constructor argument."""
        from zep_cloud.client import Zep

        from zep_crewai import ZepUserStorage

        client = MagicMock(spec=Zep)

        storage = ZepUserStorage(
            client=client,
            user_uuid=USER_UUID,
            thread_uuid=THREAD_UUID,
            on_created=lambda c, u: None,
        )

        # The hook is accepted as opaque extra config and never called.
        assert "on_created" in storage._config

    def test_zep_storage_does_not_create_user_or_thread(self) -> None:
        from zep_cloud.client import Zep

        from zep_crewai import ZepStorage

        client = MagicMock(spec=Zep)
        client.user = MagicMock()
        client.thread = MagicMock()
        client.thread.add_messages = MagicMock()

        storage = ZepStorage(
            client=client,
            user_uuid=USER_UUID,
            thread_uuid=THREAD_UUID,
            graph_uuid=GRAPH_UUID,
        )
        storage.save("hi", metadata={"type": "message", "role": "user"})

        client.user.create.assert_not_called()
        client.thread.create.assert_not_called()
        client.thread.add_messages.assert_called_once()

    def test_graph_storage_has_no_on_created(self) -> None:
        """ZepGraphStorage is graph-scoped (no user), so it must not accept
        on_created."""
        from zep_cloud.client import Zep

        from zep_crewai import ZepGraphStorage

        client = MagicMock(spec=Zep)

        with pytest.raises(TypeError):
            ZepGraphStorage(client=client, graph_uuid=GRAPH_UUID, on_created=lambda c, u: None)
