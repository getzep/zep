"""
Tests for the pin-or-expose ``create_search_graph_tool`` /
``create_search_memory_tool`` tool schema (BREAKING in this version -- see
the CHANGELOG).

Every search parameter (``scope``, ``reranker``, ``limit``, ``mmr_lambda``,
``center_node_uuid``) is exposed to the model by default and can be pinned
(fixed to a constant, hidden from the model) or hidden (removed from the
schema without pinning; Zep's own default applies) at construction time.
``filters``/``bfs_origin_node_uuids`` are always constructor-only.

Zep v4 has one search method for each scope, so the ``scope`` selects the SDK
method instead of becoming a request parameter.

AG2's ``Tool``/``register_for_llm`` derives its schema from the wrapped
function's typed signature (``inspect.signature`` + ``get_type_hints``),
matching autogen's ``FunctionTool`` -- so pin-or-expose is implemented the
same way: exposed params become real, typed parameters of a dynamically-built
signature; pinned/hidden params are never parameters of the function at all.
"""

from __future__ import annotations

import inspect
import logging
from typing import get_args, get_type_hints
from unittest.mock import MagicMock

import pytest
from zep_cloud.client import Zep

from zep_ag2 import ZepAG2MemoryError, create_search_graph_tool, create_search_memory_tool

GRAPH_UUID = "graph-uuid-1"


def _make_pager(items: list | None = None) -> MagicMock:
    pager = MagicMock()
    pager.items = items or []
    return pager


def _mock_zep_client() -> MagicMock:
    client = MagicMock(spec=Zep)
    client.graph = MagicMock()
    client.graph.search_edges = MagicMock(return_value=_make_pager())
    client.graph.search_nodes = MagicMock(return_value=_make_pager())
    client.graph.search_episodes = MagicMock(return_value=_make_pager())
    client.graph.search_observations = MagicMock(return_value=_make_pager())
    client.graph.search_thread_summaries = MagicMock(return_value=_make_pager())
    client.graph.get_context = MagicMock(return_value=MagicMock(context=""))
    return client


@pytest.fixture
def mock_zep_client() -> MagicMock:
    return _mock_zep_client()


def _choices(tool: object, name: str) -> set[str]:
    hints = get_type_hints(tool, include_extras=True)
    return set(hints[name].__origin__.__args__)


class TestSearchGraphToolExposedByDefault:
    def test_search_tool_exposes_params_by_default(self, mock_zep_client: MagicMock) -> None:
        tool = create_search_graph_tool(mock_zep_client, GRAPH_UUID)
        sig = inspect.signature(tool)

        for param_name in ("scope", "reranker", "limit", "mmr_lambda", "center_node_uuid"):
            assert param_name in sig.parameters, f"{param_name} should be exposed by default"

    def test_search_tool_six_scopes(self, mock_zep_client: MagicMock) -> None:
        tool = create_search_graph_tool(mock_zep_client, GRAPH_UUID)
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

    def test_search_tool_rerankers_without_episode_uuids(self, mock_zep_client: MagicMock) -> None:
        tool = create_search_graph_tool(mock_zep_client, GRAPH_UUID)
        assert _choices(tool, "reranker") == {"rrf", "mmr", "node_distance", "cross_encoder"}

    def test_search_tool_five_rerankers_with_episode_uuids(
        self, mock_zep_client: MagicMock
    ) -> None:
        tool = create_search_graph_tool(
            mock_zep_client, GRAPH_UUID, filters={"episode_uuids": ["ep-1"]}
        )
        assert _choices(tool, "reranker") == {
            "rrf",
            "mmr",
            "node_distance",
            "episode_mentions",
            "cross_encoder",
        }


