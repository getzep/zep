"""
Basic tests for the zep-crewai package.
"""

from unittest.mock import MagicMock

import pytest

from zep_crewai import ZepStorage


def test_package_import():
    """Test that the package can be imported successfully."""
    import zep_crewai

    assert zep_crewai is not None


def test_zep_storage_import():
    """Test that ZepStorage can be imported successfully."""
    assert ZepStorage is not None


class TestBasicFunctionality:
    """Basic functionality tests for the zep-crewai package."""

    def test_package_structure(self):
        """Test that the package has the expected structure."""
        import zep_crewai

        assert hasattr(zep_crewai, "__version__")
        assert hasattr(zep_crewai, "__author__")
        assert hasattr(zep_crewai, "__description__")


class TestZepStorageMock:
    """Test ZepStorage with mock clients."""

    def test_zep_storage_initialization_with_mock(self):
        """Test that ZepStorage can be initialized with a mock client."""
        try:
            from zep_cloud.client import Zep

            # Create a mock Zep client
            mock_client = MagicMock(spec=Zep)
            storage = ZepStorage(client=mock_client, user_id="test-user", thread_id="test-thread")
            assert storage is not None
            assert storage._client is mock_client
            assert storage._user_id == "test-user"
            assert storage._thread_id == "test-thread"

        except ImportError:
            # If zep_cloud is not available, test with a generic mock
            class MockZep:
                pass

            mock_client = MockZep()
            with pytest.raises(TypeError, match="client must be an instance of Zep"):
                ZepStorage(client=mock_client, user_id="test-user", thread_id="test-thread")

    def test_zep_storage_requires_user_id_and_thread_id(self):
        """Test that ZepStorage requires both user_id and thread_id."""
        try:
            from zep_cloud.client import Zep

            mock_client = MagicMock(spec=Zep)

            with pytest.raises(ValueError, match="user_id is required"):
                ZepStorage(client=mock_client, user_id="", thread_id="test-thread")

            with pytest.raises(ValueError, match="thread_id is required"):
                ZepStorage(client=mock_client, user_id="test-user", thread_id="")

        except ImportError:
            pytest.skip("zep_cloud not available")

    def test_zep_storage_requires_zep_client(self):
        """Test that ZepStorage raises TypeError when client is not Zep."""
        with pytest.raises(TypeError, match="client must be an instance of Zep"):
            ZepStorage(client="not_a_client", user_id="test-user", thread_id="test-thread")

    def test_zep_storage_save_message_sync(self):
        """Test saving memory as thread message using sync interface."""
        try:
            from zep_cloud.client import Zep

            mock_client = MagicMock(spec=Zep)
            mock_client.thread = MagicMock()
            mock_client.thread.add_messages = MagicMock()

            storage = ZepStorage(client=mock_client, user_id="test-user", thread_id="test-thread")

            # Test saving message content to thread
            storage.save(
                "Test message content",
                metadata={"type": "message", "role": "user", "name": "John Doe"},
            )

            # Verify the thread add_messages was called
            mock_client.thread.add_messages.assert_called_once()

            # Check the call arguments
            call_args = mock_client.thread.add_messages.call_args
            assert call_args[1]["thread_id"] == "test-thread"
            assert len(call_args[1]["messages"]) == 1

            message = call_args[1]["messages"][0]
            assert message.content == "Test message content"
            assert message.role == "user"
            assert message.name == "John Doe"

        except ImportError:
            pytest.skip("zep_cloud not available")

    def test_zep_storage_save_graph_sync(self):
        """Test saving memory as graph data using sync interface."""
        try:
            from zep_cloud.client import Zep

            mock_client = MagicMock(spec=Zep)
            mock_client.graph = MagicMock()
            mock_client.graph.add = MagicMock()

            storage = ZepStorage(client=mock_client, user_id="test-user", thread_id="test-thread")

            # Test saving text content to graph
            storage.save("Test text content", metadata={"type": "text", "category": "facts"})

            # Verify the graph add was called
            mock_client.graph.add.assert_called_once()

            # Check the call arguments
            call_args = mock_client.graph.add.call_args
            assert call_args[1]["user_id"] == "test-user"
            assert call_args[1]["data"] == "Test text content"
            assert call_args[1]["type"] == "text"

        except ImportError:
            pytest.skip("zep_cloud not available")

    def test_zep_storage_search_sync(self):
        """Test searching memory using sync interface."""
        try:
            from zep_cloud.client import Zep
            from zep_cloud.types import EntityEdge, GraphSearchResults

            mock_client = MagicMock(spec=Zep)
            mock_client.thread = MagicMock()
            mock_client.thread.get_user_context = MagicMock()
            mock_client.graph = MagicMock()
            mock_client.graph.search = MagicMock()

            # Mock thread context response
            mock_thread_context = MagicMock()
            mock_thread_context.context = "Mock thread context summary"
            mock_client.thread.get_user_context.return_value = mock_thread_context

            # Mock graph search response
            mock_edge = MagicMock(spec=EntityEdge)
            mock_edge.fact = "Mock fact from graph"
            mock_edge.valid_at = "2023-01-01"
            mock_edge.invalid_at = None

            mock_graph_results = MagicMock(spec=GraphSearchResults)
            mock_graph_results.edges = [mock_edge]
            mock_client.graph.search.return_value = mock_graph_results

            storage = ZepStorage(client=mock_client, user_id="test-user", thread_id="test-thread")

            results = storage.search("test query", limit=5)

            # Should return a list of results
            assert isinstance(results, list)
            assert len(results) >= 1  # At least thread context

            # Verify both thread context and graph search were called
            mock_client.thread.get_user_context.assert_called_once_with(thread_id="test-thread")
            mock_client.graph.search.assert_called_once()

        except ImportError:
            pytest.skip("zep_cloud not available")

    def test_zep_storage_reset_sync(self):
        """Test resetting memory using sync interface."""
        try:
            from zep_cloud.client import Zep

            mock_client = MagicMock(spec=Zep)

            storage = ZepStorage(client=mock_client, user_id="test-user", thread_id="test-thread")

            # Should not raise an exception (currently just logs a warning)
            storage.reset()

        except ImportError:
            pytest.skip("zep_cloud not available")

    def test_zep_storage_properties(self):
        """Test ZepStorage properties."""
        try:
            from zep_cloud.client import Zep

            mock_client = MagicMock(spec=Zep)
            storage = ZepStorage(client=mock_client, user_id="test-user", thread_id="test-thread")

            assert storage.user_id == "test-user"
            assert storage.thread_id == "test-thread"

        except ImportError:
            pytest.skip("zep_cloud not available")
