"""
Zep Memory integration for CrewAI.

This module provides memory storage that integrates Zep with CrewAI's memory system.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from zep_cloud.client import Zep
from zep_cloud.types import GraphSearchResults, Message

from .limits import truncate_graph_data, truncate_message_content
from .provisioning import UserSetupHook
from .provisioning import ensure_thread as _ensure_thread
from .provisioning import ensure_user as _ensure_user


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
        **Lazy provisioning.** Like :class:`~zep_crewai.user_storage.ZepUserStorage`,
        the Zep user and thread are created lazily, on first use, by the
        private ``_ensure_user_and_thread()`` -- called internally from
        :meth:`save` and :meth:`search`, with the result cached on the
        instance. The lazy path is hot-path-wrapped: a genuine provisioning
        failure (or an ``on_created`` hook failure) is logged and returns
        ``False``, never raised into :meth:`save`/:meth:`search`. Callers who
        want provisioning failures to surface loudly should call
        :func:`zep_crewai.provisioning.ensure_user` and
        :func:`zep_crewai.provisioning.ensure_thread` directly, out-of-band.
    """

    def __init__(
        self,
        client: Zep,
        user_id: str,
        thread_id: str,
        *,
        first_name: str | None = None,
        last_name: str | None = None,
        email: str | None = None,
        on_created: UserSetupHook | None = None,
        **kwargs: Any,
    ) -> None:
        """
        Initialize ZepStorage with a Zep client instance.

        Args:
            client: An initialized Zep instance (sync client)
            user_id: User ID identifying a created Zep user (required)
            thread_id: Thread ID identifying current conversation thread (required)
            first_name: Optional first name, passed to ``user.add`` during lazy
                provisioning.
            last_name: Optional last name, passed to ``user.add`` during lazy
                provisioning.
            email: Optional email, passed to ``user.add`` during lazy provisioning.
            on_created: Optional hook invoked exactly once, right after a new
                Zep user is created during lazy provisioning. Does not fire
                for users that already exist. See
                :func:`zep_crewai.provisioning.ensure_user` for the hook
                contract; on this lazy path a hook failure is logged and
                swallowed rather than raised.
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
        self._first_name = first_name
        self._last_name = last_name
        self._email = email
        self._on_created = on_created
        self._config = kwargs

        self._logger = logging.getLogger(__name__)

        # Whether the Zep user and thread have been created (or confirmed to
        # already exist). Cached so repeated calls do not re-issue setup
        # calls.
        self._user_ready = False
        self._thread_ready = False

    def _ensure_user_and_thread(self) -> bool:
        """Lazily create the Zep user and thread, hot-path-wrapped.

        Every failure here -- including an ``on_created`` hook failure -- is
        logged and swallowed so a Zep or setup-code outage never raises into
        :meth:`save`/:meth:`search`. The result is cached on the instance.

        Returns:
            ``True`` if the user and thread are ready, ``False`` on a
            genuine failure.
        """
        if not self._user_ready:
            try:
                _ensure_user(
                    self._client,
                    user_id=self._user_id,
                    first_name=self._first_name,
                    last_name=self._last_name,
                    email=self._email,
                    on_created=self._on_created,
                )
                self._user_ready = True
            except Exception as exc:
                self._logger.warning(f"Failed to create Zep user {self._user_id}: {exc}")
                return False

        if not self._thread_ready:
            try:
                _ensure_thread(self._client, thread_id=self._thread_id, user_id=self._user_id)
                self._thread_ready = True
            except Exception as exc:
                self._logger.warning(f"Failed to create Zep thread {self._thread_id}: {exc}")
                return False

        return True

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

        if not self._ensure_user_and_thread():
            return

        try:
            if content_type == "message":
                message_metadata = metadata.copy()
                role = message_metadata.get("role", "norole")
                name = message_metadata.get("name")

                message = Message(
                    role=role,
                    name=name,
                    content=truncate_message_content(content_str),
                )

                self._client.thread.add_messages(thread_id=self._thread_id, messages=[message])

                self._logger.debug(
                    f"Saved message from {metadata.get('name', 'unknown')}: {content_str[:100]}..."
                )

            else:
                self._client.graph.add(
                    user_id=self._user_id,
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

        self._ensure_user_and_thread()

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
            results.append({"context": thread_context.context})

        for result in edges_search_results:
            results.append({"context": result})

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
