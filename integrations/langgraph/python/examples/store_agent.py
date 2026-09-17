"""
Using ``ZepStore`` as a LangGraph ``BaseStore`` (secondary path).

``ZepStore`` is a hybrid-delegate :class:`~langgraph.store.base.BaseStore`: a
backing KV store (here the default ``InMemoryStore``) handles exact-key
``get`` / ``put`` / ``delete``, while ``search`` is routed to Zep's semantic
graph search and every ``put`` is also ingested into the Zep graph.

This makes ``ZepStore`` a drop-in store for ``create_react_agent(store=...)``
and for langmem's memory tools, which require a ``BaseStore``.

This example uses the store directly (no LLM needed) to show the round-trip and
the semantic-search routing. Note that Zep ingestion is **asynchronous**: the
facts written here are immediately available for exact-key ``get`` but their
extracted facts are not instantly returned by ``search`` -- allow time for the
graph to build.

Zep v4 addresses a graph by its server-generated UUID. The example creates a
standalone graph, reads ``graph.uuid_`` from the response, and gives that UUID
to ``ZepStore``.

Prerequisites::

    pip install zep-langgraph
    export ZEP_API_KEY="your-zep-api-key"

Run::

    python examples/store_agent.py
"""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

from zep_cloud.client import AsyncZep

from zep_langgraph import ZepStore

ZEP_API_KEY = os.environ.get("ZEP_API_KEY", "")
if not ZEP_API_KEY:
    raise OSError("ZEP_API_KEY is not set.")

GRAPH_NAME = f"langgraph-store-example-{uuid4().hex[:8]}"
NAMESPACE = ("memories", "facts")


async def main() -> None:
    zep = AsyncZep(api_key=ZEP_API_KEY)

    # Create the standalone graph that holds the store data. Zep assigns the
    # graph UUID; store it in your own database.
    graph = await zep.graph.create(name=GRAPH_NAME)

    # Default backing store is InMemoryStore; pass your own (e.g. PostgresStore)
    # for durable exact-key storage in production.
    store = ZepStore(zep, graph_uuid=graph.uuid_)

    print("=" * 60)
    print("LangGraph ZepStore example (hybrid-delegate BaseStore)")
    print(f"  graph_uuid={graph.uuid_}")
    print("=" * 60)

    # --- put(): writes to the backing KV store AND ingests into Zep ---
    print("\n--- put() three memories ---")
    await store.aput(NAMESPACE, "m1", {"text": "Alice is a software engineer at Acme Corp."})
    await store.aput(NAMESPACE, "m2", {"text": "Alice lives in Portland."})
    await store.aput(NAMESPACE, "m3", {"text": "Alice enjoys hiking on weekends."})
    print("  wrote m1, m2, m3")

    # --- get(): exact-key retrieval served synchronously by the backing store ---
    print("\n--- get() by exact key (served by backing store) ---")
    item = await store.aget(NAMESPACE, "m1")
    print(f"  m1 -> {item.value if item else None}")

    # --- wait for Zep to build the graph (asynchronous ingestion) ---
    print("\n--- Waiting 20s for Zep to ingest into the graph ---")
    await asyncio.sleep(20)

    # --- search(): routed to Zep semantic graph search ---
    print("\n--- search() routed to Zep semantic search ---")
    results = await store.asearch(NAMESPACE, query="What does Alice do for work?")
    if results:
        for r in results:
            print(f"  - {r.value}")
    else:
        print("  (no results yet -- ingestion may still be in progress)")

    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
