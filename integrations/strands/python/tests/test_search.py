"""Tests for pin-or-expose ``create_zep_search_tool``."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from zep_strands import create_zep_search_tool
from zep_strands.search import _filters_for_scope, _format_results, _search_graph


def _make_client() -> MagicMock:
    client = MagicMock()
    client.graph = MagicMock()
    client.user = MagicMock()
    client.user.get = AsyncMock(return_value=SimpleNamespace(graph_uuid="g1"))
    client.graph.get_context = AsyncMock(return_value=SimpleNamespace(context="auto context"))
    client.graph.search_edges = AsyncMock(
        return_value=SimpleNamespace(items=[SimpleNamespace(fact="a fact")])
    )
    return client


class TestCreateZepSearchTool:
    def test_requires_exactly_one_scope(self) -> None:
        client = _make_client()
        with pytest.raises(ValueError, match="user_uuid or graph_uuid"):
            create_zep_search_tool(zep_client=client)
        with pytest.raises(ValueError, match="Only one"):
            create_zep_search_tool(zep_client=client, user_uuid="u", graph_uuid="g")

    def test_rejects_unknown_pinned_params(self) -> None:
        with pytest.raises(ValueError, match="Unknown pinned"):
            create_zep_search_tool(
                zep_client=_make_client(),
                user_uuid="u",
                search_pinned_params={"bogus": 1},
            )

    def test_rejects_unknown_hidden_params(self) -> None:
        with pytest.raises(ValueError, match="Unknown hidden"):
            create_zep_search_tool(
                zep_client=_make_client(),
                user_uuid="u",
                search_hidden_params={"bogus"},
            )

    @pytest.mark.asyncio
    async def test_tool_calls_graph_search(self) -> None:
        client = _make_client()
        tool = create_zep_search_tool(
            zep_client=client,
            user_uuid="u1",
            search_pinned_params={"scope": "edges", "limit": 5},
        )
        # Strands DecoratedFunctionTool exposes the underlying callable via
        # various attributes depending on version; exercise through stream
        # or direct function if available.
        fn = getattr(tool, "original_function", None) or getattr(tool, "_func", None)
        if fn is None:
            # Fall back: tool may be the decorated function itself.
            fn = tool
        result = await fn(query="hiking")
        assert "a fact" in result or result == "auto context" or "fact" in result
        client.graph.search_edges.assert_awaited_once()
        args = client.graph.search_edges.await_args
        assert args.args[0] == "g1"
        kwargs = args.kwargs
        assert kwargs["query"] == "hiking"
        assert kwargs["page_size"] == 5

    @pytest.mark.asyncio
    async def test_tool_returns_error_string_on_failure(self) -> None:
        client = _make_client()
        client.graph.search_edges.side_effect = RuntimeError("network down")
        tool = create_zep_search_tool(zep_client=client, user_uuid="u1")
        fn = getattr(tool, "original_function", None) or getattr(tool, "_func", None) or tool
        result = await fn(query="x")
        assert result == "Graph search failed."

    @pytest.mark.asyncio
    async def test_tool_caches_user_graph_uuid(self) -> None:
        client = _make_client()
        tool = create_zep_search_tool(zep_client=client, user_uuid="u1")
        fn = getattr(tool, "original_function", None) or getattr(tool, "_func", None) or tool

        await fn(query="first")
        await fn(query="second")

        client.user.get.assert_awaited_once_with("u1")
        assert client.graph.search_edges.await_count == 2

    @pytest.mark.asyncio
    async def test_hidden_limit_uses_sdk_default(self) -> None:
        client = _make_client()
        tool = create_zep_search_tool(
            zep_client=client,
            user_uuid="u1",
            search_hidden_params={"limit"},
        )
        fn = getattr(tool, "original_function", None) or getattr(tool, "_func", None) or tool

        await fn(query="hiking")

        assert "page_size" not in client.graph.search_edges.await_args.kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scope", "method"),
    [
        ("edges", "search_edges"),
        ("nodes", "search_nodes"),
        ("episodes", "search_episodes"),
        ("observations", "search_observations"),
        ("thread_summaries", "search_thread_summaries"),
        ("auto", "get_context"),
    ],
)
async def test_search_routes_all_v4_scopes(scope: str, method: str) -> None:
    client = _make_client()
    for name in (
        "search_edges",
        "search_nodes",
        "search_episodes",
        "search_observations",
        "search_thread_summaries",
    ):
        setattr(client.graph, name, AsyncMock(return_value=SimpleNamespace(items=[])))

    await _search_graph(client, "g1", "query", scope=scope, limit=7)

    call = getattr(client.graph, method).await_args
    assert call.args[0] == "g1"
    assert call.kwargs["query"] == "query"
    if scope != "auto":
        assert call.kwargs["page_size"] == 7


def test_search_filters_are_typed_per_scope() -> None:
    from zep_cloud.types.edge_search_filters import EdgeSearchFilters
    from zep_cloud.types.episode_search_filters import EpisodeSearchFilters
    from zep_cloud.types.node_search_filters import NodeSearchFilters
    from zep_cloud.types.search_filters import SearchFilters
    from zep_cloud.types.thread_summary_search_filters import ThreadSummarySearchFilters

    assert isinstance(_filters_for_scope("edges", {}), EdgeSearchFilters)
    assert isinstance(_filters_for_scope("nodes", {}), NodeSearchFilters)
    assert isinstance(_filters_for_scope("episodes", {}), EpisodeSearchFilters)
    assert isinstance(_filters_for_scope("observations", {}), dict)
    assert isinstance(_filters_for_scope("thread_summaries", {}), ThreadSummarySearchFilters)
    assert isinstance(_filters_for_scope("auto", {}), SearchFilters)


@pytest.mark.parametrize(
    ("scope", "result", "expected"),
    [
        (
            "edges",
            SimpleNamespace(items=[SimpleNamespace(fact="Ada likes math")]),
            "- Ada likes math",
        ),
        (
            "nodes",
            SimpleNamespace(items=[SimpleNamespace(name="Ada", summary="mathematician")]),
            "- Ada: mathematician",
        ),
        (
            "episodes",
            SimpleNamespace(items=[SimpleNamespace(content="Ada wrote notes")]),
            "- Ada wrote notes",
        ),
        (
            "observations",
            SimpleNamespace(items=[SimpleNamespace(name="Ada", summary="likes math")]),
            "- Ada: likes math",
        ),
        (
            "thread_summaries",
            SimpleNamespace(items=[SimpleNamespace(summary="Discussed math")]),
            "- Discussed math",
        ),
        ("auto", SimpleNamespace(context="assembled context"), "assembled context"),
    ],
)
def test_format_results_for_all_scopes(scope: str, result: SimpleNamespace, expected: str) -> None:
    assert _format_results(result, scope) == expected
