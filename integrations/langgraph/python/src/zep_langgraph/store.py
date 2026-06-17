"""
``ZepStore`` -- a hybrid-delegate :class:`~langgraph.store.base.BaseStore` backed by Zep.

``BaseStore`` is LangGraph's cross-thread long-term-memory interface. langmem's
``create_manage_memory_tool`` / ``create_search_memory_tool`` and
``create_react_agent(store=...)`` all expect a ``BaseStore``. Zep, however, is a
temporal knowledge graph -- not a key-value store -- so a *pure* Zep store cannot
faithfully honour exact-key ``get`` / ``update`` / hard ``delete`` /
read-after-write.

``ZepStore`` resolves this with a **hybrid-delegate** design:

* It wraps a backing KV ``BaseStore`` (default
  :class:`~langgraph.store.memory.InMemoryStore`) which handles exact-key
  ``get`` / ``put`` / ``delete`` / ``list_namespaces`` faithfully and
  synchronously.
* On every ``put`` it **also** ingests the stored value into Zep via
  ``graph.add(type="json")``, so the data becomes part of the temporal graph.
* It routes ``search`` to Zep's semantic ``graph.search`` (the differentiator),
  optionally merged with the backing store's own search.

Only the two abstract methods -- :meth:`batch` and :meth:`abatch` -- are
implemented. Every public ``get`` / ``put`` / ``search`` / ``delete`` /
``list_namespaces`` (and their async mirrors) is inherited from ``BaseStore``
and delegates to these by constructing ``Op`` objects.

.. important::
   Zep ingestion is **asynchronous**. A value written with ``put`` is available
   immediately for exact-key ``get`` (served by the backing store) but its
   extracted facts are **not** instantly returned by Zep ``search`` -- there is
   no read-after-write of graph facts within a turn. ``ZepStore`` is the
   long-term-memory layer, not the checkpointer, so graph execution, threads,
   and short-term state are unaffected.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

from langgraph.store.base import (
    BaseStore,
    Item,
    Op,
    PutOp,
    Result,
    SearchItem,
    SearchOp,
)
from langgraph.store.memory import InMemoryStore
from zep_cloud.client import AsyncZep, Zep

logger = logging.getLogger(__name__)

#: Maximum characters Zep's ``graph.add`` accepts in a single call.
MAX_GRAPH_ADD_CHARS = 10_000

#: A resolver mapping a store namespace to a Zep search/ingest target.
#: It receives the namespace tuple and returns either ``{"user_id": ...}`` or
#: ``{"graph_id": ...}``.
NamespaceTargetResolver = Callable[[tuple[str, ...]], dict[str, str]]


def _default_namespace_target(namespace: tuple[str, ...]) -> dict[str, str]:
    """Default resolver: treat the first namespace element as a Zep ``graph_id``.

    For example, namespace ``("memories", "user-123")`` maps to
    ``{"graph_id": "memories"}``. Override this with a resolver that maps a
    namespace identifying an end user to ``{"user_id": ...}`` to use a personal
    user graph (and unlock ``thread.get_user_context``) instead.
    """
    graph_id = namespace[0] if namespace else "default"
    return {"graph_id": graph_id}


class ZepStore(BaseStore):
    """A ``BaseStore`` that delegates KV operations and routes search to Zep.

    Args:
        zep_client: An initialised :class:`~zep_cloud.client.AsyncZep` client,
            used by :meth:`abatch` for ingestion and search.
        sync_zep_client: An optional synchronous :class:`~zep_cloud.client.Zep`
            client, used by :meth:`batch`. When omitted, synchronous ingestion
            and synchronous Zep search are skipped (the backing store still
            serves synchronous KV operations); a warning is logged.
        backing_store: The KV ``BaseStore`` that handles exact-key
            ``get`` / ``put`` / ``delete`` / ``list_namespaces``. Defaults to a
            fresh :class:`~langgraph.store.memory.InMemoryStore`.
        namespace_target: A callable mapping a namespace tuple to a Zep target
            (``{"user_id": ...}`` or ``{"graph_id": ...}``). Defaults to
            :func:`_default_namespace_target`.
        search_scope: Zep ``graph.search`` scope used for ``search`` operations.
            Defaults to ``"edges"`` (facts).
        ingest_on_put: When ``True`` (default), every ``put`` also ingests the
            value into Zep. Set ``False`` to use Zep only for search.
        merge_backing_search: When ``True``, ``search`` results combine Zep
            results with the backing store's own search results. Defaults to
            ``False`` (Zep results only).
    """

    # ``BaseStore`` declares ``__slots__ = ("__weakref__",)``; declare our own
    # so instances can hold state without a ``__dict__``.
    __slots__ = (
        "_zep",
        "_sync_zep",
        "_backing",
        "_namespace_target",
        "_search_scope",
        "_ingest_on_put",
        "_merge_backing_search",
    )

    def __init__(
        self,
        zep_client: AsyncZep,
        *,
        sync_zep_client: Zep | None = None,
        backing_store: BaseStore | None = None,
        namespace_target: NamespaceTargetResolver = _default_namespace_target,
        search_scope: str = "edges",
        ingest_on_put: bool = True,
        merge_backing_search: bool = False,
    ) -> None:
        self._zep: AsyncZep = zep_client
        self._sync_zep: Zep | None = sync_zep_client
        self._backing: BaseStore = backing_store if backing_store is not None else InMemoryStore()
        self._namespace_target: NamespaceTargetResolver = namespace_target
        self._search_scope: str = search_scope
        self._ingest_on_put: bool = ingest_on_put
        self._merge_backing_search: bool = merge_backing_search

    # ------------------------------------------------------------------
    # Op partitioning helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _partition(ops: Iterable[Op]) -> tuple[list[tuple[int, Op]], list[tuple[int, SearchOp]]]:
        """Split ops into (delegated-to-backing, routed-to-Zep search) with indices."""
        op_list = list(ops)
        backing: list[tuple[int, Op]] = []
        searches: list[tuple[int, SearchOp]] = []
        for i, op in enumerate(op_list):
            if isinstance(op, SearchOp):
                searches.append((i, op))
            else:
                backing.append((i, op))
        return backing, searches

    def _ingest_put_payload(self, op: PutOp) -> str | None:
        """Serialise a ``PutOp`` value to a JSON string for ``graph.add``.

        Returns ``None`` for deletes (``value is None``) or values that cannot
        be serialised / exceed Zep's size limit.
        """
        if op.value is None:
            return None
        try:
            payload = json.dumps({"key": op.key, "value": op.value})
        except (TypeError, ValueError):
            logger.warning(
                "Could not JSON-serialise value at namespace=%s key=%s for Zep ingestion",
                op.namespace,
                op.key,
            )
            return None
        if len(payload) > MAX_GRAPH_ADD_CHARS:
            logger.warning(
                "Skipping Zep ingestion for namespace=%s key=%s: payload %d chars exceeds %d",
                op.namespace,
                op.key,
                len(payload),
                MAX_GRAPH_ADD_CHARS,
            )
            return None
        return payload

    # ------------------------------------------------------------------
    # Zep search → SearchItem conversion
    # ------------------------------------------------------------------

    def _results_to_search_items(self, op: SearchOp, result: Any) -> list[SearchItem]:
        """Convert Zep ``graph.search`` results into ``SearchItem`` objects."""
        now = datetime.now(UTC)
        items: list[SearchItem] = []
        scope = self._search_scope

        if scope == "edges":
            for edge in getattr(result, "edges", None) or []:
                fact = getattr(edge, "fact", None)
                if not fact:
                    continue
                items.append(
                    SearchItem(
                        namespace=op.namespace_prefix,
                        key=getattr(edge, "uuid_", "") or fact[:64],
                        value={"fact": fact, "type": "edge"},
                        created_at=now,
                        updated_at=now,
                        score=getattr(edge, "score", None),
                    )
                )
        elif scope == "nodes":
            for node in getattr(result, "nodes", None) or []:
                name = getattr(node, "name", None)
                if not name:
                    continue
                items.append(
                    SearchItem(
                        namespace=op.namespace_prefix,
                        key=getattr(node, "uuid_", "") or name,
                        value={
                            "name": name,
                            "summary": getattr(node, "summary", None),
                            "type": "node",
                        },
                        created_at=now,
                        updated_at=now,
                        score=getattr(node, "score", None),
                    )
                )
        else:  # episodes / auto / other -> use episodes when present
            for episode in getattr(result, "episodes", None) or []:
                content = getattr(episode, "content", None)
                if not content:
                    continue
                items.append(
                    SearchItem(
                        namespace=op.namespace_prefix,
                        key=getattr(episode, "uuid_", "") or content[:64],
                        value={"content": content, "type": "episode"},
                        created_at=now,
                        updated_at=now,
                        score=getattr(episode, "score", None),
                    )
                )

        if op.limit:
            items = items[: op.limit]
        return items

    def _zep_search_kwargs(self, op: SearchOp) -> dict[str, Any] | None:
        """Build ``graph.search`` kwargs for a ``SearchOp``, or ``None`` to skip."""
        if not op.query:
            # No natural-language query -> nothing for the semantic graph to do.
            return None
        target = self._namespace_target(op.namespace_prefix)
        return {
            "query": op.query,
            "scope": self._search_scope,
            "limit": op.limit or 10,
            **target,
        }

    # ------------------------------------------------------------------
    # Abstract methods (the only two required)
    # ------------------------------------------------------------------

    def batch(self, ops: Iterable[Op]) -> list[Result]:
        """Execute operations synchronously (the abstract ``BaseStore`` hook).

        KV operations are delegated to the backing store; ``put`` additionally
        ingests into Zep when a synchronous client is configured; ``search`` is
        routed to Zep semantic search. Results preserve input order.
        """
        backing_ops, search_ops = self._partition(ops)
        results: list[Result] = [None] * (len(backing_ops) + len(search_ops))

        # 1. Delegate KV ops to the backing store (faithful, synchronous).
        if backing_ops:
            backing_results = self._backing.batch([op for _, op in backing_ops])
            for (idx, op), res in zip(backing_ops, backing_results, strict=True):
                results[idx] = res
                # Mirror successful writes into Zep's graph.
                if self._ingest_on_put and isinstance(op, PutOp):
                    self._sync_ingest(op)

        # 2. Route search ops to Zep (semantic), optionally merged with backing.
        for idx, op in search_ops:
            results[idx] = self._sync_search(op)

        return results

    async def abatch(self, ops: Iterable[Op]) -> list[Result]:
        """Execute operations asynchronously (the abstract ``BaseStore`` hook).

        Uses the :class:`~zep_cloud.client.AsyncZep` client for ingestion and
        search. KV operations are delegated to the backing store. Results
        preserve input order.
        """
        backing_ops, search_ops = self._partition(ops)
        results: list[Result] = [None] * (len(backing_ops) + len(search_ops))

        if backing_ops:
            backing_results = await self._backing.abatch([op for _, op in backing_ops])
            for (idx, op), res in zip(backing_ops, backing_results, strict=True):
                results[idx] = res
                if self._ingest_on_put and isinstance(op, PutOp):
                    await self._async_ingest(op)

        for idx, op in search_ops:
            results[idx] = await self._async_search(op)

        return results

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    async def _async_ingest(self, op: PutOp) -> None:
        payload = self._ingest_put_payload(op)
        if payload is None:
            return
        target = self._namespace_target(op.namespace)
        try:
            await self._zep.graph.add(
                data=payload,
                type="json",
                user_id=target.get("user_id"),
                graph_id=target.get("graph_id"),
            )
        except Exception:
            logger.warning(
                "Failed to ingest namespace=%s key=%s into Zep",
                op.namespace,
                op.key,
                exc_info=True,
            )

    def _sync_ingest(self, op: PutOp) -> None:
        payload = self._ingest_put_payload(op)
        if payload is None:
            return
        if self._sync_zep is None:
            logger.warning(
                "No synchronous Zep client configured -- skipping Zep ingestion for "
                "namespace=%s key=%s. Pass sync_zep_client or use the async API.",
                op.namespace,
                op.key,
            )
            return
        target = self._namespace_target(op.namespace)
        try:
            self._sync_zep.graph.add(
                data=payload,
                type="json",
                user_id=target.get("user_id"),
                graph_id=target.get("graph_id"),
            )
        except Exception:
            logger.warning(
                "Failed to ingest namespace=%s key=%s into Zep",
                op.namespace,
                op.key,
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def _async_search(self, op: SearchOp) -> list[SearchItem]:
        items: list[SearchItem] = []
        kwargs = self._zep_search_kwargs(op)
        if kwargs is not None:
            try:
                result = await self._zep.graph.search(**kwargs)
                items = self._results_to_search_items(op, result)
            except Exception:
                logger.warning("Zep graph search failed for %s", op.namespace_prefix, exc_info=True)
        if self._merge_backing_search:
            items = self._merge(items, await self._backing.abatch([op]))
        return items

    def _sync_search(self, op: SearchOp) -> list[SearchItem]:
        items: list[SearchItem] = []
        kwargs = self._zep_search_kwargs(op)
        if kwargs is not None:
            if self._sync_zep is None:
                logger.warning(
                    "No synchronous Zep client configured -- Zep search skipped for %s. "
                    "Pass sync_zep_client or use the async API.",
                    op.namespace_prefix,
                )
            else:
                try:
                    result = self._sync_zep.graph.search(**kwargs)
                    items = self._results_to_search_items(op, result)
                except Exception:
                    logger.warning(
                        "Zep graph search failed for %s", op.namespace_prefix, exc_info=True
                    )
        if self._merge_backing_search:
            items = self._merge(items, self._backing.batch([op]))
        return items

    @staticmethod
    def _merge(zep_items: list[SearchItem], backing_results: list[Result]) -> list[SearchItem]:
        """Merge Zep search items with backing-store search results, de-duped by key."""
        merged: list[SearchItem] = list(zep_items)
        seen = {(tuple(i.namespace), i.key) for i in merged}
        backing = backing_results[0] if backing_results else None
        if isinstance(backing, list):
            for item in backing:
                if isinstance(item, SearchItem):
                    ident = (tuple(item.namespace), item.key)
                    if ident not in seen:
                        merged.append(item)
                        seen.add(ident)
        return merged


__all__ = ["ZepStore", "NamespaceTargetResolver", "Item", "SearchItem"]
