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

``create_graph_search_tool`` (BREAKING in this version -- see the CHANGELOG)
follows the pin-or-expose pattern shared by the other Zep framework
integrations: every ``graph.search`` parameter (``scope``, ``reranker``,
``limit``, ``mmr_lambda``, ``center_node_uuid``) is exposed to the model by
default and can be pinned (fixed to a constant, hidden from the model) or
hidden (removed from the schema without pinning; Zep's own default applies)
at construction time. The exposed schema is built dynamically with
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

#: Zep caps ``graph.search`` ``limit`` at 50; larger values are rejected.
MAX_SEARCH_LIMIT = 50

#: Rerankers Zep rejects when ``scope == "auto"`` (auto always uses RRF
#: retrieval and applies its own internal cross-scope rerank).
_AUTO_INCOMPATIBLE_RERANKERS = ("node_distance", "episode_mentions")

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
# Each entry describes a graph.search parameter that can be pinned or exposed
# to the model.  Keys match the Zep SDK's ``graph.search()`` kwargs.  Model-
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
_CONSTRUCTOR_ONLY_PARAMS = frozenset({"search_filters", "bfs_origin_node_uuids"})

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
    elif scope == "observations":
        for observation in getattr(result, "observations", None) or []:
            name = getattr(observation, "name", None) or "Observation"
            summary = getattr(observation, "summary", None)
            parts.append(f"- {name}: {summary}" if summary else f"- {name}")
    elif scope == "thread_summaries":
        for thread_summary in getattr(result, "thread_summaries", None) or []:
            summary = getattr(thread_summary, "summary", None)
            text = summary or getattr(thread_summary, "name", None)
            if text:
                parts.append(f"- {text}")

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
    target: dict[str, str],
    constructor_only: dict[str, Any],
) -> dict[str, Any]:
    """Merge pinned / model-provided / default parameters for one search call.

    A param pinned or hidden is never read from ``call_args``. A param that is
    neither pinned nor supplied by the model is omitted entirely -- in
    particular, ``mmr_lambda``/``center_node_uuid`` (whose spec default is
    ``None``) are never forwarded as an explicit ``None``, matching the
    sibling ports' ``if value is not None`` guard so Zep's own server-side
    default applies instead of an explicit null on the wire.

    A model-provided ``limit`` is clamped to ``[1, MAX_SEARCH_LIMIT]``, and a
    reranker is dropped when the effective scope is ``auto`` (auto search
    always uses RRF internally; Zep rejects
    ``node_distance``/``episode_mentions`` outright), so the call never 400s
    on model-chosen parameters.
    """
    query = str(call_args.get("query", ""))[:400]
    search_kwargs: dict[str, Any] = {"query": query, **target}

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

    if search_kwargs.get("scope") == "auto" and "reranker" in search_kwargs:
        # Auto search always uses RRF internally and ignores reranker
        # entirely; Zep rejects node_distance/episode_mentions outright.
        # Warn only when the (would-be) reranker is one Zep would reject.
        dropped_reranker = search_kwargs.pop("reranker")
        if dropped_reranker in _AUTO_INCOMPATIBLE_RERANKERS:
            logger.warning(
                "Graph-search reranker %r is invalid for scope='auto'; omitting reranker.",
                dropped_reranker,
            )

    search_kwargs.update(constructor_only)
    return search_kwargs


