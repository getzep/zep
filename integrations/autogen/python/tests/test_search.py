from types import SimpleNamespace
from typing import get_origin
from unittest.mock import AsyncMock, MagicMock

import pytest
from autogen_core import CancellationToken
from zep_cloud import SearchFilters
from zep_cloud.client import AsyncZep
from zep_cloud.types import (
    EdgeSearchFilters,
    EpisodeSearchFilters,
    NodeSearchFilters,
    ObservationSearchFilters,
    ThreadSummarySearchFilters,
)

from zep_autogen.tools import create_add_graph_data_tool, create_search_graph_tool

SCOPE_METHODS = {
    "edges": ("search_edges", EdgeSearchFilters),
    "nodes": ("search_nodes", NodeSearchFilters),
    "episodes": ("search_episodes", EpisodeSearchFilters),
    "observations": ("search_observations", ObservationSearchFilters),
    "thread_summaries": ("search_thread_summaries", ThreadSummarySearchFilters),
}


def _make_client() -> MagicMock:
    client = MagicMock(spec=AsyncZep)
    client.graph = MagicMock()
    for method_name, _ in SCOPE_METHODS.values():
        setattr(
            client.graph,
            method_name,
            AsyncMock(return_value=SimpleNamespace(items=[])),
        )
    client.graph.get_context = AsyncMock(
        return_value=SimpleNamespace(context="Assembled context block")
    )
    return client


async def _run(tool, **kwargs: object) -> str:
    return await tool.run_json(kwargs, CancellationToken())


def test_search_schema_exposes_v4_scopes_and_controls() -> None:
    tool = create_search_graph_tool(_make_client(), user_uuid="user-1")
    properties = tool.schema["parameters"]["properties"]

    assert set(properties["scope"]["enum"]) == {
        "edges",
        "nodes",
        "episodes",
        "observations",
        "thread_summaries",
        "auto",
    }
    assert set(properties["reranker"]["enum"]) == {
        "rrf",
        "mmr",
        "node_distance",
        "episode_mentions",
        "cross_encoder",
    }
    assert {"query", "limit", "mmr_lambda", "center_node_uuid"} <= set(properties)
    assert tool.schema["parameters"]["required"] == ["query"]


@pytest.mark.asyncio
async def test_default_search_calls_edges_with_v4_arguments() -> None:
    client = _make_client()
    tool = create_search_graph_tool(client, user_uuid="user-1")

    await _run(tool, query="query")

    kwargs = client.graph.search_edges.call_args.kwargs
    assert client.graph.search_edges.call_args.args == ("user-1",)
    assert kwargs["query"] == "query"
    assert kwargs["page_size"] == 10
    assert kwargs["reranker"] == "rrf"
    assert "mmr_lambda" not in kwargs
    assert "center_node_uuid" not in kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scope", "method_name", "filter_type"),
    [
        (scope, method_name, filter_type)
        for scope, (method_name, filter_type) in SCOPE_METHODS.items()
    ],
)
async def test_each_search_scope_uses_its_sdk_method_and_typed_filter(
    scope: str, method_name: str, filter_type: type
) -> None:
    client = _make_client()
    tool = create_search_graph_tool(
        client,
        graph_uuid="graph-1",
        scope=scope,
        search_filters=SearchFilters(
            edge_types=["LIKES"],
            node_labels=["Person"],
            content_policy_violated=True,
        ),
    )

    await _run(tool, query="query")

    method = getattr(client.graph, method_name)
    assert method.call_args.args == ("graph-1",)
    filters = method.call_args.kwargs["filters"]
    if get_origin(filter_type) is dict:
        assert filters == {
            "edge_types": ["LIKES"],
            "node_labels": ["Person"],
            "content_policy_violated": True,
        }
    else:
        assert isinstance(filters, filter_type)
        fields = filter_type.model_fields
        if "node_labels" in fields:
            assert filters.node_labels == ["Person"]
        if "edge_types" in fields:
            assert filters.edge_types == ["LIKES"]
        if "content_policy_violated" in fields:
            assert filters.content_policy_violated is True
        assert "node_labels" in fields or scope != "nodes"
        assert "edge_types" in fields or scope != "edges"
        assert "content_policy_violated" in fields or scope != "episodes"
    assert method.call_args.kwargs["page_size"] == 10


