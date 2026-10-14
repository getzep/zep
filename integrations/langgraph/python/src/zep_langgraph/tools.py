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
from collections.abc import Sequence
from typing import Any, Literal, get_args

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, ValidationError, create_model
from zep_cloud.client import AsyncZep, Zep
from zep_cloud.types import (
    EdgeSearchFilters,
    EpisodeSearchFilters,
    NodeSearchFilters,
    ObservationSearchFilters,
    SearchFilters,
    ThreadSummarySearchFilters,
)

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
GraphSearchFilters = (
    EdgeSearchFilters
    | NodeSearchFilters
    | EpisodeSearchFilters
    | ObservationSearchFilters
    | ThreadSummarySearchFilters
    | SearchFilters
    | dict[str, Any]
)

#: Zep caps the graph-search ``page_size`` at 50; larger values are rejected.
MAX_SEARCH_LIMIT = 50

#: Every scope and every reranker, in schema order.
_ALL_SCOPES: tuple[str, ...] = get_args(GraphSearchScope)
_ALL_RERANKERS: tuple[str, ...] = get_args(GraphSearchReranker)

#: The rerankers that the v4 search method of each scope accepts. Zep rejects
#: any other pair with HTTP 400. ``auto`` calls ``graph.get_context``, which
#: takes no reranker.
_SCOPE_RERANKERS: dict[str, tuple[str, ...]] = {
    "edges": ("rrf", "mmr", "node_distance", "episode_mentions", "cross_encoder"),
    "nodes": ("rrf", "mmr", "node_distance", "cross_encoder"),
    "episodes": ("rrf", "mmr"),
    "observations": ("rrf", "mmr", "cross_encoder"),
    "thread_summaries": ("rrf", "mmr", "cross_encoder"),
    "auto": (),
}

