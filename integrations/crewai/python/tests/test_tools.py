"""
Tests for Zep CrewAI Tools.

``ZepSearchTool``/``create_search_tool`` follow the pin-or-expose pattern:
every graph search parameter (``scope``, ``reranker``, ``limit``,
``mmr_lambda``, ``center_node_uuid``) is exposed in the tool's dynamically
built ``args_schema`` by default and can be pinned or hidden at construction
time. Zep v4 addresses a graph by UUID and gives one search method for each
scope, so ``scope`` selects the SDK method that the tool calls.
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

GRAPH_UUID = "33333333-3333-3333-3333-333333333333"


def _make_mock_client(**pagers):
    """A Zep mock whose search methods return the given items."""
    from zep_cloud.client import Zep

    client = MagicMock(spec=Zep)
    client.graph = MagicMock()
    client.graph.episode = MagicMock()
    for method in (
        "search_edges",
        "search_nodes",
        "search_episodes",
        "search_observations",
        "search_thread_summaries",
    ):
        getattr(client.graph, method).return_value = iter(pagers.get(method, []))
    client.graph.get_context.return_value = MagicMock(context=None)
    return client


class TestZepSearchTool:
    """Test suite for ZepSearchTool."""

    def test_initialization_with_graph_uuid(self):
        """Test initialization with graph_uuid."""
        mock_client = _make_mock_client()
        tool = ZepSearchTool(client=mock_client, graph_uuid=GRAPH_UUID)

        assert tool.client is mock_client
        assert tool.graph_uuid == GRAPH_UUID
        assert GRAPH_UUID in tool.description

    def test_initialization_requires_graph_uuid(self):
        """Test that graph_uuid is required."""
        mock_client = _make_mock_client()

        with pytest.raises(ValueError, match="graph_uuid must be provided"):
            ZepSearchTool(client=mock_client, graph_uuid="")

    def test_search_graph_edges(self):
        """Test searching graph for edges."""
        from zep_cloud.types import Edge

        mock_edge = Edge(fact="Python is great for AI")
        mock_client = _make_mock_client(search_edges=[mock_edge])

        tool = ZepSearchTool(client=mock_client, graph_uuid=GRAPH_UUID)

        # Run search (direct _run call: params not supplied are omitted --
        # CrewAI's run() fills args_schema defaults before reaching _run)
        result = tool._run(query="Python", limit=5, scope="edges")

        # Verify the edge search method was called correctly
        mock_client.graph.search_edges.assert_called_once_with(GRAPH_UUID, query="Python", limit=5)

        # Check result formatting
        assert "Python is great for AI" in result

    def test_search_nodes(self):
        """Test searching a graph for nodes."""
        from zep_cloud.types import Node

        mock_node = Node(name="UserPreference", summary="User's programming preferences")
        mock_client = _make_mock_client(search_nodes=[mock_node])

        tool = ZepSearchTool(client=mock_client, graph_uuid=GRAPH_UUID)

        result = tool._run(query="preferences", limit=3, scope="nodes")

        mock_client.graph.search_nodes.assert_called_once_with(
            GRAPH_UUID, query="preferences", limit=3
        )

        assert "UserPreference" in result

    def test_search_auto_scope_returns_context(self):
        """Scope 'auto' calls graph.get_context and returns its Context Block."""
        mock_client = _make_mock_client()
        mock_client.graph.get_context.return_value = MagicMock(context="Composed context")

        tool = ZepSearchTool(client=mock_client, graph_uuid=GRAPH_UUID)

        result = tool._run(query="Python", scope="auto")

        mock_client.graph.get_context.assert_called_once_with(GRAPH_UUID, query="Python")
        assert result == "Composed context"

    def test_search_no_results(self):
        """Test search with no results."""
        mock_client = _make_mock_client()

        tool = ZepSearchTool(client=mock_client, graph_uuid=GRAPH_UUID)

        # Run search
        result = tool._run(query="nonexistent", limit=5)

        # Should return no results message
        assert "No results found." in result

    def test_search_empty_query_is_error(self):
        """An empty query must return an error string without calling Zep."""
        mock_client = _make_mock_client()

        tool = ZepSearchTool(client=mock_client, graph_uuid=GRAPH_UUID)

        result = tool._run(query="   ")

        assert "Error" in result
        mock_client.graph.search_edges.assert_not_called()

    def test_search_error_handling(self):
        """Test error handling during search."""
        mock_client = _make_mock_client()
        mock_client.graph.search_edges.side_effect = Exception("API error")

        tool = ZepSearchTool(client=mock_client, graph_uuid=GRAPH_UUID)

        # Run search
        result = tool._run(query="test", limit=5)

        # Should return error message
        assert "Error searching Zep memory: API error" in result


class TestZepSearchToolPinOrExpose:
    """Pin-or-expose contract tests."""

    def test_search_tool_exposes_params_by_default(self):
        """Every graph search parameter is exposed in args_schema by default."""
        mock_client = _make_mock_client()
        tool = ZepSearchTool(client=mock_client, graph_uuid=GRAPH_UUID)

        fields = tool.args_schema.model_fields
        for param_name in ("query", "scope", "reranker", "limit", "mmr_lambda", "center_node_uuid"):
            assert param_name in fields, f"{param_name} should be exposed by default"

    def test_search_tool_six_scopes(self):
        """The scope field's Literal type carries all six documented values."""
        mock_client = _make_mock_client()
        tool = ZepSearchTool(client=mock_client, graph_uuid=GRAPH_UUID)

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
        mock_client = _make_mock_client()

        tool = ZepSearchTool(
            client=mock_client,
            graph_uuid=GRAPH_UUID,
            pinned_params={"scope": "nodes", "limit": 3},
        )

        assert "scope" not in tool.args_schema.model_fields
        assert "limit" not in tool.args_schema.model_fields

        tool._run(query="hello")

        mock_client.graph.search_nodes.assert_called_once()
        call_kwargs = mock_client.graph.search_nodes.call_args.kwargs
        assert call_kwargs["limit"] == 3

    def test_search_tool_hidden_params_omitted_from_sdk_call(self):
        """A hidden (not pinned) param disappears from args_schema and is
        never sent -- Zep's own default applies."""
        mock_client = _make_mock_client()

        tool = ZepSearchTool(client=mock_client, graph_uuid=GRAPH_UUID, hidden_params={"reranker"})

        assert "reranker" not in tool.args_schema.model_fields

        tool._run(query="hello")

        call_kwargs = mock_client.graph.search_edges.call_args.kwargs
        assert "reranker" not in call_kwargs

    def test_search_tool_query_only_omits_unset_none_default_params(self):
        """mmr_lambda / center_node_uuid default to None; when unset by the
        caller they must be OMITTED from the search call, never sent as an
        explicit None."""
        mock_client = _make_mock_client()

        tool = ZepSearchTool(client=mock_client, graph_uuid=GRAPH_UUID)

        tool._run(query="hello")

        call_kwargs = mock_client.graph.search_edges.call_args.kwargs
        assert "mmr_lambda" not in call_kwargs
        assert "center_node_uuid" not in call_kwargs

    def test_search_tool_legacy_args_pin(self):
        """The legacy scope=/reranker=/limit= constructor args still pin
        (and thus hide) their parameter."""
        mock_client = _make_mock_client()

        tool = ZepSearchTool(client=mock_client, graph_uuid=GRAPH_UUID, scope="nodes", limit=7)

        assert "scope" not in tool.args_schema.model_fields
        assert "limit" not in tool.args_schema.model_fields

        tool._run(query="hello")

        mock_client.graph.search_nodes.assert_called_once()
        assert mock_client.graph.search_nodes.call_args.kwargs["limit"] == 7

    def test_search_tool_unknown_pinned_param_raises(self):
        mock_client = _make_mock_client()
        with pytest.raises(ValueError, match="Unknown pinned"):
            ZepSearchTool(client=mock_client, graph_uuid=GRAPH_UUID, pinned_params={"bogus": "x"})

    def test_search_tool_unknown_hidden_param_raises(self):
        mock_client = _make_mock_client()
        with pytest.raises(ValueError, match="Unknown hidden"):
            ZepSearchTool(client=mock_client, graph_uuid=GRAPH_UUID, hidden_params={"bogus"})

    def test_search_tool_constructor_only_search_filters(self):
        """search_filters is constructor-only and is sent as the v4 'filters'."""
        mock_client = _make_mock_client()

        tool = ZepSearchTool(
            client=mock_client, graph_uuid=GRAPH_UUID, search_filters={"node_labels": ["Person"]}
        )
        assert "search_filters" not in tool.args_schema.model_fields

        tool._run(query="hello")

        call_kwargs = mock_client.graph.search_edges.call_args.kwargs
        assert call_kwargs["filters"] == {"node_labels": ["Person"]}

    def test_search_tool_bfs_origin_node_uuids_constructor_only(self):
        mock_client = _make_mock_client()

        tool = ZepSearchTool(
            client=mock_client, graph_uuid=GRAPH_UUID, bfs_origin_node_uuids=["uuid-1"]
        )
        assert "bfs_origin_node_uuids" not in tool.args_schema.model_fields

        tool._run(query="hello")

        call_kwargs = mock_client.graph.search_edges.call_args.kwargs
        assert call_kwargs["bfs_origin_node_uuids"] == ["uuid-1"]

    def test_search_tool_query_truncated_to_400_chars(self):
        mock_client = _make_mock_client()

        tool = ZepSearchTool(client=mock_client, graph_uuid=GRAPH_UUID)
        tool._run(query="x" * 500)

        call_kwargs = mock_client.graph.search_edges.call_args.kwargs
        assert len(call_kwargs["query"]) == 400


