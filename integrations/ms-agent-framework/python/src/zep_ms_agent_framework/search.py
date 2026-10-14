"""On-demand Zep graph search for Microsoft Agent Framework."""

from __future__ import annotations

import logging
from typing import Any, Literal

from agent_framework import FunctionTool, tool
from zep_cloud.client import AsyncZep
from zep_cloud.types import (
    EdgeSearchFilters,
    EpisodeSearchFilters,
    NodeSearchFilters,
    SearchFilters,
    ThreadSummarySearchFilters,
)

logger = logging.getLogger(__name__)

Scope = Literal["edges", "nodes", "episodes", "observations", "thread_summaries", "auto"]
Reranker = Literal["rrf", "mmr", "node_distance", "episode_mentions", "cross_encoder"]
ZepSearchTool = FunctionTool
MAX_SEARCH_LIMIT = 50

_SEARCH_PARAM_SPECS: dict[str, dict[str, Any]] = {
    "scope": {
        "type": "string",
        "description": "Result type to search. Use auto for Zep-built context.",
        "enum": ["edges", "nodes", "episodes", "observations", "thread_summaries", "auto"],
        "default": "edges",
    },
    "reranker": {
        "type": "string",
        "description": "Result ordering method. The auto scope uses graph context retrieval.",
        "enum": ["rrf", "mmr", "node_distance", "episode_mentions", "cross_encoder"],
        "default": "rrf",
    },
    "limit": {
        "type": "integer",
        "description": "Maximum number of results.",
        "default": 10,
        "minimum": 1,
        "maximum": MAX_SEARCH_LIMIT,
    },
    "mmr_lambda": {
        "type": "number",
        "description": "Balance between result diversity and query relevance.",
    },
    "center_node_uuid": {
        "type": "string",
        "description": "Center node UUID for distance-based reranking.",
    },
}
_PINNABLE_PARAMS = frozenset(_SEARCH_PARAM_SPECS)
_FILTER_TYPES = {
    "edges": EdgeSearchFilters,
    "nodes": NodeSearchFilters,
    "episodes": EpisodeSearchFilters,
    "thread_summaries": ThreadSummarySearchFilters,
}


def create_zep_search_tool(
    *,
    zep_client: AsyncZep,
    graph_uuid: str,
    search_pinned_params: dict[str, Any] | None = None,
    search_hidden_params: set[str] | None = None,
    search_filters: dict[str, Any] | None = None,
    bfs_origin_node_uuids: list[str] | None = None,
    name: str = "zep_search",
    description: str = "Search the Zep graph for facts, entities, and prior context.",
) -> ZepSearchTool:
    """Create a model-callable tool for a Zep graph."""
    if not graph_uuid:
        raise ValueError("graph_uuid must not be empty")

    pinned = dict(search_pinned_params or {})
    hidden = set(search_hidden_params or ())
    unknown = (set(pinned) | hidden) - _PINNABLE_PARAMS
    if unknown:
        raise ValueError(f"Unknown search parameters: {sorted(unknown)}")
    if "limit" in pinned:
        pinned["limit"] = min(max(int(pinned["limit"]), 1), MAX_SEARCH_LIMIT)

    schema = _build_json_schema(pinned=pinned, hidden=hidden, description=description)

    async def zep_search(**kwargs: Any) -> str:
        query = str(kwargs.get("query", ""))[:400]
        if not query.strip():
            return "Error: No search query provided."

        params: dict[str, Any] = {}
        for parameter, spec in _SEARCH_PARAM_SPECS.items():
            if parameter in pinned:
                params[parameter] = pinned[parameter]
            elif parameter not in hidden and kwargs.get(parameter) is not None:
                params[parameter] = kwargs[parameter]
            elif parameter not in hidden and spec.get("default") is not None:
                params[parameter] = spec["default"]
        limit = min(max(int(params.get("limit", 10)), 1), MAX_SEARCH_LIMIT)
        scope: Scope = params.get("scope", "edges")

        try:
            if scope == "auto":
                request: dict[str, Any] = {"query": query}
                if search_filters is not None:
                    request["filters"] = SearchFilters(**search_filters)
                response = await zep_client.graph.get_context(graph_uuid, **request)
                return _format_context(getattr(response, "context", None))

            request = {
                "query": query,
                "page_size": limit,
            }
            if params.get("reranker") is not None:
                request["reranker"] = params["reranker"]
            if params.get("mmr_lambda") is not None:
                request["mmr_lambda"] = params["mmr_lambda"]
            if params.get("center_node_uuid") is not None:
                request["center_node_uuid"] = params["center_node_uuid"]
            if bfs_origin_node_uuids is not None:
                request["bfs_origin_node_uuids"] = bfs_origin_node_uuids

            if search_filters is not None:
                if scope == "observations":
                    request["filters"] = search_filters
                else:
                    filter_type = _FILTER_TYPES[scope]
                    request["filters"] = filter_type(**search_filters)

            search_method = getattr(zep_client.graph, f"search_{scope}")
            result = await search_method(graph_uuid, **request)
            return _format_results(getattr(result, "items", []), scope)
        except Exception as exc:
            logger.warning("Zep graph search failed", exc_info=True)
            return f"Graph search failed: {exc}"

    return tool(zep_search, name=name, description=description, schema=schema)


def _build_json_schema(
    *,
    pinned: dict[str, Any],
    hidden: set[str],
    description: str,
) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "query": {
            "type": "string",
            "description": "Search query text. The tool uses at most 400 characters.",
        }
    }
    for parameter, spec in _SEARCH_PARAM_SPECS.items():
        if parameter in pinned or parameter in hidden:
            continue
        prop: dict[str, Any] = {"type": spec["type"], "description": spec["description"]}
        if "enum" in spec:
            prop["enum"] = spec["enum"]
        for bound in ("minimum", "maximum"):
            if bound in spec:
                prop[bound] = spec[bound]
        properties[parameter] = prop
    return {
        "type": "object",
        "properties": properties,
        "required": ["query"],
        "description": description,
    }


def _format_context(context: Any) -> str:
    if context and str(context).strip():
        return str(context).strip()
    return "No results found."


def _format_results(items: Any, scope: Scope) -> str:
    parts: list[str] = []
    for item in items or []:
        if scope == "edges":
            text = getattr(item, "fact", None)
        elif scope == "nodes":
            name = getattr(item, "name", None) or "Entity"
            summary = getattr(item, "summary", None)
            text = f"{name}: {summary}" if summary else name
        elif scope == "episodes":
            text = getattr(item, "content", None)
        elif scope == "observations":
            name = getattr(item, "name", None) or "Observation"
            summary = getattr(item, "summary", None)
            text = f"{name}: {summary}" if summary else name
        else:
            text = getattr(item, "summary", None) or getattr(item, "name", None)
        if text:
            parts.append(f"- {text}")
    return "\n".join(parts) if parts else "No results found."
