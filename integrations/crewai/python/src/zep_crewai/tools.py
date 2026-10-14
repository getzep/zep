"""CrewAI tools for searching and adding Zep graph data."""

from __future__ import annotations

import logging
from typing import Any, Literal

from crewai.tools import BaseTool
from pydantic import BaseModel, Field, create_model
from zep_cloud.client import Zep
from zep_cloud.types import (
    EdgeSearchFilters,
    EpisodeSearchFilters,
    NodeSearchFilters,
    ObservationSearchFilters,
    SearchFilters,
    ThreadSummarySearchFilters,
)

from .limits import truncate_graph_data

logger = logging.getLogger(__name__)

SearchScope = Literal["edges", "nodes", "episodes", "observations", "thread_summaries", "auto"]
SearchReranker = Literal["rrf", "mmr", "node_distance", "episode_mentions", "cross_encoder"]
MAX_PAGE_SIZE = 50
_AUTO_INCOMPATIBLE_RERANKERS = {"node_distance", "episode_mentions"}
_PARAMS: dict[str, tuple[Any, Any, str]] = {
    "scope": (SearchScope, "edges", "Search scope."),
    "reranker": (SearchReranker, "rrf", "Result reranker."),
    "limit": (int, 10, "Maximum results to return."),
    "mmr_lambda": (float | None, None, "MMR relevance/diversity balance."),
    "center_node_uuid": (str | None, None, "Center node UUID for distance ranking."),
}
_SCOPES = {
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
    "observations": ObservationSearchFilters,
    "thread_summaries": ThreadSummarySearchFilters,
}


def _clamp_limit(limit: int) -> int:
    if limit > MAX_PAGE_SIZE:
        logger.warning(
            "Zep search limit %d exceeds the maximum %d; clamping to %d.",
            limit,
            MAX_PAGE_SIZE,
            MAX_PAGE_SIZE,
        )
        return MAX_PAGE_SIZE
    return max(1, limit)


def _filters_for_scope(filters: SearchFilters, scope: str) -> Any:
    filter_type: Any = _FILTER_TYPES[scope]
    if scope == "observations":
        return filters.model_dump(exclude_none=True)
    values = filters.model_dump(exclude_none=True)
    unsupported = set(values) - set(filter_type.model_fields)
    if unsupported:
        names = ", ".join(sorted(unsupported))
        raise ValueError(f"Filters {names} are not supported for {scope} search.")
    return filter_type(**values)


def _schema(pinned: dict[str, Any], hidden: set[str]) -> type[BaseModel]:
    fields: dict[str, Any] = {"query": (str, Field(description="Search query."))}
    for name, (annotation, default, description) in _PARAMS.items():
        if name not in pinned and name not in hidden:
            fields[name] = (annotation, Field(default=default, description=description))
    return create_model("ZepSearchInput", **fields)


def _format_results(result: Any, scope: str) -> str:
    if scope == "auto":
        return str(getattr(result, "context", "") or "No results found.")
    items = getattr(result, "items", None) or []
    rows: list[str] = []
    for item in items:
        if scope == "edges":
            value = getattr(item, "fact", None)
        elif scope == "nodes":
            value = " — ".join(
                str(part)
                for part in (getattr(item, "name", None), getattr(item, "summary", None))
                if part
            )
        elif scope == "thread_summaries":
            value = getattr(item, "summary", None)
        else:
            value = (
                getattr(item, "content", None)
                or getattr(item, "text", None)
                or getattr(item, "summary", None)
            )
        if value:
            rows.append(f"- {value}")
    return "\n".join(rows) if rows else "No results found."


class SearchInput(BaseModel):
    query: str


class AddGraphDataInput(BaseModel):
    data: str = Field(description="Data to add to the graph.")
    data_type: Literal["text", "json", "message"] = Field(default="text")


