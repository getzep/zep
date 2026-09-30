"""
Basic tests for the zep-integrations package.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from autogen_core.memory import MemoryContent, MemoryMimeType
from conftest import FakePager

from zep_autogen import ZepUserMemory

USER_UUID = "11111111-1111-1111-1111-111111111111"
THREAD_UUID = "22222222-2222-2222-2222-222222222222"
GRAPH_UUID = "33333333-3333-3333-3333-333333333333"


def test_package_import():
    """Test that the package can be imported successfully."""
    import zep_autogen

    assert zep_autogen is not None


def test_zep_memory_import():
    """Test that ZepMemory can be imported successfully."""
    assert ZepUserMemory is not None


class TestZepMemoryMock:
    """Test ZepMemory with mock clients."""

    def test_zep_memory_initialization_with_mock(self):
        """Test that ZepMemory can be initialized with a mock client."""
        from zep_cloud.client import AsyncZep

        # Create a mock AsyncZep client
        mock_client = MagicMock(spec=AsyncZep)
        memory = ZepUserMemory(client=mock_client, thread_uuid=THREAD_UUID, user_uuid=USER_UUID)
        assert memory is not None
        assert memory._client is mock_client
        assert memory._thread_uuid == THREAD_UUID
        assert memory._user_uuid == USER_UUID

    def test_zep_memory_requires_user_uuid(self):
        """Test that ZepMemory requires a user_uuid."""
        from zep_cloud.client import AsyncZep

        mock_client = MagicMock(spec=AsyncZep)

        with pytest.raises(ValueError, match="user_uuid is required"):
            ZepUserMemory(client=mock_client, user_uuid="")

    def test_zep_memory_requires_async_zep_client(self):
        """Test that ZepMemory raises TypeError when client is not AsyncZep."""
        with pytest.raises(TypeError, match="client must be an instance of AsyncZep"):
            ZepUserMemory(client="not_a_client", user_uuid=USER_UUID)

    @pytest.mark.asyncio
    async def test_zep_memory_add_message_with_mock(self):
        """Test adding memory as message with a mock client."""
        from zep_cloud.client import AsyncZep

        mock_client = MagicMock(spec=AsyncZep)
        mock_client.thread = MagicMock()
        mock_client.thread.add_messages = AsyncMock()

        memory = ZepUserMemory(client=mock_client, user_uuid=USER_UUID, thread_uuid=THREAD_UUID)

        # Test adding memory content as message type
        content = MemoryContent(
            content="Test message",
            mime_type=MemoryMimeType.TEXT,
            metadata={"type": "message", "role": "user", "name": "user123"},
        )

        await memory.add(content)

        # Verify the thread.add_messages mock was called with the thread UUID
        mock_client.thread.add_messages.assert_called_once()
        args, _ = mock_client.thread.add_messages.call_args
        assert args[0] == THREAD_UUID

    @pytest.mark.asyncio
    async def test_zep_memory_creates_thread_when_absent(self):
        """A missing thread is created on the first message, and its UUID is kept."""
        from zep_cloud.client import AsyncZep

        mock_client = MagicMock(spec=AsyncZep)
        mock_client.thread = MagicMock()
        mock_client.thread.create = AsyncMock(return_value=MagicMock(uuid_=THREAD_UUID))
        mock_client.thread.add_messages = AsyncMock()

        memory = ZepUserMemory(client=mock_client, user_uuid=USER_UUID)

        content = MemoryContent(
            content="Test message",
            mime_type=MemoryMimeType.TEXT,
            metadata={"type": "message", "role": "user"},
        )
        await memory.add(content)

        mock_client.thread.create.assert_called_once_with(user_uuid=USER_UUID)
        assert memory.thread_uuid == THREAD_UUID
        args, _ = mock_client.thread.add_messages.call_args
        assert args[0] == THREAD_UUID

    @pytest.mark.asyncio
    async def test_zep_memory_add_graph_data_with_mock(self):
        """Test adding memory as graph data with a mock client."""
        from zep_cloud.client import AsyncZep

        mock_client = MagicMock(spec=AsyncZep)
        mock_client.user = MagicMock()
        mock_client.user.get = AsyncMock(return_value=MagicMock(graph_uuid=GRAPH_UUID))
        mock_client.graph = MagicMock()
        mock_client.graph.episode = MagicMock()
        mock_client.graph.episode.add = AsyncMock()

        memory = ZepUserMemory(
            client=mock_client,
            thread_uuid=THREAD_UUID,
            user_uuid=USER_UUID,
        )

        # Content without an explicit type is stored as graph data
        content = MemoryContent(
            content="Test data for graph",
            mime_type=MemoryMimeType.TEXT,
            metadata={"category": "facts"},
        )

        await memory.add(content)

        mock_client.graph.episode.add.assert_called_once()
        args, kwargs = mock_client.graph.episode.add.call_args
        assert args[0] == GRAPH_UUID
        assert kwargs["type"] == "text"

    @pytest.mark.asyncio
    async def test_zep_memory_uses_configured_graph_uuid(self):
        """A configured graph_uuid removes the user read."""
        from zep_cloud.client import AsyncZep

        mock_client = MagicMock(spec=AsyncZep)
        mock_client.user = MagicMock()
        mock_client.user.get = AsyncMock()
        mock_client.graph = MagicMock()
        mock_client.graph.episode = MagicMock()
        mock_client.graph.episode.add = AsyncMock()

        memory = ZepUserMemory(client=mock_client, user_uuid=USER_UUID, graph_uuid=GRAPH_UUID)

        await memory.add(MemoryContent(content="fact", mime_type=MemoryMimeType.TEXT, metadata={}))

        mock_client.user.get.assert_not_called()
        args, _ = mock_client.graph.episode.add.call_args
        assert args[0] == GRAPH_UUID

    @pytest.mark.asyncio
    async def test_zep_memory_rejects_unsupported_metadata_type(self):
        """An unknown metadata type raises a ValueError."""
        from zep_cloud.client import AsyncZep

        mock_client = MagicMock(spec=AsyncZep)
        mock_client.graph = MagicMock()
        mock_client.graph.episode = MagicMock()
        mock_client.graph.episode.add = AsyncMock()

        memory = ZepUserMemory(
            client=mock_client,
            user_uuid=USER_UUID,
            thread_uuid=THREAD_UUID,
        )

        content = MemoryContent(
            content="Test data for graph",
            mime_type=MemoryMimeType.TEXT,
            metadata={"type": "unsupported", "category": "facts"},
        )

        with pytest.raises(ValueError, match="Unsupported metadata type"):
            await memory.add(content)

    @pytest.mark.asyncio
    async def test_zep_memory_query_with_mock(self):
        """Test querying memory with a mock client."""
        from zep_cloud.client import AsyncZep

        mock_client = MagicMock(spec=AsyncZep)
        mock_client.user = MagicMock()
        mock_client.user.get = AsyncMock(return_value=MagicMock(graph_uuid=GRAPH_UUID))
        mock_client.graph = MagicMock()
        mock_client.graph.search_edges = AsyncMock(return_value=FakePager([]))

        memory = ZepUserMemory(client=mock_client, user_uuid=USER_UUID, thread_uuid=THREAD_UUID)

        results = await memory.query("test query")

        mock_client.graph.search_edges.assert_called_once()
        args, kwargs = mock_client.graph.search_edges.call_args
        assert args[0] == GRAPH_UUID
        assert kwargs["query"] == "test query"
        assert hasattr(results, "results")  # Should be a MemoryQueryResult
        assert len(results.results) == 0

    @pytest.mark.asyncio
    async def test_zep_memory_mime_type_validation(self):
        """Test that ZepMemory validates mime types correctly."""
        from zep_cloud.client import AsyncZep

        mock_client = MagicMock(spec=AsyncZep)
        memory = ZepUserMemory(client=mock_client, user_uuid=USER_UUID, thread_uuid=THREAD_UUID)

        # Test supported mime types - these should work
        supported_types = [MemoryMimeType.TEXT, MemoryMimeType.MARKDOWN, MemoryMimeType.JSON]

        mock_client.thread = MagicMock()
        mock_client.thread.add_messages = AsyncMock()

        for mime_type in supported_types:
            content = MemoryContent(
                content="Test content",
                mime_type=mime_type,
                metadata={
                    "type": "message",
                    "role": "user",
                    "name": "user123",
                    "category": "test",
                },  # Add type for message storage
            )
            # Should not raise an exception
            await memory.add(content)

        # Test unsupported mime types - these should raise ValueError
        unsupported_types = [MemoryMimeType.IMAGE, MemoryMimeType.BINARY]

        for mime_type in unsupported_types:
            unsupported_content = MemoryContent(
                content="Test content",
                mime_type=mime_type,
                metadata={"category": "test"},
            )

            with pytest.raises(ValueError, match="Unsupported mime type"):
                await memory.add(unsupported_content)

    @pytest.mark.asyncio
    async def test_update_context_calls_get_context_with_thread_uuid(self):
        """Context retrieval addresses the thread by UUID."""
        from autogen_core.model_context import UnboundedChatCompletionContext
        from autogen_core.models import UserMessage
        from zep_cloud.client import AsyncZep

        mock_client = MagicMock(spec=AsyncZep)
        mock_client.thread = MagicMock()
        mock_client.thread.get_context = AsyncMock(
            return_value=MagicMock(context="some context block")
        )
        mock_client.thread.list_messages = AsyncMock(return_value=FakePager([]))

        memory = ZepUserMemory(client=mock_client, user_uuid=USER_UUID, thread_uuid=THREAD_UUID)

        model_context = UnboundedChatCompletionContext()
        await model_context.add_message(UserMessage(content="hello", source="user"))

        await memory.update_context(model_context)

        mock_client.thread.get_context.assert_called_once()
        args, kwargs = mock_client.thread.get_context.call_args
        assert args[0] == THREAD_UUID
        assert "template_uuid" not in kwargs

    @pytest.mark.asyncio
    async def test_update_context_passes_template_uuid(self):
        """When configured, context_template_uuid is forwarded as template_uuid."""
        from autogen_core.model_context import UnboundedChatCompletionContext
        from autogen_core.models import UserMessage
        from zep_cloud.client import AsyncZep

        template_uuid = "44444444-4444-4444-4444-444444444444"

        mock_client = MagicMock(spec=AsyncZep)
        mock_client.thread = MagicMock()
        mock_client.thread.get_context = AsyncMock(
            return_value=MagicMock(context="templated context")
        )
        mock_client.thread.list_messages = AsyncMock(return_value=FakePager([]))

        memory = ZepUserMemory(
            client=mock_client,
            user_uuid=USER_UUID,
            thread_uuid=THREAD_UUID,
            context_template_uuid=template_uuid,
        )

        model_context = UnboundedChatCompletionContext()
        await model_context.add_message(UserMessage(content="hello", source="user"))

        await memory.update_context(model_context)

        mock_client.thread.get_context.assert_called_once()
        args, kwargs = mock_client.thread.get_context.call_args
        assert args[0] == THREAD_UUID
        assert kwargs["template_uuid"] == template_uuid
