"""
Zep Memory integration for AutoGen.

This module provides memory classes that integrate Zep with AutoGen's memory system.
"""

import asyncio
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
from zep_cloud import GraphSearchResults, SearchFilters
from zep_cloud.client import AsyncZep
from zep_cloud.graph.utils import compose_context_string


class ZepGraphMemory(Memory):
    """
    A memory implementation that integrates with Zep for persistent storage
    and retrieval of conversation context and agent memories.

    This class implements AutoGen's Memory interface and provides:
    - Automatic context injection via update_context()
    - Manual memory queries via query()
    - Message storage in Zep threads
    - Data storage in Zep user graphs
    """

    def __init__(
        self,
        client: AsyncZep,
        graph_id: str,
        search_filters: SearchFilters | None = None,
        facts_limit: int = 20,
        entity_limit: int = 5,
        **kwargs: Any,
    ) -> None:
        """
        Initialize ZepGraphMemory with an AsyncZep client instance.

        Args:
            client: An initialized AsyncZep instance
            graph_id: Identifier of the graph in Zep (required)
            **kwargs: Additional configuration options
        """
        if not isinstance(client, AsyncZep):
            raise TypeError("client must be an instance of AsyncZep")

        if not graph_id:
            raise ValueError("graph_id is required")

        self._client = client
        self._graph_id = graph_id
        self._search_filters = search_filters
        self._facts_limit = facts_limit
        self._entity_limit = entity_limit

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
            entry: The memory content to store

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

        # Add data to user's graph
        await self._client.graph.add(
            graph_id=self._graph_id, type=data_type, data=str(content.content)
        )

    def _graph_results_to_memory_content(
        self, graph_results: GraphSearchResults
    ) -> list[MemoryContent]:
        """
        Helper method to convert graph search results to MemoryContent.
        """
        results = []
        # Add graph search results
        if graph_results.edges:
            for edge in graph_results.edges:
                results.append(
                    MemoryContent(
                        content=edge.fact,
                        mime_type=MemoryMimeType.TEXT,
                        metadata={
                            "source": "graph",
                            "edge_name": edge.name,
                            "edge_attributes": edge.attributes or {},
                            "created_at": edge.created_at,
                            "expired_at": edge.expired_at,
                            "valid_at": edge.valid_at,
                            "invalid_at": edge.invalid_at,
                        },
                    )
                )
        if graph_results.nodes:
            for node in graph_results.nodes:
                results.append(
                    MemoryContent(
                        content=f"{node.name}:\n {node.summary}",
                        mime_type=MemoryMimeType.TEXT,
                        metadata={
                            "source": "graph",
                            "node_name": node.name,
                            "node_attributes": node.attributes or {},
                            "created_at": node.created_at,
                        },
                    )
                )
        if graph_results.episodes:
            for episode in graph_results.episodes:
                results.append(
                    MemoryContent(
                        content=episode.content,
                        mime_type=MemoryMimeType.TEXT,
                        metadata={
                            "source": "graph",
                            "episode_type": episode.source,
                            "episode_role": episode.role_type,
                            "episode_name": episode.role,
                            "created_at": episode.created_at,
                        },
                    )
                )

        return results

    async def query(
        self,
        query: str | MemoryContent,
        cancellation_token: CancellationToken | None = None,
        **kwargs: Any,
    ) -> MemoryQueryResult:
        """
        Query memories from Zep storage using graph.search.

        Args:
            query: Search query string or MemoryContent
            cancellation_token: Optional cancellation token
            **kwargs: Additional query parameters

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

        results = []

        try:
            # Search the user's graph
            graph_results = await self._client.graph.search(
                graph_id=self._graph_id,
                query=query_str,
                limit=limit,
                search_filters=self._search_filters,
                **kwargs,
            )
            results = self._graph_results_to_memory_content(graph_results)

        except Exception as e:
            # Log error but don't fail completely
            self._logger.error(f"Error querying Zep memory: {e}")

        return MemoryQueryResult(results=results)

    async def _retrieve_graph_context(self) -> MemoryContent | None:
        recent_messages = await self._client.graph.episode.get_by_graph_id(
            graph_id=self._graph_id, lastn=2
        )
        if not recent_messages.episodes:
            return None
        query = ""
        for msg in recent_messages.episodes:
            query += f"{msg.content}\n"

        # trim query to 400 chars
        query = query[-400:]
        search_functions = []

        search_functions.append(
            self._client.graph.search(
                graph_id=self._graph_id,
                query=query,
                limit=self._facts_limit,
                scope="edges",
                search_filters=self._search_filters,
            )
        )
        search_functions.append(
            self._client.graph.search(
                graph_id=self._graph_id,
                query=query,
                limit=self._entity_limit,
                scope="nodes",
                search_filters=self._search_filters,
            )
        )

        results: list[GraphSearchResults] = await asyncio.gather(*search_functions)

        edges = []
        nodes = []

        for result in results:
            if result.edges:
                edges.extend(result.edges)
            if result.nodes:
                nodes.extend(result.nodes)
        if not edges and not nodes:
            return None
        context = compose_context_string(edges, nodes, [])
        return MemoryContent(
            content=context,
            mime_type=MemoryMimeType.TEXT,
            metadata={"source": "graph_context"},
        )

    async def update_context(self, model_context: ChatCompletionContext) -> UpdateContextResult:
        """
        Update the agent's model context with retrieved memories.

        Gets memory from Zep, and if memory exists, includes up to 10 last messages
        from history and adds the memory context as a system message.

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
        Clear all memories from Zep storage by deleting the session.

        This will delete the entire session and all its messages.
        Note: This operation cannot be undone.
        """
        try:
            await self._client.graph.delete(graph_id=self._graph_id)
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
