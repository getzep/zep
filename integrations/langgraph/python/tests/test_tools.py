"""
Tests for the prebuilt graph-search tools (zep_langgraph.tools).
"""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.tools import StructuredTool

from zep_langgraph.tools import (
    _format_results,
    create_graph_search_tool,
    create_graph_search_tool_sync,
)

GRAPH_UUID = "22222222-2222-2222-2222-222222222222"
OTHER_GRAPH_UUID = "44444444-4444-4444-4444-444444444444"

_SEARCH_METHODS = (
    "search_edges",
    "search_nodes",
    "search_episodes",
    "search_observations",
    "search_thread_summaries",
)


def _make_async_client() -> MagicMock:
    client = MagicMock()
    client.graph = MagicMock()
    for method in _SEARCH_METHODS:
        setattr(client.graph, method, AsyncMock())
    client.graph.get_context = AsyncMock()
    return client


def _make_sync_client() -> MagicMock:
    client = MagicMock()
    client.graph = MagicMock()
    for method in _SEARCH_METHODS:
        setattr(client.graph, method, MagicMock())
    client.graph.get_context = MagicMock()
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


def _thread_summary(summary: str) -> MagicMock:
    ts = MagicMock()
    ts.summary = summary
    ts.uuid_ = "ts-uuid"
    ts.score = None
    return ts


def _page(items: list) -> MagicMock:
    """A v4 pager: the records of one page are in ``items``."""
    page = MagicMock()
    page.items = items
    return page


def _context(context: str | None) -> MagicMock:
    """A v4 ``GraphContextResponse``."""
    response = MagicMock()
    response.context = context
    return response


class TestToolMetadata:
    def test_default_name_and_description(self) -> None:
        tool = create_graph_search_tool(_make_async_client(), graph_uuid=GRAPH_UUID)
        assert tool.name == "search_memory"
        assert "memory" in tool.description.lower()

    def test_custom_name_and_description(self) -> None:
        tool = create_graph_search_tool(
            _make_async_client(),
            graph_uuid=GRAPH_UUID,
            name="search_docs",
            description="Find docs.",
        )
        assert tool.name == "search_docs"
        assert tool.description == "Find docs."

    def test_is_async_tool(self) -> None:
        tool = create_graph_search_tool(_make_async_client(), graph_uuid=GRAPH_UUID)
        assert tool.coroutine is not None

    def test_returns_structured_tool(self) -> None:
        tool = create_graph_search_tool(_make_async_client(), graph_uuid=GRAPH_UUID)
        assert isinstance(tool, StructuredTool)


