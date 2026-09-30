"""
Basic tests for the zep-crewai package.
"""

from unittest.mock import MagicMock

import pytest

from zep_crewai import ZepStorage

USER_UUID = "11111111-1111-1111-1111-111111111111"
THREAD_UUID = "22222222-2222-2222-2222-222222222222"
GRAPH_UUID = "33333333-3333-3333-3333-333333333333"


def _make_mock_zep_client():
    """A Zep mock whose ``user.get`` returns a user with a graph UUID."""
    from zep_cloud.client import Zep

    client = MagicMock(spec=Zep)
    client.user = MagicMock()
    client.user.get = MagicMock(return_value=MagicMock(graph_uuid=GRAPH_UUID))
    client.thread = MagicMock()
    client.graph = MagicMock()
    client.graph.episode = MagicMock()
    return client


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
        mock_client = _make_mock_zep_client()
        storage = ZepStorage(client=mock_client, user_uuid=USER_UUID, thread_uuid=THREAD_UUID)
        assert storage is not None
        assert storage._client is mock_client
        assert storage._user_uuid == USER_UUID
        assert storage._thread_uuid == THREAD_UUID

    def test_zep_storage_requires_user_uuid_and_thread_uuid(self):
        """Test that ZepStorage requires both user_uuid and thread_uuid."""
        mock_client = _make_mock_zep_client()

        with pytest.raises(ValueError, match="user_uuid is required"):
            ZepStorage(client=mock_client, user_uuid="", thread_uuid=THREAD_UUID)

        with pytest.raises(ValueError, match="thread_uuid is required"):
            ZepStorage(client=mock_client, user_uuid=USER_UUID, thread_uuid="")

    def test_zep_storage_requires_zep_client(self):
        """Test that ZepStorage raises TypeError when client is not Zep."""
        with pytest.raises(TypeError, match="client must be an instance of Zep"):
            ZepStorage(client="not_a_client", user_uuid=USER_UUID, thread_uuid=THREAD_UUID)

    def test_zep_storage_save_message_sync(self):
        """Test saving memory as thread message using sync interface."""
        mock_client = _make_mock_zep_client()
        mock_client.thread.add_messages = MagicMock()

        storage = ZepStorage(client=mock_client, user_uuid=USER_UUID, thread_uuid=THREAD_UUID)

        # Test saving message content to thread
        storage.save(
            "Test message content",
            metadata={"type": "message", "role": "user", "name": "John Doe"},
        )

        # Verify the thread add_messages was called
        mock_client.thread.add_messages.assert_called_once()

        # Check the call arguments
        call_args = mock_client.thread.add_messages.call_args
        assert call_args[0][0] == THREAD_UUID
        assert len(call_args[1]["messages"]) == 1

        message = call_args[1]["messages"][0]
        assert message.content == "Test message content"
        assert message.role == "user"
        assert message.name == "John Doe"

    def test_save_does_not_raise_on_zep_error(self, caplog):
        """save() must log and return normally when the Zep SDK call raises --
        never propagate the error into the crew."""
        mock_client = _make_mock_zep_client()
        mock_client.thread.add_messages = MagicMock(side_effect=Exception("Zep API error"))

        storage = ZepStorage(client=mock_client, user_uuid=USER_UUID, thread_uuid=THREAD_UUID)

        with caplog.at_level("ERROR"):
            storage.save(
                "Test message content",
                metadata={"type": "message", "role": "user", "name": "John Doe"},
            )

        mock_client.thread.add_messages.assert_called_once()
        assert "Zep API error" in caplog.text

    def test_zep_storage_save_graph_sync(self):
        """Test saving memory as graph data using sync interface."""
        mock_client = _make_mock_zep_client()
        mock_client.graph.episode.add = MagicMock()

        storage = ZepStorage(client=mock_client, user_uuid=USER_UUID, thread_uuid=THREAD_UUID)

        # Test saving text content to graph
        storage.save("Test text content", metadata={"type": "text", "category": "facts"})

        # Verify the episode add was called
        mock_client.graph.episode.add.assert_called_once()

        # Check the call arguments
        call_args = mock_client.graph.episode.add.call_args
        assert call_args[0][0] == GRAPH_UUID
        assert call_args[1]["data"] == "Test text content"
        assert call_args[1]["type"] == "text"

    def test_zep_storage_uses_graph_uuid_from_constructor(self):
        """A graph_uuid given to the constructor avoids the user.get call."""
        mock_client = _make_mock_zep_client()
        mock_client.graph.episode.add = MagicMock()

        storage = ZepStorage(
            client=mock_client,
            user_uuid=USER_UUID,
            thread_uuid=THREAD_UUID,
            graph_uuid=GRAPH_UUID,
        )
        storage.save("Test text content", metadata={"type": "text"})

        mock_client.user.get.assert_not_called()
        assert mock_client.graph.episode.add.call_args[0][0] == GRAPH_UUID

    def test_zep_storage_search_sync(self):
        """Test searching memory using sync interface."""
        mock_client = _make_mock_zep_client()
        mock_client.thread.get_context = MagicMock()
        mock_client.graph.search_edges = MagicMock()

        # Mock thread context response
        mock_thread_context = MagicMock()
        mock_thread_context.context = "Mock thread context summary"
        mock_client.thread.get_context.return_value = mock_thread_context

        # Mock graph search response
        mock_edge = MagicMock()
        mock_edge.fact = "Mock fact from graph"
        mock_edge.valid_at = "2023-01-01"
        mock_edge.invalid_at = None
        mock_client.graph.search_edges.return_value = iter([mock_edge])

        storage = ZepStorage(
            client=mock_client,
            user_uuid=USER_UUID,
            thread_uuid=THREAD_UUID,
            graph_uuid=GRAPH_UUID,
        )

        results = storage.search("test query", limit=5)

        # Should return a list of results
        assert isinstance(results, list)
        assert len(results) >= 1  # At least thread context

        # Verify both thread context and graph search were called
        mock_client.thread.get_context.assert_called_once_with(THREAD_UUID)
        mock_client.graph.search_edges.assert_called_once_with(
            GRAPH_UUID, query="test query", limit=5
        )

    def test_zep_storage_reset_sync(self):
        """Test resetting memory using sync interface."""
        mock_client = _make_mock_zep_client()

        storage = ZepStorage(client=mock_client, user_uuid=USER_UUID, thread_uuid=THREAD_UUID)

        # Should not raise an exception (currently just logs a warning)
        storage.reset()

    def test_zep_storage_properties(self):
        """Test ZepStorage properties."""
        mock_client = _make_mock_zep_client()
        storage = ZepStorage(
            client=mock_client,
            user_uuid=USER_UUID,
            thread_uuid=THREAD_UUID,
            graph_uuid=GRAPH_UUID,
        )

        assert storage.user_uuid == USER_UUID
        assert storage.thread_uuid == THREAD_UUID
        assert storage.graph_uuid == GRAPH_UUID
