"""
Tests for zep_adk.memory_service.ZepMemoryService.

Uses mocked AsyncZep clients to validate the ADK-native memory service
without requiring a live Zep instance.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_zep_client() -> MagicMock:
    client = MagicMock()
    client.graph = MagicMock()
    client.graph.search = AsyncMock()
    return client


def _make_search_result(**kwargs: object) -> MagicMock:
    result = MagicMock()
    result.edges = kwargs.get("edges")
    result.nodes = kwargs.get("nodes")
    result.episodes = kwargs.get("episodes")
    result.observations = kwargs.get("observations")
    result.thread_summaries = kwargs.get("thread_summaries")
    result.context = kwargs.get("context")
    return result


def _make_edge(fact: str) -> MagicMock:
    edge = MagicMock()
    edge.fact = fact
    return edge


def _make_node(name: str, summary: str | None) -> MagicMock:
    node = MagicMock()
    node.name = name
    node.summary = summary
    return node


def _make_episode(content: str) -> MagicMock:
    episode = MagicMock()
    episode.content = content
    return episode


def _make_observation(name: str | None, summary: str | None) -> MagicMock:
    observation = MagicMock()
    observation.name = name
    observation.summary = summary
    return observation


def _make_thread_summary(name: str | None, summary: str | None) -> MagicMock:
    thread_summary = MagicMock()
    thread_summary.name = name
    thread_summary.summary = summary
    return thread_summary


class TestZepMemoryServiceIsABaseMemoryService:
    """Constructing the service proves the ABC's abstract methods are implemented."""

    def test_instantiation_satisfies_abstract_base(self) -> None:
        from google.adk.memory.base_memory_service import BaseMemoryService

        from zep_adk.memory_service import ZepMemoryService

        service = ZepMemoryService(zep=_make_zep_client())
        assert isinstance(service, BaseMemoryService)

    def test_default_scope_is_edges(self) -> None:
        from zep_adk.memory_service import ZepMemoryService

        service = ZepMemoryService(zep=_make_zep_client())
        assert service._scope == "edges"

    def test_default_limit_is_none(self) -> None:
        from zep_adk.memory_service import ZepMemoryService

        service = ZepMemoryService(zep=_make_zep_client())
        assert service._limit is None

    def test_custom_scope_and_limit_stored(self) -> None:
        from zep_adk.memory_service import ZepMemoryService

        service = ZepMemoryService(zep=_make_zep_client(), scope="nodes", limit=5)
        assert service._scope == "nodes"
        assert service._limit == 5


class TestAddSessionToMemoryIsNoOp:
    """add_session_to_memory must never call Zep -- persistence happens live
    via the memory loop (ZepContextTool + after-model callback)."""

    @pytest.mark.asyncio
    async def test_no_zep_calls_made(self) -> None:
        from zep_adk.memory_service import ZepMemoryService

        client = _make_zep_client()
        service = ZepMemoryService(zep=client)

        session = MagicMock()
        await service.add_session_to_memory(session)

        client.graph.search.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_none(self) -> None:
        from zep_adk.memory_service import ZepMemoryService

        service = ZepMemoryService(zep=_make_zep_client())
        result = await service.add_session_to_memory(MagicMock())
        assert result is None

    @pytest.mark.asyncio
    async def test_logs_at_debug_level(self, caplog: pytest.LogCaptureFixture) -> None:
        from zep_adk.memory_service import ZepMemoryService

        service = ZepMemoryService(zep=_make_zep_client())

        with caplog.at_level("DEBUG"):
            await service.add_session_to_memory(MagicMock())

        assert any(
            "no-op" in record.message.lower() or "noop" in record.message.lower()
            for record in caplog.records
        )


