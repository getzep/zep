"""Tests for ``ZepMemoryStore`` search / add / add_messages / initialize."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from zep_strands import ZepMemoryStore
from zep_strands._text import GRAPH_DATA_TRUNCATE_LIMIT
from zep_strands.memory_store import _extract_text, _results_to_entries, _role_to_zep
from zep_strands.search import SearchResults

USER_UUID = "11111111-1111-1111-1111-111111111111"
THREAD_UUID = "22222222-2222-2222-2222-222222222222"
USER_GRAPH_UUID = "33333333-3333-3333-3333-333333333333"
STANDALONE_GRAPH_UUID = "44444444-4444-4444-4444-444444444444"


def _pager(items: list[object]) -> SimpleNamespace:
    """A minimal stand-in for the v4 ``AsyncPager``."""
    return SimpleNamespace(items=items)


def _make_mock_client() -> MagicMock:
    client = MagicMock()
    client.user = MagicMock()
    client.user.get = AsyncMock(
        return_value=SimpleNamespace(uuid_=USER_UUID, graph_uuid=USER_GRAPH_UUID)
    )
    client.thread = MagicMock()
    client.thread.create = AsyncMock()
    client.thread.add_messages = AsyncMock(return_value=SimpleNamespace(message_uuids=["m1"]))
    client.graph = MagicMock()
    client.graph.get_context = AsyncMock(return_value=SimpleNamespace(context="assembled context"))
    client.graph.search_edges = AsyncMock(return_value=_pager([]))
    client.graph.search_nodes = AsyncMock(return_value=_pager([]))
    client.graph.episode = MagicMock()
    client.graph.episode.add = AsyncMock(return_value=SimpleNamespace(uuid_="ep-1"))
    return client


def _make_store(**kwargs: object) -> ZepMemoryStore:
    defaults: dict[str, object] = {
        "zep_client": _make_mock_client(),
        "user_uuid": USER_UUID,
        "thread_uuid": THREAD_UUID,
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

    def test_role_to_zep_maps_unknown_role_to_user(self) -> None:
        """Zep v4 has no ``norole``, so an unknown role becomes ``user``."""
        assert _role_to_zep("mystery") == "user"
        assert _role_to_zep("norole") == "user"

    def test_results_to_entries_auto_uses_context(self) -> None:
        results = SearchResults(scope="auto", context="assembled context")
        entries = _results_to_entries(results, limit=5)
        assert len(entries) == 1
        assert entries[0].content == "assembled context"

    def test_results_to_entries_edges(self) -> None:
        results = SearchResults(
            scope="edges",
            items=[
                SimpleNamespace(fact="Ada likes math", uuid_="e1"),
                SimpleNamespace(fact="Ada lives in London", uuid_="e2"),
            ],
        )
        entries = _results_to_entries(results, limit=1)
        assert len(entries) == 1
        assert entries[0].content == "Ada likes math"
        assert entries[0].metadata is not None
        assert entries[0].metadata["scope"] == "edges"


class TestInitialize:
    @pytest.mark.asyncio
    async def test_initialize_makes_no_zep_calls(self) -> None:
        """Strands runs ``initialize`` on a throwaway event loop from
        ``Agent.__init__``, so it must not touch the caller's client."""
        store = _make_store()
        await store.initialize()
        store._zep.user.get.assert_not_awaited()
        store._zep.graph.get_context.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_store_never_creates_resources(self) -> None:
        """The store takes UUIDs; the application creates the resources."""
        store = _make_store()
        await store.search("anything")
        await store.add("a fact")
        await store.add_messages([{"role": "user", "content": [{"text": "hi"}]}])  # type: ignore[arg-type]
        store._zep.thread.create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_user_graph_uuid_is_read_once(self) -> None:
        store = _make_store()
        await store.search("first")
        await store.search("second")
        await store.add("a fact")
        store._zep.user.get.assert_awaited_once_with(USER_UUID)

    @pytest.mark.asyncio
    async def test_standalone_graph_needs_no_user_read(self) -> None:
        client = _make_mock_client()
        store = ZepMemoryStore(zep_client=client, graph_uuid=STANDALONE_GRAPH_UUID)
        await store.initialize()
        await store.search("anything")
        client.user.get.assert_not_awaited()


