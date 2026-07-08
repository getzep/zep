"""
Tests for the ``create_zep_search_tool`` factory with a mocked Zep client.

``create_zep_search_tool`` returns a ``pydantic_ai.Tool`` instance (not a bare
callable) so pinned/hidden search parameters can be excluded from the model-
facing JSON schema.  Tests call the tool's underlying function directly via
``_call`` (mirroring how Pydantic AI's ``FunctionSchema.call`` invokes it:
``ctx`` positional, everything else keyword) and inspect
``tool.tool_def.parameters_json_schema`` for schema-shape assertions.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic_ai import Tool

from zep_pydantic_ai import ZepDeps, create_zep_search_tool


def _make_deps(client: MagicMock) -> ZepDeps:
    return ZepDeps(client=client, user_id="user-1", thread_id="thread-1")


def _make_ctx(deps: ZepDeps) -> MagicMock:
    ctx = MagicMock()
    ctx.deps = deps
    return ctx


async def _call(tool: Tool, ctx: MagicMock, query: str, **kwargs: object) -> str:
    """Invoke a ``Tool``'s underlying function the way Pydantic AI does:
    ``ctx`` positional, all schema arguments as keywords."""
    return await tool.function(ctx, query=query, **kwargs)  # type: ignore[no-any-return]


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

        await _call(tool, ctx, "what do you know")

        kwargs = client.graph.search.call_args.kwargs
        assert kwargs["user_id"] == "user-1"
        assert "graph_id" not in kwargs

    @pytest.mark.asyncio
    async def test_searches_graph_id_when_set(self) -> None:
        client = MagicMock()
        client.graph.search = AsyncMock(return_value=_make_result(edges=[]))
        tool = create_zep_search_tool(graph_id="docs-graph")
        ctx = _make_ctx(_make_deps(client))

        await _call(tool, ctx, "query")

        kwargs = client.graph.search.call_args.kwargs
        assert kwargs["graph_id"] == "docs-graph"
        assert "user_id" not in kwargs

    @pytest.mark.asyncio
    async def test_default_params(self) -> None:
        client = MagicMock()
        client.graph.search = AsyncMock(return_value=_make_result(edges=[]))
        tool = create_zep_search_tool()
        ctx = _make_ctx(_make_deps(client))

        await _call(tool, ctx, "query")

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

        await _call(tool, ctx, "query")

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

        await _call(tool, ctx, "x" * 1000)

        kwargs = client.graph.search.call_args.kwargs
        assert len(kwargs["query"]) == 400

    def test_custom_tool_name(self) -> None:
        tool = create_zep_search_tool(name="search_memory")
        assert tool.name == "search_memory"


class TestLimitClamping:
    @pytest.mark.asyncio
    async def test_limit_clamped_to_50(self) -> None:
        client = MagicMock()
        client.graph.search = AsyncMock(return_value=_make_result(edges=[]))
        tool = create_zep_search_tool(limit=1000)
        ctx = _make_ctx(_make_deps(client))

        await _call(tool, ctx, "query")

        kwargs = client.graph.search.call_args.kwargs
        assert kwargs["limit"] == 50

    @pytest.mark.asyncio
    async def test_limit_at_ceiling_unchanged(self) -> None:
        client = MagicMock()
        client.graph.search = AsyncMock(return_value=_make_result(edges=[]))
        tool = create_zep_search_tool(limit=50)
        ctx = _make_ctx(_make_deps(client))

        await _call(tool, ctx, "query")

        assert client.graph.search.call_args.kwargs["limit"] == 50

    @pytest.mark.asyncio
    async def test_limit_floor_is_one(self) -> None:
        client = MagicMock()
        client.graph.search = AsyncMock(return_value=_make_result(edges=[]))
        tool = create_zep_search_tool(limit=0)
        ctx = _make_ctx(_make_deps(client))

        await _call(tool, ctx, "query")

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

        await _call(tool, ctx, "query")

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

        await _call(tool, ctx, "query")

        assert "reranker" not in client.graph.search.call_args.kwargs

    @pytest.mark.asyncio
    async def test_non_auto_scope_keeps_reranker(self) -> None:
        client = MagicMock()
        client.graph.search = AsyncMock(return_value=_make_result(edges=[]))
        tool = create_zep_search_tool(scope="edges", reranker="node_distance")
        ctx = _make_ctx(_make_deps(client))

        await _call(tool, ctx, "query")

        assert client.graph.search.call_args.kwargs["reranker"] == "node_distance"


class TestExtendedScopes:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("scope", ["observations", "thread_summaries"])
    async def test_new_scopes_accepted(self, scope: str) -> None:
        client = MagicMock()
        client.graph.search = AsyncMock(return_value=_make_result(edges=[]))
        tool = create_zep_search_tool(scope=scope)  # type: ignore[arg-type]
        ctx = _make_ctx(_make_deps(client))

        await _call(tool, ctx, "query")

        assert client.graph.search.call_args.kwargs["scope"] == scope


class TestResultFormatting:
    @pytest.mark.asyncio
    async def test_formats_edges(self) -> None:
        client = MagicMock()
        client.graph.search = AsyncMock(
            return_value=_make_result(edges=[_edge("Alice works at Acme"), _edge("Bob hikes")])
        )
        tool = create_zep_search_tool(scope="edges")
        out = await _call(tool, _make_ctx(_make_deps(client)), "q")
        assert "Alice works at Acme" in out
        assert "Bob hikes" in out

    @pytest.mark.asyncio
    async def test_formats_nodes(self) -> None:
        client = MagicMock()
        client.graph.search = AsyncMock(
            return_value=_make_result(nodes=[_node("Alice", "An engineer")])
        )
        tool = create_zep_search_tool(scope="nodes")
        out = await _call(tool, _make_ctx(_make_deps(client)), "q")
        assert "Alice: An engineer" in out

    @pytest.mark.asyncio
    async def test_formats_episodes(self) -> None:
        client = MagicMock()
        client.graph.search = AsyncMock(
            return_value=_make_result(episodes=[_episode("I work at Acme")])
        )
        tool = create_zep_search_tool(scope="episodes")
        out = await _call(tool, _make_ctx(_make_deps(client)), "q")
        assert "I work at Acme" in out

    @pytest.mark.asyncio
    async def test_auto_scope_returns_context(self) -> None:
        client = MagicMock()
        client.graph.search = AsyncMock(
            return_value=_make_result(context="Assembled context block")
        )
        tool = create_zep_search_tool(scope="auto")
        out = await _call(tool, _make_ctx(_make_deps(client)), "q")
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
        out = await _call(tool, _make_ctx(_make_deps(client)), "q")
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
        out = await _call(tool, _make_ctx(_make_deps(client)), "q")
        assert out != "No results found."
        assert "User discussed Q3 roadmap" in out
        # Falls back to the node name when no summary is present.
        assert "onboarding-thread" in out

    @pytest.mark.asyncio
    async def test_empty_results(self) -> None:
        client = MagicMock()
        client.graph.search = AsyncMock(return_value=_make_result(edges=[]))
        tool = create_zep_search_tool()
        out = await _call(tool, _make_ctx(_make_deps(client)), "q")
        assert out == "No results found."


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_search_failure_returns_error_string(self) -> None:
        client = MagicMock()
        client.graph.search = AsyncMock(side_effect=RuntimeError("boom"))
        tool = create_zep_search_tool()
        out = await _call(tool, _make_ctx(_make_deps(client)), "q")
        assert "failed" in out.lower()


class TestPinOrExposeSchema:
    def test_exposes_scope_reranker_limit_by_default(self) -> None:
        """By default (no pins), scope/reranker/limit/mmr_lambda/center_node_uuid
        are all in the model-facing schema alongside query."""
        tool = create_zep_search_tool()
        schema = tool.tool_def.parameters_json_schema
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

    def test_pinned_params_hidden_from_schema(self) -> None:
        tool = create_zep_search_tool(pinned_params={"scope": "nodes", "limit": 5})
        properties = tool.tool_def.parameters_json_schema["properties"]

        assert "scope" not in properties
        assert "limit" not in properties
        # Unpinned params remain exposed.
        assert "reranker" in properties

    @pytest.mark.asyncio
    async def test_pinned_params_sent_to_sdk(self) -> None:
        client = MagicMock()
        client.graph.search = AsyncMock(return_value=_make_result(nodes=[]))
        tool = create_zep_search_tool(pinned_params={"scope": "nodes", "limit": 5})
        ctx = _make_ctx(_make_deps(client))

        # scope/limit are pinned -> not part of the schema, so the model
        # cannot (and needn't) pass them; the tool sends the pinned value.
        await _call(tool, ctx, "query")

        kwargs = client.graph.search.call_args.kwargs
        assert kwargs["scope"] == "nodes"
        assert kwargs["limit"] == 5

    def test_hidden_params_removed_from_schema_and_use_zep_default(self) -> None:
        """hidden_params hides a param from the schema WITHOUT pinning it to a
        fixed value -- Zep's own default applies (the param is simply omitted
        from the SDK call)."""
        tool = create_zep_search_tool(hidden_params={"mmr_lambda", "center_node_uuid"})
        properties = tool.tool_def.parameters_json_schema["properties"]

        assert "mmr_lambda" not in properties
        assert "center_node_uuid" not in properties
        assert "scope" in properties

    @pytest.mark.asyncio
    async def test_hidden_params_omitted_from_sdk_call(self) -> None:
        client = MagicMock()
        client.graph.search = AsyncMock(return_value=_make_result(edges=[]))
        tool = create_zep_search_tool(hidden_params={"mmr_lambda"})
        ctx = _make_ctx(_make_deps(client))

        await _call(tool, ctx, "query")

        assert "mmr_lambda" not in client.graph.search.call_args.kwargs

    def test_bfs_and_filters_constructor_only(self) -> None:
        """search_filters / bfs_origin_node_uuids are never part of the model
        schema -- constructor-only."""
        tool = create_zep_search_tool(
            search_filters={"node_labels": ["Person"]},
            bfs_origin_node_uuids=["uuid-1"],
        )
        properties = tool.tool_def.parameters_json_schema["properties"]

        assert "search_filters" not in properties
        assert "bfs_origin_node_uuids" not in properties

    @pytest.mark.asyncio
    async def test_bfs_and_filters_sent_to_sdk(self) -> None:
        client = MagicMock()
        client.graph.search = AsyncMock(return_value=_make_result(edges=[]))
        tool = create_zep_search_tool(
            search_filters={"node_labels": ["Person"]},
            bfs_origin_node_uuids=["uuid-1"],
        )
        ctx = _make_ctx(_make_deps(client))

        await _call(tool, ctx, "query")

        kwargs = client.graph.search.call_args.kwargs
        assert kwargs["search_filters"] == {"node_labels": ["Person"]}
        assert kwargs["bfs_origin_node_uuids"] == ["uuid-1"]

    def test_legacy_constructor_args_pin(self) -> None:
        """Back-compat: the original scope/reranker/limit constructor args
        still work and pin (hide) those params from the schema, exactly like
        passing them via pinned_params."""
        tool = create_zep_search_tool(scope="nodes", reranker="cross_encoder", limit=3)
        properties = tool.tool_def.parameters_json_schema["properties"]

        assert "scope" not in properties
        assert "reranker" not in properties
        assert "limit" not in properties
        assert "mmr_lambda" in properties

    def test_model_facing_tool_is_a_pydantic_ai_tool(self) -> None:
        tool = create_zep_search_tool()
        assert isinstance(tool, Tool)
        assert tool.takes_ctx is True
