"""
Zep user memory integration for LiveKit agents.

This module provides the ZepUserAgent class that integrates Zep's memory capabilities
with LiveKit's voice AI agent framework
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from livekit import agents
from livekit.agents.llm.chat_context import ChatContext, ChatMessage
from zep_cloud import SearchFilters
from zep_cloud.client import AsyncZep
from zep_cloud.types import AddMessage

from .exceptions import AgentConfigurationError
from .limits import truncate_graph_data, truncate_message_content

logger = logging.getLogger(__name__)


def _current_session(agent: agents.Agent) -> Any | None:
    """Return ``agent.session`` if the agent is attached to a running
    activity, else ``None``.

    ``Agent.session`` raises ``RuntimeError`` (not ``AttributeError``) before
    the agent is attached to a session/activity -- e.g. when a
    ``ContextBuilder``/``GraphContextBuilder`` is invoked from a unit test
    that drives ``on_user_turn_completed`` directly, without a real
    ``AgentSession``. ``ContextInput.session``/``GraphContextInput.session``
    are documented as "the ``AgentSession`` if reachable, else ``None``", so
    that RuntimeError is expected and swallowed here rather than surfaced.
    """
    try:
        return agent.session
    except RuntimeError:
        return None


#: Default template used to wrap retrieved Zep context before injecting it
#: into the conversation as a system message.  Rendered via plain string
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
    """Input handed to a custom :data:`ContextBuilder`.

    Bundling the builder's inputs into a single frozen dataclass (rather than
    positional arguments) lets us add fields later without breaking existing
    builders.

    Attributes:
        zep: The ``AsyncZep`` client in use by the agent.
        user_uuid: The UUID of the Zep user this agent is scoped to.
        thread_uuid: The UUID of the Zep thread this agent records the
            conversation in.
        user_message: The user's message text for this turn.
        session: The LiveKit ``AgentSession`` for this turn, if reachable from
            ``on_user_turn_completed`` (``self.session``), else ``None``.

    Example:
        A builder that searches a per-user graph instead of using the
        thread's default context retrieval::

            async def my_builder(ctx: ContextInput) -> str | None:
                page = await ctx.zep.graph.search_edges(
                    graph_uuid,
                    query=ctx.user_message,
                )
                if not page.items:
                    return None
                return "\\n".join(edge.fact for edge in page.items if edge.fact)

            agent = ZepUserAgent(
                zep_client=zep,
                user_uuid="6e7d...",
                thread_uuid="1f2a...",
                context_builder=my_builder,
            )
    """

    zep: AsyncZep
    user_uuid: str
    thread_uuid: str
    user_message: str
    session: Any | None = None


#: Type alias for a custom context builder function.
#:
#: A context builder receives a single :class:`ContextInput` and returns the
#: context string to inject into the conversation (or ``None`` to skip
#: injection).
#:
#: Error semantics: if the builder raises, ``ZepUserAgent`` logs a warning and
#: skips injection for that turn -- it does not crash the agent and does not
#: prevent message persistence from completing. See
#: :meth:`ZepUserAgent.on_user_turn_completed` for the full error-isolation
#: contract between persistence and the builder.
ContextBuilder = Callable[[ContextInput], Awaitable[str | None]]


@dataclass(frozen=True)
class GraphContextInput:
    """Input handed to a custom :data:`GraphContextBuilder`.

    Attributes:
        zep: The ``AsyncZep`` client in use by the agent.
        graph_uuid: The UUID of the Zep graph this agent is scoped to.
        user_message: The user's message text for this turn.
        session: The LiveKit ``AgentSession`` for this turn, if reachable from
            ``on_user_turn_completed`` (``self.session``), else ``None``.
    """

    zep: AsyncZep
    graph_uuid: str
    user_message: str
    session: Any | None = None


#: Type alias for a custom graph context builder function.
#:
#: When set on :class:`ZepGraphAgent`, this replaces
#: :meth:`ZepGraphAgent._retrieve_graph_context` entirely.  Receives a single
#: :class:`GraphContextInput` and returns the context string to inject (or
#: ``None`` to skip injection).  If the builder raises, a warning is logged
#: and injection is skipped for that turn -- message persistence to the graph
#: is unaffected either way, since it happens independently.
GraphContextBuilder = Callable[[GraphContextInput], Awaitable[str | None]]


class ZepUserAgent(agents.Agent):
    """
    LiveKit agent with Zep memory capabilities.

    A drop-in replacement for LiveKit's Agent that adds persistent memory:
    - Stores user and assistant messages in Zep threads
    - Retrieves relevant context and injects it for personalized responses
    - Accepts all standard LiveKit Agent parameters

    Note:
        **Per-session identity.** ``user_uuid``/``thread_uuid`` are fixed
        constructor arguments, resolved once at construction -- not
        re-resolved per turn. This is idiomatic for voice: construct one
        ``ZepUserAgent`` (and typically one ``AgentSession``) per user/call
        rather than sharing a single instance across users.

    Note:
        **UUID addressing.** Zep v4 addresses a user and a thread by a
        server-generated UUID. A ``user_id`` or a ``thread_id`` is a name,
        not an address. Create the user and the thread out-of-band with
        :func:`~zep_livekit.provisioning.create_user` and
        :func:`~zep_livekit.provisioning.create_thread`, store the returned
        UUIDs in your own database, and pass them here. The agent does not
        call ``lookup`` at run time.

    Args:
        zep_client: Initialized AsyncZep client for memory operations
        user_uuid: UUID of the Zep user this agent is scoped to
        thread_uuid: UUID of the Zep thread that records the conversation
        user_message_name: Optional name to set on user messages in Zep
        assistant_message_name: Optional name to set on assistant messages in Zep
        context_builder: An optional async callable that constructs the
            context block to inject, in place of the default
            ``thread.add_messages(return_context=True)`` retrieval. Receives a
            single :class:`ContextInput`. When set, message persistence and
            context building run **concurrently** for lower latency -- see the
            Note on :meth:`on_user_turn_completed` for the error-isolation
            contract between the two.
        context_template: Template used to wrap retrieved context before
            injecting it as a system message. Must contain a literal
            ``{context}`` placeholder, replaced via plain string replacement
            (never ``str.format``). Defaults to :data:`DEFAULT_CONTEXT_TEMPLATE`.
        **kwargs: All other LiveKit Agent parameters (chat_ctx, tools, stt, llm, tts, etc.)

    Note:
        **Error isolation between persistence and the context builder.**
        When ``context_builder`` is set, persistence (``add_messages``) and
        the builder run concurrently via ``asyncio.gather(...,
        return_exceptions=True)``. Each is isolated from the other's failure:

        * If the builder raises, a warning is logged and injection is
          skipped for this turn -- but persistence still completes.
        * If persistence raises, a warning is logged -- but a successful
          builder result may still be injected into the conversation.
    """

    def __init__(
        self,
        *,
        zep_client: AsyncZep,
        user_uuid: str,
        thread_uuid: str,
        user_message_name: str | None = None,
        assistant_message_name: str | None = None,
        context_builder: ContextBuilder | None = None,
        context_template: str = DEFAULT_CONTEXT_TEMPLATE,
        **kwargs: Any,
    ) -> None:
        if not user_uuid:
            raise AgentConfigurationError("user_uuid must be a non-empty string")
        if not thread_uuid:
            raise AgentConfigurationError("thread_uuid must be a non-empty string")

        # Initialize base Agent with all parameters passed through
        super().__init__(**kwargs)

        self._zep_client = zep_client
        self._user_uuid = user_uuid
        self._thread_uuid = thread_uuid
        self._user_message_name = user_message_name
        self._assistant_message_name = assistant_message_name

        self._context_builder = context_builder
        self._context_template = context_template

    async def on_enter(self) -> None:
        """Called when the agent enters a conversation."""
        await super().on_enter()

        # Hook into session events to capture assistant messages
        if hasattr(self, "session"):
            self._setup_session_handlers()

    def _setup_session_handlers(self) -> None:
        """Set up event handlers on the session to capture assistant responses."""

        @self.session.on("conversation_item_added")
        def on_conversation_item_added(event: Any) -> None:
            """Handle conversation item addition events to capture assistant responses."""
            # Schedule async storage to avoid blocking event processing
            asyncio.create_task(self._handle_conversation_item(event))

    async def _handle_conversation_item(self, event: Any) -> None:
        """Handle conversation item from session event."""
        try:
            # Extract conversation item from event
            if not hasattr(event, "item"):
                return

            item = event.item

            # Validate item has required message attributes
            if not (hasattr(item, "role") and hasattr(item, "content")):
                return

            role = item.role
            content = item.content

            # Only store assistant messages (user messages handled in on_user_turn_completed)
            if role == "assistant":
                content_text = self._extract_text_content(content)
                if content_text.strip():
                    await self._store_assistant_message(content_text.strip(), item)

        except Exception as e:
            logger.error(f"Failed to handle conversation item: {e}")

    def _extract_text_content(self, content: Any) -> str:
        """Extract text content from various LiveKit content formats."""
        if isinstance(content, str):
            return content

        if isinstance(content, list):
            text_parts = []
            for item in content:
                if hasattr(item, "text"):
                    text_parts.append(item.text)
                elif isinstance(item, str):
                    text_parts.append(item)
            return " ".join(text_parts)

        return str(content)

    async def _store_assistant_message(self, content_text: str, item: Any) -> None:
        """Store assistant message in Zep thread memory."""
        try:
            # Use custom assistant name if provided, otherwise fallback to item name
            message_name = self._assistant_message_name or getattr(item, "name", None)

            zep_message = AddMessage(
                content=truncate_message_content(content_text, label="assistant"),
                role="assistant",
                name=message_name,
            )

            await self._zep_client.thread.add_messages(self._thread_uuid, messages=[zep_message])

        except Exception as e:
            logger.warning(f"Failed to store assistant response: {e}")

    async def on_user_turn_completed(self, turn_ctx: ChatContext, new_message: ChatMessage) -> None:
        """
        Handle user turn completion - store message and inject memory context.

        1. Store user message in Zep.
        2. Retrieve relevant context from Zep (default: single
           ``thread.add_messages(return_context=True)`` round-trip; or, when
           ``context_builder`` is set, persistence and the builder run
           concurrently instead).
        3. Inject context into the conversation as a system message.

        Note:
            **Default-path efficiency.** The default path folds persistence
            and retrieval into a single ``thread.add_messages(
            return_context=True)`` call instead of a separate
            ``thread.add_messages`` + ``thread.get_context`` round-trip.
        """
        await super().on_user_turn_completed(turn_ctx, new_message)

        user_text = new_message.text_content
        if not user_text or not user_text.strip():
            return

        user_text = truncate_message_content(user_text.strip(), label="user")

        if self._context_builder is not None:
            context = await self._persist_and_build_context(user_text)
        else:
            context = await self._persist_with_return_context(user_text)

        if context:
            instruction = self._context_template.replace("{context}", context)
            turn_ctx.add_message(role="system", content=instruction)

    async def _persist_with_return_context(self, user_text: str) -> str | None:
        """Persist the user message and retrieve context in one round-trip.

        Returns:
            The retrieved context string, or ``None`` on failure or when no
            context is available.
        """
        try:
            zep_message = AddMessage(content=user_text, role="user", name=self._user_message_name)

            response = await self._zep_client.thread.add_messages(
                self._thread_uuid,
                messages=[zep_message],
                return_context=True,
            )
            return response.context if response else None

        except Exception as e:
            logger.warning(f"Failed to store user message / retrieve context from Zep: {e}")
            return None

    async def _persist_and_build_context(self, user_text: str) -> str | None:
        """Persist the message and build context concurrently.

        Runs ``thread.add_messages`` (without ``return_context``) and the
        custom ``context_builder`` concurrently via
        ``asyncio.gather(..., return_exceptions=True)`` so one side's
        exception can never cancel or mask the other's result.

        * If the builder raises, a warning is logged and ``None`` is used for
          the context, but persistence is unaffected.
        * If persistence raises, a warning is logged, but a successful
          builder result is still returned for injection.

        Returns:
            The context string to inject, or ``None``.
        """
        context_builder = self._context_builder
        assert context_builder is not None  # caller already checked

        session = _current_session(self)

        async def _persist() -> None:
            zep_message = AddMessage(content=user_text, role="user", name=self._user_message_name)
            await self._zep_client.thread.add_messages(self._thread_uuid, messages=[zep_message])

        async def _build() -> str | None:
            context_input = ContextInput(
                zep=self._zep_client,
                user_uuid=self._user_uuid,
                thread_uuid=self._thread_uuid,
                user_message=user_text,
                session=session,
            )
            return await context_builder(context_input)

        persist_result, build_result = await asyncio.gather(
            _persist(), _build(), return_exceptions=True
        )

        if isinstance(persist_result, BaseException):
            logger.warning(f"Failed to store user message in Zep: {persist_result}")

        if isinstance(build_result, BaseException):
            logger.warning(
                "Custom context_builder raised — skipping context injection for this turn",
                exc_info=build_result,
            )
            return None

        return build_result

    async def on_exit(self) -> None:
        """Called when the agent exits a conversation."""
        await super().on_exit()


class ZepGraphAgent(agents.Agent):
    """
    LiveKit agent with Zep graph memory capabilities.

    A drop-in replacement for LiveKit's Agent that adds persistent knowledge storage:
    - Stores user and assistant messages in Zep graph
    - Retrieves a prompt-ready context block for the graph with
      ``graph.get_context``
    - Optional user name prefixing for message attribution

    User Identification:
    - If user_name is provided, messages are stored as "[UserName]: message" and "[Assistant]: response"
    - If user_name is None, messages are stored without prefixes
    - Designed for per-user agent instances (typical deployment pattern)

    Note:
        **No ``on_created`` hook.** Unlike :class:`ZepUserAgent`, this class
        accepts no ``on_created`` hook: it is scoped to a graph, not a Zep
        user, so there is no "user created" event to hook into. Passing
        ``on_created`` raises
        ``TypeError`` at construction so a typo or copy-paste from
        ``ZepUserAgent`` fails loudly instead of being silently swallowed by
        ``**kwargs``.

    Args:
        zep_client: Initialized AsyncZep client for memory operations
        graph_uuid: UUID of the Zep graph used for knowledge storage. For a
            user's own graph, pass the ``graph_uuid`` of the user.
        user_name: Optional user name for message prefixing (e.g., "Alice", "Bob")
        search_filters: Optional filters applied to context retrieval
        max_characters: Optional maximum length of the retrieved context block
        context_builder: An optional async callable that replaces
            :meth:`_retrieve_graph_context` entirely. Receives a single
            :class:`GraphContextInput`. If it raises, a warning is logged and
            injection is skipped for that turn.
        context_template: Template used to wrap retrieved context before
            injecting it as a system message. Must contain a literal
            ``{context}`` placeholder, replaced via plain string replacement
            (never ``str.format``). Defaults to :data:`DEFAULT_CONTEXT_TEMPLATE`.
        **kwargs: All other LiveKit Agent parameters

    Raises:
        TypeError: If ``on_created`` is passed (not supported -- see the
            class docstring).
    """

    def __init__(
        self,
        *,
        zep_client: AsyncZep,
        graph_uuid: str,
        user_name: str | None = None,
        search_filters: SearchFilters | None = None,
        max_characters: int | None = None,
        context_builder: GraphContextBuilder | None = None,
        context_template: str = DEFAULT_CONTEXT_TEMPLATE,
        **kwargs: Any,
    ) -> None:
        if not graph_uuid:
            raise AgentConfigurationError("graph_uuid must be a non-empty string")

        if "on_created" in kwargs:
            raise TypeError(
                "ZepGraphAgent does not support 'on_created': it is scoped to a "
                "graph, not a Zep user. Use ZepUserAgent for user-scoped "
                "provisioning hooks."
            )

        # Initialize base Agent with all parameters passed through
        super().__init__(**kwargs)

        self._zep_client = zep_client
        self._graph_uuid = graph_uuid
        self._user_name = user_name
        self._search_filters = search_filters
        self._max_characters = max_characters
        self._context_builder = context_builder
        self._context_template = context_template

    async def on_enter(self) -> None:
        """Called when the agent enters a conversation."""
        await super().on_enter()

        # Hook into session events to capture assistant messages
        if hasattr(self, "session"):
            self._setup_session_handlers()

    def _setup_session_handlers(self) -> None:
        """Set up event handlers on the session to capture assistant responses."""

        @self.session.on("conversation_item_added")
        def on_conversation_item_added(event: Any) -> None:
            """Handle conversation item addition events to capture assistant responses."""
            # Schedule async storage to avoid blocking event processing
            asyncio.create_task(self._handle_conversation_item(event))

    async def _handle_conversation_item(self, event: Any) -> None:
        """Handle conversation item from session event."""
        try:
            # Extract conversation item from event
            if not hasattr(event, "item"):
                return

            item = event.item

            # Validate item has required message attributes
            if not (hasattr(item, "role") and hasattr(item, "content")):
                return

            role = item.role
            content = item.content

            # Only store assistant messages (user messages handled in on_user_turn_completed)
            if role == "assistant":
                content_text = self._extract_text_content(content)
                if content_text.strip():
                    await self._store_assistant_message(content_text.strip(), item)

        except Exception as e:
            logger.error(f"Failed to handle conversation item: {e}")

    def _extract_text_content(self, content: Any) -> str:
        """Extract text content from various LiveKit content formats."""
        if isinstance(content, str):
            return content

        if isinstance(content, list):
            text_parts = []
            for item in content:
                if hasattr(item, "text"):
                    text_parts.append(item.text)
                elif isinstance(item, str):
                    text_parts.append(item)
            return " ".join(text_parts)

        return str(content)

    async def _store_assistant_message(self, content_text: str, item: Any) -> None:
        """Store assistant message in Zep graph."""
        try:
            # Prefix assistant messages for consistency when user has a name
            if self._user_name:
                message_data = f"[Assistant]: {content_text}"
            else:
                message_data = content_text

            await self._zep_client.graph.episode.add(
                self._graph_uuid,
                type="message",
                data=truncate_graph_data(message_data, label="assistant graph data"),
            )

        except Exception as e:
            logger.warning(f"Failed to store assistant response: {e}")

    async def on_user_turn_completed(self, turn_ctx: ChatContext, new_message: ChatMessage) -> None:
        """
        Handle user turn completion - store message and inject memory context.

        1. Store user message in Zep graph
        2. Retrieve relevant context (default: ``graph.get_context``; or,
           when ``context_builder`` is set, the custom builder replaces this
           entirely)
        3. Inject context into the conversation as a system message
        """
        await super().on_user_turn_completed(turn_ctx, new_message)

        user_text = new_message.text_content
        if not user_text or not user_text.strip():
            return

        # Step 1: Store user message in Zep graph with user identification
        try:
            # Prefix message with user name if provided
            message_data = user_text.strip()
            if self._user_name:
                message_data = f"[{self._user_name}]: {message_data}"

            await self._zep_client.graph.episode.add(
                self._graph_uuid,
                type="message",
                data=truncate_graph_data(message_data, label="user graph data"),
            )

        except Exception as e:
            logger.warning(f"Failed to store user message in Zep graph: {e}")

        # Step 2: Retrieve relevant context
        try:
            if self._context_builder is not None:
                context = await self._build_custom_context(user_text[:400])
            else:
                context = await self._retrieve_graph_context(user_text[:400])  # Limit query length

            if context:
                # Step 3: Inject context as system message
                instruction = self._context_template.replace("{context}", context)
                turn_ctx.add_message(role="system", content=instruction)

        except Exception as e:
            logger.warning(f"Failed to retrieve context from Zep graph: {e}")

    async def _build_custom_context(self, query: str) -> str | None:
        """Run the custom ``context_builder``, isolating its failures.

        Returns ``None`` (logging a warning) if the builder raises, rather
        than propagating -- message persistence above is unaffected either
        way since it already completed independently.
        """
        context_builder = self._context_builder
        assert context_builder is not None  # caller already checked

        try:
            context_input = GraphContextInput(
                zep=self._zep_client,
                graph_uuid=self._graph_uuid,
                user_message=query,
                session=_current_session(self),
            )
            return await context_builder(context_input)
        except Exception:
            logger.warning(
                "Custom context_builder raised — skipping context injection for this turn",
                exc_info=True,
            )
            return None

    async def _retrieve_graph_context(self, query: str) -> str | None:
        """
        Retrieve a prompt-ready context block for the graph.

        ``graph.get_context`` assembles the relevant facts, entities, and
        episodes into one string, so the agent does not compose the block
        itself.
        """
        try:
            response = await self._zep_client.graph.get_context(
                self._graph_uuid,
                query=query,
                filters=self._search_filters,
                max_characters=self._max_characters,
            )
            context = response.context if response else None
            return context or None

        except Exception as e:
            logger.error(f"Error retrieving graph context: {e}")
            return None

    async def on_exit(self) -> None:
        """Called when the agent exits a conversation."""
        await super().on_exit()
