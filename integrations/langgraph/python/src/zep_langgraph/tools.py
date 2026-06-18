"""
Prebuilt Zep graph-search tools for LangGraph / LangChain agents.

:func:`create_graph_search_tool` returns a LangChain
:class:`~langchain_core.tools.StructuredTool` that wraps Zep's
:meth:`graph.search`. Bind it to a model (or pass it to
``create_react_agent(tools=[...])``) and the model decides when to search the
knowledge graph.

The search target is fixed at construction: pass ``user_id`` to search one
user's personal graph, or ``graph_id`` to search a shared standalone graph
(e.g. a documentation knowledge base). Exactly one of the two must be given.

Search parameters (``scope``, ``reranker``, ``limit``) are pinned at
construction time so they stay out of the model-facing schema -- the model only
provides the ``query``. Results are formatted into compact text the model can
read directly. A Zep failure returns an error string rather than raising, so the
agent loop never crashes.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field
from zep_cloud.client import AsyncZep, Zep

logger = logging.getLogger(__name__)

GraphSearchScope = Literal["edges", "nodes", "episodes", "auto"]
GraphSearchReranker = Literal["rrf", "mmr", "node_distance", "episode_mentions", "cross_encoder"]

#: Default tool name surfaced to the model.
DEFAULT_TOOL_NAME = "search_memory"

#: Default tool description surfaced to the model.
DEFAULT_TOOL_DESCRIPTION = (
    "Search long-term memory for facts, entities, and prior context relevant to "
    "the query. Use this to recall details the user shared earlier or knowledge "
    "stored in the graph. The input is a natural-language search query."
)


class _GraphSearchInput(BaseModel):
    """Argument schema exposed to the model: a single search query."""

    query: str = Field(
        description="Natural-language search query (max 400 characters).",
    )


def _format_results(result: Any, scope: str) -> str:
    """Format :class:`GraphSearchResults` into compact text for the model."""
    # ``auto`` scope returns a pre-assembled context string rather than lists.
    if scope == "auto":
        context: str | None = getattr(result, "context", None)
        if context and context.strip():
            return context.strip()

    parts: list[str] = []
    if scope == "edges":
        for edge in getattr(result, "edges", None) or []:
            fact = getattr(edge, "fact", None)
            if fact:
                parts.append(f"- {fact}")
    elif scope == "nodes":
        for node in getattr(result, "nodes", None) or []:
            name = getattr(node, "name", None) or "Entity"
            summary = getattr(node, "summary", None)
            parts.append(f"- {name}: {summary}" if summary else f"- {name}")
    elif scope == "episodes":
        for episode in getattr(result, "episodes", None) or []:
            content = getattr(episode, "content", None)
            if content:
                parts.append(f"- {content}")

    if parts:
        return "\n".join(parts)
    return "No results found."


def _resolve_target(user_id: str | None, graph_id: str | None) -> dict[str, str]:
    """Validate and return the mutually-exclusive search target kwargs."""
    if bool(user_id) == bool(graph_id):
        raise ValueError(
            "Provide exactly one of 'user_id' (personal user graph) or "
            "'graph_id' (shared standalone graph)."
        )
    return {"user_id": user_id} if user_id else {"graph_id": graph_id}  # type: ignore[dict-item]


def create_graph_search_tool(
    zep_client: AsyncZep,
    *,
    user_id: str | None = None,
    graph_id: str | None = None,
    name: str = DEFAULT_TOOL_NAME,
    description: str = DEFAULT_TOOL_DESCRIPTION,
    scope: GraphSearchScope = "edges",
    reranker: GraphSearchReranker | None = None,
    limit: int = 10,
    search_filters: Any | None = None,
) -> StructuredTool:
    """Create an async graph-search tool bound to a user or standalone graph.

    Args:
        zep_client: An initialised :class:`~zep_cloud.client.AsyncZep` client.
        user_id: The Zep user whose personal graph to search. Mutually exclusive
            with ``graph_id``.
        graph_id: The standalone graph to search. Mutually exclusive with
            ``user_id``.
        name: Tool name surfaced to the model.
        description: Tool description surfaced to the model.
        scope: What to search -- ``"edges"`` (facts, default), ``"nodes"``
            (entities + summaries), ``"episodes"`` (raw source data), or
            ``"auto"`` (Zep assembles a ready-to-use context string).
        reranker: Optional result-ranking strategy. ``None`` uses Zep's default
            (``rrf``).
        limit: Maximum number of results to return.
        search_filters: Optional :class:`~zep_cloud.types.search_filters.SearchFilters`
            to constrain results by entity/edge type, properties, or dates.

    Returns:
        A :class:`~langchain_core.tools.StructuredTool` with an async
        implementation, ready to bind to a model or pass to
        ``create_react_agent``.

    Raises:
        ValueError: If neither or both of ``user_id`` / ``graph_id`` are given.
    """
    target = _resolve_target(user_id, graph_id)

    async def _search(query: str) -> str:
        if not query or not query.strip():
            return "Error: empty search query."
        search_kwargs: dict[str, Any] = {
            "query": query,
            "scope": scope,
            "limit": limit,
            **target,
        }
        if reranker is not None:
            search_kwargs["reranker"] = reranker
        if search_filters is not None:
            search_kwargs["search_filters"] = search_filters

        try:
            result = await zep_client.graph.search(**search_kwargs)
        except Exception as exc:
            logger.warning("Zep graph search failed: %s", exc, exc_info=True)
            return f"Memory search failed: {exc}"
        return _format_results(result, scope)

    return StructuredTool.from_function(
        coroutine=_search,
        name=name,
        description=description,
        args_schema=_GraphSearchInput,
    )


def create_graph_search_tool_sync(
    zep_client: Zep,
    *,
    user_id: str | None = None,
    graph_id: str | None = None,
    name: str = DEFAULT_TOOL_NAME,
    description: str = DEFAULT_TOOL_DESCRIPTION,
    scope: GraphSearchScope = "edges",
    reranker: GraphSearchReranker | None = None,
    limit: int = 10,
    search_filters: Any | None = None,
) -> StructuredTool:
    """Synchronous variant of :func:`create_graph_search_tool`.

    Uses a synchronous :class:`~zep_cloud.client.Zep` client and returns a
    :class:`~langchain_core.tools.StructuredTool` with a synchronous
    implementation. See :func:`create_graph_search_tool` for argument semantics.

    Raises:
        ValueError: If neither or both of ``user_id`` / ``graph_id`` are given.
    """
    target = _resolve_target(user_id, graph_id)

    def _search(query: str) -> str:
        if not query or not query.strip():
            return "Error: empty search query."
        search_kwargs: dict[str, Any] = {
            "query": query,
            "scope": scope,
            "limit": limit,
            **target,
        }
        if reranker is not None:
            search_kwargs["reranker"] = reranker
        if search_filters is not None:
            search_kwargs["search_filters"] = search_filters

        try:
            result = zep_client.graph.search(**search_kwargs)
        except Exception as exc:
            logger.warning("Zep graph search failed: %s", exc, exc_info=True)
            return f"Memory search failed: {exc}"
        return _format_results(result, scope)

    return StructuredTool.from_function(
        func=_search,
        name=name,
        description=description,
        args_schema=_GraphSearchInput,
    )
