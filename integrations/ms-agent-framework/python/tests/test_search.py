"""Tests for the model-callable V4 graph-search tool."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from agent_framework import Message
from zep_cloud.client import AsyncZep
from zep_cloud.types import (
    EdgeSearchFilters,
    EpisodeSearchFilters,
    NodeSearchFilters,
    SearchFilters,
    ThreadSummarySearchFilters,
)

from zep_ms_agent_framework import ZepContextProvider
from zep_ms_agent_framework.search import create_zep_search_tool

_SCOPES = ("edges", "nodes", "episodes", "observations", "thread_summaries")
_FILTER_TYPES = {
    "edges": EdgeSearchFilters,
    "nodes": NodeSearchFilters,
    "episodes": EpisodeSearchFilters,
    "observations": dict,
    "thread_summaries": ThreadSummarySearchFilters,
}


def _client() -> MagicMock:
    client = MagicMock(spec=AsyncZep)
    client.graph = MagicMock()
    client.graph.get_context = AsyncMock(return_value=SimpleNamespace(context="Built context"))
    for scope in _SCOPES:
        setattr(
            client.graph,
            f"search_{scope}",
            AsyncMock(return_value=SimpleNamespace(items=[])),
        )
    return client


def _edge(fact: str) -> SimpleNamespace:
    return SimpleNamespace(fact=fact)


def _result_item(scope: str) -> SimpleNamespace:
    if scope == "edges":
        return SimpleNamespace(fact="Alice works at Acme")
    if scope == "nodes":
        return SimpleNamespace(name="Alice", summary="An engineer")
    if scope == "episodes":
        return SimpleNamespace(content="I work at Acme")
    if scope == "observations":
        return SimpleNamespace(name="Prefers async updates", summary="Communication pattern")
    return SimpleNamespace(name="thread", summary="User discussed the Q3 roadmap")


async def _call(tool, **kwargs: object) -> str:
    result = await tool.invoke(arguments=kwargs, skip_parsing=True)
    return str(result)


class TestSearchRouting:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("scope", _SCOPES)
    async def test_calls_scope_method_with_graph_uuid(self, scope: str) -> None:
        client = _client()
        tool = create_zep_search_tool(zep_client=client, graph_uuid="graph-uuid")

        await _call(tool, query="find a memory", scope=scope, limit=7)

        method = getattr(client.graph, f"search_{scope}")
        method.assert_awaited_once()
        graph_uuid, kwargs = method.call_args.args[0], method.call_args.kwargs
        assert graph_uuid == "graph-uuid"
        assert kwargs["query"] == "find a memory"
        assert kwargs["page_size"] == 7
        assert client.graph.get_context.await_count == 0

    @pytest.mark.asyncio
    async def test_auto_uses_graph_context(self) -> None:
        client = _client()
        tool = create_zep_search_tool(zep_client=client, graph_uuid="graph-uuid")

        result = await _call(tool, query="find a memory", scope="auto")

        assert result == "Built context"
        client.graph.get_context.assert_awaited_once_with(
            "graph-uuid",
            query="find a memory",
        )
        for scope in _SCOPES:
            getattr(client.graph, f"search_{scope}").assert_not_awaited()

    @pytest.mark.asyncio
    async def test_auto_passes_supported_filters_but_not_search_tool_params(self) -> None:
        client = _client()
        tool = create_zep_search_tool(
            zep_client=client,
            graph_uuid="graph-uuid",
            search_filters={"node_labels": ["Person"]},
            bfs_origin_node_uuids=["origin-uuid"],
        )

        await _call(
            tool,
            query="find a person",
            scope="auto",
            reranker="mmr",
            limit=3,
            mmr_lambda=0.2,
            center_node_uuid="center",
        )

        kwargs = client.graph.get_context.call_args.kwargs
        assert kwargs["query"] == "find a person"
        assert isinstance(kwargs["filters"], SearchFilters)
        assert kwargs["filters"].node_labels == ["Person"]
        assert "reranker" not in kwargs
        assert "page_size" not in kwargs
        assert "mmr_lambda" not in kwargs
        assert "center_node_uuid" not in kwargs
        assert "bfs_origin_node_uuids" not in kwargs

    @pytest.mark.asyncio
    async def test_scope_defaults_are_sent(self) -> None:
        client = _client()
        tool = create_zep_search_tool(zep_client=client, graph_uuid="graph-uuid")

        await _call(tool, query="find a memory")

        kwargs = client.graph.search_edges.call_args.kwargs
        assert kwargs["page_size"] == 10
        assert kwargs["reranker"] == "rrf"
        assert "mmr_lambda" not in kwargs
        assert "center_node_uuid" not in kwargs

    @pytest.mark.asyncio
    async def test_model_search_params_are_forwarded(self) -> None:
        client = _client()
        tool = create_zep_search_tool(zep_client=client, graph_uuid="graph-uuid")

        await _call(
            tool,
            query="find a memory",
            reranker="mmr",
            mmr_lambda=0.4,
            center_node_uuid="center-uuid",
        )

        kwargs = client.graph.search_edges.call_args.kwargs
        assert kwargs["reranker"] == "mmr"
        assert kwargs["mmr_lambda"] == 0.4
        assert kwargs["center_node_uuid"] == "center-uuid"

    @pytest.mark.asyncio
    async def test_scoped_methods_receive_typed_filters_and_bfs_origins(self) -> None:
        client = _client()
        tool = create_zep_search_tool(
            zep_client=client,
            graph_uuid="graph-uuid",
            search_filters={},
            bfs_origin_node_uuids=["origin-uuid"],
        )

        for scope in _SCOPES:
            await _call(tool, query="find a memory", scope=scope)
            kwargs = getattr(client.graph, f"search_{scope}").call_args.kwargs
            assert isinstance(kwargs["filters"], _FILTER_TYPES[scope])
            assert kwargs["bfs_origin_node_uuids"] == ["origin-uuid"]

    def test_constructor_requires_graph_uuid(self) -> None:
        with pytest.raises(ValueError, match="graph_uuid"):
            create_zep_search_tool(zep_client=_client(), graph_uuid="")


class TestResultFormatting:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("scope", "expected"),
        [
            ("edges", "Alice works at Acme"),
            ("nodes", "Alice: An engineer"),
            ("episodes", "I work at Acme"),
            ("observations", "Prefers async updates: Communication pattern"),
            ("thread_summaries", "User discussed the Q3 roadmap"),
        ],
    )
    async def test_formats_scope_results(self, scope: str, expected: str) -> None:
        client = _client()
        getattr(client.graph, f"search_{scope}").return_value = SimpleNamespace(
            items=[_result_item(scope)]
        )
        tool = create_zep_search_tool(zep_client=client, graph_uuid="graph-uuid")

        result = await _call(tool, query="q", scope=scope)

        assert expected in result

    @pytest.mark.asyncio
    async def test_formats_empty_results(self) -> None:
        client = _client()
        tool = create_zep_search_tool(zep_client=client, graph_uuid="graph-uuid")

        assert await _call(tool, query="q", scope="edges") == "No results found."

    @pytest.mark.asyncio
    async def test_formats_empty_auto_context(self) -> None:
        client = _client()
        client.graph.get_context.return_value = SimpleNamespace(context=None)
        tool = create_zep_search_tool(zep_client=client, graph_uuid="graph-uuid")

        assert await _call(tool, query="q", scope="auto") == "No results found."


class TestSearchToolSchema:
    def test_exposes_search_parameters_by_default(self) -> None:
        tool = create_zep_search_tool(zep_client=_client(), graph_uuid="graph-uuid")

        schema = tool.parameters()
        assert set(schema["properties"]) == {
            "query",
            "scope",
            "reranker",
            "limit",
            "mmr_lambda",
            "center_node_uuid",
        }
        assert schema["properties"]["scope"]["enum"] == [
            "edges",
            "nodes",
            "episodes",
            "observations",
            "thread_summaries",
            "auto",
        ]
        assert schema["required"] == ["query"]
        assert schema["properties"]["limit"]["minimum"] == 1
        assert schema["properties"]["limit"]["maximum"] == 50

    def test_pins_and_hides_search_parameters(self) -> None:
        tool = create_zep_search_tool(
            zep_client=_client(),
            graph_uuid="graph-uuid",
            search_pinned_params={"scope": "nodes", "limit": 5},
            search_hidden_params={"mmr_lambda"},
        )

        properties = tool.parameters()["properties"]
        assert "scope" not in properties
        assert "limit" not in properties
        assert "mmr_lambda" not in properties
        assert "reranker" in properties
        assert "center_node_uuid" in properties

    @pytest.mark.asyncio
    async def test_pinned_values_are_sent(self) -> None:
        client = _client()
        tool = create_zep_search_tool(
            zep_client=client,
            graph_uuid="graph-uuid",
            search_pinned_params={"scope": "nodes", "limit": 5},
        )

        await _call(tool, query="find a person")

        kwargs = client.graph.search_nodes.call_args.kwargs
        assert kwargs["page_size"] == 5
        assert client.graph.search_nodes.call_args.args[0] == "graph-uuid"

    @pytest.mark.asyncio
    async def test_pinned_limit_is_clamped(self) -> None:
        client = _client()
        tool = create_zep_search_tool(
            zep_client=client,
            graph_uuid="graph-uuid",
            search_pinned_params={"scope": "nodes", "limit": 100},
        )

        await _call(tool, query="find a person")

        assert client.graph.search_nodes.call_args.kwargs["page_size"] == 50

    @pytest.mark.asyncio
    async def test_hidden_value_is_omitted(self) -> None:
        client = _client()
        tool = create_zep_search_tool(
            zep_client=client,
            graph_uuid="graph-uuid",
            search_hidden_params={"mmr_lambda"},
        )

        await _call(tool, query="find a memory", mmr_lambda=0.2)

        assert "mmr_lambda" not in client.graph.search_edges.call_args.kwargs

    def test_rejects_unknown_pin_or_hide_parameter(self) -> None:
        with pytest.raises(ValueError, match="Unknown search parameters"):
            create_zep_search_tool(
                zep_client=_client(),
                graph_uuid="graph-uuid",
                search_hidden_params={"unknown"},
            )

    def test_constructor_only_options_are_not_model_parameters(self) -> None:
        tool = create_zep_search_tool(
            zep_client=_client(),
            graph_uuid="graph-uuid",
            search_filters={"node_labels": ["Person"]},
            bfs_origin_node_uuids=["origin-uuid"],
        )

        properties = tool.parameters()["properties"]
        assert "search_filters" not in properties
        assert "bfs_origin_node_uuids" not in properties

    def test_custom_tool_name(self) -> None:
        tool = create_zep_search_tool(
            zep_client=_client(),
            graph_uuid="graph-uuid",
            name="search_memory",
        )

        assert tool.name == "search_memory"


class TestSearchErrorHandling:
    @pytest.mark.asyncio
    async def test_empty_query_returns_error_without_search(self) -> None:
        client = _client()
        tool = create_zep_search_tool(zep_client=client, graph_uuid="graph-uuid")

        assert await _call(tool, query="   ") == "Error: No search query provided."
        client.graph.search_edges.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_search_errors_return_text(self) -> None:
        client = _client()
        client.graph.search_edges.side_effect = RuntimeError("API down")
        tool = create_zep_search_tool(zep_client=client, graph_uuid="graph-uuid")

        result = await _call(tool, query="q")

        assert "Graph search failed" in result
        assert "API down" in result

    @pytest.mark.asyncio
    async def test_query_is_clamped(self) -> None:
        client = _client()
        tool = create_zep_search_tool(zep_client=client, graph_uuid="graph-uuid")

        await _call(tool, query="x" * 500)

        assert len(client.graph.search_edges.call_args.kwargs["query"]) == 400

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("limit", "expected"), [(100, 50), (0, 1)])
    async def test_model_limit_is_clamped(self, limit: int, expected: int) -> None:
        client = _client()
        tool = create_zep_search_tool(zep_client=client, graph_uuid="graph-uuid")

        await _call(tool, query="q", limit=limit)

        assert client.graph.search_edges.call_args.kwargs["page_size"] == expected


class TestExposeSearchToolOnProvider:
    @pytest.mark.asyncio
    async def test_expose_search_tool_extends_tools(self) -> None:
        client = _client()
        client.thread = MagicMock()
        client.thread.add_messages = AsyncMock(return_value=SimpleNamespace(context=None))
        provider = ZepContextProvider(
            zep_client=client,
            user_uuid="user-uuid",
            thread_uuid="thread-uuid",
            graph_uuid="user-uuid",
            expose_search_tool=True,
        )
        context = MagicMock()
        context.input_messages = [Message("user", ["Hi"])]
        context.extend_instructions = MagicMock()
        context.extend_tools = MagicMock()

        await provider.before_run(
            agent=MagicMock(),
            session=MagicMock(),
            context=context,
            state={},
        )

        context.extend_tools.assert_called_once()
        source_id, tools = context.extend_tools.call_args.args
        assert source_id == "zep"
        assert len(tools) == 1

    @pytest.mark.asyncio
    async def test_search_tool_requires_graph_uuid(self) -> None:
        with pytest.raises(ValueError, match="graph_uuid"):
            ZepContextProvider(
                zep_client=_client(),
                user_uuid="user-uuid",
                thread_uuid="thread-uuid",
                expose_search_tool=True,
            )

    @pytest.mark.asyncio
    async def test_search_tool_is_not_extended_by_default(self) -> None:
        client = _client()
        client.thread = MagicMock()
        client.thread.add_messages = AsyncMock(return_value=SimpleNamespace(context=None))
        provider = ZepContextProvider(
            zep_client=client,
            user_uuid="user-uuid",
            thread_uuid="thread-uuid",
            graph_uuid="user-uuid",
        )
        context = MagicMock()
        context.input_messages = [Message("user", ["Hi"])]
        context.extend_instructions = MagicMock()
        context.extend_tools = MagicMock()

        await provider.before_run(
            agent=MagicMock(),
            session=MagicMock(),
            context=context,
            state={},
        )

        context.extend_tools.assert_not_called()
