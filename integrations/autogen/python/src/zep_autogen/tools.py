"""AutoGen tools that search and add data to Zep v4 graphs."""

from __future__ import annotations

import inspect
import logging
from typing import Annotated, Any, Literal, get_origin

from autogen_core.tools import FunctionTool
from zep_cloud import SearchFilters
from zep_cloud.client import AsyncZep
from zep_cloud.types import (
    EdgeSearchFilters,
    EpisodeSearchFilters,
    NodeSearchFilters,
    ObservationSearchFilters,
    ThreadSummarySearchFilters,
)

from .limits import truncate_graph_data

logger = logging.getLogger(__name__)

Scope = Literal["edges", "nodes", "episodes", "observations", "thread_summaries", "auto"]
Reranker = Literal["rrf", "mmr", "node_distance", "episode_mentions", "cross_encoder"]
MAX_SEARCH_LIMIT = 50
_PARAMS: dict[str, dict[str, Any]] = {
    "scope": {
        "type": Scope,
        "description": "Search scope: edges, nodes, episodes, observations, thread_summaries, or auto.",
        "default": "edges",
    },
    "reranker": {
        "type": Reranker,
        "description": "Search result reranker.",
        "default": "rrf",
    },
    "limit": {
        "type": int,
        "description": "Maximum number of search results.",
        "default": 10,
    },
    "mmr_lambda": {
        "type": float | None,
        "description": "Relevance and diversity balance for the mmr reranker.",
        "default": None,
    },
    "center_node_uuid": {
        "type": str | None,
        "description": "Center node UUID for node_distance reranking.",
        "default": None,
    },
}
_SCOPE_METHODS = {
    "edges": "search_edges",
    "nodes": "search_nodes",
    "episodes": "search_episodes",
    "observations": "search_observations",
    "thread_summaries": "search_thread_summaries",
}
_FILTER_TYPES = {
    "edges": EdgeSearchFilters,
    "nodes": NodeSearchFilters,
    "episodes": EpisodeSearchFilters,
    "observations": ObservationSearchFilters,
    "thread_summaries": ThreadSummarySearchFilters,
}


def _typed_search_filters(scope: str, filters: SearchFilters | dict[str, Any] | None) -> Any:
    """Convert the shared filter input to the filter model for one scope."""
    filter_type = _FILTER_TYPES.get(scope)
    if filters is None or filter_type is None:
        return filters
    if isinstance(filters, dict):
        values = filters
    else:
        values = filters.model_dump(exclude_none=True)
    if get_origin(filter_type) is dict:
        return values
    filter_model: Any = filter_type
    allowed = filter_model.model_fields
    return filter_model(**{key: value for key, value in values.items() if key in allowed})


def _build_signature(
    exposed: dict[str, dict[str, Any]],
) -> tuple[inspect.Signature, dict[str, Any]]:
    params = [
        inspect.Parameter(
            "query",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=Annotated[str, "Search query text."],
        )
    ]
    for name, spec in exposed.items():
        params.append(
            inspect.Parameter(
                name,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=spec["default"],
                annotation=Annotated[spec["type"], spec["description"]],
            )
        )
    signature = inspect.Signature(params)
    return signature, {parameter.name: parameter.annotation for parameter in params}


def _format_results(result: Any, scope: str) -> str:
    if scope == "auto":
        context = getattr(result, "context", None)
        return str(context).strip() if context and str(context).strip() else "No results found."
    parts: list[str] = []
    for item in getattr(result, "items", []) or []:
        if scope == "edges":
            text = getattr(item, "fact", None)
        elif scope == "nodes":
            name, summary = getattr(item, "name", None), getattr(item, "summary", None)
            text = f"{name}: {summary}" if name and summary else name or summary
        elif scope == "episodes":
            text = getattr(item, "content", None)
        elif scope == "observations":
            name, summary = getattr(item, "name", None), getattr(item, "summary", None)
            text = f"{name}: {summary}" if name and summary else name or summary
        else:
            text = getattr(item, "summary", None) or getattr(item, "name", None)
        if text:
            parts.append(f"- {text}")
    return "\n".join(parts) if parts else "No results found."


async def _execute_search(
    client: AsyncZep,
    graph_uuid: str,
    query: str,
    scope: str,
    options: dict[str, Any],
    search_filters: SearchFilters | None,
    bfs_origin_node_uuids: list[str] | None,
) -> Any:
    if scope == "auto":
        context_kwargs: dict[str, Any] = {"query": query}
        if search_filters is not None:
            context_kwargs["filters"] = search_filters.model_dump(exclude_none=True)
        return await client.graph.get_context(graph_uuid, **context_kwargs)
    method_name = _SCOPE_METHODS.get(scope)
    if method_name is None:
        raise ValueError(f"Unsupported search scope: {scope}")
    search_kwargs: dict[str, Any] = {"query": query}
    if "limit" in options:
        search_kwargs["page_size"] = options["limit"]
    if search_filters is not None:
        search_kwargs["filters"] = _typed_search_filters(scope, search_filters)
    if bfs_origin_node_uuids is not None:
        search_kwargs["bfs_origin_node_uuids"] = bfs_origin_node_uuids
    for name in ("reranker", "mmr_lambda", "center_node_uuid"):
        if options.get(name) is not None:
            search_kwargs[name] = options[name]
    return await getattr(client.graph, method_name)(graph_uuid, **search_kwargs)