class TestSearchLimitClamping:
    """A limit above Zep's ceiling (or below 1) is clamped, never rejected."""

    def test_pinned_limit_clamped_with_warning(self, caplog):
        """A pinned limit above MAX_SEARCH_LIMIT is clamped at construction
        with a warning, and the clamped value is sent."""
        mock_client = _make_mock_client()

        with caplog.at_level(logging.WARNING, logger="zep_crewai.tools"):
            tool = ZepSearchTool(
                client=mock_client, graph_uuid=GRAPH_UUID, pinned_params={"limit": 100}
            )

        assert any("clamping" in record.message for record in caplog.records)

        tool._run(query="hello")

        call_kwargs = mock_client.graph.search_edges.call_args.kwargs
        assert call_kwargs["limit"] == MAX_SEARCH_LIMIT

    def test_pinned_limit_below_one_clamped_to_one(self):
        mock_client = _make_mock_client()

        tool = ZepSearchTool(client=mock_client, graph_uuid=GRAPH_UUID, pinned_params={"limit": 0})

        tool._run(query="hello")

        call_kwargs = mock_client.graph.search_edges.call_args.kwargs
        assert call_kwargs["limit"] == 1

    def test_legacy_limit_arg_clamped(self):
        """The legacy limit= constructor arg pins and is clamped the same way."""
        mock_client = _make_mock_client()

        tool = ZepSearchTool(client=mock_client, graph_uuid=GRAPH_UUID, limit=100)

        tool._run(query="hello")

        call_kwargs = mock_client.graph.search_edges.call_args.kwargs
        assert call_kwargs["limit"] == MAX_SEARCH_LIMIT

    def test_model_provided_limit_clamped_at_call_time(self):
        """A model-provided limit above the ceiling is clamped to 50, not
        rejected -- the tool never 400s on limit."""
        mock_client = _make_mock_client()

        tool = ZepSearchTool(client=mock_client, graph_uuid=GRAPH_UUID)

        tool._run(query="hello", limit=200)

        call_kwargs = mock_client.graph.search_edges.call_args.kwargs
        assert call_kwargs["limit"] == MAX_SEARCH_LIMIT

    def test_model_provided_limit_below_one_clamped_to_one(self):
        mock_client = _make_mock_client()

        tool = ZepSearchTool(client=mock_client, graph_uuid=GRAPH_UUID)

        tool._run(query="hello", limit=-3)

        call_kwargs = mock_client.graph.search_edges.call_args.kwargs
        assert call_kwargs["limit"] == 1

    def test_valid_limit_passes_through_unchanged(self):
        mock_client = _make_mock_client()

        tool = ZepSearchTool(client=mock_client, graph_uuid=GRAPH_UUID)

        tool._run(query="hello", limit=25)

        call_kwargs = mock_client.graph.search_edges.call_args.kwargs
        assert call_kwargs["limit"] == 25


