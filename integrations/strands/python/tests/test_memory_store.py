"""Tests for ``ZepMemoryStore`` search / add / add_messages / initialize."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from zep_strands import ZepMemoryStore
from zep_strands.memory_store import _extract_text, _results_to_entries, _role_to_zep


def _make_mock_client() -> MagicMock:
    client = MagicMock()
    client.user = MagicMock()
    client.user.add = AsyncMock()
    client.thread = MagicMock()
    client.thread.create = AsyncMock()
    client.thread.add_messages = AsyncMock(return_value=SimpleNamespace(message_uuids=["m1"]))
    client.graph = MagicMock()
    client.graph.search = AsyncMock()
    client.graph.add = AsyncMock(return_value=SimpleNamespace(uuid_="ep-1"))
    return client


def _make_store(**kwargs: object) -> ZepMemoryStore:
    defaults: dict[str, object] = {
        "zep_client": _make_mock_client(),
        "user_id": "user-1",
        "thread_id": "thread-1",
        "first_name": "Ada",
        "last_name": "Lovelace",
    }
    defaults.update(kwargs)
    return ZepMemoryStore(**defaults)  # type: ignore[arg-type]


class TestHelpers:
    def test_extract_text_joins_blocks(self) -> None:
        message = {
            "role": "user",
            "content": [{"text": "Hello"}, {"text": "world"}, {"toolUse": {}}],
        }
        assert _extract_text(message) == "Hello\nworld"  # type: ignore[arg-type]

    def test_role_to_zep_passthrough(self) -> None:
        assert _role_to_zep("user") == "user"
        assert _role_to_zep("Assistant") == "assistant"
        assert _role_to_zep("mystery") == "norole"

    def test_results_to_entries_auto_prefers_context(self) -> None:
        result = SimpleNamespace(
            context="assembled context",
            edges=[SimpleNamespace(fact="ignored", uuid_="e1")],
            nodes=None,
            episodes=None,
            observations=None,
            thread_summaries=None,
        )
        entries = _results_to_entries(result, "auto", limit=5)
        assert len(entries) == 1
        assert entries[0].content == "assembled context"

    def test_results_to_entries_edges(self) -> None:
        result = SimpleNamespace(
            context=None,
            edges=[
                SimpleNamespace(fact="Ada likes math", uuid_="e1"),
                SimpleNamespace(fact="Ada lives in London", uuid_="e2"),
            ],
            nodes=None,
            episodes=None,
            observations=None,
            thread_summaries=None,
        )
        entries = _results_to_entries(result, "edges", limit=1)
        assert len(entries) == 1
        assert entries[0].content == "Ada likes math"
        assert entries[0].metadata is not None
        assert entries[0].metadata["scope"] == "edges"


class TestInitialize:
    @pytest.mark.asyncio
    async def test_initialize_provisions_user_and_thread(self) -> None:
        store = _make_store()
        await store.initialize()
        store._zep.user.add.assert_awaited_once()
        store._zep.thread.create.assert_awaited_once()
        assert store._resources_ready is True

    @pytest.mark.asyncio
    async def test_initialize_graph_mode_is_noop(self) -> None:
        client = _make_mock_client()
        store = ZepMemoryStore(zep_client=client, graph_id="g1")
        await store.initialize()
        client.user.add.assert_not_awaited()
        client.thread.create.assert_not_awaited()


class TestSearch:
    @pytest.mark.asyncio
    async def test_search_user_graph(self) -> None:
        store = _make_store(search_scope="edges")
        store._zep.graph.search.return_value = SimpleNamespace(
            context=None,
            edges=[SimpleNamespace(fact="Prefers dark mode", uuid_="e1")],
            nodes=None,
            episodes=None,
            observations=None,
            thread_summaries=None,
        )
        entries = await store.search("preferences")
        assert len(entries) == 1
        assert "dark mode" in entries[0].content
        store._zep.graph.search.assert_awaited_once()
        kwargs = store._zep.graph.search.await_args.kwargs
        assert kwargs["user_id"] == "user-1"
        assert kwargs["scope"] == "edges"
        assert kwargs["query"] == "preferences"

    @pytest.mark.asyncio
    async def test_search_empty_query_returns_empty(self) -> None:
        store = _make_store()
        assert await store.search("   ") == []
        store._zep.graph.search.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_search_rejects_invalid_max(self) -> None:
        store = _make_store()
        with pytest.raises(ValueError, match="max_search_results"):
            await store.search("q", {"max_search_results": 0})


class TestAdd:
    @pytest.mark.asyncio
    async def test_add_text_to_user_graph(self) -> None:
        store = _make_store()
        await store.add("User prefers aisle seats", metadata={"source": "prefs"})
        store._zep.graph.add.assert_awaited_once()
        kwargs = store._zep.graph.add.await_args.kwargs
        assert kwargs["user_id"] == "user-1"
        assert kwargs["type"] == "text"
        assert kwargs["data"] == "User prefers aisle seats"
        assert kwargs["metadata"] == {"source": "prefs"}

    @pytest.mark.asyncio
    async def test_add_json_type(self) -> None:
        store = _make_store()
        await store.add('{"plan": "premium"}', metadata={"type": "json"})
        kwargs = store._zep.graph.add.await_args.kwargs
        assert kwargs["type"] == "json"
        assert "metadata" not in kwargs  # type key consumed

    @pytest.mark.asyncio
    async def test_add_rejects_when_not_writable(self) -> None:
        store = _make_store(writable=False, extraction=False)
        with pytest.raises(ValueError, match="not writable"):
            await store.add("x")

    @pytest.mark.asyncio
    async def test_add_to_standalone_graph(self) -> None:
        client = _make_mock_client()
        store = ZepMemoryStore(zep_client=client, graph_id="kb-1", writable=True)
        await store.add("Company holiday is July 4")
        kwargs = client.graph.add.await_args.kwargs
        assert kwargs["graph_id"] == "kb-1"
        assert "user_id" not in kwargs


class TestAddMessages:
    @pytest.mark.asyncio
    async def test_add_messages_converts_and_persists(self) -> None:
        store = _make_store()
        messages = [
            {"role": "user", "content": [{"text": "I love hiking"}]},
            {"role": "assistant", "content": [{"text": "Noted!"}]},
        ]
        await store.add_messages(messages)  # type: ignore[arg-type]
        store._zep.thread.add_messages.assert_awaited_once()
        kwargs = store._zep.thread.add_messages.await_args.kwargs
        assert kwargs["thread_id"] == "thread-1"
        assert len(kwargs["messages"]) == 2
        assert kwargs["messages"][0].role == "user"
        assert kwargs["messages"][0].content == "I love hiking"
        assert kwargs["messages"][0].name == "Ada Lovelace"
        assert kwargs["messages"][1].role == "assistant"
        assert kwargs["messages"][1].name == "Assistant"

    @pytest.mark.asyncio
    async def test_add_messages_skips_empty_and_tool_only(self) -> None:
        store = _make_store()
        messages = [
            {"role": "assistant", "content": [{"toolUse": {"name": "x"}}]},
            {"role": "user", "content": [{"text": "   "}]},
        ]
        result = await store.add_messages(messages)  # type: ignore[arg-type]
        assert result is None
        store._zep.thread.add_messages.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_add_messages_requires_thread(self) -> None:
        store = _make_store(thread_id=None, extraction=False)
        with pytest.raises(ValueError, match="thread_id"):
            await store.add_messages([{"role": "user", "content": [{"text": "hi"}]}])  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_add_messages_rejects_graph_mode(self) -> None:
        client = _make_mock_client()
        store = ZepMemoryStore(zep_client=client, graph_id="g1", writable=True)
        with pytest.raises(ValueError, match="user-graph"):
            await store.add_messages([{"role": "user", "content": [{"text": "hi"}]}])  # type: ignore[arg-type]


class TestGetTools:
    def test_get_tools_empty_by_default(self) -> None:
        store = _make_store()
        assert store.get_tools() == []

    def test_get_tools_when_exposed(self) -> None:
        store = _make_store(expose_search_tool=True)
        tools = store.get_tools()
        assert len(tools) == 1