class TestSearchMemoryMapsResults:
    """search_memory maps graph.search results to MemoryEntry objects."""

    @pytest.mark.asyncio
    async def test_maps_edges_to_memory_entries(self) -> None:
        from zep_adk.memory_service import ZepMemoryService

        client = _make_zep_client()
        client.graph.search.return_value = _make_search_result(
            edges=[_make_edge("Alice works at Acme"), _make_edge("Bob likes hiking")]
        )
        service = ZepMemoryService(zep=client)

        response = await service.search_memory(app_name="my_app", user_id="user-1", query="Alice")

        assert len(response.memories) == 2
        texts = [entry.content.parts[0].text for entry in response.memories]
        assert "Alice works at Acme" in texts
        assert "Bob likes hiking" in texts

    @pytest.mark.asyncio
    async def test_memory_entry_author_populated(self) -> None:
        from zep_adk.memory_service import ZepMemoryService

        client = _make_zep_client()
        client.graph.search.return_value = _make_search_result(
            edges=[_make_edge("Alice works at Acme")]
        )
        service = ZepMemoryService(zep=client)

        response = await service.search_memory(app_name="my_app", user_id="user-1", query="Alice")

        assert response.memories[0].author is not None

    @pytest.mark.asyncio
    async def test_maps_nodes_scope(self) -> None:
        from zep_adk.memory_service import ZepMemoryService

        client = _make_zep_client()
        client.graph.search.return_value = _make_search_result(
            nodes=[_make_node("Alice", "A software engineer at Acme")]
        )
        service = ZepMemoryService(zep=client, scope="nodes")

        response = await service.search_memory(app_name="my_app", user_id="user-1", query="Alice")

        assert len(response.memories) == 1
        text = response.memories[0].content.parts[0].text
        assert "Alice" in text
        assert "software engineer" in text

    @pytest.mark.asyncio
    async def test_node_with_summary_only_renders_summary_without_label(self) -> None:
        """A node with a summary but no name maps to a memory entry that is
        just the summary text -- no generic 'Entity'-style label prefix,
        matching Go/TypeScript."""
        from zep_adk.memory_service import ZepMemoryService

        client = _make_zep_client()
        client.graph.search.return_value = _make_search_result(
            nodes=[_make_node(None, "A software engineer at Acme")]
        )
        service = ZepMemoryService(zep=client, scope="nodes")

        response = await service.search_memory(app_name="my_app", user_id="user-1", query="Alice")

        assert len(response.memories) == 1
        assert response.memories[0].content.parts[0].text == "A software engineer at Acme"

    @pytest.mark.asyncio
    async def test_maps_episodes_scope(self) -> None:
        from zep_adk.memory_service import ZepMemoryService

        client = _make_zep_client()
        client.graph.search.return_value = _make_search_result(
            episodes=[_make_episode("I work at Acme Corp")]
        )
        service = ZepMemoryService(zep=client, scope="episodes")

        response = await service.search_memory(app_name="my_app", user_id="user-1", query="Acme")

        assert len(response.memories) == 1
        assert "Acme Corp" in response.memories[0].content.parts[0].text

    @pytest.mark.asyncio
    async def test_maps_observations_scope(self) -> None:
        from zep_adk.memory_service import ZepMemoryService

        client = _make_zep_client()
        client.graph.search.return_value = _make_search_result(
            observations=[_make_observation("Alice", "Prefers async communication")]
        )
        service = ZepMemoryService(zep=client, scope="observations")

        response = await service.search_memory(app_name="my_app", user_id="user-1", query="Alice")

        assert len(response.memories) == 1
        text = response.memories[0].content.parts[0].text
        assert "Alice" in text
        assert "Prefers async communication" in text

    @pytest.mark.asyncio
    async def test_observation_name_only_still_included(self) -> None:
        """A name-only observation (no summary) must still produce a memory
        entry -- the same name/summary fallback the graph-search tool's
        _format_results applies via the shared scope_results_to_texts."""
        from zep_adk.memory_service import ZepMemoryService

        client = _make_zep_client()
        client.graph.search.return_value = _make_search_result(
            observations=[_make_observation("Alice", None)]
        )
        service = ZepMemoryService(zep=client, scope="observations")

        response = await service.search_memory(app_name="my_app", user_id="user-1", query="Alice")

        assert len(response.memories) == 1
        assert response.memories[0].content.parts[0].text == "Alice"

    @pytest.mark.asyncio
    async def test_observation_with_summary_only_renders_summary_without_label(self) -> None:
        """An observation with a summary but no name maps to a memory entry
        that is just the summary text -- no generic label prefix, matching
        the nodes/thread_summaries symmetry and the Go/TypeScript behavior."""
        from zep_adk.memory_service import ZepMemoryService

        client = _make_zep_client()
        client.graph.search.return_value = _make_search_result(
            observations=[_make_observation(None, "Prefers async communication")]
        )
        service = ZepMemoryService(zep=client, scope="observations")

        response = await service.search_memory(app_name="my_app", user_id="user-1", query="Alice")

        assert len(response.memories) == 1
        assert response.memories[0].content.parts[0].text == "Prefers async communication"

    @pytest.mark.asyncio
    async def test_maps_thread_summaries_scope(self) -> None:
        from zep_adk.memory_service import ZepMemoryService

        client = _make_zep_client()
        client.graph.search.return_value = _make_search_result(
            thread_summaries=[_make_thread_summary("thread-1", "Discussed billing issue")]
        )
        service = ZepMemoryService(zep=client, scope="thread_summaries")

        response = await service.search_memory(app_name="my_app", user_id="user-1", query="billing")

        assert len(response.memories) == 1
        text = response.memories[0].content.parts[0].text
        assert "thread-1" in text
        assert "Discussed billing issue" in text

    @pytest.mark.asyncio
    async def test_thread_summary_name_only_still_included(self) -> None:
        from zep_adk.memory_service import ZepMemoryService

        client = _make_zep_client()
        client.graph.search.return_value = _make_search_result(
            thread_summaries=[_make_thread_summary("thread-1", None)]
        )
        service = ZepMemoryService(zep=client, scope="thread_summaries")

        response = await service.search_memory(app_name="my_app", user_id="user-1", query="billing")

        assert len(response.memories) == 1
        assert response.memories[0].content.parts[0].text == "thread-1"

    @pytest.mark.asyncio
    async def test_thread_summary_with_summary_only_renders_summary_without_label(self) -> None:
        """A thread summary with a summary but no name maps to a memory
        entry that is just the summary text -- no generic 'Thread'-style
        label prefix, matching Go/TypeScript."""
        from zep_adk.memory_service import ZepMemoryService

        client = _make_zep_client()
        client.graph.search.return_value = _make_search_result(
            thread_summaries=[_make_thread_summary(None, "Discussed billing issue")]
        )
        service = ZepMemoryService(zep=client, scope="thread_summaries")

        response = await service.search_memory(app_name="my_app", user_id="user-1", query="billing")

        assert len(response.memories) == 1
        assert response.memories[0].content.parts[0].text == "Discussed billing issue"

    @pytest.mark.asyncio
    async def test_maps_auto_scope_to_single_entry(self) -> None:
        from zep_adk.memory_service import ZepMemoryService

        client = _make_zep_client()
        client.graph.search.return_value = _make_search_result(
            context="Pre-materialized context block."
        )
        service = ZepMemoryService(zep=client, scope="auto")

        response = await service.search_memory(
            app_name="my_app", user_id="user-1", query="anything"
        )

        assert len(response.memories) == 1
        assert response.memories[0].content.parts[0].text == "Pre-materialized context block."

    @pytest.mark.asyncio
    async def test_empty_results_yield_empty_response(self) -> None:
        from zep_adk.memory_service import ZepMemoryService

        client = _make_zep_client()
        client.graph.search.return_value = _make_search_result(edges=[])
        service = ZepMemoryService(zep=client)

        response = await service.search_memory(app_name="my_app", user_id="user-1", query="nothing")

        assert response.memories == []