class TestSearchGraphToolPinOrExpose:
    def test_search_tool_pinned_params_hidden_and_sent(self, mock_zep_client: MagicMock) -> None:
        tool = create_search_graph_tool(
            mock_zep_client, GRAPH_UUID, pinned_params={"scope": "nodes", "limit": 3}
        )
        sig = inspect.signature(tool)

        assert "scope" not in sig.parameters
        assert "limit" not in sig.parameters

        tool(query="hello")

        call_kwargs = mock_zep_client.graph.search_nodes.call_args.kwargs
        assert call_kwargs["page_size"] == 3
        mock_zep_client.graph.search_edges.assert_not_called()

    def test_search_tool_hidden_params_omitted_from_sdk_call(
        self, mock_zep_client: MagicMock
    ) -> None:
        tool = create_search_graph_tool(mock_zep_client, GRAPH_UUID, hidden_params={"reranker"})
        sig = inspect.signature(tool)

        assert "reranker" not in sig.parameters

        tool(query="hello")

        call_kwargs = mock_zep_client.graph.search_edges.call_args.kwargs
        assert "reranker" not in call_kwargs

    def test_search_tool_query_only_omits_unset_none_default_params(
        self, mock_zep_client: MagicMock
    ) -> None:
        """mmr_lambda / center_node_uuid default to None; when unset by the
        caller they must be OMITTED from the graph.search call, never sent
        as explicit None."""
        tool = create_search_graph_tool(mock_zep_client, GRAPH_UUID)

        tool(query="hello")

        call_kwargs = mock_zep_client.graph.search_edges.call_args.kwargs
        assert "mmr_lambda" not in call_kwargs
        assert "center_node_uuid" not in call_kwargs

    def test_search_tool_legacy_args_pin(self, mock_zep_client: MagicMock) -> None:
        tool = create_search_graph_tool(mock_zep_client, GRAPH_UUID, scope="nodes", limit=7)
        sig = inspect.signature(tool)

        assert "scope" not in sig.parameters
        assert "limit" not in sig.parameters

        tool(query="hello")

        call_kwargs = mock_zep_client.graph.search_nodes.call_args.kwargs
        assert call_kwargs["page_size"] == 7
        mock_zep_client.graph.search_edges.assert_not_called()

    def test_search_tool_unknown_pinned_param_raises(self, mock_zep_client: MagicMock) -> None:
        with pytest.raises(ValueError, match="Unknown pinned"):
            create_search_graph_tool(mock_zep_client, GRAPH_UUID, pinned_params={"bogus": "x"})

    def test_search_tool_unknown_hidden_param_raises(self, mock_zep_client: MagicMock) -> None:
        with pytest.raises(ValueError, match="Unknown hidden"):
            create_search_graph_tool(mock_zep_client, GRAPH_UUID, hidden_params={"bogus"})

    def test_search_tool_constructor_only_search_filters(self, mock_zep_client: MagicMock) -> None:
        tool = create_search_graph_tool(
            mock_zep_client, GRAPH_UUID, filters={"node_labels": ["Person"]}
        )
        sig = inspect.signature(tool)
        assert "filters" not in sig.parameters

        tool(query="hello")

        call_kwargs = mock_zep_client.graph.search_edges.call_args.kwargs
        assert call_kwargs["filters"] == {"node_labels": ["Person"]}

    def test_search_tool_bfs_origin_node_uuids_constructor_only(
        self, mock_zep_client: MagicMock
    ) -> None:
        tool = create_search_graph_tool(
            mock_zep_client, GRAPH_UUID, bfs_origin_node_uuids=["uuid-1"]
        )
        sig = inspect.signature(tool)
        assert "bfs_origin_node_uuids" not in sig.parameters

        tool(query="hello")

        call_kwargs = mock_zep_client.graph.search_edges.call_args.kwargs
        assert call_kwargs["bfs_origin_node_uuids"] == ["uuid-1"]

    def test_search_tool_model_supplied_values_forwarded(self, mock_zep_client: MagicMock) -> None:
        tool = create_search_graph_tool(mock_zep_client, GRAPH_UUID)

        tool(query="hello", scope="episodes", reranker="mmr", limit=20, mmr_lambda=0.5)

        call_kwargs = mock_zep_client.graph.search_episodes.call_args.kwargs
        assert call_kwargs["reranker"] == "mmr"
        assert call_kwargs["page_size"] == 20
        assert call_kwargs["mmr_lambda"] == 0.5

    def test_search_tool_requires_graph_uuid(self, mock_zep_client: MagicMock) -> None:
        with pytest.raises(ZepAG2MemoryError):
            create_search_graph_tool(mock_zep_client, "")

    def test_search_tool_is_sync_callable(self, mock_zep_client: MagicMock) -> None:
        tool = create_search_graph_tool(mock_zep_client, GRAPH_UUID)
        assert not inspect.iscoroutinefunction(tool)
        result = tool(query="hello")
        assert isinstance(result, str)

    def test_search_tool_handler_returns_error_string_never_raises(
        self, mock_zep_client: MagicMock
    ) -> None:
        mock_zep_client.graph.search_edges = MagicMock(side_effect=Exception("boom"))
        tool = create_search_graph_tool(mock_zep_client, GRAPH_UUID)

        result = tool(query="hello")

        assert isinstance(result, str)
        assert "boom" not in result


