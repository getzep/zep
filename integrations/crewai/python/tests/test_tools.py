"""Tests for UUID-based V4 CrewAI tools."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from zep_cloud.client import Zep
from zep_cloud.types import (
    EdgeSearchFilters,
    EpisodeSearchFilters,
    NodeSearchFilters,
    SearchFilters,
    ThreadSummarySearchFilters,
)

from zep_crewai import (
    ZepAddDataTool,
    ZepSearchTool,
    create_add_data_tool,
    create_search_tool,
)
from zep_crewai.tools import MAX_PAGE_SIZE


def _client() -> MagicMock:
    client = MagicMock(spec=Zep)
    client.user = MagicMock()
    client.user.get.return_value.graph_uuid = "user-graph"
    client.graph = MagicMock()
    return client


def _result(items: list[object] | None = None) -> SimpleNamespace:
    return SimpleNamespace(items=items or [], context=None)


def test_search_tool_initialization_uses_graph_uuid() -> None:
    tool = ZepSearchTool(_client(), graph_uuid="graph-uuid")
    assert tool._graph_uuid == "graph-uuid"
    assert "graph-uuid" in tool.description


def test_search_tool_resolves_user_uuid_to_graph_uuid() -> None:
    client = _client()
    tool = ZepSearchTool(client, user_uuid="user-uuid")
    assert tool._graph_uuid == "user-graph"
    client.user.get.assert_called_once_with("user-uuid")


def test_search_tool_requires_exactly_one_uuid() -> None:
    client = _client()
    with pytest.raises(ValueError, match="exactly one"):
        ZepSearchTool(client)
    with pytest.raises(ValueError, match="exactly one"):
        ZepSearchTool(client, graph_uuid="g", user_uuid="u")


def test_search_tool_schema_exposes_search_parameters() -> None:
    fields = ZepSearchTool(_client(), graph_uuid="g").args_schema.model_fields
    assert {"query", "scope", "reranker", "limit", "mmr_lambda", "center_node_uuid"} <= set(fields)
    assert "search_filters" not in fields
    assert "bfs_origin_node_uuids" not in fields


def test_pinned_parameters_are_removed_from_schema_and_applied() -> None:
    client = _client()
    client.graph.search_nodes.return_value = _result()
    tool = ZepSearchTool(client, graph_uuid="g", pinned_params={"scope": "nodes", "limit": 7})
    assert "scope" not in tool.args_schema.model_fields
    assert "limit" not in tool.args_schema.model_fields
    tool._run(query="topic")
    client.graph.search_nodes.assert_called_once_with("g", query="topic", page_size=7)


def test_hidden_parameters_are_removed_from_schema() -> None:
    fields = ZepSearchTool(
        _client(), graph_uuid="g", hidden_params={"scope", "reranker"}
    ).args_schema.model_fields
    assert "scope" not in fields
    assert "reranker" not in fields


def test_parameter_cannot_be_both_pinned_and_hidden() -> None:
    with pytest.raises(ValueError, match="both pinned and hidden"):
        ZepSearchTool(
            _client(), graph_uuid="g", pinned_params={"scope": "edges"}, hidden_params={"scope"}
        )


def test_unknown_tool_parameter_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown search parameter"):
        ZepSearchTool(_client(), graph_uuid="g", pinned_params={"unknown": 1})


def test_unknown_hidden_parameter_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown search parameter"):
        ZepSearchTool(_client(), graph_uuid="g", hidden_params={"unknown"})


def test_legacy_constructor_arguments_pin_params() -> None:
    client = _client()
    client.graph.search_episodes.return_value = _result()
    tool = ZepSearchTool(client, graph_uuid="g", scope="episodes", limit=3)
    tool._run(query="topic")
    client.graph.search_episodes.assert_called_once_with("g", query="topic", page_size=3)


@pytest.mark.parametrize(
    ("scope", "method", "item", "expected"),
    [
        ("edges", "search_edges", SimpleNamespace(fact="Edge fact"), "Edge fact"),
        ("nodes", "search_nodes", SimpleNamespace(name="Person", summary="A user"), "Person"),
        ("episodes", "search_episodes", SimpleNamespace(content="Episode"), "Episode"),
        (
            "observations",
            "search_observations",
            SimpleNamespace(content="Observation"),
            "Observation",
        ),
        (
            "thread_summaries",
            "search_thread_summaries",
            SimpleNamespace(summary="Summary"),
            "Summary",
        ),
    ],
)
def test_scope_calls_v4_search_and_formats_results(
    scope: str, method: str, item: object, expected: str
) -> None:
    client = _client()
    getattr(client.graph, method).return_value = _result([item])
    result = ZepSearchTool(client, graph_uuid="g")._run(query="topic", scope=scope)
    assert expected in result
    assert getattr(client.graph, method).call_args.args == ("g",)
    assert getattr(client.graph, method).call_args.kwargs == {
        "query": "topic",
        "page_size": 10,
    }


def test_auto_scope_uses_get_context() -> None:
    client = _client()
    client.graph.get_context.return_value.context = "Ready context"
    result = ZepSearchTool(client, graph_uuid="g")._run(query="topic", scope="auto")
    assert result == "Ready context"
    client.graph.get_context.assert_called_once_with("g", query="topic")
    client.graph.search_edges.assert_not_called()


def test_auto_scope_returns_empty_result_message() -> None:
    client = _client()
    client.graph.get_context.return_value.context = None
    assert (
        ZepSearchTool(client, graph_uuid="g")._run(query="topic", scope="auto")
        == "No results found."
    )


def test_constructor_filters_and_bfs_origins_are_sent_to_scope_search() -> None:
    client = _client()
    client.graph.search_edges.return_value = _result()
    filters = SearchFilters(node_labels=["Person"])
    origins = ["node-uuid"]
    tool = ZepSearchTool(
        client, graph_uuid="g", search_filters=filters, bfs_origin_node_uuids=origins
    )
    tool._run(query="topic", scope="nodes")
    assert client.graph.search_nodes.call_args.kwargs["filters"].node_labels == ["Person"]
    assert client.graph.search_nodes.call_args.kwargs["bfs_origin_node_uuids"] == origins


def test_auto_scope_passes_supported_filters_only() -> None:
    client = _client()
    client.graph.get_context.return_value.context = "context"
    filters = SearchFilters(node_labels=["Person"])
    ZepSearchTool(client, graph_uuid="g", search_filters=filters, bfs_origin_node_uuids=["n"])._run(
        query="topic", scope="auto"
    )
    client.graph.get_context.assert_called_once_with("g", query="topic", filters=filters)


def test_filter_for_scope_uses_v4_typed_filter() -> None:
    client = _client()
    client.graph.search_nodes.return_value = _result()
    ZepSearchTool(
        client,
        graph_uuid="g",
        search_filters=SearchFilters(node_labels=["Person"]),
    )._run(query="topic", scope="nodes")
    filters = client.graph.search_nodes.call_args.kwargs["filters"]
    assert filters.node_labels == ["Person"]


@pytest.mark.parametrize(
    ("scope", "filters", "expected_type"),
    [
        ("edges", SearchFilters(edge_types=["LIKES"]), EdgeSearchFilters),
        ("nodes", SearchFilters(node_labels=["Person"]), NodeSearchFilters),
        (
            "episodes",
            SearchFilters(content_policy_violated=True),
            EpisodeSearchFilters,
        ),
        ("observations", SearchFilters(episode_uuids=["episode-uuid"]), dict),
        ("thread_summaries", SearchFilters(), ThreadSummarySearchFilters),
    ],
)
def test_scope_filters_use_installed_sdk_filter_types(
    scope: str, filters: SearchFilters, expected_type: type
) -> None:
    client = _client()
    client.graph.search_edges.return_value = _result()
    client.graph.search_nodes.return_value = _result()
    client.graph.search_episodes.return_value = _result()
    client.graph.search_observations.return_value = _result()
    client.graph.search_thread_summaries.return_value = _result()
    ZepSearchTool(client, graph_uuid="g", search_filters=filters)._run(query="topic", scope=scope)
    method = getattr(client.graph, f"search_{scope}")
    assert isinstance(method.call_args.kwargs["filters"], expected_type)


def test_unsupported_filter_returns_clear_error_for_scope() -> None:
    client = _client()
    tool = ZepSearchTool(
        client,
        graph_uuid="g",
        search_filters=SearchFilters(node_labels=["Person"]),
    )
    result = tool._run(query="topic", scope="edges")
    assert "node_labels" in result
    assert "not supported for edges" in result
    client.graph.search_edges.assert_not_called()


def test_search_passes_model_selected_reranker_options() -> None:
    client = _client()
    client.graph.search_edges.return_value = _result()
    ZepSearchTool(client, graph_uuid="g")._run(
        query="topic",
        scope="edges",
        reranker="mmr",
        mmr_lambda=0.4,
        center_node_uuid="center",
    )
    assert client.graph.search_edges.call_args.kwargs == {
        "query": "topic",
        "page_size": 10,
        "reranker": "mmr",
        "mmr_lambda": 0.4,
        "center_node_uuid": "center",
    }


def test_non_auto_scope_keeps_node_distance_reranker() -> None:
    client = _client()
    client.graph.search_edges.return_value = _result()
    ZepSearchTool(client, graph_uuid="g")._run(
        query="topic",
        scope="edges",
        reranker="node_distance",
        center_node_uuid="center",
    )
    assert client.graph.search_edges.call_args.kwargs["reranker"] == "node_distance"


def test_empty_search_query_does_not_call_sdk() -> None:
    client = _client()
    result = ZepSearchTool(client, graph_uuid="g")._run(query="  ")
    assert result.startswith("Error:")
    client.graph.search_edges.assert_not_called()


def test_search_limit_is_capped_at_sdk_maximum() -> None:
    client = _client()
    client.graph.search_edges.return_value = _result()
    ZepSearchTool(client, graph_uuid="g")._run(query="q", limit=100)
    assert client.graph.search_edges.call_args.kwargs["page_size"] == MAX_PAGE_SIZE


def test_search_limit_is_at_least_one() -> None:
    client = _client()
    client.graph.search_edges.return_value = _result()
    ZepSearchTool(client, graph_uuid="g")._run(query="q", limit=0)
    assert client.graph.search_edges.call_args.kwargs["page_size"] == 1


def test_search_query_is_truncated_for_sdk() -> None:
    client = _client()
    client.graph.search_edges.return_value = _result()
    query = "x" * 500
    ZepSearchTool(client, graph_uuid="g")._run(query=query)
    assert client.graph.search_edges.call_args.kwargs["query"] == query[:400]


def test_search_sdk_error_returns_error_string() -> None:
    client = _client()
    client.graph.search_edges.side_effect = RuntimeError("offline")
    result = ZepSearchTool(client, graph_uuid="g")._run(query="q")
    assert "offline" in result


def test_hidden_reranker_is_not_sent_when_unset() -> None:
    client = _client()
    client.graph.search_edges.return_value = _result()
    tool = ZepSearchTool(client, graph_uuid="g", hidden_params={"reranker"})
    tool._run(query="topic")
    assert "reranker" not in client.graph.search_edges.call_args.kwargs


def test_auto_scope_ignores_reranker_options(caplog: pytest.LogCaptureFixture) -> None:
    client = _client()
    client.graph.get_context.return_value.context = "context"
    with caplog.at_level("WARNING", logger="zep_crewai.tools"):
        ZepSearchTool(client, graph_uuid="g")._run(
            query="topic", scope="auto", reranker="node_distance", center_node_uuid="node"
        )
    assert client.graph.get_context.call_args.kwargs == {"query": "topic"}
    assert any("not supported for auto scope" in record.message for record in caplog.records)


def test_pinned_auto_scope_drops_incompatible_reranker(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = _client()
    client.graph.get_context.return_value.context = "context"
    with caplog.at_level("WARNING", logger="zep_crewai.tools"):
        tool = ZepSearchTool(
            client,
            graph_uuid="g",
            pinned_params={"scope": "auto", "reranker": "node_distance"},
        )
        tool._run(query="topic")
    assert client.graph.get_context.call_args.kwargs == {"query": "topic"}
    assert "reranker" not in tool.args_schema.model_fields
    assert any("not supported for auto scope" in record.message for record in caplog.records)


def test_pinned_auto_scope_drops_model_selected_incompatible_reranker() -> None:
    client = _client()
    client.graph.get_context.return_value.context = "context"
    ZepSearchTool(client, graph_uuid="g", pinned_params={"scope": "auto"})._run(
        query="topic", reranker="episode_mentions"
    )
    assert client.graph.get_context.call_args.kwargs == {"query": "topic"}


def test_pinned_limit_is_capped_and_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = _client()
    client.graph.search_edges.return_value = _result()
    with caplog.at_level("WARNING", logger="zep_crewai.tools"):
        ZepSearchTool(client, graph_uuid="g", pinned_params={"limit": MAX_PAGE_SIZE + 10})._run(
            query="topic"
        )
    assert client.graph.search_edges.call_args.kwargs["page_size"] == MAX_PAGE_SIZE
    assert any("clamping" in record.message for record in caplog.records)


def test_pinned_limit_below_one_is_clamped() -> None:
    client = _client()
    client.graph.search_edges.return_value = _result()
    ZepSearchTool(client, graph_uuid="g", pinned_params={"limit": 0})._run(query="topic")
    assert client.graph.search_edges.call_args.kwargs["page_size"] == 1


def test_legacy_limit_argument_is_clamped() -> None:
    client = _client()
    client.graph.search_edges.return_value = _result()
    ZepSearchTool(client, graph_uuid="g", limit=MAX_PAGE_SIZE + 10)._run(query="topic")
    assert client.graph.search_edges.call_args.kwargs["page_size"] == MAX_PAGE_SIZE


def test_model_limit_below_one_is_clamped() -> None:
    client = _client()
    client.graph.search_edges.return_value = _result()
    ZepSearchTool(client, graph_uuid="g")._run(query="topic", limit=-3)
    assert client.graph.search_edges.call_args.kwargs["page_size"] == 1


def test_valid_search_limit_is_unchanged() -> None:
    client = _client()
    client.graph.search_edges.return_value = _result()
    ZepSearchTool(client, graph_uuid="g")._run(query="topic", limit=25)
    assert client.graph.search_edges.call_args.kwargs["page_size"] == 25


def test_search_factory_requires_one_uuid() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        create_search_tool(_client())
    with pytest.raises(ValueError, match="exactly one"):
        create_search_tool(_client(), graph_uuid="g", user_uuid="u")


@pytest.mark.parametrize("data_type", ["text", "json", "message"])
def test_add_data_tool_supports_documented_types(data_type: str) -> None:
    client = _client()
    ZepAddDataTool(client, graph_uuid="g")._run("payload", data_type=data_type)
    assert client.graph.episode.add.call_args.kwargs["type"] == data_type


def test_add_data_tool_uses_graph_episode_api() -> None:
    client = _client()
    result = ZepAddDataTool(client, graph_uuid="g")._run("fact", data_type="text")
    client.graph.episode.add.assert_called_once_with("g", data="fact", type="text")
    assert "Successfully added" in result


def test_add_data_tool_requires_graph_uuid() -> None:
    with pytest.raises(ValueError, match="graph_uuid is required"):
        ZepAddDataTool(_client(), graph_uuid="")


def test_add_data_tool_error_returns_error_string() -> None:
    client = _client()
    client.graph.episode.add.side_effect = RuntimeError("offline")
    result = ZepAddDataTool(client, graph_uuid="g")._run("fact")
    assert "offline" in result


def test_search_factory_creates_uuid_scoped_tool() -> None:
    tool = create_search_tool(_client(), graph_uuid="g")
    assert isinstance(tool, ZepSearchTool)
    assert tool._graph_uuid == "g"


def test_search_factory_accepts_user_uuid() -> None:
    client = _client()
    tool = create_search_tool(client, user_uuid="u")
    assert tool._graph_uuid == "user-graph"


def test_add_data_factory_creates_tool() -> None:
    tool = create_add_data_tool(_client(), graph_uuid="g")
    assert isinstance(tool, ZepAddDataTool)


def test_add_data_factory_requires_graph_uuid() -> None:
    with pytest.raises(ValueError, match="graph_uuid is required"):
        create_add_data_tool(_client(), graph_uuid="")


def test_tools_have_crewai_attributes() -> None:
    search = ZepSearchTool(_client(), graph_uuid="g")
    add = ZepAddDataTool(_client(), graph_uuid="g")
    for tool in (search, add):
        assert tool.name
        assert tool.description
        assert tool.args_schema
        assert callable(tool._run)


def test_add_data_schema_restricts_supported_types() -> None:
    field = ZepAddDataTool(_client(), graph_uuid="g").args_schema.model_fields["data_type"]
    assert field.default == "text"
