"""
Tests for out-of-band Zep resource creation: ``create_user``,
``create_thread``, and the ``on_created`` hook contract.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from zep_autogen.provisioning import create_thread, create_user

USER_UUID = "11111111-1111-1111-1111-111111111111"
THREAD_UUID = "22222222-2222-2222-2222-222222222222"
GRAPH_UUID = "33333333-3333-3333-3333-333333333333"


def _make_mock_client() -> MagicMock:
    client = MagicMock()
    client.user = MagicMock()
    client.user.create = AsyncMock(return_value=MagicMock(uuid_=USER_UUID, graph_uuid=GRAPH_UUID))
    client.thread = MagicMock()
    client.thread.create = AsyncMock(return_value=MagicMock(uuid_=THREAD_UUID))
    return client


class _ApiError(Exception):
    """Minimal stand-in for a typed Zep SDK error exposing ``status_code``."""

    def __init__(self, status_code: int, message: str = "error") -> None:
        self.status_code = status_code
        super().__init__(message)


class TestCreateUser:
    @pytest.mark.asyncio
    async def test_create_user_returns_the_created_user(self) -> None:
        """Zep assigns the UUID; the caller does not pass a user_id."""
        client = _make_mock_client()

        user = await create_user(client)

        assert user.uuid_ == USER_UUID
        client.user.create.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_passes_identity_fields(self) -> None:
        client = _make_mock_client()

        await create_user(client, first_name="Jane", last_name="Smith", email="jane@example.com")

        client.user.create.assert_called_once_with(
            first_name="Jane", last_name="Smith", email="jane@example.com"
        )

    @pytest.mark.asyncio
    async def test_create_user_propagates_errors(self) -> None:
        client = _make_mock_client()
        client.user.create.side_effect = _ApiError(401, "unauthorized")

        with pytest.raises(_ApiError):
            await create_user(client)

    @pytest.mark.asyncio
    async def test_on_created_receives_the_user_uuid(self) -> None:
        client = _make_mock_client()
        hook = AsyncMock()

        await create_user(client, on_created=hook)

        hook.assert_called_once_with(client, USER_UUID)

    @pytest.mark.asyncio
    async def test_hook_error_propagates_from_create_user(self) -> None:
        client = _make_mock_client()

        async def _failing_hook(_client: MagicMock, _user_uuid: str) -> None:
            raise RuntimeError("setup failed")

        with pytest.raises(RuntimeError, match="setup failed"):
            await create_user(client, on_created=_failing_hook)


class TestCreateThread:
    @pytest.mark.asyncio
    async def test_create_thread_returns_the_created_thread(self) -> None:
        client = _make_mock_client()

        thread = await create_thread(client, user_uuid=USER_UUID)

        assert thread.uuid_ == THREAD_UUID
        client.thread.create.assert_called_once_with(user_uuid=USER_UUID)

    @pytest.mark.asyncio
    async def test_propagates_errors(self) -> None:
        client = _make_mock_client()
        client.thread.create.side_effect = _ApiError(500, "internal error")

        with pytest.raises(_ApiError):
            await create_thread(client, user_uuid=USER_UUID)


class TestMemoryAddSurvivesCreationFailure:
    @pytest.mark.asyncio
    async def test_memory_add_survives_thread_creation_failure(self) -> None:
        """A 500 from thread.create during the first add() must be logged and
        swallowed -- add() must not raise."""
        from autogen_core.memory import MemoryContent, MemoryMimeType
        from zep_cloud.client import AsyncZep

        from zep_autogen import ZepUserMemory

        client = MagicMock(spec=AsyncZep)
        client.thread = MagicMock()
        client.thread.create = AsyncMock(side_effect=_ApiError(500, "internal error"))
        client.thread.add_messages = AsyncMock()

        memory = ZepUserMemory(client=client, user_uuid=USER_UUID)

        content = MemoryContent(
            content="hello",
            mime_type=MemoryMimeType.TEXT,
            metadata={"type": "message", "role": "user"},
        )

        # Must not raise.
        await memory.add(content)

        # Because creation failed, the message should not have been sent.
        client.thread.add_messages.assert_not_called()