class TestPinOrExposeSchema:
    def test_search_tool_exposes_params_by_default(self) -> None:
        tool = create_graph_search_tool(_make_async_client(), graph_uuid=GRAPH_UUID)
        schema = tool.args_schema.model_json_schema()
        properties = schema["properties"]
        for name in ("query", "scope", "reranker", "limit", "mmr_lambda", "center_node_uuid"):
            assert name in properties, f"{name} should be exposed by default"
        assert schema["required"] == ["query"]

    def test_search_tool_six_scopes(self) -> None:
        tool = create_graph_search_tool(_make_async_client(), graph_uuid=GRAPH_UUID)
        schema = tool.args_schema.model_json_schema()
        scope_enum = schema["properties"]["scope"]["enum"]
        assert set(scope_enum) == {
            "edges",
            "nodes",
            "episodes",
            "observations",
            "thread_summaries",
            "auto",
        }

    def test_search_tool_five_rerankers(self) -> None:
        tool = create_graph_search_tool(_make_async_client(), graph_uuid=GRAPH_UUID)
        schema = tool.args_schema.model_json_schema()
        reranker_enum = schema["properties"]["reranker"]["enum"]
        assert set(reranker_enum) == {
            "rrf",
            "mmr",
            "node_distance",
            "episode_mentions",
            "cross_encoder",
        }

    def test_constructor_only_params_never_in_schema(self) -> None:
        tool = create_graph_search_tool(
            _make_async_client(),
            graph_uuid=GRAPH_UUID,
            search_filters={"node_labels": ["Person"]},
        )
        schema = tool.args_schema.model_json_schema()
        assert "search_filters" not in schema["properties"]
        assert "filters" not in schema["properties"]
        assert "bfs_origin_node_uuids" not in schema["properties"]

    def test_search_tool_pinned_params_hidden_and_sent(self) -> None:
        client = _make_async_client()
        client.graph.search_nodes.return_value = _page([_node("Alice", "engineer")])
        tool = create_graph_search_tool(
            client, graph_uuid=GRAPH_UUID, pinned_params={"scope": "nodes", "limit": 3}
        )
        schema = tool.args_schema.model_json_schema()
        assert "scope" not in schema["properties"]
        assert "limit" not in schema["properties"]

    @pytest.mark.asyncio
    async def test_pinned_params_applied_to_call(self) -> None:
        client = _make_async_client()
        client.graph.search_nodes.return_value = _page([_node("Alice", "engineer")])
        tool = create_graph_search_tool(
            client, graph_uuid=GRAPH_UUID, pinned_params={"scope": "nodes", "limit": 3}
        )
        out = await tool.ainvoke({"query": "who"})
        client.graph.search_edges.assert_not_awaited()
        call = client.graph.search_nodes.call_args
        assert call.args[0] == GRAPH_UUID
        assert call.kwargs["limit"] == 3
        assert "scope" not in call.kwargs
        assert "Alice" in out

    def test_search_tool_hidden_params_omitted_from_schema(self) -> None:
        tool = create_graph_search_tool(
            _make_async_client(), graph_uuid=GRAPH_UUID, hidden_params={"mmr_lambda"}
        )
        schema = tool.args_schema.model_json_schema()
        assert "mmr_lambda" not in schema["properties"]

    @pytest.mark.asyncio
    async def test_search_tool_hidden_params_omitted_from_sdk_call(self) -> None:
        client = _make_async_client()
        client.graph.search_edges.return_value = _page([_edge("fact")])
        tool = create_graph_search_tool(
            client, graph_uuid=GRAPH_UUID, hidden_params={"mmr_lambda", "center_node_uuid"}
        )
        await tool.ainvoke({"query": "x"})
        call = client.graph.search_edges.call_args.kwargs
        assert "mmr_lambda" not in call
        assert "center_node_uuid" not in call

    @pytest.mark.asyncio
    async def test_search_tool_query_only_omits_unset_none_default_params(self) -> None:
        # mmr_lambda / center_node_uuid default to None in the spec; when the
        # model doesn't supply them, they must never be forwarded as an
        # explicit `None` to the v4 search method (only Zep's own server
        # default should apply).
        client = _make_async_client()
        client.graph.search_edges.return_value = _page([_edge("fact")])
        tool = create_graph_search_tool(client, graph_uuid=GRAPH_UUID)
        await tool.ainvoke({"query": "x"})
        call = client.graph.search_edges.call_args.kwargs
        assert "mmr_lambda" not in call
        assert "center_node_uuid" not in call
        # But defaulted params ARE sent.
        assert call["reranker"] == "rrf"
        assert call["limit"] == 10

    @pytest.mark.asyncio
    async def test_model_provided_mmr_lambda_forwarded(self) -> None:
        client = _make_async_client()
        client.graph.search_edges.return_value = _page([_edge("fact")])
        tool = create_graph_search_tool(client, graph_uuid=GRAPH_UUID)
        await tool.ainvoke({"query": "x", "reranker": "mmr", "mmr_lambda": 0.5})
        assert client.graph.search_edges.call_args.kwargs["mmr_lambda"] == 0.5

    def test_search_tool_legacy_args_pin(self) -> None:
        tool = create_graph_search_tool(
            _make_async_client(), graph_uuid=GRAPH_UUID, scope="nodes", limit=5
        )
        schema = tool.args_schema.model_json_schema()
        assert "scope" not in schema["properties"]
        assert "limit" not in schema["properties"]

    @pytest.mark.asyncio
    async def test_legacy_args_pin_applied_to_call(self) -> None:
        client = _make_async_client()
        client.graph.search_nodes.return_value = _page([_node("Alice", "x")])
        tool = create_graph_search_tool(client, graph_uuid=GRAPH_UUID, scope="nodes", limit=5)
        await tool.ainvoke({"query": "who"})
        assert client.graph.search_nodes.call_args.kwargs["limit"] == 5

    def test_unknown_pinned_param_rejected(self) -> None:
        with pytest.raises(ValueError, match="Unknown pinned"):
            create_graph_search_tool(
                _make_async_client(), graph_uuid=GRAPH_UUID, pinned_params={"bogus": 1}
            )

    def test_unknown_hidden_param_rejected(self) -> None:
        with pytest.raises(ValueError, match="Unknown hidden"):
            create_graph_search_tool(
                _make_async_client(), graph_uuid=GRAPH_UUID, hidden_params={"bogus"}
            )


