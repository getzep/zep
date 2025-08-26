"""
Tests for Zep CrewAI Tools.
"""

from unittest.mock import MagicMock

import pytest

from zep_crewai import (
    ZepAddDataTool,
    ZepSearchTool,
    create_add_data_tool,
    create_search_tool,
)


class TestZepSearchTool:
    """Test suite for ZepSearchTool."""

    def test_initialization_with_graph_id(self):
        """Test initialization with graph_id."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        tool = ZepSearchTool(client=mock_client, graph_id="test-graph")

        assert tool.client is mock_client
        assert tool.graph_id == "test-graph"
        assert tool.user_id is None
        assert "test-graph" in tool.description

    def test_initialization_with_user_id(self):
        """Test initialization with user_id."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        tool = ZepSearchTool(client=mock_client, user_id="test-user")

        assert tool.client is mock_client
        assert tool.user_id == "test-user"
        assert tool.graph_id is None
        assert "test-user" in tool.description

    def test_initialization_requires_either_graph_or_user(self):
        """Test that either graph_id or user_id is required."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)

        with pytest.raises(ValueError, match="Either graph_id or user_id must be provided"):
            ZepSearchTool(client=mock_client)

    def test_initialization_prevents_both_graph_and_user(self):
        """Test that both graph_id and user_id cannot be provided."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)

        with pytest.raises(ValueError, match="Only one of graph_id or user_id should be provided"):
            ZepSearchTool(client=mock_client, graph_id="test-graph", user_id="test-user")

    def test_search_graph_edges(self):
        """Test searching graph for edges."""
        from zep_cloud.client import Zep
        from zep_cloud.types import EntityEdge, GraphSearchResults

        mock_client = MagicMock(spec=Zep)
        mock_client.graph = MagicMock()

        # Mock edge result
        mock_edge = MagicMock(spec=EntityEdge)
        mock_edge.fact = "Python is great for AI"
        mock_edge.name = "python_fact"
        mock_edge.created_at = "2024-01-01"

        mock_results = MagicMock(spec=GraphSearchResults)
        mock_results.edges = [mock_edge]
        mock_results.nodes = []
        mock_results.episodes = []

        mock_client.graph.search.return_value = mock_results

        tool = ZepSearchTool(client=mock_client, graph_id="test-graph")

        # Run search
        result = tool._run("Python", limit=5, scope="edges")

        # Verify search was called correctly
        mock_client.graph.search.assert_called_once_with(
            graph_id="test-graph", query="Python", limit=5, scope="edges"
        )

        # Check result formatting
        assert "Found 1 relevant memories" in result
        assert "Python is great for AI" in result
        assert "[FACT]" in result

    def test_search_user_nodes(self):
        """Test searching user graph for nodes."""
        from zep_cloud.client import Zep
        from zep_cloud.types import EntityNode, GraphSearchResults

        mock_client = MagicMock(spec=Zep)
        mock_client.graph = MagicMock()

        # Mock node result
        mock_node = MagicMock(spec=EntityNode)
        mock_node.name = "UserPreference"
        mock_node.summary = "User's programming preferences"
        mock_node.created_at = "2024-01-01"

        mock_results = MagicMock(spec=GraphSearchResults)
        mock_results.edges = []
        mock_results.nodes = [mock_node]
        mock_results.episodes = []

        mock_client.graph.search.return_value = mock_results

        tool = ZepSearchTool(client=mock_client, user_id="test-user")

        # Run search
        result = tool._run("preferences", limit=3, scope="nodes")

        # Verify search was called correctly
        mock_client.graph.search.assert_called_once_with(
            user_id="test-user", query="preferences", limit=3, scope="nodes"
        )

        # Check result formatting
        assert "Found 1 relevant memories" in result
        assert "UserPreference" in result
        assert "[ENTITY]" in result

    def test_search_all_scopes(self):
        """Test searching all scopes at once."""
        from zep_cloud.client import Zep
        from zep_cloud.types import EntityEdge, EntityNode, Episode, GraphSearchResults

        mock_client = MagicMock(spec=Zep)
        mock_client.graph = MagicMock()

        # Mock different result types
        mock_edge = MagicMock(spec=EntityEdge)
        mock_edge.fact = "Fact content"
        mock_edge.name = "fact"
        mock_edge.created_at = None

        mock_node = MagicMock(spec=EntityNode)
        mock_node.name = "Entity"
        mock_node.summary = "Entity description"
        mock_node.created_at = None

        mock_episode = MagicMock(spec=Episode)
        mock_episode.content = "Episode content"
        mock_episode.source = "chat"
        mock_episode.role = "user"
        mock_episode.created_at = None

        # Return different results for each scope
        def mock_search(*args, **kwargs):
            scope = kwargs.get("scope", "edges")
            mock_results = MagicMock(spec=GraphSearchResults)

            if scope == "edges":
                mock_results.edges = [mock_edge]
                mock_results.nodes = []
                mock_results.episodes = []
            elif scope == "nodes":
                mock_results.edges = []
                mock_results.nodes = [mock_node]
                mock_results.episodes = []
            elif scope == "episodes":
                mock_results.edges = []
                mock_results.nodes = []
                mock_results.episodes = [mock_episode]

            return mock_results

        mock_client.graph.search.side_effect = mock_search

        tool = ZepSearchTool(client=mock_client, graph_id="test-graph")

        # Run search with scope="all"
        result = tool._run("test", limit=5, scope="all")

        # Should have called search 3 times (edges, nodes, episodes)
        assert mock_client.graph.search.call_count == 3

        # Check all result types are in output
        assert "Found 3 relevant memories" in result
        assert "Fact content" in result
        assert "Entity: Entity description" in result
        assert "Episode content" in result

    def test_search_no_results(self):
        """Test search with no results."""
        from zep_cloud.client import Zep
        from zep_cloud.types import GraphSearchResults

        mock_client = MagicMock(spec=Zep)
        mock_client.graph = MagicMock()

        mock_results = MagicMock(spec=GraphSearchResults)
        mock_results.edges = []
        mock_results.nodes = []
        mock_results.episodes = []

        mock_client.graph.search.return_value = mock_results

        tool = ZepSearchTool(client=mock_client, graph_id="test-graph")

        # Run search
        result = tool._run("nonexistent", limit=5)

        # Should return no results message
        assert "No results found for query: 'nonexistent'" in result

    def test_search_error_handling(self):
        """Test error handling during search."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        mock_client.graph = MagicMock()
        mock_client.graph.search.side_effect = Exception("API error")

        tool = ZepSearchTool(client=mock_client, graph_id="test-graph")

        # Run search
        result = tool._run("test", limit=5)

        # Should return error message
        assert "Error searching Zep memory: API error" in result


class TestZepAddDataTool:
    """Test suite for ZepAddDataTool."""

    def test_initialization_with_graph_id(self):
        """Test initialization with graph_id."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        tool = ZepAddDataTool(client=mock_client, graph_id="test-graph")

        assert tool.client is mock_client
        assert tool.graph_id == "test-graph"
        assert tool.user_id is None
        assert "test-graph" in tool.description

    def test_initialization_with_user_id(self):
        """Test initialization with user_id."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        tool = ZepAddDataTool(client=mock_client, user_id="test-user")

        assert tool.client is mock_client
        assert tool.user_id == "test-user"
        assert tool.graph_id is None
        assert "test-user" in tool.description

    def test_add_text_to_graph(self):
        """Test adding text data to graph."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        mock_client.graph = MagicMock()
        mock_client.graph.add = MagicMock()

        tool = ZepAddDataTool(client=mock_client, graph_id="test-graph")

        # Add text data
        result = tool._run("Python is versatile", data_type="text")

        # Verify add was called correctly
        mock_client.graph.add.assert_called_once_with(
            graph_id="test-graph", type="text", data="Python is versatile"
        )

        # Check success message
        assert "Successfully added text data to graph 'test-graph'" in result

    def test_add_json_to_user(self):
        """Test adding JSON data to user graph."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        mock_client.graph = MagicMock()
        mock_client.graph.add = MagicMock()

        tool = ZepAddDataTool(client=mock_client, user_id="test-user")

        # Add JSON data
        json_data = '{"preference": "dark_mode", "language": "en"}'
        result = tool._run(json_data, data_type="json")

        # Verify add was called correctly
        mock_client.graph.add.assert_called_once_with(
            user_id="test-user", type="json", data=json_data
        )

        # Check success message
        assert "Successfully added json data to user 'test-user' memory" in result

    def test_add_defaults_to_text(self):
        """Test that data_type defaults to text."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        mock_client.graph = MagicMock()
        mock_client.graph.add = MagicMock()

        tool = ZepAddDataTool(client=mock_client, graph_id="test-graph")

        # Add with invalid type (should default to text)
        tool._run("Some data", data_type="invalid")

        # Should use text type
        mock_client.graph.add.assert_called_once_with(
            graph_id="test-graph", type="text", data="Some data"
        )

    def test_add_error_handling(self):
        """Test error handling during add."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        mock_client.graph = MagicMock()
        mock_client.graph.add.side_effect = Exception("API error")

        tool = ZepAddDataTool(client=mock_client, graph_id="test-graph")

        # Try to add data
        result = tool._run("Test data", data_type="text")

        # Should return error message
        assert "Error adding data to Zep: API error" in result


class TestToolFactoryFunctions:
    """Test the factory functions for creating tools."""

    def test_create_search_tool_with_graph(self):
        """Test creating search tool with graph_id."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        tool = create_search_tool(mock_client, graph_id="test-graph")

        assert isinstance(tool, ZepSearchTool)
        assert tool.graph_id == "test-graph"
        assert tool.user_id is None

    def test_create_search_tool_with_user(self):
        """Test creating search tool with user_id."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        tool = create_search_tool(mock_client, user_id="test-user")

        assert isinstance(tool, ZepSearchTool)
        assert tool.user_id == "test-user"
        assert tool.graph_id is None

    def test_create_add_data_tool_with_graph(self):
        """Test creating add data tool with graph_id."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        tool = create_add_data_tool(mock_client, graph_id="test-graph")

        assert isinstance(tool, ZepAddDataTool)
        assert tool.graph_id == "test-graph"
        assert tool.user_id is None

    def test_create_add_data_tool_with_user(self):
        """Test creating add data tool with user_id."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        tool = create_add_data_tool(mock_client, user_id="test-user")

        assert isinstance(tool, ZepAddDataTool)
        assert tool.user_id == "test-user"
        assert tool.graph_id is None

    def test_create_tools_require_id(self):
        """Test that factory functions require either graph_id or user_id."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)

        with pytest.raises(ValueError):
            create_search_tool(mock_client)

        with pytest.raises(ValueError):
            create_add_data_tool(mock_client)

    def test_create_tools_prevent_both_ids(self):
        """Test that factory functions prevent both IDs."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)

        with pytest.raises(ValueError):
            create_search_tool(mock_client, graph_id="test", user_id="test")

        with pytest.raises(ValueError):
            create_add_data_tool(mock_client, graph_id="test", user_id="test")


class TestToolIntegration:
    """Test tools integration with CrewAI patterns."""

    def test_tool_has_correct_attributes(self):
        """Test that tools have attributes expected by CrewAI."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        search_tool = ZepSearchTool(client=mock_client, graph_id="test")
        add_tool = ZepAddDataTool(client=mock_client, user_id="test")

        # Check required CrewAI tool attributes
        assert hasattr(search_tool, "name")
        assert hasattr(search_tool, "description")
        assert hasattr(search_tool, "args_schema")
        assert hasattr(search_tool, "_run")

        assert hasattr(add_tool, "name")
        assert hasattr(add_tool, "description")
        assert hasattr(add_tool, "args_schema")
        assert hasattr(add_tool, "_run")

        # Check names are set
        assert search_tool.name == "Zep Memory Search"
        assert add_tool.name == "Zep Add Data"

    def test_tool_schemas(self):
        """Test that tool schemas are properly defined."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        search_tool = ZepSearchTool(client=mock_client, graph_id="test")
        add_tool = ZepAddDataTool(client=mock_client, user_id="test")

        # Check search tool schema
        search_schema = search_tool.args_schema
        assert "query" in search_schema.model_fields
        assert "limit" in search_schema.model_fields
        assert "scope" in search_schema.model_fields

        # Check add tool schema
        add_schema = add_tool.args_schema
        assert "data" in add_schema.model_fields
        assert "data_type" in add_schema.model_fields
