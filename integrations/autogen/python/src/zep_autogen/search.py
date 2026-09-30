"""Scoped graph search helpers for the Zep v4 SDK.

Zep v4 replaces the single v3 ``graph.search`` call with one method for each
scope, and each method returns an ``AsyncPager``. The helpers here run the
method that belongs to a scope, collect at most ``limit`` results from the
pager, and convert the results into AutoGen ``MemoryContent`` objects.

The helpers are private to the package. ``ZepUserMemory``, ``ZepGraphMemory``,
and the search tool all use them, so the three call paths return the same
shapes.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from autogen_core.memory import MemoryContent, MemoryMimeType
from zep_cloud.client import AsyncZep

logger = logging.getLogger(__name__)

#: Search scopes that Zep v4 supports. ``auto`` is not a pager scope: it maps
#: to ``graph.get_context``, which returns a composed context string.
Scope = Literal[
    "edges",
    "nodes",
    "episodes",
    "observations",
    "thread_summaries",
    "auto",
]

#: Scopes that map to a paged search method.
PAGED_SCOPES = ("edges", "nodes", "episodes", "observations", "thread_summaries")

#: Zep caps the ``limit`` of a graph search at 50. A larger value is rejected.
MAX_SEARCH_LIMIT = 50


def clamp_limit(limit: int) -> int:
    """Clamp a search limit into the range that Zep accepts."""
    if limit > MAX_SEARCH_LIMIT:
        logger.warning(
            "zep search limit %d exceeds the Zep ceiling %d; the limit is clamped to %d",
            limit,
            MAX_SEARCH_LIMIT,
            MAX_SEARCH_LIMIT,
        )
        return MAX_SEARCH_LIMIT
    if limit < 1:
        return 1
    return limit


async def collect(pager: Any, limit: int) -> list[Any]:
    """Collect at most ``limit`` items from an ``AsyncPager``."""
    items: list[Any] = []
    async for item in pager:
        items.append(item)
        if len(items) >= limit:
            break
    return items


async def search_scope(
    client: AsyncZep,
    *,
    graph_uuid: str,
    query: str,
    scope: str = "edges",
    limit: int = 10,
    **kwargs: Any,
) -> list[Any]:
    """Run the v4 search method of ``scope`` and return the results.

    Args:
        client: An initialised ``AsyncZep`` client.
        graph_uuid: The UUID of the graph to search.
        query: The search query text.
        scope: One of :data:`PAGED_SCOPES`.
        limit: The maximum number of results.
        **kwargs: Further parameters of the search method, such as
            ``filters``, ``reranker``, ``mmr_lambda``, ``center_node_uuid``,
            and ``bfs_origin_node_uuids``.

    Returns:
        A list of result objects of the scope.

    Raises:
        ValueError: If ``scope`` is not a paged scope.
    """
    limit = clamp_limit(limit)
    if scope == "edges":
        pager: Any = await client.graph.search_edges(graph_uuid, query=query, limit=limit, **kwargs)
    elif scope == "nodes":
        pager = await client.graph.search_nodes(graph_uuid, query=query, limit=limit, **kwargs)
    elif scope == "episodes":
        pager = await client.graph.search_episodes(graph_uuid, query=query, limit=limit, **kwargs)
    elif scope == "observations":
        pager = await client.graph.search_observations(
            graph_uuid, query=query, limit=limit, **kwargs
        )
    elif scope == "thread_summaries":
        pager = await client.graph.search_thread_summaries(
            graph_uuid, query=query, limit=limit, **kwargs
        )
    else:
        raise ValueError(f"Unsupported search scope: {scope}. Supported: {list(PAGED_SCOPES)}")

    return await collect(pager, limit)


def results_to_memory_content(results: list[Any], scope: str, source: str) -> list[MemoryContent]:
    """Convert scoped search results into AutoGen ``MemoryContent`` objects.

    Args:
        results: The result objects of one scope.
        scope: The scope that produced the results.
        source: The value of the ``source`` metadata key.

    Returns:
        A list of ``MemoryContent`` objects. An item without text content is
        dropped.
    """
    contents: list[MemoryContent] = []
    for item in results:
        if scope == "edges":
            if not item.fact:
                continue
            contents.append(
                MemoryContent(
                    content=item.fact,
                    mime_type=MemoryMimeType.TEXT,
                    metadata={
                        "source": source,
                        "edge_name": item.name,
                        "edge_attributes": item.attributes or {},
                        "created_at": item.created_at,
                        "expired_at": item.expired_at,
                        "valid_at": item.valid_at,
                        "invalid_at": item.invalid_at,
                    },
                )
            )
        elif scope in ("nodes", "observations"):
            text = name_summary_text(item.name, item.summary)
            if not text:
                continue
            contents.append(
                MemoryContent(
                    content=text,
                    mime_type=MemoryMimeType.TEXT,
                    metadata={
                        "source": source,
                        "node_name": item.name,
                        "node_attributes": item.attributes or {},
                        "created_at": item.created_at,
                    },
                )
            )
        elif scope == "episodes":
            if not item.content:
                continue
            contents.append(
                MemoryContent(
                    content=item.content,
                    mime_type=MemoryMimeType.TEXT,
                    metadata={
                        "source": source,
                        "episode_type": item.source,
                        "episode_role": item.role,
                        "episode_name": item.role_name,
                        "created_at": item.created_at,
                    },
                )
            )
        elif scope == "thread_summaries":
            if not item.summary:
                continue
            contents.append(
                MemoryContent(
                    content=item.summary,
                    mime_type=MemoryMimeType.TEXT,
                    metadata={
                        "source": source,
                        "thread_uuid": item.thread_uuid,
                        "created_at": item.created_at,
                    },
                )
            )
    return contents


def name_summary_text(name: str | None, summary: str | None) -> str:
    """Join a name and a summary as ``"name: summary"``."""
    if name and summary:
        return f"{name}: {summary}"
    if name:
        return name
    if summary:
        return summary
    return ""