class TestScopeRouting:
    @pytest.mark.parametrize(
        ("scope", "method", "page"),
        [
            ("edges", "search_edges", _page([_edge("a fact")])),
            ("nodes", "search_nodes", _page([_node("Alice", "engineer")])),
            ("episodes", "search_episodes", _page([_episode("raw text")])),
            ("observations", "search_observations", _page([_node("Obs", "detail")])),
            (
                "thread_summaries",
                "search_thread_summaries",
                _page([_thread_summary("a summary")]),
            ),
        ],
    )
    @pytest.mark.asyncio
    async def test_scope_selects_dedicated_method(self, scope: str, method: str, page) -> None:
        client = _make_async_client()
        getattr(client.graph, method).return_value = page
        tool = create_graph_search_tool(client, graph_uuid=GRAPH_UUID)

        await tool.ainvoke({"query": "x", "scope": scope})

        called = getattr(client.graph, method)
        called.assert_awaited_once()
        assert called.call_args.args[0] == GRAPH_UUID
        for other in _SEARCH_METHODS:
            if other != method:
                getattr(client.graph, other).assert_not_awaited()
        client.graph.get_context.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_auto_scope_calls_get_context(self) -> None:
        client = _make_async_client()
        client.graph.get_context.return_value = _context("prebuilt block")
        tool = create_graph_search_tool(client, graph_uuid=GRAPH_UUID)

        out = await tool.ainvoke({"query": "x", "scope": "auto"})

        client.graph.get_context.assert_awaited_once()
        assert client.graph.get_context.call_args.args[0] == GRAPH_UUID
        assert out == "prebuilt block"
        client.graph.search_edges.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_graph_uuid_is_the_only_address(self) -> None:
        client = _make_async_client()
        client.graph.search_edges.return_value = _page([_edge("fact")])
        tool = create_graph_search_tool(client, graph_uuid=OTHER_GRAPH_UUID)

        await tool.ainvoke({"query": "x"})

        call = client.graph.search_edges.call_args
        assert call.args == (OTHER_GRAPH_UUID,)
        assert "user_id" not in call.kwargs
        assert "graph_id" not in call.kwargs


class TestLimitClamping:
    @pytest.mark.asyncio
    async def test_pinned_limit_above_ceiling_clamped_with_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = _make_async_client()
        client.graph.search_edges.return_value = _page([])
        with caplog.at_level(logging.WARNING, logger="zep_langgraph.tools"):
            tool = create_graph_search_tool(
                client, graph_uuid=GRAPH_UUID, pinned_params={"limit": 100}
            )
        assert "clamping" in caplog.text

        await tool.ainvoke({"query": "x"})

        assert client.graph.search_edges.call_args.kwargs["limit"] == 50

    @pytest.mark.asyncio
    async def test_pinned_limit_below_one_clamped(self) -> None:
        client = _make_async_client()
        client.graph.search_edges.return_value = _page([])
        tool = create_graph_search_tool(client, graph_uuid=GRAPH_UUID, pinned_params={"limit": 0})

        await tool.ainvoke({"query": "x"})

        assert client.graph.search_edges.call_args.kwargs["limit"] == 1

    @pytest.mark.asyncio
    async def test_model_provided_limit_above_ceiling_clamped(self) -> None:
        client = _make_async_client()
        client.graph.search_edges.return_value = _page([])
        tool = create_graph_search_tool(client, graph_uuid=GRAPH_UUID)

        await tool.ainvoke({"query": "x", "limit": 200})

        assert client.graph.search_edges.call_args.kwargs["limit"] == 50

    @pytest.mark.asyncio
    async def test_model_provided_limit_below_one_clamped(self) -> None:
        client = _make_async_client()
        client.graph.search_edges.return_value = _page([])
        tool = create_graph_search_tool(client, graph_uuid=GRAPH_UUID)

        await tool.ainvoke({"query": "x", "limit": 0})

        assert client.graph.search_edges.call_args.kwargs["limit"] == 1


