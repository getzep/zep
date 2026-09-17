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
parameters of the function at all -- they are merged in as constants (or
omitted) when the tool actually calls the Zep search method.

Zep v4 has one method for each scope: ``graph.search_edges``,
``graph.search_nodes``, ``graph.search_episodes``,
``graph.search_observations``, and ``graph.search_thread_summaries``. The
``auto`` scope maps onto ``graph.get_context``, which returns the assembled
Context Block. :func:`run_graph_search` dispatches on the scope and gives one
result shape back to both callers.
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from strands import tool
from strands.types.tools import AgentTool
from zep_cloud.client import AsyncZep

from ._graph import GraphUuidResolver

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

#: Rerankers Zep rejects when ``scope == "auto"`` (auto always uses RRF
#: retrieval and applies its own internal cross-scope rerank).
_AUTO_INCOMPATIBLE_RERANKERS = ("node_distance", "episode_mentions")

#: The type of tool ``create_zep_search_tool`` returns.
ZepSearchTool = AgentTool

# ---------------------------------------------------------------------------
# Parameter definitions
# ---------------------------------------------------------------------------
# Each entry describes a search parameter that can be pinned or exposed
# to the model.  Keys match the Zep SDK's search kwargs.  Model-
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


@dataclass
class SearchResults:
    """The result of one Zep search, in the shape both callers need.

    Attributes:
        scope: The scope that produced the result.
        context: The assembled Context Block for ``scope="auto"``.
        items: The typed results of a scoped search (edges, nodes, episodes,
            observations, or thread summaries).
    """

    scope: str
    context: str | None = None
    items: list[Any] = field(default_factory=list)


async def run_graph_search(
    client: AsyncZep,
    *,
    graph_uuid: str,
    scope: str,
    query: str,
    limit: int | None = None,
    reranker: str | None = None,
    mmr_lambda: float | None = None,
    center_node_uuid: str | None = None,
    filters: dict[str, Any] | None = None,
    bfs_origin_node_uuids: list[str] | None = None,
) -> SearchResults:
    """Search one Zep graph with the v4 method that matches ``scope``.

    ``scope="auto"`` calls ``graph.get_context`` and returns the Context
    Block. Every other scope calls the search method of that scope and
    returns the first page of results.

    Args:
        client: An initialised ``AsyncZep`` client.
        graph_uuid: The UUID of the graph to search.
        scope: One of the values of :data:`Scope`.
        query: The search query text.
        limit: The maximum number of results for a scoped search.
        reranker: The reranker for a scoped search.
        mmr_lambda: The MMR balance, used when ``reranker`` is ``"mmr"``.
        center_node_uuid: The center node, used when ``reranker`` is
            ``"node_distance"``.
        filters: Optional Zep search filters.
        bfs_origin_node_uuids: Optional BFS seed nodes for a scoped search.

    Returns:
        The results of the search.

    Raises:
        ValueError: If ``scope`` is not a known scope.
    """
    if scope == "auto":
        response = await client.graph.get_context(
            graph_uuid,
            query=query,
            **({"filters": filters} if filters is not None else {}),
        )
        return SearchResults(scope=scope, context=response.context)

    kwargs: dict[str, Any] = {"query": query}
    if limit is not None:
        kwargs["limit"] = limit
    if reranker is not None:
        kwargs["reranker"] = reranker
    if mmr_lambda is not None:
        kwargs["mmr_lambda"] = mmr_lambda
    if center_node_uuid is not None:
        kwargs["center_node_uuid"] = center_node_uuid
    if filters is not None:
        kwargs["filters"] = filters
    if bfs_origin_node_uuids is not None:
        kwargs["bfs_origin_node_uuids"] = bfs_origin_node_uuids

    if scope == "edges":
        pager: Any = await client.graph.search_edges(graph_uuid, **kwargs)
    elif scope == "nodes":
        pager = await client.graph.search_nodes(graph_uuid, **kwargs)
    elif scope == "episodes":
        pager = await client.graph.search_episodes(graph_uuid, **kwargs)
    elif scope == "observations":
        pager = await client.graph.search_observations(graph_uuid, **kwargs)
    elif scope == "thread_summaries":
        pager = await client.graph.search_thread_summaries(graph_uuid, **kwargs)
    else:
        raise ValueError(f"Unknown Zep search scope: {scope!r}")

    # One page is enough: the caller asks for at most ``limit`` results, and
    # Zep returns them on the first page.
    return SearchResults(scope=scope, items=list(pager.items or []))


