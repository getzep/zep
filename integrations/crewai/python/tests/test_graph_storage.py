"""
Tests for ZepGraphStorage.
"""

from unittest.mock import MagicMock, patch

import pytest

from zep_crewai import ZepGraphStorage
from zep_crewai.utils import DEFAULT_CONTEXT_TEMPLATE

GRAPH_UUID = "33333333-3333-3333-3333-333333333333"


def _make_mock_client():
    from zep_cloud.client import Zep

    client = MagicMock(spec=Zep)
    client.graph = MagicMock()
    client.graph.episode = MagicMock()
    return client


class TestZepGraphStorage:
    """Test suite for ZepGraphStorage."""

    def test_initialization_success(self):
        """Test successful initialization with required parameters."""
        mock_client = _make_mock_client()
        storage = ZepGraphStorage(
            client=mock_client,
            graph_uuid=GRAPH_UUID,
            search_filters={"node_labels": ["Technology"]},
            max_characters=1500,
        )

        assert storage._client is mock_client
        assert storage.graph_uuid == GRAPH_UUID
        assert storage._search_filters == {"node_labels": ["Technology"]}
        assert storage._max_characters == 1500

    def test_initialization_requires_graph_uuid(self):
        """Test that graph_uuid is required."""
        mock_client = _make_mock_client()

        with pytest.raises(ValueError, match="graph_uuid is required"):
            ZepGraphStorage(client=mock_client, graph_uuid="")

    def test_initialization_requires_zep_client(self):
        """Test that client must be Zep instance."""
        with pytest.raises(TypeError, match="client must be an instance of Zep"):
            ZepGraphStorage(client="not_a_client", graph_uuid=GRAPH_UUID)

    def test_save_text_data(self):
        """Test saving text data to graph."""
        mock_client = _make_mock_client()
        mock_client.graph.episode.add = MagicMock()

        storage = ZepGraphStorage(client=mock_client, graph_uuid=GRAPH_UUID)

        # Save text data
        storage.save("Python is great for AI", metadata={"type": "text"})

        # Verify graph.episode.add was called correctly
        mock_client.graph.episode.add.assert_called_once_with(
            GRAPH_UUID, data="Python is great for AI", type="text"
        )

    def test_save_json_data(self):
        """Test saving JSON data to graph."""
        mock_client = _make_mock_client()
        mock_client.graph.episode.add = MagicMock()

        storage = ZepGraphStorage(client=mock_client, graph_uuid=GRAPH_UUID)

        # Save JSON data
        json_data = '{"language": "Python", "use_case": "AI/ML"}'
        storage.save(json_data, metadata={"type": "json"})

        # Verify graph.episode.add was called correctly
        mock_client.graph.episode.add.assert_called_once_with(
            GRAPH_UUID, data=json_data, type="json"
        )

    def test_save_defaults_to_text(self):
        """Test that save defaults to text type when not specified."""
        mock_client = _make_mock_client()
        mock_client.graph.episode.add = MagicMock()

        storage = ZepGraphStorage(client=mock_client, graph_uuid=GRAPH_UUID)

        # Save without metadata
        storage.save("Default content")

        # Should default to text type
        mock_client.graph.episode.add.assert_called_once_with(
            GRAPH_UUID, data="Default content", type="text"
        )

    def test_save_does_not_raise_on_zep_error(self, caplog):
        """save() must log and return normally when the Zep SDK call raises --
        never propagate the error into the crew."""
        mock_client = _make_mock_client()
        mock_client.graph.episode.add = MagicMock(side_effect=Exception("Zep API error"))

        storage = ZepGraphStorage(client=mock_client, graph_uuid=GRAPH_UUID)

        with caplog.at_level("ERROR"):
            storage.save("Python is great for AI", metadata={"type": "text"})

        mock_client.graph.episode.add.assert_called_once()
        assert "Zep API error" in caplog.text

    @patch("zep_crewai.graph_storage.compose_graph_context")
    def test_search_with_results(self, mock_compose):
        """Test search returns the Context Block of the graph."""
        mock_client = _make_mock_client()

        mock_compose.return_value = (
            "Facts:\n- Python is used for AI\n\nEntities:\n- Python: A programming language"
        )

        storage = ZepGraphStorage(client=mock_client, graph_uuid=GRAPH_UUID)

        # Perform search
        results = storage.search("Python", limit=5)

        # Verify results
        assert isinstance(results, list)
        assert len(results) == 1  # Single composed result

        assert results[0]["context"] == (
            "Facts:\n- Python is used for AI\n\nEntities:\n- Python: A programming language"
        )
        assert results[0]["type"] == "graph_context"
        assert results[0]["source"] == "graph"
        assert results[0]["query"] == "Python"

        mock_compose.assert_called_once_with(
            client=mock_client,
            query="Python",
            graph_uuid=GRAPH_UUID,
            max_characters=None,
            search_filters=None,
            context_template=DEFAULT_CONTEXT_TEMPLATE,
        )

    @patch("zep_crewai.graph_storage.compose_graph_context")
    def test_search_with_no_results(self, mock_compose):
        """Test search returns empty list when no results found."""
        mock_client = _make_mock_client()

        # Mock the search to return None (no results)
        mock_compose.return_value = None

        storage = ZepGraphStorage(client=mock_client, graph_uuid=GRAPH_UUID)

        # Perform search
        results = storage.search("NonExistent", limit=5)

        # Verify empty results
        assert isinstance(results, list)
        assert len(results) == 0

    @patch("zep_crewai.graph_storage.compose_graph_context")
    def test_search_with_max_characters(self, mock_compose):
        """Test search passes max_characters to the retrieval function."""
        mock_client = _make_mock_client()

        mock_compose.return_value = "Context with a maximum length"

        storage = ZepGraphStorage(client=mock_client, graph_uuid=GRAPH_UUID, max_characters=3000)

        # Perform search
        storage.search("test", limit=15)

        mock_compose.assert_called_once_with(
            client=mock_client,
            query="test",
            graph_uuid=GRAPH_UUID,
            max_characters=3000,
            search_filters=None,
            context_template=DEFAULT_CONTEXT_TEMPLATE,
        )

    @patch("zep_crewai.graph_storage.compose_graph_context")
    def test_search_with_filters(self, mock_compose):
        """Test that search filters are passed correctly."""
        mock_client = _make_mock_client()

        mock_compose.return_value = "Filtered context"

        search_filters = {"node_labels": ["Technology", "Company"]}
        storage = ZepGraphStorage(
            client=mock_client, graph_uuid=GRAPH_UUID, search_filters=search_filters
        )

        # Perform search to trigger filter usage
        storage.search("test query", limit=5)

        mock_compose.assert_called_once_with(
            client=mock_client,
            query="test query",
            graph_uuid=GRAPH_UUID,
            max_characters=None,
            search_filters=search_filters,
            context_template=DEFAULT_CONTEXT_TEMPLATE,
        )

    def test_reset_does_nothing(self):
        """Test that reset method exists but does nothing."""
        mock_client = _make_mock_client()
        storage = ZepGraphStorage(client=mock_client, graph_uuid=GRAPH_UUID)

        # Should not raise exception
        storage.reset()

        # No methods should be called on client
        mock_client.assert_not_called()
