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

Zep v4 has one search method for each scope, and each method returns a pager.
``scope`` therefore selects the method instead of becoming a request field.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from autogen_core import CancellationToken
from conftest import FakePager
from zep_cloud.client import AsyncZep

from zep_autogen.tools import create_search_graph_tool

USER_UUID = "11111111-1111-1111-1111-111111111111"
GRAPH_UUID = "33333333-3333-3333-3333-333333333333"
DOCS_GRAPH_UUID = "44444444-4444-4444-4444-444444444444"


def _make_mock_client() -> MagicMock:
    client = MagicMock(spec=AsyncZep)
    client.user = MagicMock()
    client.user.get = AsyncMock(return_value=MagicMock(graph_uuid=GRAPH_UUID))
    client.graph = MagicMock()
    client.graph.search_edges = AsyncMock(return_value=FakePager([]))
    client.graph.search_nodes = AsyncMock(return_value=FakePager([]))
    client.graph.search_episodes = AsyncMock(return_value=FakePager([]))
    client.graph.search_observations = AsyncMock(return_value=FakePager([]))
    client.graph.search_thread_summaries = AsyncMock(return_value=FakePager([]))
    client.graph.get_context = AsyncMock(return_value=MagicMock(context="ctx"))
    return client


async def _run(tool, **kwargs: object):
    return await tool.run_json(kwargs, CancellationToken())


class TestPinOrExposeSchema:
    def test_search_tool_exposes_scope_reranker_limit_by_default(self) -> None:
        """By default (no pins), scope/reranker/limit/mmr_lambda/center_node_uuid
        are all in the model-facing schema alongside query."""
        client = _make_mock_client()
        tool = create_search_graph_tool(client, user_uuid=USER_UUID)
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
        tool = create_search_graph_tool(
            client,
            user_uuid=USER_UUID,
            pinned_params={"scope": "nodes", "limit": 5},
        )

        properties = tool.schema["parameters"]["properties"]
        assert "scope" not in properties
        assert "limit" not in properties
        assert "reranker" in properties

        await _run(tool, query="query")

        client.graph.search_nodes.assert_called_once()
        assert client.graph.search_nodes.call_args.kwargs["limit"] == 5

    @pytest.mark.asyncio
    async def test_search_tool_hidden_params_omitted_from_sdk_call(self) -> None:
        client = _make_mock_client()
        tool = create_search_graph_tool(
            client, user_uuid=USER_UUID, hidden_params={"mmr_lambda", "center_node_uuid"}
        )

        properties = tool.schema["parameters"]["properties"]
        assert "mmr_lambda" not in properties
        assert "center_node_uuid" not in properties
        assert "scope" in properties

        await _run(tool, query="query")

        kwargs = client.graph.search_edges.call_args.kwargs
        assert "mmr_lambda" not in kwargs
        assert "center_node_uuid" not in kwargs

    @pytest.mark.asyncio
    async def test_search_tool_legacy_args_pin(self) -> None:
        """Legacy factory args (scope/limit) pin the corresponding param,
        same as passing it via pinned_params -- back-compat."""
        client = _make_mock_client()
        tool = create_search_graph_tool(client, user_uuid=USER_UUID, scope="nodes", limit=3)

        properties = tool.schema["parameters"]["properties"]
        assert "scope" not in properties
        assert "limit" not in properties

        await _run(tool, query="query")

        client.graph.search_nodes.assert_called_once()
        assert client.graph.search_nodes.call_args.kwargs["limit"] == 3

    def test_search_tool_six_scopes(self) -> None:
        client = _make_mock_client()
        tool = create_search_graph_tool(client, user_uuid=USER_UUID)
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
        tool = create_search_graph_tool(client, user_uuid=USER_UUID)

        await _run(tool, query="query")

        client.graph.search_edges.assert_called_once()
        kwargs = client.graph.search_edges.call_args.kwargs
        assert "mmr_lambda" not in kwargs
        assert "center_node_uuid" not in kwargs
        assert kwargs["limit"] == 10
        assert kwargs["reranker"] == "rrf"


