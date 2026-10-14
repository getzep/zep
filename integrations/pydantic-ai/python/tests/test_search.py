"""Tests for the V4 graph-search tool and its model-facing schema."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic_ai import Tool
from zep_cloud.types.edge_search_filters import EdgeSearchFilters
from zep_cloud.types.episode_search_filters import EpisodeSearchFilters
from zep_cloud.types.node_search_filters import NodeSearchFilters
from zep_cloud.types.search_filters import SearchFilters
from zep_cloud.types.thread_summary_search_filters import ThreadSummarySearchFilters

from zep_pydantic_ai import ZepDeps, create_zep_search_tool


def _client() -> MagicMock:
    client = MagicMock()
    for method in (
        "search_edges",
        "search_nodes",
        "search_episodes",
        "search_observations",
        "search_thread_summaries",
    ):
        setattr(client.graph, method, AsyncMock(return_value=SimpleNamespace(items=[])))
    client.graph.get_context = AsyncMock(return_value=SimpleNamespace(context="Context"))
    return client


def _ctx(client: MagicMock, **kwargs: object) -> MagicMock:
    ctx = MagicMock()
    ctx.deps = ZepDeps(client=client, user_uuid="user-uuid", thread_uuid="thread-uuid", **kwargs)
    return ctx


async def _call(tool: Tool, ctx: MagicMock, query: str = "query", **kwargs: object) -> str:
    return await tool.function(ctx, query=query, **kwargs)  # type: ignore[no-any-return]


def _item(**kwargs: str | None) -> SimpleNamespace:
    return SimpleNamespace(**kwargs)


@pytest.mark.asyncio
async def test_defaults_to_user_graph_and_edges_scope() -> None:
    client = _client()
    await _call(create_zep_search_tool(), _ctx(client))

    client.graph.search_edges.assert_awaited_once_with(
        "user-uuid",
        query="query",
        reranker="rrf",
        page_size=10,
    )


@pytest.mark.asyncio
async def test_factory_graph_uuid_targets_shared_graph() -> None:
    client = _client()
    await _call(create_zep_search_tool(graph_uuid="shared-graph"), _ctx(client))

    assert client.graph.search_edges.call_args.args == ("shared-graph",)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scope", "method"),
    [
        ("edges", "search_edges"),
        ("nodes", "search_nodes"),
        ("episodes", "search_episodes"),
        ("observations", "search_observations"),
        ("thread_summaries", "search_thread_summaries"),
    ],
)
async def test_scope_routes_to_v4_search_method(scope: str, method: str) -> None:
    client = _client()
    await _call(create_zep_search_tool(scope=scope), _ctx(client))  # type: ignore[arg-type]

    getattr(client.graph, method).assert_awaited_once()
    for other in {
        "search_edges",
        "search_nodes",
        "search_episodes",
        "search_observations",
        "search_thread_summaries",
    } - {method}:
        getattr(client.graph, other).assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_scope_uses_get_context_and_supported_arguments() -> None:
    client = _client()
    tool = create_zep_search_tool(
        scope="auto",
        reranker="cross_encoder",
        limit=20,
        filters={"node_labels": ["Person"]},
        bfs_origin_node_uuids=["node-uuid"],
    )

    await _call(tool, _ctx(client))

    client.graph.get_context.assert_awaited_once()
    args, kwargs = client.graph.get_context.call_args
    assert args == ("user-uuid",)
    assert kwargs["query"] == "query"
    assert isinstance(kwargs["filters"], SearchFilters)
    assert "page_size" not in kwargs
    assert "reranker" not in kwargs
    assert "bfs_origin_node_uuids" not in kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scope", "model", "filters"),
    [
        ("edges", EdgeSearchFilters, {"edge_types": ["KNOWS"]}),
        ("nodes", NodeSearchFilters, {"node_labels": ["Person"]}),
        ("episodes", EpisodeSearchFilters, {"content_policy_violated": False}),
        ("observations", dict, {"raw_data": "value"}),
        (
            "thread_summaries",
            ThreadSummarySearchFilters,
            {"metadata_filters": {"type": "and", "filters": []}},
        ),
    ],
)
async def test_scope_filters_use_v4_types(
    scope: str, model: type, filters: dict[str, object]
) -> None:
    client = _client()
    await _call(
        create_zep_search_tool(scope=scope, filters=filters),  # type: ignore[arg-type]
        _ctx(client),
    )

    result = getattr(client.graph, f"search_{scope}").call_args.kwargs["filters"]
    if scope == "observations":
        assert isinstance(result, dict)
        assert result == filters
    else:
        assert isinstance(result, model)


@pytest.mark.asyncio
async def test_search_parameters_map_limit_to_page_size_and_include_bfs() -> None:
    client = _client()
    tool = create_zep_search_tool(
        scope="nodes",
        limit=4,
        filters={"node_labels": ["Person"]},
        bfs_origin_node_uuids=["node-uuid"],
    )

    await _call(tool, _ctx(client))

    kwargs = client.graph.search_nodes.call_args.kwargs
    assert kwargs["page_size"] == 4
    assert kwargs["bfs_origin_node_uuids"] == ["node-uuid"]
    assert isinstance(kwargs["filters"], NodeSearchFilters)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("limit", "expected"),
    [(0, 1), (1, 1), (10, 10), (50, 50), (51, 50)],
)
async def test_model_limit_is_clamped_to_supported_range(limit: int, expected: int) -> None:
    client = _client()
    await _call(create_zep_search_tool(), _ctx(client), limit=limit)

    assert client.graph.search_edges.call_args.kwargs["page_size"] == expected


@pytest.mark.asyncio
async def test_legacy_constructor_aliases_are_sent_as_pinned_values() -> None:
    client = _client()
    tool = create_zep_search_tool(scope="nodes", reranker="cross_encoder", limit=3)
    await _call(tool, _ctx(client))

    kwargs = client.graph.search_nodes.call_args.kwargs
    assert kwargs["reranker"] == "cross_encoder"
    assert kwargs["page_size"] == 3


@pytest.mark.asyncio
async def test_model_arguments_are_clamped_and_query_is_truncated() -> None:
    client = _client()
    await _call(
        create_zep_search_tool(),
        _ctx(client),
        "q" * 500,
        limit=1000,
        scope=None,
        mmr_lambda=None,
        center_node_uuid=None,
    )

    kwargs = client.graph.search_edges.call_args.kwargs
    assert len(kwargs["query"]) == 400
    assert kwargs["page_size"] == 50
    assert "mmr_lambda" not in kwargs
    assert "center_node_uuid" not in kwargs


@pytest.mark.asyncio
async def test_empty_query_returns_error_without_sdk_call() -> None:
    client = _client()
    result = await _call(create_zep_search_tool(), _ctx(client), "")

    assert "no search query" in result.lower()
    client.graph.search_edges.assert_not_awaited()


@pytest.mark.asyncio
async def test_search_errors_return_text() -> None:
    client = _client()
    client.graph.search_edges.side_effect = RuntimeError("service unavailable")

    result = await _call(create_zep_search_tool(), _ctx(client))

    assert "service unavailable" in result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scope", "items", "expected"),
    [
        ("edges", [_item(fact="Alice works at Acme")], "Alice works at Acme"),
        ("nodes", [_item(name="Alice", summary="An engineer")], "Alice: An engineer"),
        ("episodes", [_item(content="I work at Acme")], "I work at Acme"),
        (
            "observations",
            [_item(name="Prefers updates", summary="Communication pattern")],
            "Prefers updates: Communication pattern",
        ),
        (
            "thread_summaries",
            [_item(name="thread", summary="Roadmap discussion")],
            "Roadmap discussion",
        ),
    ],
)
async def test_formats_scope_results(
    scope: str, items: list[SimpleNamespace], expected: str
) -> None:
    client = _client()
    getattr(client.graph, f"search_{scope}").return_value = SimpleNamespace(items=items)

    result = await _call(create_zep_search_tool(scope=scope), _ctx(client))  # type: ignore[arg-type]

    assert expected in result


@pytest.mark.asyncio
async def test_formats_auto_context_and_empty_results() -> None:
    client = _client()
    tool = create_zep_search_tool(scope="auto")
    assert await _call(tool, _ctx(client)) == "Context"
    assert "reranker" not in client.graph.get_context.call_args.kwargs

    client.graph.get_context.return_value = SimpleNamespace(context=None)
    assert await _call(tool, _ctx(client)) == "No results found."


def test_pin_and_hide_parameters_in_tool_schema() -> None:
    tool = create_zep_search_tool(
        pinned_params={"scope": "nodes", "limit": 5},
        hidden_params={"mmr_lambda"},
    )
    properties = tool.tool_def.parameters_json_schema["properties"]

    assert "query" in properties
    assert "scope" not in properties
    assert "limit" not in properties
    assert "mmr_lambda" not in properties
    assert "filters" not in properties
    assert "bfs_origin_node_uuids" not in properties


def test_default_schema_exposes_supported_parameters_and_bounds() -> None:
    properties = create_zep_search_tool().tool_def.parameters_json_schema["properties"]

    assert {
        "query",
        "scope",
        "reranker",
        "limit",
        "mmr_lambda",
        "center_node_uuid",
    } <= properties.keys()
    assert properties["limit"]["minimum"] == 1
    assert properties["limit"]["maximum"] == 50
    assert set(properties["scope"]["enum"]) == {
        "edges",
        "nodes",
        "episodes",
        "observations",
        "thread_summaries",
        "auto",
    }
    assert properties["reranker"]["enum"] == [
        "rrf",
        "mmr",
        "node_distance",
        "episode_mentions",
        "cross_encoder",
    ]


@pytest.mark.asyncio
async def test_pinned_parameters_are_sent_to_sdk() -> None:
    client = _client()
    await _call(
        create_zep_search_tool(pinned_params={"scope": "nodes", "limit": 5}),
        _ctx(client),
    )

    kwargs = client.graph.search_nodes.call_args.kwargs
    assert kwargs["page_size"] == 5


@pytest.mark.asyncio
async def test_hidden_parameters_are_omitted_from_sdk_call() -> None:
    client = _client()
    await _call(
        create_zep_search_tool(hidden_params={"mmr_lambda", "center_node_uuid"}),
        _ctx(client),
        mmr_lambda=0.5,
        center_node_uuid="node-uuid",
    )

    kwargs = client.graph.search_edges.call_args.kwargs
    assert "mmr_lambda" not in kwargs
    assert "center_node_uuid" not in kwargs


def test_legacy_constructor_aliases_and_invalid_parameters() -> None:
    tool = create_zep_search_tool(scope="nodes", reranker="cross_encoder", limit=3)
    properties = tool.tool_def.parameters_json_schema["properties"]
    assert "scope" not in properties
    assert "reranker" not in properties
    assert "limit" not in properties

    with pytest.raises(ValueError, match="Unknown pinned parameters"):
        create_zep_search_tool(pinned_params={"user_uuid": "user"})
    with pytest.raises(ValueError, match="Unknown hidden parameters"):
        create_zep_search_tool(hidden_params={"filters"})


def test_factory_returns_pydantic_ai_tool() -> None:
    tool = create_zep_search_tool(name="search_memory")
    assert isinstance(tool, Tool)
    assert tool.name == "search_memory"
    assert tool.takes_ctx is True
