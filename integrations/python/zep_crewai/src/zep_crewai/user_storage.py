"""
Zep User Storage for CrewAI.

This module provides user-specific storage that integrates Zep's user graph
and thread capabilities with CrewAI's memory system.
"""

import logging
from typing import Any, Literal

from crewai.memory.storage.interface import Storage
from zep_cloud.client import Zep
from zep_cloud.types import Message, SearchFilters

from .utils import search_graph_and_compose_context


class ZepUserStorage(Storage):
    """
    Storage implementation for Zep's user-specific graphs and threads.

    This class provides persistent storage and retrieval of user-specific memories
    and conversations using Zep's user graph and thread capabilities.
    """

    def __init__(
        self,
        client: Zep,
        user_id: str,
        thread_id: str,
        search_filters: SearchFilters | None = None,
        facts_limit: int = 20,
        entity_limit: int = 5,
        mode: Literal["summary", "basic"] = "summary",
        **kwargs: Any,
    ) -> None:
        """
        Initialize ZepUserStorage with a Zep client instance.

        Args:
            client: An initialized Zep instance (sync client)
            user_id: User ID identifying a created Zep user (required)
            thread_id: Thread ID for conversation context (required)
            search_filters: Optional filters for search operations
            facts_limit: Maximum number of facts (edges) to retrieve for context
            entity_limit: Maximum number of entities (nodes) to retrieve for context
            mode: Mode for thread context retrieval ("summary" or "basic")
            **kwargs: Additional configuration options
        """
        if not isinstance(client, Zep):
            raise TypeError("client must be an instance of Zep")

        if not user_id:
            raise ValueError("user_id is required")

        if not thread_id:
            raise ValueError("thread_id is required")

        self._client = client
        self._user_id = user_id
        self._thread_id = thread_id
        self._search_filters = search_filters
        self._facts_limit = facts_limit
        self._entity_limit = entity_limit
        self._mode = mode
        self._config = kwargs

        self._logger = logging.getLogger(__name__)

    def save(self, value: Any, metadata: dict[str, Any] | None = None) -> None:
        """
        Save data to the user's graph or thread.

        Routes storage based on metadata.type:
        - "message": Store as thread message (requires thread_id)
        - "json": Store as JSON data in user graph
        - "text": Store as text data in user graph (default)

        Args:
            value: The content to store
            metadata: Metadata including type, role, name, etc.
        """
        metadata = metadata or {}
        content_str = str(value)
        content_type = metadata.get("type", "text")

        # Validate content type
        if content_type not in ["message", "json", "text"]:
            content_type = "text"

        try:
            if content_type == "message":
                # Store as thread message
                role = metadata.get("role", "user")
                name = metadata.get("name")

                message = Message(
                    role=role,
                    name=name,
                    content=content_str,
                )

                self._client.thread.add_messages(thread_id=self._thread_id, messages=[message])

                self._logger.debug(
                    f"Saved message to thread {self._thread_id} from {name or role}: {content_str[:100]}..."
                )

            else:
                # Store in user graph
                self._client.graph.add(
                    user_id=self._user_id,
                    data=content_str,
                    type=content_type,
                )

                self._logger.debug(
                    f"Saved {content_type} data to user graph {self._user_id}: {content_str[:100]}..."
                )

        except Exception as e:
            self._logger.error(f"Error saving to Zep user storage: {e}")
            raise

    def search(
        self, query: str, limit: int = 10, score_threshold: float = 0.0
    ) -> dict[str, Any] | list[Any]:
        """
        Search the user's graph and return composed context.

        Performs parallel searches across edges, nodes, and episodes in the user graph,
        then returns composed context string.

        Args:
            query: Search query string from the agent
            limit: Maximum number of results per scope
            score_threshold: Minimum relevance score (not used in Zep, kept for interface compatibility)

        Returns:
            List with context results from user storage
        """
        try:
            # Use the shared utility function for graph search and context composition
            context = search_graph_and_compose_context(
                client=self._client,
                query=query,
                user_id=self._user_id,
                facts_limit=self._facts_limit,
                entity_limit=self._entity_limit,
                episodes_limit=limit,
                search_filters=self._search_filters,
            )

            if context:
                self._logger.info(f"Composed context for query: {query}")
                return [
                    {
                        "memory": context,
                        "type": "user_graph_context",
                        "source": "user_graph",
                        "query": query,
                    }
                ]

            self._logger.info(f"No results found for query: {query}")
            return []

        except Exception as e:
            self._logger.error(f"Error searching user graph: {e}")
            return []

    def get_context(self) -> str | None:
        """
        Get context from the thread using get_user_context.

        Returns:
            The context string if available, None otherwise.
        """
        if not self._thread_id:
            return None

        try:
            context = self._client.thread.get_user_context(
                thread_id=self._thread_id, mode=self._mode
            )

            # Return the context string if available
            if context and hasattr(context, "context"):
                return context.context
            return None

        except Exception as e:
            self._logger.error(f"Error getting context from thread: {e}")
            return None

    def reset(self) -> None:
        """Reset is not implemented for user storage as it should persist."""
        pass

    @property
    def user_id(self) -> str:
        """Get the user ID."""
        return self._user_id

    @property
    def thread_id(self) -> str:
        """Get the thread ID."""
        return self._thread_id