def create_graph_search_tool(
    zep_client: AsyncZep,
    *,
    user_id: str | None = None,
    graph_id: str | None = None,
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
    """Create an async graph-search tool bound to a user or standalone graph.

    **Pin-or-expose.** Every ``graph.search`` parameter (``scope``,
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
        user_id: The Zep user whose personal graph to search. Mutually exclusive
            with ``graph_id``.
        graph_id: The standalone graph to search. Mutually exclusive with
            ``user_id``.
        name: Tool name surfaced to the model.
        description: Tool description surfaced to the model.
        pinned_params: Optional mapping of ``graph.search`` parameter name to a
            fixed value. Pinned parameters are hidden from the model's tool
            schema and always sent with the given value.
        hidden_params: Optional set of ``graph.search`` parameter names to hide
            from the model's tool schema without pinning them -- omitted from
            the SDK call so Zep's own default takes effect.
        search_filters: Optional :class:`~zep_cloud.types.search_filters.SearchFilters`
            to constrain results by entity/edge type, properties, or dates
            (constructor-only).
        bfs_origin_node_uuids: Optional list of node UUIDs for BFS seeding
            (constructor-only).
        scope: Deprecated back-compat alias for ``pinned_params={"scope": scope}``.
        reranker: Deprecated back-compat alias for ``pinned_params={"reranker": reranker}``.
        limit: Deprecated back-compat alias for ``pinned_params={"limit": limit}``.

    Returns:
        A :class:`~langchain_core.tools.StructuredTool` with an async
        implementation, ready to bind to a model or pass to
        ``create_react_agent``.

    Raises:
        ValueError: If neither or both of ``user_id`` / ``graph_id`` are given,
            or ``pinned_params``/``hidden_params`` (or a legacy alias) contains
            an unknown parameter name.
    """
    target = _resolve_target(user_id, graph_id)
    pinned, hidden = _resolve_pinned_and_hidden(
        pinned_params=pinned_params,
        hidden_params=hidden_params,
        scope=scope,
        reranker=reranker,
        limit=limit,
    )

    constructor_only: dict[str, Any] = {}
    if search_filters is not None:
        constructor_only["search_filters"] = search_filters
    if bfs_origin_node_uuids is not None:
        constructor_only["bfs_origin_node_uuids"] = bfs_origin_node_uuids

    args_schema = _build_args_schema(pinned=pinned, hidden=hidden)

    async def _search(**kwargs: Any) -> str:
        query = str(kwargs.get("query", "")).strip()
        if not query:
            return "Error: empty search query."

        search_kwargs = _build_search_kwargs(
            kwargs,
            pinned=pinned,
            hidden=hidden,
            target=target,
            constructor_only=constructor_only,
        )

        try:
            result = await zep_client.graph.search(**search_kwargs)
        except Exception as exc:
            logger.warning("Zep graph search failed: %s", exc, exc_info=True)
            return f"Memory search failed: {exc}"

        effective_scope = str(search_kwargs.get("scope", "edges"))
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
    user_id: str | None = None,
    graph_id: str | None = None,
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
        ValueError: If neither or both of ``user_id`` / ``graph_id`` are given,
            or ``pinned_params``/``hidden_params`` (or a legacy alias) contains
            an unknown parameter name.
    """
    target = _resolve_target(user_id, graph_id)
    pinned, hidden = _resolve_pinned_and_hidden(
        pinned_params=pinned_params,
        hidden_params=hidden_params,
        scope=scope,
        reranker=reranker,
        limit=limit,
    )

    constructor_only: dict[str, Any] = {}
    if search_filters is not None:
        constructor_only["search_filters"] = search_filters
    if bfs_origin_node_uuids is not None:
        constructor_only["bfs_origin_node_uuids"] = bfs_origin_node_uuids

    args_schema = _build_args_schema(pinned=pinned, hidden=hidden)

    def _search(**kwargs: Any) -> str:
        query = str(kwargs.get("query", "")).strip()
        if not query:
            return "Error: empty search query."

        search_kwargs = _build_search_kwargs(
            kwargs,
            pinned=pinned,
            hidden=hidden,
            target=target,
            constructor_only=constructor_only,
        )

        try:
            result = zep_client.graph.search(**search_kwargs)
        except Exception as exc:
            logger.warning("Zep graph search failed: %s", exc, exc_info=True)
            return f"Memory search failed: {exc}"

        effective_scope = str(search_kwargs.get("scope", "edges"))
        return _format_results(result, effective_scope)

    return StructuredTool.from_function(
        func=_search,
        name=name,
        description=description,
        args_schema=args_schema,
    )
