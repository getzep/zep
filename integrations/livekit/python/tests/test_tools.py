from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from livekit.agents.llm import RawFunctionTool
from zep_cloud import SearchFilters
from zep_cloud.types import (
    EdgeSearchFilters,
    EpisodeSearchFilters,
    NodeSearchFilters,
    ThreadSummarySearchFilters,
)

from zep_livekit.exceptions import AgentConfigurationError
from zep_livekit.tools import MAX_SEARCH_LIMIT, create_graph_search_tool


def make_client() -> MagicMock:
    client = MagicMock()
    client.graph = MagicMock()
    for name in (
        "search_edges",
        "search_nodes",
        "search_episodes",
        "search_observations",
        "search_thread_summaries",
        "get_context",
    ):
        setattr(client.graph, name, AsyncMock())
    return client


async def call_tool(tool: RawFunctionTool, **arguments: Any) -> str:
    return await tool(raw_arguments=arguments)


@pytest.mark.parametrize(
    ("scope", "method", "item", "expected"),
    [
        ("edges", "search_edges", SimpleNamespace(fact="A fact"), "A fact"),
        ("nodes", "search_nodes", SimpleNamespace(name="Person", summary="A person"), "A person"),
        (
            "episodes",
            "search_episodes",
            SimpleNamespace(data="An episode"),
            "An episode",
        ),
        (
            "observations",
            "search_observations",
            SimpleNamespace(name="Pattern", summary="A pattern"),
            "A pattern",
        ),
        (
            "thread_summaries",
            "search_thread_summaries",
            SimpleNamespace(summary="A thread summary"),
            "A thread summary",
        ),
    ],
)
@pytest.mark.asyncio
async def test_scope_routes_to_v4_method_and_formats_page_items(
    scope: str,
    method: str,
    item: Any,
    expected: str,
) -> None:
    client = make_client()
    getattr(client.graph, method).return_value = SimpleNamespace(items=[item])
    tool = create_graph_search_tool(client, graph_uuid="graph-uuid")

    result = await call_tool(tool, query="query", scope=scope)

    assert expected in result
    args, kwargs = getattr(client.graph, method).call_args
    assert args == ("graph-uuid",)
    assert kwargs["query"] == "query"
    assert kwargs["page_size"] == 10


def test_tool_schema_exposes_all_scopes_and_search_parameters() -> None:
    tool = create_graph_search_tool(make_client(), graph_uuid="graph-uuid")
    properties = tool.info.raw_schema["parameters"]["properties"]

    assert set(properties["scope"]["enum"]) == {
        "edges",
        "nodes",
        "episodes",
        "observations",
        "thread_summaries",
        "auto",
    }
    assert {"reranker", "limit", "mmr_lambda", "center_node_uuid"} <= set(properties)
    assert tool.info.raw_schema["parameters"]["required"] == ["query"]


def test_graph_uuid_is_required() -> None:
    with pytest.raises(TypeError):
        create_graph_search_tool(make_client())  # type: ignore[call-arg]
    with pytest.raises(AgentConfigurationError, match="graph_uuid"):
        create_graph_search_tool(make_client(), graph_uuid="")


def test_custom_name_and_description() -> None:
    tool = create_graph_search_tool(
        make_client(),
        graph_uuid="graph-uuid",
        name="search_memory",
        description="Search memory.",
    )

    assert tool.info.name == "search_memory"
    assert tool.info.raw_schema["description"] == "Search memory."


def test_pinned_and_hidden_parameters_are_removed_from_schema() -> None:
    tool = create_graph_search_tool(
        make_client(),
        graph_uuid="graph-uuid",
        pinned_params={"scope": "nodes", "limit": 5},
        hidden_params={"mmr_lambda"},
    )
    properties = tool.info.raw_schema["parameters"]["properties"]

    assert "scope" not in properties
    assert "limit" not in properties
    assert "mmr_lambda" not in properties
    assert "reranker" in properties


@pytest.mark.asyncio
async def test_pinned_parameters_are_sent_to_scope_method() -> None:
    client = make_client()
    client.graph.search_nodes.return_value = SimpleNamespace(items=[])
    tool = create_graph_search_tool(
        client,
        graph_uuid="graph-uuid",
        pinned_params={"scope": "nodes", "limit": 5, "reranker": "mmr"},
    )

    await call_tool(tool, query="query")

    assert client.graph.search_nodes.call_args.kwargs["page_size"] == 5
    assert client.graph.search_nodes.call_args.kwargs["reranker"] == "mmr"


def test_unknown_pinned_or_hidden_parameter_is_rejected() -> None:
    with pytest.raises(AgentConfigurationError, match="Unknown"):
        create_graph_search_tool(
            make_client(),
            graph_uuid="graph-uuid",
            pinned_params={"invalid": "value"},
        )
    with pytest.raises(AgentConfigurationError, match="Unknown"):
        create_graph_search_tool(
            make_client(),
            graph_uuid="graph-uuid",
            hidden_params={"invalid"},
        )


@pytest.mark.asyncio
async def test_auto_scope_uses_graph_get_context() -> None:
    client = make_client()
    client.graph.get_context.return_value = SimpleNamespace(context="Retrieved context")
    tool = create_graph_search_tool(
        client,
        graph_uuid="graph-uuid",
        pinned_params={"scope": "auto"},
    )

    result = await call_tool(tool, query="query")

    assert result == "Retrieved context"
    client.graph.get_context.assert_awaited_once_with(
        "graph-uuid",
        query="query",
        filters=None,
    )
    for scope in (
        "search_edges",
        "search_nodes",
        "search_episodes",
        "search_observations",
        "search_thread_summaries",
    ):
        getattr(client.graph, scope).assert_not_called()


