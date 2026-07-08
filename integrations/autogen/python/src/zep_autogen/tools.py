"""
Zep AutoGen Tools.

This module provides AutoGen tools for interacting with Zep memory storage,
including graph and user memory operations.

``create_search_graph_tool`` (BREAKING in this version -- see the CHANGELOG)
follows the pin-or-expose pattern shared by the other Zep framework
integrations: every ``graph.search`` parameter (``scope``, ``reranker``,
``limit``, ``mmr_lambda``, ``center_node_uuid``) is exposed to the model by
default and can be pinned (fixed to a constant, hidden from the model) or
hidden (removed from the schema without pinning; Zep's own default applies)
at construction time.

Unlike the sibling integrations' ``create_zep_search_tool`` (which builds a
tool from a hand-crafted JSON schema), AutoGen's ``FunctionTool`` derives its
schema strictly from the wrapped Python function's typed signature -- there is
no raw-JSON-schema constructor argument. So pin-or-expose here works by
*dynamically building the wrapped function's signature*: exposed parameters
become real, typed parameters of the function AutoGen introspects (so they
appear in ``tool.schema["parameters"]["properties"]``), while pinned/hidden
parameters are never parameters of the function at all -- they are merged in
as constants (or omitted) when the tool actually calls ``graph.search``. This
was verified against the installed ``autogen_core`` package's
``FunctionTool``/``args_base_model_from_signature`` implementation.
"""

import inspect
import logging
from typing import Annotated, Any, Literal

from autogen_core.tools import FunctionTool
from zep_cloud.client import AsyncZep

from .limits import truncate_graph_data

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


def _name_summary_text(name: str | None, summary: str | None) -> str:
    """Join a name and summary as "name: summary", falling back gracefully."""
    if name and summary:
        return f"{name}: {summary}"
    if name:
        return name
    if summary:
        return summary
    return ""


