"""Tests for pin-or-expose ``create_zep_search_tool``."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from zep_strands import create_zep_search_tool


def _make_client() -> MagicMock:
    client = MagicMock()
    client.graph = MagicMock()
    client.graph.search = AsyncMock(
        return_value=SimpleNamespace(
            context="auto context",
            edges=[SimpleNamespace(fact="a fact")],
            nodes=None,
            episodes=None,
            observations=None,
            thread_summaries=None,
        )
    )
    return client


class TestCreateZepSearchTool:
    def test_requires_exactly_one_scope(self) -> None:
        client = _make_client()
        with pytest.raises(ValueError, match="user_id or graph_id"):
            create_zep_search_tool(zep_client=client)
        with pytest.raises(ValueError, match="Only one"):
            create_zep_search_tool(zep_client=client, user_id="u", graph_id="g")

    def test_rejects_unknown_pinned_params(self) -> None:
        with pytest.raises(ValueError, match="Unknown pinned"):
            create_zep_search_tool(
                zep_client=_make_client(),
                user_id="u",
                search_pinned_params={"bogus": 1},
            )

    def test_rejects_unknown_hidden_params(self) -> None:
        with pytest.raises(ValueError, match="Unknown hidden"):
            create_zep_search_tool(
                zep_client=_make_client(),
                user_id="u",
                search_hidden_params={"bogus"},
            )

    @pytest.mark.asyncio
    async def test_tool_calls_graph_search(self) -> None:
        client = _make_client()
        tool = create_zep_search_tool(
            zep_client=client,
            user_id="u1",
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
        client.graph.search.assert_awaited_once()
        kwargs = client.graph.search.await_args.kwargs
        assert kwargs["user_id"] == "u1"
        assert kwargs["query"] == "hiking"
        assert kwargs["scope"] == "edges"
        assert kwargs["limit"] == 5

    @pytest.mark.asyncio
    async def test_tool_returns_error_string_on_failure(self) -> None:
        client = _make_client()
        client.graph.search.side_effect = RuntimeError("network down")
        tool = create_zep_search_tool(zep_client=client, user_id="u1")
        fn = getattr(tool, "original_function", None) or getattr(tool, "_func", None) or tool
        result = await fn(query="x")
        assert "Graph search failed" in result
