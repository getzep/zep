"""
Tests for the Zep graph-search tool (``create_zep_search_tool`` +
``expose_search_tool``/``search_pinned_params``/``search_hidden_params`` on
``ZepContextProvider``) with a mocked Zep client.

``create_zep_search_tool`` returns an ``agent_framework.FunctionTool`` built
from a hand-crafted JSON schema (pin-or-expose) rather than introspected from
a Python function signature, mirroring ``zep_pydantic_ai``'s
``create_zep_search_tool``.

Zep v4 has one search method for each scope, and every method addresses the
graph by its UUID, so the tests assert on the per-scope method.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from agent_framework import Message
from zep_cloud.client import AsyncZep

from zep_ms_agent_framework import ZepContextProvider
from zep_ms_agent_framework.search import create_zep_search_tool

GRAPH_UUID = "22222222-2222-2222-2222-222222222222"
USER_UUID = "11111111-1111-1111-1111-111111111111"
THREAD_UUID = "33333333-3333-3333-3333-333333333333"


def _pager(items: list[object] | None) -> MagicMock:
    pager = MagicMock()
    pager.items = items
    return pager


def _make_mock_client() -> MagicMock:
    client = MagicMock(spec=AsyncZep)
    client.graph = MagicMock()
    for method in (
        "search_edges",
        "search_nodes",
        "search_episodes",
        "search_observations",
        "search_thread_summaries",
    ):
        setattr(client.graph, method, AsyncMock(return_value=_pager([])))
    client.graph.get_context = AsyncMock(return_value=MagicMock(context=None))
    return client


def _edge(fact: str) -> MagicMock:
    e = MagicMock()
    e.fact = fact
    return e


def _node(name: str, summary: str | None) -> MagicMock:
    n = MagicMock()
    n.name = name
    n.summary = summary
    return n


def _episode(content: str) -> MagicMock:
    ep = MagicMock()
    ep.content = content
    return ep


def _observation(name: str, summary: str | None = None) -> MagicMock:
    obs = MagicMock()
    obs.name = name
    obs.summary = summary
    return obs


def _thread_summary(summary: str | None, name: str = "thread") -> MagicMock:
    ts = MagicMock()
    ts.summary = summary
    ts.name = name
    return ts


async def _call(tool, **kwargs: object) -> str:
    """Invoke the tool's handler via .invoke(), unwrapping to plain text."""
    result = await tool.invoke(arguments=kwargs, skip_parsing=True)
    return str(result)


class TestSearchTargeting:
    @pytest.mark.asyncio
    async def test_addresses_the_graph_by_uuid(self) -> None:
        client = _make_mock_client()
        client.graph.search_edges.return_value = _pager([_edge("fact")])
        tool = create_zep_search_tool(zep_client=client, graph_uuid=GRAPH_UUID)

        await _call(tool, query="what do you know")

        assert client.graph.search_edges.call_args.args == (GRAPH_UUID,)

    def test_rejects_an_empty_graph_uuid(self) -> None:
        client = _make_mock_client()

        with pytest.raises(ValueError, match="graph_uuid"):
            create_zep_search_tool(zep_client=client, graph_uuid="")

    @pytest.mark.asyncio
    async def test_default_params(self) -> None:
        client = _make_mock_client()
        tool = create_zep_search_tool(zep_client=client, graph_uuid=GRAPH_UUID)

        await _call(tool, query="query")

        kwargs = client.graph.search_edges.call_args.kwargs
        assert kwargs["reranker"] == "rrf"
        assert kwargs["limit"] == 10
        assert "scope" not in kwargs

    def test_custom_tool_name(self) -> None:
        client = _make_mock_client()
        tool = create_zep_search_tool(
            zep_client=client, graph_uuid=GRAPH_UUID, name="search_memory"
        )
        assert tool.name == "search_memory"