def _format_results(result: Any, scope: str) -> str:
    """Render Zep search results as readable text for the model."""
    if scope == "auto":
        context = getattr(result, "context", None)
        if context and str(context).strip():
            return str(context).strip()
        return "No results found."

    parts: list[str] = []
    if scope == "edges" and result.edges:
        parts = [f"- {edge.fact}" for edge in result.edges if edge.fact]
    elif scope == "nodes" and result.nodes:
        for node in result.nodes:
            text = _name_summary_text(getattr(node, "name", None), getattr(node, "summary", None))
            if text:
                parts.append(f"- {text}")
    elif scope == "episodes" and result.episodes:
        parts = [f"- {ep.content}" for ep in result.episodes if ep.content]
    elif scope == "observations" and result.observations:
        for obs in result.observations:
            text = _name_summary_text(getattr(obs, "name", None), getattr(obs, "summary", None))
            if text:
                parts.append(f"- {text}")
    elif scope == "thread_summaries" and result.thread_summaries:
        for ts in result.thread_summaries:
            summary = getattr(ts, "summary", None)
            summary_text = summary or getattr(ts, "name", None)
            if summary_text:
                parts.append(f"- {summary_text}")

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
    graph_id: str | None = None,
    user_id: str | None = None,
    *,
    pinned_params: dict[str, Any] | None = None,
    hidden_params: set[str] | None = None,
    search_filters: dict[str, Any] | None = None,
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

        tool = create_search_graph_tool(zep_client, user_id="user-123")
        agent = AssistantAgent(..., tools=[tool], reflect_on_tool_use=True)

    By default the tool searches the graph identified by **exactly one of**
    ``graph_id`` or ``user_id`` (required, mutually exclusive).

    **Pin-or-expose.** Every ``graph.search`` parameter (``scope``,
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
        graph_id: Optional graph ID to bind to this tool.
        user_id: Optional user ID to bind to this tool.
        pinned_params: Optional mapping of ``graph.search`` parameter name to
            a fixed value.  Pinned parameters are hidden from the model's
            tool schema and always sent with the given value.
        hidden_params: Optional set of ``graph.search`` parameter names to
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
        An ``autogen_core.tools.FunctionTool``. Calling it executes
        ``graph.search`` with pinned/model-provided/default parameters
        merged; Zep failures are caught and returned as an error string --
        the tool never raises into the agent.

    Raises:
        ValueError: If neither or both of ``graph_id``/``user_id`` are
            provided, or ``pinned_params``/``hidden_params`` (or a legacy
            alias) contains an unknown parameter name.
    """
    if not graph_id and not user_id:
        raise ValueError("Either graph_id or user_id must be provided when creating the tool")
    if graph_id and user_id:
        raise ValueError(
            "Only one of graph_id or user_id should be provided when creating the tool"
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

    exposed = {
        param_name: spec
        for param_name, spec in _SEARCH_PARAM_SPECS.items()
        if param_name not in pinned and param_name not in hidden
    }
    signature, annotations = _build_search_signature(exposed)

    constructor_only: dict[str, Any] = {}
    if search_filters is not None:
        constructor_only["search_filters"] = search_filters
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
                continue  # hidden, not pinned -> omit; Zep applies its own default
            elif param_name in call_args:
                value = call_args[param_name]
                if value is not None:
                    search_kwargs[param_name] = value

        if graph_id:
            search_kwargs["graph_id"] = graph_id
        else:
            search_kwargs["user_id"] = user_id

        search_kwargs.update(constructor_only)

        if not search_kwargs.get("query"):
            return "Error: No search query provided."

        effective_scope = str(search_kwargs.get("scope", "edges"))

        try:
            result = await client.graph.search(**search_kwargs)
        except Exception as exc:
            logger.warning("Zep graph search failed: %s", exc, exc_info=True)
            return f"Graph search failed: {exc}"

        return _format_results(result, effective_scope)

    zep_search.__signature__ = signature  # type: ignore[attr-defined]
    zep_search.__annotations__ = {**annotations, "return": str}
    zep_search.__name__ = name

    return FunctionTool(zep_search, description=description, name=name)


async def add_graph_data(
    client: AsyncZep,
    data: Annotated[str, "The data/information to store in the graph"],
    graph_id: Annotated[str | None, "Graph ID to store data in (for graph memory)"] = None,
    user_id: Annotated[str | None, "User ID to store data for (for user memory)"] = None,
    data_type: Annotated[str, "Type of data: 'text', 'json', or 'message'"] = "text",
) -> dict[str, Any]:
    """
    Add data to Zep memory storage.

    Adds data to either graph memory (if graph_id provided) or user memory (if user_id provided).

    Args:
        client: AsyncZep client instance
        data: Data content to store
        graph_id: Graph ID for non-user graph storage
        user_id: User ID for user graph storage
        data_type: Type of data being stored

    Returns:
        Dictionary with operation result

    Raises:
        ValueError: If parameters are invalid
    """
    if not graph_id and not user_id:
        raise ValueError("Either graph_id or user_id must be provided")

    if graph_id and user_id:
        raise ValueError("Only one of graph_id or user_id should be provided")

    truncated_data = truncate_graph_data(data)

    try:
        if graph_id:
            # Add to graph memory
            await client.graph.add(graph_id=graph_id, type=data_type, data=truncated_data)

            logger.debug(f"Added data to graph {graph_id}")
            return {
                "success": True,
                "message": "Data added to graph memory",
                "graph_id": graph_id,
                "data_type": data_type,
            }

        else:  # user_id provided
            # Add to user graph memory
            await client.graph.add(user_id=user_id, type=data_type, data=truncated_data)

            logger.debug(f"Added data to user graph {user_id}")
            return {
                "success": True,
                "message": "Data added to user graph memory",
                "user_id": user_id,
                "data_type": data_type,
            }

    except Exception as e:
        logger.error(f"Error adding memory data: {e}")
        return {"success": False, "message": f"Failed to add data: {str(e)}"}


def create_add_graph_data_tool(
    client: AsyncZep, graph_id: str | None = None, user_id: str | None = None
) -> FunctionTool:
    """
    Create an add memory data tool bound to a Zep client.

    Args:
        client: AsyncZep client instance
        graph_id: Optional graph ID to bind to this tool
        user_id: Optional user ID to bind to this tool

    Returns:
        FunctionTool for adding memory data

    Raises:
        ValueError: If neither or both graph_id and user_id are provided
    """
    if not graph_id and not user_id:
        raise ValueError("Either graph_id or user_id must be provided when creating the tool")

    if graph_id and user_id:
        raise ValueError(
            "Only one of graph_id or user_id should be provided when creating the tool"
        )

    async def bound_add_memory_data(
        data: Annotated[str, "The data/information to store in memory"],
        data_type: Annotated[str, "Type of data: 'text', 'json', or 'message'"] = "text",
    ) -> dict[str, Any]:
        return await add_graph_data(client, data, graph_id, user_id, data_type)

    return FunctionTool(
        bound_add_memory_data,
        description=f"Add data to Zep memory storage in {'graph ' + (graph_id or '') if graph_id else 'user ' + (user_id or '')}.",
    )
