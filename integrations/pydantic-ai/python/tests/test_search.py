"""
Tests for the ``create_zep_search_tool`` factory with a mocked Zep client.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from zep_pydantic_ai import ZepDeps, create_zep_search_tool


def _make_deps(client: MagicMock) -> ZepDeps:
    return ZepDeps(client=client, user_id="user-1", thread_id="thread-1")


def _make_ctx(deps: ZepDeps) -> MagicMock:
    ctx = MagicMock()
    ctx.deps = deps
    return ctx


def _make_result(edges=None, nodes=None, episodes=None, context=None) -> MagicMock:
    result = MagicMock()
    result.edges = edges
    result.nodes = nodes
    result.episodes = episodes
    result.context = context
    return result


def _edge(fact: str) -> MagicMock:
    e = MagicMock()
    e.fact = fact
    return e


def _node(name: str, summary: str | None) -> MagicMock:
    n = MagicMock()
    n.name = name
    n.summary = summary
    return n


def _episode(content: str) -> MagicMock:
    ep = MagicMock()
    ep.content = content
    return ep


class TestSearchTargeting:
    @pytest.mark.asyncio
    async def test_searches_user_graph_by_default(self) -> None:
        client = MagicMock()
        client.graph.search = AsyncMock(return_value=_make_result(edges=[_edge("fact")]))
        tool = create_zep_search_tool()
        ctx = _make_ctx(_make_deps(client))

        await tool(ctx, "what do you know")

        kwargs = client.graph.search.call_args.kwargs
        assert kwargs["user_id"] == "user-1"
        assert "graph_id" not in kwargs

    @pytest.mark.asyncio
    async def test_searches_graph_id_when_set(self) -> None:
        client = MagicMock()
        client.graph.search = AsyncMock(return_value=_make_result(edges=[]))
        tool = create_zep_search_tool(graph_id="docs-graph")
        ctx = _make_ctx(_make_deps(client))

        await tool(ctx, "query")

        kwargs = client.graph.search.call_args.kwargs
        assert kwargs["graph_id"] == "docs-graph"
        assert "user_id" not in kwargs

    @pytest.mark.asyncio
    async def test_default_params(self) -> None:
        client = MagicMock()
        client.graph.search = AsyncMock(return_value=_make_result(edges=[]))
        tool = create_zep_search_tool()
        ctx = _make_ctx(_make_deps(client))

        await tool(ctx, "query")

        kwargs = client.graph.search.call_args.kwargs
        assert kwargs["scope"] == "edges"
        assert kwargs["reranker"] == "rrf"
        assert kwargs["limit"] == 10

    @pytest.mark.asyncio
    async def test_pinned_params_used(self) -> None:
        client = MagicMock()
        client.graph.search = AsyncMock(return_value=_make_result(nodes=[]))
        tool = create_zep_search_tool(scope="nodes", reranker="cross_encoder", limit=3)
        ctx = _make_ctx(_make_deps(client))

        await tool(ctx, "query")

        kwargs = client.graph.search.call_args.kwargs
        assert kwargs["scope"] == "nodes"
        assert kwargs["reranker"] == "cross_encoder"
        assert kwargs["limit"] == 3

    @pytest.mark.asyncio
    async def test_query_truncated_to_400(self) -> None:
        client = MagicMock()
        client.graph.search = AsyncMock(return_value=_make_result(edges=[]))
        tool = create_zep_search_tool()
        ctx = _make_ctx(_make_deps(client))

        await tool(ctx, "x" * 1000)

        kwargs = client.graph.search.call_args.kwargs
        assert len(kwargs["query"]) == 400

    def test_custom_tool_name(self) -> None:
        tool = create_zep_search_tool(name="search_memory")
        assert tool.__name__ == "search_memory"


class TestResultFormatting:
    @pytest.mark.asyncio
    async def test_formats_edges(self) -> None:
        client = MagicMock()
        client.graph.search = AsyncMock(
            return_value=_make_result(edges=[_edge("Alice works at Acme"), _edge("Bob hikes")])
        )
        tool = create_zep_search_tool(scope="edges")
        out = await tool(_make_ctx(_make_deps(client)), "q")
        assert "Alice works at Acme" in out
        assert "Bob hikes" in out

    @pytest.mark.asyncio
    async def test_formats_nodes(self) -> None:
        client = MagicMock()
        client.graph.search = AsyncMock(
            return_value=_make_result(nodes=[_node("Alice", "An engineer")])
        )
        tool = create_zep_search_tool(scope="nodes")
        out = await tool(_make_ctx(_make_deps(client)), "q")
        assert "Alice: An engineer" in out

    @pytest.mark.asyncio
    async def test_formats_episodes(self) -> None:
        client = MagicMock()
        client.graph.search = AsyncMock(
            return_value=_make_result(episodes=[_episode("I work at Acme")])
        )
        tool = create_zep_search_tool(scope="episodes")
        out = await tool(_make_ctx(_make_deps(client)), "q")
        assert "I work at Acme" in out

    @pytest.mark.asyncio
    async def test_auto_scope_returns_context(self) -> None:
        client = MagicMock()
        client.graph.search = AsyncMock(
            return_value=_make_result(context="Assembled context block")
        )
        tool = create_zep_search_tool(scope="auto")
        out = await tool(_make_ctx(_make_deps(client)), "q")
        assert out == "Assembled context block"

    @pytest.mark.asyncio
    async def test_empty_results(self) -> None:
        client = MagicMock()
        client.graph.search = AsyncMock(return_value=_make_result(edges=[]))
        tool = create_zep_search_tool()
        out = await tool(_make_ctx(_make_deps(client)), "q")
        assert out == "No results found."


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_search_failure_returns_error_string(self) -> None:
        client = MagicMock()
        client.graph.search = AsyncMock(side_effect=RuntimeError("boom"))
        tool = create_zep_search_tool()
        out = await tool(_make_ctx(_make_deps(client)), "q")
        assert "failed" in out.lower()
