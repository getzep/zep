"""
Zep AutoGen Tools.

This module provides AutoGen tools for interacting with Zep memory storage,
including graph and user memory operations.

``create_search_graph_tool`` follows the pin-or-expose pattern shared by the
other Zep framework integrations: every search parameter (``scope``,
``reranker``, ``limit``, ``mmr_lambda``, ``center_node_uuid``) is exposed to
the model by default and can be pinned (fixed to a constant, hidden from the
model) or hidden (removed from the schema without pinning; Zep's own default
applies) at construction time.

Zep v4 replaces the single v3 ``graph.search`` call with one method for each
scope, and it addresses a graph by UUID. The tool factories therefore take
``graph_uuid`` or ``user_uuid``, and the ``scope`` parameter selects the v4
method to call.

Unlike the sibling integrations' ``create_zep_search_tool`` (which builds a
tool from a hand-crafted JSON schema), AutoGen's ``FunctionTool`` derives its
schema strictly from the wrapped Python function's typed signature -- there is
no raw-JSON-schema constructor argument. So pin-or-expose here works by
*dynamically building the wrapped function's signature*: exposed parameters
become real, typed parameters of the function AutoGen introspects (so they
appear in ``tool.schema["parameters"]["properties"]``), while pinned/hidden
parameters are never parameters of the function at all -- they are merged in
as constants (or omitted) when the tool actually calls the search method. This
was verified against the installed ``autogen_core`` package's
``FunctionTool``/``args_base_model_from_signature`` implementation.
"""

import inspect
import logging
from typing import Annotated, Any, Literal

from autogen_core.tools import FunctionTool
from zep_cloud import SearchFilters
from zep_cloud.client import AsyncZep

from .limits import truncate_graph_data
from .search import Scope, clamp_limit, name_summary_text, search_scope

logger = logging.getLogger(__name__)

Reranker = Literal["rrf", "mmr", "node_distance", "episode_mentions", "cross_encoder"]

#: Rerankers Zep rejects when ``scope == "auto"`` (auto always uses RRF
#: retrieval and applies its own internal cross-scope rerank).
_AUTO_INCOMPATIBLE_RERANKERS = ("node_distance", "episode_mentions")

# ---------------------------------------------------------------------------
# Parameter definitions
# ---------------------------------------------------------------------------
# Each entry describes a graph.search parameter that can be pinned or exposed
# to the model.  Keys match the Zep SDK's ``graph.search()`` kwargs.  Model-
# exposed by default; hidden only when pinned or explicitly listed in
# ``hidden_params``. ``annotation`` is the real typed annotation used to build
# the dynamic function signature FunctionTool introspects.

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
_CONSTRUCTOR_ONLY_PARAMS = frozenset({"search_filters", "bfs_origin_node_uuids"})

#: All parameters that may be pinned or hidden at construction.
_PINNABLE_PARAMS = frozenset(_SEARCH_PARAM_SPECS.keys())


def _format_results(items: list[Any], scope: str) -> str:
    """Render the results of a scoped Zep search as readable text."""
    parts: list[str] = []
    if scope == "edges":
        parts = [f"- {edge.fact}" for edge in items if edge.fact]
    elif scope in ("nodes", "observations"):
        for item in items:
            text = name_summary_text(item.name, item.summary)
            if text:
                parts.append(f"- {text}")
    elif scope == "episodes":
        parts = [f"- {episode.content}" for episode in items if episode.content]
    elif scope == "thread_summaries":
        for summary in items:
            if summary.summary:
                parts.append(f"- {summary.summary}")

    return "\n".join(parts) if parts else "No results found."