def create_search_graph_tool(
    client: AsyncZep,
    graph_uuid: str | None = None,
    user_uuid: str | None = None,
    *,
    pinned_params: dict[str, Any] | None = None,
    hidden_params: set[str] | None = None,
    search_filters: SearchFilters | None = None,
    bfs_origin_node_uuids: list[str] | None = None,
    name: str = "zep_search",
    description: str = "Search a Zep graph for facts, entities, or context.",
    scope: Scope | None = None,
    limit: int | None = None,
) -> FunctionTool:
    """Create a search tool bound to one graph UUID or one user UUID."""
    if bool(graph_uuid) == bool(user_uuid):
        raise ValueError("Provide exactly one of graph_uuid or user_uuid")
    pinned = dict(pinned_params or {})
    hidden = set(hidden_params or ())
    if scope is not None:
        pinned.setdefault("scope", scope)
    if limit is not None:
        pinned.setdefault("limit", limit)
    unknown = (set(pinned) | hidden) - set(_PARAMS)
    if unknown:
        raise ValueError(f"Unknown search parameter(s): {sorted(unknown)}")
    for parameter in pinned:
        hidden.add(parameter)
    if "limit" in pinned:
        requested_limit = int(pinned["limit"])
        pinned["limit"] = min(MAX_SEARCH_LIMIT, max(1, requested_limit))
        if pinned["limit"] != requested_limit:
            logger.warning(
                "Clamped pinned search limit from %s to %s",
                requested_limit,
                pinned["limit"],
            )
    exposed = {key: value for key, value in _PARAMS.items() if key not in hidden}
    signature, annotations = _build_signature(exposed)
    selected_graph_uuid = graph_uuid or user_uuid or ""

    async def zep_search(*args: Any, **kwargs: Any) -> str:
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        options = dict(bound.arguments)
        query = str(options.pop("query", ""))[:400]
        if not query.strip():
            return "Error: No search query provided."
        options = {
            **{key: value["default"] for key, value in _PARAMS.items() if key not in hidden},
            **options,
            **pinned,
        }
        options["limit"] = min(MAX_SEARCH_LIMIT, max(1, int(options.get("limit", 10))))
        selected_scope = str(options.get("scope", "edges"))
        try:
            result = await _execute_search(
                client,
                selected_graph_uuid,
                query,
                selected_scope,
                options,
                search_filters,
                bfs_origin_node_uuids,
            )
            return _format_results(result, selected_scope)
        except Exception as exc:
            logger.warning("Zep graph search failed: %s", type(exc).__name__)
            return "Graph search failed."

    zep_search.__signature__ = signature  # type: ignore[attr-defined]
    zep_search.__annotations__ = {**annotations, "return": str}
    zep_search.__name__ = name
    return FunctionTool(zep_search, description=description, name=name)


async def add_graph_data(
    client: AsyncZep,
    data: Annotated[str, "Content to store in the graph."],
    graph_uuid: Annotated[str | None, "Context Graph UUID."] = None,
    user_uuid: Annotated[str | None, "User UUID whose graph will store the data."] = None,
    data_type: Annotated[str, "Data type: text, json, or message."] = "text",
) -> dict[str, Any]:
    """Add data to one Context Graph or one user's graph."""
    if bool(graph_uuid) == bool(user_uuid):
        raise ValueError("Provide exactly one of graph_uuid or user_uuid")
    if data_type not in {"text", "json", "message"}:
        raise ValueError("data_type must be 'text', 'json', or 'message'")
    selected_graph_uuid = graph_uuid or user_uuid or ""
    try:
        await client.graph.episode.add(
            selected_graph_uuid,
            type=data_type,
            data=truncate_graph_data(data),
        )
        return {
            "success": True,
            "message": "Data added to graph memory",
            "graph_uuid": selected_graph_uuid,
            "data_type": data_type,
        }
    except Exception as exc:
        logger.warning("Zep graph data add failed: %s", type(exc).__name__)
        return {"success": False, "message": "Failed to add data"}


def create_add_graph_data_tool(
    client: AsyncZep,
    graph_uuid: str | None = None,
    user_uuid: str | None = None,
) -> FunctionTool:
    """Create a tool that adds data to one graph UUID or user UUID."""
    if bool(graph_uuid) == bool(user_uuid):
        raise ValueError("Provide exactly one of graph_uuid or user_uuid")

    async def bound_add_memory_data(
        data: Annotated[str, "Content to store."],
        data_type: Annotated[str, "Data type: text, json, or message."] = "text",
    ) -> dict[str, Any]:
        return await add_graph_data(client, data, graph_uuid, user_uuid, data_type)

    return FunctionTool(
        bound_add_memory_data,
        description="Add data to Zep graph memory.",
    )