class TestSearchLimitClamp:
    """A limit above Zep's ceiling (50) is clamped, never sent as-is (400)."""

    def test_pinned_limit_clamped_with_warning(
        self, mock_zep_client: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="zep_ag2.tools"):
            tool = create_search_graph_tool(
                mock_zep_client, GRAPH_UUID, pinned_params={"limit": 100}
            )
        assert any("clamping" in record.message for record in caplog.records)

        tool(query="hello")

        call_kwargs = mock_zep_client.graph.search_edges.call_args.kwargs
        assert call_kwargs["page_size"] == 50

    def test_pinned_limit_below_one_clamped_to_one(self, mock_zep_client: MagicMock) -> None:
        tool = create_search_graph_tool(mock_zep_client, GRAPH_UUID, pinned_params={"limit": 0})

        tool(query="hello")

        call_kwargs = mock_zep_client.graph.search_edges.call_args.kwargs
        assert call_kwargs["page_size"] == 1

    def test_model_limit_clamped_at_call_time(self, mock_zep_client: MagicMock) -> None:
        tool = create_search_graph_tool(mock_zep_client, GRAPH_UUID)

        tool(query="hello", limit=200)

        call_kwargs = mock_zep_client.graph.search_edges.call_args.kwargs
        assert call_kwargs["page_size"] == 50

    def test_model_limit_below_one_clamped_to_one(self, mock_zep_client: MagicMock) -> None:
        tool = create_search_graph_tool(mock_zep_client, GRAPH_UUID)

        tool(query="hello", limit=-3)

        call_kwargs = mock_zep_client.graph.search_edges.call_args.kwargs
        assert call_kwargs["page_size"] == 1

    def test_search_memory_tool_pinned_limit_clamped(self, mock_zep_client: MagicMock) -> None:
        tool = create_search_memory_tool(mock_zep_client, GRAPH_UUID, limit=100)

        tool(query="hello")

        call_kwargs = mock_zep_client.graph.search_edges.call_args.kwargs
        assert call_kwargs["page_size"] == 50


class TestAutoScopeReranker:
    """scope='auto' takes no reranker."""

    @pytest.mark.parametrize("reranker", ["rrf", "mmr", "node_distance", "cross_encoder"])
    def test_pinned_auto_scope_with_reranker_raises(
        self, mock_zep_client: MagicMock, reranker: str
    ) -> None:
        with pytest.raises(ValueError, match="'auto' scope takes no reranker"):
            create_search_graph_tool(
                mock_zep_client,
                GRAPH_UUID,
                pinned_params={"scope": "auto", "reranker": reranker, "center_node_uuid": "n"},
            )

    def test_search_memory_tool_pinned_auto_scope_with_reranker_raises(
        self, mock_zep_client: MagicMock
    ) -> None:
        with pytest.raises(ValueError, match="'auto' scope takes no reranker"):
            create_search_memory_tool(
                mock_zep_client,
                GRAPH_UUID,
                pinned_params={"scope": "auto", "reranker": "rrf"},
            )

    @pytest.mark.parametrize("reranker", ["rrf", "mmr", "episode_mentions"])
    def test_model_auto_scope_with_reranker_returns_error(
        self, mock_zep_client: MagicMock, reranker: str
    ) -> None:
        tool = create_search_graph_tool(mock_zep_client, GRAPH_UUID)

        result = tool(query="hello", scope="auto", reranker=reranker)

        assert result.startswith("Error:")
        assert "'auto' scope takes no reranker" in result
        assert f"'{reranker}'" in result
        mock_zep_client.graph.get_context.assert_not_called()

    def test_search_memory_tool_model_auto_scope_with_reranker_returns_error(
        self, mock_zep_client: MagicMock
    ) -> None:
        tool = create_search_memory_tool(mock_zep_client, GRAPH_UUID)

        result = tool(query="hello", scope="auto", reranker="rrf")

        assert "'auto' scope takes no reranker" in result
        mock_zep_client.graph.get_context.assert_not_called()

    def test_model_auto_scope_without_reranker_calls_get_context(
        self, mock_zep_client: MagicMock
    ) -> None:
        tool = create_search_graph_tool(mock_zep_client, GRAPH_UUID)

        tool(query="hello", scope="auto")

        mock_zep_client.graph.get_context.assert_called_once()
        assert "reranker" not in mock_zep_client.graph.get_context.call_args.kwargs

    def test_pinned_reranker_removes_auto_scope_choice(self, mock_zep_client: MagicMock) -> None:
        tool = create_search_graph_tool(
            mock_zep_client, GRAPH_UUID, pinned_params={"reranker": "rrf"}
        )

        scope_choices = set(get_args(inspect.signature(tool).parameters["scope"].annotation))
        assert "auto" not in scope_choices