class TestSearchMemoryUnsupportedScope:
    """An unsupported scope must be rejected before any network call is made,
    matching Go's searchScopeSupported fail-fast check in memory.go."""

    @pytest.mark.asyncio
    async def test_does_not_call_graph_search(self) -> None:
        from zep_adk.memory_service import ZepMemoryService

        client = _make_zep_client()
        service = ZepMemoryService(zep=client, scope="not-a-real-scope")

        await service.search_memory(app_name="my_app", user_id="user-1", query="hi")

        client.graph.search.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_empty_response(self) -> None:
        from zep_adk.memory_service import ZepMemoryService

        client = _make_zep_client()
        service = ZepMemoryService(zep=client, scope="not-a-real-scope")

        response = await service.search_memory(app_name="my_app", user_id="user-1", query="hi")

        assert response.memories == []

    @pytest.mark.asyncio
    async def test_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        from zep_adk.memory_service import ZepMemoryService

        client = _make_zep_client()
        service = ZepMemoryService(zep=client, scope="not-a-real-scope")

        with caplog.at_level("WARNING"):
            await service.search_memory(app_name="my_app", user_id="user-1", query="hi")

        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1
        assert "not-a-real-scope" in warnings[0].message

    @pytest.mark.asyncio
    async def test_does_not_raise(self) -> None:
        from zep_adk.memory_service import ZepMemoryService

        client = _make_zep_client()
        service = ZepMemoryService(zep=client, scope="not-a-real-scope")

        # Should not raise.
        await service.search_memory(app_name="my_app", user_id="user-1", query="hi")


