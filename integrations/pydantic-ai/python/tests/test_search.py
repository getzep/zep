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


def _node(name: str, summary: str | None) -> MagicMock:
    n = MagicMock()
    n.name = name
    n.summary = summary
    return n


def _episode(content: str) -> MagicMock:
    ep = MagicMock()
    ep.content = content
    return ep


def _observation(name: str, summary: str | None = None) -> MagicMock:
    obs = MagicMock()
    obs.name = name
    obs.summary = summary
    return obs


def _thread_summary(summary: str | None, name: str = "thread") -> MagicMock:
    ts = MagicMock()
    ts.summary = summary
    ts.name = name
    return ts


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


class TestLimitClamping:
    @pytest.mark.asyncio
    async def test_limit_clamped_to_50(self) -> None:
        client = MagicMock()
        client.graph.search = AsyncMock(return_value=_make_result(edges=[]))
        tool = create_zep_search_tool(limit=1000)
        ctx = _make_ctx(_make_deps(client))

        await tool(ctx, "query")

        kwargs = client.graph.search.call_args.kwargs
        assert kwargs["limit"] == 50

    @pytest.mark.asyncio
    async def test_limit_at_ceiling_unchanged(self) -> None:
        client = MagicMock()
        client.graph.search = AsyncMock(return_value=_make_result(edges=[]))
        tool = create_zep_search_tool(limit=50)
        ctx = _make_ctx(_make_deps(client))

        await tool(ctx, "query")

        assert client.graph.search.call_args.kwargs["limit"] == 50

    @pytest.mark.asyncio
    async def test_limit_floor_is_one(self) -> None:
        client = MagicMock()
        client.graph.search = AsyncMock(return_value=_make_result(edges=[]))
        tool = create_zep_search_tool(limit=0)
        ctx = _make_ctx(_make_deps(client))

        await tool(ctx, "query")

        assert client.graph.search.call_args.kwargs["limit"] == 1


class TestAutoScopeReranker:
    @pytest.mark.asyncio
    async def test_auto_scope_drops_incompatible_reranker(self) -> None:
        """node_distance / episode_mentions are rejected by Zep for auto scope;
        the tool must not pass a reranker at all."""
        client = MagicMock()
        client.graph.search = AsyncMock(return_value=_make_result(context="ctx"))
        tool = create_zep_search_tool(scope="auto", reranker="node_distance")
        ctx = _make_ctx(_make_deps(client))

        await tool(ctx, "query")

        kwargs = client.graph.search.call_args.kwargs
        assert kwargs["scope"] == "auto"
        assert "reranker" not in kwargs

    @pytest.mark.asyncio
    async def test_auto_scope_drops_default_reranker_too(self) -> None:
        """Auto search ignores reranker entirely; even rrf is omitted."""
        client = MagicMock()
        client.graph.search = AsyncMock(return_value=_make_result(context="ctx"))
        tool = create_zep_search_tool(scope="auto")
        ctx = _make_ctx(_make_deps(client))

        await tool(ctx, "query")

        assert "reranker" not in client.graph.search.call_args.kwargs

    @pytest.mark.asyncio
    async def test_non_auto_scope_keeps_reranker(self) -> None:
        client = MagicMock()
        client.graph.search = AsyncMock(return_value=_make_result(edges=[]))
        tool = create_zep_search_tool(scope="edges", reranker="node_distance")
        ctx = _make_ctx(_make_deps(client))

        await tool(ctx, "query")

        assert client.graph.search.call_args.kwargs["reranker"] == "node_distance"


class TestExtendedScopes:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("scope", ["observations", "thread_summaries"])
    async def test_new_scopes_accepted(self, scope: str) -> None:
        client = MagicMock()
        client.graph.search = AsyncMock(return_value=_make_result(edges=[]))
        tool = create_zep_search_tool(scope=scope)  # type: ignore[arg-type]
        ctx = _make_ctx(_make_deps(client))

        await tool(ctx, "query")

        assert client.graph.search.call_args.kwargs["scope"] == scope


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
    async def test_formats_observations(self) -> None:
        client = MagicMock()
        client.graph.search = AsyncMock(
            return_value=_make_result(
                observations=[
                    _observation("Prefers async updates", "Communication pattern"),
                    _observation("Ships on Fridays"),
                ]
            )
        )
        tool = create_zep_search_tool(scope="observations")
        out = await tool(_make_ctx(_make_deps(client)), "q")
        assert out != "No results found."
        assert "Prefers async updates: Communication pattern" in out
        assert "Ships on Fridays" in out

    @pytest.mark.asyncio
    async def test_formats_thread_summaries(self) -> None:
        client = MagicMock()
        client.graph.search = AsyncMock(
            return_value=_make_result(
                thread_summaries=[
                    _thread_summary("User discussed Q3 roadmap"),
                    _thread_summary(None, name="onboarding-thread"),
                ]
            )
        )
        tool = create_zep_search_tool(scope="thread_summaries")
        out = await tool(_make_ctx(_make_deps(client)), "q")
        assert out != "No results found."
        assert "User discussed Q3 roadmap" in out
        # Falls back to the node name when no summary is present.
        assert "onboarding-thread" in out

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
