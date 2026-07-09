"""
Tests for out-of-band Zep resource provisioning: ``ensure_user``,
``ensure_thread``, and the ``on_created`` hook contract.

This package is sync-only (CrewAI's storage adapters use the synchronous
``Zep`` client), so ``ensure_user``/``ensure_thread`` are plain sync
functions -- there is no async twin to port.
"""

from unittest.mock import MagicMock

import pytest

from zep_crewai.provisioning import (
    _is_already_exists_error,
    ensure_thread,
    ensure_user,
)


def _make_mock_client() -> MagicMock:
    client = MagicMock()
    client.user = MagicMock()
    client.thread = MagicMock()
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

    def test_401_is_genuine_failure(self) -> None:
        assert _is_already_exists_error(_ApiError(401, "unauthorized")) is False

    def test_untyped_already_exists_message_is_conflict(self) -> None:
        assert _is_already_exists_error(Exception("resource already exists")) is True

    def test_untyped_conflict_message_is_conflict(self) -> None:
        assert _is_already_exists_error(Exception("409 conflict")) is True

    def test_untyped_unrelated_message_is_genuine_failure(self) -> None:
        assert _is_already_exists_error(Exception("network timeout")) is False


class TestEnsureUser:
    def test_ensure_user_created_signal(self) -> None:
        """True on genuine creation, False when the user already exists."""
        client = _make_mock_client()

        created = ensure_user(client, user_id="u1")
        assert created is True
        client.user.add.assert_called_once_with(
            user_id="u1", first_name=None, last_name=None, email=None
        )

        client.user.add.side_effect = Exception("already exists")
        already_existed = ensure_user(client, user_id="u1")
        assert already_existed is False

    def test_passes_identity_fields(self) -> None:
        client = _make_mock_client()

        ensure_user(
            client, user_id="u1", first_name="Jane", last_name="Smith", email="jane@example.com"
        )

        client.user.add.assert_called_once_with(
            user_id="u1", first_name="Jane", last_name="Smith", email="jane@example.com"
        )

    def test_on_created_not_fired_when_exists(self) -> None:
        client = _make_mock_client()
        client.user.add.side_effect = _ApiError(409, "already exists")
        hook = MagicMock()

        created = ensure_user(client, user_id="u1", on_created=hook)

        assert created is False
        hook.assert_not_called()

    def test_on_created_fires_on_new_user(self) -> None:
        client = _make_mock_client()
        hook = MagicMock()

        created = ensure_user(client, user_id="u1", on_created=hook)

        assert created is True
        hook.assert_called_once_with(client, "u1")

    def test_ensure_user_propagates_genuine_errors(self) -> None:
        """A known non-conflict status code (e.g. 401) must raise, regardless
        of message text."""
        client = _make_mock_client()
        client.user.add.side_effect = _ApiError(401, "unauthorized")

        with pytest.raises(_ApiError):
            ensure_user(client, user_id="u1")

    def test_hook_error_propagates_from_ensure_user(self) -> None:
        client = _make_mock_client()

        def _failing_hook(_client: MagicMock, _user_id: str) -> None:
            raise RuntimeError("setup failed")

        with pytest.raises(RuntimeError, match="setup failed"):
            ensure_user(client, user_id="u1", on_created=_failing_hook)


class TestEnsureThread:
    def test_returns_true_on_actual_creation(self) -> None:
        client = _make_mock_client()

        created = ensure_thread(client, thread_id="t1", user_id="u1")

        assert created is True
        client.thread.create.assert_called_once_with(thread_id="t1", user_id="u1")

    def test_returns_false_when_already_exists(self) -> None:
        client = _make_mock_client()
        client.thread.create.side_effect = _ApiError(409, "already exists")

        created = ensure_thread(client, thread_id="t1", user_id="u1")

        assert created is False

    def test_propagates_genuine_errors(self) -> None:
        client = _make_mock_client()
        client.thread.create.side_effect = _ApiError(500, "internal error")

        with pytest.raises(_ApiError):
            ensure_thread(client, thread_id="t1", user_id="u1")


