"""
Tests for ``ContextInput`` / ``context_builder`` support on ``ZepUserStorage``.

CrewAI's storage adapters split persistence and retrieval into separate,
caller-driven calls (``save()`` / ``search()``) -- there is no gather or
concurrency here. When ``context_builder`` is set it entirely REPLACES the
default thread-context + edges composition in ``search()``: neither
``thread.get_user_context`` nor ``graph.search`` is called.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from zep_cloud.client import Zep

from zep_crewai import ZepUserStorage
from zep_crewai.user_storage import ContextInput


def _make_mock_client() -> MagicMock:
    client = MagicMock(spec=Zep)
    client.user = MagicMock()
    client.thread = MagicMock()
    client.graph = MagicMock()
    return client


class TestContextBuilder:
    def test_search_uses_context_builder(self) -> None:
        """When context_builder is set, get_user_context / graph.search are
        NOT called; the builder receives a ContextInput with the right
        fields; the result shape matches the existing search() contract."""
        client = _make_mock_client()
        received: list[ContextInput] = []

        def builder(ctx: ContextInput) -> str | None:
            received.append(ctx)
            return "Built context block"

        storage = ZepUserStorage(
            client=client, user_id="user-1", thread_id="thread-1", context_builder=builder
        )

        results = storage.search("What's up?", limit=5)

        client.thread.get_user_context.assert_not_called()
        client.graph.search.assert_not_called()

        assert len(received) == 1
        built = received[0]
        assert built.zep is client
        assert built.user_id == "user-1"
        assert built.thread_id == "thread-1"
        assert built.user_message == "What's up?"

        assert isinstance(results, list)
        assert len(results) == 1
        assert "Built context block" in results[0]["context"]
        assert results[0]["type"] == "user_graph_context"
        assert results[0]["source"] == "user_graph"
        assert results[0]["query"] == "What's up?"

    def test_search_builder_none_returns_empty(self) -> None:
        """A builder returning None means no context -> empty results."""
        client = _make_mock_client()

        def builder(ctx: ContextInput) -> str | None:
            return None

        storage = ZepUserStorage(
            client=client, user_id="user-1", thread_id="thread-1", context_builder=builder
        )

        results = storage.search("anything")

        assert results == []

    def test_search_builder_error_degrades(self) -> None:
        """A builder exception must be logged and degrade to empty results,
        never raise into search()."""
        client = _make_mock_client()

        def failing_builder(ctx: ContextInput) -> str | None:
            raise RuntimeError("builder boom")

        storage = ZepUserStorage(
            client=client,
            user_id="user-1",
            thread_id="thread-1",
            context_builder=failing_builder,
        )

        results = storage.search("hello")

        assert results == []
