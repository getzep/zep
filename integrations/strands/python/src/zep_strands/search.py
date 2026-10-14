"""A Strands Agents tool for searching a Zep knowledge graph on demand.

:class:`~zep_strands.memory_store.ZepMemoryStore` injects recalled context via
the ``MemoryManager`` search/injection path.  This module provides the
complementary *pull* path: a model-callable tool that lets the agent decide
when to search the graph for specific facts, entities, or prior episodes.

:func:`create_zep_search_tool` returns a Strands ``@tool``-decorated
:class:`~strands.types.tools.AgentTool`.  Strands derives the tool schema from
the wrapped function's typed signature (there is no raw-JSON-schema
constructor), so pin-or-expose works by *dynamically building the wrapped
function's signature*: exposed parameters become real, typed parameters of
the function Strands introspects, while pinned/hidden parameters are never
parameters of the function at all. The tool maps those values to the matching
V4 ``graph.search_*`` method or to ``graph.get_context`` for ``auto``.
"""

from __future__ import annotations

import inspect
import logging
from typing import Annotated, Any, Literal

from strands import tool
from strands.types.tools import AgentTool
from zep_cloud.client import AsyncZep
from zep_cloud.types.edge_search_filters import EdgeSearchFilters
from zep_cloud.types.episode_search_filters import EpisodeSearchFilters
from zep_cloud.types.node_search_filters import NodeSearchFilters
from zep_cloud.types.search_filters import SearchFilters
from zep_cloud.types.thread_summary_search_filters import ThreadSummarySearchFilters

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

#: Zep caps scoped search page sizes at 50; larger values are rejected.
MAX_SEARCH_LIMIT = 50

#: Rerankers Zep rejects when ``scope == "auto"`` (auto always uses RRF
#: retrieval and applies its own internal cross-scope rerank).
_AUTO_INCOMPATIBLE_RERANKERS = ("node_distance", "episode_mentions")

#: The type of tool ``create_zep_search_tool`` returns.
ZepSearchTool = AgentTool

# ---------------------------------------------------------------------------
# Parameter definitions
# ---------------------------------------------------------------------------
# Each entry describes a V4 search parameter that can be pinned or exposed
# to the model. Exposed values are translated to V4 request arguments. Model-
# exposed by default; hidden only when pinned or explicitly listed in
# ``search_hidden_params``. ``annotation`` is the real typed annotation used
# to build the dynamic function signature Strands introspects.

