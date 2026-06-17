"""
Tests for ZepStore -- the hybrid-delegate BaseStore (zep_langgraph.store).

These exercise the public BaseStore surface (get / put / search / delete /
list_namespaces and async mirrors), which the base class routes through the
``batch`` / ``abatch`` methods we implement. They verify:

* exact-key KV operations are served faithfully by the backing store,
* ``put`` also ingests the value into Zep,
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _async_client() -> MagicMock:
    client = MagicMock(spec=AsyncZep)
    client.graph = MagicMock()
    client.graph.add = AsyncMock()
    client.graph.search = AsyncMock()
    return client


def _sync_client() -> MagicMock:
    client = MagicMock(spec=Zep)
    client.graph = MagicMock()
    client.graph.add = MagicMock()
    client.graph.search = MagicMock()
    return client


def _edge(fact: str, score: float | None = 0.9) -> MagicMock:
    e = MagicMock()
    e.fact = fact
    e.score = score
    e.uuid_ = f"edge-{fact[:6]}"
    return e


def _search_result(edges=None, nodes=None, episodes=None, context=None) -> MagicMock:
    r = MagicMock()
    r.edges = edges
    r.nodes = nodes
    r.episodes = episodes
    r.context = context
    return r


# ---------------------------------------------------------------------------
# Contract: ZepStore IS a fully-concrete BaseStore
# ---------------------------------------------------------------------------
class TestBaseStoreContract:
    def test_isinstance_base_store(self) -> None:
        store = ZepStore(_async_client())
        assert isinstance(store, BaseStore)

    def test_abstractmethods_empty(self) -> None:
        assert ZepStore.__abstractmethods__ == frozenset()

    def test_default_backing_is_in_memory(self) -> None:
        store = ZepStore(_async_client())
        assert isinstance(store._backing, InMemoryStore)

    def test_accepts_custom_backing(self) -> None:
        backing = InMemoryStore()
        store = ZepStore(_async_client(), backing_store=backing)
        assert store._backing is backing


# ---------------------------------------------------------------------------
# Synchronous public API -> batch
# ---------------------------------------------------------------------------
class TestSyncKvDelegation:
    def test_put_then_get_roundtrip(self) -> None:
        store = ZepStore(_async_client(), sync_zep_client=_sync_client())
        store.put(("memories", "u1"), "k1", {"text": "hello"})
        item = store.get(("memories", "u1"), "k1")
        assert isinstance(item, Item)
        assert item.value == {"text": "hello"}

    def test_get_missing_returns_none(self) -> None:
        store = ZepStore(_async_client(), sync_zep_client=_sync_client())
        assert store.get(("memories", "u1"), "absent") is None

    def test_delete_removes_item(self) -> None:
        store = ZepStore(_async_client(), sync_zep_client=_sync_client())
        store.put(("ns",), "k", {"v": 1})
        store.delete(("ns",), "k")
        assert store.get(("ns",), "k") is None

    def test_list_namespaces_delegates(self) -> None:
        store = ZepStore(_async_client(), sync_zep_client=_sync_client())
        store.put(("docs", "a"), "k", {"v": 1})
        store.put(("docs", "b"), "k", {"v": 2})
        namespaces = store.list_namespaces()
        assert ("docs", "a") in namespaces
        assert ("docs", "b") in namespaces


class TestSyncIngestion:
    def test_put_ingests_into_zep(self) -> None:
        sync = _sync_client()
        store = ZepStore(_async_client(), sync_zep_client=sync)
        store.put(("memories", "u1"), "k1", {"text": "hello"})

        sync.graph.add.assert_called_once()
        kwargs = sync.graph.add.call_args.kwargs
        assert kwargs["type"] == "json"
        # default namespace resolver -> first element is graph_id
        assert kwargs["graph_id"] == "memories"
        payload = json.loads(kwargs["data"])
        assert payload["key"] == "k1"
        assert payload["value"] == {"text": "hello"}

    def test_delete_does_not_ingest(self) -> None:
        sync = _sync_client()
        store = ZepStore(_async_client(), sync_zep_client=sync)
        store.put(("ns",), "k", {"v": 1})
        sync.graph.add.reset_mock()
        store.delete(("ns",), "k")
        sync.graph.add.assert_not_called()

    def test_ingest_disabled(self) -> None:
        sync = _sync_client()
        store = ZepStore(_async_client(), sync_zep_client=sync, ingest_on_put=False)
        store.put(("ns",), "k", {"v": 1})
        sync.graph.add.assert_not_called()

    def test_missing_sync_client_skips_ingest_but_kv_works(self) -> None:
        store = ZepStore(_async_client())  # no sync_zep_client
        # KV still works (backing store), ingestion is skipped without crashing
        store.put(("ns",), "k", {"v": 1})
        assert store.get(("ns",), "k") is not None

    def test_zep_ingest_failure_does_not_crash(self) -> None:
        sync = _sync_client()
        sync.graph.add.side_effect = RuntimeError("ingest down")
        store = ZepStore(_async_client(), sync_zep_client=sync)
        # Must not raise; KV write still succeeds.
        store.put(("ns",), "k", {"v": 1})
        assert store.get(("ns",), "k") is not None

    def test_custom_namespace_target_user_id(self) -> None:
        sync = _sync_client()
        store = ZepStore(
            _async_client(),
            sync_zep_client=sync,
            namespace_target=lambda ns: {"user_id": ns[-1]},
        )
        store.put(("memories", "user-7"), "k", {"v": 1})
        kwargs = sync.graph.add.call_args.kwargs
        assert kwargs["user_id"] == "user-7"
        assert kwargs["graph_id"] is None


class TestSyncSearch:
    def test_search_routes_to_zep(self) -> None:
        sync = _sync_client()
        sync.graph.search.return_value = _search_result(edges=[_edge("Alice likes blue")])
        store = ZepStore(_async_client(), sync_zep_client=sync)

        results = store.search(("memories", "u1"), query="preferences")

        sync.graph.search.assert_called_once()
        kwargs = sync.graph.search.call_args.kwargs
        assert kwargs["query"] == "preferences"
        assert kwargs["graph_id"] == "memories"
        assert len(results) == 1
        assert isinstance(results[0], SearchItem)
        assert results[0].value["fact"] == "Alice likes blue"

    def test_search_without_query_skips_zep(self) -> None:
        sync = _sync_client()
        store = ZepStore(_async_client(), sync_zep_client=sync)
        store.put(("ns",), "k", {"v": 1})
        sync.graph.search.reset_mock()
        # No natural-language query -> nothing for the semantic graph to do.
        results = store.search(("ns",))
        sync.graph.search.assert_not_called()
        assert results == []

    def test_search_missing_sync_client_returns_empty(self) -> None:
        store = ZepStore(_async_client())  # no sync client
        results = store.search(("ns",), query="anything")
        assert results == []

    def test_search_zep_failure_returns_empty(self) -> None:
        sync = _sync_client()
        sync.graph.search.side_effect = RuntimeError("search down")
        store = ZepStore(_async_client(), sync_zep_client=sync)
        assert store.search(("ns",), query="x") == []

    def test_search_respects_limit(self) -> None:
        sync = _sync_client()
        sync.graph.search.return_value = _search_result(edges=[_edge("a"), _edge("b"), _edge("c")])
        store = ZepStore(_async_client(), sync_zep_client=sync)
        results = store.search(("ns",), query="q", limit=2)
        assert len(results) == 2

    def test_search_clamps_limit_to_zep_max(self) -> None:
        from zep_langgraph.store import MAX_SEARCH_LIMIT

        sync = _sync_client()
        sync.graph.search.return_value = _search_result(edges=[])
        store = ZepStore(_async_client(), sync_zep_client=sync)
        # BaseStore allows any limit; Zep rejects > 50, so we must clamp.
        store.search(("ns",), query="q", limit=500)
        sent = sync.graph.search.call_args.kwargs["limit"]
        assert sent <= MAX_SEARCH_LIMIT

    def test_search_honors_offset(self) -> None:
        sync = _sync_client()
        sync.graph.search.return_value = _search_result(
            edges=[_edge("a"), _edge("b"), _edge("c"), _edge("d")]
        )
        store = ZepStore(_async_client(), sync_zep_client=sync)
        # Skip the first two, take the next one.
        results = store.search(("ns",), query="q", limit=1, offset=2)
        assert len(results) == 1
        assert results[0].value["fact"] == "c"
        # Zep has no server-side offset, so we must fetch offset+limit rows.
        assert sync.graph.search.call_args.kwargs["limit"] >= 3

    def test_search_auto_scope_returns_context_item(self) -> None:
        sync = _sync_client()
        # ``auto`` scope returns a context string, not result lists.
        sync.graph.search.return_value = _search_result(context="Alice works at Acme.")
        store = ZepStore(_async_client(), sync_zep_client=sync, search_scope="auto")
        results = store.search(("ns",), query="where does Alice work?")
        assert len(results) == 1
        assert results[0].value["context"] == "Alice works at Acme."
        assert results[0].value["type"] == "context"

    def test_search_filter_warns_and_ignored(self, caplog) -> None:
        import logging

        sync = _sync_client()
        sync.graph.search.return_value = _search_result(edges=[_edge("a")])
        store = ZepStore(_async_client(), sync_zep_client=sync)
        with caplog.at_level(logging.WARNING, logger="zep_langgraph.store"):
            results = store.search(("ns",), query="q", filter={"type": "report"})
        # The op still runs; the filter is not forwarded to Zep.
        assert "search_filters" not in sync.graph.search.call_args.kwargs
        assert len(results) == 1
        assert any("filter" in rec.message.lower() for rec in caplog.records)


# ---------------------------------------------------------------------------
# Asynchronous public API -> abatch
# ---------------------------------------------------------------------------
class TestAsyncApi:
    @pytest.mark.asyncio
    async def test_aput_then_aget(self) -> None:
        store = ZepStore(_async_client())
        await store.aput(("memories", "u1"), "k1", {"text": "hi"})
        item = await store.aget(("memories", "u1"), "k1")
        assert isinstance(item, Item)
        assert item.value == {"text": "hi"}

    @pytest.mark.asyncio
    async def test_aput_ingests_into_zep(self) -> None:
        client = _async_client()
        store = ZepStore(client)
        await store.aput(("memories", "u1"), "k1", {"text": "hi"})
        client.graph.add.assert_awaited_once()
        kwargs = client.graph.add.call_args.kwargs
        assert kwargs["type"] == "json"
        assert kwargs["graph_id"] == "memories"

    @pytest.mark.asyncio
    async def test_asearch_routes_to_zep(self) -> None:
        client = _async_client()
        client.graph.search.return_value = _search_result(edges=[_edge("fact")])
        store = ZepStore(client)
        results = await store.asearch(("ns",), query="q")
        client.graph.search.assert_awaited_once()
        assert len(results) == 1
        assert results[0].value["fact"] == "fact"

    @pytest.mark.asyncio
    async def test_adelete_removes_and_does_not_ingest(self) -> None:
        client = _async_client()
        store = ZepStore(client)
        await store.aput(("ns",), "k", {"v": 1})
        client.graph.add.reset_mock()
        await store.adelete(("ns",), "k")
        assert await store.aget(("ns",), "k") is None
        client.graph.add.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_asearch_zep_failure_returns_empty(self) -> None:
        client = _async_client()
        client.graph.search.side_effect = RuntimeError("down")
        store = ZepStore(client)
        assert await store.asearch(("ns",), query="x") == []


# ---------------------------------------------------------------------------
# Batch ordering + payload-size guard
# ---------------------------------------------------------------------------
class TestBatchSemantics:
    def test_mixed_batch_preserves_order(self) -> None:
        from langgraph.store.base import GetOp, SearchOp

        sync = _sync_client()
        sync.graph.search.return_value = _search_result(edges=[_edge("found")])
        store = ZepStore(_async_client(), sync_zep_client=sync)
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
        store = ZepStore(_async_client(), sync_zep_client=sync)
        big = {"text": "x" * 20_000}
        store.put(("ns",), "k", big)
        # KV write still succeeds, but ingestion is skipped (payload too large).
        assert store.get(("ns",), "k") is not None
        sync.graph.add.assert_not_called()

    def test_unserialisable_payload_skips_ingest(self) -> None:
        sync = _sync_client()
        store = ZepStore(_async_client(), sync_zep_client=sync)
        # InMemoryStore stores the dict as-is; json.dumps fails on the set value.
        store.put(("ns",), "k", {"bad": {1, 2, 3}})
        sync.graph.add.assert_not_called()
