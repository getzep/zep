"""
Zep Graph Storage for CrewAI.

This module provides graph-based storage that integrates Zep's knowledge graph
capabilities with CrewAI's memory system.
"""

import logging
from typing import Any

from crewai.memory.storage.interface import Storage
from zep_cloud.client import Zep
from zep_cloud.types import SearchFilters

from .utils import search_graph_and_compose_context


class ZepGraphStorage(Storage):
    """
    Storage implementation for Zep's generic knowledge graphs.

    This class provides persistent storage and retrieval of structured knowledge
    using Zep's graph capabilities for non-user-specific data.
    """

    def __init__(
        self,
        client: Zep,
        graph_id: str,
        search_filters: SearchFilters | None = None,
        facts_limit: int = 20,
        entity_limit: int = 5,
        **kwargs: Any,
    ) -> None:
        """
        Initialize ZepGraphStorage with a Zep client instance.

        Args:
            client: An initialized Zep instance (sync client)
            graph_id: Identifier for the knowledge graph
            search_filters: Optional filters for search operations
            facts_limit: Maximum number of facts (edges) to retrieve for context
            entity_limit: Maximum number of entities (nodes) to retrieve for context
            **kwargs: Additional configuration options
        """
        if not isinstance(client, Zep):
            raise TypeError("client must be an instance of Zep")

        if not graph_id:
            raise ValueError("graph_id is required")

        self._client = client
        self._graph_id = graph_id
        self._search_filters = search_filters
        self._facts_limit = facts_limit
        self._entity_limit = entity_limit
        self._config = kwargs

        self._logger = logging.getLogger(__name__)

    def save(self, value: Any, metadata: dict[str, Any] | None = None) -> None:
        """
        Save data to the Zep knowledge graph.

        Routes storage based on metadata.type:
        - "json": Store as JSON data
        - "text": Store as text data (default)
        - "message": Store as message data

        Args:
            value: The content to store
            metadata: Metadata including type information
        """
        metadata = metadata or {}
        content_str = str(value)
        content_type = metadata.get("type", "text")

        # Validate content type
        if content_type not in ["json", "text", "message"]:
            content_type = "text"

        try:
            # Add data to the graph
            self._client.graph.add(
                graph_id=self._graph_id,
                data=content_str,
                type=content_type,
            )

            self._logger.debug(
                f"Saved {content_type} data to graph {self._graph_id}: {content_str[:100]}..."
            )

        except Exception as e:
            self._logger.error(f"Error saving to Zep graph: {e}")
            raise

    def search(
        self, query: str, limit: int = 10, score_threshold: float = 0.0
    ) -> dict[str, Any] | list[Any]:
        """
        Search the Zep knowledge graph and return composed context.

        Performs parallel searches across edges, nodes, and episodes,
        then returns a composed context string.

        Args:
            query: Search query string from the agent
            limit: Maximum number of results per scope
            score_threshold: Minimum relevance score (not used in Zep, kept for interface compatibility)

        Returns:
            List with a single dict containing the composed context string
        """
        try:
            # Use the shared utility function for graph search and context composition
            context = search_graph_and_compose_context(
                client=self._client,
                query=query,
                graph_id=self._graph_id,
                facts_limit=self._facts_limit,
                entity_limit=self._entity_limit,
                episodes_limit=limit,
                search_filters=self._search_filters,
            )

            if context:
                self._logger.info(f"Composed context for query: {query}")
                return [
                    {"memory": context, "type": "graph_context", "source": "graph", "query": query}
                ]

            self._logger.info(f"No results found for query: {query}")
            return []

        except Exception as e:
            self._logger.error(f"Error searching graph: {e}")
            return []

    def reset(self) -> None:
        """Reset is not implemented for graph storage as graphs should persist."""
        pass

    @property
    def graph_id(self) -> str:
        """Get the graph ID."""
        return self._graph_id