class ZepSearchTool(BaseTool):
    """Search one Zep graph across a selected V4 scope."""

    name: str = "Zep Memory Search"
    description: str = "Search Zep memory for relevant information."
    args_schema: type[BaseModel] = SearchInput

    def __init__(
        self,
        client: Zep,
        graph_uuid: str | None = None,
        user_uuid: str | None = None,
        *,
        pinned_params: dict[str, Any] | None = None,
        hidden_params: set[str] | None = None,
        search_filters: SearchFilters | None = None,
        bfs_origin_node_uuids: list[str] | None = None,
        scope: SearchScope | None = None,
        reranker: SearchReranker | None = None,
        limit: int | None = None,
        **kwargs: Any,
    ) -> None:
        if bool(graph_uuid) == bool(user_uuid):
            raise ValueError("Provide exactly one of graph_uuid or user_uuid.")
        if user_uuid:
            graph_uuid = client.user.get(user_uuid).graph_uuid
        pinned = dict(pinned_params or {})
        for key, value in (("scope", scope), ("reranker", reranker), ("limit", limit)):
            if value is not None:
                pinned.setdefault(key, value)
        if "limit" in pinned:
            pinned["limit"] = _clamp_limit(int(pinned["limit"]))
        if pinned.get("scope") == "auto" and pinned.get("reranker") in _AUTO_INCOMPATIBLE_RERANKERS:
            logger.warning(
                "Reranker %r is not supported for auto scope and will be ignored.",
                pinned["reranker"],
            )
        hidden = set(hidden_params or ())
        unknown = (set(pinned) | hidden) - set(_PARAMS)
        if unknown:
            raise ValueError(f"Unknown search parameter(s): {', '.join(sorted(unknown))}")
        if set(pinned) & hidden:
            raise ValueError("A search parameter cannot be both pinned and hidden.")
        kwargs.setdefault("description", f"Search Zep graph {graph_uuid} for relevant information.")
        kwargs["args_schema"] = _schema(pinned, hidden)
        super().__init__(**kwargs)
        self._client = client
        self._graph_uuid = graph_uuid
        self._pinned = pinned
        self._hidden = hidden
        self._search_filters = search_filters
        self._bfs_origins = bfs_origin_node_uuids

    def _run(self, **kwargs: Any) -> str:
        query = str(kwargs.get("query", "")).strip()
        if not query:
            return "Error: empty search query."
        args = {**kwargs, **self._pinned}
        scope = args.get("scope") or "edges"
        try:
            if scope == "auto":
                if args.get("reranker") in _AUTO_INCOMPATIBLE_RERANKERS:
                    logger.warning(
                        "Reranker %r is not supported for auto scope and will be ignored.",
                        args["reranker"],
                    )
                context_args: dict[str, Any] = {"query": query[:400]}
                if self._search_filters is not None:
                    context_args["filters"] = self._search_filters
                result = self._client.graph.get_context(self._graph_uuid, **context_args)
            else:
                method = getattr(self._client.graph, _SCOPES[scope])
                page_size = _clamp_limit(int(args.get("limit", 10)))
                search_args: dict[str, Any] = {"query": query[:400], "page_size": page_size}
                optional = {
                    "filters": (
                        _filters_for_scope(self._search_filters, scope)
                        if self._search_filters is not None
                        else None
                    ),
                    "bfs_origin_node_uuids": self._bfs_origins,
                    "reranker": args.get("reranker"),
                    "mmr_lambda": args.get("mmr_lambda"),
                    "center_node_uuid": args.get("center_node_uuid"),
                }
                search_args.update(
                    {key: value for key, value in optional.items() if value is not None}
                )
                result = method(self._graph_uuid, **search_args)
            return _format_results(result, scope)
        except Exception as exc:
            logger.error("Error searching Zep memory: %s", exc)
            return f"Error searching Zep memory: {exc}"


class ZepAddDataTool(BaseTool):
    """Add an episode to a Zep graph."""

    name: str = "Zep Add Data"
    description: str = "Add data to Zep graph memory."
    args_schema: type[BaseModel] = AddGraphDataInput

    def __init__(self, client: Zep, graph_uuid: str, **kwargs: Any) -> None:
        if not graph_uuid:
            raise ValueError("graph_uuid is required.")
        super().__init__(**kwargs)
        self._client = client
        self._graph_uuid = graph_uuid

    def _run(self, data: str, data_type: str = "text") -> str:
        try:
            self._client.graph.episode.add(
                self._graph_uuid, data=truncate_graph_data(data), type=data_type
            )
            return f"Successfully added {data_type} data to graph {self._graph_uuid}."
        except Exception as exc:
            logger.error("Error adding data to Zep: %s", exc)
            return f"Error adding data to Zep: {exc}"


def create_search_tool(
    client: Zep,
    graph_uuid: str | None = None,
    user_uuid: str | None = None,
    **kwargs: Any,
) -> ZepSearchTool:
    return ZepSearchTool(client, graph_uuid=graph_uuid, user_uuid=user_uuid, **kwargs)


def create_add_data_tool(client: Zep, graph_uuid: str) -> ZepAddDataTool:
    return ZepAddDataTool(client, graph_uuid=graph_uuid)