class TestAutoScopeParameters:
    @pytest.mark.asyncio
    async def test_pinned_auto_scope_drops_incompatible_reranker(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = _make_async_client()
        client.graph.get_context.return_value = _context("ctx")
        with caplog.at_level(logging.WARNING, logger="zep_langgraph.tools"):
            tool = create_graph_search_tool(
                client,
                graph_uuid=GRAPH_UUID,
                pinned_params={"scope": "auto", "reranker": "node_distance"},
            )
        assert "node_distance" in caplog.text

        await tool.ainvoke({"query": "x"})

        kwargs = client.graph.get_context.call_args.kwargs
        assert "reranker" not in kwargs

    @pytest.mark.asyncio
    async def test_model_provided_auto_scope_drops_unsupported_params(self) -> None:
        client = _make_async_client()
        client.graph.get_context.return_value = _context("ctx")
        tool = create_graph_search_tool(client, graph_uuid=GRAPH_UUID)

        await tool.ainvoke(
            {"query": "x", "scope": "auto", "reranker": "episode_mentions", "limit": 5}
        )

        kwargs = client.graph.get_context.call_args.kwargs
        # graph.get_context accepts neither a reranker nor a limit.
        assert "reranker" not in kwargs
        assert "limit" not in kwargs
        assert kwargs["query"] == "x"

    @pytest.mark.asyncio
    async def test_auto_scope_keeps_filters(self) -> None:
        client = _make_async_client()
        client.graph.get_context.return_value = _context("ctx")
        filters = {"node_labels": ["Person"]}
        tool = create_graph_search_tool(
            client, graph_uuid=GRAPH_UUID, search_filters=filters, pinned_params={"scope": "auto"}
        )

        await tool.ainvoke({"query": "x"})

        assert client.graph.get_context.call_args.kwargs["filters"] == filters

    @pytest.mark.asyncio
    async def test_non_auto_scope_keeps_reranker(self) -> None:
        client = _make_async_client()
        client.graph.search_edges.return_value = _page([])
        tool = create_graph_search_tool(client, graph_uuid=GRAPH_UUID)

        await tool.ainvoke({"query": "x", "scope": "edges", "reranker": "node_distance"})

        assert client.graph.search_edges.call_args.kwargs["reranker"] == "node_distance"


class TestAsyncToolExecution:
    @pytest.mark.asyncio
    async def test_searches_and_formats(self) -> None:
        client = _make_async_client()
        client.graph.search_edges.return_value = _page([_edge("Alice likes blue")])
        tool = create_graph_search_tool(client, graph_uuid=GRAPH_UUID)

        out = await tool.ainvoke({"query": "preferences"})

        call = client.graph.search_edges.call_args
        assert call.args[0] == GRAPH_UUID
        assert call.kwargs["query"] == "preferences"
        assert call.kwargs["limit"] == 10
        assert "Alice likes blue" in out

    @pytest.mark.asyncio
    async def test_model_chosen_scope_reranker_limit(self) -> None:
        client = _make_async_client()
        client.graph.search_nodes.return_value = _page([_node("Alice", "engineer")])
        tool = create_graph_search_tool(client, graph_uuid=GRAPH_UUID)

        out = await tool.ainvoke(
            {"query": "who", "scope": "nodes", "reranker": "cross_encoder", "limit": 3}
        )

        call = client.graph.search_nodes.call_args.kwargs
        assert call["reranker"] == "cross_encoder"
        assert call["limit"] == 3
        assert "Alice" in out

    @pytest.mark.asyncio
    async def test_search_filters_passed_as_v4_filters(self) -> None:
        client = _make_async_client()
        client.graph.search_edges.return_value = _page([])
        filters = {"node_labels": ["Person"]}
        tool = create_graph_search_tool(client, graph_uuid=GRAPH_UUID, search_filters=filters)

        await tool.ainvoke({"query": "x"})

        call = client.graph.search_edges.call_args.kwargs
        assert call["filters"] == filters
        assert "search_filters" not in call

    @pytest.mark.asyncio
    async def test_bfs_origin_node_uuids_passed(self) -> None:
        client = _make_async_client()
        client.graph.search_edges.return_value = _page([])
        tool = create_graph_search_tool(
            client, graph_uuid=GRAPH_UUID, bfs_origin_node_uuids=["uuid-1", "uuid-2"]
        )

        await tool.ainvoke({"query": "x"})

        assert client.graph.search_edges.call_args.kwargs["bfs_origin_node_uuids"] == [
            "uuid-1",
            "uuid-2",
        ]

    @pytest.mark.asyncio
    async def test_empty_query_short_circuits(self) -> None:
        client = _make_async_client()
        tool = create_graph_search_tool(client, graph_uuid=GRAPH_UUID)
        out = await tool.ainvoke({"query": "   "})
        assert "Error" in out
        client.graph.search_edges.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_zep_failure_returns_error_string(self) -> None:
        client = _make_async_client()
        client.graph.search_edges.side_effect = RuntimeError("boom")
        tool = create_graph_search_tool(client, graph_uuid=GRAPH_UUID)
        out = await tool.ainvoke({"query": "x"})
        assert "failed" in out.lower()

    @pytest.mark.asyncio
    async def test_no_results_message(self) -> None:
        client = _make_async_client()
        client.graph.search_edges.return_value = _page([])
        tool = create_graph_search_tool(client, graph_uuid=GRAPH_UUID)
        out = await tool.ainvoke({"query": "x"})
        assert out == "No results found."


class TestSyncToolExecution:
    def test_searches_and_formats(self) -> None:
        client = _make_sync_client()
        client.graph.search_edges.return_value = _page([_edge("fact one")])
        tool = create_graph_search_tool_sync(client, graph_uuid=GRAPH_UUID)
        out = tool.invoke({"query": "q"})
        assert client.graph.search_edges.call_args.args[0] == GRAPH_UUID
        assert "fact one" in out

    def test_zep_failure_returns_error(self) -> None:
        client = _make_sync_client()
        client.graph.search_edges.side_effect = RuntimeError("down")
        tool = create_graph_search_tool_sync(client, graph_uuid=GRAPH_UUID)
        out = tool.invoke({"query": "q"})
        assert "failed" in out.lower()

    def test_sync_tool_exposes_params_by_default(self) -> None:
        tool = create_graph_search_tool_sync(_make_sync_client(), graph_uuid=GRAPH_UUID)
        schema = tool.args_schema.model_json_schema()
        for name in ("query", "scope", "reranker", "limit", "mmr_lambda", "center_node_uuid"):
            assert name in schema["properties"]

    def test_sync_tool_pinned_params(self) -> None:
        client = _make_sync_client()
        client.graph.search_nodes.return_value = _page([_node("Bob", None)])
        tool = create_graph_search_tool_sync(
            client, graph_uuid=GRAPH_UUID, pinned_params={"scope": "nodes"}
        )
        out = tool.invoke({"query": "who"})
        client.graph.search_nodes.assert_called_once()
        assert "Bob" in out

    def test_sync_tool_auto_scope_calls_get_context(self) -> None:
        client = _make_sync_client()
        client.graph.get_context.return_value = _context("block")
        tool = create_graph_search_tool_sync(
            client, graph_uuid=GRAPH_UUID, pinned_params={"scope": "auto"}
        )
        assert tool.invoke({"query": "q"}) == "block"
        client.graph.get_context.assert_called_once()

    def test_sync_tool_omits_unset_none_default_params(self) -> None:
        client = _make_sync_client()
        client.graph.search_edges.return_value = _page([_edge("fact")])
        tool = create_graph_search_tool_sync(client, graph_uuid=GRAPH_UUID)
        tool.invoke({"query": "x"})
        call = client.graph.search_edges.call_args.kwargs
        assert "mmr_lambda" not in call
        assert "center_node_uuid" not in call


class TestFormatResults:
    def test_format_edges(self) -> None:
        out = _format_results(_page([_edge("A"), _edge("B")]), "edges")
        assert "- A" in out
        assert "- B" in out

    def test_format_nodes_with_summary(self) -> None:
        out = _format_results(_page([_node("Alice", "An engineer")]), "nodes")
        assert "Alice: An engineer" in out

    def test_format_nodes_without_summary(self) -> None:
        out = _format_results(_page([_node("Alice", None)]), "nodes")
        assert "- Alice" in out

    def test_format_episodes(self) -> None:
        out = _format_results(_page([_episode("raw text")]), "episodes")
        assert "raw text" in out

    def test_format_thread_summaries(self) -> None:
        out = _format_results(_page([_thread_summary("a summary")]), "thread_summaries")
        assert "- a summary" in out

    def test_format_auto_uses_context(self) -> None:
        out = _format_results(_context("prebuilt block"), "auto")
        assert out == "prebuilt block"

    def test_format_auto_empty_context(self) -> None:
        assert _format_results(_context(None), "auto") == "No results found."

    def test_empty_returns_no_results(self) -> None:
        assert _format_results(_page([]), "edges") == "No results found."
