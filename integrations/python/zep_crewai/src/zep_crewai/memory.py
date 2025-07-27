"""
Zep Memory integration for CrewAI.

This module provides memory storage that integrates Zep with CrewAI's memory system.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from crewai.memory.storage.interface import Storage
from zep_cloud.client import Zep
from zep_cloud.types import GraphSearchResults, Message


class ZepStorage(Storage):
    """
    A storage implementation that integrates with Zep for persistent storage
    and retrieval of CrewAI agent memories.
    """

    def __init__(self, client: Zep, user_id: str, thread_id: str, **kwargs: Any) -> None:
        """
        Initialize ZepStorage with a Zep client instance.

        Args:
            client: An initialized Zep instance (sync client)
            user_id: User ID identifying a created Zep user (required)
            thread_id: Thread ID identifying current conversation thread (required)
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
        self._config = kwargs

        self._logger = logging.getLogger(__name__)

    def save(self, value: Any, metadata: dict[str, Any] | None = None) -> None:
        """
        Save a memory entry to Zep using metadata-based routing.

        Routes storage based on metadata.type:
        - "message": Store as thread message with role from metadata
        - "json" or "text": Store as graph data

        Args:
            value: The memory content to store
            metadata: Metadata including type, role, name, etc.
        """
        metadata = metadata or {}

        content_str = str(value)
        content_type = metadata.get("type", "text")
        if content_type not in ["message", "json", "text"]:
            content_type = "text"

        try:
            if content_type == "message":
                message_metadata = metadata.copy()
                role = message_metadata.get("role", "norole")
                name = message_metadata.get("name")

                message = Message(
                    role=role,
                    name=name,
                    content=content_str,
                )

                self._client.thread.add_messages(thread_id=self._thread_id, messages=[message])

                self._logger.debug(
                    f"Saved message from {metadata.get('name', 'unknown')}: {content_str[:100]}..."
                )

            else:
                self._client.graph.add(
                    user_id=self._user_id,
                    data=content_str,
                    type=content_type,
                )

                self._logger.debug(f"Saved {content_type} data: {content_str[:100]}...")

        except Exception as e:
            self._logger.error(f"Error saving to Zep: {e}")
            raise

    def search(
        self, query: str, limit: int = 5, score_threshold: float = 0.5
    ) -> dict[str, Any] | list[Any]:
        """
        Search Zep user graph.

        This always retrieves thread-specific context and performs targeted graph search on the user graph
        using the provided query, combining both sources.

        Args:
            query: Search query string (truncated to 400 chars max for graph search)
            limit: Maximum number of results to return from graph search

        Returns:
            List of matching memory entries from both thread context and graph search
        """
        results: list[dict[str, Any]] = []

        # Truncate query to max 400 characters to avoid API errors
        truncated_query = query[:400] if len(query) > 400 else query

        # Define search functions for concurrent execution
        def get_thread_context() -> Any:
            try:
                return self._client.thread.get_user_context(thread_id=self._thread_id)
            except Exception as e:
                self._logger.debug(f"Thread context not available: {e}")
                return None

        def search_graph_edges() -> list[str]:
            try:
                if not query:
                    return []
                results: GraphSearchResults = self._client.graph.search(
                    user_id=self._user_id, query=truncated_query, limit=limit, scope="edges"
                )
                edges: list[str] = []
                if results.edges:
                    for edge in results.edges:
                        edge_str = f"{edge.fact} (valid_at: {edge.valid_at}, invalid_at: {edge.invalid_at or 'current'})"
                        edges.append(edge_str)
                return edges
            except Exception as e:
                self._logger.debug(f"Graph search not available: {e}")
                return []

        thread_context = None
        edges_search_results: list[str] = []

        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                future_thread = executor.submit(get_thread_context)
                future_edges = executor.submit(search_graph_edges)

                thread_context = future_thread.result()
                edges_search_results = future_edges.result() or []

        except Exception as e:
            self._logger.debug(f"Failed to search user memories: {e}")

        if thread_context and hasattr(thread_context, "context") and thread_context.context:
            results.append({"memory": thread_context.context})

        for result in edges_search_results:
            results.append({"memory": result})

        return results

    def reset(self) -> None:
        pass

    @property
    def user_id(self) -> str:
        """Get the user ID."""
        return self._user_id

    @property
    def thread_id(self) -> str:
        """Get the thread ID."""
        return self._thread_id
