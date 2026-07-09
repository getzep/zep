"""
Tests for ``create_graph_search_tool`` -- a LiveKit ``RawFunctionTool`` built
from a hand-crafted JSON schema (:data:`zep_livekit.tools._SEARCH_PARAM_SPECS`)
via ``function_tool(raw_schema=...)``.

Raw-schema handler convention (confirmed against ``livekit.agents.llm.mcp``):
the wrapped function receives a single ``raw_arguments: dict[str, Any]``
argument. These tests call the tool as ``await tool(raw_arguments={...})``,
matching that convention (``RawFunctionTool.__call__`` forwards positional /
keyword args straight to the wrapped function).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from livekit.agents.llm import RawFunctionTool

from zep_livekit.exceptions import AgentConfigurationError
from zep_livekit.tools import MAX_SEARCH_LIMIT, create_graph_search_tool


def _make_result(
    edges=None,
    nodes=None,
    episodes=None,
    context=None,
    observations=None,
    thread_summaries=None,
) -> MagicMock:
    result = MagicMock()
    result.edges = edges
    result.nodes = nodes
    result.episodes = episodes
    result.context = context
    result.observations = observations
    result.thread_summaries = thread_summaries
    return result


def _edge(fact: str) -> MagicMock:
    e = MagicMock()
    e.fact = fact
    return e


async def _call(tool: RawFunctionTool, **raw_arguments: object) -> str:
    return await tool(raw_arguments=raw_arguments)  # type: ignore[no-any-return]


class TestRequiresTarget:
    def test_neither_graph_id_nor_user_id_raises(self) -> None:
        client = MagicMock()
        with pytest.raises(AgentConfigurationError):
            create_graph_search_tool(client)

    def test_both_graph_id_and_user_id_raises(self) -> None:
        client = MagicMock()
        with pytest.raises(AgentConfigurationError):
            create_graph_search_tool(client, graph_id="g1", user_id="u1")

    def test_graph_id_only_is_valid(self) -> None:
        client = MagicMock()
        tool = create_graph_search_tool(client, graph_id="g1")
        assert isinstance(tool, RawFunctionTool)

    def test_user_id_only_is_valid(self) -> None:
        client = MagicMock()
        tool = create_graph_search_tool(client, user_id="u1")
        assert isinstance(tool, RawFunctionTool)


class TestSchemaExposure:
    def test_search_tool_exposes_params_by_default(self) -> None:
        client = MagicMock()
        tool = create_graph_search_tool(client, user_id="u1")
        schema = tool.info.raw_schema["parameters"]
        properties = schema["properties"]

        assert "query" in properties
        assert "scope" in properties
        assert set(properties["scope"]["enum"]) == {
            "edges",
            "nodes",
            "episodes",
            "observations",
            "thread_summaries",
            "auto",
        }
        assert "reranker" in properties
        assert set(properties["reranker"]["enum"]) == {
            "rrf",
            "mmr",
            "node_distance",
            "episode_mentions",
            "cross_encoder",
        }
        assert "limit" in properties
        assert "mmr_lambda" in properties
        assert "center_node_uuid" in properties
        assert schema["required"] == ["query"]

    def test_limit_schema_advertises_zep_bounds(self) -> None:
        """The model-facing schema must carry Zep's limit bounds so
        well-behaved models self-limit instead of sending e.g. 200."""
        client = MagicMock()
        tool = create_graph_search_tool(client, user_id="u1")
        limit_prop = tool.info.raw_schema["parameters"]["properties"]["limit"]

        assert limit_prop["minimum"] == 1
        assert limit_prop["maximum"] == MAX_SEARCH_LIMIT

    def test_custom_name_and_description(self) -> None:
        client = MagicMock()
        tool = create_graph_search_tool(
            client, user_id="u1", name="search_memory", description="Custom desc"
        )
        assert tool.info.name == "search_memory"
        assert tool.info.raw_schema["description"] == "Custom desc"


class TestPinnedAndHiddenParams:
    @pytest.mark.asyncio
    async def test_search_tool_pinned_params_hidden_and_sent(self) -> None:
        client = MagicMock()
        client.graph.search = AsyncMock(return_value=_make_result(nodes=[]))
        tool = create_graph_search_tool(
            client, user_id="u1", pinned_params={"scope": "nodes", "limit": 5}
        )

        properties = tool.info.raw_schema["parameters"]["properties"]
        assert "scope" not in properties
        assert "limit" not in properties
        assert "reranker" in properties

        await _call(tool, query="query")

        kwargs = client.graph.search.call_args.kwargs
        assert kwargs["scope"] == "nodes"
        assert kwargs["limit"] == 5

    @pytest.mark.asyncio
    async def test_search_tool_hidden_params_omitted_from_sdk_call(self) -> None:
        client = MagicMock()
        client.graph.search = AsyncMock(return_value=_make_result(edges=[]))
        tool = create_graph_search_tool(client, user_id="u1", hidden_params={"mmr_lambda"})

        properties = tool.info.raw_schema["parameters"]["properties"]
        assert "mmr_lambda" not in properties

        await _call(tool, query="query")

        assert "mmr_lambda" not in client.graph.search.call_args.kwargs

    def test_unknown_pinned_param_raises(self) -> None:
        client = MagicMock()
        with pytest.raises(AgentConfigurationError):
            create_graph_search_tool(client, user_id="u1", pinned_params={"bogus": 1})

    def test_unknown_hidden_param_raises(self) -> None:
        client = MagicMock()
        with pytest.raises(AgentConfigurationError):
            create_graph_search_tool(client, user_id="u1", hidden_params={"bogus"})

    def test_bfs_and_filters_constructor_only(self) -> None:
        client = MagicMock()
        tool = create_graph_search_tool(
            client,
            user_id="u1",
            search_filters={"node_labels": ["Person"]},
            bfs_origin_node_uuids=["uuid-1"],
        )
        properties = tool.info.raw_schema["parameters"]["properties"]
        assert "search_filters" not in properties
        assert "bfs_origin_node_uuids" not in properties

    @pytest.mark.asyncio
    async def test_bfs_and_filters_sent_to_sdk(self) -> None:
        client = MagicMock()
        client.graph.search = AsyncMock(return_value=_make_result(edges=[]))
        tool = create_graph_search_tool(
            client,
            user_id="u1",
            search_filters={"node_labels": ["Person"]},
            bfs_origin_node_uuids=["uuid-1"],
        )

        await _call(tool, query="query")

        kwargs = client.graph.search.call_args.kwargs
        assert kwargs["search_filters"] == {"node_labels": ["Person"]}
        assert kwargs["bfs_origin_node_uuids"] == ["uuid-1"]


class TestLimitClamping:
    @pytest.mark.asyncio
    async def test_model_provided_limit_clamped_to_ceiling(self) -> None:
        """A model-sent limit above Zep's ceiling is clamped, not forwarded
        verbatim (which would 400 and degrade the tool to an error string)."""
        client = MagicMock()
        client.graph.search = AsyncMock(return_value=_make_result(edges=[]))
        tool = create_graph_search_tool(client, user_id="u1")

        await _call(tool, query="query", limit=200)

        assert client.graph.search.call_args.kwargs["limit"] == MAX_SEARCH_LIMIT

    @pytest.mark.asyncio
    async def test_model_provided_limit_clamped_to_floor(self) -> None:
        client = MagicMock()
        client.graph.search = AsyncMock(return_value=_make_result(edges=[]))
        tool = create_graph_search_tool(client, user_id="u1")

        await _call(tool, query="query", limit=0)

        assert client.graph.search.call_args.kwargs["limit"] == 1

    @pytest.mark.asyncio
    async def test_model_provided_limit_in_range_forwarded_unchanged(self) -> None:
        client = MagicMock()
        client.graph.search = AsyncMock(return_value=_make_result(edges=[]))
        tool = create_graph_search_tool(client, user_id="u1")

        await _call(tool, query="query", limit=25)

        assert client.graph.search.call_args.kwargs["limit"] == 25

    @pytest.mark.asyncio
    async def test_pinned_limit_clamped_at_construction(self) -> None:
        client = MagicMock()
        client.graph.search = AsyncMock(return_value=_make_result(edges=[]))
        tool = create_graph_search_tool(client, user_id="u1", pinned_params={"limit": 200})

        await _call(tool, query="query")

        assert client.graph.search.call_args.kwargs["limit"] == MAX_SEARCH_LIMIT


class TestQueryHandling:
    @pytest.mark.asyncio
    async def test_search_tool_query_only_omits_unset_none_default_params(self) -> None:
        """When the model sends only 'query', mmr_lambda/center_node_uuid
        (no default) must be omitted from the SDK call entirely."""
        client = MagicMock()
        client.graph.search = AsyncMock(return_value=_make_result(edges=[]))
        tool = create_graph_search_tool(client, user_id="u1")

        await _call(tool, query="find facts")

        kwargs = client.graph.search.call_args.kwargs
        assert kwargs["query"] == "find facts"
        assert kwargs["scope"] == "edges"
        assert kwargs["reranker"] == "rrf"
        assert kwargs["limit"] == 10
        assert "mmr_lambda" not in kwargs
        assert "center_node_uuid" not in kwargs
        assert kwargs["user_id"] == "u1"
        assert "graph_id" not in kwargs

    @pytest.mark.asyncio
    async def test_query_truncated_to_400(self) -> None:
        client = MagicMock()
        client.graph.search = AsyncMock(return_value=_make_result(edges=[]))
        tool = create_graph_search_tool(client, user_id="u1")

        await _call(tool, query="x" * 1000)

        assert len(client.graph.search.call_args.kwargs["query"]) == 400

    @pytest.mark.asyncio
    async def test_no_query_returns_error_string(self) -> None:
        client = MagicMock()
        tool = create_graph_search_tool(client, user_id="u1")

        out = await _call(tool, query="")

        assert "error" in out.lower()

    @pytest.mark.asyncio
    async def test_graph_id_target_used(self) -> None:
        client = MagicMock()
        client.graph.search = AsyncMock(return_value=_make_result(edges=[]))
        tool = create_graph_search_tool(client, graph_id="docs-graph")

        await _call(tool, query="query")

        kwargs = client.graph.search.call_args.kwargs
        assert kwargs["graph_id"] == "docs-graph"
        assert "user_id" not in kwargs


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_search_tool_errors_return_string(self) -> None:
        client = MagicMock()
        client.graph.search = AsyncMock(side_effect=RuntimeError("boom"))
        tool = create_graph_search_tool(client, user_id="u1")

        out = await _call(tool, query="query")

        assert isinstance(out, str)
        assert "failed" in out.lower()


class TestAutoScopeReranker:
    @pytest.mark.asyncio
    async def test_auto_scope_drops_incompatible_reranker(self) -> None:
        client = MagicMock()
        client.graph.search = AsyncMock(return_value=_make_result(context="ctx"))
        tool = create_graph_search_tool(
            client, user_id="u1", pinned_params={"scope": "auto", "reranker": "node_distance"}
        )

        await _call(tool, query="query")

        kwargs = client.graph.search.call_args.kwargs
        assert kwargs["scope"] == "auto"
        assert "reranker" not in kwargs


class TestResultFormatting:
    @pytest.mark.asyncio
    async def test_formats_edges(self) -> None:
        client = MagicMock()
        client.graph.search = AsyncMock(
            return_value=_make_result(edges=[_edge("Alice works at Acme")])
        )
        tool = create_graph_search_tool(client, user_id="u1", pinned_params={"scope": "edges"})

        out = await _call(tool, query="q")

        assert "Alice works at Acme" in out

    @pytest.mark.asyncio
    async def test_empty_results(self) -> None:
        client = MagicMock()
        client.graph.search = AsyncMock(return_value=_make_result(edges=[]))
        tool = create_graph_search_tool(client, user_id="u1")

        out = await _call(tool, query="q")

        assert out == "No results found."
