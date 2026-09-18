"""
Zep Memory integration for AutoGen.

This module provides memory classes that integrate Zep with AutoGen's memory system.
"""

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
from .search import collect, results_to_memory_content, search_scope


class ZepGraphMemory(Memory):
    """
    A memory implementation that integrates with Zep for persistent storage
    and retrieval of conversation context and agent memories.

    This class implements AutoGen's Memory interface and provides:
    - Automatic context injection via update_context()
    - Manual memory queries via query()
    - Data storage in a standalone Zep graph

    Note:
        **No user-scoped provisioning here.** Unlike ``ZepUserMemory``, this
        class is scoped to a standalone graph, such as a shared knowledge
        base, and not to a Zep user. Create the graph out-of-band with
        ``client.graph.create(...)``, store the ``uuid_`` of the response,
        and pass the value as ``graph_uuid``.
    """

    def __init__(
        self,
        client: AsyncZep,
        graph_uuid: str,
        search_filters: SearchFilters | None = None,
        max_characters: int | None = None,
        **kwargs: Any,
    ) -> None:
        """
        Initialize ZepGraphMemory with an AsyncZep client instance.

        Args:
            client: An initialized AsyncZep instance
            graph_uuid: The UUID of the graph in Zep (required). Zep assigns
                the value when the graph is created.
            search_filters: Optional Zep search filters applied to a query
                and to the context retrieval.
            max_characters: Optional maximum length of the retrieved context
                block. When omitted, Zep applies its own default.
            **kwargs: Additional configuration options
        """
        if not isinstance(client, AsyncZep):
            raise TypeError("client must be an instance of AsyncZep")

        if not graph_uuid:
            raise ValueError("graph_uuid is required")

        self._client = client
        self._graph_uuid = graph_uuid
        self._search_filters = search_filters
        self._max_characters = max_characters

        self._config = kwargs

        # Set up module logger
        self._logger = logging.getLogger(__name__)

    async def add(
        self,
        content: MemoryContent,
        cancellation_token: CancellationToken | None = None,
    ) -> None:
        """
        Add data to Zep graph.

        Uses metadata.type to determine the data type:

        Args:
            content: The memory content to store

        Raises:
            ValueError: If the memory content mime type or metadata type is not supported
        """
        # Validate mime type - only support TEXT, MARKDOWN, and JSON
        supported_mime_types = {MemoryMimeType.TEXT, MemoryMimeType.MARKDOWN, MemoryMimeType.JSON}

        if content.mime_type not in supported_mime_types:
            raise ValueError(
                f"Unsupported mime type: {content.mime_type}. "
                f"ZepGraphMemory only supports: {', '.join(str(mt) for mt in supported_mime_types)}"
            )

        # Extract metadata
        metadata_copy = content.metadata.copy() if content.metadata else {}
        content_type = metadata_copy.get("type", "data")  # Default to "data" if no type specified

        mime_to_data_type: dict[MemoryMimeType, str] = {
            MemoryMimeType.TEXT: "text",
            MemoryMimeType.MARKDOWN: "text",
            MemoryMimeType.JSON: "json",
        }

        if content_type == "message":
            data_type = "message"
        elif isinstance(content.mime_type, MemoryMimeType):
            data_type = mime_to_data_type.get(content.mime_type, "text")
        else:
            data_type = "text"  # Default for string or unknown types

        # Add data to the graph
        truncated_data = truncate_graph_data(str(content.content))
        await self._client.graph.episode.add(self._graph_uuid, type=data_type, data=truncated_data)

    async def query(
        self,
        query: str | MemoryContent,
        cancellation_token: CancellationToken | None = None,
        **kwargs: Any,
    ) -> MemoryQueryResult:
        """
        Query memories from Zep storage with a scoped graph search.

        Args:
            query: Search query string or MemoryContent
            cancellation_token: Optional cancellation token
            **kwargs: Additional query parameters. ``scope`` selects the
                search scope and defaults to ``"edges"``. The other
                parameters are passed to the search method.

        Returns:
            MemoryQueryResult containing matching memories
        """
        # Convert query to string if it's MemoryContent
        if isinstance(query, MemoryContent):
            query_str = str(query.content)
        else:
            query_str = query

        # Extract limit from kwargs for backward compatibility
        limit = kwargs.pop("limit", 5)
        scope = kwargs.pop("scope", "edges")

        results: list[MemoryContent] = []

        try:
            items = await search_scope(
                self._client,
                graph_uuid=self._graph_uuid,
                query=query_str,
                scope=scope,
                limit=limit,
                filters=self._search_filters,
                **kwargs,
            )
            results = results_to_memory_content(items, scope, source="graph")

        except Exception as e:
            # Log error but don't fail completely
            self._logger.error(f"Error querying Zep memory: {e}")

        return MemoryQueryResult(results=results)

    async def _retrieve_graph_context(self) -> MemoryContent | None:
        """Build a context block from the most recent episodes of the graph.

        The two most recent episodes form the query, and ``graph.get_context``
        returns the composed context block for that query.
        """
        pager = await self._client.graph.episode.list(self._graph_uuid, limit=2)
        recent_episodes = await collect(pager, 2)
        if not recent_episodes:
            return None

        query = ""
        for episode in recent_episodes:
            query += f"{episode.content}\n"

        # trim query to 400 chars
        query = query[-400:]

        context_args: dict[str, Any] = {"query": query}
        if self._search_filters is not None:
            context_args["filters"] = self._search_filters
        if self._max_characters is not None:
            context_args["max_characters"] = self._max_characters

        response = await self._client.graph.get_context(self._graph_uuid, **context_args)
        if not response.context:
            return None

        return MemoryContent(
            content=response.context,
            mime_type=MemoryMimeType.TEXT,
            metadata={"source": "graph_context"},
        )

    async def update_context(self, model_context: ChatCompletionContext) -> UpdateContextResult:
        """
        Update the agent's model context with retrieved memories.

        Gets memory from Zep, and if memory exists, adds the memory context
        as a system message.

        Args:
            model_context: The model context to update

        Returns:
            UpdateContextResult with the memories that were retrieved
        """
        try:
            # Get messages from current context
            messages = await model_context.get_messages()
            if not messages:
                return UpdateContextResult(memories=MemoryQueryResult(results=[]))
            graph_context = await self._retrieve_graph_context()
            if not graph_context:
                return UpdateContextResult(memories=MemoryQueryResult(results=[]))
            await model_context.add_message(SystemMessage(content=str(graph_context.content)))
            return UpdateContextResult(memories=MemoryQueryResult(results=[graph_context]))

        except Exception as e:
            # Log error but don't fail completely
            self._logger.error(f"Error updating context with Zep memory: {e}")
            return UpdateContextResult(memories=MemoryQueryResult(results=[]))

    async def clear(self) -> None:
        """
        Clear all memories from Zep storage by deleting the graph.

        This will delete the entire graph and all its data.
        Note: This operation cannot be undone. Zep runs the delete
        asynchronously and returns a task.
        """
        try:
            await self._client.graph.delete(self._graph_uuid)
        except Exception as e:
            self._logger.error(f"Error clearing Zep graph: {e}")
            raise

    async def close(self) -> None:
        """
        Clean up Zep client resources.

        Note: This method does not close the AsyncZep instance since it was
        provided externally. The caller is responsible for managing the client lifecycle.
        """
        # The client was provided externally, so we don't close it here
        # The caller is responsible for closing the client when appropriate
        pass
