"""Tests for V4 user and thread creation helpers."""

from unittest.mock import MagicMock

import pytest

from zep_crewai.provisioning import ensure_thread, ensure_user


def test_ensure_user_returns_server_resource_and_created_flag() -> None:
    client = MagicMock()
    created = MagicMock(uuid_="user-uuid")
    client.user.create.return_value = created
    result, was_created = ensure_user(client)
    assert result is created
    assert was_created is True
    client.user.create.assert_called_once_with(first_name=None, last_name=None, email=None)


def test_ensure_user_does_not_pass_client_selected_id() -> None:
    client = MagicMock()
    ensure_user(client, first_name="Alice", last_name="Smith", email="a@example.com")
    kwargs = client.user.create.call_args.kwargs
    assert "user_id" not in kwargs
    assert kwargs == {
        "first_name": "Alice",
        "last_name": "Smith",
        "email": "a@example.com",
    }


def test_ensure_user_runs_hook_with_returned_user() -> None:
    client = MagicMock()
    user = MagicMock(uuid_="user-uuid")
    client.user.create.return_value = user
    hook = MagicMock()
    created_user, was_created = ensure_user(client, on_created=hook)
    assert created_user is user
    assert was_created is True
    hook.assert_called_once_with(client, user)


def test_ensure_user_propagates_sdk_error() -> None:
    client = MagicMock()
    client.user.create.side_effect = RuntimeError("create failed")
    with pytest.raises(RuntimeError, match="create failed"):
        ensure_user(client)


def test_ensure_user_propagates_hook_error() -> None:
    client = MagicMock()
    client.user.create.return_value = MagicMock()
    hook = MagicMock(side_effect=RuntimeError("hook failed"))
    with pytest.raises(RuntimeError, match="hook failed"):
        ensure_user(client, on_created=hook)


def test_ensure_thread_returns_server_resource_and_created_flag() -> None:
    client = MagicMock()
    thread = MagicMock(uuid_="thread-uuid")
    client.thread.create.return_value = thread
    created_thread, was_created = ensure_thread(client, user_uuid="user-uuid")
    assert created_thread is thread
    assert was_created is True
    client.thread.create.assert_called_once_with(user_uuid="user-uuid")


def test_ensure_thread_propagates_sdk_error() -> None:
    client = MagicMock()
    client.thread.create.side_effect = RuntimeError("create failed")
    with pytest.raises(RuntimeError, match="create failed"):
        ensure_thread(client, user_uuid="user-uuid")
