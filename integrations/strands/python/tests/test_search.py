"""Tests for pin-or-expose ``create_zep_search_tool``."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from zep_strands import create_zep_search_tool
from zep_strands.search import DEFAULT_MMR_LAMBDA, search_settings_error

USER_UUID = "11111111-1111-1111-1111-111111111111"
GRAPH_UUID = "33333333-3333-3333-3333-333333333333"


def _make_client() -> MagicMock:
    client = MagicMock()
    client.user = MagicMock()
    client.user.get = AsyncMock(
        return_value=SimpleNamespace(uuid_=USER_UUID, graph_uuid=GRAPH_UUID)
    )
    client.graph = MagicMock()
    client.graph.get_context = AsyncMock(return_value=SimpleNamespace(context="auto context"))
    client.graph.search_edges = AsyncMock(
        return_value=SimpleNamespace(items=[SimpleNamespace(fact="a fact", uuid_="e1")])
    )
    return client


class TestCreateZepSearchTool:
    def test_requires_exactly_one_scope(self) -> None:
        client = _make_client()
        with pytest.raises(ValueError, match="user_uuid or graph_uuid"):
            create_zep_search_tool(zep_client=client)
        with pytest.raises(ValueError, match="Only one"):
            create_zep_search_tool(zep_client=client, user_uuid=USER_UUID, graph_uuid=GRAPH_UUID)

    def test_rejects_unknown_pinned_params(self) -> None:
        with pytest.raises(ValueError, match="Unknown pinned"):
            create_zep_search_tool(
                zep_client=_make_client(),
                user_uuid=USER_UUID,
                search_pinned_params={"bogus": 1},
            )

    def test_rejects_unknown_hidden_params(self) -> None:
        with pytest.raises(ValueError, match="Unknown hidden"):
            create_zep_search_tool(
                zep_client=_make_client(),
                user_uuid=USER_UUID,
                search_hidden_params={"bogus"},
            )

    @pytest.mark.asyncio
    async def test_tool_calls_graph_search(self) -> None:
        client = _make_client()
        tool = create_zep_search_tool(
            zep_client=client,
            user_uuid=USER_UUID,
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
        assert "a fact" in result
        client.graph.search_edges.assert_awaited_once()
        args, kwargs = client.graph.search_edges.await_args
        assert args[0] == GRAPH_UUID
        assert kwargs["query"] == "hiking"
        assert kwargs["page_size"] == 5

    @pytest.mark.asyncio
    async def test_tool_auto_scope_uses_get_context(self) -> None:
        client = _make_client()
        tool = create_zep_search_tool(
            zep_client=client,
            graph_uuid=GRAPH_UUID,
            search_pinned_params={"scope": "auto"},
        )
        fn = getattr(tool, "original_function", None) or getattr(tool, "_func", None) or tool
        result = await fn(query="hiking")
        assert result == "auto context"
        args, _ = client.graph.get_context.await_args
        assert args[0] == GRAPH_UUID
        client.user.get.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_tool_returns_error_string_on_failure(self) -> None:
        client = _make_client()
        client.graph.search_edges.side_effect = RuntimeError("network down")
        tool = create_zep_search_tool(zep_client=client, user_uuid=USER_UUID)
        fn = getattr(tool, "original_function", None) or getattr(tool, "_func", None) or tool
        result = await fn(query="x")
        assert "Graph search failed" in result


def _fn(tool: object) -> object:
    return getattr(tool, "original_function", None) or getattr(tool, "_func", None) or tool


class TestSearchSettingsValidation:
    @pytest.mark.parametrize(
        ("scope", "reranker"),
        [
            ("nodes", "episode_mentions"),
            ("episodes", "node_distance"),
            ("episodes", "episode_mentions"),
            ("episodes", "cross_encoder"),
            ("observations", "node_distance"),
            ("observations", "episode_mentions"),
            ("thread_summaries", "node_distance"),
            ("thread_summaries", "episode_mentions"),
        ],
    )
    def test_unsupported_pair_is_reported(self, scope: str, reranker: str) -> None:
        error = search_settings_error(scope, reranker, center_node_uuid="n1")
        assert error is not None
        assert f"not supported for scope {scope!r}" in error
        assert "Use one of:" in error

    def test_node_distance_requires_center_node(self) -> None:
        error = search_settings_error("edges", "node_distance")
        assert error is not None and "center_node_uuid" in error
        assert search_settings_error("edges", "node_distance", center_node_uuid="n1") is None

    def test_episode_mentions_requires_episode_uuids(self) -> None:
        error = search_settings_error("edges", "episode_mentions")
        assert error is not None and "episode_uuids" in error
        assert (
            search_settings_error("edges", "episode_mentions", filters={"episode_uuids": ["e1"]})
            is None
        )

    def test_auto_accepts_any_reranker(self) -> None:
        assert search_settings_error("auto", "episode_mentions") is None

    def test_reranker_enum_hides_unusable_rerankers(self) -> None:
        tool = create_zep_search_tool(zep_client=_make_client(), user_uuid=USER_UUID)
        props = tool.tool_spec["inputSchema"]["json"]["properties"]
        assert props["reranker"]["enum"] == ["rrf", "mmr", "node_distance", "cross_encoder"]
        assert "episodes: rrf, mmr" in tool.tool_spec["description"]

    def test_reranker_enum_follows_pinned_scope(self) -> None:
        tool = create_zep_search_tool(
            zep_client=_make_client(),
            user_uuid=USER_UUID,
            search_pinned_params={"scope": "episodes"},
        )
        props = tool.tool_spec["inputSchema"]["json"]["properties"]
        assert props["reranker"]["enum"] == ["rrf", "mmr"]

    def test_scope_enum_follows_pinned_reranker(self) -> None:
        tool = create_zep_search_tool(
            zep_client=_make_client(),
            user_uuid=USER_UUID,
            search_pinned_params={"reranker": "cross_encoder"},
        )
        props = tool.tool_spec["inputSchema"]["json"]["properties"]
        assert props["scope"]["enum"] == [
            "edges",
            "nodes",
            "observations",
            "thread_summaries",
            "auto",
        ]

    def test_episode_mentions_is_offered_with_episode_filter(self) -> None:
        tool = create_zep_search_tool(
            zep_client=_make_client(),
            user_uuid=USER_UUID,
            search_filters={"episode_uuids": ["e1"]},
        )
        props = tool.tool_spec["inputSchema"]["json"]["properties"]
        assert "episode_mentions" in props["reranker"]["enum"]

    def test_pinned_invalid_pair_raises(self) -> None:
        with pytest.raises(ValueError, match="not supported for scope 'episodes'"):
            create_zep_search_tool(
                zep_client=_make_client(),
                user_uuid=USER_UUID,
                search_pinned_params={"scope": "episodes", "reranker": "cross_encoder"},
            )

    def test_pinned_node_distance_without_center_node_raises(self) -> None:
        with pytest.raises(ValueError, match="center_node_uuid"):
            create_zep_search_tool(
                zep_client=_make_client(),
                user_uuid=USER_UUID,
                search_pinned_params={"scope": "edges", "reranker": "node_distance"},
                search_hidden_params={"center_node_uuid"},
            )

    @pytest.mark.asyncio
    async def test_model_invalid_pair_returns_clear_error(self) -> None:
        client = _make_client()
        client.graph.search_episodes = AsyncMock()
        tool = create_zep_search_tool(zep_client=client, user_uuid=USER_UUID)
        result = await _fn(tool)(query="x", scope="episodes", reranker="cross_encoder")  # type: ignore[operator]
        assert result.startswith("Error: reranker 'cross_encoder' is not supported")
        assert "Use one of: rrf, mmr" in result
        client.graph.search_episodes.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_model_node_distance_without_center_returns_clear_error(self) -> None:
        client = _make_client()
        tool = create_zep_search_tool(zep_client=client, user_uuid=USER_UUID)
        result = await _fn(tool)(query="x", scope="edges", reranker="node_distance")  # type: ignore[operator]
        assert "requires center_node_uuid" in result
        client.graph.search_edges.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mmr_without_lambda_sends_default(self) -> None:
        client = _make_client()
        tool = create_zep_search_tool(zep_client=client, user_uuid=USER_UUID)
        await _fn(tool)(query="x", scope="edges", reranker="mmr")  # type: ignore[operator]
        _, kwargs = client.graph.search_edges.await_args
        assert kwargs["reranker"] == "mmr"
        assert kwargs["mmr_lambda"] == DEFAULT_MMR_LAMBDA

    @pytest.mark.asyncio
    async def test_mmr_keeps_given_lambda(self) -> None:
        client = _make_client()
        tool = create_zep_search_tool(zep_client=client, user_uuid=USER_UUID)
        await _fn(tool)(query="x", scope="edges", reranker="mmr", mmr_lambda=0.9)  # type: ignore[operator]
        _, kwargs = client.graph.search_edges.await_args
        assert kwargs["mmr_lambda"] == 0.9