class TestAutoScopeReranker:
    """scope='auto' ignores reranker and rejects node_distance /
    episode_mentions outright -- the reranker must be dropped, not sent."""

    def test_pinned_auto_scope_drops_incompatible_reranker(self, caplog):
        mock_client = _make_mock_client()

        with caplog.at_level(logging.WARNING, logger="zep_crewai.tools"):
            tool = ZepSearchTool(
                client=mock_client,
                graph_uuid=GRAPH_UUID,
                pinned_params={"scope": "auto", "reranker": "node_distance"},
            )

        assert any("reranker" in record.message for record in caplog.records)

        tool._run(query="hello")

        call_kwargs = mock_client.graph.get_context.call_args.kwargs
        assert "reranker" not in call_kwargs

    def test_model_provided_auto_scope_drops_incompatible_reranker(self):
        mock_client = _make_mock_client()

        tool = ZepSearchTool(client=mock_client, graph_uuid=GRAPH_UUID)

        tool._run(query="hello", scope="auto", reranker="episode_mentions")

        mock_client.graph.get_context.assert_called_once_with(GRAPH_UUID, query="hello")

    def test_pinned_auto_scope_with_model_reranker_dropped(self):
        """scope pinned to 'auto' + reranker left model-exposed: a
        model-provided incompatible reranker is still dropped at call time."""
        mock_client = _make_mock_client()

        tool = ZepSearchTool(
            client=mock_client, graph_uuid=GRAPH_UUID, pinned_params={"scope": "auto"}
        )

        tool._run(query="hello", reranker="node_distance")

        mock_client.graph.get_context.assert_called_once_with(GRAPH_UUID, query="hello")

    def test_non_auto_scope_keeps_reranker(self):
        mock_client = _make_mock_client()

        tool = ZepSearchTool(client=mock_client, graph_uuid=GRAPH_UUID)

        tool._run(query="hello", scope="edges", reranker="node_distance")

        call_kwargs = mock_client.graph.search_edges.call_args.kwargs
        assert call_kwargs["reranker"] == "node_distance"