class TestSearch:
    @pytest.mark.asyncio
    async def test_search_user_graph(self) -> None:
        store = _make_store(search_scope="edges")
        store._zep.graph.search_edges.return_value = _pager(
            [SimpleNamespace(fact="Prefers dark mode", uuid_="e1")]
        )
        entries = await store.search("preferences")
        assert len(entries) == 1
        assert "dark mode" in entries[0].content
        store._zep.graph.search_edges.assert_awaited_once()
        args, kwargs = store._zep.graph.search_edges.await_args
        assert args[0] == USER_GRAPH_UUID
        assert kwargs["query"] == "preferences"

    @pytest.mark.asyncio
    async def test_search_auto_scope_uses_get_context(self) -> None:
        store = _make_store()
        entries = await store.search("preferences")
        assert len(entries) == 1
        assert entries[0].content == "assembled context"
        args, kwargs = store._zep.graph.get_context.await_args
        assert args[0] == USER_GRAPH_UUID
        assert kwargs["query"] == "preferences"

    @pytest.mark.asyncio
    async def test_search_standalone_graph(self) -> None:
        client = _make_mock_client()
        store = ZepMemoryStore(
            zep_client=client,
            graph_uuid=STANDALONE_GRAPH_UUID,
            search_scope="edges",
        )
        await store.search("holidays")
        args, _ = client.graph.search_edges.await_args
        assert args[0] == STANDALONE_GRAPH_UUID

    @pytest.mark.asyncio
    async def test_search_empty_query_returns_empty(self) -> None:
        store = _make_store()
        assert await store.search("   ") == []
        store._zep.graph.get_context.assert_not_awaited()

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
        store._zep.graph.episode.add.assert_awaited_once()
        args, kwargs = store._zep.graph.episode.add.await_args
        assert args[0] == USER_GRAPH_UUID
        assert kwargs["type"] == "text"
        assert kwargs["data"] == "User prefers aisle seats"
        assert kwargs["metadata"] == {"source": "prefs"}

    @pytest.mark.asyncio
    async def test_add_json_type(self) -> None:
        store = _make_store()
        await store.add('{"plan": "premium"}', metadata={"type": "json"})
        kwargs = store._zep.graph.episode.add.await_args.kwargs
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
        store = ZepMemoryStore(zep_client=client, graph_uuid=STANDALONE_GRAPH_UUID, writable=True)
        await store.add("Company holiday is July 4")
        args, _ = client.graph.episode.add.await_args
        assert args[0] == STANDALONE_GRAPH_UUID

    @pytest.mark.asyncio
    async def test_add_truncates_oversize_text(self) -> None:
        store = _make_store()
        await store.add("z" * (GRAPH_DATA_TRUNCATE_LIMIT + 500))
        kwargs = store._zep.graph.episode.add.await_args.kwargs
        assert len(kwargs["data"]) == GRAPH_DATA_TRUNCATE_LIMIT

    @pytest.mark.asyncio
    async def test_add_rejects_oversize_json_instead_of_corrupting_it(self) -> None:
        """Valid-but-oversize JSON must raise, not be sliced into invalid JSON."""
        store = _make_store()
        payload = json.dumps({"notes": ["x" * 200 for _ in range(60)]})
        assert len(payload) > GRAPH_DATA_TRUNCATE_LIMIT
        json.loads(payload)  # the input itself is valid JSON

        with pytest.raises(ValueError, match="cannot be truncated safely"):
            await store.add(payload, metadata={"type": "json"})

        store._zep.graph.episode.add.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_add_allows_json_at_the_limit(self) -> None:
        store = _make_store()
        filler = "a" * (GRAPH_DATA_TRUNCATE_LIMIT - len(json.dumps({"k": ""})))
        payload = json.dumps({"k": filler})
        assert len(payload) == GRAPH_DATA_TRUNCATE_LIMIT

        await store.add(payload, metadata={"type": "json"})
        sent = store._zep.graph.episode.add.await_args.kwargs["data"]
        assert json.loads(sent) == {"k": filler}


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
        args, kwargs = store._zep.thread.add_messages.await_args
        assert args[0] == THREAD_UUID
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
        store = _make_store(thread_uuid=None, extraction=False)
        with pytest.raises(ValueError, match="thread_uuid"):
            await store.add_messages([{"role": "user", "content": [{"text": "hi"}]}])  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_add_messages_rejects_graph_mode(self) -> None:
        client = _make_mock_client()
        store = ZepMemoryStore(zep_client=client, graph_uuid=STANDALONE_GRAPH_UUID, writable=True)
        with pytest.raises(ValueError, match="user-graph"):
            await store.add_messages([{"role": "user", "content": [{"text": "hi"}]}])  # type: ignore[arg-type]


class TestZepFailuresPropagate:
    """Zep SDK errors must reach the framework, which owns failure isolation.

    ``MemoryManager.search`` skips failing stores, ``MemoryManager.add`` raises
    ``AggregateMemoryError``, and ``ExtractionCoordinator`` rolls its high-water
    mark back so a failed batch retries. Swallowing here would turn a retryable
    extraction failure into permanent, silent message loss.
    """

    @pytest.mark.asyncio
    async def test_search_propagates(self) -> None:
        store = _make_store()
        store._zep.graph.get_context.side_effect = RuntimeError("zep down")
        with pytest.raises(RuntimeError, match="zep down"):
            await store.search("anything")

    @pytest.mark.asyncio
    async def test_add_propagates(self) -> None:
        store = _make_store()
        store._zep.graph.episode.add.side_effect = RuntimeError("zep down")
        with pytest.raises(RuntimeError, match="zep down"):
            await store.add("a fact")

    @pytest.mark.asyncio
    async def test_add_messages_propagates_so_the_batch_retries(self) -> None:
        store = _make_store()
        store._zep.thread.add_messages.side_effect = RuntimeError("zep down")
        with pytest.raises(RuntimeError, match="zep down"):
            await store.add_messages([{"role": "user", "content": [{"text": "hi"}]}])  # type: ignore[arg-type]


class TestGetTools:
    def test_get_tools_empty_by_default(self) -> None:
        store = _make_store()
        assert store.get_tools() == []

    def test_get_tools_when_exposed(self) -> None:
        store = _make_store(expose_search_tool=True)
        tools = store.get_tools()
        assert len(tools) == 1
