"""
Zep Memory integration for AutoGen.

This module provides memory classes that integrate Zep with AutoGen's memory system.

``ZepUserMemory`` implements AutoGen's native ``Memory`` interface, which
splits the memory loop into two independently-invoked hooks:

* :meth:`ZepUserMemory.update_context` -- called automatically by AutoGen
  before every model call. This is **injection only**: it retrieves Zep's
  Context Block (or a custom ``context_builder`` result) and adds it to the
  model context as a system message. It never persists anything.
* :meth:`ZepUserMemory.add` -- called explicitly by the application (e.g.
  after each user/assistant turn) to persist a message or graph data. This is
  **persistence only**.

Because AutoGen invokes these as two separate, caller-controlled steps (not a
single "turn" the integration owns), there is no concurrent
persist-and-build-context gather here as in the ADK/pydantic-ai/ms-agent-framework
ports -- ``update_context`` never persists, so there is nothing to run
concurrently with the context builder. See the ``context_builder`` docstring
below for the exact contract.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
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
from zep_cloud.client import AsyncZep
from zep_cloud.types import Message

from .limits import truncate_graph_data, truncate_message_content
from .provisioning import UserSetupHook
from .provisioning import ensure_thread as _ensure_thread
from .provisioning import ensure_user as _ensure_user

logger = logging.getLogger(__name__)

#: Default template used to wrap retrieved Zep context before injecting it
#: into the model context as a system message.  Rendered via plain string
#: replacement (``template.replace("{context}", context_text)``), never
#: ``str.format`` -- so context text or a custom template containing
#: ``{``/``}``/``%`` is always safe to inject.
#:
#: This exact string is canonical across zep-adk's Python, Go, and
#: TypeScript implementations -- keep them in sync.
DEFAULT_CONTEXT_TEMPLATE = (
    "The following context is retrieved from Zep, the agent's long-term memory. "
    "It contains relevant facts, entities, and prior knowledge about the user. "
    "Use it to inform your responses.\n\n"
    "<ZEP_CONTEXT>\n"
    "{context}\n"
    "</ZEP_CONTEXT>"
)


@dataclass(frozen=True)
class ContextInput:
    """Input handed to a custom context builder.

    Bundling the builder's inputs into a single frozen dataclass (rather than
    positional arguments) lets us add fields later without breaking existing
    builders.

    Attributes:
        zep: The ``AsyncZep`` client in use by this memory instance.
        user_id: The Zep user ID this memory is scoped to.
        thread_id: The Zep thread ID this memory records the conversation in.
        user_message: The last user-role message's text content from
            ``model_context.get_messages()``, or ``""`` if none is found.
        model_context: The AutoGen ``ChatCompletionContext`` passed to
            ``update_context()`` for this call.

    Example:
        A builder that searches a per-user graph instead of using the
        thread's default Context Block retrieval::

            async def my_builder(ctx: ContextInput) -> str | None:
                results = await ctx.zep.graph.search(
                    user_id=ctx.user_id,
                    query=ctx.user_message,
                    scope="edges",
                )
                if not results.edges:
                    return None
                return "\\n".join(edge.fact for edge in results.edges)

            memory = ZepUserMemory(
                client=zep,
                user_id="user-123",
                thread_id="thread-abc",
                context_builder=my_builder,
            )
    """

    zep: AsyncZep
    user_id: str
    thread_id: str
    user_message: str
    model_context: ChatCompletionContext


#: Type alias for a custom context builder function.
#:
#: A context builder receives a single :class:`ContextInput` and returns the
#: context string to inject into the model context (or ``None`` to skip
#: injection).
#:
#: Error semantics: if the builder raises, ``ZepUserMemory.update_context``
#: logs a warning and returns an empty ``UpdateContextResult`` -- it never
#: raises into the caller.
#:
#: **Retrieval only.** Unlike the sibling Zep integrations (ADK,
#: Microsoft Agent Framework, Pydantic AI), this builder is never run
#: concurrently with message persistence. AutoGen's ``Memory`` protocol calls
#: ``update_context()`` and ``add()`` as two separate, caller-controlled
#: steps -- ``update_context()`` (where ``context_builder`` runs) never
#: persists a message, so there is nothing to ``asyncio.gather`` it with.
#: Persist turns explicitly via ``add()``.
ContextBuilder = Callable[[ContextInput], Awaitable[str | None]]


class ZepUserMemory(Memory):
    """
    A memory implementation that integrates with Zep for persistent storage
    and retrieval of conversation context and agent memories.

    This class implements AutoGen's Memory interface and provides:
    - Automatic context injection via update_context()
    - Manual memory queries via query()
    - Message storage in Zep threads
    - Data storage in Zep user graphs

    Note:
        **The Zep memory loop, precisely.** Context INJECTION is automatic:
        AutoGen calls ``update_context()`` before every model call. PERSISTENCE
        is NOT automatic: the application must call ``add()`` itself (typically
        once per user turn and once per assistant turn) -- this is AutoGen's
        design, not a limitation of this integration. See the package README
        for the canonical wiring snippet.

    Note:
        **Lazy provisioning.** The Zep user and thread are created lazily, on
        first use, by both ``add()`` and ``update_context()`` -- whichever is
        called first. Creation is idempotent and cached on the instance (see
        ``on_created``), so repeated calls incur no extra setup round-trips.
        This lazy path is hot-path-wrapped: a genuine provisioning failure (or
        an ``on_created`` hook failure) is logged and swallowed, never raised
        into ``add()``/``update_context()``. Callers who want provisioning
        failures to surface loudly should call
        :func:`zep_autogen.provisioning.ensure_user` and
        :func:`zep_autogen.provisioning.ensure_thread` directly, out-of-band,
        before the first turn.
    """

    def __init__(
        self,
        client: AsyncZep,
        user_id: str,
        thread_id: str | None = None,
        context_template_id: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        email: str | None = None,
        on_created: UserSetupHook | None = None,
        context_builder: ContextBuilder | None = None,
        context_template: str = DEFAULT_CONTEXT_TEMPLATE,
        **kwargs: Any,
    ) -> None:
        """
        Initialize ZepMemory with an AsyncZep client instance.

        Args:
            client: An initialized AsyncZep instance
            user_id: User ID for memory isolation (required)
            thread_id: Optional thread ID. If not provided, will be created automatically
            context_template_id: Optional context template ID used to customize the
                rendering of the retrieved Context Block. When omitted, Zep returns the
                default structured Context Block. Ignored when ``context_builder`` is set.
            first_name: Optional first name, passed to ``user.add`` during lazy
                provisioning. Helps Zep anchor the user's identity node in the graph.
            last_name: Optional last name, passed to ``user.add`` during lazy provisioning.
            email: Optional email, passed to ``user.add`` during lazy provisioning.
            on_created: Optional async hook invoked exactly once, right after a new
                Zep user is created during lazy provisioning. Use it to configure
                per-user ontology, custom instructions, or user summary instructions.
                Does not fire for users that already exist. See
                :func:`zep_autogen.provisioning.ensure_user` for the hook contract;
                note that on this lazy path, a hook failure is logged and swallowed
                rather than raised (see the class-level "Lazy provisioning" note).
            context_builder: Optional async callable that constructs the context
                block injected by ``update_context()``, in place of the default
                ``thread.get_user_context(...)`` call. Receives a single
                :class:`ContextInput`. See :data:`ContextBuilder` for the full
                error-isolation contract.
            context_template: Template used to wrap retrieved context before
                injecting it into the model context as a system message. Must
                contain a literal ``{context}`` placeholder, replaced via plain
                string replacement (never ``str.format``). Defaults to
                :data:`DEFAULT_CONTEXT_TEMPLATE`.
            **kwargs: Additional configuration options

        Note:
            The legacy ``thread_context_mode`` ("basic"/"summary") option has been
            removed. In Zep V3 the Context Block is auto-assembled and the ``mode``
            parameter no longer exists. Use ``context_template_id`` together with a
            context template if you need custom rendering of the Context Block.
        """
        if not isinstance(client, AsyncZep):
            raise TypeError("client must be an instance of AsyncZep")

        if not user_id:
            raise ValueError("user_id is required")

        self._client = client
        self._user_id = user_id
        self._thread_id = thread_id
        self._context_template_id = context_template_id
        self._first_name = first_name
        self._last_name = last_name
        self._email = email
        self._on_created = on_created
        self._context_builder = context_builder
        self._context_template = context_template
        self._config = kwargs

        # Whether the Zep user (and, once a thread_id exists, the thread) have
        # been created (or confirmed to already exist). Cached so repeated
        # add()/update_context() calls do not re-issue setup calls.
        self._user_ready = False
        self._thread_ready = False

        # Set up module logger
        self._logger = logging.getLogger(__name__)

    async def _ensure_resources(self) -> bool:
        """Lazily create the Zep user and (if set) thread, hot-path-wrapped.

        Unlike calling :func:`zep_autogen.provisioning.ensure_user` /
        :func:`~.provisioning.ensure_thread` directly (where a genuine failure
        or an ``on_created`` hook error propagates to the caller), every
        failure here -- including a hook failure -- is logged and swallowed so
        a Zep or setup-code outage never raises into ``add()``/
        ``update_context()``.

        Returns:
            ``True`` if the user (and thread, when applicable) are ready,
            ``False`` on a genuine failure.
        """
        if not self._user_ready:
            try:
                await _ensure_user(
                    self._client,
                    user_id=self._user_id,
                    first_name=self._first_name,
                    last_name=self._last_name,
                    email=self._email,
                    on_created=self._on_created,
                )
                self._user_ready = True
            except Exception as exc:
                self._logger.warning("Failed to create Zep user %s: %s", self._user_id, exc)
                return False

        if self._thread_id and not self._thread_ready:
            try:
                await _ensure_thread(self._client, thread_id=self._thread_id, user_id=self._user_id)
                self._thread_ready = True
            except Exception as exc:
                self._logger.warning("Failed to create Zep thread %s: %s", self._thread_id, exc)
                return False

        return True

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
            if not self._thread_id:
                self._thread_id = f"thread_{uuid.uuid4().hex[:16]}"

            if not await self._ensure_resources():
                return

            # Store as message in thread session
            role = metadata_copy.get("role", "user")
            name = metadata_copy.get("name")

            truncated_content = truncate_message_content(str(content.content), label=role)
            message = Message(name=name, content=truncated_content, role=role)

            # Add message to user's thread in Zep
            await self._client.thread.add_messages(thread_id=self._thread_id, messages=[message])

        elif content_type == "data":
            if not await self._ensure_resources():
                return

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
            truncated_data = truncate_graph_data(str(content.content))
            await self._client.graph.add(
                user_id=self._user_id, type=data_type, data=truncated_data
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

    @staticmethod
    async def _last_user_text(model_context: ChatCompletionContext) -> str:
        """Return the last ``UserMessage``'s text content, or ``""`` if none.

        AutoGen's ``LLMMessage`` union discriminates on the ``type`` field
        (``"UserMessage"``, ``"AssistantMessage"``, ``"SystemMessage"``, ...)
        -- ``source`` is a free-form string set by the caller, not a role
        marker, so ``type`` is the reliable way to find the user turn.
        """
        messages = await model_context.get_messages()
        for message in reversed(messages):
            if getattr(message, "type", None) != "UserMessage":
                continue
            content = getattr(message, "content", None)
            if isinstance(content, str) and content:
                return content
        return ""

    async def update_context(self, model_context: ChatCompletionContext) -> UpdateContextResult:
        """
        Update the agent's model context with retrieved memories.

        Injection only -- this method never persists a message. It retrieves
        a context block (via ``context_builder`` if set, otherwise
        ``thread.get_user_context(...)``), wraps it in ``context_template``,
        and appends it to ``model_context`` as a ``SystemMessage``. The Zep
        user/thread are created lazily on first use (see the class docstring).

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

            if not self._thread_id:
                return UpdateContextResult(memories=MemoryQueryResult(results=[]))

            if not await self._ensure_resources():
                return UpdateContextResult(memories=MemoryQueryResult(results=[]))

            memory_contents = []
            memory_parts = []

            if self._context_builder is not None:
                user_message = await self._last_user_text(model_context)
                try:
                    context_text = await self._context_builder(
                        ContextInput(
                            zep=self._client,
                            user_id=self._user_id,
                            thread_id=self._thread_id,
                            user_message=user_message,
                            model_context=model_context,
                        )
                    )
                except Exception as exc:
                    self._logger.warning(
                        "Custom context_builder raised — skipping context injection: %s", exc
                    )
                    return UpdateContextResult(memories=MemoryQueryResult(results=[]))

                if context_text:
                    memory_contents.append(
                        MemoryContent(
                            content=context_text,
                            mime_type=MemoryMimeType.TEXT,
                            metadata={"source": "context_builder"},
                        )
                    )
                    memory_parts.append(self._context_template.replace("{context}", context_text))
            else:
                if self._context_template_id is not None:
                    memory_result = await self._client.thread.get_user_context(
                        thread_id=self._thread_id,
                        template_id=self._context_template_id,
                    )
                else:
                    memory_result = await self._client.thread.get_user_context(
                        thread_id=self._thread_id,
                    )

                # If we have memory context, include it
                if memory_result.context:
                    memory_contents.append(
                        MemoryContent(
                            content=memory_result.context,
                            mime_type=MemoryMimeType.TEXT,
                            metadata={"source": "thread_context"},
                        )
                    )
                    memory_parts.append(
                        self._context_template.replace("{context}", memory_result.context)
                    )

                thread = await self._client.thread.get(thread_id=self._thread_id, lastn=10)
                # Only include recent messages if we have memory
                if memory_parts and thread.messages:
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
