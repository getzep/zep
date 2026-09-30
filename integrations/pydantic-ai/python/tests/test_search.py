"""
Tests for the ``create_zep_search_tool`` factory with a mocked Zep client.

``create_zep_search_tool`` returns a ``pydantic_ai.Tool`` instance (not a bare
callable) so pinned/hidden search parameters can be excluded from the model-
facing JSON schema.  Tests call the tool's underlying function directly via
``_call`` (mirroring how Pydantic AI's ``FunctionSchema.call`` invokes it:
``ctx`` positional, everything else keyword) and inspect
``tool.tool_def.parameters_json_schema`` for schema-shape assertions.

Zep v4 has one search method for each scope, and every method takes the graph
UUID as its first positional argument and returns a pager.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic_ai import Tool

from zep_pydantic_ai import ZepDeps, create_zep_search_tool

GRAPH_UUID = "graph-uuid-1"


def _make_deps(client: MagicMock, graph_uuid: str | None = GRAPH_UUID) -> ZepDeps:
    return ZepDeps(
        client=client,
        user_uuid="user-uuid-1",
        thread_uuid="thread-uuid-1",
        graph_uuid=graph_uuid,
    )


def _make_ctx(deps: ZepDeps) -> MagicMock:
    ctx = MagicMock()
    ctx.deps = deps
    return ctx


async def _call(tool: Tool, ctx: MagicMock, query: str, **kwargs: object) -> str:
    """Invoke a ``Tool``'s underlying function the way Pydantic AI does:
    ``ctx`` positional, all schema arguments as keywords."""
    return await tool.function(ctx, query=query, **kwargs)  # type: ignore[no-any-return]


def _make_client(scope: str = "edges", items: list[MagicMock] | None = None) -> MagicMock:
    """Build a mock client whose search method for ``scope`` returns a pager."""
    client = MagicMock()
    pager = MagicMock()
    pager.items = items if items is not None else []
    method = AsyncMock(return_value=pager)
    setattr(client.graph, f"search_{scope}", method)
    return client


def _search_mock(client: MagicMock, scope: str = "edges") -> AsyncMock:
    """Return the mock search method of ``scope`` on ``client``."""
    return getattr(client.graph, f"search_{scope}")  # type: ignore[no-any-return]


def _make_context_client(context: str | None) -> MagicMock:
    client = MagicMock()
    response = MagicMock()
    response.context = context
    client.graph.get_context = AsyncMock(return_value=response)
    return client


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


def _thread_summary(summary: str | None) -> MagicMock:
    ts = MagicMock()
    ts.summary = summary
    return ts


class TestSearchTargeting:
    @pytest.mark.asyncio
    async def test_searches_the_graph_uuid_of_deps_by_default(self) -> None:
        client = _make_client(items=[_edge("fact")])
        tool = create_zep_search_tool()
        ctx = _make_ctx(_make_deps(client))

        await _call(tool, ctx, "what do you know")

        args = _search_mock(client).call_args.args
        assert args[0] == GRAPH_UUID

    @pytest.mark.asyncio
    async def test_searches_graph_uuid_of_the_tool_when_set(self) -> None:
        client = _make_client()
        tool = create_zep_search_tool(graph_uuid="docs-graph-uuid")
        ctx = _make_ctx(_make_deps(client))

        await _call(tool, ctx, "query")

        assert _search_mock(client).call_args.args[0] == "docs-graph-uuid"

    @pytest.mark.asyncio
    async def test_reports_a_missing_graph_uuid(self) -> None:
        client = _make_client()
        tool = create_zep_search_tool()
        ctx = _make_ctx(_make_deps(client, graph_uuid=None))

        out = await _call(tool, ctx, "query")

        assert "graph UUID" in out
        _search_mock(client).assert_not_called()

    @pytest.mark.asyncio
    async def test_default_params(self) -> None:
        client = _make_client()
        tool = create_zep_search_tool()
        ctx = _make_ctx(_make_deps(client))

        await _call(tool, ctx, "query")

        kwargs = _search_mock(client).call_args.kwargs
        assert kwargs["reranker"] == "rrf"
        assert kwargs["limit"] == 10
        assert "scope" not in kwargs

    @pytest.mark.asyncio
    async def test_pinned_params_used(self) -> None:
        client = _make_client(scope="nodes")
        tool = create_zep_search_tool(scope="nodes", reranker="cross_encoder", limit=3)
        ctx = _make_ctx(_make_deps(client))

        await _call(tool, ctx, "query")

        kwargs = _search_mock(client, "nodes").call_args.kwargs
        assert kwargs["reranker"] == "cross_encoder"
        assert kwargs["limit"] == 3

    @pytest.mark.asyncio
    async def test_query_truncated_to_400(self) -> None:
        client = _make_client()
        tool = create_zep_search_tool()
        ctx = _make_ctx(_make_deps(client))

        await _call(tool, ctx, "x" * 1000)

        assert len(_search_mock(client).call_args.kwargs["query"]) == 400

    def test_custom_tool_name(self) -> None:
        tool = create_zep_search_tool(name="search_memory")
        assert tool.name == "search_memory"


class TestLimitClamping:
    @pytest.mark.asyncio
    async def test_limit_clamped_to_50(self) -> None:
        client = _make_client()
        tool = create_zep_search_tool(limit=1000)
        ctx = _make_ctx(_make_deps(client))

        await _call(tool, ctx, "query")

        assert _search_mock(client).call_args.kwargs["limit"] == 50

    @pytest.mark.asyncio
    async def test_limit_at_ceiling_unchanged(self) -> None:
        client = _make_client()
        tool = create_zep_search_tool(limit=50)
        ctx = _make_ctx(_make_deps(client))

        await _call(tool, ctx, "query")

        assert _search_mock(client).call_args.kwargs["limit"] == 50

    @pytest.mark.asyncio
    async def test_limit_floor_is_one(self) -> None:
        client = _make_client()
        tool = create_zep_search_tool(limit=0)
        ctx = _make_ctx(_make_deps(client))

        await _call(tool, ctx, "query")

        assert _search_mock(client).call_args.kwargs["limit"] == 1


class TestModelProvidedArguments:
    @pytest.mark.asyncio
    async def test_none_scope_falls_back_to_default(self) -> None:
        """An explicit JSON null from the model (Tool.from_schema skips
        argument validation) must not select a scope of None; the spec default
        applies and the tool searches edges."""
        client = _make_client(items=[_edge("Alice hikes")])
        tool = create_zep_search_tool()
        ctx = _make_ctx(_make_deps(client))

        out = await _call(tool, ctx, "query", scope=None)

        _search_mock(client).assert_called_once()
        assert "Alice hikes" in out

    @pytest.mark.asyncio
    async def test_none_optional_params_omitted(self) -> None:
        """None for a defaultless param (mmr_lambda, center_node_uuid) is
        omitted from the SDK call rather than sent as JSON null."""
        client = _make_client()
        tool = create_zep_search_tool()
        ctx = _make_ctx(_make_deps(client))

        await _call(tool, ctx, "query", mmr_lambda=None, center_node_uuid=None)

        kwargs = _search_mock(client).call_args.kwargs
        assert "mmr_lambda" not in kwargs
        assert "center_node_uuid" not in kwargs

    @pytest.mark.asyncio
    async def test_model_limit_clamped_to_ceiling(self) -> None:
        """A model-provided limit above Zep's cap is clamped at call time so
        the search never 400s."""
        client = _make_client()
        tool = create_zep_search_tool()
        ctx = _make_ctx(_make_deps(client))

        await _call(tool, ctx, "query", limit=200)

        assert _search_mock(client).call_args.kwargs["limit"] == 50

    @pytest.mark.asyncio
    async def test_model_limit_floored_to_one(self) -> None:
        client = _make_client()
        tool = create_zep_search_tool()
        ctx = _make_ctx(_make_deps(client))

        await _call(tool, ctx, "query", limit=0)

        assert _search_mock(client).call_args.kwargs["limit"] == 1

    @pytest.mark.asyncio
    async def test_model_limit_in_range_unchanged(self) -> None:
        client = _make_client()
        tool = create_zep_search_tool()
        ctx = _make_ctx(_make_deps(client))

        await _call(tool, ctx, "query", limit=25)

        assert _search_mock(client).call_args.kwargs["limit"] == 25

    def test_limit_schema_carries_bounds(self) -> None:
        """The model-facing schema advertises Zep's limit bounds so
        well-behaved models self-limit."""
        tool = create_zep_search_tool()
        limit_prop = tool.tool_def.parameters_json_schema["properties"]["limit"]

        assert limit_prop["minimum"] == 1
        assert limit_prop["maximum"] == 50


class TestAutoScope:
    @pytest.mark.asyncio
    async def test_auto_scope_calls_get_context(self) -> None:
        """The auto scope uses graph.get_context, which takes the query and
        the filters only."""
        client = _make_context_client("ctx")
        tool = create_zep_search_tool(scope="auto", reranker="node_distance", limit=25)
        ctx = _make_ctx(_make_deps(client))

        await _call(tool, ctx, "query")

        assert client.graph.get_context.call_args.args[0] == GRAPH_UUID
        kwargs = client.graph.get_context.call_args.kwargs
        assert kwargs == {"query": "query"}

    @pytest.mark.asyncio
    async def test_auto_scope_sends_filters(self) -> None:
        client = _make_context_client("ctx")
        tool = create_zep_search_tool(scope="auto", filters={"node_labels": ["Person"]})
        ctx = _make_ctx(_make_deps(client))

        await _call(tool, ctx, "query")

        kwargs = client.graph.get_context.call_args.kwargs
        assert kwargs["filters"] == {"node_labels": ["Person"]}

    @pytest.mark.asyncio
    async def test_non_auto_scope_keeps_reranker(self) -> None:
        client = _make_client()
        tool = create_zep_search_tool(scope="edges", reranker="node_distance")
        ctx = _make_ctx(_make_deps(client))

        await _call(tool, ctx, "query")

        assert _search_mock(client).call_args.kwargs["reranker"] == "node_distance"


class TestExtendedScopes:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("scope", ["observations", "thread_summaries"])
    async def test_new_scopes_call_their_own_method(self, scope: str) -> None:
        client = _make_client(scope=scope)
        tool = create_zep_search_tool(scope=scope)  # type: ignore[arg-type]
        ctx = _make_ctx(_make_deps(client))

        await _call(tool, ctx, "query")

        _search_mock(client, scope).assert_called_once()


class TestResultFormatting:
    @pytest.mark.asyncio
    async def test_formats_edges(self) -> None:
        client = _make_client(items=[_edge("Alice works at Acme"), _edge("Bob hikes")])
        tool = create_zep_search_tool(scope="edges")
        out = await _call(tool, _make_ctx(_make_deps(client)), "q")
        assert "Alice works at Acme" in out
        assert "Bob hikes" in out

    @pytest.mark.asyncio
    async def test_formats_nodes(self) -> None:
        client = _make_client(scope="nodes", items=[_node("Alice", "An engineer")])
        tool = create_zep_search_tool(scope="nodes")
        out = await _call(tool, _make_ctx(_make_deps(client)), "q")
        assert "Alice: An engineer" in out

    @pytest.mark.asyncio
    async def test_formats_episodes(self) -> None:
        client = _make_client(scope="episodes", items=[_episode("I work at Acme")])
        tool = create_zep_search_tool(scope="episodes")
        out = await _call(tool, _make_ctx(_make_deps(client)), "q")
        assert "I work at Acme" in out

    @pytest.mark.asyncio
    async def test_auto_scope_returns_context(self) -> None:
        client = _make_context_client("Assembled context block")
        tool = create_zep_search_tool(scope="auto")
        out = await _call(tool, _make_ctx(_make_deps(client)), "q")
        assert out == "Assembled context block"

    @pytest.mark.asyncio
    async def test_auto_scope_without_context(self) -> None:
        client = _make_context_client(None)
        tool = create_zep_search_tool(scope="auto")
        out = await _call(tool, _make_ctx(_make_deps(client)), "q")
        assert out == "No results found."

    @pytest.mark.asyncio
    async def test_formats_observations(self) -> None:
        client = _make_client(
            scope="observations",
            items=[
                _observation("Prefers async updates", "Communication pattern"),
                _observation("Ships on Fridays"),
            ],
        )
        tool = create_zep_search_tool(scope="observations")
        out = await _call(tool, _make_ctx(_make_deps(client)), "q")
        assert out != "No results found."
        assert "Prefers async updates: Communication pattern" in out
        assert "Ships on Fridays" in out

    @pytest.mark.asyncio
    async def test_formats_thread_summaries(self) -> None:
        client = _make_client(
            scope="thread_summaries",
            items=[_thread_summary("User discussed Q3 roadmap"), _thread_summary(None)],
        )
        tool = create_zep_search_tool(scope="thread_summaries")
        out = await _call(tool, _make_ctx(_make_deps(client)), "q")
        assert out == "- User discussed Q3 roadmap"

    @pytest.mark.asyncio
    async def test_empty_results(self) -> None:
        client = _make_client()
        tool = create_zep_search_tool()
        out = await _call(tool, _make_ctx(_make_deps(client)), "q")
        assert out == "No results found."


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_search_failure_returns_error_string(self) -> None:
        client = _make_client()
        _search_mock(client).side_effect = RuntimeError("boom")
        tool = create_zep_search_tool()
        out = await _call(tool, _make_ctx(_make_deps(client)), "q")
        assert "failed" in out.lower()

    @pytest.mark.asyncio
    async def test_context_failure_returns_error_string(self) -> None:
        client = _make_context_client("ctx")
        client.graph.get_context.side_effect = RuntimeError("boom")
        tool = create_zep_search_tool(scope="auto")
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
        client = _make_client(scope="nodes")
        tool = create_zep_search_tool(pinned_params={"scope": "nodes", "limit": 5})
        ctx = _make_ctx(_make_deps(client))

        # scope/limit are pinned -> not part of the schema, so the model
        # cannot (and needn't) pass them; the tool sends the pinned value.
        await _call(tool, ctx, "query")

        assert _search_mock(client, "nodes").call_args.kwargs["limit"] == 5

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
        client = _make_client()
        tool = create_zep_search_tool(hidden_params={"mmr_lambda"})
        ctx = _make_ctx(_make_deps(client))

        await _call(tool, ctx, "query")

        assert "mmr_lambda" not in _search_mock(client).call_args.kwargs

    def test_bfs_and_filters_constructor_only(self) -> None:
        """filters / bfs_origin_node_uuids are never part of the model
        schema -- constructor-only."""
        tool = create_zep_search_tool(
            filters={"node_labels": ["Person"]},
            bfs_origin_node_uuids=["uuid-1"],
        )
        properties = tool.tool_def.parameters_json_schema["properties"]

        assert "filters" not in properties
        assert "bfs_origin_node_uuids" not in properties

    @pytest.mark.asyncio
    async def test_bfs_and_filters_sent_to_sdk(self) -> None:
        client = _make_client()
        tool = create_zep_search_tool(
            filters={"node_labels": ["Person"]},
            bfs_origin_node_uuids=["uuid-1"],
        )
        ctx = _make_ctx(_make_deps(client))

        await _call(tool, ctx, "query")

        kwargs = _search_mock(client).call_args.kwargs
        assert kwargs["filters"] == {"node_labels": ["Person"]}
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