#: The ``mmr_lambda`` sent when the reranker is ``mmr`` and no value is set.
#: Zep requires ``mmr_lambda`` for ``mmr``.
DEFAULT_MMR_LAMBDA = 0.5

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
    "stored in the graph. The input is a natural-language search query. Each "
    "scope accepts only some rerankers; the reranker parameter lists them."
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
        "description": "Result ordering algorithm.",
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
            "Balance between diversity (0.0) and relevance (1.0). Only used when "
            f"reranker is 'mmr'. Defaults to {DEFAULT_MMR_LAMBDA}."
        ),
        "default": None,
    },
    "center_node_uuid": {
        "annotation": str | None,
        "description": (
            "UUID of the node to rank results by distance from. "
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


_RERANKER_LABELS: dict[str, str] = {
    "rrf": "'rrf' (balanced)",
    "mmr": "'mmr' (diverse)",
    "node_distance": "'node_distance' (near the center node)",
    "episode_mentions": "'episode_mentions' (frequently referenced)",
    "cross_encoder": "'cross_encoder' (highest accuracy)",
}


def _literal(values: Sequence[str]) -> Any:
    """Return a ``Literal`` type of ``values`` for the dynamic args schema."""
    return Literal.__getitem__(tuple(values))  # type: ignore[attr-defined]


def _filters_have_episode_uuids(filters: Any) -> bool:
    """Return whether the constructor-only filters set ``episode_uuids``."""
    if filters is None:
        return False
    if isinstance(filters, dict):
        return bool(filters.get("episode_uuids"))
    if isinstance(filters, EdgeSearchFilters | NodeSearchFilters | SearchFilters):
        return bool(filters.episode_uuids)
    return False


def _model_rerankers(
    *,
    pinned: dict[str, Any],
    hidden: set[str],
    constructor_only: dict[str, Any],
) -> tuple[str, ...]:
    """Return the rerankers that the model can use correctly.

    The list holds only the rerankers that the pinned scope accepts, or that
    some scope accepts when the model selects the scope. ``node_distance``
    needs a ``center_node_uuid``, and the search results do not show node
    UUIDs, so it is available only when ``center_node_uuid`` is pinned.
    ``episode_mentions`` needs ``episode_uuids`` in ``search_filters``, which
    only the developer can set.
    """
    if "reranker" in pinned:
        return (str(pinned["reranker"]),)
    scope = pinned.get("scope", "edges" if "scope" in hidden else None)
    scopes = [scope] if scope is not None else list(_ALL_SCOPES)
    candidates = {reranker for name in scopes for reranker in _SCOPE_RERANKERS[name]}
    if "center_node_uuid" not in pinned:
        candidates.discard("node_distance")
    if not _filters_have_episode_uuids(constructor_only.get("filters")):
        candidates.discard("episode_mentions")
    return tuple(reranker for reranker in _ALL_RERANKERS if reranker in candidates)


def _reranker_description(rerankers: Sequence[str], *, scope_exposed: bool) -> str:
    """Describe the reranker options, and the limits of each scope."""
    text = "Result ordering algorithm: " + ", ".join(_RERANKER_LABELS[r] for r in rerankers) + "."
    if scope_exposed:
        limits = [
            f"{name}: {', '.join(r for r in _SCOPE_RERANKERS[name] if r in rerankers)}"
            for name in _ALL_SCOPES
            if name != "auto"
        ]
        text += (
            " Each scope accepts only these rerankers: "
            + "; ".join(limits)
            + ". Scope 'auto' uses no reranker."
        )
    if "mmr" in rerankers:
        text += " 'mmr' uses mmr_lambda."
    return text


def _build_args_schema(
    *,
    pinned: dict[str, Any],
    hidden: set[str],
    constructor_only: dict[str, Any],
) -> type[BaseModel]:
    """Build the pydantic model ``StructuredTool`` uses as its ``args_schema``.

    ``query`` is always present and required. Params in ``_SEARCH_PARAM_SPECS``
    that are neither pinned nor hidden become model-visible fields with their
    documented default. The ``reranker`` field lists only the rerankers that
    the model can use correctly, and the ``scope`` field lists only the scopes
    that accept a pinned reranker. A field that no available option uses is
    not shown: the reranker parameters for a pinned ``auto`` scope,
    ``mmr_lambda`` when ``mmr`` is not available, and ``center_node_uuid``
    unless the reranker is pinned to ``node_distance``.
    """
    rerankers = _model_rerankers(pinned=pinned, hidden=hidden, constructor_only=constructor_only)
    pinned_reranker = pinned.get("reranker")
    pinned_scope = pinned.get("scope")

    omitted: set[str] = set()
    if pinned_scope == "auto":
        omitted.update(_AUTO_UNSUPPORTED_PARAMS)
    if not rerankers:
        omitted.add("reranker")
    if "mmr" not in rerankers and pinned_reranker != "mmr":
        omitted.add("mmr_lambda")
    if pinned_reranker != "node_distance":
        omitted.add("center_node_uuid")

    fields: dict[str, Any] = {
        "query": (str, Field(description="Natural-language search query (max 400 characters)."))
    }
    for param_name, spec in _SEARCH_PARAM_SPECS.items():
        if param_name in pinned or param_name in hidden or param_name in omitted:
            continue  # pinned, hidden, or unused -> not exposed to the model
        annotation = spec["annotation"]
        default = spec["default"]
        description = spec["description"]
        if param_name == "scope" and pinned_reranker is not None:
            scopes = [
                name
                for name in _ALL_SCOPES
                if name == "auto" or pinned_reranker in _SCOPE_RERANKERS[name]
            ]
            annotation = _literal(scopes)
            if default not in scopes:
                default = scopes[0]
        elif param_name == "reranker":
            annotation = _literal(rerankers)
            if default not in rerankers:
                default = rerankers[0]
            description = _reranker_description(
                rerankers, scope_exposed="scope" not in pinned and "scope" not in hidden
            )
        elif param_name == "center_node_uuid":
            fields[param_name] = (str, Field(description=description))
            continue
        fields[param_name] = (annotation, Field(default=default, description=description))
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
    search_filters: GraphSearchFilters | None,
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

    return pinned, hidden


def _validate_pinned(
    pinned: dict[str, Any],
    hidden: set[str],
    constructor_only: dict[str, Any],
) -> None:
    """Reject a pinned scope and reranker setting that Zep rejects.

    Raises:
        ValueError: If the pinned scope or reranker is unknown, the pinned
            reranker is not valid for the pinned scope, ``node_distance`` has
            no way to get a ``center_node_uuid``, or ``episode_mentions`` has
            no ``episode_uuids`` in ``search_filters``.
    """
    scope = pinned.get("scope")
    reranker = pinned.get("reranker")
    if scope is not None and scope not in _ALL_SCOPES:
        raise ValueError(f"Unknown graph-search scope {scope!r}. Allowed: {list(_ALL_SCOPES)}")
    if reranker is None:
        return
    if reranker not in _ALL_RERANKERS:
        raise ValueError(
            f"Unknown graph-search reranker {reranker!r}. Allowed: {list(_ALL_RERANKERS)}"
        )
    if scope == "auto":
        raise ValueError(
            "Scope 'auto' calls graph.get_context, which takes no reranker. "
            "Remove the pinned reranker, or pin another scope."
        )
    if scope is not None and reranker not in _SCOPE_RERANKERS[scope]:
        raise ValueError(
            f"Reranker {reranker!r} is not valid for scope {scope!r}. "
            f"Use one of: {list(_SCOPE_RERANKERS[scope])}."
        )
    if reranker == "node_distance" and "center_node_uuid" not in pinned:
        if "center_node_uuid" in hidden:
            raise ValueError(
                "Reranker 'node_distance' requires center_node_uuid. Pin "
                "center_node_uuid, or do not hide it."
            )
    if reranker == "episode_mentions" and not _filters_have_episode_uuids(
        constructor_only.get("filters")
    ):
        raise ValueError("Reranker 'episode_mentions' requires search_filters with episode_uuids.")


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

    A model-provided ``limit`` is clamped to ``[1, MAX_SEARCH_LIMIT]`` and is
    sent to the v4 search method as ``page_size``. When
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
        for param_name in _AUTO_UNSUPPORTED_PARAMS:
            search_kwargs.pop(param_name, None)
        search_kwargs.pop("bfs_origin_node_uuids", None)
        if "filters" in constructor_only:
            search_kwargs["filters"] = constructor_only["filters"]
        return search_kwargs

    scope = str(search_kwargs.get("scope", "edges"))
    reranker = search_kwargs.get("reranker")
    if reranker is not None and reranker not in _SCOPE_RERANKERS.get(scope, _ALL_RERANKERS):
        logger.warning(
            "Graph-search reranker %r is invalid for scope=%r; omitting reranker.",
            reranker,
            scope,
        )
        search_kwargs.pop("reranker")
        reranker = None
    if reranker == "mmr" and search_kwargs.get("mmr_lambda") is None:
        search_kwargs["mmr_lambda"] = DEFAULT_MMR_LAMBDA
    if "limit" in search_kwargs:
        search_kwargs["page_size"] = search_kwargs.pop("limit")
    search_kwargs.update(constructor_only)
    return search_kwargs


def _validation_error_message(exc: ValidationError) -> str:
    """Return a tool result that names each invalid argument and its fix."""
    problems = "; ".join(
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}" for error in exc.errors()
    )
    return f"Error: invalid search arguments. {problems}. Correct the arguments and search again."


def _reranker_error(search_kwargs: dict[str, Any]) -> str | None:
    """Return an error message for the model if the reranker cannot run.

    ``node_distance`` without ``center_node_uuid`` and ``episode_mentions``
    without ``episode_uuids`` in the filters fail with HTTP 400. The message
    names the setting and the rerankers to use in its place, so the model can
    try again.
    """
    reranker = search_kwargs.get("reranker")
    scope = str(search_kwargs.get("scope", "edges"))
    alternatives = ", ".join(
        repr(r) for r in _SCOPE_RERANKERS.get(scope, ()) if r in ("rrf", "mmr", "cross_encoder")
    )
    if reranker == "node_distance" and not search_kwargs.get("center_node_uuid"):
        return (
            "Error: reranker 'node_distance' requires center_node_uuid. Give "
            f"center_node_uuid, or use one of these rerankers: {alternatives}."
        )
    if reranker == "episode_mentions" and not _filters_have_episode_uuids(
        search_kwargs.get("filters")
    ):
        return (
            "Error: reranker 'episode_mentions' is not available for this tool. "
            f"Use one of these rerankers: {alternatives}."
        )
    return None


def create_graph_search_tool(
    zep_client: AsyncZep,
    *,
    graph_uuid: str,
    name: str = DEFAULT_TOOL_NAME,
    description: str = DEFAULT_TOOL_DESCRIPTION,
    pinned_params: dict[str, Any] | None = None,
    hidden_params: set[str] | None = None,
    search_filters: GraphSearchFilters | None = None,
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

    **Valid search settings.** The schema shows only the rerankers that the
    selected scope accepts. ``node_distance`` is shown only when
    ``center_node_uuid`` is pinned, and ``episode_mentions`` only when
    ``search_filters`` sets ``episode_uuids``. If the model sends a reranker
    that its scope does not accept, the tool omits the reranker. If the
    reranker is ``mmr`` and no ``mmr_lambda`` is set, the tool sends
    ``DEFAULT_MMR_LAMBDA``.

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
        search_filters: Optional filters for the selected scope
            (constructor-only), sent as the v4 ``filters`` argument. Each
            scope has its own filter type: ``EdgeSearchFilters``,
            ``NodeSearchFilters``, ``EpisodeSearchFilters``,
            ``ObservationSearchFilters``, ``ThreadSummarySearchFilters``, and
            ``SearchFilters`` for ``auto``. Zep rejects a key that the scope
            cannot apply.
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
            contains an unknown parameter name, or if the pinned scope and
            reranker are a setting that Zep rejects.
    """
    pinned, hidden = _resolve_pinned_and_hidden(
        pinned_params=pinned_params,
        hidden_params=hidden_params,
        scope=scope,
        reranker=reranker,
        limit=limit,
    )

    constructor_only = _build_constructor_only(search_filters, bfs_origin_node_uuids)
    _validate_pinned(pinned, hidden, constructor_only)
    args_schema = _build_args_schema(
        pinned=pinned, hidden=hidden, constructor_only=constructor_only
    )

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
        error = _reranker_error(search_kwargs)
        if error is not None:
            return error
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
        handle_validation_error=_validation_error_message,
    )


def create_graph_search_tool_sync(
    zep_client: Zep,
    *,
    graph_uuid: str,
    name: str = DEFAULT_TOOL_NAME,
    description: str = DEFAULT_TOOL_DESCRIPTION,
    pinned_params: dict[str, Any] | None = None,
    hidden_params: set[str] | None = None,
    search_filters: GraphSearchFilters | None = None,
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
            contains an unknown parameter name, or if the pinned scope and
            reranker are a setting that Zep rejects.
    """
    pinned, hidden = _resolve_pinned_and_hidden(
        pinned_params=pinned_params,
        hidden_params=hidden_params,
        scope=scope,
        reranker=reranker,
        limit=limit,
    )

    constructor_only = _build_constructor_only(search_filters, bfs_origin_node_uuids)
    _validate_pinned(pinned, hidden, constructor_only)
    args_schema = _build_args_schema(
        pinned=pinned, hidden=hidden, constructor_only=constructor_only
    )

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
        error = _reranker_error(search_kwargs)
        if error is not None:
            return error
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
        handle_validation_error=_validation_error_message,
    )