class TestResultFormatting:
    @pytest.mark.asyncio
    async def test_formats_edges(self) -> None:
        client = _make_mock_client()
        client.graph.search_edges.return_value = _pager(
            [_edge("Alice works at Acme"), _edge("Bob hikes")]
        )
        tool = create_zep_search_tool(zep_client=client, graph_uuid=GRAPH_UUID)
        out = await _call(tool, query="q", scope="edges")
        assert "Alice works at Acme" in out
        assert "Bob hikes" in out

    @pytest.mark.asyncio
    async def test_formats_nodes(self) -> None:
        client = _make_mock_client()
        client.graph.search_nodes.return_value = _pager([_node("Alice", "An engineer")])
        tool = create_zep_search_tool(zep_client=client, graph_uuid=GRAPH_UUID)
        out = await _call(tool, query="q", scope="nodes")
        assert "Alice: An engineer" in out

    @pytest.mark.asyncio
    async def test_formats_episodes(self) -> None:
        client = _make_mock_client()
        client.graph.search_episodes.return_value = _pager([_episode("I work at Acme")])
        tool = create_zep_search_tool(zep_client=client, graph_uuid=GRAPH_UUID)
        out = await _call(tool, query="q", scope="episodes")
        assert "I work at Acme" in out

    @pytest.mark.asyncio
    async def test_formats_observations(self) -> None:
        client = _make_mock_client()
        client.graph.search_observations.return_value = _pager(
            [
                _observation("Prefers async updates", "Communication pattern"),
                _observation("Ships on Fridays"),
            ]
        )
        tool = create_zep_search_tool(zep_client=client, graph_uuid=GRAPH_UUID)
        out = await _call(tool, query="q", scope="observations")
        assert out != "No results found."
        assert "Prefers async updates: Communication pattern" in out
        assert "Ships on Fridays" in out

    @pytest.mark.asyncio
    async def test_formats_thread_summaries(self) -> None:
        client = _make_mock_client()
        client.graph.search_thread_summaries.return_value = _pager(
            [
                _thread_summary("User discussed Q3 roadmap"),
                _thread_summary(None, name="onboarding-thread"),
            ]
        )
        tool = create_zep_search_tool(zep_client=client, graph_uuid=GRAPH_UUID)
        out = await _call(tool, query="q", scope="thread_summaries")
        assert "User discussed Q3 roadmap" in out
        assert "onboarding-thread" in out

    @pytest.mark.asyncio
    async def test_auto_scope_returns_context(self) -> None:
        client = _make_mock_client()
        client.graph.get_context.return_value = MagicMock(context="Assembled context block")
        tool = create_zep_search_tool(zep_client=client, graph_uuid=GRAPH_UUID)
        out = await _call(tool, query="q", scope="auto")

        assert out == "Assembled context block"
        assert client.graph.get_context.call_args.args == (GRAPH_UUID,)
        # graph.get_context takes no ranking parameters.
        kwargs = client.graph.get_context.call_args.kwargs
        assert "limit" not in kwargs
        assert "reranker" not in kwargs

    @pytest.mark.asyncio
    async def test_empty_results(self) -> None:
        client = _make_mock_client()
        tool = create_zep_search_tool(zep_client=client, graph_uuid=GRAPH_UUID)
        out = await _call(tool, query="q")
        assert out == "No results found."


class TestModelProvidedArguments:
    @pytest.mark.asyncio
    async def test_null_model_argument_falls_back_to_default(self) -> None:
        """An explicit JSON null from the model is treated as absent: the spec
        default applies instead of forwarding None to the SDK.

        Calls the raw handler (``tool.func``) directly: ``FunctionTool.invoke``
        may reject nulls itself depending on the framework version, but the
        handler must be null-safe regardless.
        """
        client = _make_mock_client()
        client.graph.search_edges.return_value = _pager([_edge("Alice works at Acme")])
        tool = create_zep_search_tool(zep_client=client, graph_uuid=GRAPH_UUID)

        out = await tool.func(query="q", scope=None)

        client.graph.search_edges.assert_called_once()
        assert "Alice works at Acme" in out

    @pytest.mark.asyncio
    async def test_null_model_argument_without_default_is_omitted(self) -> None:
        client = _make_mock_client()
        tool = create_zep_search_tool(zep_client=client, graph_uuid=GRAPH_UUID)

        await tool.func(query="q", mmr_lambda=None, center_node_uuid=None)

        kwargs = client.graph.search_edges.call_args.kwargs
        assert "mmr_lambda" not in kwargs
        assert "center_node_uuid" not in kwargs

    @pytest.mark.asyncio
    async def test_model_limit_above_ceiling_is_clamped(self) -> None:
        client = _make_mock_client()
        tool = create_zep_search_tool(zep_client=client, graph_uuid=GRAPH_UUID)

        await _call(tool, query="q", limit=200)

        assert client.graph.search_edges.call_args.kwargs["limit"] == 50

    @pytest.mark.asyncio
    async def test_model_limit_below_floor_is_clamped(self) -> None:
        client = _make_mock_client()
        tool = create_zep_search_tool(zep_client=client, graph_uuid=GRAPH_UUID)

        await _call(tool, query="q", limit=0)

        assert client.graph.search_edges.call_args.kwargs["limit"] == 1

    def test_limit_schema_carries_bounds(self) -> None:
        client = _make_mock_client()
        tool = create_zep_search_tool(zep_client=client, graph_uuid=GRAPH_UUID)
        limit_prop = tool.parameters()["properties"]["limit"]

        assert limit_prop["minimum"] == 1
        assert limit_prop["maximum"] == 50


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_search_tool_errors_return_string(self) -> None:
        client = _make_mock_client()
        client.graph.search_edges.side_effect = RuntimeError("boom")
        tool = create_zep_search_tool(zep_client=client, graph_uuid=GRAPH_UUID)
        out = await _call(tool, query="q")
        assert "failed" in out.lower()

    @pytest.mark.asyncio
    async def test_auto_scope_errors_return_string(self) -> None:
        client = _make_mock_client()
        client.graph.get_context.side_effect = RuntimeError("boom")
        tool = create_zep_search_tool(zep_client=client, graph_uuid=GRAPH_UUID)
        out = await _call(tool, query="q", scope="auto")
        assert "failed" in out.lower()


