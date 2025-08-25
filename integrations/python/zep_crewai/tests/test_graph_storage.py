"""
Tests for ZepGraphStorage.
"""

from unittest.mock import MagicMock, patch

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
            entity_limit=10,
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
            graph_id="test-graph", data="Python is great for AI", type="text"
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
            graph_id="test-graph", data=json_data, type="json"
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
            graph_id="test-graph", data="Default content", type="text"
        )

    @patch("zep_crewai.graph_storage.search_graph_and_compose_context")
    def test_search_with_results(self, mock_search_compose):
        """Test search returns composed context from graph."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        mock_client.graph = MagicMock()

        # Mock the search_graph_and_compose_context to return composed context
        mock_search_compose.return_value = (
            "Facts:\n- Python is used for AI\n\n"
            "Entities:\n- Python: A programming language\n\n"
            "Episodes:\n- Discussion about Python"
        )

        storage = ZepGraphStorage(client=mock_client, graph_id="test-graph")

        # Perform search
        results = storage.search("Python", limit=5)

        # Verify results
        assert isinstance(results, list)
        assert len(results) == 1  # Single composed result

        # Check the composed result
        assert results[0]["memory"] == (
            "Facts:\n- Python is used for AI\n\n"
            "Entities:\n- Python: A programming language\n\n"
            "Episodes:\n- Discussion about Python"
        )
        assert results[0]["type"] == "graph_context"
        assert results[0]["source"] == "graph"
        assert results[0]["query"] == "Python"

        # Verify search_graph_and_compose_context was called with correct args
        mock_search_compose.assert_called_once_with(
            client=mock_client,
            query="Python",
            graph_id="test-graph",
            facts_limit=20,
            entity_limit=5,
            episodes_limit=5,
            search_filters=None,
        )

    @patch("zep_crewai.graph_storage.search_graph_and_compose_context")
    def test_search_with_no_results(self, mock_search_compose):
        """Test search returns empty list when no results found."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        mock_client.graph = MagicMock()

        # Mock the search to return None (no results)
        mock_search_compose.return_value = None

        storage = ZepGraphStorage(client=mock_client, graph_id="test-graph")

        # Perform search
        results = storage.search("NonExistent", limit=5)

        # Verify empty results
        assert isinstance(results, list)
        assert len(results) == 0

    @patch("zep_crewai.graph_storage.search_graph_and_compose_context")
    def test_search_with_custom_limits(self, mock_search_compose):
        """Test search passes custom limits to search function."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        mock_client.graph = MagicMock()

        # Mock the search to return composed context
        mock_search_compose.return_value = "Context with custom limits"

        storage = ZepGraphStorage(
            client=mock_client, graph_id="test-graph", facts_limit=30, entity_limit=10
        )

        # Perform search
        storage.search("test", limit=15)

        # Verify search_graph_and_compose_context was called with custom limits
        mock_search_compose.assert_called_once_with(
            client=mock_client,
            query="test",
            graph_id="test-graph",
            facts_limit=30,
            entity_limit=10,
            episodes_limit=15,  # Uses the limit parameter for episodes
            search_filters=None,
        )

    @patch("zep_crewai.graph_storage.search_graph_and_compose_context")
    def test_search_with_filters(self, mock_search_compose):
        """Test that search filters are passed correctly."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        mock_client.graph = MagicMock()

        # Mock the search to return composed context
        mock_search_compose.return_value = "Filtered context"

        search_filters = {"node_labels": ["Technology", "Company"]}
        storage = ZepGraphStorage(
            client=mock_client, graph_id="test-graph", search_filters=search_filters
        )

        # Perform search to trigger filter usage
        storage.search("test query", limit=5)

        # Verify search_graph_and_compose_context was called with filters
        mock_search_compose.assert_called_once_with(
            client=mock_client,
            query="test query",
            graph_id="test-graph",
            facts_limit=20,
            entity_limit=5,
            episodes_limit=5,
            search_filters=search_filters,
        )

    def test_reset_does_nothing(self):
        """Test that reset method exists but does nothing."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        storage = ZepGraphStorage(client=mock_client, graph_id="test-graph")

        # Should not raise exception
        storage.reset()

        # No methods should be called on client
        mock_client.assert_not_called()