class TestSearchMemoryParameterPassthrough:
    """scope/limit from the constructor and user_id/query from the call are
    passed through to graph.search."""

    @pytest.mark.asyncio
    async def test_user_id_from_call_used(self) -> None:
        from zep_adk.memory_service import ZepMemoryService

        client = _make_zep_client()
        client.graph.search.return_value = _make_search_result(edges=[])
        service = ZepMemoryService(zep=client)

        await service.search_memory(app_name="my_app", user_id="user-42", query="hi")

        call_kwargs = client.graph.search.call_args[1]
        assert call_kwargs["user_id"] == "user-42"

    @pytest.mark.asyncio
    async def test_query_from_call_used(self) -> None:
        from zep_adk.memory_service import ZepMemoryService

        client = _make_zep_client()
        client.graph.search.return_value = _make_search_result(edges=[])
        service = ZepMemoryService(zep=client)

        await service.search_memory(app_name="my_app", user_id="user-1", query="find this")

        call_kwargs = client.graph.search.call_args[1]
        assert call_kwargs["query"] == "find this"

    @pytest.mark.asyncio
    async def test_scope_from_constructor_used(self) -> None:
        from zep_adk.memory_service import ZepMemoryService

        client = _make_zep_client()
        client.graph.search.return_value = _make_search_result(nodes=[])
        service = ZepMemoryService(zep=client, scope="nodes")

        await service.search_memory(app_name="my_app", user_id="user-1", query="hi")

        call_kwargs = client.graph.search.call_args[1]
        assert call_kwargs["scope"] == "nodes"

    @pytest.mark.asyncio
    async def test_limit_from_constructor_used(self) -> None:
        from zep_adk.memory_service import ZepMemoryService

        client = _make_zep_client()
        client.graph.search.return_value = _make_search_result(edges=[])
        service = ZepMemoryService(zep=client, limit=7)

        await service.search_memory(app_name="my_app", user_id="user-1", query="hi")

        call_kwargs = client.graph.search.call_args[1]
        assert call_kwargs["limit"] == 7

    @pytest.mark.asyncio
    async def test_no_limit_omits_limit_kwarg(self) -> None:
        """limit=None (the SDK default) should not be forced into the call
        -- let the Zep SDK apply its own default."""
        from zep_adk.memory_service import ZepMemoryService

        client = _make_zep_client()
        client.graph.search.return_value = _make_search_result(edges=[])
        service = ZepMemoryService(zep=client)

        await service.search_memory(app_name="my_app", user_id="user-1", query="hi")

        call_kwargs = client.graph.search.call_args[1]
        assert "limit" not in call_kwargs or call_kwargs["limit"] is None

    @pytest.mark.asyncio
    async def test_app_name_not_forwarded_to_zep(self) -> None:
        """app_name has no Zep equivalent (Zep scopes by user graph, not app) --
        it must not leak into the graph.search call."""
        from zep_adk.memory_service import ZepMemoryService

        client = _make_zep_client()
        client.graph.search.return_value = _make_search_result(edges=[])
        service = ZepMemoryService(zep=client)

        await service.search_memory(app_name="my_app", user_id="user-1", query="hi")

        call_kwargs = client.graph.search.call_args[1]
        assert "app_name" not in call_kwargs


class TestSearchMemoryErrorHandling:
    """A Zep failure must never propagate into the agent."""

    @pytest.mark.asyncio
    async def test_zep_error_returns_empty_response(self) -> None:
        from zep_adk.memory_service import ZepMemoryService

        client = _make_zep_client()
        client.graph.search.side_effect = RuntimeError("Zep is down")
        service = ZepMemoryService(zep=client)

        response = await service.search_memory(app_name="my_app", user_id="user-1", query="hi")

        assert response.memories == []

    @pytest.mark.asyncio
    async def test_zep_error_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        from zep_adk.memory_service import ZepMemoryService

        client = _make_zep_client()
        client.graph.search.side_effect = RuntimeError("Zep is down")
        service = ZepMemoryService(zep=client)

        with caplog.at_level("WARNING"):
            await service.search_memory(app_name="my_app", user_id="user-1", query="hi")

        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1

    @pytest.mark.asyncio
    async def test_zep_error_warning_omits_query_content(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The warning should log lengths/counts, not raw query text."""
        from zep_adk.memory_service import ZepMemoryService

        client = _make_zep_client()
        client.graph.search.side_effect = RuntimeError("Zep is down")
        service = ZepMemoryService(zep=client)

        secret_query = "a very specific secret user query"
        with caplog.at_level("WARNING"):
            await service.search_memory(app_name="my_app", user_id="user-1", query=secret_query)

        assert not any(secret_query in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_does_not_raise(self) -> None:
        from zep_adk.memory_service import ZepMemoryService

        client = _make_zep_client()
        client.graph.search.side_effect = ValueError("boom")
        service = ZepMemoryService(zep=client)

        # Should not raise.
        await service.search_memory(app_name="my_app", user_id="user-1", query="hi")
