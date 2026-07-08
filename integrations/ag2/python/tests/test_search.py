"""
Tests for the pin-or-expose ``create_search_graph_tool`` /
``create_search_memory_tool`` tool schema (BREAKING in this version -- see
the CHANGELOG).

Every ``graph.search`` parameter (``scope``, ``reranker``, ``limit``,
``mmr_lambda``, ``center_node_uuid``) is exposed to the model by default and
can be pinned (fixed to a constant, hidden from the model) or hidden (removed
from the schema without pinning; Zep's own default applies) at construction
time. ``search_filters``/``bfs_origin_node_uuids`` are always constructor-only.

AG2's ``Tool``/``register_for_llm`` derives its schema from the wrapped
function's typed signature (``inspect.signature`` + ``get_type_hints``),
matching autogen's ``FunctionTool`` -- so pin-or-expose is implemented the
same way: exposed params become real, typed parameters of a dynamically-built
signature; pinned/hidden params are never parameters of the function at all.
"""

from __future__ import annotations

import inspect
from typing import get_type_hints
from unittest.mock import AsyncMock, MagicMock

import pytest
from zep_cloud.client import AsyncZep

from zep_ag2 import ZepAG2MemoryError, create_search_graph_tool, create_search_memory_tool


def _make_mock_graph_results(
    edges: list | None = None,
    nodes: list | None = None,
    episodes: list | None = None,
) -> MagicMock:
    r = MagicMock()
    r.edges = edges or []
    r.nodes = nodes or []
    r.episodes = episodes or []
    r.observations = []
    r.thread_summaries = []
    return r


def _mock_zep_client() -> MagicMock:
    client = MagicMock(spec=AsyncZep)
    client.graph = MagicMock()
    client.graph.search = AsyncMock(return_value=_make_mock_graph_results())
    return client


@pytest.fixture
def mock_zep_client() -> MagicMock:
    return _mock_zep_client()


class TestSearchGraphToolExposedByDefault:
    def test_search_tool_exposes_params_by_default(self, mock_zep_client: MagicMock) -> None:
        tool = create_search_graph_tool(mock_zep_client, user_id="u1")
        sig = inspect.signature(tool)

        for param_name in ("scope", "reranker", "limit", "mmr_lambda", "center_node_uuid"):
            assert param_name in sig.parameters, f"{param_name} should be exposed by default"

    def test_search_tool_six_scopes(self, mock_zep_client: MagicMock) -> None:
        tool = create_search_graph_tool(mock_zep_client, user_id="u1")
        hints = get_type_hints(tool, include_extras=True)
        scope_hint = hints["scope"]
        # Annotated[Literal[...], description]
        literal_type = scope_hint.__origin__
        values = set(literal_type.__args__)
        assert values == {
            "edges",
            "nodes",
            "episodes",
            "observations",
            "thread_summaries",
            "auto",
        }

    def test_search_tool_five_rerankers(self, mock_zep_client: MagicMock) -> None:
        tool = create_search_graph_tool(mock_zep_client, user_id="u1")
        hints = get_type_hints(tool, include_extras=True)
        reranker_hint = hints["reranker"]
        literal_type = reranker_hint.__origin__
        values = set(literal_type.__args__)
        assert values == {"rrf", "mmr", "node_distance", "episode_mentions", "cross_encoder"}


