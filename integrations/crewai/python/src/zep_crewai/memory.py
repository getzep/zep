"""
Zep Memory integration for CrewAI.

This module provides memory storage that integrates Zep with CrewAI's memory system.
"""

import itertools
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from zep_cloud.client import Zep
from zep_cloud.types import AddMessage

from .limits import truncate_graph_data, truncate_message_content


class ZepStorage:
    """
    A storage implementation that integrates with Zep for persistent storage
    and retrieval of CrewAI agent memories.

    Standalone Zep storage adapter. CrewAI 1.x removed
    ``crewai.memory.storage.interface.Storage`` (and the ``ExternalMemory``
    wrapper that consumed it), so this class no longer subclasses a CrewAI base.
    It preserves the historical ``save(value, metadata)`` /
    ``search(query, limit, score_threshold)`` / ``reset()`` contract so existing
    callers continue to work.

    Note:
        **No provisioning on the turn path.** Zep v4 addresses a user and a
        thread by UUID, so this class never creates a user or a thread.
        Create both one time, out-of-band, with
        :func:`zep_crewai.provisioning.ensure_user` and
        :func:`zep_crewai.provisioning.ensure_thread`, and pass the UUIDs
        here.
    """

    def __init__(
        self,
        client: Zep,
        user_uuid: str,
        thread_uuid: str,
        *,
        graph_uuid: str | None = None,
        **kwargs: Any,
    ) -> None:
        """
        Initialize ZepStorage with a Zep client instance.

        Args:
            client: An initialized Zep instance (sync client)
            user_uuid: The UUID of an existing Zep user (required)
            thread_uuid: The UUID of an existing Zep thread (required)
            graph_uuid: Optional UUID of the user graph. When the caller does
                not pass it, the storage reads it one time from
                ``user.get(user_uuid)`` and caches it on the instance.
            **kwargs: Additional configuration options
        """
        if not isinstance(client, Zep):
            raise TypeError("client must be an instance of Zep")

        if not user_uuid:
            raise ValueError("user_uuid is required")

        if not thread_uuid:
            raise ValueError("thread_uuid is required")

        self._client = client
        self._user_uuid = user_uuid
        self._thread_uuid = thread_uuid
        self._graph_uuid = graph_uuid
        self._config = kwargs

        self._logger = logging.getLogger(__name__)

    def _resolve_graph_uuid(self) -> str | None:
        """Return the UUID of the user graph, and cache it on the instance.

        The caller can pass ``graph_uuid`` to the constructor and avoid this
        call. A failure is logged and returns ``None``, so a Zep outage never
        raises into :meth:`save`/:meth:`search`.
        """
        if self._graph_uuid:
            return self._graph_uuid

        try:
            user = self._client.user.get(self._user_uuid)
        except Exception as exc:
            self._logger.warning(f"Failed to read Zep user {self._user_uuid}: {exc}")
            return None

        self._graph_uuid = user.graph_uuid
        return self._graph_uuid

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

                message = AddMessage(
                    role=role,
                    name=name,
                    content=truncate_message_content(content_str),
                )

                self._client.thread.add_messages(self._thread_uuid, messages=[message])

                self._logger.debug(
                    f"Saved message from {metadata.get('name', 'unknown')}: {content_str[:100]}..."
                )

            else:
                graph_uuid = self._resolve_graph_uuid()
                if not graph_uuid:
                    return

                self._client.graph.episode.add(
                    graph_uuid,
                    data=truncate_graph_data(content_str),
                    type=content_type,
                )

                self._logger.debug(f"Saved {content_type} data: {content_str[:100]}...")

        except Exception as e:
            # Log-only: a Zep failure here must never propagate into the
            # crew's execution. Callers that need to know about persistence
            # failures should inspect logs; save() always returns normally.
            self._logger.error(f"Error saving to Zep: {e}")

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
                return self._client.thread.get_context(self._thread_uuid)
            except Exception as e:
                self._logger.debug(f"Thread context not available: {e}")
                return None

        def search_graph_edges() -> list[str]:
            try:
                if not query:
                    return []
                graph_uuid = self._resolve_graph_uuid()
                if not graph_uuid:
                    return []
                pager = self._client.graph.search_edges(
                    graph_uuid, query=truncated_query, limit=limit
                )
                edges: list[str] = []
                for edge in itertools.islice(pager, limit):
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
            results.append({"context": thread_context.context})

        for result in edges_search_results:
            results.append({"context": result})

        return results

    def reset(self) -> None:
        pass

    @property
    def user_uuid(self) -> str:
        """Get the user UUID."""
        return self._user_uuid

    @property
    def thread_uuid(self) -> str:
        """Get the thread UUID."""
        return self._thread_uuid

    @property
    def graph_uuid(self) -> str | None:
        """Get the user graph UUID, when it is known."""
        return self._graph_uuid
