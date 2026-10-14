from __future__ import annotations

import logging
from typing import Any, Literal

from livekit.agents import function_tool
from livekit.agents.llm import RawFunctionTool
from zep_cloud import SearchFilters
from zep_cloud.client import AsyncZep
from zep_cloud.types import (
    EdgeSearchFilters,
    EpisodeSearchFilters,
    NodeSearchFilters,
    ThreadSummarySearchFilters,
)

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
MAX_SEARCH_LIMIT = 50
_SCOPES: dict[str, str] = {
    "edges": "search_edges",
    "nodes": "search_nodes",
    "episodes": "search_episodes",
    "observations": "search_observations",
    "thread_summaries": "search_thread_summaries",
}
_FILTER_TYPES: dict[str, Any] = {
    "edges": EdgeSearchFilters,
    "nodes": NodeSearchFilters,
    "episodes": EpisodeSearchFilters,
    "observations": dict,
    "thread_summaries": ThreadSummarySearchFilters,
}
_PARAMS: dict[str, dict[str, Any]] = {
    "scope": {
        "type": "string",
        "description": "The graph data to search.",
        "enum": list(_SCOPES) + ["auto"],
        "default": "edges",
    },
    "reranker": {
        "type": "string",
        "description": "The result ranking method.",
        "enum": ["rrf", "mmr", "node_distance", "episode_mentions", "cross_encoder"],
        "default": "rrf",
    },
    "limit": {
        "type": "integer",
        "description": "The maximum number of results.",
        "default": 10,
        "minimum": 1,
        "maximum": MAX_SEARCH_LIMIT,
    },
    "mmr_lambda": {
        "type": "number",
        "description": "The diversity and relevance balance for the mmr reranker.",
    },
    "center_node_uuid": {
        "type": "string",
        "description": "The center node UUID for node-distance reranking.",
    },
}
_PINNABLE_PARAMS = frozenset(_PARAMS)


def create_graph_search_tool(
    zep_client: AsyncZep,
    *,
    graph_uuid: str,
    pinned_params: dict[str, Any] | None = None,
    hidden_params: set[str] | None = None,
    search_filters: SearchFilters | dict[str, Any] | None = None,
    bfs_origin_node_uuids: list[str] | None = None,
    name: str | None = None,
    description: str | None = None,
) -> RawFunctionTool:
    if not graph_uuid:
        raise AgentConfigurationError("graph_uuid must be a non-empty string")

    pinned = dict(pinned_params or {})
    hidden = set(hidden_params or ())
    unknown = (set(pinned) | hidden) - _PINNABLE_PARAMS
    if unknown:
        raise AgentConfigurationError(f"Unknown search parameters: {sorted(unknown)}")
    if "limit" in pinned:
        pinned["limit"] = _clamp_limit(pinned["limit"])

    schema = _build_json_schema(pinned, hidden)

    async def search(raw_arguments: dict[str, Any]) -> str:
        query = str(raw_arguments.get("query", ""))[:400]
        if not query:
            return "Error: No search query provided."
        params: dict[str, Any] = {}
        for key, spec in _PARAMS.items():
            if key in pinned:
                params[key] = pinned[key]
            elif key not in hidden and raw_arguments.get(key) is not None:
                params[key] = raw_arguments[key]
            elif key not in hidden and "default" in spec:
                params[key] = spec["default"]

        scope = params.get("scope", "edges")
        try:
            filters = _coerce_search_filters(search_filters)
            if scope == "auto":
                if any(
                    key in params for key in ("reranker", "limit", "mmr_lambda", "center_node_uuid")
                ):
                    logger.debug(
                        "The auto scope uses graph.get_context and ignores scoped search options"
                    )
                response = await zep_client.graph.get_context(
                    graph_uuid,
                    query=query,
                    filters=filters,
                )
                return getattr(response, "context", None) or "No results found."

            method_name = _SCOPES.get(scope)
            if method_name is None:
                return f"Error: Unsupported search scope: {scope}"
            search_method = getattr(zep_client.graph, method_name)
            kwargs: dict[str, Any] = {
                "query": query,
            }
            if "limit" in params:
                kwargs["page_size"] = _clamp_limit(params["limit"])
            typed_filters = _typed_filters(scope, filters)
            if typed_filters is not None:
                kwargs["filters"] = typed_filters
            if bfs_origin_node_uuids is not None:
                kwargs["bfs_origin_node_uuids"] = bfs_origin_node_uuids
            for key in ("reranker", "mmr_lambda", "center_node_uuid"):
                if params.get(key) is not None:
                    kwargs[key] = params[key]
            result = await search_method(graph_uuid, **kwargs)
            return _format_results(result, scope)
        except Exception as exc:
            logger.warning("Zep graph search failed: %s", exc, exc_info=True)
            return f"Graph search failed: {exc}"

    raw_schema = {
        "name": name or "zep_search",
        "description": description or "Search a Zep graph for relevant information.",
        "parameters": schema,
    }
    return function_tool(search, raw_schema=raw_schema)


def _coerce_search_filters(
    filters: SearchFilters | dict[str, Any] | None,
) -> SearchFilters | None:
    if filters is None:
        return None
    if isinstance(filters, SearchFilters):
        return filters
    return SearchFilters(**filters)


def _typed_filters(scope: str, filters: SearchFilters | None) -> Any | None:
    if filters is None:
        return None
    values = filters.model_dump(exclude_none=True)
    filter_type = _FILTER_TYPES[scope]
    if filter_type is dict:
        return values
    supported = set(filter_type.model_fields)
    unsupported = set(values) - supported
    if unsupported:
        raise AgentConfigurationError(
            f"Search filters are not supported for scope '{scope}': {sorted(unsupported)}"
        )
    return filter_type(**values)


def _clamp_limit(value: Any) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return 10
    return min(max(limit, 1), MAX_SEARCH_LIMIT)


def _build_json_schema(pinned: dict[str, Any], hidden: set[str]) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "query": {
            "type": "string",
            "description": "A natural-language graph search query.",
        }
    }
    for key, definition in _PARAMS.items():
        if key not in pinned and key not in hidden:
            properties[key] = dict(definition)
    return {
        "type": "object",
        "properties": properties,
        "required": ["query"],
    }


def _format_results(response: Any, scope: str) -> str:
    items = getattr(response, "items", None) or []
    lines: list[str] = []
    for item in items:
        if scope == "edges":
            text = getattr(item, "fact", None)
        elif scope in ("nodes", "observations"):
            text = getattr(item, "summary", None) or getattr(item, "name", None)
        elif scope == "episodes":
            text = getattr(item, "data", None) or getattr(item, "content", None)
        else:
            text = getattr(item, "summary", None) or getattr(item, "name", None)
        if text:
            lines.append(f"- {text}")
    return "\n".join(lines) if lines else "No results found."
