"""
Prebuilt Zep graph-search tools for LangGraph / LangChain agents.

:func:`create_graph_search_tool` returns a LangChain
:class:`~langchain_core.tools.StructuredTool` that wraps Zep's graph search.
Bind it to a model (or pass it to ``create_react_agent(tools=[...])``) and the
model decides when to search the knowledge graph.

The search target is fixed at construction: pass the ``graph_uuid`` of the
graph to search. For one user's personal graph this is ``User.graph_uuid``;
for a shared standalone graph it is ``Graph.uuid_``. Zep v4 addresses a graph
by its UUID only, so resolve an application identifier one time and store the
UUID.

Zep v4 has one search method for each scope. The tool sends ``scope`` to
``graph.search_edges``, ``graph.search_nodes``, ``graph.search_episodes``,
``graph.search_observations``, or ``graph.search_thread_summaries``, and it
sends ``scope="auto"`` to ``graph.get_context``.

``create_graph_search_tool`` follows the pin-or-expose pattern shared by the
other Zep framework integrations: every search parameter (``scope``,
``reranker``, ``limit``, ``mmr_lambda``, ``center_node_uuid``) is exposed to
the model by default and can be pinned (fixed to a constant, hidden from the
model) or hidden (removed from the schema without pinning; Zep's own default
applies) at construction time. The exposed schema is built dynamically with
``pydantic.create_model`` and passed as the ``StructuredTool``'s
``args_schema``. Results are formatted into compact text the model can read
directly. A Zep failure returns an error string rather than raising, so the
agent loop never crashes.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, create_model
from zep_cloud.client import AsyncZep, Zep

logger = logging.getLogger(__name__)

GraphSearchScope = Literal[
    "edges",
    "nodes",
    "episodes",
    "observations",
    "thread_summaries",
    "auto",
]
GraphSearchReranker = Literal["rrf", "mmr", "node_distance", "episode_mentions", "cross_encoder"]

#: Zep caps the graph-search ``limit`` at 50; larger values are rejected.
MAX_SEARCH_LIMIT = 50

#: Rerankers Zep rejects when ``scope == "auto"`` (auto always uses RRF
#: retrieval and applies its own internal cross-scope rerank).
_AUTO_INCOMPATIBLE_RERANKERS = ("node_distance", "episode_mentions")

#: The Zep v4 search method for each scope. ``auto`` is served by
#: ``graph.get_context`` instead, which takes a different parameter set.
_SCOPE_METHODS: dict[str, str] = {
    "edges": "search_edges",
    "nodes": "search_nodes",
    "episodes": "search_episodes",
    "observations": "search_observations",
    "thread_summaries": "search_thread_summaries",
}

#: Parameters ``graph.get_context`` does not accept. They are dropped when the
#: effective scope is ``auto``.
_AUTO_UNSUPPORTED_PARAMS = ("reranker", "limit", "mmr_lambda", "center_node_uuid")

#: Default tool name surfaced to the model.
DEFAULT_TOOL_NAME = "search_memory"

#: Default tool description surfaced to the model.
DEFAULT_TOOL_DESCRIPTION = (
    "Search long-term memory for facts, entities, and prior context relevant to "
    "the query. Use this to recall details the user shared earlier or knowledge "
    "stored in the graph. The input is a natural-language search query."
)

# ---------------------------------------------------------------------------
# Parameter definitions
# ---------------------------------------------------------------------------
# Each entry describes a search parameter that can be pinned or exposed to the
# model.  Every key except ``scope`` matches a keyword argument of the v4
# ``graph.search_*`` methods; ``scope`` selects the method itself.  Model-
# exposed by default; hidden only when pinned or explicitly listed in
# ``hidden_params``.  ``annotation`` is the typed annotation used to build the
# dynamic pydantic args_schema.

_SEARCH_PARAM_SPECS: dict[str, dict[str, Any]] = {
    "scope": {
        "annotation": GraphSearchScope,
        "description": (
            "What to search for: 'edges' for facts and relationships, "
            "'nodes' for entities and their summaries, "
            "'episodes' for raw text data (unstructured text, messages, or JSON), "
            "'observations' for derived memories, "
            "'thread_summaries' for incremental thread summaries, "
            "'auto' to let Zep decide the best mix of results."
        ),
        "default": "edges",
    },
    "reranker": {
        "annotation": GraphSearchReranker,
        "description": (
            "Result ordering algorithm: 'rrf' (balanced), 'mmr' (diverse), "
            "'cross_encoder' (highest accuracy), 'episode_mentions' "
            "(frequently referenced), 'node_distance' (near a specific entity)."
        ),
        "default": "rrf",
    },
    "limit": {
        "annotation": int,
        "description": "Maximum number of results to return.",
        "default": 10,
    },
    "mmr_lambda": {
        "annotation": float | None,
        "description": (
            "Balance between diversity (0.0) and relevance (1.0). Only used when reranker is 'mmr'."
        ),
        "default": None,
    },
    "center_node_uuid": {
        "annotation": str | None,
        "description": (
            "UUID of the center node for distance-based reranking. "
            "Required when reranker is 'node_distance'."
        ),
        "default": None,
    },
}

#: Parameters that are always constructor-only (complex types not suitable for
#: model schema generation).
_CONSTRUCTOR_ONLY_PARAMS = frozenset({"filters", "bfs_origin_node_uuids"})

#: All parameters that may be pinned or hidden at construction.
_PINNABLE_PARAMS = frozenset(_SEARCH_PARAM_SPECS.keys())


def _build_args_schema(
    *,
    pinned: dict[str, Any],
    hidden: set[str],
) -> type[BaseModel]:
    """Build the pydantic model ``StructuredTool`` uses as its ``args_schema``.

    ``query`` is always present and required. Params in ``_SEARCH_PARAM_SPECS``
    that are neither pinned nor hidden become model-visible fields with their
    documented default.
    """
    fields: dict[str, Any] = {
        "query": (str, Field(description="Natural-language search query (max 400 characters)."))
    }
    for param_name, spec in _SEARCH_PARAM_SPECS.items():
        if param_name in pinned or param_name in hidden:
            continue  # pinned or hidden -> not exposed to the model
        fields[param_name] = (
            spec["annotation"],
            Field(default=spec["default"], description=spec["description"]),
        )
    return create_model("GraphSearchInput", **fields)


def _format_results(result: Any, scope: str) -> str:
    """Format one page of v4 search results into compact text for the model.

    ``auto`` scope reads the assembled Context Block from
    :class:`~zep_cloud.types.graph_context_response.GraphContextResponse`.
    Every other scope reads the first page of a pager, whose ``items`` hold
    the records of that scope.
    """
    if scope == "auto":
        context: str | None = getattr(result, "context", None)
        if context and context.strip():
            return context.strip()
        return "No results found."

    items = getattr(result, "items", None) or []
    parts: list[str] = []
    if scope == "edges":
        for edge in items:
            fact = getattr(edge, "fact", None)
            if fact:
                parts.append(f"- {fact}")
    elif scope in ("nodes", "observations"):
        default_name = "Entity" if scope == "nodes" else "Observation"
        for record in items:
            name = getattr(record, "name", None) or default_name
            summary = getattr(record, "summary", None)
            parts.append(f"- {name}: {summary}" if summary else f"- {name}")
    elif scope == "episodes":
        for episode in items:
            content = getattr(episode, "content", None)
            if content:
                parts.append(f"- {content}")
    elif scope == "thread_summaries":
        for thread_summary in items:
            summary = getattr(thread_summary, "summary", None)
            if summary:
                parts.append(f"- {summary}")

    if parts:
        return "\n".join(parts)
    return "No results found."


def _build_constructor_only(
    search_filters: dict[str, Any] | None,
    bfs_origin_node_uuids: list[str] | None,
) -> dict[str, Any]:
    """Return the constructor-only keyword arguments of the search call."""
    constructor_only: dict[str, Any] = {}
    if search_filters is not None:
        constructor_only["filters"] = search_filters
    if bfs_origin_node_uuids is not None:
        constructor_only["bfs_origin_node_uuids"] = bfs_origin_node_uuids
    return constructor_only


def resolve_search_method(zep_client: Any, scope: str) -> Any:
    """Return the v4 graph method that serves ``scope``.

    ``auto`` scope is served by ``graph.get_context``. Every other scope has a
    dedicated ``graph.search_*`` method. An unknown scope falls back to
    ``graph.search_edges``, which is the default scope of the tool schema.
    """
    if scope == "auto":
        return zep_client.graph.get_context
    return getattr(zep_client.graph, _SCOPE_METHODS.get(scope, "search_edges"))


def _resolve_pinned_and_hidden(
    *,
    pinned_params: dict[str, Any] | None,
    hidden_params: set[str] | None,
    scope: GraphSearchScope | None,
    reranker: GraphSearchReranker | None,
    limit: int | None,
) -> tuple[dict[str, Any], set[str]]:
    """Merge explicit pin/hide args with legacy back-compat constructor args."""
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
                "Pinned graph-search limit %d exceeds Zep ceiling %d; clamping to %d",
                pinned_limit,
                MAX_SEARCH_LIMIT,
                MAX_SEARCH_LIMIT,
            )
            pinned["limit"] = MAX_SEARCH_LIMIT
        elif pinned_limit < 1:
            pinned["limit"] = 1

    # Auto scope rejects node_distance / episode_mentions and ignores reranker
    # entirely.  If scope is pinned to "auto" and reranker is also pinned,
    # resolve the effective value once, here, so the call path is always valid.
    if pinned.get("scope") == "auto" and "reranker" in pinned:
        if pinned["reranker"] in _AUTO_INCOMPATIBLE_RERANKERS:
            logger.warning(
                "Graph-search reranker %r is invalid for scope='auto'; "
                "omitting reranker (auto search uses RRF).",
                pinned["reranker"],
            )
        del pinned["reranker"]

    return pinned, hidden


def _build_search_kwargs(
    call_args: dict[str, Any],
    *,
    pinned: dict[str, Any],
    hidden: set[str],
    constructor_only: dict[str, Any],
) -> dict[str, Any]:
    """Merge pinned / model-provided / default parameters for one search call.

    A param pinned or hidden is never read from ``call_args``. A param that is
    neither pinned nor supplied by the model is omitted entirely -- in
    particular, ``mmr_lambda``/``center_node_uuid`` (whose spec default is
    ``None``) are never forwarded as an explicit ``None``, matching the
    sibling ports' ``if value is not None`` guard so Zep's own server-side
    default applies instead of an explicit null on the wire.

    A model-provided ``limit`` is clamped to ``[1, MAX_SEARCH_LIMIT]``. When
    the effective scope is ``auto``, the call goes to ``graph.get_context``,
    which takes neither a reranker nor a limit, so those parameters are
    dropped and the call never 400s on model-chosen parameters.

    The returned mapping holds ``scope`` together with the keyword arguments
    of the search call. The caller removes ``scope`` and uses it to select the
    v4 method.
    """
    query = str(call_args.get("query", ""))[:400]
    search_kwargs: dict[str, Any] = {"query": query}

    for param_name in _SEARCH_PARAM_SPECS:
        if param_name in pinned:
            search_kwargs[param_name] = pinned[param_name]
        elif param_name in hidden:
            continue  # hidden, not pinned -> omit; Zep applies its own default
        elif param_name in call_args:
            value = call_args[param_name]
            if value is not None:
                if param_name == "limit" and not (1 <= value <= MAX_SEARCH_LIMIT):
                    clamped = max(1, min(value, MAX_SEARCH_LIMIT))
                    logger.warning(
                        "Model-provided graph-search limit %d out of range [1, %d]; clamping to %d",
                        value,
                        MAX_SEARCH_LIMIT,
                        clamped,
                    )
                    value = clamped
                search_kwargs[param_name] = value

    if search_kwargs.get("scope", "edges") == "auto":
        # graph.get_context serves auto scope. It always uses RRF internally
        # and accepts neither a reranker nor a result limit.
        dropped_reranker = search_kwargs.get("reranker")
        if dropped_reranker in _AUTO_INCOMPATIBLE_RERANKERS:
            logger.warning(
                "Graph-search reranker %r is invalid for scope='auto'; omitting reranker.",
                dropped_reranker,
            )
        for param_name in _AUTO_UNSUPPORTED_PARAMS:
            search_kwargs.pop(param_name, None)
        search_kwargs.pop("bfs_origin_node_uuids", None)
        if "filters" in constructor_only:
            search_kwargs["filters"] = constructor_only["filters"]
        return search_kwargs

    search_kwargs.update(constructor_only)
    return search_kwargs


def create_graph_search_tool(
    zep_client: AsyncZep,
    *,
    graph_uuid: str,
    name: str = DEFAULT_TOOL_NAME,
    description: str = DEFAULT_TOOL_DESCRIPTION,
    pinned_params: dict[str, Any] | None = None,
    hidden_params: set[str] | None = None,
    search_filters: dict[str, Any] | None = None,
    bfs_origin_node_uuids: list[str] | None = None,
    # Back-compat: the original constructor args.  Each, if passed, pins
    # (hides) the corresponding parameter -- equivalent to putting it in
    # ``pinned_params``.
    scope: GraphSearchScope | None = None,
    reranker: GraphSearchReranker | None = None,
    limit: int | None = None,
) -> StructuredTool:
    """Create an async graph-search tool bound to one graph.

    **Pin-or-expose.** Every search parameter (``scope``,
    ``reranker``, ``limit``, ``mmr_lambda``, ``center_node_uuid``) is exposed
    to the model in the tool's schema by default, with the documented
    defaults below. Use ``pinned_params`` to fix a parameter to a constant
    value and remove it from the schema (the model can no longer choose it);
    use ``hidden_params`` to remove a parameter from the schema *without*
    pinning it -- Zep's own server-side default applies, and the parameter is
    simply omitted from the SDK call.

    ``search_filters`` and ``bfs_origin_node_uuids`` are always
    constructor-only: their complex/list-of-object shapes are not exposed to
    the model.

    Args:
        zep_client: An initialised :class:`~zep_cloud.client.AsyncZep` client.
        graph_uuid: The UUID of the graph to search. Use ``User.graph_uuid``
            for a user's personal graph, or ``Graph.uuid_`` for a shared
            standalone graph.
        name: Tool name surfaced to the model.
        description: Tool description surfaced to the model.
        pinned_params: Optional mapping of search parameter name to a
            fixed value. Pinned parameters are hidden from the model's tool
            schema and always sent with the given value.
        hidden_params: Optional set of search parameter names to hide
            from the model's tool schema without pinning them -- omitted from
            the SDK call so Zep's own default takes effect.
        search_filters: Optional :class:`~zep_cloud.types.search_filters.SearchFilters`
            to constrain results by entity/edge type, properties, or dates
            (constructor-only). Sent as the v4 ``filters`` argument.
        bfs_origin_node_uuids: Optional list of node UUIDs for BFS seeding
            (constructor-only). Ignored when the effective scope is ``auto``.
        scope: Deprecated back-compat alias for ``pinned_params={"scope": scope}``.
        reranker: Deprecated back-compat alias for ``pinned_params={"reranker": reranker}``.
        limit: Deprecated back-compat alias for ``pinned_params={"limit": limit}``.

    Returns:
        A :class:`~langchain_core.tools.StructuredTool` with an async
        implementation, ready to bind to a model or pass to
        ``create_react_agent``.

    Raises:
        ValueError: If ``pinned_params``/``hidden_params`` (or a legacy alias)
            contains an unknown parameter name.
    """
    pinned, hidden = _resolve_pinned_and_hidden(
        pinned_params=pinned_params,
        hidden_params=hidden_params,
        scope=scope,
        reranker=reranker,
        limit=limit,
    )

    constructor_only = _build_constructor_only(search_filters, bfs_origin_node_uuids)
    args_schema = _build_args_schema(pinned=pinned, hidden=hidden)

    async def _search(**kwargs: Any) -> str:
        query = str(kwargs.get("query", "")).strip()
        if not query:
            return "Error: empty search query."

        search_kwargs = _build_search_kwargs(
            kwargs,
            pinned=pinned,
            hidden=hidden,
            constructor_only=constructor_only,
        )
        effective_scope = str(search_kwargs.pop("scope", "edges"))
        search_method = resolve_search_method(zep_client, effective_scope)

        try:
            result = await search_method(graph_uuid, **search_kwargs)
        except Exception as exc:
            logger.warning("Zep graph search failed: %s", exc, exc_info=True)
            return f"Memory search failed: {exc}"

        return _format_results(result, effective_scope)

    return StructuredTool.from_function(
        coroutine=_search,
        name=name,
        description=description,
        args_schema=args_schema,
    )


def create_graph_search_tool_sync(
    zep_client: Zep,
    *,
    graph_uuid: str,
    name: str = DEFAULT_TOOL_NAME,
    description: str = DEFAULT_TOOL_DESCRIPTION,
    pinned_params: dict[str, Any] | None = None,
    hidden_params: set[str] | None = None,
    search_filters: dict[str, Any] | None = None,
    bfs_origin_node_uuids: list[str] | None = None,
    scope: GraphSearchScope | None = None,
    reranker: GraphSearchReranker | None = None,
    limit: int | None = None,
) -> StructuredTool:
    """Synchronous variant of :func:`create_graph_search_tool`.

    Uses a synchronous :class:`~zep_cloud.client.Zep` client and returns a
    :class:`~langchain_core.tools.StructuredTool` with a synchronous
    implementation. See :func:`create_graph_search_tool` for argument semantics.

    Raises:
        ValueError: If ``pinned_params``/``hidden_params`` (or a legacy alias)
            contains an unknown parameter name.
    """
    pinned, hidden = _resolve_pinned_and_hidden(
        pinned_params=pinned_params,
        hidden_params=hidden_params,
        scope=scope,
        reranker=reranker,
        limit=limit,
    )

    constructor_only = _build_constructor_only(search_filters, bfs_origin_node_uuids)
    args_schema = _build_args_schema(pinned=pinned, hidden=hidden)

    def _search(**kwargs: Any) -> str:
        query = str(kwargs.get("query", "")).strip()
        if not query:
            return "Error: empty search query."

        search_kwargs = _build_search_kwargs(
            kwargs,
            pinned=pinned,
            hidden=hidden,
            constructor_only=constructor_only,
        )
        effective_scope = str(search_kwargs.pop("scope", "edges"))
        search_method = resolve_search_method(zep_client, effective_scope)

        try:
            result = search_method(graph_uuid, **search_kwargs)
        except Exception as exc:
            logger.warning("Zep graph search failed: %s", exc, exc_info=True)
            return f"Memory search failed: {exc}"

        return _format_results(result, effective_scope)

    return StructuredTool.from_function(
        func=_search,
        name=name,
        description=description,
        args_schema=args_schema,
    )