_SEARCH_PARAM_SPECS: dict[str, dict[str, Any]] = {
    "scope": {
        "annotation": Scope,
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
        "annotation": Reranker,
        "description": (
            "Result ordering algorithm: 'rrf' (balanced), 'mmr' (diverse), "
            "'cross_encoder' (highest accuracy), 'episode_mentions' "
            "(frequently referenced), 'node_distance' (near a specific entity)."
        ),
        "default": "rrf",
    },
    "limit": {
        "annotation": int,
        "description": "Maximum number of results to return (up to 50).",
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


def _name_summary_text(name: str | None, summary: str | None) -> str:
    """Join a name and summary as ``"name: summary"``, falling back gracefully."""
    if name and summary:
        return f"{name}: {summary}"
    if name:
        return name
    if summary:
        return summary
    return ""


def _filters_for_scope(scope: str, filters: dict[str, Any] | None) -> Any:
    if filters is None:
        return None
    filter_types: dict[str, Any] = {
        "edges": EdgeSearchFilters,
        "nodes": NodeSearchFilters,
        "episodes": EpisodeSearchFilters,
        "observations": dict,
        "thread_summaries": ThreadSummarySearchFilters,
        "auto": SearchFilters,
    }
    filter_type = filter_types[scope]
    return dict(filters) if filter_type is dict else filter_type(**filters)


async def _search_graph(
    client: AsyncZep,
    graph_uuid: str,
    query: str,
    *,
    scope: str,
    limit: int | None,
    reranker: str | None = None,
    filters: dict[str, Any] | None = None,
    bfs_origin_node_uuids: list[str] | None = None,
    mmr_lambda: float | None = None,
    center_node_uuid: str | None = None,
) -> Any:
    if scope == "auto":
        context_kwargs: dict[str, Any] = {"query": query}
        if filters is not None:
            context_kwargs["filters"] = _filters_for_scope(scope, filters)
        return await client.graph.get_context(graph_uuid, **context_kwargs)

    kwargs: dict[str, Any] = {"query": query}
    if limit is not None:
        kwargs["page_size"] = limit
    if reranker is not None:
        kwargs["reranker"] = reranker
    if filters is not None:
        kwargs["filters"] = _filters_for_scope(scope, filters)
    if bfs_origin_node_uuids is not None:
        kwargs["bfs_origin_node_uuids"] = bfs_origin_node_uuids
    if mmr_lambda is not None:
        kwargs["mmr_lambda"] = mmr_lambda
    if center_node_uuid is not None:
        kwargs["center_node_uuid"] = center_node_uuid
    return await getattr(client.graph, f"search_{scope}")(graph_uuid, **kwargs)


def _format_results(result: Any, scope: str) -> str:
    """Render Zep search results as readable text for the model."""
    if scope == "auto":
        context = getattr(result, "context", None)
        if context and str(context).strip():
            return str(context).strip()
        return "No results found."

    parts: list[str] = []
    items = getattr(result, "items", None) or []
    if scope == "edges":
        parts = [f"- {edge.fact}" for edge in items if getattr(edge, "fact", None)]
    elif scope == "nodes":
        for node in items:
            text = _name_summary_text(getattr(node, "name", None), getattr(node, "summary", None))
            if text:
                parts.append(f"- {text}")
    elif scope == "episodes":
        parts = [f"- {ep.content}" for ep in items if getattr(ep, "content", None)]
    elif scope == "observations":
        for obs in items:
            text = _name_summary_text(getattr(obs, "name", None), getattr(obs, "summary", None))
            if text:
                parts.append(f"- {text}")
    elif scope == "thread_summaries":
        for ts in items:
            summary = getattr(ts, "summary", None)
            summary_text = summary or getattr(ts, "name", None)
            if summary_text:
                parts.append(f"- {summary_text}")

    return "\n".join(parts) if parts else "No results found."


def _build_search_signature(
    exposed: dict[str, dict[str, Any]],
) -> tuple[inspect.Signature, dict[str, Any]]:
    """Build the typed signature Strands ``@tool`` will introspect.

    ``query`` is always present and required; ``exposed`` params (those not
    pinned or hidden) become real, defaulted parameters annotated with
    ``Annotated[<type>, <description>]`` so schema generation picks up both
    the type/enum and the description.
    """
    params: list[inspect.Parameter] = [
        inspect.Parameter(
            "query",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=Annotated[str, "Search query text (max 400 characters)."],
        )
    ]
    for name, spec in exposed.items():
        base_type = spec["annotation"]
        param_description = spec["description"]
        params.append(
            inspect.Parameter(
                name,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=spec["default"],
                annotation=Annotated[base_type, param_description],
            )
        )
    signature = inspect.Signature(params)
    annotations = {p.name: p.annotation for p in params}
    return signature, annotations


def create_zep_search_tool(
    *,
    zep_client: AsyncZep,
    user_uuid: str | None = None,
    graph_uuid: str | None = None,
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
    """Build a Strands tool that searches a Zep knowledge graph.

    Register the returned tool with an agent, or return it from
    :meth:`~zep_strands.memory_store.ZepMemoryStore.get_tools`::

        from zep_strands import create_zep_search_tool

        tool = create_zep_search_tool(zep_client=zep, user_uuid="user-123")
        agent = Agent(tools=[tool])

    By default the tool searches the **given user's** graph (``user_uuid``
    fixed at construction time). Pass ``graph_uuid`` to target a shared
    standalone graph instead; provide exactly one of ``user_uuid`` / ``graph_uuid``.

    **Pin-or-expose.** Every V4 search parameter (``scope``,
    ``reranker``, ``limit``, ``mmr_lambda``, ``center_node_uuid``) is exposed
    to the model in the tool's schema by default. Use
    ``search_pinned_params`` to fix a parameter to a constant value and remove
    it from the schema; use ``search_hidden_params`` to remove a parameter
    without pinning it -- Zep's own server-side default applies.

    ``search_filters`` and ``bfs_origin_node_uuids`` are always
    constructor-only.

    Args:
        zep_client: An initialised ``AsyncZep`` client.
        user_uuid: The Zep user ID whose graph is searched. Required unless
            ``graph_uuid`` is set.
        graph_uuid: Optional standalone graph ID. When set, all searches target
            this graph; mutually exclusive with ``user_uuid``.
        search_pinned_params: Optional mapping of V4 search parameter
            name to a fixed value. Pinned parameters are hidden from the
            model's tool schema and always sent with the given value.
        search_hidden_params: Optional set of V4 search parameter
            names to hide from the model's tool schema without pinning them.
        search_filters: Optional Zep search filters (constructor-only).
        bfs_origin_node_uuids: Optional list of node UUIDs for BFS seeding
            (constructor-only).
        name: The tool name exposed to the model. Defaults to ``"zep_search"``.
        description: The tool description exposed to the model.

    Returns:
        A Strands ``AgentTool``. The tool calls the V4 method for the selected
        scope. Zep failures are caught and returned as an error string.

    Raises:
        ValueError: If neither or both of ``user_uuid``/``graph_uuid`` are
            provided, or ``search_pinned_params``/``search_hidden_params``
            contains an unknown parameter name.
    """
    if not user_uuid and not graph_uuid:
        raise ValueError("Either user_uuid or graph_uuid must be provided when creating the tool")
    if user_uuid and graph_uuid:
        raise ValueError(
            "Only one of user_uuid or graph_uuid should be provided when creating the tool"
        )

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

    if pinned.get("scope") == "auto" and "reranker" in pinned:
        if pinned["reranker"] in _AUTO_INCOMPATIBLE_RERANKERS:
            logger.warning(
                "zep_search reranker %r is invalid for scope='auto'; "
                "omitting reranker (auto search uses RRF).",
                pinned["reranker"],
            )
        del pinned["reranker"]
        hidden.add("reranker")

    exposed = {
        param_name: spec
        for param_name, spec in _SEARCH_PARAM_SPECS.items()
        if param_name not in pinned and param_name not in hidden
    }
    signature, annotations = _build_search_signature(exposed)
    resolved_graph_uuid = graph_uuid

    async def zep_search(*args: Any, **kwargs: Any) -> str:
        nonlocal resolved_graph_uuid
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        call_args = dict(bound.arguments)

        query = str(call_args.pop("query", ""))[:400]
        search_kwargs: dict[str, Any] = {"query": query}

        for param_name in _SEARCH_PARAM_SPECS:
            if param_name in pinned:
                search_kwargs[param_name] = pinned[param_name]
            elif param_name in hidden:
                continue
            elif param_name in call_args:
                value = call_args[param_name]
                # Never forward explicit None -- omit so Zep's default applies.
                if value is not None:
                    search_kwargs[param_name] = value

        if "limit" in search_kwargs:
            search_kwargs["limit"] = min(max(int(search_kwargs["limit"]), 1), MAX_SEARCH_LIMIT)

        if not search_kwargs.get("query"):
            return "Error: No search query provided."

        try:
            if resolved_graph_uuid is None:
                user = await zep_client.user.get(user_uuid)
                resolved_graph_uuid = user.graph_uuid
                if not resolved_graph_uuid:
                    raise RuntimeError("Zep returned a user without a graph UUID")
            effective_scope = str(search_kwargs.get("scope", "edges"))
            results = await _search_graph(
                zep_client,
                resolved_graph_uuid,
                str(search_kwargs["query"]),
                scope=effective_scope,
                limit=(
                    min(max(int(search_kwargs["limit"]), 1), MAX_SEARCH_LIMIT)
                    if "limit" in search_kwargs
                    else None
                ),
                reranker=search_kwargs.get("reranker"),
                filters=search_filters,
                bfs_origin_node_uuids=bfs_origin_node_uuids,
                mmr_lambda=search_kwargs.get("mmr_lambda"),
                center_node_uuid=search_kwargs.get("center_node_uuid"),
            )
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            if status is not None:
                logger.warning(
                    "Zep graph search failed type=%s status=%s",
                    type(exc).__name__,
                    status,
                )
            else:
                logger.warning("Zep graph search failed type=%s", type(exc).__name__)
            logger.debug("Zep graph search failed", exc_info=True)
            return "Graph search failed."

        return _format_results(results, str(effective_scope))

    zep_search.__signature__ = signature  # type: ignore[attr-defined]
    zep_search.__annotations__ = annotations
    zep_search.__name__ = name
    zep_search.__doc__ = description

    # Runtime ``tool`` accepts ``func, name=, description=``; the published
    # overloads only type the decorator form, so cast the result.
    decorated = tool(name=name, description=description)(zep_search)
    return decorated  # type: ignore[no-any-return]
