"""
Tests for ZepUserStorage.
"""

from unittest.mock import MagicMock, patch

import pytest

from zep_crewai import ZepUserStorage


class TestZepUserStorage:
    """Test suite for ZepUserStorage."""

    def test_initialization_success(self):
        """Test successful initialization with required parameters."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        storage = ZepUserStorage(
            client=mock_client,
            user_id="test-user",
            thread_id="test-thread",
            search_filters={"node_labels": ["Preference"]},
            facts_limit=15,
            entity_limit=10,
            mode="summary",
        )

        assert storage._client is mock_client
        assert storage.user_id == "test-user"
        assert storage.thread_id == "test-thread"
        assert storage._search_filters == {"node_labels": ["Preference"]}
        assert storage._facts_limit == 15
        assert storage._entity_limit == 10
        assert storage._mode == "summary"  # Should default to summary

    def test_initialization_without_thread_raises_error(self):
        """Test initialization without thread_id raises TypeError."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        with pytest.raises(TypeError, match="missing 1 required positional argument: 'thread_id'"):
            ZepUserStorage(client=mock_client, user_id="test-user")

    def test_initialization_requires_user_id(self):
        """Test that user_id is required."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)

        with pytest.raises(ValueError, match="user_id is required"):
            ZepUserStorage(client=mock_client, user_id="", thread_id="test-thread")

    def test_initialization_requires_thread_id(self):
        """Test that thread_id is required and non-empty."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)

        with pytest.raises(ValueError, match="thread_id is required"):
            ZepUserStorage(client=mock_client, user_id="test-user", thread_id="")

    def test_initialization_requires_zep_client(self):
        """Test that client must be Zep instance."""
        with pytest.raises(TypeError, match="client must be an instance of Zep"):
            ZepUserStorage(client="not_a_client", user_id="test-user", thread_id="test-thread")

    def test_save_message_with_thread(self):
        """Test saving message when thread_id is set."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        mock_client.thread = MagicMock()
        mock_client.thread.add_messages = MagicMock()

        storage = ZepUserStorage(client=mock_client, user_id="test-user", thread_id="test-thread")

        # Save message
        storage.save(
            "Hello, how can I help?",
            metadata={"type": "message", "role": "assistant", "name": "Helper"},
        )

        # Verify thread.add_messages was called
        mock_client.thread.add_messages.assert_called_once()

        call_args = mock_client.thread.add_messages.call_args
        assert call_args[1]["thread_id"] == "test-thread"
        assert len(call_args[1]["messages"]) == 1

        message = call_args[1]["messages"][0]
        assert message.content == "Hello, how can I help?"
        assert message.role == "assistant"
        assert message.name == "Helper"

    def test_save_json_data(self):
        """Test saving JSON data to user graph."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        mock_client.graph = MagicMock()
        mock_client.graph.add = MagicMock()

        storage = ZepUserStorage(client=mock_client, user_id="test-user", thread_id="test-thread")

        # Save JSON data
        json_data = '{"preference": "dark_mode", "timezone": "PST"}'
        storage.save(json_data, metadata={"type": "json"})

        # Verify graph.add was called correctly
        mock_client.graph.add.assert_called_once_with(
            user_id="test-user", data=json_data, type="json"
        )

    def test_save_text_data(self):
        """Test saving text data to user graph."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        mock_client.graph = MagicMock()
        mock_client.graph.add = MagicMock()

        storage = ZepUserStorage(client=mock_client, user_id="test-user", thread_id="test-thread")

        # Save text data
        storage.save("User prefers morning meetings", metadata={"type": "text"})

        # Verify graph.add was called correctly
        mock_client.graph.add.assert_called_once_with(
            user_id="test-user", data="User prefers morning meetings", type="text"
        )

    @patch("zep_crewai.utils.compose_context_string")
    @patch("zep_crewai.utils.ThreadPoolExecutor")
    def test_search_with_thread_context(self, mock_executor, mock_compose):
        """Test search includes thread context when available."""
        from zep_cloud.client import Zep
        from zep_cloud.types import GraphSearchResults

        mock_client = MagicMock(spec=Zep)
        mock_client.thread = MagicMock()
        mock_client.graph = MagicMock()

        # Setup mock executor
        mock_executor_instance = MagicMock()
        mock_executor.return_value.__enter__.return_value = mock_executor_instance

        # Create proper GraphSearchResults mocks
        edge_results = MagicMock(spec=GraphSearchResults)
        edge_results.edges = [MagicMock(fact="User likes Python")]

        node_results = MagicMock(spec=GraphSearchResults)
        node_results.nodes = []

        episode_results = MagicMock(spec=GraphSearchResults)
        episode_results.episodes = []

        # Mock futures
        future_edges = MagicMock()
        future_edges.result.return_value = edge_results

        future_nodes = MagicMock()
        future_nodes.result.return_value = node_results

        future_episodes = MagicMock()
        future_episodes.result.return_value = episode_results

        mock_executor_instance.submit.side_effect = [future_edges, future_nodes, future_episodes]

        # Mock compose_context_string to return context
        mock_compose.return_value = "Context: User likes Python"

        storage = ZepUserStorage(client=mock_client, user_id="test-user", thread_id="test-thread")

        # Perform search
        results = storage.search("test query", limit=5)

        # Verify results include context
        assert isinstance(results, list)
        assert len(results) == 1
        assert results[0]["type"] == "user_graph_context"
        assert results[0]["memory"] == "Context: User likes Python"

    @patch("zep_crewai.utils.compose_context_string")
    @patch("zep_crewai.utils.ThreadPoolExecutor")
    def test_search_user_graph(self, mock_executor, mock_compose):
        """Test search searches user graph correctly."""
        from zep_cloud.client import Zep
        from zep_cloud.types import EntityEdge, EntityNode, GraphSearchResults

        mock_client = MagicMock(spec=Zep)
        mock_client.thread = MagicMock()
        mock_client.graph = MagicMock()

        # Mock edge
        mock_edge = MagicMock(spec=EntityEdge)
        mock_edge.fact = "User prefers Python"
        mock_edge.name = "preference_fact"
        mock_edge.attributes = {}
        mock_edge.created_at = "2024-01-01"
        mock_edge.valid_at = "2024-01-01"
        mock_edge.invalid_at = None

        # Mock node
        mock_node = MagicMock(spec=EntityNode)
        mock_node.name = "UserPreference"
        mock_node.summary = "Programming language preference"
        mock_node.attributes = {}
        mock_node.created_at = "2024-01-01"

        # Setup mock executor
        mock_executor_instance = MagicMock()
        mock_executor.return_value.__enter__.return_value = mock_executor_instance

        # Create proper GraphSearchResults mocks
        edge_results = MagicMock(spec=GraphSearchResults)
        edge_results.edges = [mock_edge]

        node_results = MagicMock(spec=GraphSearchResults)
        node_results.nodes = [mock_node]

        episode_results = MagicMock(spec=GraphSearchResults)
        episode_results.episodes = []

        # Mock futures
        future_edges = MagicMock()
        future_edges.result.return_value = edge_results

        future_nodes = MagicMock()
        future_nodes.result.return_value = node_results

        future_episodes = MagicMock()
        future_episodes.result.return_value = episode_results

        mock_executor_instance.submit.side_effect = [future_edges, future_nodes, future_episodes]

        # Mock compose_context_string to return formatted context
        mock_compose.return_value = (
            "Context: User prefers Python. UserPreference: Programming language preference"
        )

        storage = ZepUserStorage(client=mock_client, user_id="test-user", thread_id="test-thread")

        # Perform search
        results = storage.search("preferences", limit=5)

        # Verify results
        # Verify compose_context_string was called with correct arguments
        mock_compose.assert_called_once()
        call_args = mock_compose.call_args
        assert call_args[1]["edges"] == [mock_edge]
        assert call_args[1]["nodes"] == [mock_node]
        assert call_args[1]["episodes"] == []

        # Verify results
        assert len(results) == 1
        assert results[0]["type"] == "user_graph_context"
        assert "User prefers Python" in results[0]["memory"]
        assert "UserPreference" in results[0]["memory"]

    def test_get_context_with_thread(self):
        """Test get_context retrieves context using thread.get_user_context."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        mock_client.thread = MagicMock()

        # Mock thread context response
        mock_context = MagicMock()
        mock_context.context = "User's conversation context with summary"
        mock_client.thread.get_user_context.return_value = mock_context

        storage = ZepUserStorage(
            client=mock_client, user_id="test-user", thread_id="test-thread", mode="summary"
        )

        # Get context
        context = storage.get_context()

        # Verify thread.get_user_context was called with correct mode
        mock_client.thread.get_user_context.assert_called_once_with(
            thread_id="test-thread", mode="summary"
        )

        assert context == "User's conversation context with summary"

    def test_get_context_with_basic_mode(self):
        """Test get_context with basic mode."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        mock_client.thread = MagicMock()

        # Mock thread context response with basic
        mock_context = MagicMock()
        mock_context.context = "User: Hello\nAssistant: Hi there!\nUser: How are you?"
        mock_client.thread.get_user_context.return_value = mock_context

        storage = ZepUserStorage(
            client=mock_client, user_id="test-user", thread_id="test-thread", mode="basic"
        )

        # Get context
        context = storage.get_context()

        # Verify thread.get_user_context was called with basic mode
        mock_client.thread.get_user_context.assert_called_once_with(
            thread_id="test-thread", mode="basic"
        )

        assert context == "User: Hello\nAssistant: Hi there!\nUser: How are you?"

    def test_get_context_with_empty_response(self):
        """Test get_context handles empty context response."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        mock_client.thread = MagicMock()

        # Mock empty context response
        mock_context = MagicMock()
        mock_context.context = None
        mock_client.thread.get_user_context.return_value = mock_context

        storage = ZepUserStorage(client=mock_client, user_id="test-user", thread_id="test-thread")

        # Get context
        context = storage.get_context()

        # Should return None for empty context
        assert context is None

    def test_search_with_filters(self):
        """Test that search filters are applied correctly."""
        from zep_cloud.client import Zep
        from zep_cloud.types import GraphSearchResults

        mock_client = MagicMock(spec=Zep)
        mock_client.thread = MagicMock()
        mock_client.thread.get_user_context = MagicMock(return_value=None)
        mock_client.graph = MagicMock()
        mock_client.graph.search = MagicMock()

        # Mock empty results
        mock_results = MagicMock(spec=GraphSearchResults)
        mock_results.edges = []
        mock_results.nodes = []
        mock_results.episodes = []
        mock_client.graph.search.return_value = mock_results

        search_filters = {"node_labels": ["Preference", "Project"]}
        storage = ZepUserStorage(
            client=mock_client,
            user_id="test-user",
            thread_id="test-thread",
            search_filters=search_filters,
        )

        # Perform search
        storage.search("test query", limit=5)

        # Verify search was called with filters
        calls = mock_client.graph.search.call_args_list
        for call in calls:
            if "user_id" in call[1]:  # Only check user graph searches
                assert call[1].get("search_filters") == search_filters

    def test_reset_does_nothing(self):
        """Test that reset method exists but does nothing."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        storage = ZepUserStorage(client=mock_client, user_id="test-user", thread_id="test-thread")

        # Should not raise exception
        storage.reset()

        # No methods should be called on client
        mock_client.assert_not_called()
