"""
A Microsoft Agent Framework tool for searching a Zep knowledge graph on demand.

``ZepContextProvider`` injects the user's context automatically on every turn.
This module provides the complementary *pull* path: a model-callable tool that
lets the agent decide when to search the graph for specific facts, entities, or
prior episodes.

:func:`create_zep_search_tool` returns an ``agent_framework.FunctionTool`` --
suitable for ``context.extend_tools(source_id, [tool])`` or an agent's
``tools=[...]`` -- built from a hand-crafted JSON schema
(:data:`_SEARCH_PARAM_SPECS`) rather than introspected from a Python function
signature.  This lets any search parameter be *pinned* (fixed to a value and
hidden from the model) or *hidden* (removed from the schema, Zep's own default
applies) at construction time, independently of the others.

Zep v4 has one search method for each scope, and every method addresses the
graph by its UUID.  The tool keeps the single model-facing ``scope``
parameter and dispatches to the matching v4 method.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from agent_framework import FunctionTool, tool
from zep_cloud.client import AsyncZep

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

#: Zep caps the search ``limit`` at 50; larger values are rejected.
MAX_SEARCH_LIMIT = 50

#: The type of a v4 graph search method: it takes the graph UUID and the
#: search parameters, and it returns a pager.
_SearchMethod = Callable[..., Awaitable[Any]]


def _scope_methods(client: AsyncZep) -> dict[str, _SearchMethod]:
    """Map each scope to the v4 graph search method that serves it.

    The ``auto`` scope has no search method. It maps to ``graph.get_context``,
    which assembles a context block across all scopes.
    """
    graph = client.graph
    return {
        "edges": graph.search_edges,
        "nodes": graph.search_nodes,
        "episodes": graph.search_episodes,
        "observations": graph.search_observations,
        "thread_summaries": graph.search_thread_summaries,
    }


#: The type of tool ``create_zep_search_tool`` returns.
ZepSearchTool = FunctionTool

# ---------------------------------------------------------------------------
# Parameter definitions
# ---------------------------------------------------------------------------
# Each entry describes a search parameter that can be pinned or exposed to the
# model.  Keys match the Zep SDK's ``graph.search_*()`` kwargs.  Model-exposed
# by default; hidden only when pinned or explicitly listed in
# ``search_hidden_params``.

_SEARCH_PARAM_SPECS: dict[str, dict[str, Any]] = {
    "scope": {
        "type": "string",
        "description": (
            "What to search for: 'edges' for facts and relationships, "
            "'nodes' for entities and their summaries, "
            "'episodes' for raw text data (unstructured text, messages, or JSON), "
            "'observations' for derived memories, "
            "'thread_summaries' for incremental thread summaries, "
            "'auto' to let Zep assemble a context block across all scopes."
        ),
        "enum": ["edges", "nodes", "episodes", "observations", "thread_summaries", "auto"],
        "default": "edges",
    },
    "reranker": {
        "type": "string",
        "description": (
            "Result ordering algorithm: 'rrf' (balanced), 'mmr' (diverse), "
            "'cross_encoder' (highest accuracy), 'episode_mentions' "
            "(frequently referenced), 'node_distance' (near a specific entity)."
        ),
        "enum": ["rrf", "mmr", "node_distance", "episode_mentions", "cross_encoder"],
        "default": "rrf",
    },
    "limit": {
        "type": "integer",
        "description": "Maximum number of results to return.",
        "default": 10,
        "minimum": 1,
        "maximum": MAX_SEARCH_LIMIT,
    },
    "mmr_lambda": {
        "type": "number",
        "description": (
            "Balance between diversity (0.0) and relevance (1.0). Only used when reranker is 'mmr'."
        ),
    },
    "center_node_uuid": {
        "type": "string",
        "description": (
            "UUID of the center node for distance-based reranking. "
            "Required when reranker is 'node_distance'."
        ),
    },
}

#: Parameters that the ``auto`` scope does not accept.  ``graph.get_context``
#: assembles the context block itself, so it takes no ranking parameters.
_AUTO_UNSUPPORTED_PARAMS = ("reranker", "limit", "mmr_lambda", "center_node_uuid")

#: Parameters that are always constructor-only (complex types not suitable for
#: model schema generation).
_CONSTRUCTOR_ONLY_PARAMS = frozenset({"search_filters", "bfs_origin_node_uuids"})

#: All parameters that may be pinned or hidden at construction.
_PINNABLE_PARAMS = frozenset(_SEARCH_PARAM_SPECS.keys())


def create_zep_search_tool(
    *,
    zep_client: AsyncZep,
    graph_uuid: str,
    search_pinned_params: dict[str, Any] | None = None,
    search_hidden_params: set[str] | None = None,
    search_filters: dict[str, Any] | None = None,
    bfs_origin_node_uuids: list[str] | None = None,
    name: str = "zep_search",
    description: str = (
        "Search the knowledge graph for facts, entities, or prior context. "
        "Use this to look up specific details the user has shared before, or "
        "domain knowledge stored in the graph."
    ),
) -> ZepSearchTool:
    """Build a Microsoft Agent Framework tool that searches a Zep knowledge graph.

    Register the returned tool with an agent, or add it to a single run via
    ``context.extend_tools(source_id, [tool])`` (see
    ``ZepContextProvider(expose_search_tool=True)``)::

        from zep_ms_agent_framework.search import create_zep_search_tool

        tool = create_zep_search_tool(zep_client=zep, graph_uuid=user.graph_uuid)

    Zep v4 addresses a graph by its UUID. To search the graph of a user, give
    the ``graph_uuid`` that ``user.create`` returns. To search a standalone
    graph, such as a documentation knowledge base, give the ``uuid_`` that
    ``graph.create`` returns.

    **Pin-or-expose.** Every search parameter (``scope``, ``reranker``,
    ``limit``, ``mmr_lambda``, ``center_node_uuid``) is exposed to the model
    in the tool's JSON schema by default, with the documented defaults above.
    Use ``search_pinned_params`` to fix a parameter to a constant value and
    remove it from the schema (the model can no longer choose it); use
    ``search_hidden_params`` to remove a parameter from the schema *without*
    pinning it -- Zep's own server-side default applies, and the parameter is
    simply omitted from the SDK call.

    ``search_filters`` and ``bfs_origin_node_uuids`` are always
    constructor-only: their complex/list-of-object shapes are not exposed to
    the model.

    Args:
        zep_client: An initialised ``AsyncZep`` client.
        graph_uuid: The UUID of the graph to search.  Use the ``graph_uuid``
            of a user for personal memory, or the ``uuid_`` of a standalone
            graph for shared knowledge.
        search_pinned_params: Optional mapping of search parameter name to a
            fixed value.  Pinned parameters are hidden from the model's tool
            schema and always sent with the given value.
        search_hidden_params: Optional set of search parameter names to hide
            from the model's tool schema without pinning them -- omitted from
            the SDK call so Zep's own default takes effect.
        search_filters: Optional Zep search filters (constructor-only), sent
            as the SDK's ``filters`` argument.  Supports ``node_labels``,
            ``edge_types``, ``exclude_node_labels``, ``exclude_edge_types``,
            and property filters.
        bfs_origin_node_uuids: Optional list of node UUIDs for BFS seeding
            (constructor-only).
        name: The tool name exposed to the model.  Defaults to ``"zep_search"``.
        description: The tool description exposed to the model.

    Returns:
        An ``agent_framework.FunctionTool`` suitable for ``tools=[...]`` or
        ``context.extend_tools(...)``.  Calling it executes the v4 search
        method for the effective scope with pinned/model-provided/default
        parameters merged; Zep failures are caught and returned as an error
        string -- the tool never raises.

    Raises:
        ValueError: If ``graph_uuid`` is empty, or if
            ``search_pinned_params``/``search_hidden_params`` contains an
            unknown parameter name.
    """
    if not graph_uuid:
        raise ValueError("graph_uuid must be a non-empty string")

    pinned: dict[str, Any] = dict(search_pinned_params or {})
    hidden: set[str] = set(search_hidden_params or ())

    unknown_pinned = set(pinned.keys()) - _PINNABLE_PARAMS
    if unknown_pinned:
        raise ValueError(
            f"Unknown pinned parameters: {unknown_pinned}. Allowed: {sorted(_PINNABLE_PARAMS)}"
        )
    unknown_hidden = hidden - _PINNABLE_PARAMS
    if unknown_hidden:
        raise ValueError(
            f"Unknown hidden parameters: {unknown_hidden}. Allowed: {sorted(_PINNABLE_PARAMS)}"
        )

    # Clamp a pinned limit to Zep's ceiling at construction time so the call
    # never 400s.
    if "limit" in pinned:
        pinned_limit = pinned["limit"]
        if pinned_limit > MAX_SEARCH_LIMIT:
            logger.warning(
                "zep_search limit %d exceeds Zep ceiling %d; clamping to %d",
                pinned_limit,
                MAX_SEARCH_LIMIT,
                MAX_SEARCH_LIMIT,
            )
            pinned["limit"] = MAX_SEARCH_LIMIT
        elif pinned_limit < 1:
            pinned["limit"] = 1

    json_schema = _build_json_schema(pinned=pinned, hidden=hidden, description=description)

    async def zep_search(**kwargs: Any) -> str:
        query = str(kwargs.get("query", ""))[:400]
        if not query:
            return "Error: No search query provided."

        search_kwargs: dict[str, Any] = {"query": query}

        for param_name in _SEARCH_PARAM_SPECS:
            if param_name in pinned:
                search_kwargs[param_name] = pinned[param_name]
            elif param_name in hidden:
                continue  # hidden, not pinned -> omit; Zep applies its own default
            elif param_name in kwargs and kwargs[param_name] is not None:
                search_kwargs[param_name] = kwargs[param_name]
            else:
                default = _SEARCH_PARAM_SPECS[param_name].get("default")
                if default is not None:
                    search_kwargs[param_name] = default

        # Clamp a model-provided limit to Zep's bounds so the call never 400s
        # (a pinned limit is already clamped at construction time; the schema
        # advertises these bounds, but not every model respects them).
        if "limit" in search_kwargs:
            search_kwargs["limit"] = min(max(search_kwargs["limit"], 1), MAX_SEARCH_LIMIT)

        effective_scope = str(search_kwargs.pop("scope", "edges"))

        if search_filters is not None:
            search_kwargs["filters"] = search_filters
        if bfs_origin_node_uuids is not None:
            search_kwargs["bfs_origin_node_uuids"] = bfs_origin_node_uuids

        if effective_scope == "auto":
            # graph.get_context assembles the context block itself and takes
            # no ranking parameters.
            for param_name in _AUTO_UNSUPPORTED_PARAMS:
                search_kwargs.pop(param_name, None)
            search_kwargs.pop("bfs_origin_node_uuids", None)
            try:
                response = await zep_client.graph.get_context(graph_uuid, **search_kwargs)
            except Exception as exc:
                logger.warning("Zep graph context failed: %s", exc, exc_info=True)
                return f"Graph search failed: {exc}"
            context = getattr(response, "context", None)
            if context and str(context).strip():
                return str(context).strip()
            return "No results found."

        search_method = _scope_methods(zep_client).get(effective_scope)
        if search_method is None:
            return f"Error: Unknown search scope {effective_scope!r}."

        try:
            pager = await search_method(graph_uuid, **search_kwargs)
        except Exception as exc:
            logger.warning("Zep graph search failed: %s", exc, exc_info=True)
            return f"Graph search failed: {exc}"

        # A v4 search method returns a cursor pager. The first page holds up
        # to ``limit`` results, which is the full result set that the tool
        # asks for, so the tool does not follow the cursor.
        return _format_results(pager.items or [], effective_scope)

    return tool(
        zep_search,
        name=name,
        description=description,
        schema=json_schema,
    )


def _build_json_schema(
    *,
    pinned: dict[str, Any],
    hidden: set[str],
    description: str,
) -> dict[str, Any]:
    """Build the model-facing JSON schema, excluding pinned/hidden parameters."""
    properties: dict[str, Any] = {
        "query": {
            "type": "string",
            "description": "Search query text (max 400 characters).",
        }
    }
    required = ["query"]

    for param_name, spec in _SEARCH_PARAM_SPECS.items():
        if param_name in pinned or param_name in hidden:
            continue  # pinned or hidden -> not exposed to the model

        prop: dict[str, Any] = {"type": spec["type"], "description": spec["description"]}
        if "enum" in spec:
            prop["enum"] = spec["enum"]
        for bound in ("minimum", "maximum"):
            if bound in spec:
                prop[bound] = spec[bound]
        properties[param_name] = prop

    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "description": description,
    }


def _format_results(items: list[Any], scope: str) -> str:
    """Render Zep search results as readable text for the model."""
    parts: list[str] = []

    if scope == "edges":
        parts = [f"- {edge.fact}" for edge in items if getattr(edge, "fact", None)]
    elif scope in ("nodes", "observations"):
        label = "Entity" if scope == "nodes" else "Observation"
        for item in items:
            item_name = getattr(item, "name", None) or label
            summary = getattr(item, "summary", None)
            if summary:
                parts.append(f"- {item_name}: {summary}")
            else:
                parts.append(f"- {item_name}")
    elif scope == "episodes":
        parts = [f"- {ep.content}" for ep in items if getattr(ep, "content", None)]
    elif scope == "thread_summaries":
        for item in items:
            summary = getattr(item, "summary", None) or getattr(item, "name", None)
            if summary:
                parts.append(f"- {summary}")

    return "\n".join(parts) if parts else "No results found."
