"""
A LiveKit function tool for searching a Zep knowledge graph on demand.

``ZepUserAgent``/``ZepGraphAgent`` inject context automatically on every turn.
This module provides the complementary *pull* path: a model-callable tool
that lets the agent decide when to search the graph for specific facts,
entities, or prior episodes.

:func:`create_graph_search_tool` returns a LiveKit ``RawFunctionTool`` --
suitable for ``tools=[...]`` on an ``Agent`` -- built from a hand-crafted
JSON schema (:data:`_SEARCH_PARAM_SPECS`) via ``function_tool(raw_schema=...)``
rather than introspected from a Python function signature. This lets any
search parameter be *pinned* (fixed to a value and hidden from the model) or
*hidden* (removed from the schema, Zep's own default applies) at construction
time, independently of the others.

Raw-schema handler convention (confirmed against the installed
``livekit-agents``, see ``livekit.agents.llm.mcp``'s ``_make_function_tool``):
a function decorated with ``function_tool(f, raw_schema=...)`` is called with
a single ``raw_arguments: dict[str, Any]`` argument carrying the model's
parsed tool-call arguments -- there is no per-parameter binding/validation
like the plain ``@function_tool`` decorator performs from a Python signature.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from livekit.agents import function_tool
from livekit.agents.llm import RawFunctionTool
from zep_cloud.client import AsyncZep
from zep_cloud.types.graph_search_results import GraphSearchResults

from .exceptions import AgentConfigurationError

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

# ---------------------------------------------------------------------------
# Parameter definitions
# ---------------------------------------------------------------------------
# Each entry describes a graph.search parameter that can be pinned or exposed
# to the model.  Keys match the Zep SDK's ``graph.search()`` kwargs.  Model-
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
_CONSTRUCTOR_ONLY_PARAMS = frozenset({"search_filters", "bfs_origin_node_uuids"})

#: All parameters that may be pinned or hidden at construction.
_PINNABLE_PARAMS = frozenset(_SEARCH_PARAM_SPECS.keys())

#: Default tool name/description exposed to the model.
_DEFAULT_NAME = "zep_search"
_DEFAULT_DESCRIPTION = (
    "Search the knowledge graph for facts, entities, or prior context. "
    "Use this to look up specific details the user has shared before, or "
    "domain knowledge stored in the graph."
)


def create_graph_search_tool(
    zep_client: AsyncZep,
    *,
    graph_id: str | None = None,
    user_id: str | None = None,
    pinned_params: dict[str, Any] | None = None,
    hidden_params: set[str] | None = None,
    search_filters: dict[str, Any] | None = None,
    bfs_origin_node_uuids: list[str] | None = None,
    name: str | None = None,
    description: str | None = None,
) -> RawFunctionTool:
    """Build a LiveKit function tool that searches a Zep knowledge graph.

    Register the returned tool with an ``Agent``::

        from livekit import agents
        from zep_livekit import create_graph_search_tool

        agent = agents.Agent(
            tools=[create_graph_search_tool(zep_client, user_id="user-123")],
        )

    Exactly one of ``graph_id``/``user_id`` must be set: ``graph_id`` targets a
    shared standalone graph (e.g. a documentation knowledge base), ``user_id``
    targets that user's personal graph.

    **Pin-or-expose.** Every ``graph.search`` parameter (``scope``,
    ``reranker``, ``limit``, ``mmr_lambda``, ``center_node_uuid``) is exposed
    to the model in the tool's JSON schema by default, with the documented
    defaults above.  Use ``pinned_params`` to fix a parameter to a constant
    value and remove it from the schema (the model can no longer choose it);
    use ``hidden_params`` to remove a parameter from the schema *without*
    pinning it -- Zep's own server-side default applies, and the parameter is
    simply omitted from the SDK call.

    ``search_filters`` and ``bfs_origin_node_uuids`` are always
    constructor-only: their complex/list-of-object shapes are not exposed to
    the model.

    Args:
        zep_client: An initialised ``AsyncZep`` client.
        graph_id: Optional standalone graph ID.  Mutually exclusive with
            ``user_id``.
        user_id: Optional Zep user ID whose personal graph is searched.
            Mutually exclusive with ``graph_id``.
        pinned_params: Optional mapping of ``graph.search`` parameter name to
            a fixed value.  Pinned parameters are hidden from the model's
            tool schema and always sent with the given value.
        hidden_params: Optional set of ``graph.search`` parameter names to
            hide from the model's tool schema without pinning them -- omitted
            from the SDK call so Zep's own default takes effect.
        search_filters: Optional Zep search filters (constructor-only).
            Supports ``node_labels``, ``edge_types``, ``exclude_node_labels``,
            ``exclude_edge_types``, and property filters.
        bfs_origin_node_uuids: Optional list of node UUIDs for BFS seeding
            (constructor-only).
        name: The tool name exposed to the model.  Defaults to ``"zep_search"``.
        description: The tool description exposed to the model.

    Returns:
        A LiveKit ``RawFunctionTool`` suitable for ``tools=[...]``.  Calling
        it executes ``graph.search`` with pinned/model-provided/default
        parameters merged; Zep failures are caught and returned as an error
        string -- the tool never raises.

    Raises:
        AgentConfigurationError: If neither or both of ``graph_id``/``user_id``
            are set, or if ``pinned_params``/``hidden_params`` contains an
            unknown parameter name.
    """
    if bool(graph_id) == bool(user_id):
        raise AgentConfigurationError(
            "create_graph_search_tool requires exactly one of graph_id or user_id"
        )

    pinned: dict[str, Any] = dict(pinned_params or {})
    hidden: set[str] = set(hidden_params or ())

    unknown_pinned = set(pinned.keys()) - _PINNABLE_PARAMS
    if unknown_pinned:
        raise AgentConfigurationError(
            f"Unknown pinned parameters: {unknown_pinned}. Allowed: {sorted(_PINNABLE_PARAMS)}"
        )
    unknown_hidden = hidden - _PINNABLE_PARAMS
    if unknown_hidden:
        raise AgentConfigurationError(
            f"Unknown hidden parameters: {unknown_hidden}. Allowed: {sorted(_PINNABLE_PARAMS)}"
        )

    # Clamp a pinned limit to Zep's ceiling at construction time so the call
    # never 400s.
    if "limit" in pinned:
        pinned["limit"] = _clamp_limit(pinned["limit"])

    # Auto scope rejects node_distance / episode_mentions and ignores reranker
    # entirely.  If scope is pinned to "auto" and reranker is also pinned,
    # resolve the effective value once, here, so the call path is always valid.
    if pinned.get("scope") == "auto" and "reranker" in pinned:
        if pinned["reranker"] in _AUTO_INCOMPATIBLE_RERANKERS:
            logger.warning(
                "zep_search reranker %r is invalid for scope='auto'; "
                "omitting reranker (auto search uses RRF).",
                pinned["reranker"],
            )
        del pinned["reranker"]

    tool_name = name or _DEFAULT_NAME
    tool_description = description or _DEFAULT_DESCRIPTION
    json_schema = _build_json_schema(pinned=pinned, hidden=hidden)

    async def zep_search(raw_arguments: dict[str, Any]) -> str:
        query = str(raw_arguments.get("query", ""))[:400]
        search_kwargs: dict[str, Any] = {"query": query}

        for param_name in _SEARCH_PARAM_SPECS:
            if param_name in pinned:
                search_kwargs[param_name] = pinned[param_name]
            elif param_name in hidden:
                continue  # hidden, not pinned -> omit; Zep applies its own default
            elif param_name in raw_arguments and raw_arguments[param_name] is not None:
                value = raw_arguments[param_name]
                if param_name == "limit":
                    # Clamp a model-provided limit at call time so the call
                    # never 400s (the schema advertises the bounds, but the
                    # model may still ignore them).
                    value = _clamp_limit(value)
                search_kwargs[param_name] = value
            else:
                default = _SEARCH_PARAM_SPECS[param_name].get("default")
                if default is not None:
                    search_kwargs[param_name] = default

        effective_scope = search_kwargs.get("scope", "edges")
        if effective_scope == "auto" and "reranker" in search_kwargs:
            # Auto search always uses RRF internally and ignores reranker
            # entirely; Zep rejects node_distance/episode_mentions outright.
            # Warn only when the (would-be) reranker is one Zep would reject.
            dropped_reranker = search_kwargs.pop("reranker")
            if dropped_reranker in _AUTO_INCOMPATIBLE_RERANKERS:
                logger.warning(
                    "zep_search reranker %r is invalid for scope='auto'; omitting reranker.",
                    dropped_reranker,
                )

        if graph_id:
            search_kwargs["graph_id"] = graph_id
        else:
            search_kwargs["user_id"] = user_id

        if search_filters is not None:
            search_kwargs["search_filters"] = search_filters
        if bfs_origin_node_uuids is not None:
            search_kwargs["bfs_origin_node_uuids"] = bfs_origin_node_uuids

        if not search_kwargs.get("query"):
            return "Error: No search query provided."

        try:
            results = await zep_client.graph.search(**search_kwargs)
        except Exception as exc:
            logger.warning("Zep graph search failed: %s", exc, exc_info=True)
            return f"Graph search failed: {exc}"

        return _format_results(results, effective_scope)

    raw_schema = {
        "name": tool_name,
        "description": tool_description,
        "parameters": json_schema,
    }
    return function_tool(zep_search, raw_schema=raw_schema)


def _clamp_limit(limit: int) -> int:
    """Clamp ``limit`` to Zep's accepted range ``[1, MAX_SEARCH_LIMIT]``."""
    if limit > MAX_SEARCH_LIMIT:
        logger.warning(
            "zep_search limit %d exceeds Zep ceiling %d; clamping to %d",
            limit,
            MAX_SEARCH_LIMIT,
            MAX_SEARCH_LIMIT,
        )
        return MAX_SEARCH_LIMIT
    if limit < 1:
        return 1
    return limit


def _build_json_schema(
    *,
    pinned: dict[str, Any],
    hidden: set[str],
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
    }


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
