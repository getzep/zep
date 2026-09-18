"""
Tests for ZepStore -- the hybrid-delegate BaseStore (zep_langgraph.store).

These exercise the public BaseStore surface (get / put / search / delete /
list_namespaces and async mirrors), which the base class routes through the
``batch`` / ``abatch`` methods we implement. They verify:

* exact-key KV operations are served faithfully by the backing store,
* ``put`` also ingests the value into the Zep graph addressed by ``graph_uuid``,
* ``search`` is routed to Zep semantic search and converted to ``SearchItem``s,
* Zep failures never crash the store.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from langgraph.store.base import BaseStore, Item, SearchItem
from langgraph.store.memory import InMemoryStore
from zep_cloud.client import AsyncZep, Zep

from zep_langgraph.store import ZepStore

GRAPH_UUID = "22222222-2222-2222-2222-222222222222"
OTHER_GRAPH_UUID = "44444444-4444-4444-4444-444444444444"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _async_client() -> MagicMock:
    client = MagicMock(spec=AsyncZep)
    client.graph = MagicMock()
    client.graph.episode = MagicMock()
    client.graph.episode.add = AsyncMock()
    client.graph.search_edges = AsyncMock()
    client.graph.search_nodes = AsyncMock()
    client.graph.get_context = AsyncMock()
    return client


def _sync_client() -> MagicMock:
    client = MagicMock(spec=Zep)
    client.graph = MagicMock()
    client.graph.episode = MagicMock()
    client.graph.episode.add = MagicMock()
    client.graph.search_edges = MagicMock()
    client.graph.search_nodes = MagicMock()
    client.graph.get_context = MagicMock()
    return client


def _store(async_client: MagicMock, **kwargs) -> ZepStore:
    return ZepStore(async_client, graph_uuid=GRAPH_UUID, **kwargs)


def _edge(fact: str, score: float | None = 0.9) -> MagicMock:
    e = MagicMock()
    e.fact = fact
    e.score = score
    e.uuid_ = f"edge-{fact[:6]}"
    return e


def _page(items: list) -> MagicMock:
    """A v4 pager: the records of one page are in ``items``."""
    page = MagicMock()
    page.items = items
    return page


def _context(context: str | None) -> MagicMock:
    """A v4 ``GraphContextResponse``."""
    response = MagicMock()
    response.context = context
    return response


# ---------------------------------------------------------------------------
# Contract: ZepStore IS a fully-concrete BaseStore
# ---------------------------------------------------------------------------
class TestBaseStoreContract:
    def test_isinstance_base_store(self) -> None:
        assert isinstance(_store(_async_client()), BaseStore)

    def test_abstractmethods_empty(self) -> None:
        assert ZepStore.__abstractmethods__ == frozenset()

    def test_default_backing_is_in_memory(self) -> None:
        assert isinstance(_store(_async_client())._backing, InMemoryStore)

    def test_accepts_custom_backing(self) -> None:
        backing = InMemoryStore()
        store = _store(_async_client(), backing_store=backing)
        assert store._backing is backing

    def test_graph_uuid_is_required(self) -> None:
        with pytest.raises(TypeError):
            ZepStore(_async_client())  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Synchronous public API -> batch
# ---------------------------------------------------------------------------
class TestSyncKvDelegation:
    def test_put_then_get_roundtrip(self) -> None:
        store = _store(_async_client(), sync_zep_client=_sync_client())
        store.put(("memories", "u1"), "k1", {"text": "hello"})
        item = store.get(("memories", "u1"), "k1")
        assert isinstance(item, Item)
        assert item.value == {"text": "hello"}

    def test_get_missing_returns_none(self) -> None:
        store = _store(_async_client(), sync_zep_client=_sync_client())
        assert store.get(("memories", "u1"), "absent") is None

    def test_delete_removes_item(self) -> None:
        store = _store(_async_client(), sync_zep_client=_sync_client())
        store.put(("ns",), "k", {"v": 1})
        store.delete(("ns",), "k")
        assert store.get(("ns",), "k") is None

    def test_list_namespaces_delegates(self) -> None:
        store = _store(_async_client(), sync_zep_client=_sync_client())
        store.put(("docs", "a"), "k", {"v": 1})
        store.put(("docs", "b"), "k", {"v": 2})
        namespaces = store.list_namespaces()
        assert ("docs", "a") in namespaces
        assert ("docs", "b") in namespaces


class TestSyncIngestion:
    def test_put_ingests_into_zep(self) -> None:
        sync = _sync_client()
        store = _store(_async_client(), sync_zep_client=sync)
        store.put(("memories", "u1"), "k1", {"text": "hello"})

        sync.graph.episode.add.assert_called_once()
        call = sync.graph.episode.add.call_args
        # v4 addresses the graph by UUID, as the first positional argument.
        assert call.args == (GRAPH_UUID,)
        assert call.kwargs["type"] == "json"
        assert "graph_id" not in call.kwargs
        payload = json.loads(call.kwargs["data"])
        assert payload["key"] == "k1"
        assert payload["value"] == {"text": "hello"}

    def test_delete_does_not_ingest(self) -> None:
        sync = _sync_client()
        store = _store(_async_client(), sync_zep_client=sync)
        store.put(("ns",), "k", {"v": 1})
        sync.graph.episode.add.reset_mock()
        store.delete(("ns",), "k")
        sync.graph.episode.add.assert_not_called()

    def test_ingest_disabled(self) -> None:
        sync = _sync_client()
        store = _store(_async_client(), sync_zep_client=sync, ingest_on_put=False)
        store.put(("ns",), "k", {"v": 1})
        sync.graph.episode.add.assert_not_called()

    def test_missing_sync_client_skips_ingest_but_kv_works(self) -> None:
        store = _store(_async_client())  # no sync_zep_client
        # KV still works (backing store), ingestion is skipped without crashing
        store.put(("ns",), "k", {"v": 1})
        assert store.get(("ns",), "k") is not None

    def test_zep_ingest_failure_does_not_crash(self) -> None:
        sync = _sync_client()
        sync.graph.episode.add.side_effect = RuntimeError("ingest down")
        store = _store(_async_client(), sync_zep_client=sync)
        # Must not raise; KV write still succeeds.
        store.put(("ns",), "k", {"v": 1})
        assert store.get(("ns",), "k") is not None

    def test_custom_namespace_target_selects_graph(self) -> None:
        sync = _sync_client()
        store = _store(
            _async_client(),
            sync_zep_client=sync,
            namespace_target=lambda ns: OTHER_GRAPH_UUID if ns[0] == "other" else GRAPH_UUID,
        )
        store.put(("other", "u7"), "k", {"v": 1})
        assert sync.graph.episode.add.call_args.args == (OTHER_GRAPH_UUID,)


class TestSyncSearch:
    def test_search_routes_to_zep(self) -> None:
        sync = _sync_client()
        sync.graph.search_edges.return_value = _page([_edge("Alice likes blue")])
        store = _store(_async_client(), sync_zep_client=sync)

        results = store.search(("memories", "u1"), query="preferences")

        sync.graph.search_edges.assert_called_once()
        call = sync.graph.search_edges.call_args
        assert call.args == (GRAPH_UUID,)
        assert call.kwargs["query"] == "preferences"
        assert "graph_id" not in call.kwargs
        assert len(results) == 1
        assert isinstance(results[0], SearchItem)
        assert results[0].value["fact"] == "Alice likes blue"

    def test_search_scope_selects_method(self) -> None:
        sync = _sync_client()
        node = MagicMock()
        node.name = "Alice"
        node.summary = "An engineer"
        node.uuid_ = "node-1"
        node.score = 0.5
        sync.graph.search_nodes.return_value = _page([node])
        store = _store(_async_client(), sync_zep_client=sync, search_scope="nodes")

        results = store.search(("ns",), query="who")

        sync.graph.search_nodes.assert_called_once()
        sync.graph.search_edges.assert_not_called()
        assert results[0].value["name"] == "Alice"

    def test_search_without_query_skips_zep(self) -> None:
        sync = _sync_client()
        store = _store(_async_client(), sync_zep_client=sync)
        store.put(("ns",), "k", {"v": 1})
        # No natural-language query -> nothing for the semantic graph to do.
        results = store.search(("ns",))
        sync.graph.search_edges.assert_not_called()
        assert results == []

    def test_search_missing_sync_client_returns_empty(self) -> None:
        store = _store(_async_client())  # no sync client
        assert store.search(("ns",), query="anything") == []

    def test_search_zep_failure_returns_empty(self) -> None:
        sync = _sync_client()
        sync.graph.search_edges.side_effect = RuntimeError("search down")
        store = _store(_async_client(), sync_zep_client=sync)
        assert store.search(("ns",), query="x") == []

    def test_search_respects_limit(self) -> None:
        sync = _sync_client()
        sync.graph.search_edges.return_value = _page([_edge("a"), _edge("b"), _edge("c")])
        store = _store(_async_client(), sync_zep_client=sync)
        assert len(store.search(("ns",), query="q", limit=2)) == 2

    def test_search_clamps_limit_to_zep_max(self) -> None:
        from zep_langgraph.store import MAX_SEARCH_LIMIT

        sync = _sync_client()
        sync.graph.search_edges.return_value = _page([])
        store = _store(_async_client(), sync_zep_client=sync)
        # BaseStore allows any limit; Zep rejects > 50, so we must clamp.
        store.search(("ns",), query="q", limit=500)
        assert sync.graph.search_edges.call_args.kwargs["limit"] <= MAX_SEARCH_LIMIT

    def test_search_honors_offset(self) -> None:
        sync = _sync_client()
        sync.graph.search_edges.return_value = _page(
            [_edge("a"), _edge("b"), _edge("c"), _edge("d")]
        )
        store = _store(_async_client(), sync_zep_client=sync)
        # Skip the first two, take the next one.
        results = store.search(("ns",), query="q", limit=1, offset=2)
        assert len(results) == 1
        assert results[0].value["fact"] == "c"
        # Zep has no server-side offset, so we must fetch offset+limit rows.
        assert sync.graph.search_edges.call_args.kwargs["limit"] >= 3

    def test_search_auto_scope_returns_context_item(self) -> None:
        sync = _sync_client()
        # ``auto`` scope is served by graph.get_context, which returns one
        # assembled Context Block.
        sync.graph.get_context.return_value = _context("Alice works at Acme.")
        store = _store(_async_client(), sync_zep_client=sync, search_scope="auto")

        results = store.search(("ns",), query="where does Alice work?")

        sync.graph.get_context.assert_called_once()
        assert sync.graph.get_context.call_args.args == (GRAPH_UUID,)
        # graph.get_context takes no result limit.
        assert "limit" not in sync.graph.get_context.call_args.kwargs
        assert len(results) == 1
        assert results[0].value["context"] == "Alice works at Acme."
        assert results[0].value["type"] == "context"

    def test_search_filter_warns_and_ignored(self, caplog) -> None:
        import logging

        sync = _sync_client()
        sync.graph.search_edges.return_value = _page([_edge("a")])
        store = _store(_async_client(), sync_zep_client=sync)
        with caplog.at_level(logging.WARNING, logger="zep_langgraph.store"):
            results = store.search(("ns",), query="q", filter={"type": "report"})
        # The op still runs; the filter is not forwarded to Zep.
        assert "filters" not in sync.graph.search_edges.call_args.kwargs
        assert len(results) == 1
        assert any("filter" in rec.message.lower() for rec in caplog.records)


# ---------------------------------------------------------------------------
# Asynchronous public API -> abatch
# ---------------------------------------------------------------------------
class TestAsyncApi:
    @pytest.mark.asyncio
    async def test_aput_then_aget(self) -> None:
        store = _store(_async_client())
        await store.aput(("memories", "u1"), "k1", {"text": "hi"})
        item = await store.aget(("memories", "u1"), "k1")
        assert isinstance(item, Item)
        assert item.value == {"text": "hi"}

    @pytest.mark.asyncio
    async def test_aput_ingests_into_zep(self) -> None:
        client = _async_client()
        store = _store(client)
        await store.aput(("memories", "u1"), "k1", {"text": "hi"})
        client.graph.episode.add.assert_awaited_once()
        call = client.graph.episode.add.call_args
        assert call.args == (GRAPH_UUID,)
        assert call.kwargs["type"] == "json"

    @pytest.mark.asyncio
    async def test_asearch_routes_to_zep(self) -> None:
        client = _async_client()
        client.graph.search_edges.return_value = _page([_edge("fact")])
        store = _store(client)
        results = await store.asearch(("ns",), query="q")
        client.graph.search_edges.assert_awaited_once()
        assert client.graph.search_edges.call_args.args == (GRAPH_UUID,)
        assert len(results) == 1
        assert results[0].value["fact"] == "fact"

    @pytest.mark.asyncio
    async def test_adelete_removes_and_does_not_ingest(self) -> None:
        client = _async_client()
        store = _store(client)
        await store.aput(("ns",), "k", {"v": 1})
        client.graph.episode.add.reset_mock()
        await store.adelete(("ns",), "k")
        assert await store.aget(("ns",), "k") is None
        client.graph.episode.add.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_asearch_zep_failure_returns_empty(self) -> None:
        client = _async_client()
        client.graph.search_edges.side_effect = RuntimeError("down")
        store = _store(client)
        assert await store.asearch(("ns",), query="x") == []


# ---------------------------------------------------------------------------
# Batch ordering + payload-size guard
# ---------------------------------------------------------------------------
class TestBatchSemantics:
    def test_mixed_batch_preserves_order(self) -> None:
        from langgraph.store.base import GetOp, SearchOp

        sync = _sync_client()
        sync.graph.search_edges.return_value = _page([_edge("found")])
        store = _store(_async_client(), sync_zep_client=sync)
        store.put(("ns",), "k", {"v": 1})

        ops = [
            GetOp(namespace=("ns",), key="k"),
            SearchOp(namespace_prefix=("ns",), query="found"),
            GetOp(namespace=("ns",), key="missing"),
        ]
        results = store.batch(ops)
        assert isinstance(results[0], Item)  # get hit
        assert isinstance(results[1], list)  # search results
        assert results[2] is None  # get miss

    def test_oversized_payload_skips_ingest(self) -> None:
        sync = _sync_client()
        store = _store(_async_client(), sync_zep_client=sync)
        big = {"text": "x" * 20_000}
        store.put(("ns",), "k", big)
        # KV write still succeeds, but ingestion is skipped (payload too large).
        assert store.get(("ns",), "k") is not None
        sync.graph.episode.add.assert_not_called()

    def test_unserialisable_payload_skips_ingest(self) -> None:
        sync = _sync_client()
        store = _store(_async_client(), sync_zep_client=sync)
        # InMemoryStore stores the dict as-is; json.dumps fails on the set value.
        store.put(("ns",), "k", {"bad": {1, 2, 3}})
        sync.graph.episode.add.assert_not_called()