class TestStorageLazyPath:
    def test_on_created_fires_on_new_user_via_storage(self) -> None:
        """ZepUserStorage's lazy path fires on_created exactly once, on the
        first save()/search() call that creates the user."""
        from zep_cloud.client import Zep

        from zep_crewai import ZepUserStorage

        client = MagicMock(spec=Zep)
        client.user = MagicMock()
        client.thread = MagicMock()
        client.thread.add_messages = MagicMock()
        hook = MagicMock()

        storage = ZepUserStorage(client=client, user_id="u1", thread_id="t1", on_created=hook)
        storage.save("hi", metadata={"type": "message", "role": "user"})

        client.user.add.assert_called_once()
        client.thread.create.assert_called_once()
        hook.assert_called_once_with(client, "u1")

    def test_storage_lazy_path_swallows_provisioning_errors(self) -> None:
        """A genuine provisioning failure during the lazy path must be logged
        and swallowed -- save() must not raise."""
        from zep_cloud.client import Zep

        from zep_crewai import ZepUserStorage

        client = MagicMock(spec=Zep)
        client.user = MagicMock()
        client.user.add = MagicMock(side_effect=_ApiError(500, "internal error"))
        client.thread = MagicMock()
        client.thread.add_messages = MagicMock()

        storage = ZepUserStorage(client=client, user_id="u1", thread_id="t1")

        # Must not raise.
        storage.save("hi", metadata={"type": "message", "role": "user"})

        # Because provisioning failed, the message should not have been sent.
        client.thread.add_messages.assert_not_called()

    def test_lazy_path_caches_and_does_not_reprovision(self) -> None:
        """Once ensured, repeated save()/search() calls do not re-issue
        provisioning calls."""
        from zep_cloud.client import Zep

        from zep_crewai import ZepUserStorage

        client = MagicMock(spec=Zep)
        client.user = MagicMock()
        client.thread = MagicMock()
        client.thread.add_messages = MagicMock()

        storage = ZepUserStorage(client=client, user_id="u1", thread_id="t1")
        storage.save("hi", metadata={"type": "message", "role": "user"})
        storage.save("hi again", metadata={"type": "message", "role": "user"})

        client.user.add.assert_called_once()
        client.thread.create.assert_called_once()

    def test_zep_storage_lazy_path_and_on_created(self) -> None:
        """ZepStorage also binds user+thread, so it gets the same lazy
        provisioning path and on_created hook as ZepUserStorage."""
        from zep_cloud.client import Zep

        from zep_crewai import ZepStorage

        client = MagicMock(spec=Zep)
        client.user = MagicMock()
        client.thread = MagicMock()
        client.thread.add_messages = MagicMock()
        hook = MagicMock()

        storage = ZepStorage(client=client, user_id="u1", thread_id="t1", on_created=hook)
        storage.save("hi", metadata={"type": "message", "role": "user"})

        client.user.add.assert_called_once()
        client.thread.create.assert_called_once()
        hook.assert_called_once_with(client, "u1")

    def test_zep_storage_lazy_path_swallows_provisioning_errors(self) -> None:
        """A genuine provisioning failure during ZepStorage's lazy path must
        be logged and swallowed -- save() must not raise."""
        from zep_cloud.client import Zep

        from zep_crewai import ZepStorage

        client = MagicMock(spec=Zep)
        client.user = MagicMock()
        client.user.add = MagicMock(side_effect=_ApiError(500, "internal error"))
        client.thread = MagicMock()
        client.thread.add_messages = MagicMock()

        storage = ZepStorage(client=client, user_id="u1", thread_id="t1")

        # Must not raise.
        storage.save("hi", metadata={"type": "message", "role": "user"})

        # Because provisioning failed, the message should not have been sent.
        client.thread.add_messages.assert_not_called()

    def test_graph_storage_has_no_on_created(self) -> None:
        """ZepGraphStorage is graph-scoped (no user), so it must not accept
        on_created."""
        from zep_cloud.client import Zep

        from zep_crewai import ZepGraphStorage

        client = MagicMock(spec=Zep)

        with pytest.raises(TypeError):
            ZepGraphStorage(client=client, graph_id="g1", on_created=lambda c, u: None)
