"""
Tests for the zep-ag2 integration package.

Covers: imports, tool factories, ZepMemoryManager, ZepGraphMemoryManager,
and AG2 registration patterns. All Zep SDK calls are mocked.
"""

import inspect
from typing import get_type_hints
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zep_ag2 import (
    ZepAG2ConfigError,
    ZepAG2Error,
    ZepAG2MemoryError,
    ZepDependencyError,
    ZepGraphMemoryManager,
    ZepMemoryManager,
    create_add_graph_data_tool,
    create_add_memory_tool,
    create_search_graph_tool,
    create_search_memory_tool,
    register_all_tools,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_mock_graph_results(
    edges: list[MagicMock] | None = None,
    nodes: list[MagicMock] | None = None,
    episodes: list[MagicMock] | None = None,
) -> MagicMock:
    r = MagicMock()
    r.edges = edges or []
    r.nodes = nodes or []
    r.episodes = episodes or []
    return r


def _mock_zep_client() -> MagicMock:
    """Create a mock AsyncZep client with all needed sub-clients."""
    from zep_cloud.client import AsyncZep

    client = MagicMock(spec=AsyncZep)

    # _client_wrapper.api_key — needed by tool factories
    client._client_wrapper = MagicMock()
    client._client_wrapper.api_key = "fake-api-key"

    # Thread sub-client
    client.thread = MagicMock()
    client.thread.get = AsyncMock()
    client.thread.create = AsyncMock()
    client.thread.add_messages = AsyncMock()
    client.thread.get_user_context = AsyncMock(return_value=MagicMock(context="Alice likes hiking"))
    mock_thread = MagicMock()
    mock_thread.messages = []
    client.thread.get.return_value = mock_thread

    # Graph sub-client
    client.graph = MagicMock()
    client.graph.search = AsyncMock(return_value=_make_mock_graph_results())
    client.graph.add = AsyncMock()

    # Graph episode sub-client
    client.graph.episode = MagicMock()
    mock_episodes = MagicMock()
    mock_episodes.episodes = []
    client.graph.episode.get_by_graph_id = AsyncMock(return_value=mock_episodes)

    return client


@pytest.fixture
def mock_zep_client() -> MagicMock:
    return _mock_zep_client()


@pytest.fixture
def mock_ag2_agent() -> MagicMock:
    agent = MagicMock()
    agent.system_message = "You are a helpful assistant."
    agent.update_system_message = MagicMock()
    agent.register_for_llm = MagicMock(return_value=lambda f: f)
    return agent


@pytest.fixture
def mock_ag2_executor() -> MagicMock:
    executor = MagicMock()
    executor.register_for_execution = MagicMock(return_value=lambda f: f)
    return executor


# ---------------------------------------------------------------------------
# Helper: patch AsyncZep so tool coroutines use a mock client
# ---------------------------------------------------------------------------


def _patched_tool_call(tool_fn, mock_inner_client, **kwargs):  # type: ignore[no-untyped-def]
    """Call a sync tool function while patching the AsyncZep it creates internally."""
    with patch("zep_ag2.tools.AsyncZep", return_value=mock_inner_client):
        return tool_fn(**kwargs)


# ---------------------------------------------------------------------------
# 1. Import tests
# ---------------------------------------------------------------------------


class TestImports:
    def test_package_import(self) -> None:
        import zep_ag2

        assert zep_ag2 is not None

    def test_version(self) -> None:
        import zep_ag2

        assert hasattr(zep_ag2, "__version__")
        assert zep_ag2.__version__ == "0.1.0"

    def test_all_public_symbols(self) -> None:
        assert ZepMemoryManager is not None
        assert ZepGraphMemoryManager is not None
        assert create_search_memory_tool is not None
        assert create_add_memory_tool is not None
        assert create_search_graph_tool is not None
        assert create_add_graph_data_tool is not None
        assert register_all_tools is not None

    def test_exception_hierarchy(self) -> None:
        assert issubclass(ZepAG2ConfigError, ZepAG2Error)
        assert issubclass(ZepAG2MemoryError, ZepAG2Error)
        assert issubclass(ZepDependencyError, ImportError)


# ---------------------------------------------------------------------------
# 2. Tool factory tests
# ---------------------------------------------------------------------------


class TestToolFactories:
    def test_create_search_memory_tool_returns_sync_callable(
        self, mock_zep_client: MagicMock
    ) -> None:
        tool = create_search_memory_tool(mock_zep_client, user_id="u1")
        assert callable(tool)
        # Tools are now sync (not async)
        assert not inspect.iscoroutinefunction(tool)

    def test_create_add_memory_tool_returns_sync_callable(self, mock_zep_client: MagicMock) -> None:
        tool = create_add_memory_tool(mock_zep_client, user_id="u1", session_id="s1")
        assert callable(tool)
        assert not inspect.iscoroutinefunction(tool)

    def test_create_search_graph_tool_returns_sync_callable(
        self, mock_zep_client: MagicMock
    ) -> None:
        tool = create_search_graph_tool(mock_zep_client, user_id="u1")
        assert callable(tool)
        assert not inspect.iscoroutinefunction(tool)

    def test_create_add_graph_data_tool_returns_sync_callable(
        self, mock_zep_client: MagicMock
    ) -> None:
        tool = create_add_graph_data_tool(mock_zep_client, user_id="u1")
        assert callable(tool)
        assert not inspect.iscoroutinefunction(tool)

    def test_search_graph_tool_requires_id(self, mock_zep_client: MagicMock) -> None:
        with pytest.raises(ZepAG2MemoryError, match="Either user_id or graph_id"):
            create_search_graph_tool(mock_zep_client)

    def test_search_graph_tool_rejects_both_ids(self, mock_zep_client: MagicMock) -> None:
        with pytest.raises(ZepAG2MemoryError, match="Only one of"):
            create_search_graph_tool(mock_zep_client, user_id="u1", graph_id="g1")

    def test_add_graph_data_tool_requires_id(self, mock_zep_client: MagicMock) -> None:
        with pytest.raises(ZepAG2MemoryError, match="Either user_id or graph_id"):
            create_add_graph_data_tool(mock_zep_client)

    def test_add_graph_data_tool_rejects_both_ids(self, mock_zep_client: MagicMock) -> None:
        with pytest.raises(ZepAG2MemoryError, match="Only one of"):
            create_add_graph_data_tool(mock_zep_client, user_id="u1", graph_id="g1")

    def test_search_memory_tool_calls_sdk(self, mock_zep_client: MagicMock) -> None:
        inner = _mock_zep_client()
        tool = create_search_memory_tool(mock_zep_client, user_id="u1")
        result = _patched_tool_call(tool, inner, query="hiking", limit=3)

        inner.graph.search.assert_called_once_with(user_id="u1", query="hiking", limit=3)
        assert isinstance(result, str)

    def test_add_memory_tool_with_session(self, mock_zep_client: MagicMock) -> None:
        inner = _mock_zep_client()
        tool = create_add_memory_tool(mock_zep_client, user_id="u1", session_id="s1")
        result = _patched_tool_call(tool, inner, content="Hello world", role="user")

        inner.thread.add_messages.assert_called_once()
        assert "successfully" in result.lower()

    def test_add_memory_tool_without_session_uses_graph(self, mock_zep_client: MagicMock) -> None:
        inner = _mock_zep_client()
        tool = create_add_memory_tool(mock_zep_client, user_id="u1")
        result = _patched_tool_call(tool, inner, content="Some fact")

        inner.graph.add.assert_called_once_with(user_id="u1", type="text", data="Some fact")
        assert "knowledge graph" in result.lower()

    def test_search_graph_tool_with_graph_id(self, mock_zep_client: MagicMock) -> None:
        inner = _mock_zep_client()
        tool = create_search_graph_tool(mock_zep_client, graph_id="g1")
        _patched_tool_call(tool, inner, query="Python", limit=3, scope="edges")

        inner.graph.search.assert_called_once()
        call_kwargs = inner.graph.search.call_args.kwargs
        assert call_kwargs.get("graph_id") == "g1"

    def test_add_graph_data_tool_calls_sdk(self, mock_zep_client: MagicMock) -> None:
        inner = _mock_zep_client()
        tool = create_add_graph_data_tool(mock_zep_client, user_id="u1")
        result = _patched_tool_call(tool, inner, data="Python is great", data_type="text")

        inner.graph.add.assert_called_once()
        assert "successfully" in result.lower()

    def test_tool_error_handling(self, mock_zep_client: MagicMock) -> None:
        inner = _mock_zep_client()
        inner.graph.search = AsyncMock(side_effect=Exception("API error"))
        tool = create_search_memory_tool(mock_zep_client, user_id="u1")
        result = _patched_tool_call(tool, inner, query="test")

        assert "error" in result.lower()

    def test_tool_annotations(self, mock_zep_client: MagicMock) -> None:
        """Verify that tool functions have Annotated type hints for AG2 compatibility."""
        tool = create_search_memory_tool(mock_zep_client, user_id="u1")
        hints = get_type_hints(tool, include_extras=True)

        assert "query" in hints
        origin = getattr(hints["query"], "__metadata__", None)
        assert origin is not None, "query param should use Annotated"


# ---------------------------------------------------------------------------
# 3. ZepMemoryManager tests
# ---------------------------------------------------------------------------


class TestZepMemoryManager:
    def test_init_validates_client(self) -> None:
        with pytest.raises(ZepAG2ConfigError, match="AsyncZep"):
            ZepMemoryManager(client="bad", user_id="u1")  # type: ignore[arg-type]

    def test_init_requires_user_id(self, mock_zep_client: MagicMock) -> None:
        with pytest.raises(ZepAG2ConfigError, match="user_id"):
            ZepMemoryManager(client=mock_zep_client, user_id="")

    def test_init_success(self, mock_zep_client: MagicMock) -> None:
        mgr = ZepMemoryManager(mock_zep_client, user_id="u1", session_id="s1")
        assert mgr.user_id == "u1"
        assert mgr.session_id == "s1"
        assert mgr.client is mock_zep_client

    @pytest.mark.asyncio
    async def test_get_memory_context_with_query(self, mock_zep_client: MagicMock) -> None:
        mock_edge = MagicMock()
        mock_edge.fact = "Alice likes hiking"
        mock_zep_client.graph.search = AsyncMock(
            return_value=_make_mock_graph_results(edges=[mock_edge])
        )

        mgr = ZepMemoryManager(mock_zep_client, user_id="u1")
        context = await mgr.get_memory_context(query="hiking")

        assert "hiking" in context
        mock_zep_client.graph.search.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_memory_context_with_session(self, mock_zep_client: MagicMock) -> None:
        mgr = ZepMemoryManager(mock_zep_client, user_id="u1", session_id="s1")
        context = await mgr.get_memory_context()

        assert "Alice likes hiking" in context
        mock_zep_client.thread.get_user_context.assert_called_once()

    @pytest.mark.asyncio
    async def test_enrich_system_message(
        self, mock_zep_client: MagicMock, mock_ag2_agent: MagicMock
    ) -> None:
        mgr = ZepMemoryManager(mock_zep_client, user_id="u1", session_id="s1")
        await mgr.enrich_system_message(mock_ag2_agent)

        mock_ag2_agent.update_system_message.assert_called_once()
        call_args = mock_ag2_agent.update_system_message.call_args[0][0]
        assert "Relevant Memory Context" in call_args

    @pytest.mark.asyncio
    async def test_enrich_system_message_no_context(
        self, mock_zep_client: MagicMock, mock_ag2_agent: MagicMock
    ) -> None:
        mock_zep_client.thread.get_user_context = AsyncMock(return_value=MagicMock(context=None))
        mgr = ZepMemoryManager(mock_zep_client, user_id="u1", session_id="s1")
        await mgr.enrich_system_message(mock_ag2_agent)

        mock_ag2_agent.update_system_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_add_messages(self, mock_zep_client: MagicMock) -> None:
        mgr = ZepMemoryManager(mock_zep_client, user_id="u1", session_id="s1")
        await mgr.add_messages([{"content": "Hi", "role": "user"}])

        mock_zep_client.thread.add_messages.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_messages_requires_session(self, mock_zep_client: MagicMock) -> None:
        mgr = ZepMemoryManager(mock_zep_client, user_id="u1")
        with pytest.raises(ZepAG2ConfigError, match="session_id"):
            await mgr.add_messages([{"content": "Hi", "role": "user"}])

    @pytest.mark.asyncio
    async def test_get_session_facts(self, mock_zep_client: MagicMock) -> None:
        mgr = ZepMemoryManager(mock_zep_client, user_id="u1", session_id="s1")
        facts = await mgr.get_session_facts()

        assert isinstance(facts, list)
        assert len(facts) == 1
        assert "hiking" in facts[0]

    @pytest.mark.asyncio
    async def test_get_session_facts_requires_session(self, mock_zep_client: MagicMock) -> None:
        mgr = ZepMemoryManager(mock_zep_client, user_id="u1")
        with pytest.raises(ZepAG2ConfigError, match="session_id"):
            await mgr.get_session_facts()


# ---------------------------------------------------------------------------
# 4. ZepGraphMemoryManager tests
# ---------------------------------------------------------------------------


class TestZepGraphMemoryManager:
    def test_init_validates_client(self) -> None:
        with pytest.raises(ZepAG2ConfigError, match="AsyncZep"):
            ZepGraphMemoryManager(client="bad", graph_id="g1")  # type: ignore[arg-type]

    def test_init_requires_graph_id(self, mock_zep_client: MagicMock) -> None:
        with pytest.raises(ZepAG2ConfigError, match="graph_id"):
            ZepGraphMemoryManager(client=mock_zep_client, graph_id="")

    def test_init_success(self, mock_zep_client: MagicMock) -> None:
        mgr = ZepGraphMemoryManager(mock_zep_client, graph_id="g1")
        assert mgr.graph_id == "g1"
        assert mgr.client is mock_zep_client

    @pytest.mark.asyncio
    async def test_search(self, mock_zep_client: MagicMock) -> None:
        mock_edge = MagicMock()
        mock_edge.fact = "Python is popular"
        mock_edge.name = "popularity"
        mock_edge.attributes = {}
        mock_edge.created_at = "2024-01-01"
        mock_zep_client.graph.search = AsyncMock(
            return_value=_make_mock_graph_results(edges=[mock_edge])
        )

        mgr = ZepGraphMemoryManager(mock_zep_client, graph_id="g1")
        results = await mgr.search("Python")

        assert len(results) == 1
        assert results[0]["content"] == "Python is popular"
        assert results[0]["type"] == "edge"

    @pytest.mark.asyncio
    async def test_add_data(self, mock_zep_client: MagicMock) -> None:
        mgr = ZepGraphMemoryManager(mock_zep_client, graph_id="g1")
        success = await mgr.add_data("Python is great", data_type="text")

        assert success is True
        mock_zep_client.graph.add.assert_called_once_with(
            graph_id="g1", type="text", data="Python is great"
        )

    @pytest.mark.asyncio
    async def test_add_data_error(self, mock_zep_client: MagicMock) -> None:
        mock_zep_client.graph.add = AsyncMock(side_effect=Exception("API error"))
        mgr = ZepGraphMemoryManager(mock_zep_client, graph_id="g1")
        success = await mgr.add_data("data")

        assert success is False

    @pytest.mark.asyncio
    async def test_enrich_system_message_with_query(
        self, mock_zep_client: MagicMock, mock_ag2_agent: MagicMock
    ) -> None:
        mock_edge = MagicMock()
        mock_edge.fact = "Python is popular"
        mock_edge.name = "popularity"
        mock_edge.attributes = {}
        mock_edge.created_at = "2024-01-01"
        mock_zep_client.graph.search = AsyncMock(
            return_value=_make_mock_graph_results(edges=[mock_edge])
        )

        mgr = ZepGraphMemoryManager(mock_zep_client, graph_id="g1")
        await mgr.enrich_system_message(mock_ag2_agent, query="Python")

        mock_ag2_agent.update_system_message.assert_called_once()
        call_args = mock_ag2_agent.update_system_message.call_args[0][0]
        assert "Knowledge Graph Context" in call_args

    @pytest.mark.asyncio
    async def test_search_error_returns_empty(self, mock_zep_client: MagicMock) -> None:
        mock_zep_client.graph.search = AsyncMock(side_effect=Exception("API error"))
        mgr = ZepGraphMemoryManager(mock_zep_client, graph_id="g1")
        results = await mgr.search("test")

        assert results == []


# ---------------------------------------------------------------------------
# 5. Integration pattern tests
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_register_all_tools(
        self,
        mock_zep_client: MagicMock,
        mock_ag2_agent: MagicMock,
        mock_ag2_executor: MagicMock,
    ) -> None:
        tools = register_all_tools(
            agent=mock_ag2_agent,
            executor=mock_ag2_executor,
            client=mock_zep_client,
            user_id="u1",
            session_id="s1",
        )

        assert len(tools) == 4
        assert "search_memory" in tools
        assert "add_memory" in tools
        assert "search_graph" in tools
        assert "add_graph_data" in tools

        assert mock_ag2_agent.register_for_llm.call_count == 4
        assert mock_ag2_executor.register_for_execution.call_count == 4

    def test_register_all_tools_with_graph_id(
        self,
        mock_zep_client: MagicMock,
        mock_ag2_agent: MagicMock,
        mock_ag2_executor: MagicMock,
    ) -> None:
        tools = register_all_tools(
            agent=mock_ag2_agent,
            executor=mock_ag2_executor,
            client=mock_zep_client,
            user_id="u1",
            graph_id="g1",
        )

        assert len(tools) == 4

    def test_tool_annotations_for_ag2(self, mock_zep_client: MagicMock) -> None:
        """Verify all tool functions have Annotated parameters for AG2 compatibility."""
        tools = [
            create_search_memory_tool(mock_zep_client, user_id="u1"),
            create_add_memory_tool(mock_zep_client, user_id="u1"),
            create_search_graph_tool(mock_zep_client, user_id="u1"),
            create_add_graph_data_tool(mock_zep_client, user_id="u1"),
        ]

        for tool in tools:
            hints = get_type_hints(tool, include_extras=True)
            has_annotated = any(
                getattr(h, "__metadata__", None) is not None for h in hints.values()
            )
            assert has_annotated, f"{tool.__name__} missing Annotated parameters"


# ---------------------------------------------------------------------------
# 6. Additional coverage tests
# ---------------------------------------------------------------------------


class TestFormattingHelpers:
    """Test _format_graph_results through tool calls."""

    def test_search_memory_with_edges_nodes_episodes(self, mock_zep_client: MagicMock) -> None:
        inner = _mock_zep_client()
        mock_edge = MagicMock()
        mock_edge.fact = "Alice likes hiking"
        mock_node = MagicMock()
        mock_node.name = "Alice"
        mock_node.summary = "A person who likes hiking"
        mock_episode = MagicMock()
        mock_episode.content = "Discussed hiking plans"

        inner.graph.search = AsyncMock(
            return_value=_make_mock_graph_results(
                edges=[mock_edge], nodes=[mock_node], episodes=[mock_episode]
            )
        )

        tool = create_search_memory_tool(mock_zep_client, user_id="u1")
        result = _patched_tool_call(tool, inner, query="hiking")

        assert "Alice likes hiking" in result
        assert "Alice" in result
        assert "Discussed hiking plans" in result

    def test_search_memory_no_results(self, mock_zep_client: MagicMock) -> None:
        inner = _mock_zep_client()
        tool = create_search_memory_tool(mock_zep_client, user_id="u1")
        result = _patched_tool_call(tool, inner, query="nothing")

        assert "No results found" in result

    def test_search_memory_node_no_summary(self, mock_zep_client: MagicMock) -> None:
        inner = _mock_zep_client()
        mock_node = MagicMock()
        mock_node.name = "Bob"
        mock_node.summary = None
        inner.graph.search = AsyncMock(return_value=_make_mock_graph_results(nodes=[mock_node]))

        tool = create_search_memory_tool(mock_zep_client, user_id="u1")
        result = _patched_tool_call(tool, inner, query="Bob")

        assert "No summary" in result


class TestAddMemoryEdgeCases:
    def test_add_memory_graph_error(self, mock_zep_client: MagicMock) -> None:
        inner = _mock_zep_client()
        inner.graph.add = AsyncMock(side_effect=Exception("Graph error"))
        tool = create_add_memory_tool(mock_zep_client, user_id="u1")
        result = _patched_tool_call(tool, inner, content="test")

        assert "error" in result.lower()

    def test_add_memory_thread_error(self, mock_zep_client: MagicMock) -> None:
        inner = _mock_zep_client()
        inner.thread.add_messages = AsyncMock(side_effect=Exception("Thread error"))
        tool = create_add_memory_tool(mock_zep_client, user_id="u1", session_id="s1")
        result = _patched_tool_call(tool, inner, content="test", role="user")

        assert "error" in result.lower()


class TestSearchGraphEdgeCases:
    def test_search_graph_error(self, mock_zep_client: MagicMock) -> None:
        inner = _mock_zep_client()
        inner.graph.search = AsyncMock(side_effect=Exception("Search error"))
        tool = create_search_graph_tool(mock_zep_client, user_id="u1")
        result = _patched_tool_call(tool, inner, query="test")

        assert "error" in result.lower()

    def test_add_graph_data_error(self, mock_zep_client: MagicMock) -> None:
        inner = _mock_zep_client()
        inner.graph.add = AsyncMock(side_effect=Exception("Add error"))
        tool = create_add_graph_data_tool(mock_zep_client, user_id="u1")
        result = _patched_tool_call(tool, inner, data="test")

        assert "error" in result.lower()

    def test_add_graph_data_with_graph_id(self, mock_zep_client: MagicMock) -> None:
        inner = _mock_zep_client()
        tool = create_add_graph_data_tool(mock_zep_client, graph_id="g1")
        result = _patched_tool_call(tool, inner, data="test data", data_type="json")

        inner.graph.add.assert_called_once()
        assert "g1" in result


class TestMemoryManagerEdgeCases:
    @pytest.mark.asyncio
    async def test_get_memory_context_with_messages(self, mock_zep_client: MagicMock) -> None:
        mock_msg = MagicMock()
        mock_msg.name = "Alice"
        mock_msg.role = "user"
        mock_msg.content = "Hello"
        mock_thread = MagicMock()
        mock_thread.messages = [mock_msg]
        mock_zep_client.thread.get.return_value = mock_thread

        mgr = ZepMemoryManager(mock_zep_client, user_id="u1", session_id="s1")
        context = await mgr.get_memory_context()

        assert "Alice user: Hello" in context

    @pytest.mark.asyncio
    async def test_get_memory_context_graph_with_nodes(self, mock_zep_client: MagicMock) -> None:
        mock_node = MagicMock()
        mock_node.name = "Alice"
        mock_node.summary = "A hiker"
        mock_zep_client.graph.search = AsyncMock(
            return_value=_make_mock_graph_results(nodes=[mock_node])
        )

        mgr = ZepMemoryManager(mock_zep_client, user_id="u1")
        context = await mgr.get_memory_context(query="Alice")

        assert "Alice: A hiker" in context

    @pytest.mark.asyncio
    async def test_get_memory_context_graph_error(self, mock_zep_client: MagicMock) -> None:
        mock_zep_client.graph.search = AsyncMock(side_effect=Exception("Graph error"))
        mgr = ZepMemoryManager(mock_zep_client, user_id="u1")
        context = await mgr.get_memory_context(query="test")

        assert context == ""

    @pytest.mark.asyncio
    async def test_get_memory_context_thread_error(self, mock_zep_client: MagicMock) -> None:
        mock_zep_client.thread.get_user_context = AsyncMock(side_effect=Exception("err"))
        mgr = ZepMemoryManager(mock_zep_client, user_id="u1", session_id="s1")
        context = await mgr.get_memory_context()

        assert context == ""

    @pytest.mark.asyncio
    async def test_add_messages_error(self, mock_zep_client: MagicMock) -> None:
        mock_zep_client.thread.add_messages = AsyncMock(side_effect=Exception("API err"))
        mgr = ZepMemoryManager(mock_zep_client, user_id="u1", session_id="s1")

        with pytest.raises(ZepAG2MemoryError, match="Failed to add messages"):
            await mgr.add_messages([{"content": "hi", "role": "user"}])

    @pytest.mark.asyncio
    async def test_get_session_facts_empty(self, mock_zep_client: MagicMock) -> None:
        mock_zep_client.thread.get_user_context = AsyncMock(return_value=MagicMock(context=None))
        mgr = ZepMemoryManager(mock_zep_client, user_id="u1", session_id="s1")
        facts = await mgr.get_session_facts()

        assert facts == []

    @pytest.mark.asyncio
    async def test_get_session_facts_error(self, mock_zep_client: MagicMock) -> None:
        mock_zep_client.thread.get_user_context = AsyncMock(side_effect=Exception("err"))
        mgr = ZepMemoryManager(mock_zep_client, user_id="u1", session_id="s1")
        facts = await mgr.get_session_facts()

        assert facts == []


class TestGraphMemoryManagerEdgeCases:
    @pytest.mark.asyncio
    async def test_search_with_nodes_and_episodes(self, mock_zep_client: MagicMock) -> None:
        mock_node = MagicMock()
        mock_node.name = "Python"
        mock_node.summary = "A language"
        mock_node.attributes = {"type": "language"}
        mock_node.created_at = "2024-01-01"
        mock_episode = MagicMock()
        mock_episode.content = "Discussed Python"
        mock_episode.source = "chat"
        mock_episode.role = "user"
        mock_episode.created_at = "2024-01-01"
        mock_zep_client.graph.search = AsyncMock(
            return_value=_make_mock_graph_results(nodes=[mock_node], episodes=[mock_episode])
        )

        mgr = ZepGraphMemoryManager(mock_zep_client, graph_id="g1")
        results = await mgr.search("Python")

        assert len(results) == 2
        assert results[0]["type"] == "node"
        assert results[1]["type"] == "episode"

    @pytest.mark.asyncio
    async def test_enrich_system_message_no_query_with_episodes(
        self, mock_zep_client: MagicMock, mock_ag2_agent: MagicMock
    ) -> None:
        mock_episode = MagicMock()
        mock_episode.content = "Recent chat about Python"
        mock_episodes = MagicMock()
        mock_episodes.episodes = [mock_episode]
        mock_zep_client.graph.episode.get_by_graph_id = AsyncMock(return_value=mock_episodes)

        mock_edge = MagicMock()
        mock_edge.fact = "Python is great"
        mock_edge.name = "fact"
        mock_edge.attributes = {}
        mock_edge.created_at = "2024-01-01"
        mock_zep_client.graph.search = AsyncMock(
            return_value=_make_mock_graph_results(edges=[mock_edge])
        )

        mgr = ZepGraphMemoryManager(mock_zep_client, graph_id="g1")
        await mgr.enrich_system_message(mock_ag2_agent)

        mock_ag2_agent.update_system_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_enrich_system_message_no_query_no_episodes(
        self, mock_zep_client: MagicMock, mock_ag2_agent: MagicMock
    ) -> None:
        mock_episodes = MagicMock()
        mock_episodes.episodes = []
        mock_zep_client.graph.episode.get_by_graph_id = AsyncMock(return_value=mock_episodes)

        mgr = ZepGraphMemoryManager(mock_zep_client, graph_id="g1")
        await mgr.enrich_system_message(mock_ag2_agent)

        mock_ag2_agent.update_system_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_enrich_system_message_no_query_error(
        self, mock_zep_client: MagicMock, mock_ag2_agent: MagicMock
    ) -> None:
        mock_zep_client.graph.episode.get_by_graph_id = AsyncMock(side_effect=Exception("err"))

        mgr = ZepGraphMemoryManager(mock_zep_client, graph_id="g1")
        await mgr.enrich_system_message(mock_ag2_agent)

        mock_ag2_agent.update_system_message.assert_not_called()


class TestExceptions:
    def test_zep_ag2_error(self) -> None:
        err = ZepAG2Error("test")
        assert str(err) == "test"

    def test_zep_dependency_error(self) -> None:
        err = ZepDependencyError(framework="AG2", install_command="pip install zep-ag2")
        assert "AG2" in str(err)
        assert err.framework == "AG2"
        assert err.install_command == "pip install zep-ag2"


class TestSyncWrappers:
    """Test synchronous wrapper methods on manager classes."""

    def test_memory_manager_get_context_sync(self) -> None:
        client = _mock_zep_client()
        mgr = ZepMemoryManager(client, user_id="u1", session_id="s1")
        result = mgr.get_memory_context_sync()

        assert isinstance(result, str)
        assert "hiking" in result

    def test_memory_manager_enrich_sync(self) -> None:
        client = _mock_zep_client()
        agent = MagicMock()
        agent.system_message = "You are helpful."
        agent.update_system_message = MagicMock()

        mgr = ZepMemoryManager(client, user_id="u1", session_id="s1")
        mgr.enrich_system_message_sync(agent)

        agent.update_system_message.assert_called_once()

    def test_graph_manager_search_sync(self) -> None:
        client = _mock_zep_client()
        mock_edge = MagicMock()
        mock_edge.fact = "test fact"
        mock_edge.name = "f"
        mock_edge.attributes = {}
        mock_edge.created_at = "2024-01-01"
        client.graph.search = AsyncMock(return_value=_make_mock_graph_results(edges=[mock_edge]))

        mgr = ZepGraphMemoryManager(client, graph_id="g1")
        results = mgr.search_sync("test")

        assert len(results) == 1

    def test_graph_manager_add_data_sync(self) -> None:
        client = _mock_zep_client()
        mgr = ZepGraphMemoryManager(client, graph_id="g1")
        result = mgr.add_data_sync("data")

        assert result is True
