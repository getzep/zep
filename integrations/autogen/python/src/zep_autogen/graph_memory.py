"""AutoGen memory for a shared Zep Context Graph."""

from __future__ import annotations

import logging
from typing import Any

from autogen_core import CancellationToken
from autogen_core.memory import (
    Memory,
    MemoryContent,
    MemoryMimeType,
    MemoryQueryResult,
    UpdateContextResult,
)
from autogen_core.model_context import ChatCompletionContext
from autogen_core.models import SystemMessage
from zep_cloud import SearchFilters
from zep_cloud.client import AsyncZep

from .limits import truncate_graph_data
from .tools import _typed_search_filters

logger = logging.getLogger(__name__)


class ZepGraphMemory(Memory):
    """Store and retrieve data from a shared Context Graph."""

    def __init__(
        self,
        client: AsyncZep,
        graph_uuid: str,
        search_filters: SearchFilters | None = None,
        max_characters: int = 4000,
    ) -> None:
        if not isinstance(client, AsyncZep):
            raise TypeError("client must be an instance of AsyncZep")
        if not graph_uuid:
            raise ValueError("graph_uuid is required")
        self._client = client
        self._graph_uuid = graph_uuid
        self._search_filters = search_filters
        self._max_characters = max_characters

    async def add(
        self,
        content: MemoryContent,
        cancellation_token: CancellationToken | None = None,
    ) -> None:
        """Add an episode to the graph without failing the agent turn."""
        if content.mime_type not in {
            MemoryMimeType.TEXT,
            MemoryMimeType.MARKDOWN,
            MemoryMimeType.JSON,
        }:
            raise ValueError(f"Unsupported mime type: {content.mime_type}")
        metadata = dict(content.metadata or {})
        content_type = metadata.get("type", "data")
        if content_type not in {"data", "message"}:
            raise ValueError("metadata.type must be 'data' or 'message'")
        data_type = (
            "message"
            if content_type == "message"
            else ("json" if content.mime_type == MemoryMimeType.JSON else "text")
        )
        try:
            await self._client.graph.episode.add(
                self._graph_uuid,
                type=data_type,
                data=truncate_graph_data(str(content.content)),
            )
        except Exception as exc:
            logger.warning("Failed to add Zep graph episode: %s", type(exc).__name__)

    async def query(
        self,
        query: str | MemoryContent,
        cancellation_token: CancellationToken | None = None,
        **kwargs: Any,
    ) -> MemoryQueryResult:
        """Search the shared graph with a selected v4 scope."""
        query_text = str(query.content) if isinstance(query, MemoryContent) else query
        scope = kwargs.pop("scope", "edges")
        limit = kwargs.pop("limit", kwargs.pop("page_size", 5))
        search_filters = kwargs.pop("search_filters", kwargs.pop("filters", self._search_filters))
        methods = {
            "edges": self._client.graph.search_edges,
            "nodes": self._client.graph.search_nodes,
            "episodes": self._client.graph.search_episodes,
            "observations": self._client.graph.search_observations,
            "thread_summaries": self._client.graph.search_thread_summaries,
        }
        try:
            if scope == "auto":
                context_kwargs: dict[str, Any] = {
                    "query": query_text,
                    "max_characters": self._max_characters,
                }
                if search_filters is not None:
                    context_kwargs["filters"] = search_filters
                result = await self._client.graph.get_context(
                    self._graph_uuid,
                    **context_kwargs,
                )
                context = getattr(result, "context", None)
                items = (
                    [MemoryContent(content=context, mime_type=MemoryMimeType.TEXT)]
                    if context
                    else []
                )
            else:
                method = methods.get(scope)
                if method is None:
                    raise ValueError(f"Unsupported search scope: {scope}")
                search_kwargs: dict[str, Any] = {
                    "query": query_text,
                    "page_size": limit,
                    **kwargs,
                }
                if search_filters is not None:
                    search_kwargs["filters"] = _typed_search_filters(scope, search_filters)
                page = await method(self._graph_uuid, **search_kwargs)
                items = self._to_memory_content(page.items or [], scope)
            return MemoryQueryResult(results=items)
        except Exception as exc:
            logger.warning("Zep graph query failed: %s", type(exc).__name__)
            return MemoryQueryResult(results=[])

    @staticmethod
    def _to_memory_content(items: list[Any], scope: str) -> list[MemoryContent]:
        results: list[MemoryContent] = []
        for item in items:
            if scope == "edges":
                text = getattr(item, "fact", None)
                metadata = {
                    "source": "graph",
                    "edge_name": getattr(item, "name", None),
                    "edge_attributes": getattr(item, "attributes", None) or {},
                    "created_at": getattr(item, "created_at", None),
                    "valid_at": getattr(item, "valid_at", None),
                    "invalid_at": getattr(item, "invalid_at", None),
                    "expired_at": getattr(item, "expired_at", None),
                }
            elif scope == "nodes":
                name = getattr(item, "name", None)
                summary = getattr(item, "summary", None)
                text = f"{name}:\n {summary}" if name and summary else name or summary
                metadata = {
                    "source": "graph",
                    "node_name": name,
                    "node_attributes": getattr(item, "attributes", None) or {},
                    "created_at": getattr(item, "created_at", None),
                }
            elif scope == "episodes":
                text = getattr(item, "content", None)
                metadata = {
                    "source": "graph",
                    "episode_type": getattr(item, "source", None),
                    "episode_role": getattr(item, "role", None),
                    "episode_name": getattr(item, "role_name", None),
                    "created_at": getattr(item, "created_at", None),
                }
            elif scope == "observations":
                name, summary = getattr(item, "name", None), getattr(item, "summary", None)
                text = f"{name}: {summary}" if name and summary else name or summary
                metadata = {
                    "source": "graph",
                    "observation_name": getattr(item, "name", None),
                    "observation_attributes": getattr(item, "attributes", None) or {},
                    "created_at": getattr(item, "created_at", None),
                }
            else:
                text = getattr(item, "summary", None) or getattr(item, "name", None)
                metadata = {
                    "source": "graph",
                    "thread_uuid": getattr(item, "thread_uuid", None),
                    "created_at": getattr(item, "created_at", None),
                }
            if text:
                results.append(
                    MemoryContent(
                        content=str(text),
                        mime_type=MemoryMimeType.TEXT,
                        metadata=metadata,
                    )
                )
        return results

    async def _retrieve_graph_context(self) -> MemoryContent | None:
        try:
            page = await self._client.graph.episode.list(
                self._graph_uuid,
                page_size=2,
                order="desc",
            )
            episodes = getattr(page, "items", None) or []
            query = "\n".join(
                str(episode.content) for episode in episodes if getattr(episode, "content", None)
            )[-400:]
            if not query:
                return None
            context_kwargs: dict[str, Any] = {
                "query": query,
                "max_characters": self._max_characters,
            }
            if self._search_filters is not None:
                context_kwargs["filters"] = self._search_filters
            response = await self._client.graph.get_context(
                self._graph_uuid,
                **context_kwargs,
            )
            context = getattr(response, "context", None)
            if not context:
                return None
            return MemoryContent(
                content=context,
                mime_type=MemoryMimeType.TEXT,
                metadata={"source": "graph_context"},
            )
        except Exception as exc:
            logger.warning("Zep graph context retrieval failed: %s", type(exc).__name__)
            return None

    async def update_context(self, model_context: ChatCompletionContext) -> UpdateContextResult:
        """Inject graph context as a system message when context is available."""
        try:
            if not await model_context.get_messages():
                return UpdateContextResult(memories=MemoryQueryResult(results=[]))
            context = await self._retrieve_graph_context()
            if context is None:
                return UpdateContextResult(memories=MemoryQueryResult(results=[]))
            await model_context.add_message(SystemMessage(content=str(context.content)))
            return UpdateContextResult(memories=MemoryQueryResult(results=[context]))
        except Exception as exc:
            logger.warning("Failed to update AutoGen context: %s", type(exc).__name__)
            return UpdateContextResult(memories=MemoryQueryResult(results=[]))

    async def clear(self) -> None:
        await self._client.graph.delete(self._graph_uuid)

    async def close(self) -> None:
        """The caller owns the client lifecycle."""
