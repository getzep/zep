"""
Zep User Storage for CrewAI.

This module provides user-specific storage that integrates Zep's user graph
and thread capabilities with CrewAI's memory system.
"""

import logging
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from zep_cloud.client import Zep
from zep_cloud.types import AddMessage, SearchFilters

from .limits import truncate_graph_data, truncate_message_content
from .utils import DEFAULT_CONTEXT_TEMPLATE, compose_graph_context

__all__ = [
    "ZepUserStorage",
    "ContextInput",
    "ContextBuilder",
    "DEFAULT_CONTEXT_TEMPLATE",
]


@dataclass(frozen=True)
class ContextInput:
    """Input handed to a custom context builder.

    Bundling the builder's inputs into a single frozen dataclass (rather than
    positional arguments) lets us add fields later without breaking existing
    builders.

    Unlike some sibling integrations, there is no framework-object field here
    (no agent, crew, or task): the storage adapters are framework-agnostic --
    CrewAI 1.x has no memory extension point that would hand one to us -- so
    ``ContextInput`` carries only the Zep call inputs.

    Attributes:
        zep: The ``Zep`` client in use by this storage adapter.
        user_uuid: The UUID of the Zep user this storage is scoped to.
        thread_uuid: The UUID of the Zep thread this storage records the
            conversation in.
        graph_uuid: The UUID of the user graph.
        user_message: The search query text ``search()`` was called with.

    Example:
        A builder that searches the edges of the user graph instead of
        retrieving the Context Block::

            def my_builder(ctx: ContextInput) -> str | None:
                edges = list(
                    ctx.zep.graph.search_edges(
                        ctx.graph_uuid,
                        query=ctx.user_message,
                        limit=10,
                    )
                )
                if not edges:
                    return None
                return "\\n".join(edge.fact for edge in edges if edge.fact)

            storage = ZepUserStorage(
                zep,
                user_uuid=user.uuid_,
                thread_uuid=thread.uuid_,
                context_builder=my_builder,
            )
    """

    zep: Zep
    user_uuid: str
    thread_uuid: str
    graph_uuid: str
    user_message: str


#: Type alias for a custom context builder function.
#:
#: A context builder receives a single :class:`ContextInput` and returns the
#: context string to use (or ``None`` to return no results). Sync, matching
#: this package's synchronous ``Zep`` client -- there is no ``asyncio.gather``
#: here to run it concurrently with anything, since persistence (``save``) is
#: a separate, caller-driven call in CrewAI's model rather than one turn the
#: storage adapter owns.
#:
#: Error semantics: if the builder raises, :meth:`ZepUserStorage.search` logs
#: a warning and degrades to an empty result -- it never lets the builder's
#: exception propagate.
ContextBuilder = Callable[[ContextInput], str | None]