def _name_summary_text(name: str | None, summary: str | None) -> str:
    """Join a name and summary as ``"name: summary"``, falling back gracefully."""
    if name and summary:
        return f"{name}: {summary}"
    if name:
        return name
    if summary:
        return summary
    return ""


def result_text(item: Any, scope: str) -> str:
    """Render one Zep search result as text.

    Each v4 scope returns its own type, so the scope selects the fields.
    """
    if scope == "edges":
        return str(item.fact or "")
    if scope == "episodes":
        return str(item.content or "")
    if scope == "thread_summaries":
        return str(item.summary or "")
    if scope in ("nodes", "observations"):
        return _name_summary_text(item.name, item.summary)
    return ""


def _format_results(results: SearchResults) -> str:
    """Render Zep search results as readable text for the model."""
    if results.scope == "auto":
        context = results.context
        if context and context.strip():
            return context.strip()
        return "No results found."

    parts = [
        f"- {text}" for text in (result_text(item, results.scope) for item in results.items) if text
    ]
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

        tool = create_zep_search_tool(zep_client=zep, user_uuid=user_uuid)
        agent = Agent(tools=[tool])

    By default the tool searches the **given user's** graph (``user_uuid``
    fixed at construction time). Pass ``graph_uuid`` to target a shared
    standalone graph instead; provide exactly one of ``user_uuid`` /
    ``graph_uuid``.

    **Pin-or-expose.** Every search parameter (``scope``,
    ``reranker``, ``limit``, ``mmr_lambda``, ``center_node_uuid``) is exposed
    to the model in the tool's schema by default. Use
    ``search_pinned_params`` to fix a parameter to a constant value and remove
    it from the schema; use ``search_hidden_params`` to remove a parameter
    without pinning it -- Zep's own server-side default applies.

    ``search_filters`` and ``bfs_origin_node_uuids`` are always
    constructor-only.

    Args:
        zep_client: An initialised ``AsyncZep`` client.
        user_uuid: The UUID of the Zep user whose graph is searched. Required
            unless ``graph_uuid`` is set.
        graph_uuid: Optional UUID of a standalone graph. When set, all
            searches target this graph; mutually exclusive with ``user_uuid``.
        search_pinned_params: Optional mapping of a search parameter
            name to a fixed value. Pinned parameters are hidden from the
            model's tool schema and always sent with the given value.
        search_hidden_params: Optional set of search parameter
            names to hide from the model's tool schema without pinning them.
        search_filters: Optional Zep search filters (constructor-only).
        bfs_origin_node_uuids: Optional list of node UUIDs for BFS seeding
            (constructor-only).
        name: The tool name exposed to the model. Defaults to ``"zep_search"``.
        description: The tool description exposed to the model.

    Returns:
        A Strands ``AgentTool``. Calling it runs the Zep search method of the
        effective scope with pinned/model-provided/default parameters merged;
        Zep failures are caught and returned as an error string -- the tool
        never raises.

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

    resolver = GraphUuidResolver(zep_client, user_uuid=user_uuid, graph_uuid=graph_uuid)

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

    constructor_only: dict[str, Any] = {}
    if search_filters is not None:
        constructor_only["filters"] = search_filters
    if bfs_origin_node_uuids is not None:
        constructor_only["bfs_origin_node_uuids"] = bfs_origin_node_uuids

    async def zep_search(*args: Any, **kwargs: Any) -> str:
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

        effective_scope = search_kwargs.get("scope", "edges")
        # The scope selects the Zep method, so it is always necessary, even
        # when it is hidden from the model.
        search_kwargs["scope"] = effective_scope
        if effective_scope == "auto" and "reranker" in search_kwargs:
            dropped_reranker = search_kwargs.pop("reranker")
            if dropped_reranker in _AUTO_INCOMPATIBLE_RERANKERS:
                logger.warning(
                    "zep_search reranker %r is invalid for scope='auto'; omitting reranker.",
                    dropped_reranker,
                )

        search_kwargs.update(constructor_only)

        if not search_kwargs.get("query"):
            return "Error: No search query provided."

        try:
            results = await run_graph_search(
                zep_client,
                graph_uuid=await resolver.resolve(),
                **search_kwargs,
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

        return _format_results(results)

    zep_search.__signature__ = signature  # type: ignore[attr-defined]
    zep_search.__annotations__ = annotations
    zep_search.__name__ = name
    zep_search.__doc__ = description

    # Runtime ``tool`` accepts ``func, name=, description=``; the published
    # overloads only type the decorator form, so cast the result.
    decorated = tool(name=name, description=description)(zep_search)
    return decorated  # type: ignore[no-any-return]