class TestSearchMemoryToolPinOrExpose:
    """create_search_memory_tool follows the same pin-or-expose contract."""

    def test_search_memory_tool_exposes_params_by_default(self, mock_zep_client: MagicMock) -> None:
        tool = create_search_memory_tool(mock_zep_client, GRAPH_UUID)
        sig = inspect.signature(tool)

        for param_name in ("scope", "reranker", "limit", "mmr_lambda", "center_node_uuid"):
            assert param_name in sig.parameters

    def test_search_memory_tool_six_scopes(self, mock_zep_client: MagicMock) -> None:
        tool = create_search_memory_tool(mock_zep_client, GRAPH_UUID)
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
            mock_zep_client, GRAPH_UUID, pinned_params={"scope": "nodes"}
        )
        sig = inspect.signature(tool)
        assert "scope" not in sig.parameters

        tool(query="hello")

        mock_zep_client.graph.search_nodes.assert_called_once()
        mock_zep_client.graph.search_edges.assert_not_called()

    def test_search_memory_tool_query_only_omits_unset_none_default_params(
        self, mock_zep_client: MagicMock
    ) -> None:
        tool = create_search_memory_tool(mock_zep_client, GRAPH_UUID)

        tool(query="hello")

        call_kwargs = mock_zep_client.graph.search_edges.call_args.kwargs
        assert "mmr_lambda" not in call_kwargs
        assert "center_node_uuid" not in call_kwargs

    def test_search_memory_tool_legacy_args_pin(self, mock_zep_client: MagicMock) -> None:
        tool = create_search_memory_tool(mock_zep_client, GRAPH_UUID, limit=9)
        sig = inspect.signature(tool)
        assert "limit" not in sig.parameters

        tool(query="hello")

        call_kwargs = mock_zep_client.graph.search_edges.call_args.kwargs
        assert call_kwargs["page_size"] == 9


class TestRerankerScopeSchema:
    """The schema offers only the scope and reranker pairs that Zep accepts."""

    def test_pinned_episodes_scope_offers_rrf_and_mmr(self, mock_zep_client: MagicMock) -> None:
        tool = create_search_graph_tool(
            mock_zep_client, GRAPH_UUID, pinned_params={"scope": "episodes"}
        )
        sig = inspect.signature(tool)
        assert _choices(tool, "reranker") == {"rrf", "mmr"}
        assert "mmr_lambda" in sig.parameters
        assert "center_node_uuid" not in sig.parameters

    def test_pinned_nodes_scope_has_no_episode_mentions(self, mock_zep_client: MagicMock) -> None:
        tool = create_search_memory_tool(
            mock_zep_client,
            GRAPH_UUID,
            pinned_params={"scope": "nodes"},
            filters={"episode_uuids": ["ep-1"]},
        )
        assert _choices(tool, "reranker") == {"rrf", "mmr", "node_distance", "cross_encoder"}

    def test_pinned_reranker_limits_scopes(self, mock_zep_client: MagicMock) -> None:
        tool = create_search_graph_tool(
            mock_zep_client, GRAPH_UUID, pinned_params={"reranker": "cross_encoder"}
        )
        sig = inspect.signature(tool)
        assert _choices(tool, "scope") == {
            "edges",
            "nodes",
            "observations",
            "thread_summaries",
        }
        assert "mmr_lambda" not in sig.parameters
        assert "center_node_uuid" not in sig.parameters

    def test_hidden_center_node_removes_node_distance(self, mock_zep_client: MagicMock) -> None:
        tool = create_search_graph_tool(
            mock_zep_client, GRAPH_UUID, hidden_params={"center_node_uuid"}
        )
        assert _choices(tool, "reranker") == {"rrf", "mmr", "cross_encoder"}

    def test_reranker_description_names_scope_limits(self, mock_zep_client: MagicMock) -> None:
        tool = create_search_graph_tool(mock_zep_client, GRAPH_UUID)
        metadata = get_type_hints(tool, include_extras=True)["reranker"].__metadata__[0]
        # AG2 tool registration can replace the shared ``Annotated`` string with a Field.
        description = metadata if isinstance(metadata, str) else metadata.description
        assert "'cross_encoder' (highest accuracy) -- only with scope" in description
        assert "'episodes'" not in description.split("'cross_encoder'")[1]
        assert "default 0.5" in description


