"""
Tests for Zep CrewAI Tools.

``ZepSearchTool``/``create_search_tool`` follow the pin-or-expose pattern
(BREAKING in this version -- see the CHANGELOG): every ``graph.search``
parameter (``scope``, ``reranker``, ``limit``, ``mmr_lambda``,
``center_node_uuid``) is exposed in the tool's dynamically-built
``args_schema`` by default and can be pinned or hidden at construction time.
"""

import logging
from unittest.mock import MagicMock

import pytest

from zep_crewai import (
    ZepAddDataTool,
    ZepSearchTool,
    create_add_data_tool,
    create_search_tool,
)
from zep_crewai.tools import MAX_SEARCH_LIMIT


def _make_mock_graph_results(edges=None, nodes=None, episodes=None):
    r = MagicMock()
    r.edges = edges or []
    r.nodes = nodes or []
    r.episodes = episodes or []
    r.observations = []
    r.thread_summaries = []
    r.context = None
    return r


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
        from zep_cloud.types import EntityEdge

        mock_client = MagicMock(spec=Zep)
        mock_client.graph = MagicMock()

        # Mock edge result
        mock_edge = MagicMock(spec=EntityEdge)
        mock_edge.fact = "Python is great for AI"

        mock_results = _make_mock_graph_results(edges=[mock_edge])
        mock_client.graph.search.return_value = mock_results

        tool = ZepSearchTool(client=mock_client, graph_id="test-graph")

        # Run search (direct _run call: params not supplied are omitted --
        # CrewAI's run() fills args_schema defaults before reaching _run)
        result = tool._run(query="Python", limit=5, scope="edges")

        # Verify search was called correctly
        mock_client.graph.search.assert_called_once_with(
            query="Python", graph_id="test-graph", scope="edges", limit=5
        )

        # Check result formatting
        assert "Python is great for AI" in result

    def test_search_user_nodes(self):
        """Test searching user graph for nodes."""
        from zep_cloud.client import Zep
        from zep_cloud.types import EntityNode

        mock_client = MagicMock(spec=Zep)
        mock_client.graph = MagicMock()

        # Mock node result
        mock_node = MagicMock(spec=EntityNode)
        mock_node.name = "UserPreference"
        mock_node.summary = "User's programming preferences"

        mock_results = _make_mock_graph_results(nodes=[mock_node])
        mock_client.graph.search.return_value = mock_results

        tool = ZepSearchTool(client=mock_client, user_id="test-user")

        # Run search (direct _run call: params not supplied are omitted --
        # CrewAI's run() fills args_schema defaults before reaching _run)
        result = tool._run(query="preferences", limit=3, scope="nodes")

        # Verify search was called correctly
        mock_client.graph.search.assert_called_once_with(
            query="preferences", user_id="test-user", scope="nodes", limit=3
        )

        # Check result formatting
        assert "UserPreference" in result

    def test_search_no_results(self):
        """Test search with no results."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        mock_client.graph = MagicMock()
        mock_client.graph.search.return_value = _make_mock_graph_results()

        tool = ZepSearchTool(client=mock_client, graph_id="test-graph")

        # Run search
        result = tool._run(query="nonexistent", limit=5)

        # Should return no results message
        assert "No results found." in result

    def test_search_empty_query_is_error(self):
        """An empty query must return an error string without calling Zep."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        mock_client.graph = MagicMock()

        tool = ZepSearchTool(client=mock_client, graph_id="test-graph")

        result = tool._run(query="   ")

        assert "Error" in result
        mock_client.graph.search.assert_not_called()

    def test_search_error_handling(self):
        """Test error handling during search."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        mock_client.graph = MagicMock()
        mock_client.graph.search.side_effect = Exception("API error")

        tool = ZepSearchTool(client=mock_client, graph_id="test-graph")

        # Run search
        result = tool._run(query="test", limit=5)

        # Should return error message
        assert "Error searching Zep memory: API error" in result


class TestZepSearchToolPinOrExpose:
    """Pin-or-expose contract tests (BREAKING change)."""

    def test_search_tool_exposes_params_by_default(self):
        """Every graph.search parameter is exposed in args_schema by default."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        tool = ZepSearchTool(client=mock_client, user_id="u1")

        fields = tool.args_schema.model_fields
        for param_name in ("query", "scope", "reranker", "limit", "mmr_lambda", "center_node_uuid"):
            assert param_name in fields, f"{param_name} should be exposed by default"

    def test_search_tool_six_scopes(self):
        """The scope field's Literal type carries all six documented values."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        tool = ZepSearchTool(client=mock_client, user_id="u1")

        scope_annotation = tool.args_schema.model_fields["scope"].annotation
        assert set(scope_annotation.__args__) == {
            "edges",
            "nodes",
            "episodes",
            "observations",
            "thread_summaries",
            "auto",
        }

    def test_search_tool_pinned_params_hidden_and_sent(self):
        """A pinned param disappears from args_schema but is always sent."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        mock_client.graph = MagicMock()
        mock_client.graph.search.return_value = _make_mock_graph_results()

        tool = ZepSearchTool(
            client=mock_client, user_id="u1", pinned_params={"scope": "nodes", "limit": 3}
        )

        assert "scope" not in tool.args_schema.model_fields
        assert "limit" not in tool.args_schema.model_fields

        tool._run(query="hello")

        call_kwargs = mock_client.graph.search.call_args.kwargs
        assert call_kwargs["scope"] == "nodes"
        assert call_kwargs["limit"] == 3

    def test_search_tool_hidden_params_omitted_from_sdk_call(self):
        """A hidden (not pinned) param disappears from args_schema and is
        never sent -- Zep's own default applies."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        mock_client.graph = MagicMock()
        mock_client.graph.search.return_value = _make_mock_graph_results()

        tool = ZepSearchTool(client=mock_client, user_id="u1", hidden_params={"reranker"})

        assert "reranker" not in tool.args_schema.model_fields

        tool._run(query="hello")

        call_kwargs = mock_client.graph.search.call_args.kwargs
        assert "reranker" not in call_kwargs

    def test_search_tool_query_only_omits_unset_none_default_params(self):
        """mmr_lambda / center_node_uuid default to None; when unset by the
        caller they must be OMITTED from the graph.search call, never sent
        as explicit None."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        mock_client.graph = MagicMock()
        mock_client.graph.search.return_value = _make_mock_graph_results()

        tool = ZepSearchTool(client=mock_client, user_id="u1")

        tool._run(query="hello")

        call_kwargs = mock_client.graph.search.call_args.kwargs
        assert "mmr_lambda" not in call_kwargs
        assert "center_node_uuid" not in call_kwargs

    def test_search_tool_legacy_args_pin(self):
        """The legacy scope=/reranker=/limit= constructor args still pin
        (and thus hide) their parameter."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        mock_client.graph = MagicMock()
        mock_client.graph.search.return_value = _make_mock_graph_results()

        tool = ZepSearchTool(client=mock_client, user_id="u1", scope="nodes", limit=7)

        assert "scope" not in tool.args_schema.model_fields
        assert "limit" not in tool.args_schema.model_fields

        tool._run(query="hello")

        call_kwargs = mock_client.graph.search.call_args.kwargs
        assert call_kwargs["scope"] == "nodes"
        assert call_kwargs["limit"] == 7

    def test_search_tool_unknown_pinned_param_raises(self):
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        with pytest.raises(ValueError, match="Unknown pinned"):
            ZepSearchTool(client=mock_client, user_id="u1", pinned_params={"bogus": "x"})

    def test_search_tool_unknown_hidden_param_raises(self):
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        with pytest.raises(ValueError, match="Unknown hidden"):
            ZepSearchTool(client=mock_client, user_id="u1", hidden_params={"bogus"})

    def test_search_tool_constructor_only_search_filters(self):
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        mock_client.graph = MagicMock()
        mock_client.graph.search.return_value = _make_mock_graph_results()

        tool = ZepSearchTool(
            client=mock_client, user_id="u1", search_filters={"node_labels": ["Person"]}
        )
        assert "search_filters" not in tool.args_schema.model_fields

        tool._run(query="hello")

        call_kwargs = mock_client.graph.search.call_args.kwargs
        assert call_kwargs["search_filters"] == {"node_labels": ["Person"]}

    def test_search_tool_bfs_origin_node_uuids_constructor_only(self):
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        mock_client.graph = MagicMock()
        mock_client.graph.search.return_value = _make_mock_graph_results()

        tool = ZepSearchTool(client=mock_client, user_id="u1", bfs_origin_node_uuids=["uuid-1"])
        assert "bfs_origin_node_uuids" not in tool.args_schema.model_fields

        tool._run(query="hello")

        call_kwargs = mock_client.graph.search.call_args.kwargs
        assert call_kwargs["bfs_origin_node_uuids"] == ["uuid-1"]

    def test_search_tool_query_truncated_to_400_chars(self):
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        mock_client.graph = MagicMock()
        mock_client.graph.search.return_value = _make_mock_graph_results()

        tool = ZepSearchTool(client=mock_client, user_id="u1")
        tool._run(query="x" * 500)

        call_kwargs = mock_client.graph.search.call_args.kwargs
        assert len(call_kwargs["query"]) == 400


class TestSearchLimitClamping:
    """A limit above Zep's ceiling (or below 1) is clamped, never rejected."""

    def test_pinned_limit_clamped_with_warning(self, caplog):
        """A pinned limit above MAX_SEARCH_LIMIT is clamped at construction
        with a warning, and the clamped value is sent."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        mock_client.graph = MagicMock()
        mock_client.graph.search.return_value = _make_mock_graph_results()

        with caplog.at_level(logging.WARNING, logger="zep_crewai.tools"):
            tool = ZepSearchTool(client=mock_client, user_id="u1", pinned_params={"limit": 100})

        assert any("clamping" in record.message for record in caplog.records)

        tool._run(query="hello")

        call_kwargs = mock_client.graph.search.call_args.kwargs
        assert call_kwargs["limit"] == MAX_SEARCH_LIMIT

    def test_pinned_limit_below_one_clamped_to_one(self):
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        mock_client.graph = MagicMock()
        mock_client.graph.search.return_value = _make_mock_graph_results()

        tool = ZepSearchTool(client=mock_client, user_id="u1", pinned_params={"limit": 0})

        tool._run(query="hello")

        call_kwargs = mock_client.graph.search.call_args.kwargs
        assert call_kwargs["limit"] == 1

    def test_legacy_limit_arg_clamped(self):
        """The legacy limit= constructor arg pins and is clamped the same way."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        mock_client.graph = MagicMock()
        mock_client.graph.search.return_value = _make_mock_graph_results()

        tool = ZepSearchTool(client=mock_client, user_id="u1", limit=100)

        tool._run(query="hello")

        call_kwargs = mock_client.graph.search.call_args.kwargs
        assert call_kwargs["limit"] == MAX_SEARCH_LIMIT

    def test_model_provided_limit_clamped_at_call_time(self):
        """A model-provided limit above the ceiling is clamped to 50, not
        rejected -- the tool never 400s on limit."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        mock_client.graph = MagicMock()
        mock_client.graph.search.return_value = _make_mock_graph_results()

        tool = ZepSearchTool(client=mock_client, user_id="u1")

        tool._run(query="hello", limit=200)

        call_kwargs = mock_client.graph.search.call_args.kwargs
        assert call_kwargs["limit"] == MAX_SEARCH_LIMIT

    def test_model_provided_limit_below_one_clamped_to_one(self):
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        mock_client.graph = MagicMock()
        mock_client.graph.search.return_value = _make_mock_graph_results()

        tool = ZepSearchTool(client=mock_client, user_id="u1")

        tool._run(query="hello", limit=-3)

        call_kwargs = mock_client.graph.search.call_args.kwargs
        assert call_kwargs["limit"] == 1

    def test_valid_limit_passes_through_unchanged(self):
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        mock_client.graph = MagicMock()
        mock_client.graph.search.return_value = _make_mock_graph_results()

        tool = ZepSearchTool(client=mock_client, user_id="u1")

        tool._run(query="hello", limit=25)

        call_kwargs = mock_client.graph.search.call_args.kwargs
        assert call_kwargs["limit"] == 25


class TestAutoScopeReranker:
    """scope='auto' ignores reranker and rejects node_distance /
    episode_mentions outright -- the reranker must be dropped, not sent."""

    def test_pinned_auto_scope_drops_incompatible_reranker(self, caplog):
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        mock_client.graph = MagicMock()
        mock_client.graph.search.return_value = _make_mock_graph_results()

        with caplog.at_level(logging.WARNING, logger="zep_crewai.tools"):
            tool = ZepSearchTool(
                client=mock_client,
                user_id="u1",
                pinned_params={"scope": "auto", "reranker": "node_distance"},
            )

        assert any("reranker" in record.message for record in caplog.records)

        tool._run(query="hello")

        call_kwargs = mock_client.graph.search.call_args.kwargs
        assert call_kwargs["scope"] == "auto"
        assert "reranker" not in call_kwargs

    def test_model_provided_auto_scope_drops_incompatible_reranker(self):
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        mock_client.graph = MagicMock()
        mock_client.graph.search.return_value = _make_mock_graph_results()

        tool = ZepSearchTool(client=mock_client, user_id="u1")

        tool._run(query="hello", scope="auto", reranker="episode_mentions")

        call_kwargs = mock_client.graph.search.call_args.kwargs
        assert call_kwargs["scope"] == "auto"
        assert "reranker" not in call_kwargs

    def test_pinned_auto_scope_with_model_reranker_dropped(self):
        """scope pinned to 'auto' + reranker left model-exposed: a
        model-provided incompatible reranker is still dropped at call time."""
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        mock_client.graph = MagicMock()
        mock_client.graph.search.return_value = _make_mock_graph_results()

        tool = ZepSearchTool(client=mock_client, user_id="u1", pinned_params={"scope": "auto"})

        tool._run(query="hello", reranker="node_distance")

        call_kwargs = mock_client.graph.search.call_args.kwargs
        assert call_kwargs["scope"] == "auto"
        assert "reranker" not in call_kwargs

    def test_non_auto_scope_keeps_reranker(self):
        from zep_cloud.client import Zep

        mock_client = MagicMock(spec=Zep)
        mock_client.graph = MagicMock()
        mock_client.graph.search.return_value = _make_mock_graph_results()

        tool = ZepSearchTool(client=mock_client, user_id="u1")

        tool._run(query="hello", scope="edges", reranker="node_distance")

        call_kwargs = mock_client.graph.search.call_args.kwargs
        assert call_kwargs["reranker"] == "node_distance"


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
