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

Scope = Literal[
    "edges",
    "nodes",
    "episodes",
    "observations",
    "thread_summaries",
    "auto",
]
Reranker = Literal["rrf", "mmr", "node_distance", "episode_mentions", "cross_encoder"]

#: Zep caps ``graph.search`` ``limit`` at 50; larger values are rejected.
MAX_SEARCH_LIMIT = 50

#: Rerankers Zep rejects when ``scope == "auto"`` (auto always uses RRF
#: retrieval and applies its own internal cross-scope rerank).
_AUTO_INCOMPATIBLE_RERANKERS = ("node_distance", "episode_mentions")

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
            (entities + summaries), ``"episodes"`` (raw source text),
            ``"observations"``, ``"thread_summaries"``, or ``"auto"`` (Zep
            assembles a ready-to-use context string).
        reranker: Result ordering algorithm.  Defaults to ``"rrf"``.  Ignored
            when ``scope == "auto"``; ``"node_distance"`` / ``"episode_mentions"``
            are dropped there (Zep rejects them for auto search).
        limit: Maximum number of results to return.  Defaults to ``10`` and is
            clamped to Zep's ceiling of ``50``.
        name: The tool name exposed to the model.  Defaults to ``"zep_search"``.

    Returns:
        An async function suitable for ``@agent.tool`` / ``tools=[...]``.  It
        accepts a ``RunContext[ZepDeps]`` and a ``query`` string and returns a
        formatted, model-readable results string.  Zep failures are caught and
        returned as an error string -- the tool never raises into the agent.
    """
    # Clamp limit to Zep's ceiling at construction time so the call never 400s.
    if limit > MAX_SEARCH_LIMIT:
        logger.warning(
            "zep_search limit %d exceeds Zep ceiling %d; clamping to %d",
            limit,
            MAX_SEARCH_LIMIT,
            MAX_SEARCH_LIMIT,
        )
        limit = MAX_SEARCH_LIMIT
    elif limit < 1:
        limit = 1

    # Auto scope rejects node_distance / episode_mentions and ignores reranker
    # entirely.  Resolve the effective reranker once, here, so the call path is
    # always valid.
    effective_reranker: Reranker | None = reranker
    if scope == "auto":
        if reranker in _AUTO_INCOMPATIBLE_RERANKERS:
            logger.warning(
                "zep_search reranker %r is invalid for scope='auto'; "
                "omitting reranker (auto search uses RRF).",
                reranker,
            )
        effective_reranker = None

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
            "limit": limit,
        }
        if effective_reranker is not None:
            search_kwargs["reranker"] = effective_reranker
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
    elif scope == "observations" and results.observations:
        # Observations are DerivedNode items: ``name`` carries the derived
        # pattern, with an optional region ``summary``.
        for obs in results.observations:
            obs_name = getattr(obs, "name", None) or "Observation"
            summary = getattr(obs, "summary", None)
            if summary:
                parts.append(f"- {obs_name}: {summary}")
            else:
                parts.append(f"- {obs_name}")
    elif scope == "thread_summaries" and results.thread_summaries:
        # Thread summaries are GraphitiSagaNode items: ``summary`` holds the
        # incremental thread summary; fall back to ``name`` when absent.
        for ts in results.thread_summaries:
            summary = getattr(ts, "summary", None)
            text = summary or getattr(ts, "name", None)
            if text:
                parts.append(f"- {text}")

    return "\n".join(parts) if parts else "No results found."