@pytest.mark.asyncio
async def test_auto_scope_uses_context_without_search_only_parameters() -> None:
    client = _make_client()
    tool = create_search_graph_tool(
        client,
        user_uuid="user-1",
        pinned_params={
            "scope": "auto",
            "limit": 4,
            "reranker": "node_distance",
            "mmr_lambda": 0.5,
            "center_node_uuid": "node-1",
        },
    )

    result = await _run(tool, query="query")

    assert result == "Assembled context block"
    client.graph.get_context.assert_awaited_once_with("user-1", query="query")


@pytest.mark.asyncio
async def test_auto_scope_passes_supported_search_filters() -> None:
    client = _make_client()
    filters = SearchFilters(node_labels=["Person"])
    tool = create_search_graph_tool(
        client,
        user_uuid="user-1",
        pinned_params={"scope": "auto"},
        search_filters=filters,
    )

    await _run(tool, query="query")

    client.graph.get_context.assert_awaited_once_with(
        "user-1",
        query="query",
        filters={"node_labels": ["Person"]},
    )


@pytest.mark.asyncio
async def test_model_selected_auto_scope_omits_search_only_parameters() -> None:
    client = _make_client()
    tool = create_search_graph_tool(client, user_uuid="user-1")

    await _run(
        tool,
        query="query",
        scope="auto",
        reranker="node_distance",
        mmr_lambda=0.5,
        center_node_uuid="node-1",
        limit=4,
    )

    client.graph.get_context.assert_awaited_once_with("user-1", query="query")


def test_pinned_and_hidden_parameters_are_removed_from_schema() -> None:
    tool = create_search_graph_tool(
        _make_client(),
        user_uuid="user-1",
        pinned_params={"scope": "nodes", "limit": 5},
        hidden_params={"mmr_lambda", "center_node_uuid"},
    )

    properties = tool.schema["parameters"]["properties"]
    assert "scope" not in properties
    assert "limit" not in properties
    assert "mmr_lambda" not in properties
    assert "center_node_uuid" not in properties


@pytest.mark.asyncio
async def test_pinned_and_hidden_parameters_control_sdk_call() -> None:
    client = _make_client()
    tool = create_search_graph_tool(
        client,
        user_uuid="user-1",
        pinned_params={"scope": "nodes", "limit": 5},
        hidden_params={"reranker"},
    )

    await _run(tool, query="query")

    kwargs = client.graph.search_nodes.call_args.kwargs
    assert kwargs["page_size"] == 5
    assert "reranker" not in kwargs


@pytest.mark.asyncio
async def test_legacy_scope_and_limit_arguments_pin_parameters() -> None:
    client = _make_client()
    tool = create_search_graph_tool(client, user_uuid="user-1", scope="nodes", limit=3)

    await _run(tool, query="query")

    properties = tool.schema["parameters"]["properties"]
    assert "scope" not in properties
    assert "limit" not in properties
    assert client.graph.search_nodes.call_args.kwargs["page_size"] == 3


def test_search_filters_and_bfs_origins_are_constructor_only() -> None:
    tool = create_search_graph_tool(
        _make_client(),
        user_uuid="user-1",
        search_filters=SearchFilters(node_labels=["Person"]),
        bfs_origin_node_uuids=["node-1"],
    )

    properties = tool.schema["parameters"]["properties"]
    assert "search_filters" not in properties
    assert "bfs_origin_node_uuids" not in properties


@pytest.mark.asyncio
async def test_search_filters_and_bfs_origins_reach_scope_method() -> None:
    client = _make_client()
    tool = create_search_graph_tool(
        client,
        user_uuid="user-1",
        search_filters=SearchFilters(node_labels=["Person"]),
        bfs_origin_node_uuids=["node-1"],
    )

    await _run(tool, query="query")

    kwargs = client.graph.search_edges.call_args.kwargs
    assert isinstance(kwargs["filters"], EdgeSearchFilters)
    assert kwargs["bfs_origin_node_uuids"] == ["node-1"]