class TestSearchTargeting:
    @pytest.mark.asyncio
    async def test_searches_user_graph_by_default(self) -> None:
        client = _make_mock_client()
        tool = create_search_graph_tool(client, user_uuid=USER_UUID)

        await _run(tool, query="q")

        client.user.get.assert_called_once_with(USER_UUID)
        assert client.graph.search_edges.call_args.args[0] == GRAPH_UUID

    @pytest.mark.asyncio
    async def test_user_graph_uuid_is_resolved_once(self) -> None:
        client = _make_mock_client()
        tool = create_search_graph_tool(client, user_uuid=USER_UUID)

        await _run(tool, query="q")
        await _run(tool, query="q2")

        client.user.get.assert_called_once_with(USER_UUID)

    @pytest.mark.asyncio
    async def test_searches_graph_uuid_when_set(self) -> None:
        client = _make_mock_client()
        tool = create_search_graph_tool(client, graph_uuid=DOCS_GRAPH_UUID)

        await _run(tool, query="q")

        client.user.get.assert_not_called()
        assert client.graph.search_edges.call_args.args[0] == DOCS_GRAPH_UUID

    def test_requires_graph_uuid_or_user_uuid(self) -> None:
        client = _make_mock_client()
        with pytest.raises(ValueError, match="Either graph_uuid or user_uuid"):
            create_search_graph_tool(client)

    def test_rejects_both_graph_uuid_and_user_uuid(self) -> None:
        client = _make_mock_client()
        with pytest.raises(ValueError, match="Only one of"):
            create_search_graph_tool(client, graph_uuid=DOCS_GRAPH_UUID, user_uuid=USER_UUID)


class TestConstructorOnlyParams:
    def test_search_filters_and_bfs_not_in_schema(self) -> None:
        client = _make_mock_client()
        tool = create_search_graph_tool(
            client,
            user_uuid=USER_UUID,
            search_filters={"node_labels": ["Person"]},
            bfs_origin_node_uuids=["uuid-1"],
        )
        properties = tool.schema["parameters"]["properties"]
        assert "search_filters" not in properties
        assert "bfs_origin_node_uuids" not in properties

    @pytest.mark.asyncio
    async def test_search_filters_and_bfs_sent_to_sdk(self) -> None:
        client = _make_mock_client()
        tool = create_search_graph_tool(
            client,
            user_uuid=USER_UUID,
            search_filters={"node_labels": ["Person"]},
            bfs_origin_node_uuids=["uuid-1"],
        )

        await _run(tool, query="query")

        kwargs = client.graph.search_edges.call_args.kwargs
        assert kwargs["filters"] == {"node_labels": ["Person"]}
        assert kwargs["bfs_origin_node_uuids"] == ["uuid-1"]


class TestUnknownParams:
    def test_unknown_pinned_param_raises(self) -> None:
        client = _make_mock_client()
        with pytest.raises(ValueError, match="Unknown pinned parameters"):
            create_search_graph_tool(client, user_uuid=USER_UUID, pinned_params={"bogus": 1})

    def test_unknown_hidden_param_raises(self) -> None:
        client = _make_mock_client()
        with pytest.raises(ValueError, match="Unknown hidden parameters"):
            create_search_graph_tool(client, user_uuid=USER_UUID, hidden_params={"bogus"})


class TestLimitClamping:
    @pytest.mark.asyncio
    async def test_pinned_limit_clamped_to_ceiling_with_warning(self, caplog) -> None:
        """A pinned limit above Zep's ceiling is clamped at construction time
        (with a warning) so the call never 400s."""
        client = _make_mock_client()
        with caplog.at_level("WARNING", logger="zep_autogen.search"):
            tool = create_search_graph_tool(
                client, user_uuid=USER_UUID, pinned_params={"limit": 100}
            )
        assert any("clamped" in record.message for record in caplog.records)

        await _run(tool, query="query")

        assert client.graph.search_edges.call_args.kwargs["limit"] == 50

    @pytest.mark.asyncio
    async def test_model_provided_limit_clamped_at_call_time(self) -> None:
        """A model-provided limit above Zep's ceiling is clamped (not
        rejected) when building the SDK kwargs."""
        client = _make_mock_client()
        tool = create_search_graph_tool(client, user_uuid=USER_UUID)

        await _run(tool, query="query", limit=200)

        assert client.graph.search_edges.call_args.kwargs["limit"] == 50

    @pytest.mark.asyncio
    async def test_model_provided_limit_clamped_to_at_least_one(self) -> None:
        client = _make_mock_client()
        tool = create_search_graph_tool(client, user_uuid=USER_UUID)

        await _run(tool, query="query", limit=0)

        assert client.graph.search_edges.call_args.kwargs["limit"] == 1