def _build_search_signature(
    exposed: dict[str, dict[str, Any]],
) -> tuple[inspect.Signature, dict[str, Any]]:
    """Build the typed signature FunctionTool will introspect.

    ``query`` is always present and required; ``exposed`` params (those not
    pinned or hidden) become real, defaulted parameters annotated with
    ``Annotated[<type>, <description>]`` so FunctionTool's schema generation
    picks up both the type/enum and the description.
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


def create_search_graph_tool(
    client: AsyncZep,
    graph_uuid: str | None = None,
    user_uuid: str | None = None,
    *,
    pinned_params: dict[str, Any] | None = None,
    hidden_params: set[str] | None = None,
    search_filters: SearchFilters | dict[str, Any] | None = None,
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
    limit: int | None = None,
) -> FunctionTool:
    """Build an AutoGen ``FunctionTool`` that searches a Zep knowledge graph.

    Register the returned tool with an agent::

        from zep_autogen import create_search_graph_tool

        tool = create_search_graph_tool(zep_client, user_uuid=user.uuid_)
        agent = AssistantAgent(..., tools=[tool], reflect_on_tool_use=True)

    By default the tool searches the graph identified by **exactly one of**
    ``graph_uuid`` or ``user_uuid`` (required, mutually exclusive). With
    ``user_uuid``, the tool reads the UUID of the graph of the user from the
    user record on the first call and caches the value.

    **Pin-or-expose.** Every search parameter (``scope``,
    ``reranker``, ``limit``, ``mmr_lambda``, ``center_node_uuid``) is exposed
    to the model in the tool's schema by default, with the documented
    defaults above. Use ``pinned_params`` to fix a parameter to a constant
    value and remove it from the schema (the model can no longer choose it);
    use ``hidden_params`` to remove a parameter from the schema *without*
    pinning it -- Zep's own server-side default applies, and the parameter is
    simply omitted from the SDK call.

    ``search_filters`` and ``bfs_origin_node_uuids`` are always
    constructor-only: their complex/list-of-object shapes are not exposed to
    the model.

    Args:
        client: AsyncZep client instance.
        graph_uuid: Optional UUID of a graph to bind to this tool.
        user_uuid: Optional UUID of a user to bind to this tool.
        pinned_params: Optional mapping of a search parameter name to
            a fixed value.  Pinned parameters are hidden from the model's
            tool schema and always sent with the given value.
        hidden_params: Optional set of search parameter names to
            hide from the model's tool schema without pinning them --
            omitted from the SDK call so Zep's own default takes effect.
        search_filters: Optional Zep search filters (constructor-only).
            Supports ``node_labels``, ``edge_types``, ``exclude_node_labels``,
            ``exclude_edge_types``, and property filters.
        bfs_origin_node_uuids: Optional list of node UUIDs for BFS seeding
            (constructor-only).
        name: The tool name exposed to the model. Defaults to ``"zep_search"``.
        description: The tool description exposed to the model.
        scope: Deprecated back-compat alias for ``pinned_params={"scope": scope}``.
        limit: Deprecated back-compat alias for ``pinned_params={"limit": limit}``.

    Returns:
        An ``autogen_core.tools.FunctionTool``. Calling it executes the v4
        search method of the scope with pinned/model-provided/default
        parameters merged; Zep failures are caught and returned as an error
        string -- the tool never raises into the agent.

    Raises:
        ValueError: If neither or both of ``graph_uuid``/``user_uuid`` are
            provided, or ``pinned_params``/``hidden_params`` (or a legacy
            alias) contains an unknown parameter name.
    """
    if not graph_uuid and not user_uuid:
        raise ValueError("Either graph_uuid or user_uuid must be provided when creating the tool")
    if graph_uuid and user_uuid:
        raise ValueError(
            "Only one of graph_uuid or user_uuid should be provided when creating the tool"
        )

    pinned: dict[str, Any] = dict(pinned_params or {})
    hidden: set[str] = set(hidden_params or ())

    # Legacy constructor args pin (and thus hide) their parameter, same as
    # passing it via pinned_params -- back-compat for the pre-pin-or-expose API.
    if scope is not None:
        pinned.setdefault("scope", scope)
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
        pinned["limit"] = clamp_limit(pinned["limit"])

    # Auto scope rejects node_distance / episode_mentions and ignores reranker
    # entirely.  If scope is pinned to "auto" and reranker is also pinned,
    # resolve the effective value once, here, so the call path is always valid.
    # The reranker stays hidden from the model (the caller pinned it) but is
    # omitted from the SDK call.
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

    # Cache of the UUID of the graph of the user, read once from the user
    # record. Reading the user record is addressed by UUID; it is not a name
    # lookup.
    resolved: dict[str, str] = {}

    async def _graph_uuid() -> str:
        if graph_uuid:
            return graph_uuid
        cached = resolved.get("graph_uuid")
        if cached:
            return cached
        if user_uuid is None:
            raise ValueError("No graph_uuid and no user_uuid are bound to this tool")
        user = await client.user.get(user_uuid)
        if not user.graph_uuid:
            raise ValueError(f"Zep user {user_uuid} has no graph UUID")
        user_graph_uuid = str(user.graph_uuid)
        resolved["graph_uuid"] = user_graph_uuid
        return user_graph_uuid

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
                continue  # hidden, not pinned -> omit; Zep applies its own default
            elif param_name in call_args:
                value = call_args[param_name]
                if value is not None:
                    search_kwargs[param_name] = value

        # Clamp a model-provided limit to Zep's ceiling so the call never
        # 400s (pinned limits were already clamped at construction time).
        limit_value = search_kwargs.get("limit")
        if limit_value is not None:
            search_kwargs["limit"] = clamp_limit(limit_value)

        effective_scope = str(search_kwargs.pop("scope", "edges"))
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

        search_kwargs.update(constructor_only)

        if not search_kwargs.get("query"):
            return "Error: No search query provided."

        try:
            target_graph_uuid = await _graph_uuid()
            if effective_scope == "auto":
                # Auto search maps to graph.get_context, which composes one
                # context block. It takes no reranker, limit, or node
                # parameters.
                context_kwargs: dict[str, Any] = {"query": search_kwargs["query"]}
                if "filters" in search_kwargs:
                    context_kwargs["filters"] = search_kwargs["filters"]
                response = await client.graph.get_context(target_graph_uuid, **context_kwargs)
                context = str(response.context) if response.context else ""
                if context.strip():
                    return context.strip()
                return "No results found."

            query_text = str(search_kwargs.pop("query"))
            items = await search_scope(
                client,
                graph_uuid=target_graph_uuid,
                query=query_text,
                scope=effective_scope,
                **search_kwargs,
            )
        except Exception as exc:
            logger.warning("Zep graph search failed: %s", exc, exc_info=True)
            return f"Graph search failed: {exc}"

        return _format_results(items, effective_scope)

    zep_search.__signature__ = signature  # type: ignore[attr-defined]
    zep_search.__annotations__ = {**annotations, "return": str}
    zep_search.__name__ = name

    return FunctionTool(zep_search, description=description, name=name)


async def add_graph_data(
    client: AsyncZep,
    data: Annotated[str, "The data/information to store in the graph"],
    graph_uuid: Annotated[str | None, "Graph UUID to store data in (for graph memory)"] = None,
    user_uuid: Annotated[str | None, "User UUID to store data for (for user memory)"] = None,
    data_type: Annotated[str, "Type of data: 'text', 'json', or 'message'"] = "text",
) -> dict[str, Any]:
    """
    Add data to Zep memory storage.

    Adds data to a standalone graph (if ``graph_uuid`` is provided) or to the
    graph of a user (if ``user_uuid`` is provided).

    Args:
        client: AsyncZep client instance
        data: Data content to store
        graph_uuid: UUID of a standalone graph
        user_uuid: UUID of a user, whose graph receives the data
        data_type: Type of data being stored

    Returns:
        Dictionary with operation result

    Raises:
        ValueError: If parameters are invalid
    """
    if not graph_uuid and not user_uuid:
        raise ValueError("Either graph_uuid or user_uuid must be provided")

    if graph_uuid and user_uuid:
        raise ValueError("Only one of graph_uuid or user_uuid should be provided")

    truncated_data = truncate_graph_data(data)

    try:
        if graph_uuid:
            # Add to a standalone graph
            await client.graph.episode.add(graph_uuid, type=data_type, data=truncated_data)

            logger.debug(f"Added data to graph {graph_uuid}")
            return {
                "success": True,
                "message": "Data added to graph memory",
                "graph_uuid": graph_uuid,
                "data_type": data_type,
            }

        # user_uuid provided: add to the graph of the user
        if user_uuid is None:
            raise ValueError("Either graph_uuid or user_uuid must be provided")
        user = await client.user.get(user_uuid)
        if not user.graph_uuid:
            raise ValueError(f"Zep user {user_uuid} has no graph UUID")
        await client.graph.episode.add(user.graph_uuid, type=data_type, data=truncated_data)

        logger.debug(f"Added data to the graph of user {user_uuid}")
        return {
            "success": True,
            "message": "Data added to user graph memory",
            "user_uuid": user_uuid,
            "data_type": data_type,
        }

    except Exception as e:
        logger.error(f"Error adding memory data: {e}")
        return {"success": False, "message": f"Failed to add data: {str(e)}"}


def create_add_graph_data_tool(
    client: AsyncZep, graph_uuid: str | None = None, user_uuid: str | None = None
) -> FunctionTool:
    """
    Create an add memory data tool bound to a Zep client.

    Args:
        client: AsyncZep client instance
        graph_uuid: Optional UUID of a graph to bind to this tool
        user_uuid: Optional UUID of a user to bind to this tool

    Returns:
        FunctionTool for adding memory data

    Raises:
        ValueError: If neither or both graph_uuid and user_uuid are provided
    """
    if not graph_uuid and not user_uuid:
        raise ValueError("Either graph_uuid or user_uuid must be provided when creating the tool")

    if graph_uuid and user_uuid:
        raise ValueError(
            "Only one of graph_uuid or user_uuid should be provided when creating the tool"
        )

    async def bound_add_memory_data(
        data: Annotated[str, "The data/information to store in memory"],
        data_type: Annotated[str, "Type of data: 'text', 'json', or 'message'"] = "text",
    ) -> dict[str, Any]:
        return await add_graph_data(client, data, graph_uuid, user_uuid, data_type)

    target = f"graph {graph_uuid}" if graph_uuid else f"user {user_uuid}"
    return FunctionTool(
        bound_add_memory_data,
        description=f"Add data to Zep memory storage in {target}.",
    )
