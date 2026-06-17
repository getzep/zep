"""
Tests for the prebuilt graph-search tools (zep_langgraph.tools).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.tools import StructuredTool

from zep_langgraph.tools import (
    _format_results,
    create_graph_search_tool,
    create_graph_search_tool_sync,
)


def _make_async_client() -> MagicMock:
    client = MagicMock()
    client.graph = MagicMock()
    client.graph.search = AsyncMock()
    return client


def _make_sync_client() -> MagicMock:
    client = MagicMock()
    client.graph = MagicMock()
    client.graph.search = MagicMock()
    return client


def _edge(fact: str, score: float | None = None) -> MagicMock:
    e = MagicMock()
    e.fact = fact
    e.score = score
    e.uuid_ = "edge-uuid"
    return e


def _node(name: str, summary: str | None) -> MagicMock:
    n = MagicMock()
    n.name = name
    n.summary = summary
    n.uuid_ = "node-uuid"
    n.score = None
    return n


def _episode(content: str) -> MagicMock:
    ep = MagicMock()
    ep.content = content
    ep.uuid_ = "ep-uuid"
    ep.score = None
    return ep


def _result(edges=None, nodes=None, episodes=None, context=None) -> MagicMock:
    r = MagicMock()
    r.edges = edges
    r.nodes = nodes
    r.episodes = episodes
    r.context = context
    return r


class TestTargetValidation:
    def test_requires_exactly_one_target(self) -> None:
        with pytest.raises(ValueError, match="exactly one"):
            create_graph_search_tool(_make_async_client())

    def test_rejects_both_targets(self) -> None:
        with pytest.raises(ValueError, match="exactly one"):
            create_graph_search_tool(_make_async_client(), user_id="u", graph_id="g")

    def test_user_id_target_ok(self) -> None:
        tool = create_graph_search_tool(_make_async_client(), user_id="u")
        assert isinstance(tool, StructuredTool)

    def test_graph_id_target_ok(self) -> None:
        tool = create_graph_search_tool(_make_async_client(), graph_id="g")
        assert isinstance(tool, StructuredTool)


class TestToolMetadata:
    def test_default_name_and_description(self) -> None:
        tool = create_graph_search_tool(_make_async_client(), user_id="u")
        assert tool.name == "search_memory"
        assert "memory" in tool.description.lower()

    def test_custom_name_and_description(self) -> None:
        tool = create_graph_search_tool(
            _make_async_client(), graph_id="docs", name="search_docs", description="Find docs."
        )
        assert tool.name == "search_docs"
        assert tool.description == "Find docs."

    def test_is_async_tool(self) -> None:
        tool = create_graph_search_tool(_make_async_client(), user_id="u")
        assert tool.coroutine is not None


class TestAsyncToolExecution:
    @pytest.mark.asyncio
    async def test_searches_user_graph(self) -> None:
        client = _make_async_client()
        client.graph.search.return_value = _result(edges=[_edge("Alice likes blue")])
        tool = create_graph_search_tool(client, user_id="user-42")

        out = await tool.ainvoke({"query": "preferences"})

        call = client.graph.search.call_args.kwargs
        assert call["user_id"] == "user-42"
        assert "graph_id" not in call
        assert call["query"] == "preferences"
        assert call["scope"] == "edges"
        assert call["limit"] == 10
        assert "Alice likes blue" in out

    @pytest.mark.asyncio
    async def test_searches_standalone_graph(self) -> None:
        client = _make_async_client()
        client.graph.search.return_value = _result(edges=[_edge("Policy X applies")])
        tool = create_graph_search_tool(client, graph_id="kb", scope="edges")

        await tool.ainvoke({"query": "policy"})

        call = client.graph.search.call_args.kwargs
        assert call["graph_id"] == "kb"
        assert "user_id" not in call

    @pytest.mark.asyncio
    async def test_pinned_params_applied(self) -> None:
        client = _make_async_client()
        client.graph.search.return_value = _result(nodes=[_node("Alice", "engineer")])
        tool = create_graph_search_tool(
            client, user_id="u", scope="nodes", reranker="cross_encoder", limit=3
        )

        out = await tool.ainvoke({"query": "who"})

        call = client.graph.search.call_args.kwargs
        assert call["scope"] == "nodes"
        assert call["reranker"] == "cross_encoder"
        assert call["limit"] == 3
        assert "Alice" in out

    @pytest.mark.asyncio
    async def test_search_filters_passed(self) -> None:
        client = _make_async_client()
        client.graph.search.return_value = _result(edges=[])
        filters = {"node_labels": ["Person"]}
        tool = create_graph_search_tool(client, user_id="u", search_filters=filters)

        await tool.ainvoke({"query": "x"})

        assert client.graph.search.call_args.kwargs["search_filters"] == filters

    @pytest.mark.asyncio
    async def test_empty_query_short_circuits(self) -> None:
        client = _make_async_client()
        tool = create_graph_search_tool(client, user_id="u")
        out = await tool.ainvoke({"query": "   "})
        assert "Error" in out
        client.graph.search.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_zep_failure_returns_error_string(self) -> None:
        client = _make_async_client()
        client.graph.search.side_effect = RuntimeError("boom")
        tool = create_graph_search_tool(client, user_id="u")
        out = await tool.ainvoke({"query": "x"})
        assert "failed" in out.lower()

    @pytest.mark.asyncio
    async def test_no_results_message(self) -> None:
        client = _make_async_client()
        client.graph.search.return_value = _result(edges=[])
        tool = create_graph_search_tool(client, user_id="u")
        out = await tool.ainvoke({"query": "x"})
        assert out == "No results found."


class TestSyncToolExecution:
    def test_searches_and_formats(self) -> None:
        client = _make_sync_client()
        client.graph.search.return_value = _result(edges=[_edge("fact one")])
        tool = create_graph_search_tool_sync(client, user_id="u")
        out = tool.invoke({"query": "q"})
        assert "fact one" in out

    def test_zep_failure_returns_error(self) -> None:
        client = _make_sync_client()
        client.graph.search.side_effect = RuntimeError("down")
        tool = create_graph_search_tool_sync(client, graph_id="g")
        out = tool.invoke({"query": "q"})
        assert "failed" in out.lower()


class TestFormatResults:
    def test_format_edges(self) -> None:
        out = _format_results(_result(edges=[_edge("A"), _edge("B")]), "edges")
        assert "- A" in out
        assert "- B" in out

    def test_format_nodes_with_summary(self) -> None:
        out = _format_results(_result(nodes=[_node("Alice", "An engineer")]), "nodes")
        assert "Alice: An engineer" in out

    def test_format_nodes_without_summary(self) -> None:
        out = _format_results(_result(nodes=[_node("Alice", None)]), "nodes")
        assert "- Alice" in out

    def test_format_episodes(self) -> None:
        out = _format_results(_result(episodes=[_episode("raw text")]), "episodes")
        assert "raw text" in out

    def test_format_auto_uses_context(self) -> None:
        out = _format_results(_result(context="prebuilt block"), "auto")
        assert out == "prebuilt block"

    def test_empty_returns_no_results(self) -> None:
        assert _format_results(_result(edges=[]), "edges") == "No results found."