class TestZepAddDataTool:
    """Test suite for ZepAddDataTool."""

    def test_initialization_with_graph_uuid(self):
        """Test initialization with graph_uuid."""
        mock_client = _make_mock_client()
        tool = ZepAddDataTool(client=mock_client, graph_uuid=GRAPH_UUID)

        assert tool.client is mock_client
        assert tool.graph_uuid == GRAPH_UUID
        assert GRAPH_UUID in tool.description

    def test_initialization_requires_graph_uuid(self):
        mock_client = _make_mock_client()

        with pytest.raises(ValueError, match="graph_uuid must be provided"):
            ZepAddDataTool(client=mock_client, graph_uuid="")

    def test_add_text_to_graph(self):
        """Test adding text data to graph."""
        mock_client = _make_mock_client()

        tool = ZepAddDataTool(client=mock_client, graph_uuid=GRAPH_UUID)

        # Add text data
        result = tool._run("Python is versatile", data_type="text")

        # Verify episode.add was called correctly
        mock_client.graph.episode.add.assert_called_once_with(
            GRAPH_UUID, type="text", data="Python is versatile"
        )

        # Check success message
        assert f"Successfully added text data to graph '{GRAPH_UUID}'" in result

    def test_add_json_to_graph(self):
        """Test adding JSON data to a graph."""
        mock_client = _make_mock_client()

        tool = ZepAddDataTool(client=mock_client, graph_uuid=GRAPH_UUID)

        # Add JSON data
        json_data = '{"preference": "dark_mode", "language": "en"}'
        result = tool._run(json_data, data_type="json")

        mock_client.graph.episode.add.assert_called_once_with(
            GRAPH_UUID, type="json", data=json_data
        )

        assert f"Successfully added json data to graph '{GRAPH_UUID}'" in result

    def test_add_defaults_to_text(self):
        """Test that data_type defaults to text."""
        mock_client = _make_mock_client()

        tool = ZepAddDataTool(client=mock_client, graph_uuid=GRAPH_UUID)

        # Add with invalid type (should default to text)
        tool._run("Some data", data_type="invalid")

        # Should use text type
        mock_client.graph.episode.add.assert_called_once_with(
            GRAPH_UUID, type="text", data="Some data"
        )

    def test_add_error_handling(self):
        """Test error handling during add."""
        mock_client = _make_mock_client()
        mock_client.graph.episode.add.side_effect = Exception("API error")

        tool = ZepAddDataTool(client=mock_client, graph_uuid=GRAPH_UUID)

        # Try to add data
        result = tool._run("Test data", data_type="text")

        # Should return error message
        assert "Error adding data to Zep: API error" in result