class TestAutoScope:
    @pytest.mark.asyncio
    async def test_pinned_auto_scope_drops_incompatible_pinned_reranker(self, caplog) -> None:
        """scope pinned to 'auto' + a pinned auto-incompatible reranker: the
        reranker is dropped (with a warning) and never sent to Zep."""
        client = _make_mock_client()
        with caplog.at_level("WARNING", logger="zep_autogen.tools"):
            tool = create_search_graph_tool(
                client,
                user_uuid=USER_UUID,
                pinned_params={"scope": "auto", "reranker": "node_distance"},
            )
            await _run(tool, query="query")
        assert any("invalid for scope='auto'" in record.message for record in caplog.records)

        client.graph.get_context.assert_called_once()
        assert "reranker" not in client.graph.get_context.call_args.kwargs

    @pytest.mark.asyncio
    async def test_model_provided_auto_scope_calls_get_context(self) -> None:
        """When the model itself chooses scope='auto', the tool calls
        graph.get_context and drops an incompatible reranker."""
        client = _make_mock_client()
        tool = create_search_graph_tool(client, user_uuid=USER_UUID)

        await _run(tool, query="query", scope="auto", reranker="node_distance")

        client.graph.get_context.assert_called_once()
        args, kwargs = client.graph.get_context.call_args
        assert args[0] == GRAPH_UUID
        assert kwargs["query"] == "query"
        assert "reranker" not in kwargs
        assert "limit" not in kwargs


class TestResultFormatting:
    @pytest.mark.asyncio
    async def test_auto_scope_returns_context(self) -> None:
        client = _make_mock_client()
        client.graph.get_context = AsyncMock(
            return_value=MagicMock(context="Assembled context block")
        )
        tool = create_search_graph_tool(
            client, user_uuid=USER_UUID, pinned_params={"scope": "auto"}
        )
        out = await _run(tool, query="q")
        assert out == "Assembled context block"

    @pytest.mark.asyncio
    async def test_formats_edges(self) -> None:
        client = _make_mock_client()
        edge = MagicMock()
        edge.fact = "Jane works at Acme"
        client.graph.search_edges = AsyncMock(return_value=FakePager([edge]))
        tool = create_search_graph_tool(client, user_uuid=USER_UUID)
        out = await _run(tool, query="q")
        assert "Jane works at Acme" in str(out)

    @pytest.mark.asyncio
    async def test_formats_observations(self) -> None:
        client = _make_mock_client()
        obs = MagicMock()
        obs.name = "Prefers async updates"
        obs.summary = "Communication pattern"
        client.graph.search_observations = AsyncMock(return_value=FakePager([obs]))
        tool = create_search_graph_tool(
            client, user_uuid=USER_UUID, pinned_params={"scope": "observations"}
        )
        out = await _run(tool, query="q")
        assert "Prefers async updates" in str(out)

    @pytest.mark.asyncio
    async def test_formats_thread_summaries(self) -> None:
        client = _make_mock_client()
        ts = MagicMock()
        ts.summary = "User discussed Q3 roadmap"
        client.graph.search_thread_summaries = AsyncMock(return_value=FakePager([ts]))
        tool = create_search_graph_tool(
            client, user_uuid=USER_UUID, pinned_params={"scope": "thread_summaries"}
        )
        out = await _run(tool, query="q")
        assert "User discussed Q3 roadmap" in str(out)

    @pytest.mark.asyncio
    async def test_search_errors_return_gracefully(self) -> None:
        client = _make_mock_client()
        client.graph.search_edges = AsyncMock(side_effect=RuntimeError("boom"))
        tool = create_search_graph_tool(client, user_uuid=USER_UUID)
        out = await _run(tool, query="q")
        assert "failed" in str(out).lower() or "error" in str(out).lower()