class ZepUserStorage:
    """
    Storage implementation for Zep's user-specific graphs and threads.

    This class provides persistent storage and retrieval of user-specific memories
    and conversations using Zep's user graph and thread capabilities.

    Standalone Zep storage adapter retaining the historical
    ``save`` / ``search`` / ``reset`` contract. CrewAI 1.x removed
    ``crewai.memory.storage.interface.Storage``, so this no longer subclasses a
    CrewAI base.

    Note:
        **No provisioning on the turn path.** Zep v4 addresses a user and a
        thread by UUID, so this class never creates a user or a thread.
        Create both one time, out-of-band, with
        :func:`zep_crewai.provisioning.ensure_user` and
        :func:`zep_crewai.provisioning.ensure_thread`, store the UUIDs in
        your own database, and pass the UUIDs here.
    """

    def __init__(
        self,
        client: Zep,
        user_uuid: str,
        thread_uuid: str,
        search_filters: SearchFilters | None = None,
        max_characters: int | None = None,
        mode: Literal["summary", "basic"] | None = None,
        *,
        graph_uuid: str | None = None,
        context_builder: ContextBuilder | None = None,
        context_template: str = DEFAULT_CONTEXT_TEMPLATE,
        **kwargs: Any,
    ) -> None:
        """
        Initialize ZepUserStorage with a Zep client instance.

        Args:
            client: An initialized Zep instance (sync client)
            user_uuid: The UUID of an existing Zep user (required)
            thread_uuid: The UUID of an existing Zep thread (required)
            search_filters: Optional filters for search operations
            max_characters: Optional maximum length of the Context Block that
                :meth:`search` retrieves
            mode: Deprecated and ignored. Zep removed the thread context
                ``mode`` ("summary"/"basic") option; the Context Block is now
                auto-assembled. Accepted for backward compatibility only.
            graph_uuid: Optional UUID of the user graph. When the caller does
                not pass it, the storage reads it one time from
                ``user.get(user_uuid)`` and caches it on the instance.
            context_builder: Optional callable that replaces the default
                thread-context + edges composition used by :meth:`search`.
                Receives a single :class:`ContextInput`. See :data:`ContextBuilder`
                for the full error-isolation contract.
            context_template: Template used to wrap context built by
                ``context_builder``. Must contain a literal ``{context}``
                placeholder, replaced via plain string replacement (never
                ``str.format``). Defaults to :data:`DEFAULT_CONTEXT_TEMPLATE`.
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
        self._search_filters = search_filters
        self._max_characters = max_characters
        self._graph_uuid = graph_uuid
        self._context_builder = context_builder
        self._context_template = context_template
        self._config = kwargs

        self._logger = logging.getLogger(__name__)

        if mode is not None:
            warnings.warn(
                "The 'mode' argument is deprecated and ignored: Zep removed the "
                "thread context mode option and auto-assembles the Context Block.",
                DeprecationWarning,
                stacklevel=2,
            )

    def _resolve_graph_uuid(self) -> str | None:
        """Return the UUID of the user graph, and cache it on the instance.

        The caller can pass ``graph_uuid`` to the constructor and avoid this
        call. When the caller does not pass it, the storage reads the user
        one time with ``user.get(user_uuid)``. A failure is logged and
        returns ``None``, so a Zep outage never raises into
        :meth:`save`/:meth:`search`.
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

                message = AddMessage(
                    role=role,
                    name=name,
                    content=truncate_message_content(content_str),
                )

                self._client.thread.add_messages(self._thread_uuid, messages=[message])

                self._logger.debug(
                    f"Saved message to thread {self._thread_uuid} from {name or role}: {content_str[:100]}..."
                )

            else:
                # Store in the user graph
                graph_uuid = self._resolve_graph_uuid()
                if not graph_uuid:
                    return

                self._client.graph.episode.add(
                    graph_uuid,
                    data=truncate_graph_data(content_str),
                    type=content_type,
                )

                self._logger.debug(
                    f"Saved {content_type} data to user graph {graph_uuid}: {content_str[:100]}..."
                )

        except Exception as e:
            # Log-only: a Zep failure here must never propagate into the
            # crew's execution. Callers that need to know about persistence
            # failures should inspect logs; save() always returns normally.
            self._logger.error(f"Error saving to Zep user storage: {e}")

    def search(
        self, query: str, limit: int = 10, score_threshold: float = 0.0
    ) -> dict[str, Any] | list[Any]:
        """
        Search the user's graph and return composed context.

        If ``context_builder`` is set, it entirely **replaces** the default
        retrieval below (see :data:`ContextBuilder`): ``graph.get_context``
        is not called by this method in that case. Otherwise, the method
        retrieves the Context Block of the user graph and wraps it in
        ``context_template``.

        Args:
            query: Search query string from the agent
            limit: Accepted for interface compatibility. Zep v4 controls the
                size of the Context Block with ``max_characters``.
            score_threshold: Minimum relevance score (not used in Zep, kept for interface compatibility)

        Returns:
            List with context results from user storage. Empty when no
            context is available (builder returned ``None``/raised, or the
            default retrieval found nothing).
        """
        graph_uuid = self._resolve_graph_uuid()
        if not graph_uuid:
            return []

        if self._context_builder is not None:
            try:
                context = self._context_builder(
                    ContextInput(
                        zep=self._client,
                        user_uuid=self._user_uuid,
                        thread_uuid=self._thread_uuid,
                        graph_uuid=graph_uuid,
                        user_message=query,
                    )
                )
            except Exception as exc:
                self._logger.warning(f"Custom context_builder raised — skipping context: {exc}")
                return []

            if not context:
                self._logger.info(f"No results found for query: {query}")
                return []

            return [
                {
                    "context": self._context_template.replace("{context}", context),
                    "type": "user_graph_context",
                    "source": "user_graph",
                    "query": query,
                }
            ]

        try:
            context = compose_graph_context(
                client=self._client,
                query=query,
                graph_uuid=graph_uuid,
                max_characters=self._max_characters,
                search_filters=self._search_filters,
                context_template=self._context_template,
            )

            if context:
                self._logger.info(f"Composed context for query: {query}")
                return [
                    {
                        "context": context,
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
        Get the Context Block of the thread through ``thread.get_context``.

        Returns:
            The context string if available, None otherwise.
        """
        if not self._thread_uuid:
            return None

        try:
            response = self._client.thread.get_context(self._thread_uuid)
            return response.context or None

        except Exception as e:
            self._logger.error(f"Error getting context from thread: {e}")
            return None

    def reset(self) -> None:
        """Reset is not implemented for user storage as it should persist."""
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