class TestToolFactoryFunctions:
    """Test the factory functions for creating tools."""

    def test_create_search_tool(self):
        """Test creating a search tool with graph_uuid."""
        mock_client = _make_mock_client()
        tool = create_search_tool(mock_client, graph_uuid=GRAPH_UUID)

        assert isinstance(tool, ZepSearchTool)
        assert tool.graph_uuid == GRAPH_UUID

    def test_create_add_data_tool(self):
        """Test creating an add data tool with graph_uuid."""
        mock_client = _make_mock_client()
        tool = create_add_data_tool(mock_client, graph_uuid=GRAPH_UUID)

        assert isinstance(tool, ZepAddDataTool)
        assert tool.graph_uuid == GRAPH_UUID

    def test_create_tools_require_graph_uuid(self):
        """Test that factory functions require a graph_uuid."""
        mock_client = _make_mock_client()

        with pytest.raises(ValueError):
            create_search_tool(mock_client, graph_uuid="")

        with pytest.raises(ValueError):
            create_add_data_tool(mock_client, graph_uuid="")


class TestToolIntegration:
    """Test tools integration with CrewAI patterns."""

    def test_tool_has_correct_attributes(self):
        """Test that tools have attributes expected by CrewAI."""
        mock_client = _make_mock_client()
        search_tool = ZepSearchTool(client=mock_client, graph_uuid=GRAPH_UUID)
        add_tool = ZepAddDataTool(client=mock_client, graph_uuid=GRAPH_UUID)

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
        mock_client = _make_mock_client()
        search_tool = ZepSearchTool(client=mock_client, graph_uuid=GRAPH_UUID)
        add_tool = ZepAddDataTool(client=mock_client, graph_uuid=GRAPH_UUID)

        # Check search tool schema
        search_schema = search_tool.args_schema
        assert "query" in search_schema.model_fields
        assert "limit" in search_schema.model_fields
        assert "scope" in search_schema.model_fields

        # Check add tool schema
        add_schema = add_tool.args_schema
        assert "data" in add_schema.model_fields
        assert "data_type" in add_schema.model_fields
