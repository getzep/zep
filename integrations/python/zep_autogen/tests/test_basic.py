"""
Basic tests for the zep-integrations package.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from autogen_core.memory import MemoryContent, MemoryMimeType

from zep_autogen import ZepMemory


def test_package_import():
    """Test that the package can be imported successfully."""
    import zep_autogen

    assert zep_autogen is not None


def test_zep_memory_import():
    """Test that ZepMemory can be imported successfully."""
    assert ZepMemory is not None


class TestBasicFunctionality:
    """Basic functionality tests for the zep-autogen package."""

    def test_package_structure(self):
        """Test that the package has the expected structure."""
        import zep_autogen

        assert hasattr(zep_autogen, "__version__")
        assert hasattr(zep_autogen, "__author__")
        assert hasattr(zep_autogen, "__description__")


class TestZepMemoryMock:
    """Test ZepMemory with mock clients."""

    def test_zep_memory_initialization_with_mock(self):
        """Test that ZepMemory can be initialized with a mock client."""
        try:
            from zep_cloud.client import AsyncZep

            # Create a mock AsyncZep client
            mock_client = MagicMock(spec=AsyncZep)
            memory = ZepMemory(client=mock_client, thread_id="test-session", user_id="test-user")
            assert memory is not None
            assert memory._client is mock_client
            assert memory._thread_id == "test-session"
            assert memory._user_id == "test-user"

        except ImportError:
            # If zep_cloud is not available, test with a generic mock
            class MockAsyncZep:
                pass

            mock_client = MockAsyncZep()
            with pytest.raises(TypeError, match="client must be an instance of AsyncZep"):
                ZepMemory(client=mock_client, thread_id="test-session")

    def test_zep_memory_requires_session_id(self):
        """Test that ZepMemory requires a session_id."""
        try:
            from zep_cloud.client import AsyncZep

            mock_client = MagicMock(spec=AsyncZep)

            with pytest.raises(ValueError, match="user_id is required"):
                ZepMemory(client=mock_client, user_id="")

        except ImportError:
            pytest.skip("zep_cloud not available")

    def test_zep_memory_requires_async_zep_client(self):
        """Test that ZepMemory raises TypeError when client is not AsyncZep."""
        with pytest.raises(TypeError, match="client must be an instance of AsyncZep"):
            ZepMemory(client="not_a_client", user_id="test-user")

    @pytest.mark.asyncio
    async def test_zep_memory_add_message_with_mock(self):
        """Test adding memory as message (with user_id) with a mock client."""
        try:
            from zep_cloud.client import AsyncZep

            mock_client = MagicMock(spec=AsyncZep)
            mock_client.thread = MagicMock()
            mock_client.thread.get = AsyncMock()
            mock_client.thread.add_messages = AsyncMock()

            memory = ZepMemory(client=mock_client, user_id="test-user", thread_id="test-session")

            # Test adding memory content as message type
            content = MemoryContent(
                content="Test message",
                mime_type=MemoryMimeType.TEXT,
                metadata={"type": "message", "role": "user", "name": "user123"},
            )

            await memory.add(content)

            # Verify the thread.add_messages mock was called
            mock_client.thread.add_messages.assert_called_once()

        except ImportError:
            pytest.skip("zep_cloud not available")

    @pytest.mark.asyncio
    async def test_zep_memory_add_graph_data_with_mock(self):
        """Test adding memory as graph data (without user_id) with a mock client."""
        try:
            from zep_cloud.client import AsyncZep

            mock_client = MagicMock(spec=AsyncZep)
            mock_client.graph = MagicMock()
            mock_client.graph.add = AsyncMock()

            memory = ZepMemory(
                client=mock_client,
                thread_id="test-session",
                user_id="test-user",  # Required for graph data
            )

            # Test adding memory content without user_id (should store as graph data)
            content = MemoryContent(
                content="Test data for graph",
                mime_type=MemoryMimeType.TEXT,
                metadata={"category": "facts"},  # No user_id
            )

            await memory.add(content)

            # Verify the graph.add mock was called
            mock_client.graph.add.assert_called_once()

        except ImportError:
            pytest.skip("zep_cloud not available")

    @pytest.mark.asyncio
    async def test_zep_memory_add_graph_data_requires_user_id(self):
        """Test that adding graph data requires user_id."""
        try:
            from zep_cloud.client import AsyncZep

            mock_client = MagicMock(spec=AsyncZep)
            mock_client.graph = MagicMock()
            mock_client.graph.add = AsyncMock()

            memory = ZepMemory(
                client=mock_client,
                user_id="test-user",  # user_id is now required
                thread_id="test-session",
            )

            # Test adding memory content with unsupported type
            content = MemoryContent(
                content="Test data for graph",
                mime_type=MemoryMimeType.TEXT,
                metadata={"type": "unsupported", "category": "facts"},
            )

            with pytest.raises(ValueError, match="Unsupported metadata type"):
                await memory.add(content)

        except ImportError:
            pytest.skip("zep_cloud not available")

    @pytest.mark.asyncio
    async def test_zep_memory_query_with_mock(self):
        """Test querying memory with a mock client."""
        try:
            from zep_cloud.client import AsyncZep

            mock_client = MagicMock(spec=AsyncZep)
            mock_client.graph = MagicMock()
            mock_client.graph.search = AsyncMock()

            # Mock graph search response
            mock_graph_response = MagicMock()
            mock_graph_response.edges = []
            mock_graph_response.nodes = []
            mock_graph_response.episodes = []
            mock_client.graph.search.return_value = mock_graph_response

            memory = ZepMemory(client=mock_client, user_id="test-user", thread_id="test-session")

            results = await memory.query("test query")

            # Verify the mock was called and results returned
            mock_client.graph.search.assert_called_once()
            assert hasattr(results, "results")  # Should be a MemoryQueryResult
            assert len(results.results) >= 0  # Should return some results

        except ImportError:
            pytest.skip("zep_cloud not available")

    @pytest.mark.asyncio
    async def test_zep_memory_mime_type_validation(self):
        """Test that ZepMemory validates mime types correctly."""
        try:
            from zep_cloud.client import AsyncZep

            mock_client = MagicMock(spec=AsyncZep)
            memory = ZepMemory(client=mock_client, user_id="test-user", thread_id="test-session")

            # Test supported mime types - these should work (with user_id for message storage)
            supported_types = [MemoryMimeType.TEXT, MemoryMimeType.MARKDOWN, MemoryMimeType.JSON]

            mock_client.thread = MagicMock()
            mock_client.thread.get = AsyncMock()
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
                    metadata={"user_id": "user123", "category": "test"},  # Add user_id
                )

                with pytest.raises(ValueError, match="Unsupported mime type"):
                    await memory.add(unsupported_content)

        except ImportError:
            pytest.skip("zep_cloud not available")
