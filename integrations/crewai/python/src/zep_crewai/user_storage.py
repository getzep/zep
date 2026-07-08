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
from zep_cloud.types import Message, SearchFilters

from .limits import truncate_graph_data, truncate_message_content
from .provisioning import UserSetupHook
from .provisioning import ensure_thread as _ensure_thread
from .provisioning import ensure_user as _ensure_user
from .utils import DEFAULT_CONTEXT_TEMPLATE, search_graph_and_compose_context

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
        user_id: The Zep user ID this storage is scoped to.
        thread_id: The Zep thread ID this storage records the conversation in.
        user_message: The search query text ``search()`` was called with.

    Example:
        A builder that searches the user's graph with a pinned scope instead
        of using the default thread-context + edges composition::

            def my_builder(ctx: ContextInput) -> str | None:
                results = ctx.zep.graph.search(
                    user_id=ctx.user_id,
                    query=ctx.user_message,
                    scope="edges",
                )
                if not results.edges:
                    return None
                return "\\n".join(edge.fact for edge in results.edges)

            storage = ZepUserStorage(
                zep, user_id="user-123", thread_id="thread-abc",
                context_builder=my_builder,
            )
    """

    zep: Zep
    user_id: str
    thread_id: str
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
        **Lazy provisioning.** The Zep user and thread are created lazily, on
        first use, by the private ``_ensure_user_and_thread()`` -- called
        internally from :meth:`save` and :meth:`search`. The result is cached
        on the instance, so repeated calls incur no extra setup round-trips.
        This lazy path is hot-path-wrapped: a genuine provisioning failure (or
        an ``on_created`` hook failure) is logged and returns ``False``, never
        raised into :meth:`save`/:meth:`search`. Callers who want provisioning
        failures to surface loudly should call
        :func:`zep_crewai.provisioning.ensure_user` and
        :func:`zep_crewai.provisioning.ensure_thread` directly, out-of-band,
        before the first turn.
    """

    def __init__(
        self,
        client: Zep,
        user_id: str,
        thread_id: str,
        search_filters: SearchFilters | None = None,
        facts_limit: int = 20,
        entity_limit: int = 5,
        mode: Literal["summary", "basic"] | None = None,
        *,
        first_name: str | None = None,
        last_name: str | None = None,
        email: str | None = None,
        on_created: UserSetupHook | None = None,
        context_builder: ContextBuilder | None = None,
        context_template: str = DEFAULT_CONTEXT_TEMPLATE,
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
            mode: Deprecated and ignored. Zep V3 removed the thread context
                ``mode`` ("summary"/"basic") option; the Context Block is now
                auto-assembled. Accepted for backward compatibility only.
            first_name: Optional first name, passed to ``user.add`` during lazy
                provisioning. Helps Zep anchor the user's identity node in the graph.
            last_name: Optional last name, passed to ``user.add`` during lazy provisioning.
            email: Optional email, passed to ``user.add`` during lazy provisioning.
            on_created: Optional hook invoked exactly once, right after a new
                Zep user is created during lazy provisioning. Use it to configure
                per-user ontology, custom instructions, or user summary instructions.
                Does not fire for users that already exist. See
                :func:`zep_crewai.provisioning.ensure_user` for the hook contract;
                note that on this lazy path, a hook failure is logged and swallowed
                rather than raised (see the class-level "Lazy provisioning" note).
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
        self._first_name = first_name
        self._last_name = last_name
        self._email = email
        self._on_created = on_created
        self._context_builder = context_builder
        self._context_template = context_template
        self._config = kwargs

        self._logger = logging.getLogger(__name__)

        # Whether the Zep user and thread have been created (or confirmed to
        # already exist). Cached so repeated calls do not re-issue setup
        # calls.
        self._user_ready = False
        self._thread_ready = False

        if mode is not None:
            warnings.warn(
                "The 'mode' argument is deprecated and ignored: Zep V3 removed the "
                "thread context mode option and auto-assembles the Context Block.",
                DeprecationWarning,
                stacklevel=2,
            )

    def _ensure_user_and_thread(self) -> bool:
        """Lazily create the Zep user and thread, hot-path-wrapped.

        Unlike calling :func:`zep_crewai.provisioning.ensure_user` /
        :func:`~.provisioning.ensure_thread` directly (where a genuine
        failure or an ``on_created`` hook error propagates to the caller),
        every failure here -- including a hook failure -- is logged and
        swallowed so a Zep or setup-code outage never raises into
        :meth:`save`/:meth:`search`.

        The result is cached on the instance: subsequent calls are no-ops
        once the user and thread are confirmed ready.

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

        if not self._ensure_user_and_thread():
            return

        try:
            if content_type == "message":
                # Store as thread message
                role = metadata.get("role", "user")
                name = metadata.get("name")

                message = Message(
                    role=role,
                    name=name,
                    content=truncate_message_content(content_str),
                )

                self._client.thread.add_messages(thread_id=self._thread_id, messages=[message])

                self._logger.debug(
                    f"Saved message to thread {self._thread_id} from {name or role}: {content_str[:100]}..."
                )

            else:
                # Store in user graph
                self._client.graph.add(
                    user_id=self._user_id,
                    data=truncate_graph_data(content_str),
                    type=content_type,
                )

                self._logger.debug(
                    f"Saved {content_type} data to user graph {self._user_id}: {content_str[:100]}..."
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
        thread-context + edges composition below (see :data:`ContextBuilder`):
        neither ``thread.get_user_context`` nor ``graph.search`` is called by
        this method in that case. Otherwise, performs parallel searches
        across edges, nodes, and episodes in the user graph, then returns a
        composed context string wrapped in ``context_template``.

        Args:
            query: Search query string from the agent
            limit: Maximum number of results per scope
            score_threshold: Minimum relevance score (not used in Zep, kept for interface compatibility)

        Returns:
            List with context results from user storage. Empty when no
            context is available (builder returned ``None``/raised, or the
            default retrieval found nothing).
        """
        self._ensure_user_and_thread()

        if self._context_builder is not None:
            try:
                context = self._context_builder(
                    ContextInput(
                        zep=self._client,
                        user_id=self._user_id,
                        thread_id=self._thread_id,
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
            # Use the shared utility function for graph search and context composition
            context = search_graph_and_compose_context(
                client=self._client,
                query=query,
                user_id=self._user_id,
                facts_limit=self._facts_limit,
                entity_limit=self._entity_limit,
                episodes_limit=limit,
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
        Get context from the thread using get_user_context.

        Returns:
            The context string if available, None otherwise.
        """
        if not self._thread_id:
            return None

        try:
            context = self._client.thread.get_user_context(thread_id=self._thread_id)

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
