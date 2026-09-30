"""
Tests for ``create_graph_search_tool`` -- a LiveKit ``RawFunctionTool`` built
from a hand-crafted JSON schema (:data:`zep_livekit.tools._SEARCH_PARAM_SPECS`)
via ``function_tool(raw_schema=...)``.

Raw-schema handler convention (confirmed against ``livekit.agents.llm.mcp``):
the wrapped function receives a single ``raw_arguments: dict[str, Any]``
argument. These tests call the tool as ``await tool(raw_arguments={...})``,
matching that convention (``RawFunctionTool.__call__`` forwards positional /
keyword args straight to the wrapped function).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from livekit.agents.llm import RawFunctionTool

from zep_livekit.exceptions import AgentConfigurationError
from zep_livekit.tools import MAX_SEARCH_LIMIT, create_graph_search_tool

GRAPH_UUID = "33333333-3333-3333-3333-333333333333"


def _make_page(items: list[MagicMock] | None = None) -> MagicMock:
    """Build a stand-in for the ``AsyncPager`` that a v4 search returns."""
    page = MagicMock()
    page.items = items if items is not None else []
    return page


def _make_context(context: str | None) -> MagicMock:
    response = MagicMock()
    response.context = context
    return response


def _edge(fact: str) -> MagicMock:
    e = MagicMock()
    e.fact = fact
    return e


def _node(name: str, summary: str | None = None) -> MagicMock:
    n = MagicMock()
    n.name = name
    n.summary = summary
    return n


def _mock_client() -> MagicMock:
    client = MagicMock()
    client.graph = MagicMock()
    client.graph.search_edges = AsyncMock(return_value=_make_page())
    client.graph.search_nodes = AsyncMock(return_value=_make_page())
    client.graph.search_episodes = AsyncMock(return_value=_make_page())
    client.graph.search_observations = AsyncMock(return_value=_make_page())
    client.graph.search_thread_summaries = AsyncMock(return_value=_make_page())
    client.graph.get_context = AsyncMock(return_value=_make_context(None))
    return client


async def _call(tool: RawFunctionTool, **raw_arguments: object) -> str:
    return await tool(raw_arguments=raw_arguments)  # type: ignore[no-any-return]


class TestRequiresTarget:
    def test_missing_graph_uuid_raises(self) -> None:
        with pytest.raises(AgentConfigurationError):
            create_graph_search_tool(_mock_client(), graph_uuid="")

    def test_graph_uuid_is_valid(self) -> None:
        tool = create_graph_search_tool(_mock_client(), graph_uuid=GRAPH_UUID)
        assert isinstance(tool, RawFunctionTool)


class TestSchemaExposure:
    def test_search_tool_exposes_params_by_default(self) -> None:
        tool = create_graph_search_tool(_mock_client(), graph_uuid=GRAPH_UUID)
        schema = tool.info.raw_schema["parameters"]
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

    def test_limit_schema_advertises_zep_bounds(self) -> None:
        """The model-facing schema must carry Zep's limit bounds so
        well-behaved models self-limit instead of sending e.g. 200."""
        tool = create_graph_search_tool(_mock_client(), graph_uuid=GRAPH_UUID)
        limit_prop = tool.info.raw_schema["parameters"]["properties"]["limit"]

        assert limit_prop["minimum"] == 1
        assert limit_prop["maximum"] == MAX_SEARCH_LIMIT

    def test_custom_name_and_description(self) -> None:
        tool = create_graph_search_tool(
            _mock_client(),
            graph_uuid=GRAPH_UUID,
            name="search_memory",
            description="Custom desc",
        )
        assert tool.info.name == "search_memory"
        assert tool.info.raw_schema["description"] == "Custom desc"


class TestPinnedAndHiddenParams:
    @pytest.mark.asyncio
    async def test_search_tool_pinned_params_hidden_and_sent(self) -> None:
        client = _mock_client()
        tool = create_graph_search_tool(
            client, graph_uuid=GRAPH_UUID, pinned_params={"scope": "nodes", "limit": 5}
        )

        properties = tool.info.raw_schema["parameters"]["properties"]
        assert "scope" not in properties
        assert "limit" not in properties
        assert "reranker" in properties

        await _call(tool, query="query")

        client.graph.search_edges.assert_not_called()
        assert client.graph.search_nodes.call_args.args[0] == GRAPH_UUID
        assert client.graph.search_nodes.call_args.kwargs["limit"] == 5

    @pytest.mark.asyncio
    async def test_search_tool_hidden_params_omitted_from_sdk_call(self) -> None:
        client = _mock_client()
        tool = create_graph_search_tool(client, graph_uuid=GRAPH_UUID, hidden_params={"mmr_lambda"})

        properties = tool.info.raw_schema["parameters"]["properties"]
        assert "mmr_lambda" not in properties

        await _call(tool, query="query")

        assert "mmr_lambda" not in client.graph.search_edges.call_args.kwargs

    def test_unknown_pinned_param_raises(self) -> None:
        with pytest.raises(AgentConfigurationError):
            create_graph_search_tool(
                _mock_client(), graph_uuid=GRAPH_UUID, pinned_params={"bogus": 1}
            )

    def test_unknown_hidden_param_raises(self) -> None:
        with pytest.raises(AgentConfigurationError):
            create_graph_search_tool(_mock_client(), graph_uuid=GRAPH_UUID, hidden_params={"bogus"})

    def test_bfs_and_filters_constructor_only(self) -> None:
        tool = create_graph_search_tool(
            _mock_client(),
            graph_uuid=GRAPH_UUID,
            search_filters={"node_labels": ["Person"]},
            bfs_origin_node_uuids=["uuid-1"],
        )
        properties = tool.info.raw_schema["parameters"]["properties"]
        assert "filters" not in properties
        assert "search_filters" not in properties
        assert "bfs_origin_node_uuids" not in properties

    @pytest.mark.asyncio
    async def test_bfs_and_filters_sent_to_sdk(self) -> None:
        client = _mock_client()
        tool = create_graph_search_tool(
            client,
            graph_uuid=GRAPH_UUID,
            search_filters={"node_labels": ["Person"]},
            bfs_origin_node_uuids=["uuid-1"],
        )

        await _call(tool, query="query")

        kwargs = client.graph.search_edges.call_args.kwargs
        assert kwargs["filters"] == {"node_labels": ["Person"]}
        assert kwargs["bfs_origin_node_uuids"] == ["uuid-1"]


class TestLimitClamping:
    @pytest.mark.asyncio
    async def test_model_provided_limit_clamped_to_ceiling(self) -> None:
        """A model-sent limit above Zep's ceiling is clamped, not forwarded
        verbatim (which would 400 and degrade the tool to an error string)."""
        client = _mock_client()
        tool = create_graph_search_tool(client, graph_uuid=GRAPH_UUID)

        await _call(tool, query="query", limit=200)

        assert client.graph.search_edges.call_args.kwargs["limit"] == MAX_SEARCH_LIMIT

    @pytest.mark.asyncio
    async def test_model_provided_limit_clamped_to_floor(self) -> None:
        client = _mock_client()
        tool = create_graph_search_tool(client, graph_uuid=GRAPH_UUID)

        await _call(tool, query="query", limit=0)

        assert client.graph.search_edges.call_args.kwargs["limit"] == 1

    @pytest.mark.asyncio
    async def test_model_provided_limit_in_range_forwarded_unchanged(self) -> None:
        client = _mock_client()
        tool = create_graph_search_tool(client, graph_uuid=GRAPH_UUID)

        await _call(tool, query="query", limit=25)

        assert client.graph.search_edges.call_args.kwargs["limit"] == 25

    @pytest.mark.asyncio
    async def test_pinned_limit_clamped_at_construction(self) -> None:
        client = _mock_client()
        tool = create_graph_search_tool(client, graph_uuid=GRAPH_UUID, pinned_params={"limit": 200})

        await _call(tool, query="query")

        assert client.graph.search_edges.call_args.kwargs["limit"] == MAX_SEARCH_LIMIT


class TestQueryHandling:
    @pytest.mark.asyncio
    async def test_search_tool_query_only_omits_unset_none_default_params(self) -> None:
        """When the model sends only 'query', mmr_lambda/center_node_uuid
        (no default) must be omitted from the SDK call entirely."""
        client = _mock_client()
        tool = create_graph_search_tool(client, graph_uuid=GRAPH_UUID)

        await _call(tool, query="find facts")

        call = client.graph.search_edges.call_args
        assert call.args[0] == GRAPH_UUID
        assert call.kwargs["query"] == "find facts"
        assert call.kwargs["reranker"] == "rrf"
        assert call.kwargs["limit"] == 10
        assert "scope" not in call.kwargs
        assert "mmr_lambda" not in call.kwargs
        assert "center_node_uuid" not in call.kwargs

    @pytest.mark.asyncio
    async def test_query_truncated_to_400(self) -> None:
        client = _mock_client()
        tool = create_graph_search_tool(client, graph_uuid=GRAPH_UUID)

        await _call(tool, query="x" * 1000)

        assert len(client.graph.search_edges.call_args.kwargs["query"]) == 400

    @pytest.mark.asyncio
    async def test_no_query_returns_error_string(self) -> None:
        tool = create_graph_search_tool(_mock_client(), graph_uuid=GRAPH_UUID)

        out = await _call(tool, query="")

        assert "error" in out.lower()


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_search_tool_errors_return_string(self) -> None:
        client = _mock_client()
        client.graph.search_edges = AsyncMock(side_effect=RuntimeError("boom"))
        tool = create_graph_search_tool(client, graph_uuid=GRAPH_UUID)

        out = await _call(tool, query="query")

        assert isinstance(out, str)
        assert "failed" in out.lower()


class TestAutoScope:
    @pytest.mark.asyncio
    async def test_auto_scope_uses_get_context(self) -> None:
        client = _mock_client()
        client.graph.get_context = AsyncMock(return_value=_make_context("ctx"))
        tool = create_graph_search_tool(
            client,
            graph_uuid=GRAPH_UUID,
            pinned_params={"scope": "auto", "reranker": "node_distance"},
        )

        out = await _call(tool, query="query")

        assert out == "ctx"
        client.graph.search_edges.assert_not_called()
        call = client.graph.get_context.call_args
        assert call.args[0] == GRAPH_UUID
        assert "reranker" not in call.kwargs
        assert "limit" not in call.kwargs


class TestResultFormatting:
    @pytest.mark.asyncio
    async def test_formats_edges(self) -> None:
        client = _mock_client()
        client.graph.search_edges = AsyncMock(
            return_value=_make_page([_edge("Alice works at Acme")])
        )
        tool = create_graph_search_tool(
            client, graph_uuid=GRAPH_UUID, pinned_params={"scope": "edges"}
        )

        out = await _call(tool, query="q")

        assert "Alice works at Acme" in out

    @pytest.mark.asyncio
    async def test_formats_nodes(self) -> None:
        client = _mock_client()
        client.graph.search_nodes = AsyncMock(
            return_value=_make_page([_node("Alice", "A customer")])
        )
        tool = create_graph_search_tool(
            client, graph_uuid=GRAPH_UUID, pinned_params={"scope": "nodes"}
        )

        out = await _call(tool, query="q")

        assert "Alice: A customer" in out

    @pytest.mark.asyncio
    async def test_empty_results(self) -> None:
        tool = create_graph_search_tool(_mock_client(), graph_uuid=GRAPH_UUID)

        out = await _call(tool, query="q")

        assert out == "No results found."
