"""
A Pydantic AI tool for searching a Zep knowledge graph on demand.

The history processor injects the user's context automatically on every turn.
This module provides the complementary *pull* path: a model-callable tool that
lets the agent decide when to search the graph for specific facts, entities, or
prior episodes.

:func:`create_zep_search_tool` returns a :class:`pydantic_ai.Tool` -- suitable
for the ``tools=[...]`` constructor argument -- built from a hand-crafted JSON
schema (:data:`_SEARCH_PARAM_SPECS`) rather than introspected from a Python
function signature.  This lets any search parameter be *pinned* (fixed to a
value and hidden from the model) or *hidden* (removed from the schema, Zep's
own default applies) at construction time, independently of the others.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic_ai import RunContext, Tool
from zep_cloud.types.edge_search_filters import EdgeSearchFilters
from zep_cloud.types.episode_search_filters import EpisodeSearchFilters
from zep_cloud.types.node_search_filters import NodeSearchFilters
from zep_cloud.types.search_filters import SearchFilters
from zep_cloud.types.thread_summary_search_filters import ThreadSummarySearchFilters

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

#: Zep caps search ``page_size`` at 50; larger values are rejected.
MAX_SEARCH_LIMIT = 50

#: The type of the tool ``create_zep_search_tool`` returns.
ZepSearchTool = Tool[ZepDeps]

# ---------------------------------------------------------------------------
# Parameter definitions
# ---------------------------------------------------------------------------
# Each entry describes a V4 search parameter that can be pinned or exposed
# to the model. Model-
# exposed by default; hidden only when pinned or explicitly listed in
# ``hidden_params``.

_SEARCH_PARAM_SPECS: dict[str, dict[str, Any]] = {
    "scope": {
        "type": "string",
        "description": (
            "What to search for: 'edges' for facts and relationships, "
            "'nodes' for entities and their summaries, "
            "'episodes' for raw text data (unstructured text, messages, or JSON), "
            "'observations' for derived memories, "
            "'thread_summaries' for incremental thread summaries, "
            "'auto' to let Zep decide the best mix of results."
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

#: Parameters that are always constructor-only (complex types not suitable for
#: model schema generation).
_CONSTRUCTOR_ONLY_PARAMS = frozenset({"filters", "bfs_origin_node_uuids"})

#: All parameters that may be pinned or hidden at construction.
_PINNABLE_PARAMS = frozenset(_SEARCH_PARAM_SPECS.keys())


def create_zep_search_tool(
    *,
    graph_uuid: str | None = None,
    pinned_params: dict[str, Any] | None = None,
    hidden_params: set[str] | None = None,
    filters: dict[str, Any] | None = None,
    bfs_origin_node_uuids: list[str] | None = None,
    name: str = "zep_search",
    description: str = (
        "Search the knowledge graph for facts, entities, or prior context. "
        "Use this to look up specific details the user has shared before, or "
        "domain knowledge stored in the graph."
    ),
    # Back-compat: the original constructor args.  Each, if passed, pins
    # (hides) the corresponding parameter -- equivalent to putting it in
    # ``pinned_params``.
    scope: Scope | None = None,
    reranker: Reranker | None = None,
    limit: int | None = None,
) -> ZepSearchTool:
    """Build a Pydantic AI tool that searches a Zep knowledge graph.

    Register the returned tool with an agent::

        from pydantic_ai import Agent
        from zep_pydantic_ai import ZepDeps, create_zep_search_tool

        agent = Agent(
            "openai:gpt-4o-mini",
            deps_type=ZepDeps,
            tools=[create_zep_search_tool()],
        )

    The tool uses ``graph_uuid`` when set, then ``ZepDeps.graph_uuid``, then
    ``ZepDeps.user_uuid``. Pass ``graph_uuid`` to target a shared graph.

    **Pin-or-expose.** Every V4 search parameter (``scope``,
    ``reranker``, ``limit``, ``mmr_lambda``, ``center_node_uuid``) is exposed
    to the model in the tool's JSON schema by default, with the documented
    defaults above.  Use ``pinned_params`` to fix a parameter to a constant
    value and remove it from the schema (the model can no longer choose it);
    use ``hidden_params`` to remove a parameter from the schema *without*
    pinning it -- Zep's own server-side default applies, and the parameter is
    simply omitted from the SDK call.

    ``filters`` and ``bfs_origin_node_uuids`` are always
    constructor-only: their complex/list-of-object shapes are not exposed to
    the model.

    Args:
        graph_uuid: Optional graph UUID. When unset, the value from
            ``ZepDeps.graph_uuid`` or ``ZepDeps.user_uuid`` is used.
        pinned_params: Optional mapping of search parameter name to a
            fixed value.  Pinned parameters are hidden from the model's tool
            schema and always sent with the given value.
        hidden_params: Optional set of search parameter names to hide
            from the model's tool schema without pinning them -- omitted from
            the SDK call so Zep's own default takes effect.
        filters: Optional Zep search filters (constructor-only).
            Supports ``node_labels``, ``edge_types``, ``exclude_node_labels``,
            ``exclude_edge_types``, and property filters.
        bfs_origin_node_uuids: Optional list of node UUIDs for BFS seeding
            (constructor-only).
        name: The tool name exposed to the model.  Defaults to ``"zep_search"``.
        description: The tool description exposed to the model.
        scope: Deprecated back-compat alias for ``pinned_params={"scope": scope}``.
        reranker: Deprecated back-compat alias for ``pinned_params={"reranker": reranker}``.
        limit: Deprecated back-compat alias for ``pinned_params={"limit": limit}``.
            Values above Zep's ceiling of ``50`` are clamped; values below ``1``
            are floored to ``1``.

    Returns:
        A ``pydantic_ai.Tool[ZepDeps]`` suitable for ``tools=[...]``.  Calling
        it executes the V4 scope-specific graph search method with
        pinned/model-provided/default
        parameters merged; Zep failures are caught and returned as an error
        string -- the tool never raises into the agent.

    Raises:
        ValueError: If ``pinned_params`` (or a legacy alias) contains an
            unknown parameter name, or pins a required parameter to ``None``.
    """
    pinned: dict[str, Any] = dict(pinned_params or {})
    hidden: set[str] = set(hidden_params or ())

    # Legacy constructor args pin (and thus hide) their parameter, same as
    # passing it via pinned_params -- back-compat for the pre-pin-or-expose API.
    if scope is not None:
        pinned.setdefault("scope", scope)
    if reranker is not None:
        pinned.setdefault("reranker", reranker)
    if limit is not None:
        pinned.setdefault("limit", limit)

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

    # Auto scope uses graph.get_context, which has no reranker argument.
    if pinned.get("scope") == "auto" and "reranker" in pinned:
        logger.warning(
            "zep_search reranker %r is ignored for scope='auto'.",
            pinned["reranker"],
        )
        del pinned["reranker"]

    json_schema = _build_json_schema(pinned=pinned, hidden=hidden, description=description)

    async def zep_search(ctx: RunContext[ZepDeps], **kwargs: Any) -> str:
        deps = ctx.deps
        if deps is None:  # pragma: no cover - deps_type guarantees this
            return "Error: Zep dependencies are not available."

        query = str(kwargs.get("query", ""))[:400]
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

        # Clamp a model-provided limit to Zep's bounds at call time -- the
        # schema advertises them, but ``Tool.from_schema`` skips argument
        # validation, so an out-of-range value would otherwise 400.
        limit_value = search_kwargs.get("limit")
        if limit_value is not None and not 1 <= limit_value <= MAX_SEARCH_LIMIT:
            clamped_limit = max(1, min(limit_value, MAX_SEARCH_LIMIT))
            logger.warning(
                "zep_search limit %d is outside [1, %d]; clamping to %d",
                limit_value,
                MAX_SEARCH_LIMIT,
                clamped_limit,
            )
            search_kwargs["limit"] = clamped_limit

        if not search_kwargs.get("query"):
            return "Error: No search query provided."

        effective_scope = search_kwargs.pop("scope", "edges")
        effective_graph_uuid = graph_uuid or deps.graph_uuid or deps.user_uuid
        if effective_scope == "auto":
            search_method = deps.client.graph.get_context
            search_args: dict[str, Any] = {"query": query}
            if filters is not None:
                search_args["filters"] = SearchFilters(**filters)
        else:
            method_names = {
                "edges": "search_edges",
                "nodes": "search_nodes",
                "episodes": "search_episodes",
                "observations": "search_observations",
                "thread_summaries": "search_thread_summaries",
            }
            search_method = getattr(deps.client.graph, method_names[effective_scope])
            search_args = {"query": query}
            if "limit" in search_kwargs:
                search_args["page_size"] = search_kwargs["limit"]
            for name in ("reranker", "mmr_lambda", "center_node_uuid"):
                if name in search_kwargs:
                    search_args[name] = search_kwargs[name]
            if filters is not None:
                search_args["filters"] = _typed_filters(effective_scope, filters)
            if bfs_origin_node_uuids is not None:
                search_args["bfs_origin_node_uuids"] = bfs_origin_node_uuids

        try:
            results = await search_method(effective_graph_uuid, **search_args)
        except Exception as exc:
            logger.warning("Zep graph search failed: %s", exc, exc_info=True)
            return f"Graph search failed: {exc}"

        return _format_results(results, effective_scope)

    return Tool.from_schema(
        zep_search,
        name=name,
        description=description,
        json_schema=json_schema,
        takes_ctx=True,
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
        if "minimum" in spec:
            prop["minimum"] = spec["minimum"]
        if "maximum" in spec:
            prop["maximum"] = spec["maximum"]
        properties[param_name] = prop

    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "description": description,
    }


def _typed_filters(scope: str, filters: dict[str, Any]) -> Any:
    if scope == "observations":
        return dict(filters)
    filter_models = {
        "edges": EdgeSearchFilters,
        "nodes": NodeSearchFilters,
        "episodes": EpisodeSearchFilters,
        "thread_summaries": ThreadSummarySearchFilters,
    }
    return filter_models[scope](**filters)


def _format_results(results: Any, scope: Scope) -> str:
    """Render Zep search results as readable text for the model."""
    if scope == "auto":
        context = getattr(results, "context", None)
        if context and str(context).strip():
            return str(context).strip()
        return "No results found."

    parts: list[str] = []
    items = getattr(results, "items", None) or []
    if scope == "edges":
        parts = [f"- {edge.fact}" for edge in items if getattr(edge, "fact", None)]
    elif scope == "nodes":
        for node in items:
            node_name = getattr(node, "name", None) or "Entity"
            summary = getattr(node, "summary", None)
            if summary:
                parts.append(f"- {node_name}: {summary}")
            else:
                parts.append(f"- {node_name}")
    elif scope == "episodes":
        parts = [f"- {ep.content}" for ep in items if getattr(ep, "content", None)]
    elif scope == "observations":
        # Observations are DerivedNode items: ``name`` carries the derived
        # pattern, with an optional region ``summary``.
        for obs in items:
            obs_name = getattr(obs, "name", None) or "Observation"
            summary = getattr(obs, "summary", None)
            if summary:
                parts.append(f"- {obs_name}: {summary}")
            else:
                parts.append(f"- {obs_name}")
    elif scope == "thread_summaries":
        # Thread summaries are GraphitiSagaNode items: ``summary`` holds the
        # incremental thread summary; fall back to ``name`` when absent.
        for ts in items:
            summary = getattr(ts, "summary", None)
            text = summary or getattr(ts, "name", None)
            if text:
                parts.append(f"- {text}")

    return "\n".join(parts) if parts else "No results found."
