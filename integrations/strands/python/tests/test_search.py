"""Tests for pin-or-expose ``create_zep_search_tool``."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from zep_strands import create_zep_search_tool

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
        assert kwargs["limit"] == 5

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