@pytest.mark.asyncio
async def test_search_filters_are_typed_for_each_scope() -> None:
    cases = [
        ("edges", "search_edges", EdgeSearchFilters, {"edge_types": ["KNOWS"]}),
        ("nodes", "search_nodes", NodeSearchFilters, {"node_labels": ["Person"]}),
        (
            "episodes",
            "search_episodes",
            EpisodeSearchFilters,
            {"content_policy_violated": False},
        ),
        ("observations", "search_observations", dict, {"node_labels": ["Person"]}),
        (
            "thread_summaries",
            "search_thread_summaries",
            ThreadSummarySearchFilters,
            SearchFilters(),
        ),
    ]
    for scope, method, expected_type, filters in cases:
        client = make_client()
        getattr(client.graph, method).return_value = SimpleNamespace(items=[])
        tool = create_graph_search_tool(
            client,
            graph_uuid="graph-uuid",
            search_filters=filters,
        )

        await call_tool(tool, query="query", scope=scope)

        filters = getattr(client.graph, method).call_args.kwargs["filters"]
        assert isinstance(filters, expected_type)


@pytest.mark.asyncio
async def test_unsupported_scope_filter_returns_error_string() -> None:
    client = make_client()
    tool = create_graph_search_tool(
        client,
        graph_uuid="graph-uuid",
        search_filters={"node_labels": ["Person"]},
    )

    result = await call_tool(tool, query="query", scope="edges")

    assert "not supported" in result
    client.graph.search_edges.assert_not_called()


@pytest.mark.asyncio
async def test_constructor_filters_and_bfs_origins_are_passed() -> None:
    client = make_client()
    client.graph.search_edges.return_value = SimpleNamespace(items=[])
    tool = create_graph_search_tool(
        client,
        graph_uuid="graph-uuid",
        search_filters={"edge_types": ["WORKS_AT"]},
        bfs_origin_node_uuids=["node-uuid"],
    )

    await call_tool(tool, query="query")

    kwargs = client.graph.search_edges.call_args.kwargs
    assert isinstance(kwargs["filters"], EdgeSearchFilters)
    assert kwargs["filters"].edge_types == ["WORKS_AT"]
    assert kwargs["bfs_origin_node_uuids"] == ["node-uuid"]


@pytest.mark.asyncio
@pytest.mark.parametrize("limit, expected", [(200, MAX_SEARCH_LIMIT), (0, 1), (25, 25)])
async def test_model_limit_is_clamped(limit: int, expected: int) -> None:
    client = make_client()
    client.graph.search_edges.return_value = SimpleNamespace(items=[])
    tool = create_graph_search_tool(client, graph_uuid="graph-uuid")

    await call_tool(tool, query="query", limit=limit)

    assert client.graph.search_edges.call_args.kwargs["page_size"] == expected


@pytest.mark.asyncio
async def test_pinned_limit_is_clamped() -> None:
    client = make_client()
    client.graph.search_edges.return_value = SimpleNamespace(items=[])
    tool = create_graph_search_tool(
        client,
        graph_uuid="graph-uuid",
        pinned_params={"limit": 100},
    )

    await call_tool(tool, query="query")

    assert client.graph.search_edges.call_args.kwargs["page_size"] == MAX_SEARCH_LIMIT


@pytest.mark.asyncio
async def test_hidden_parameter_is_not_sent_to_sdk() -> None:
    client = make_client()
    client.graph.search_edges.return_value = SimpleNamespace(items=[])
    tool = create_graph_search_tool(
        client,
        graph_uuid="graph-uuid",
        hidden_params={"mmr_lambda"},
    )

    await call_tool(tool, query="query", mmr_lambda=0.5)

    assert "mmr_lambda" not in client.graph.search_edges.call_args.kwargs


@pytest.mark.asyncio
async def test_hidden_limit_uses_sdk_default() -> None:
    client = make_client()
    client.graph.search_edges.return_value = SimpleNamespace(items=[])
    tool = create_graph_search_tool(
        client,
        graph_uuid="graph-uuid",
        hidden_params={"limit"},
    )

    await call_tool(tool, query="query")

    assert "page_size" not in client.graph.search_edges.call_args.kwargs


@pytest.mark.asyncio
async def test_query_is_truncated_and_empty_query_returns_error() -> None:
    client = make_client()
    client.graph.search_edges.return_value = SimpleNamespace(items=[])
    tool = create_graph_search_tool(client, graph_uuid="graph-uuid")

    await call_tool(tool, query="x" * 1000)
    assert len(client.graph.search_edges.call_args.kwargs["query"]) == 400
    assert "error" in (await call_tool(tool, query="")).lower()


@pytest.mark.asyncio
async def test_sdk_error_returns_string() -> None:
    client = make_client()
    client.graph.search_edges.side_effect = RuntimeError("offline")
    tool = create_graph_search_tool(client, graph_uuid="graph-uuid")

    result = await call_tool(tool, query="query")

    assert "failed" in result.lower()


@pytest.mark.asyncio
async def test_empty_search_results_are_reported() -> None:
    client = make_client()
    client.graph.search_edges.return_value = SimpleNamespace(items=[])
    tool = create_graph_search_tool(client, graph_uuid="graph-uuid")

    assert await call_tool(tool, query="query") == "No results found."
