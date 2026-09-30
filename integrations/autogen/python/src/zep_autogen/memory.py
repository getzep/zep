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

Zep v4 addresses every resource by a UUID that Zep assigns. The application
creates the user and the thread once (see :mod:`zep_autogen.provisioning`),
stores ``user_uuid`` and ``thread_uuid``, and gives the UUIDs to this class.
"""

import logging
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
from zep_cloud.types import AddMessage

from .limits import truncate_graph_data, truncate_message_content
from .provisioning import create_thread as _create_thread
from .search import collect, results_to_memory_content, search_scope

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
        user_uuid: The UUID of the Zep user this memory is scoped to.
        thread_uuid: The UUID of the Zep thread this memory records the
            conversation in.
        graph_uuid: The UUID of the graph of the user, resolved from the user
            record.
        user_message: The last user-role message's text content from
            ``model_context.get_messages()``, or ``""`` if none is found.
        model_context: The AutoGen ``ChatCompletionContext`` passed to
            ``update_context()`` for this call.

    Example:
        A builder that searches the graph of the user instead of using the
        thread's default Context Block retrieval::

            async def my_builder(ctx: ContextInput) -> str | None:
                pager = await ctx.zep.graph.search_edges(
                    ctx.graph_uuid,
                    query=ctx.user_message,
                    limit=10,
                )
                facts = [edge.fact async for edge in pager if edge.fact]
                if not facts:
                    return None
                return "\\n".join(facts)

            memory = ZepUserMemory(
                client=zep,
                user_uuid=user.uuid_,
                thread_uuid=thread.uuid_,
                context_builder=my_builder,
            )
    """

    zep: AsyncZep
    user_uuid: str
    thread_uuid: str
    graph_uuid: str
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
    - Data storage in the graph of the Zep user

    Note:
        **The Zep memory loop, precisely.** Context INJECTION is automatic:
        AutoGen calls ``update_context()`` before every model call. PERSISTENCE
        is NOT automatic: the application must call ``add()`` itself (typically
        once per user turn and once per assistant turn) -- this is AutoGen's
        design, not a limitation of this integration. See the package README
        for the canonical wiring snippet.

    Note:
        **UUID addressing.** Zep v4 assigns the UUID of a user, a thread, and
        a graph. Create the user one time with
        :func:`zep_autogen.provisioning.create_user`, store ``user.uuid_``,
        and pass the value as ``user_uuid``. This class does not create a
        user and does not resolve a name to a UUID at run time.

    Note:
        **Thread creation.** When ``thread_uuid`` is omitted, the first
        ``add()`` call creates a thread for the user and keeps the UUID that
        Zep returns. Read the UUID back with :attr:`thread_uuid` and store it,
        because a new instance without ``thread_uuid`` will create a new
        thread. A creation failure is logged and swallowed, never raised into
        ``add()``/``update_context()``. Callers who want a creation failure to
        surface loudly should call
        :func:`zep_autogen.provisioning.create_thread` directly, out-of-band,
        before the first turn.
    """

    def __init__(
        self,
        client: AsyncZep,
        user_uuid: str,
        thread_uuid: str | None = None,
        graph_uuid: str | None = None,
        context_template_uuid: str | None = None,
        context_builder: ContextBuilder | None = None,
        context_template: str = DEFAULT_CONTEXT_TEMPLATE,
        **kwargs: Any,
    ) -> None:
        """
        Initialize ZepUserMemory with an AsyncZep client instance.

        Args:
            client: An initialized AsyncZep instance
            user_uuid: The UUID of the Zep user (required). Zep assigns the
                value when the user is created.
            thread_uuid: Optional UUID of the Zep thread. When omitted, the
                first ``add()`` call creates a thread for the user.
            graph_uuid: Optional UUID of the graph of the user. When omitted,
                the first graph operation reads the value from the user
                record and caches it.
            context_template_uuid: Optional UUID of a context template that
                customizes the rendering of the retrieved Context Block. When
                omitted, Zep returns the default structured Context Block.
                Ignored when ``context_builder`` is set.
            context_builder: Optional async callable that constructs the context
                block injected by ``update_context()``, in place of the default
                ``thread.get_context(...)`` call. Receives a single
                :class:`ContextInput`. See :data:`ContextBuilder` for the full
                error-isolation contract.
            context_template: Template used to wrap retrieved context before
                injecting it into the model context as a system message. Must
                contain a literal ``{context}`` placeholder, replaced via plain
                string replacement (never ``str.format``). Defaults to
                :data:`DEFAULT_CONTEXT_TEMPLATE`.
            **kwargs: Additional configuration options
        """
        if not isinstance(client, AsyncZep):
            raise TypeError("client must be an instance of AsyncZep")

        if not user_uuid:
            raise ValueError("user_uuid is required")

        self._client = client
        self._user_uuid = user_uuid
        self._thread_uuid = thread_uuid
        self._graph_uuid = graph_uuid
        self._context_template_uuid = context_template_uuid
        self._context_builder = context_builder
        self._context_template = context_template
        self._config = kwargs

        # Set up module logger
        self._logger = logging.getLogger(__name__)

    @property
    def thread_uuid(self) -> str | None:
        """The UUID of the thread, or ``None`` when no thread exists yet."""
        return self._thread_uuid

    async def _ensure_thread(self) -> str | None:
        """Return the thread UUID and create the thread when it is missing.

        A creation failure is logged and swallowed so a Zep outage never
        raises into ``add()``/``update_context()``.
        """
        if self._thread_uuid:
            return self._thread_uuid
        try:
            thread = await _create_thread(self._client, user_uuid=self._user_uuid)
        except Exception as exc:
            self._logger.warning(
                "Failed to create a Zep thread for user %s: %s", self._user_uuid, exc
            )
            return None
        self._thread_uuid = thread.uuid_
        return self._thread_uuid

    async def _resolve_graph_uuid(self) -> str | None:
        """Return the UUID of the graph of the user, and cache it.

        The value comes from the user record, which is addressed by UUID. This
        is not a name lookup. Pass ``graph_uuid`` to the constructor to remove
        the extra request.
        """
        if self._graph_uuid:
            return self._graph_uuid
        try:
            user = await self._client.user.get(self._user_uuid)
        except Exception as exc:
            self._logger.warning("Failed to read Zep user %s: %s", self._user_uuid, exc)
            return None
        self._graph_uuid = user.graph_uuid
        return self._graph_uuid

    async def add(
        self, content: MemoryContent, cancellation_token: CancellationToken | None = None
    ) -> None:
        """
        Add a memory entry to Zep storage.

        Uses metadata.type to determine storage method:
        - type="message": stores the content as a message in the thread with
          ``thread.add_messages``
        - type="data": stores the content in the graph of the user with
          ``graph.episode.add`` (maps the mime type to a data type)

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
            thread_uuid = await self._ensure_thread()
            if not thread_uuid:
                return

            # Store as message in thread session
            role = metadata_copy.get("role", "user")
            name = metadata_copy.get("name")

            truncated_content = truncate_message_content(str(content.content), label=role)
            message = AddMessage(name=name, content=truncated_content, role=role)

            # Add message to user's thread in Zep
            await self._client.thread.add_messages(thread_uuid, messages=[message])

        elif content_type == "data":
            graph_uuid = await self._resolve_graph_uuid()
            if not graph_uuid:
                return

            # Store as data in the graph of the user - map mime type to Zep data type
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

            # Add data to the graph of the user
            truncated_data = truncate_graph_data(str(content.content))
            await self._client.graph.episode.add(graph_uuid, type=data_type, data=truncated_data)

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
        Query memories from Zep storage with a scoped graph search.

        Args:
            query: Search query string or MemoryContent
            cancellation_token: Optional cancellation token
            **kwargs: Additional query parameters. ``scope`` selects the
                search scope and defaults to ``"edges"``. The other
                parameters are passed to the search method.

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
        scope = kwargs.pop("scope", "edges")

        results: list[MemoryContent] = []

        try:
            graph_uuid = await self._resolve_graph_uuid()
            if not graph_uuid:
                return MemoryQueryResult(results=[])

            # Search the graph of the user
            items = await search_scope(
                self._client,
                graph_uuid=graph_uuid,
                query=query_str,
                scope=scope,
                limit=limit,
                **kwargs,
            )
            results = results_to_memory_content(items, scope, source="user_graph")
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
        ``thread.get_context(...)``), wraps it in ``context_template``,
        and appends it to ``model_context`` as a ``SystemMessage``.

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

            if not self._thread_uuid:
                return UpdateContextResult(memories=MemoryQueryResult(results=[]))

            memory_contents = []
            memory_parts = []

            if self._context_builder is not None:
                user_message = await self._last_user_text(model_context)
                graph_uuid = await self._resolve_graph_uuid()
                try:
                    context_text = await self._context_builder(
                        ContextInput(
                            zep=self._client,
                            user_uuid=self._user_uuid,
                            thread_uuid=self._thread_uuid,
                            graph_uuid=graph_uuid or "",
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
                if self._context_template_uuid is not None:
                    memory_result = await self._client.thread.get_context(
                        self._thread_uuid,
                        template_uuid=self._context_template_uuid,
                    )
                else:
                    memory_result = await self._client.thread.get_context(self._thread_uuid)

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

                # Only include recent messages if we have memory
                if memory_parts:
                    pager = await self._client.thread.list_messages(self._thread_uuid, limit=10)
                    recent = await collect(pager, 10)
                    if recent:
                        message_history = []
                        for msg in recent:
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
        Clear all memories from Zep storage by deleting the thread.

        This will delete the entire thread and all its messages.
        Note: This operation cannot be undone. Zep runs the delete
        asynchronously and returns a task.
        """
        try:
            # Delete the thread - this clears all messages and memory for this thread
            if self._thread_uuid:
                await self._client.thread.delete(self._thread_uuid)

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