class TestPinOrExposeSchema:
    def test_search_tool_exposes_params_by_default(self) -> None:
        """By default (no pins), scope/reranker/limit/mmr_lambda/center_node_uuid
        are all in the model-facing schema alongside query."""
        client = _make_mock_client()
        tool = create_zep_search_tool(zep_client=client, graph_uuid=GRAPH_UUID)
        schema = tool.parameters()
        properties = schema["properties"]

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
        assert schema["required"] == ["query"]

    @pytest.mark.asyncio
    async def test_search_tool_pinned_params_hidden_and_sent(self) -> None:
        client = _make_mock_client()
        tool = create_zep_search_tool(
            zep_client=client,
            graph_uuid=GRAPH_UUID,
            search_pinned_params={"scope": "nodes", "limit": 5},
        )

        properties = tool.parameters()["properties"]
        assert "scope" not in properties
        assert "limit" not in properties
        assert "reranker" in properties

        await _call(tool, query="query")

        client.graph.search_nodes.assert_called_once()
        assert client.graph.search_nodes.call_args.kwargs["limit"] == 5

    def test_hidden_params_removed_from_schema_without_pinning(self) -> None:
        client = _make_mock_client()
        tool = create_zep_search_tool(
            zep_client=client,
            graph_uuid=GRAPH_UUID,
            search_hidden_params={"mmr_lambda", "center_node_uuid"},
        )
        properties = tool.parameters()["properties"]

        assert "mmr_lambda" not in properties
        assert "center_node_uuid" not in properties
        assert "scope" in properties

    @pytest.mark.asyncio
    async def test_hidden_params_omitted_from_sdk_call(self) -> None:
        client = _make_mock_client()
        tool = create_zep_search_tool(
            zep_client=client, graph_uuid=GRAPH_UUID, search_hidden_params={"mmr_lambda"}
        )

        await _call(tool, query="query")

        assert "mmr_lambda" not in client.graph.search_edges.call_args.kwargs

    def test_bfs_and_filters_constructor_only(self) -> None:
        client = _make_mock_client()
        tool = create_zep_search_tool(
            zep_client=client,
            graph_uuid=GRAPH_UUID,
            search_filters={"node_labels": ["Person"]},
            bfs_origin_node_uuids=["uuid-1"],
        )
        properties = tool.parameters()["properties"]

        assert "search_filters" not in properties
        assert "bfs_origin_node_uuids" not in properties

    @pytest.mark.asyncio
    async def test_bfs_and_filters_sent_to_sdk(self) -> None:
        client = _make_mock_client()
        tool = create_zep_search_tool(
            zep_client=client,
            graph_uuid=GRAPH_UUID,
            search_filters={"node_labels": ["Person"]},
            bfs_origin_node_uuids=["uuid-1"],
        )

        await _call(tool, query="query")

        kwargs = client.graph.search_edges.call_args.kwargs
        assert kwargs["filters"] == {"node_labels": ["Person"]}
        assert kwargs["bfs_origin_node_uuids"] == ["uuid-1"]


class TestExposeSearchToolOnProvider:
    @staticmethod
    def _provider_client() -> MagicMock:
        client = MagicMock(spec=AsyncZep)
        client.thread = MagicMock()
        client.thread.add_messages = AsyncMock()
        response = MagicMock()
        response.context = None
        client.thread.add_messages.return_value = response
        return client

    @pytest.mark.asyncio
    async def test_expose_search_tool_extends_tools(self) -> None:
        """When expose_search_tool=True, before_run registers the tool on the
        fake SessionContext's extend_tools, called once with source_id."""
        client = self._provider_client()

        provider = ZepContextProvider(
            zep_client=client,
            user_uuid=USER_UUID,
            thread_uuid=THREAD_UUID,
            graph_uuid=GRAPH_UUID,
            expose_search_tool=True,
        )

        ctx = MagicMock()
        ctx.input_messages = [Message("user", ["Hi"])]
        ctx.extend_instructions = MagicMock()
        ctx.extend_tools = MagicMock()
        ctx.response = None

        await provider.before_run(agent=MagicMock(), session=MagicMock(), context=ctx, state={})

        ctx.extend_tools.assert_called_once()
        source_id, tools = ctx.extend_tools.call_args.args
        assert source_id == "zep"
        assert len(tools) == 1

    def test_expose_search_tool_requires_a_graph_uuid(self) -> None:
        client = self._provider_client()

        with pytest.raises(ValueError, match="graph_uuid"):
            ZepContextProvider(
                zep_client=client,
                user_uuid=USER_UUID,
                thread_uuid=THREAD_UUID,
                expose_search_tool=True,
            )

    @pytest.mark.asyncio
    async def test_search_tool_not_extended_by_default(self) -> None:
        client = self._provider_client()

        provider = ZepContextProvider(
            zep_client=client, user_uuid=USER_UUID, thread_uuid=THREAD_UUID
        )

        ctx = MagicMock()
        ctx.input_messages = [Message("user", ["Hi"])]
        ctx.extend_instructions = MagicMock()
        ctx.extend_tools = MagicMock()
        ctx.response = None

        await provider.before_run(agent=MagicMock(), session=MagicMock(), context=ctx, state={})

        ctx.extend_tools.assert_not_called()