class TestPinnedRerankerValidation:
    """An invalid pinned pair raises ValueError when the tool is created."""

    @pytest.mark.parametrize(
        ("scope", "reranker"),
        [
            ("episodes", "cross_encoder"),
            ("episodes", "node_distance"),
            ("episodes", "episode_mentions"),
            ("nodes", "episode_mentions"),
            ("observations", "node_distance"),
            ("thread_summaries", "episode_mentions"),
        ],
    )
    def test_unsupported_pair_raises(
        self, mock_zep_client: MagicMock, scope: str, reranker: str
    ) -> None:
        with pytest.raises(ValueError, match=f"'{reranker}' is not supported with scope '{scope}'"):
            create_search_graph_tool(
                mock_zep_client,
                GRAPH_UUID,
                pinned_params={"scope": scope, "reranker": reranker, "center_node_uuid": "n"},
                filters={"episode_uuids": ["ep-1"]},
            )

    def test_node_distance_without_center_node_raises(self, mock_zep_client: MagicMock) -> None:
        with pytest.raises(ValueError, match="center_node_uuid"):
            create_search_memory_tool(
                mock_zep_client, GRAPH_UUID, pinned_params={"reranker": "node_distance"}
            )

    def test_episode_mentions_without_episode_uuids_raises(
        self, mock_zep_client: MagicMock
    ) -> None:
        with pytest.raises(ValueError, match="episode_uuids"):
            create_search_graph_tool(
                mock_zep_client, GRAPH_UUID, pinned_params={"reranker": "episode_mentions"}
            )

    def test_unknown_scope_and_reranker_raise(self, mock_zep_client: MagicMock) -> None:
        with pytest.raises(ValueError, match="Unknown search scope"):
            create_search_graph_tool(mock_zep_client, GRAPH_UUID, pinned_params={"scope": "x"})
        with pytest.raises(ValueError, match="Unknown reranker"):
            create_search_graph_tool(mock_zep_client, GRAPH_UUID, pinned_params={"reranker": "x"})

    def test_pinned_mmr_gets_default_lambda(self, mock_zep_client: MagicMock) -> None:
        tool = create_search_graph_tool(
            mock_zep_client, GRAPH_UUID, pinned_params={"scope": "episodes", "reranker": "mmr"}
        )
        assert "mmr_lambda" in inspect.signature(tool).parameters

        tool(query="hello")

        call_kwargs = mock_zep_client.graph.search_episodes.call_args.kwargs
        assert call_kwargs["reranker"] == "mmr"
        assert call_kwargs["mmr_lambda"] == 0.5

    def test_valid_pinned_pair_is_sent(self, mock_zep_client: MagicMock) -> None:
        tool = create_search_graph_tool(
            mock_zep_client,
            GRAPH_UUID,
            pinned_params={"scope": "nodes", "reranker": "node_distance", "center_node_uuid": "n"},
        )

        tool(query="hello")

        call_kwargs = mock_zep_client.graph.search_nodes.call_args.kwargs
        assert call_kwargs["reranker"] == "node_distance"
        assert call_kwargs["center_node_uuid"] == "n"


