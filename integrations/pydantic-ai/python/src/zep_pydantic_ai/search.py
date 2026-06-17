"""
A Pydantic AI tool for searching a Zep knowledge graph on demand.

The history processor injects the user's context automatically on every turn.
This module provides the complementary *pull* path: a model-callable tool that
lets the agent decide when to search the graph for specific facts, entities, or
prior episodes.

:func:`create_zep_search_tool` returns an async function with the
``(ctx: RunContext[ZepDeps], query: str, ...)`` shape expected by Pydantic AI's
``@agent.tool`` decorator (and by the ``tools=[...]`` constructor argument).
Search parameters such as ``scope`` and ``reranker`` can be *pinned* at
construction time, locking them to a fixed value and hiding them from the
model's tool schema.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from pydantic_ai import RunContext
from zep_cloud.types.graph_search_results import GraphSearchResults

from .deps import ZepDeps

logger = logging.getLogger(__name__)

Scope = Literal["edges", "nodes", "episodes", "auto"]
Reranker = Literal["rrf", "mmr", "node_distance", "episode_mentions", "cross_encoder"]

#: The signature of the tool function ``create_zep_search_tool`` returns.
ZepSearchTool = Callable[[RunContext[ZepDeps], str], Awaitable[str]]


def create_zep_search_tool(
    *,
    graph_id: str | None = None,
    scope: Scope = "edges",
    reranker: Reranker = "rrf",
    limit: int = 10,
    name: str = "zep_search",
) -> ZepSearchTool:
    """Build a Pydantic AI tool that searches a Zep knowledge graph.

    Register the returned function as a tool, either via the decorator-free
    constructor argument or by re-decorating it::

        from pydantic_ai import Agent
        from zep_pydantic_ai import ZepDeps, create_zep_search_tool

        agent = Agent(
            "openai:gpt-4o-mini",
            deps_type=ZepDeps,
            tools=[create_zep_search_tool()],
        )

    By default the tool searches the **current user's** graph -- the user ID is
    resolved at call time from ``ctx.deps.user_id``.  Pass ``graph_id`` to
    target a shared standalone graph (e.g. a documentation knowledge base)
    instead; the user ID is then ignored.

    Args:
        graph_id: Optional standalone graph ID.  When set, all searches target
            this graph; when ``None`` (default), the current user's graph is
            searched.
        scope: What to search -- ``"edges"`` (facts, default), ``"nodes"``
            (entities + summaries), ``"episodes"`` (raw source text), or
            ``"auto"`` (Zep assembles a ready-to-use context string).
        reranker: Result ordering algorithm.  Defaults to ``"rrf"``.
        limit: Maximum number of results to return.  Defaults to ``10``.
        name: The tool name exposed to the model.  Defaults to ``"zep_search"``.

    Returns:
        An async function suitable for ``@agent.tool`` / ``tools=[...]``.  It
        accepts a ``RunContext[ZepDeps]`` and a ``query`` string and returns a
        formatted, model-readable results string.  Zep failures are caught and
        returned as an error string -- the tool never raises into the agent.
    """

    async def zep_search(ctx: RunContext[ZepDeps], query: str) -> str:
        """Search the knowledge graph for facts, entities, or prior context.

        Use this to look up specific details the user has shared before, or
        domain knowledge stored in the graph. Provide a focused natural-language
        query (max 400 characters).
        """
        deps = ctx.deps
        if deps is None:  # pragma: no cover - deps_type guarantees this
            return "Error: Zep dependencies are not available."

        search_kwargs: dict[str, Any] = {
            "query": query[:400],
            "scope": scope,
            "reranker": reranker,
            "limit": limit,
        }
        if graph_id:
            search_kwargs["graph_id"] = graph_id
        else:
            search_kwargs["user_id"] = deps.user_id

        try:
            results = await deps.client.graph.search(**search_kwargs)
        except Exception as exc:
            logger.warning("Zep graph search failed: %s", exc, exc_info=True)
            return f"Graph search failed: {exc}"

        return _format_results(results, scope)

    zep_search.__name__ = name
    return zep_search


def _format_results(results: GraphSearchResults, scope: Scope) -> str:
    """Render Zep search results as readable text for the model."""
    if scope == "auto":
        context = getattr(results, "context", None)
        if context and str(context).strip():
            return str(context).strip()
        return "No results found."

    parts: list[str] = []
    if scope == "edges" and results.edges:
        parts = [f"- {edge.fact}" for edge in results.edges if edge.fact]
    elif scope == "nodes" and results.nodes:
        for node in results.nodes:
            node_name = getattr(node, "name", None) or "Entity"
            summary = getattr(node, "summary", None)
            if summary:
                parts.append(f"- {node_name}: {summary}")
            else:
                parts.append(f"- {node_name}")
    elif scope == "episodes" and results.episodes:
        parts = [f"- {ep.content}" for ep in results.episodes if ep.content]

    return "\n".join(parts) if parts else "No results found."
