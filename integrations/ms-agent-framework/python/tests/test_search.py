"""
Tests for the Zep graph-search tool (``create_zep_search_tool`` +
``expose_search_tool``/``search_pinned_params``/``search_hidden_params`` on
``ZepContextProvider``) with a mocked Zep client.

``create_zep_search_tool`` returns an ``agent_framework.FunctionTool`` built
from a hand-crafted JSON schema (pin-or-expose) rather than introspected from
a Python function signature, mirroring ``zep_pydantic_ai``'s
``create_zep_search_tool``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from agent_framework import Message
from zep_cloud.client import AsyncZep

from zep_ms_agent_framework import ZepContextProvider
from zep_ms_agent_framework.search import create_zep_search_tool


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
    async def test_searches_user_graph_by_default(self) -> None:
        client = _make_mock_client()
        client.graph.search.return_value = _make_result(edges=[_edge("fact")])
        tool = create_zep_search_tool(zep_client=client, user_id="user-1")

        await _call(tool, query="what do you know")

        kwargs = client.graph.search.call_args.kwargs
        assert kwargs["user_id"] == "user-1"
        assert "graph_id" not in kwargs

    @pytest.mark.asyncio
    async def test_searches_graph_id_when_set(self) -> None:
        client = _make_mock_client()
        client.graph.search.return_value = _make_result(edges=[])
        tool = create_zep_search_tool(zep_client=client, graph_id="docs-graph")

        await _call(tool, query="query")

        kwargs = client.graph.search.call_args.kwargs
        assert kwargs["graph_id"] == "docs-graph"
        assert "user_id" not in kwargs

    @pytest.mark.asyncio
    async def test_default_params(self) -> None:
        client = _make_mock_client()
        client.graph.search.return_value = _make_result(edges=[])
        tool = create_zep_search_tool(zep_client=client, user_id="user-1")

        await _call(tool, query="query")

        kwargs = client.graph.search.call_args.kwargs
        assert kwargs["scope"] == "edges"
        assert kwargs["reranker"] == "rrf"
        assert kwargs["limit"] == 10

    def test_custom_tool_name(self) -> None:
        client = _make_mock_client()
        tool = create_zep_search_tool(zep_client=client, user_id="user-1", name="search_memory")
        assert tool.name == "search_memory"


class TestResultFormatting:
    @pytest.mark.asyncio
    async def test_formats_edges(self) -> None:
        client = _make_mock_client()
        client.graph.search.return_value = _make_result(
            edges=[_edge("Alice works at Acme"), _edge("Bob hikes")]
        )
        tool = create_zep_search_tool(zep_client=client, user_id="user-1")
        out = await _call(tool, query="q", scope="edges")
        assert "Alice works at Acme" in out
        assert "Bob hikes" in out

    @pytest.mark.asyncio
    async def test_formats_nodes(self) -> None:
        client = _make_mock_client()
        client.graph.search.return_value = _make_result(nodes=[_node("Alice", "An engineer")])
        tool = create_zep_search_tool(zep_client=client, user_id="user-1")
        out = await _call(tool, query="q", scope="nodes")
        assert "Alice: An engineer" in out

    @pytest.mark.asyncio
    async def test_formats_episodes(self) -> None:
        client = _make_mock_client()
        client.graph.search.return_value = _make_result(episodes=[_episode("I work at Acme")])
        tool = create_zep_search_tool(zep_client=client, user_id="user-1")
        out = await _call(tool, query="q", scope="episodes")
        assert "I work at Acme" in out

    @pytest.mark.asyncio
    async def test_formats_observations(self) -> None:
        client = _make_mock_client()
        client.graph.search.return_value = _make_result(
            observations=[
                _observation("Prefers async updates", "Communication pattern"),
                _observation("Ships on Fridays"),
            ]
        )
        tool = create_zep_search_tool(zep_client=client, user_id="user-1")
        out = await _call(tool, query="q", scope="observations")
        assert out != "No results found."
        assert "Prefers async updates: Communication pattern" in out
        assert "Ships on Fridays" in out

    @pytest.mark.asyncio
    async def test_formats_thread_summaries(self) -> None:
        client = _make_mock_client()
        client.graph.search.return_value = _make_result(
            thread_summaries=[
                _thread_summary("User discussed Q3 roadmap"),
                _thread_summary(None, name="onboarding-thread"),
            ]
        )
        tool = create_zep_search_tool(zep_client=client, user_id="user-1")
        out = await _call(tool, query="q", scope="thread_summaries")
        assert "User discussed Q3 roadmap" in out
        assert "onboarding-thread" in out

    @pytest.mark.asyncio
    async def test_auto_scope_returns_context(self) -> None:
        client = _make_mock_client()
        client.graph.search.return_value = _make_result(context="Assembled context block")
        tool = create_zep_search_tool(zep_client=client, user_id="user-1")
        out = await _call(tool, query="q", scope="auto")
        assert out == "Assembled context block"

    @pytest.mark.asyncio
    async def test_empty_results(self) -> None:
        client = _make_mock_client()
        client.graph.search.return_value = _make_result(edges=[])
        tool = create_zep_search_tool(zep_client=client, user_id="user-1")
        out = await _call(tool, query="q")
        assert out == "No results found."


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_search_tool_errors_return_string(self) -> None:
        client = _make_mock_client()
        client.graph.search.side_effect = RuntimeError("boom")
        tool = create_zep_search_tool(zep_client=client, user_id="user-1")
        out = await _call(tool, query="q")
        assert "failed" in out.lower()


class TestPinOrExposeSchema:
    def test_search_tool_exposes_params_by_default(self) -> None:
        """By default (no pins), scope/reranker/limit/mmr_lambda/center_node_uuid
        are all in the model-facing schema alongside query."""
        client = _make_mock_client()
        tool = create_zep_search_tool(zep_client=client, user_id="user-1")
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
        client.graph.search.return_value = _make_result(nodes=[])
        tool = create_zep_search_tool(
            zep_client=client,
            user_id="user-1",
            search_pinned_params={"scope": "nodes", "limit": 5},
        )

        properties = tool.parameters()["properties"]
        assert "scope" not in properties
        assert "limit" not in properties
        assert "reranker" in properties

        await _call(tool, query="query")

        kwargs = client.graph.search.call_args.kwargs
        assert kwargs["scope"] == "nodes"
        assert kwargs["limit"] == 5

    def test_hidden_params_removed_from_schema_without_pinning(self) -> None:
        client = _make_mock_client()
        tool = create_zep_search_tool(
            zep_client=client,
            user_id="user-1",
            search_hidden_params={"mmr_lambda", "center_node_uuid"},
        )
        properties = tool.parameters()["properties"]

        assert "mmr_lambda" not in properties
        assert "center_node_uuid" not in properties
        assert "scope" in properties

    @pytest.mark.asyncio
    async def test_hidden_params_omitted_from_sdk_call(self) -> None:
        client = _make_mock_client()
        client.graph.search.return_value = _make_result(edges=[])
        tool = create_zep_search_tool(
            zep_client=client, user_id="user-1", search_hidden_params={"mmr_lambda"}
        )

        await _call(tool, query="query")

        assert "mmr_lambda" not in client.graph.search.call_args.kwargs

    def test_bfs_and_filters_constructor_only(self) -> None:
        client = _make_mock_client()
        tool = create_zep_search_tool(
            zep_client=client,
            user_id="user-1",
            search_filters={"node_labels": ["Person"]},
            bfs_origin_node_uuids=["uuid-1"],
        )
        properties = tool.parameters()["properties"]

        assert "search_filters" not in properties
        assert "bfs_origin_node_uuids" not in properties

    @pytest.mark.asyncio
    async def test_bfs_and_filters_sent_to_sdk(self) -> None:
        client = _make_mock_client()
        client.graph.search.return_value = _make_result(edges=[])
        tool = create_zep_search_tool(
            zep_client=client,
            user_id="user-1",
            search_filters={"node_labels": ["Person"]},
            bfs_origin_node_uuids=["uuid-1"],
        )

        await _call(tool, query="query")

        kwargs = client.graph.search.call_args.kwargs
        assert kwargs["search_filters"] == {"node_labels": ["Person"]}
        assert kwargs["bfs_origin_node_uuids"] == ["uuid-1"]


class TestExposeSearchToolOnProvider:
    @pytest.mark.asyncio
    async def test_expose_search_tool_extends_tools(self) -> None:
        """When expose_search_tool=True, before_run registers the tool on the
        fake SessionContext's extend_tools, called once with source_id."""
        client = MagicMock(spec=AsyncZep)
        client.user = MagicMock()
        client.user.add = AsyncMock()
        client.thread = MagicMock()
        client.thread.create = AsyncMock()
        client.thread.add_messages = AsyncMock()
        response = MagicMock()
        response.context = None
        client.thread.add_messages.return_value = response

        provider = ZepContextProvider(
            zep_client=client,
            user_id="user-1",
            thread_id="thread-1",
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

    @pytest.mark.asyncio
    async def test_search_tool_not_extended_by_default(self) -> None:
        client = MagicMock(spec=AsyncZep)
        client.user = MagicMock()
        client.user.add = AsyncMock()
        client.thread = MagicMock()
        client.thread.create = AsyncMock()
        client.thread.add_messages = AsyncMock()
        response = MagicMock()
        response.context = None
        client.thread.add_messages.return_value = response

        provider = ZepContextProvider(zep_client=client, user_id="user-1", thread_id="thread-1")

        ctx = MagicMock()
        ctx.input_messages = [Message("user", ["Hi"])]
        ctx.extend_instructions = MagicMock()
        ctx.extend_tools = MagicMock()
        ctx.response = None

        await provider.before_run(agent=MagicMock(), session=MagicMock(), context=ctx, state={})

        ctx.extend_tools.assert_not_called()