class TestSearchGraphToolPinOrExpose:
    def test_search_tool_pinned_params_hidden_and_sent(self, mock_zep_client: MagicMock) -> None:
        tool = create_search_graph_tool(
            mock_zep_client, user_id="u1", pinned_params={"scope": "nodes", "limit": 3}
        )
        sig = inspect.signature(tool)

        assert "scope" not in sig.parameters
        assert "limit" not in sig.parameters

        tool(query="hello")

        call_kwargs = mock_zep_client.graph.search.call_args.kwargs
        assert call_kwargs["scope"] == "nodes"
        assert call_kwargs["limit"] == 3

    def test_search_tool_hidden_params_omitted_from_sdk_call(
        self, mock_zep_client: MagicMock
    ) -> None:
        tool = create_search_graph_tool(mock_zep_client, user_id="u1", hidden_params={"reranker"})
        sig = inspect.signature(tool)

        assert "reranker" not in sig.parameters

        tool(query="hello")

        call_kwargs = mock_zep_client.graph.search.call_args.kwargs
        assert "reranker" not in call_kwargs

    def test_search_tool_query_only_omits_unset_none_default_params(
        self, mock_zep_client: MagicMock
    ) -> None:
        """mmr_lambda / center_node_uuid default to None; when unset by the
        caller they must be OMITTED from the graph.search call, never sent
        as explicit None."""
        tool = create_search_graph_tool(mock_zep_client, user_id="u1")

        tool(query="hello")

        call_kwargs = mock_zep_client.graph.search.call_args.kwargs
        assert "mmr_lambda" not in call_kwargs
        assert "center_node_uuid" not in call_kwargs

    def test_search_tool_legacy_args_pin(self, mock_zep_client: MagicMock) -> None:
        tool = create_search_graph_tool(mock_zep_client, user_id="u1", scope="nodes", limit=7)
        sig = inspect.signature(tool)

        assert "scope" not in sig.parameters
        assert "limit" not in sig.parameters

        tool(query="hello")

        call_kwargs = mock_zep_client.graph.search.call_args.kwargs
        assert call_kwargs["scope"] == "nodes"
        assert call_kwargs["limit"] == 7

    def test_search_tool_unknown_pinned_param_raises(self, mock_zep_client: MagicMock) -> None:
        with pytest.raises(ValueError, match="Unknown pinned"):
            create_search_graph_tool(mock_zep_client, user_id="u1", pinned_params={"bogus": "x"})

    def test_search_tool_unknown_hidden_param_raises(self, mock_zep_client: MagicMock) -> None:
        with pytest.raises(ValueError, match="Unknown hidden"):
            create_search_graph_tool(mock_zep_client, user_id="u1", hidden_params={"bogus"})

    def test_search_tool_constructor_only_search_filters(self, mock_zep_client: MagicMock) -> None:
        tool = create_search_graph_tool(
            mock_zep_client, user_id="u1", search_filters={"node_labels": ["Person"]}
        )
        sig = inspect.signature(tool)
        assert "search_filters" not in sig.parameters

        tool(query="hello")

        call_kwargs = mock_zep_client.graph.search.call_args.kwargs
        assert call_kwargs["search_filters"] == {"node_labels": ["Person"]}

    def test_search_tool_bfs_origin_node_uuids_constructor_only(
        self, mock_zep_client: MagicMock
    ) -> None:
        tool = create_search_graph_tool(
            mock_zep_client, user_id="u1", bfs_origin_node_uuids=["uuid-1"]
        )
        sig = inspect.signature(tool)
        assert "bfs_origin_node_uuids" not in sig.parameters

        tool(query="hello")

        call_kwargs = mock_zep_client.graph.search.call_args.kwargs
        assert call_kwargs["bfs_origin_node_uuids"] == ["uuid-1"]

    def test_search_tool_model_supplied_values_forwarded(self, mock_zep_client: MagicMock) -> None:
        tool = create_search_graph_tool(mock_zep_client, user_id="u1")

        tool(query="hello", scope="episodes", reranker="mmr", limit=20, mmr_lambda=0.5)

        call_kwargs = mock_zep_client.graph.search.call_args.kwargs
        assert call_kwargs["scope"] == "episodes"
        assert call_kwargs["reranker"] == "mmr"
        assert call_kwargs["limit"] == 20
        assert call_kwargs["mmr_lambda"] == 0.5

    def test_search_tool_requires_id(self, mock_zep_client: MagicMock) -> None:
        with pytest.raises(ZepAG2MemoryError):
            create_search_graph_tool(mock_zep_client)

    def test_search_tool_rejects_both_ids(self, mock_zep_client: MagicMock) -> None:
        with pytest.raises(ZepAG2MemoryError):
            create_search_graph_tool(mock_zep_client, user_id="u1", graph_id="g1")

    def test_search_tool_is_sync_callable(self, mock_zep_client: MagicMock) -> None:
        tool = create_search_graph_tool(mock_zep_client, user_id="u1")
        assert not inspect.iscoroutinefunction(tool)
        result = tool(query="hello")
        assert isinstance(result, str)

    def test_search_tool_handler_returns_error_string_never_raises(
        self, mock_zep_client: MagicMock
    ) -> None:
        mock_zep_client.graph.search = AsyncMock(side_effect=Exception("boom"))
        tool = create_search_graph_tool(mock_zep_client, user_id="u1")

        result = tool(query="hello")

        assert isinstance(result, str)
        assert "boom" not in result


class TestSearchMemoryToolPinOrExpose:
    """create_search_memory_tool follows the same pin-or-expose contract."""

    def test_search_memory_tool_exposes_params_by_default(self, mock_zep_client: MagicMock) -> None:
        tool = create_search_memory_tool(mock_zep_client, user_id="u1")
        sig = inspect.signature(tool)

        for param_name in ("scope", "reranker", "limit", "mmr_lambda", "center_node_uuid"):
            assert param_name in sig.parameters

    def test_search_memory_tool_six_scopes(self, mock_zep_client: MagicMock) -> None:
        tool = create_search_memory_tool(mock_zep_client, user_id="u1")
        hints = get_type_hints(tool, include_extras=True)
        literal_type = hints["scope"].__origin__
        assert set(literal_type.__args__) == {
            "edges",
            "nodes",
            "episodes",
            "observations",
            "thread_summaries",
            "auto",
        }

    def test_search_memory_tool_pinned_params_hidden_and_sent(
        self, mock_zep_client: MagicMock
    ) -> None:
        tool = create_search_memory_tool(
            mock_zep_client, user_id="u1", pinned_params={"scope": "nodes"}
        )
        sig = inspect.signature(tool)
        assert "scope" not in sig.parameters

        tool(query="hello")

        call_kwargs = mock_zep_client.graph.search.call_args.kwargs
        assert call_kwargs["scope"] == "nodes"

    def test_search_memory_tool_query_only_omits_unset_none_default_params(
        self, mock_zep_client: MagicMock
    ) -> None:
        tool = create_search_memory_tool(mock_zep_client, user_id="u1")

        tool(query="hello")

        call_kwargs = mock_zep_client.graph.search.call_args.kwargs
        assert "mmr_lambda" not in call_kwargs
        assert "center_node_uuid" not in call_kwargs

    def test_search_memory_tool_legacy_args_pin(self, mock_zep_client: MagicMock) -> None:
        tool = create_search_memory_tool(mock_zep_client, user_id="u1", limit=9)
        sig = inspect.signature(tool)
        assert "limit" not in sig.parameters

        tool(query="hello")

        call_kwargs = mock_zep_client.graph.search.call_args.kwargs
        assert call_kwargs["limit"] == 9
