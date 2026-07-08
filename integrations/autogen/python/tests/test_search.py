"""
Tests for the pin-or-expose ``create_search_graph_tool`` factory.

AutoGen's ``FunctionTool`` derives its JSON schema strictly from the wrapped
Python function's typed signature (via ``args_base_model_from_signature`` /
pydantic ``model_json_schema()``) -- there is no raw-JSON-schema escape hatch
like ``agent_framework.tool(schema=...)`` or ``pydantic_ai.Tool.from_schema``.
So pin-or-expose here works by *dynamically constructing the wrapped
function's signature*: exposed params become real typed parameters (so they
show up in ``tool.schema["parameters"]["properties"]``), while pinned/hidden
params are simply not parameters of the wrapped function at all -- they are
merged in as constants when the tool executes.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from autogen_core import CancellationToken
from zep_cloud.client import AsyncZep

from zep_autogen.tools import create_search_graph_tool


def _make_mock_client() -> MagicMock:
    client = MagicMock(spec=AsyncZep)
    client.graph = MagicMock()
    client.graph.search = AsyncMock()
    return client


def _make_result(
    edges=None,
    nodes=None,
    episodes=None,
    context=None,
    observations=None,
    thread_summaries=None,
) -> MagicMock:
    result = MagicMock()
    result.edges = edges
    result.nodes = nodes
    result.episodes = episodes
    result.context = context
    result.observations = observations
    result.thread_summaries = thread_summaries
    return result


async def _run(tool, **kwargs: object):
    return await tool.run_json(kwargs, CancellationToken())


class TestPinOrExposeSchema:
    def test_search_tool_exposes_scope_reranker_limit_by_default(self) -> None:
        """By default (no pins), scope/reranker/limit/mmr_lambda/center_node_uuid
        are all in the model-facing schema alongside query."""
        client = _make_mock_client()
        tool = create_search_graph_tool(client, user_id="user-1")
        schema = tool.schema
        properties = schema["parameters"]["properties"]

        assert "query" in properties
        assert "scope" in properties
        assert set(properties["scope"]["enum"]) == {
            "edges",
            "nodes",
            "episodes",
            "observations",
            "thread_summaries",
            "auto",
        }
        assert "reranker" in properties
        assert set(properties["reranker"]["enum"]) == {
            "rrf",
            "mmr",
            "node_distance",
            "episode_mentions",
            "cross_encoder",
        }
        assert "limit" in properties
        assert "mmr_lambda" in properties
        assert "center_node_uuid" in properties
        assert schema["parameters"]["required"] == ["query"]

    @pytest.mark.asyncio
    async def test_search_tool_pinned_params_hidden_and_sent(self) -> None:
        client = _make_mock_client()
        client.graph.search.return_value = _make_result(nodes=[])
        tool = create_search_graph_tool(
            client,
            user_id="user-1",
            pinned_params={"scope": "nodes", "limit": 5},
        )

        properties = tool.schema["parameters"]["properties"]
        assert "scope" not in properties
        assert "limit" not in properties
        assert "reranker" in properties

        await _run(tool, query="query")

        kwargs = client.graph.search.call_args.kwargs
        assert kwargs["scope"] == "nodes"
        assert kwargs["limit"] == 5

    @pytest.mark.asyncio
    async def test_search_tool_hidden_params_omitted_from_sdk_call(self) -> None:
        client = _make_mock_client()
        client.graph.search.return_value = _make_result(edges=[])
        tool = create_search_graph_tool(
            client, user_id="user-1", hidden_params={"mmr_lambda", "center_node_uuid"}
        )

        properties = tool.schema["parameters"]["properties"]
        assert "mmr_lambda" not in properties
        assert "center_node_uuid" not in properties
        assert "scope" in properties

        await _run(tool, query="query")

        kwargs = client.graph.search.call_args.kwargs
        assert "mmr_lambda" not in kwargs
        assert "center_node_uuid" not in kwargs

    @pytest.mark.asyncio
    async def test_search_tool_legacy_args_pin(self) -> None:
        """Legacy factory args (scope/limit) pin the corresponding param,
        same as passing it via pinned_params -- back-compat."""
        client = _make_mock_client()
        client.graph.search.return_value = _make_result(nodes=[])
        tool = create_search_graph_tool(client, user_id="user-1", scope="nodes", limit=3)

        properties = tool.schema["parameters"]["properties"]
        assert "scope" not in properties
        assert "limit" not in properties

        await _run(tool, query="query")

        kwargs = client.graph.search.call_args.kwargs
        assert kwargs["scope"] == "nodes"
        assert kwargs["limit"] == 3

    def test_search_tool_six_scopes(self) -> None:
        client = _make_mock_client()
        tool = create_search_graph_tool(client, user_id="user-1")
        properties = tool.schema["parameters"]["properties"]
        assert set(properties["scope"]["enum"]) == {
            "edges",
            "nodes",
            "episodes",
            "observations",
            "thread_summaries",
            "auto",
        }

    @pytest.mark.asyncio
    async def test_search_tool_query_only_omits_unset_none_default_params(self) -> None:
        """Calling with only ``query`` must not forward ``mmr_lambda``/
        ``center_node_uuid`` as explicit ``None`` -- the zep-cloud SDK
        serializes an explicit ``None`` as ``null`` on the wire, whereas
        omitting the kwarg lets the SDK's own OMIT sentinel apply. Params
        whose spec default is non-None (``scope``, ``limit``) must still be
        sent with their default value.
        """
        client = _make_mock_client()
        client.graph.search.return_value = _make_result(edges=[])
        tool = create_search_graph_tool(client, user_id="user-1")

        await _run(tool, query="query")

        kwargs = client.graph.search.call_args.kwargs
        assert "mmr_lambda" not in kwargs
        assert "center_node_uuid" not in kwargs
        assert kwargs["scope"] == "edges"
        assert kwargs["limit"] == 10
        assert kwargs["reranker"] == "rrf"


class TestSearchTargeting:
    @pytest.mark.asyncio
    async def test_searches_user_graph_by_default(self) -> None:
        client = _make_mock_client()
        client.graph.search.return_value = _make_result(edges=[])
        tool = create_search_graph_tool(client, user_id="user-1")

        await _run(tool, query="q")

        kwargs = client.graph.search.call_args.kwargs
        assert kwargs["user_id"] == "user-1"
        assert "graph_id" not in kwargs

    @pytest.mark.asyncio
    async def test_searches_graph_id_when_set(self) -> None:
        client = _make_mock_client()
        client.graph.search.return_value = _make_result(edges=[])
        tool = create_search_graph_tool(client, graph_id="docs-graph")

        await _run(tool, query="q")

        kwargs = client.graph.search.call_args.kwargs
        assert kwargs["graph_id"] == "docs-graph"
        assert "user_id" not in kwargs

    def test_requires_graph_id_or_user_id(self) -> None:
        client = _make_mock_client()
        with pytest.raises(ValueError, match="Either graph_id or user_id"):
            create_search_graph_tool(client)

    def test_rejects_both_graph_id_and_user_id(self) -> None:
        client = _make_mock_client()
        with pytest.raises(ValueError, match="Only one of"):
            create_search_graph_tool(client, graph_id="g", user_id="u")


class TestConstructorOnlyParams:
    def test_search_filters_and_bfs_not_in_schema(self) -> None:
        client = _make_mock_client()
        tool = create_search_graph_tool(
            client,
            user_id="user-1",
            search_filters={"node_labels": ["Person"]},
            bfs_origin_node_uuids=["uuid-1"],
        )
        properties = tool.schema["parameters"]["properties"]
        assert "search_filters" not in properties
        assert "bfs_origin_node_uuids" not in properties

    @pytest.mark.asyncio
    async def test_search_filters_and_bfs_sent_to_sdk(self) -> None:
        client = _make_mock_client()
        client.graph.search.return_value = _make_result(edges=[])
        tool = create_search_graph_tool(
            client,
            user_id="user-1",
            search_filters={"node_labels": ["Person"]},
            bfs_origin_node_uuids=["uuid-1"],
        )

        await _run(tool, query="query")

        kwargs = client.graph.search.call_args.kwargs
        assert kwargs["search_filters"] == {"node_labels": ["Person"]}
        assert kwargs["bfs_origin_node_uuids"] == ["uuid-1"]


class TestUnknownParams:
    def test_unknown_pinned_param_raises(self) -> None:
        client = _make_mock_client()
        with pytest.raises(ValueError, match="Unknown pinned parameters"):
            create_search_graph_tool(client, user_id="user-1", pinned_params={"bogus": 1})

    def test_unknown_hidden_param_raises(self) -> None:
        client = _make_mock_client()
        with pytest.raises(ValueError, match="Unknown hidden parameters"):
            create_search_graph_tool(client, user_id="user-1", hidden_params={"bogus"})


class TestResultFormatting:
    @pytest.mark.asyncio
    async def test_auto_scope_returns_context(self) -> None:
        client = _make_mock_client()
        client.graph.search.return_value = _make_result(context="Assembled context block")
        tool = create_search_graph_tool(client, user_id="user-1", pinned_params={"scope": "auto"})
        out = await _run(tool, query="q")
        assert out == "Assembled context block"

    @pytest.mark.asyncio
    async def test_formats_observations(self) -> None:
        client = _make_mock_client()
        obs = MagicMock()
        obs.name = "Prefers async updates"
        obs.summary = "Communication pattern"
        client.graph.search.return_value = _make_result(observations=[obs])
        tool = create_search_graph_tool(
            client, user_id="user-1", pinned_params={"scope": "observations"}
        )
        out = await _run(tool, query="q")
        assert "Prefers async updates" in str(out)

    @pytest.mark.asyncio
    async def test_formats_thread_summaries(self) -> None:
        client = _make_mock_client()
        ts = MagicMock()
        ts.summary = "User discussed Q3 roadmap"
        ts.name = "thread"
        client.graph.search.return_value = _make_result(thread_summaries=[ts])
        tool = create_search_graph_tool(
            client, user_id="user-1", pinned_params={"scope": "thread_summaries"}
        )
        out = await _run(tool, query="q")
        assert "User discussed Q3 roadmap" in str(out)

    @pytest.mark.asyncio
    async def test_search_errors_return_gracefully(self) -> None:
        client = _make_mock_client()
        client.graph.search.side_effect = RuntimeError("boom")
        tool = create_search_graph_tool(client, user_id="user-1")
        out = await _run(tool, query="q")
        assert "failed" in str(out).lower() or "error" in str(out).lower()
