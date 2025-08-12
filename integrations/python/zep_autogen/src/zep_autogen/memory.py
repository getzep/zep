"""
Zep Memory integration for AutoGen.

This module provides memory classes that integrate Zep with AutoGen's memory system.
"""

import logging
import uuid
from typing import Any, Literal

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
from zep_cloud.client import AsyncZep
from zep_cloud.types import Message


class ZepUserMemory(Memory):
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
        user_id: str,
        thread_id: str | None = None,
        thread_context_mode: Literal["basic", "summary"] = "summary",
        **kwargs: Any,
    ) -> None:
        """
        Initialize ZepMemory with an AsyncZep client instance.

        Args:
            client: An initialized AsyncZep instance
            user_id: User ID for memory isolation (required)
            thread_id: Optional thread ID. If not provided, will be created automatically
            **kwargs: Additional configuration options
        """
        if not isinstance(client, AsyncZep):
            raise TypeError("client must be an instance of AsyncZep")

        if not user_id:
            raise ValueError("user_id is required")

        self._client = client
        self._user_id = user_id
        self._thread_id = thread_id
        self._thread_context_mode = thread_context_mode
        self._config = kwargs

        # Set up module logger
        self._logger = logging.getLogger(__name__)

    async def add(
        self, content: MemoryContent, cancellation_token: CancellationToken | None = None
    ) -> None:
        """
        Add a memory entry to Zep storage.

        Uses metadata.type to determine storage method:
        - type="message": stores as message in thread using thread.add_messages
        - type="data": stores as data in user's graph using graph.add (maps mime type to data type)

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
                f"ZepMemory only supports: {', '.join(str(mt) for mt in supported_mime_types)}"
            )

        # Extract metadata
        metadata_copy = content.metadata.copy() if content.metadata else {}
        content_type = metadata_copy.get("type", "data")  # Default to "data" if no type specified

        if content_type == "message":
            if self._thread_id:
                # Ensure thread exists
                await self._client.thread.get(self._thread_id)

            if not self._thread_id:
                self._thread_id = f"thread_{uuid.uuid4().hex[:16]}"
                await self._client.thread.create(thread_id=self._thread_id, user_id=self._user_id)
            # Store as message in thread session
            role = metadata_copy.get("role", "user")
            name = metadata_copy.get("name")

            message = Message(name=name, content=str(content.content), role=role)

            # Add message to user's thread in Zep
            await self._client.thread.add_messages(thread_id=self._thread_id, messages=[message])

        elif content_type == "data":
            # Store as data in the user's graph - map mime type to Zep data type
            mime_to_data_type: dict[MemoryMimeType, str] = {
                MemoryMimeType.TEXT: "text",
                MemoryMimeType.MARKDOWN: "text",
                MemoryMimeType.JSON: "json",
            }

            # Safely get the data type, handling both MemoryMimeType and string
            if isinstance(content.mime_type, MemoryMimeType):
                data_type = mime_to_data_type.get(content.mime_type, "text")
            else:
                data_type = "text"  # Default for string or unknown types

            # Add data to user's graph
            await self._client.graph.add(
                user_id=self._user_id, type=data_type, data=str(content.content)
            )

        else:
            raise ValueError(
                f"Unsupported metadata type: {content_type}. Supported types: 'message', 'data'"
            )

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
                user_id=self._user_id, query=query_str, limit=limit, **kwargs
            )

            # Add graph search results
            if graph_results.edges:
                for edge in graph_results.edges:
                    results.append(
                        MemoryContent(
                            content=edge.fact,
                            mime_type=MemoryMimeType.TEXT,
                            metadata={
                                "source": "user_graph",
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
                                "source": "user_graph",
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
                                "source": "user_graph",
                                "episode_type": episode.source,
                                "episode_role": episode.role_type,
                                "episode_name": episode.role,
                                "created_at": episode.created_at,
                            },
                        )
                    )
        except Exception as e:
            # Log error but don't fail completely
            self._logger.error(f"Error querying Zep memory: {e}")

        return MemoryQueryResult(results=results)

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

            # Get memory from Zep session
            if not self._thread_id:
                return UpdateContextResult(memories=MemoryQueryResult(results=[]))
            memory_result = await self._client.thread.get_user_context(
                thread_id=self._thread_id,
                mode=self._thread_context_mode,
            )
            thread = await self._client.thread.get(thread_id=self._thread_id, lastn=10)

            memory_contents = []
            memory_parts = []

            # If we have memory context, include it
            if memory_result.context:
                memory_contents.append(
                    MemoryContent(
                        content=memory_result.context,
                        mime_type=MemoryMimeType.TEXT,
                        metadata={"source": "thread_context"},
                    )
                )
                memory_parts.append(f"Memory context: {memory_result.context}")

            # Only include recent messages if we have memory
            if thread.messages:
                message_history = []
                for msg in thread.messages:
                    name_prefix = f"{msg.name} " if msg.name else ""
                    message_history.append(f"{name_prefix}{msg.role}: {msg.content}")
                memory_parts.append("Recent conversation:\n" + "\n".join(message_history))

            # If we have memory parts, add them to the context as a system message
            if memory_parts:
                memory_context = "\n\n".join(memory_parts)
                await model_context.add_message(SystemMessage(content=memory_context))
            return UpdateContextResult(memories=MemoryQueryResult(results=memory_contents))

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
            # Delete the session - this clears all messages and memory for this session
            if self._thread_id:
                await self._client.thread.delete(thread_id=self._thread_id)

        except Exception as e:
            self._logger.error(f"Error clearing Zep memory: {e}")
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