def test_tool_requires_exactly_one_uuid_scope() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        create_search_graph_tool(_make_client())
    with pytest.raises(ValueError, match="exactly one"):
        create_search_graph_tool(_make_client(), graph_uuid="graph-1", user_uuid="user-1")


def test_tool_rejects_unknown_pinned_or_hidden_parameters() -> None:
    with pytest.raises(ValueError, match="Unknown search parameter"):
        create_search_graph_tool(_make_client(), user_uuid="user-1", pinned_params={"other": 1})
    with pytest.raises(ValueError, match="Unknown search parameter"):
        create_search_graph_tool(_make_client(), user_uuid="user-1", hidden_params={"other"})


@pytest.mark.asyncio
async def test_search_limit_is_clamped() -> None:
    client = _make_client()
    tool = create_search_graph_tool(client, user_uuid="user-1")
    await _run(tool, query="query", limit=200)
    assert client.graph.search_edges.call_args.kwargs["page_size"] == 50


@pytest.mark.asyncio
async def test_search_limit_is_clamped_to_at_least_one() -> None:
    client = _make_client()
    tool = create_search_graph_tool(client, user_uuid="user-1")

    await _run(tool, query="query", limit=0)

    assert client.graph.search_edges.call_args.kwargs["page_size"] == 1


@pytest.mark.asyncio
async def test_pinned_search_limit_is_clamped(caplog: pytest.LogCaptureFixture) -> None:
    client = _make_client()
    with caplog.at_level("WARNING", logger="zep_autogen.tools"):
        tool = create_search_graph_tool(
            client,
            user_uuid="user-1",
            pinned_params={"limit": 200},
        )

    await _run(tool, query="query")

    assert client.graph.search_edges.call_args.kwargs["page_size"] == 50
    assert "Clamped pinned search limit" in caplog.text


@pytest.mark.asyncio
async def test_empty_query_does_not_call_sdk() -> None:
    client = _make_client()
    tool = create_search_graph_tool(client, user_uuid="user-1")

    result = await _run(tool, query="   ")

    assert "query" in result.lower()
    client.graph.search_edges.assert_not_awaited()


@pytest.mark.asyncio
async def test_formats_scope_results_from_page_items() -> None:
    examples = [
        ("edges", SimpleNamespace(fact="A fact."), "A fact."),
        ("nodes", SimpleNamespace(name="Alice", summary="A user"), "Alice: A user"),
        ("episodes", SimpleNamespace(content="An episode."), "An episode."),
        (
            "observations",
            SimpleNamespace(name="Preference", summary="Likes hiking"),
            "Preference: Likes hiking",
        ),
        (
            "thread_summaries",
            SimpleNamespace(summary="Discussed hiking"),
            "Discussed hiking",
        ),
    ]

    for scope, item, expected in examples:
        client = _make_client()
        method_name = SCOPE_METHODS[scope][0]
        getattr(client.graph, method_name).return_value = SimpleNamespace(items=[item])
        tool = create_search_graph_tool(client, user_uuid="user-1", scope=scope)

        assert expected in await _run(tool, query="query")


@pytest.mark.asyncio
async def test_empty_search_results_have_clear_response() -> None:
    tool = create_search_graph_tool(_make_client(), user_uuid="user-1")

    assert await _run(tool, query="query") == "No results found."


@pytest.mark.asyncio
async def test_search_errors_return_gracefully() -> None:
    client = _make_client()
    client.graph.search_edges.side_effect = RuntimeError("unavailable")
    tool = create_search_graph_tool(client, user_uuid="user-1")

    assert await _run(tool, query="query") == "Graph search failed."


@pytest.mark.asyncio
async def test_add_tool_errors_return_gracefully() -> None:
    client = _make_client()
    client.graph.episode = MagicMock()
    client.graph.episode.add = AsyncMock(side_effect=RuntimeError("unavailable"))
    tool = create_add_graph_data_tool(client, user_uuid="user-1")

    result = await tool.run_json({"data": "fact"}, CancellationToken())

    assert result["success"] is False


def test_add_tool_requires_exactly_one_uuid_scope() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        create_add_graph_data_tool(_make_client())
    with pytest.raises(ValueError, match="exactly one"):
        create_add_graph_data_tool(_make_client(), graph_uuid="graph-1", user_uuid="user-1")
