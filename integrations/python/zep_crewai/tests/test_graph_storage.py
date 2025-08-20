"""
Tests for ZepGraphStorage.
"""

from unittest.mock import MagicMock, Mock, call, patch

import pytest

from zep_crewai import ZepGraphStorage


class TestZepGraphStorage:
    """Test suite for ZepGraphStorage."""

    def test_initialization_success(self):
        """Test successful initialization with required parameters."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        storage = ZepGraphStorage(
            client=mock_client,
            graph_id="test-graph",
            search_filters={"node_labels": ["Technology"]},
            facts_limit=15,
            entity_limit=10
        )

        assert storage._client is mock_client
        assert storage.graph_id == "test-graph"
        assert storage._search_filters == {"node_labels": ["Technology"]}
        assert storage._facts_limit == 15
        assert storage._entity_limit == 10

    def test_initialization_requires_graph_id(self):
        """Test that graph_id is required."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        
        with pytest.raises(ValueError, match="graph_id is required"):
            ZepGraphStorage(client=mock_client, graph_id="")

    def test_initialization_requires_zep_client(self):
        """Test that client must be Zep instance."""
        with pytest.raises(TypeError, match="client must be an instance of Zep"):
            ZepGraphStorage(client="not_a_client", graph_id="test-graph")

    def test_save_text_data(self):
        """Test saving text data to graph."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        mock_client.graph = MagicMock()
        mock_client.graph.add = MagicMock()

        storage = ZepGraphStorage(client=mock_client, graph_id="test-graph")
        
        # Save text data
        storage.save("Python is great for AI", metadata={"type": "text"})
        
        # Verify graph.add was called correctly
        mock_client.graph.add.assert_called_once_with(
            graph_id="test-graph",
            data="Python is great for AI",
            type="text"
        )

    def test_save_json_data(self):
        """Test saving JSON data to graph."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        mock_client.graph = MagicMock()
        mock_client.graph.add = MagicMock()

        storage = ZepGraphStorage(client=mock_client, graph_id="test-graph")
        
        # Save JSON data
        json_data = '{"language": "Python", "use_case": "AI/ML"}'
        storage.save(json_data, metadata={"type": "json"})
        
        # Verify graph.add was called correctly
        mock_client.graph.add.assert_called_once_with(
            graph_id="test-graph",
            data=json_data,
            type="json"
        )

    def test_save_defaults_to_text(self):
        """Test that save defaults to text type when not specified."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        mock_client.graph = MagicMock()
        mock_client.graph.add = MagicMock()

        storage = ZepGraphStorage(client=mock_client, graph_id="test-graph")
        
        # Save without metadata
        storage.save("Default content")
        
        # Should default to text type
        mock_client.graph.add.assert_called_once_with(
            graph_id="test-graph",
            data="Default content",
            type="text"
        )

    @patch('zep_crewai.graph_storage.ThreadPoolExecutor')
    def test_search_with_results(self, mock_executor):
        """Test search with successful results from all scopes."""
        from zep_cloud.client import Zep
        from zep_cloud.types import EntityEdge, EntityNode, Episode, GraphSearchResults

        mock_client = MagicMock(spec=Zep)
        mock_client.graph = MagicMock()
        
        # Create mock results for different scopes
        mock_edge = MagicMock(spec=EntityEdge)
        mock_edge.fact = "Python is used for AI"
        mock_edge.name = "python_ai_fact"
        mock_edge.attributes = {"confidence": "high"}
        mock_edge.created_at = "2024-01-01"
        mock_edge.valid_at = "2024-01-01"
        mock_edge.invalid_at = None

        mock_node = MagicMock(spec=EntityNode)
        mock_node.name = "Python"
        mock_node.summary = "A programming language"
        mock_node.attributes = {"type": "language"}
        mock_node.created_at = "2024-01-01"

        mock_episode = MagicMock(spec=Episode)
        mock_episode.content = "Discussion about Python"
        mock_episode.source = "conversation"
        mock_episode.role = "assistant"
        mock_episode.created_at = "2024-01-01"

        # Setup mock executor for parallel execution
        mock_executor_instance = MagicMock()
        mock_executor.return_value.__enter__.return_value = mock_executor_instance
        
        # Mock futures for parallel execution
        future_edges = MagicMock()
        future_nodes = MagicMock()
        future_episodes = MagicMock()
        
        # Set up return values for futures
        edge_results = MagicMock(spec=GraphSearchResults)
        edge_results.edges = [mock_edge]
        future_edges.result.return_value = [{
            "memory": mock_edge.fact,
            "type": "edge",
            "name": mock_edge.name,
            "attributes": mock_edge.attributes,
            "created_at": mock_edge.created_at,
            "valid_at": mock_edge.valid_at,
            "invalid_at": mock_edge.invalid_at,
        }]
        
        node_results = MagicMock(spec=GraphSearchResults)
        node_results.nodes = [mock_node]
        future_nodes.result.return_value = [{
            "memory": f"{mock_node.name}: {mock_node.summary}",
            "type": "node",
            "name": mock_node.name,
            "attributes": mock_node.attributes,
            "created_at": mock_node.created_at,
        }]
        
        episode_results = MagicMock(spec=GraphSearchResults)
        episode_results.episodes = [mock_episode]
        future_episodes.result.return_value = [{
            "memory": mock_episode.content,
            "type": "episode",
            "source": mock_episode.source,
            "role": mock_episode.role,
            "created_at": mock_episode.created_at,
        }]
        
        mock_executor_instance.submit.side_effect = [future_edges, future_nodes, future_episodes]

        storage = ZepGraphStorage(client=mock_client, graph_id="test-graph")
        
        # Perform search
        results = storage.search("Python", limit=5)
        
        # Verify results
        assert isinstance(results, list)
        assert len(results) == 3  # One from each scope
        
        # Check edge result
        assert results[0]["type"] == "edge"
        assert results[0]["memory"] == "Python is used for AI"
        
        # Check node result
        assert results[1]["type"] == "node"
        assert "Python" in results[1]["memory"]
        
        # Check episode result
        assert results[2]["type"] == "episode"
        assert results[2]["memory"] == "Discussion about Python"

    @patch('zep_crewai.graph_storage.compose_context_string')
    @patch('zep_crewai.graph_storage.ThreadPoolExecutor')
    def test_get_context(self, mock_executor, mock_compose):
        """Test get_context method with compose_context_string."""
        from zep_cloud.client import Zep
        from zep_cloud.types import EntityEdge, EntityNode, GraphSearchResults

        mock_client = MagicMock(spec=Zep)
        mock_client.graph = MagicMock()
        
        # Create mock edge and node
        mock_edge = MagicMock(spec=EntityEdge)
        mock_edge.fact = "Python is used for AI"
        
        mock_node = MagicMock(spec=EntityNode)
        mock_node.name = "Python"
        mock_node.summary = "A programming language"
        
        # Setup mock executor
        mock_executor_instance = MagicMock()
        mock_executor.return_value.__enter__.return_value = mock_executor_instance
        
        # Mock futures
        future_edges = MagicMock()
        future_nodes = MagicMock()
        
        edge_results = MagicMock(spec=GraphSearchResults)
        edge_results.edges = [mock_edge]
        future_edges.result.return_value = edge_results
        
        node_results = MagicMock(spec=GraphSearchResults)
        node_results.nodes = [mock_node]
        future_nodes.result.return_value = node_results
        
        mock_executor_instance.submit.side_effect = [future_edges, future_nodes]
        
        # Mock compose_context_string
        mock_compose.return_value = "Facts:\n- Python is used for AI\n\nEntities:\n- Python: A programming language"
        
        storage = ZepGraphStorage(
            client=mock_client,
            graph_id="test-graph",
            facts_limit=20,
            entity_limit=5
        )
        
        # Get context
        context = storage.get_context("Python programming")
        
        # Verify compose_context_string was called with correct arguments
        mock_compose.assert_called_once_with([mock_edge], [mock_node], [])
        
        # Verify context is returned correctly
        assert context == "Facts:\n- Python is used for AI\n\nEntities:\n- Python: A programming language"

    @patch('zep_crewai.graph_storage.compose_context_string')
    @patch('zep_crewai.graph_storage.ThreadPoolExecutor')
    def test_get_context_with_recent_episodes(self, mock_executor, mock_compose):
        """Test get_context retrieves recent episodes when no query provided."""
        from zep_cloud.client import Zep
        from zep_cloud.types import Episode

        mock_client = MagicMock(spec=Zep)
        mock_client.graph = MagicMock()
        mock_client.graph.episode = MagicMock()
        
        # Mock recent episodes
        mock_episode1 = MagicMock(spec=Episode)
        mock_episode1.content = "First episode content"
        
        mock_episode2 = MagicMock(spec=Episode)
        mock_episode2.content = "Second episode content"
        
        mock_episodes_response = MagicMock()
        mock_episodes_response.episodes = [mock_episode1, mock_episode2]
        
        mock_client.graph.episode.get_by_graph_id.return_value = mock_episodes_response
        
        # Setup mock executor for search
        mock_executor_instance = MagicMock()
        mock_executor.return_value.__enter__.return_value = mock_executor_instance
        
        future_edges = MagicMock()
        future_nodes = MagicMock()
        
        from zep_cloud.types import GraphSearchResults
        edge_results = MagicMock(spec=GraphSearchResults)
        edge_results.edges = []
        future_edges.result.return_value = edge_results
        
        node_results = MagicMock(spec=GraphSearchResults)
        node_results.nodes = []
        future_nodes.result.return_value = node_results
        
        mock_executor_instance.submit.side_effect = [future_edges, future_nodes]
        
        mock_compose.return_value = "Context from recent episodes"
        
        storage = ZepGraphStorage(client=mock_client, graph_id="test-graph")
        
        # Get context without query
        context = storage.get_context()
        
        # Verify recent episodes were retrieved
        mock_client.graph.episode.get_by_graph_id.assert_called_once_with(
            graph_id="test-graph",
            lastn=2
        )
        
        # Verify search was called with episode content
        assert mock_executor_instance.submit.call_count == 2

    def test_get_context_returns_none_when_no_results(self):
        """Test get_context returns None when no results found."""
        from zep_cloud.client import Zep
        from zep_cloud.types import Episode

        mock_client = MagicMock(spec=Zep)
        mock_client.graph = MagicMock()
        mock_client.graph.episode = MagicMock()
        
        # Mock empty episodes response
        mock_episodes_response = MagicMock()
        mock_episodes_response.episodes = []
        mock_client.graph.episode.get_by_graph_id.return_value = mock_episodes_response
        
        storage = ZepGraphStorage(client=mock_client, graph_id="test-graph")
        
        # Get context without query and no episodes
        context = storage.get_context()
        
        # Should return None
        assert context is None

    def test_search_with_filters(self):
        """Test that search filters are applied correctly."""
        from zep_cloud.client import Zep
        from zep_cloud.types import GraphSearchResults

        mock_client = MagicMock(spec=Zep)
        mock_client.graph = MagicMock()
        mock_client.graph.search = MagicMock()
        
        # Mock empty results to avoid complex setup
        mock_results = MagicMock(spec=GraphSearchResults)
        mock_results.edges = []
        mock_results.nodes = []
        mock_results.episodes = []
        mock_client.graph.search.return_value = mock_results
        
        search_filters = {"node_labels": ["Technology", "Company"]}
        storage = ZepGraphStorage(
            client=mock_client,
            graph_id="test-graph",
            search_filters=search_filters
        )
        
        # Perform search to trigger filter usage
        storage.search("test query", limit=5)
        
        # Verify search was called with filters
        calls = mock_client.graph.search.call_args_list
        for call in calls:
            assert call[1].get("search_filters") == search_filters

    def test_reset_does_nothing(self):
        """Test that reset method exists but does nothing."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        storage = ZepGraphStorage(client=mock_client, graph_id="test-graph")
        
        # Should not raise exception
        storage.reset()
        
        # No methods should be called on client
        mock_client.assert_not_called()