class TestModelRerankerCorrection:
    """An invalid pair from the model returns an error and makes no Zep call."""

    @pytest.mark.parametrize(
        ("scope", "reranker", "method", "valid"),
        [
            ("episodes", "cross_encoder", "search_episodes", "'rrf', 'mmr'"),
            ("episodes", "episode_mentions", "search_episodes", "'rrf', 'mmr'"),
            (
                "nodes",
                "episode_mentions",
                "search_nodes",
                "'rrf', 'mmr', 'node_distance', 'cross_encoder'",
            ),
            (
                "observations",
                "node_distance",
                "search_observations",
                "'rrf', 'mmr', 'cross_encoder'",
            ),
            (
                "thread_summaries",
                "node_distance",
                "search_thread_summaries",
                "'rrf', 'mmr', 'cross_encoder'",
            ),
        ],
    )
    def test_unsupported_pair_returns_error(
        self,
        mock_zep_client: MagicMock,
        scope: str,
        reranker: str,
        method: str,
        valid: str,
    ) -> None:
        tool = create_search_graph_tool(mock_zep_client, GRAPH_UUID)

        result = tool(query="hello", scope=scope, reranker=reranker, center_node_uuid="n")

        assert result.startswith("Error:")
        assert f"'{reranker}' is not valid with scope '{scope}'" in result
        assert valid in result
        getattr(mock_zep_client.graph, method).assert_not_called()

    def test_search_memory_tool_unsupported_pair_returns_error(
        self, mock_zep_client: MagicMock
    ) -> None:
        tool = create_search_memory_tool(mock_zep_client, GRAPH_UUID)

        result = tool(query="hello", scope="episodes", reranker="cross_encoder")

        assert "'cross_encoder' is not valid with scope 'episodes'" in result
        mock_zep_client.graph.search_episodes.assert_not_called()

    def test_mmr_without_lambda_gets_default(self, mock_zep_client: MagicMock) -> None:
        tool = create_search_graph_tool(mock_zep_client, GRAPH_UUID)

        tool(query="hello", scope="episodes", reranker="mmr")

        call_kwargs = mock_zep_client.graph.search_episodes.call_args.kwargs
        assert call_kwargs["reranker"] == "mmr"
        assert call_kwargs["mmr_lambda"] == 0.5

    def test_mmr_keeps_model_lambda(self, mock_zep_client: MagicMock) -> None:
        tool = create_search_memory_tool(mock_zep_client, GRAPH_UUID)

        tool(query="hello", scope="nodes", reranker="mmr", mmr_lambda=0.9)

        call_kwargs = mock_zep_client.graph.search_nodes.call_args.kwargs
        assert call_kwargs["mmr_lambda"] == 0.9

    def test_node_distance_without_center_node_returns_error(
        self, mock_zep_client: MagicMock
    ) -> None:
        tool = create_search_graph_tool(mock_zep_client, GRAPH_UUID)

        result = tool(query="hello", scope="edges", reranker="node_distance")

        assert result.startswith("Error:")
        assert "center_node_uuid" in result
        mock_zep_client.graph.search_edges.assert_not_called()

    def test_episode_mentions_without_episode_uuids_returns_error(
        self, mock_zep_client: MagicMock
    ) -> None:
        tool = create_search_graph_tool(mock_zep_client, GRAPH_UUID)

        result = tool(query="hello", scope="edges", reranker="episode_mentions")

        assert result.startswith("Error:")
        assert "episode_mentions" in result
        mock_zep_client.graph.search_edges.assert_not_called()

    def test_episode_mentions_with_episode_uuids_kept(self, mock_zep_client: MagicMock) -> None:
        tool = create_search_graph_tool(
            mock_zep_client, GRAPH_UUID, filters={"episode_uuids": ["ep-1"]}
        )

        tool(query="hello", scope="edges", reranker="episode_mentions")

        call_kwargs = mock_zep_client.graph.search_edges.call_args.kwargs
        assert call_kwargs["reranker"] == "episode_mentions"

    def test_supported_pair_unchanged(self, mock_zep_client: MagicMock) -> None:
        tool = create_search_graph_tool(mock_zep_client, GRAPH_UUID)

        tool(query="hello", scope="observations", reranker="cross_encoder")

        call_kwargs = mock_zep_client.graph.search_observations.call_args.kwargs
        assert call_kwargs["reranker"] == "cross_encoder"
        assert "mmr_lambda" not in call_kwargs
