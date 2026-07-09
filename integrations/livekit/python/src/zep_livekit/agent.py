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
from typing import Any, Literal

from livekit import agents
from livekit.agents.llm.chat_context import ChatContext, ChatMessage
from zep_cloud import SearchFilters
from zep_cloud.client import AsyncZep
from zep_cloud.graph.utils import compose_context_string
from zep_cloud.types import Message, Reranker

from .exceptions import AgentConfigurationError
from .limits import truncate_graph_data, truncate_message_content
from .provisioning import UserSetupHook
from .provisioning import ensure_thread as _ensure_thread
from .provisioning import ensure_user as _ensure_user

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
        user_id: The Zep user ID this agent is scoped to.
        thread_id: The Zep thread ID this agent records the conversation in.
        user_message: The user's message text for this turn.
        session: The LiveKit ``AgentSession`` for this turn, if reachable from
            ``on_user_turn_completed`` (``self.session``), else ``None``.

    Example:
        A builder that searches a per-user graph instead of using the
        thread's default context retrieval::

            async def my_builder(ctx: ContextInput) -> str | None:
                results = await ctx.zep.graph.search(
                    user_id=ctx.user_id,
                    query=ctx.user_message,
                    scope="edges",
                )
                if not results.edges:
                    return None
                return "\\n".join(edge.fact for edge in results.edges)

            agent = ZepUserAgent(
                zep_client=zep,
                user_id="user-123",
                thread_id="thread-abc",
                context_builder=my_builder,
            )
    """

    zep: AsyncZep
    user_id: str
    thread_id: str
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
        graph_id: The Zep graph ID this agent is scoped to.
        user_message: The user's message text for this turn.
        session: The LiveKit ``AgentSession`` for this turn, if reachable from
            ``on_user_turn_completed`` (``self.session``), else ``None``.
    """

    zep: AsyncZep
    graph_id: str
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
        **Per-session identity.** ``user_id``/``thread_id`` are fixed
        constructor arguments, resolved once at construction -- not
        re-resolved per turn. This is idiomatic for voice: construct one
        ``ZepUserAgent`` (and typically one ``AgentSession``) per user/call
        rather than sharing a single instance across users.

    Args:
        zep_client: Initialized AsyncZep client for memory operations
        user_id: User identifier for memory isolation and personalization
        thread_id: Thread identifier for conversation continuity
        context_mode: Deprecated and ignored. The Zep V3 Context Block returns a
            structured format and no longer supports the "basic"/"summary" mode
            selector. Retained for backwards compatibility.
        user_message_name: Optional name to set on user messages in Zep
        assistant_message_name: Optional name to set on assistant messages in Zep
        first_name: Optional first name, passed to :func:`~zep_livekit.provisioning.ensure_user`
            when resources are lazily created on the first turn.
        last_name: Optional last name, passed to
            :func:`~zep_livekit.provisioning.ensure_user`.
        email: Optional email, passed to :func:`~zep_livekit.provisioning.ensure_user`.
        on_created: Optional async hook invoked exactly once, right after a new
            Zep user is created via the lazy resource-creation path. Use it to
            set up per-user ontology, custom instructions, or user summary
            instructions. It does **not** fire for users that already exist.
            Passed through to :func:`~zep_livekit.provisioning.ensure_user` as
            its ``on_created`` hook -- a hook failure is treated the same as a
            genuine provisioning failure on this lazy path: logged and
            swallowed (never raised into the voice session), with resource
            creation retried on the next turn. Contrast this with calling
            :func:`~zep_livekit.provisioning.ensure_user` directly, out-of-band,
            where a hook failure **propagates** to the caller.
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
        user_id: str,
        thread_id: str,
        context_mode: Literal["basic", "summary"] | None = None,
        user_message_name: str | None = None,
        assistant_message_name: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        email: str | None = None,
        on_created: UserSetupHook | None = None,
        context_builder: ContextBuilder | None = None,
        context_template: str = DEFAULT_CONTEXT_TEMPLATE,
        **kwargs: Any,
    ) -> None:
        if not user_id:
            raise AgentConfigurationError("user_id must be a non-empty string")
        if not thread_id:
            raise AgentConfigurationError("thread_id must be a non-empty string")

        # Initialize base Agent with all parameters passed through
        super().__init__(**kwargs)

        self._zep_client = zep_client
        self._user_id = user_id
        self._thread_id = thread_id
        # context_mode is deprecated: the Zep V3 get_user_context no longer accepts
        # a "mode" argument. The parameter is accepted but ignored for compatibility.
        self._context_mode = context_mode
        self._user_message_name = user_message_name
        self._assistant_message_name = assistant_message_name

        self._first_name = first_name
        self._last_name = last_name
        self._email = email
        self._on_created = on_created
        self._context_builder = context_builder
        self._context_template = context_template

        # Whether the Zep user + thread have been created (or confirmed to
        # already exist) for this agent instance.  Cached so repeated turns do
        # not re-issue setup calls.
        self._resources_ready = False

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

    # ------------------------------------------------------------------
    # Lazy resource creation
    # ------------------------------------------------------------------

    async def _ensure_resources(self) -> bool:
        """Create the Zep user and thread if they do not already exist.

        Delegates to :func:`~zep_livekit.provisioning.ensure_user` and
        :func:`~zep_livekit.provisioning.ensure_thread`, the same
        create-then-catch-conflict helpers available for out-of-band
        provisioning. Idempotent and cached: succeeds for users/threads that
        already exist and only runs the ``on_created`` hook for genuinely new
        users.

        This is the hot path: unlike calling
        :func:`~zep_livekit.provisioning.ensure_user` directly (where a
        genuine failure or an ``on_created`` hook error propagates to the
        caller), here every failure -- including a hook failure -- is logged
        and swallowed so a Zep or setup-code outage never raises into
        ``on_user_turn_completed``. Out-of-band callers that need loud
        failures should call ``ensure_user``/``ensure_thread`` directly
        instead of relying on this lazy path.

        Returns:
            ``True`` if the user and thread are ready (created or
            pre-existing), ``False`` on a genuine failure (so the caller can
            skip this turn and retry on the next).
        """
        if self._resources_ready:
            return True

        try:
            await _ensure_user(
                self._zep_client,
                user_id=self._user_id,
                first_name=self._first_name,
                last_name=self._last_name,
                email=self._email,
                on_created=self._on_created,
            )
        except Exception as exc:
            # Covers both a genuine SDK failure and an on_created hook error
            # -- either way, the hot path must degrade, not raise.
            logger.warning("Failed to create Zep user %s: %s", self._user_id, exc)
            return False

        try:
            await _ensure_thread(self._zep_client, thread_id=self._thread_id, user_id=self._user_id)
        except Exception as exc:
            logger.warning("Failed to create Zep thread %s: %s", self._thread_id, exc)
            return False

        self._resources_ready = True
        return True

    async def _store_assistant_message(self, content_text: str, item: Any) -> None:
        """Store assistant message in Zep thread memory."""
        try:
            # Use custom assistant name if provided, otherwise fallback to item name
            message_name = self._assistant_message_name or getattr(item, "name", None)

            zep_message = Message(
                content=truncate_message_content(content_text, label="assistant"),
                role="assistant",
                name=message_name,
            )

            await self._zep_client.thread.add_messages(
                thread_id=self._thread_id, messages=[zep_message]
            )

        except Exception as e:
            logger.warning(f"Failed to store assistant response: {e}")

    async def on_user_turn_completed(self, turn_ctx: ChatContext, new_message: ChatMessage) -> None:
        """
        Handle user turn completion - store message and inject memory context.

        1. Lazily ensure the Zep user and thread exist.
        2. Store user message in Zep.
        3. Retrieve relevant context from Zep (default: single
           ``thread.add_messages(return_context=True)`` round-trip; or, when
           ``context_builder`` is set, persistence and the builder run
           concurrently instead).
        4. Inject context into the conversation as a system message.

        Note:
            **Default-path efficiency.** The default path folds persistence
            and retrieval into a single ``thread.add_messages(
            return_context=True)`` call instead of a separate
            ``thread.add_messages`` + ``thread.get_user_context`` round-trip.
            The (deprecated, ignored) ``context_mode`` parameter has no
            ``add_messages``-equivalent to preserve -- ``get_user_context``
            no longer accepts a ``mode`` argument at all in the Zep V3 SDK,
            so there is no non-default mode to keep a two-call path for.
        """
        await super().on_user_turn_completed(turn_ctx, new_message)

        user_text = new_message.text_content
        if not user_text or not user_text.strip():
            return

        if not await self._ensure_resources():
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
            zep_message = Message(content=user_text, role="user", name=self._user_message_name)

            response = await self._zep_client.thread.add_messages(
                thread_id=self._thread_id,
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
            zep_message = Message(content=user_text, role="user", name=self._user_message_name)
            await self._zep_client.thread.add_messages(
                thread_id=self._thread_id, messages=[zep_message]
            )

        async def _build() -> str | None:
            context_input = ContextInput(
                zep=self._zep_client,
                user_id=self._user_id,
                thread_id=self._thread_id,
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
    - Performs hybrid search to retrieve relevant context from edges, nodes, and episodes
    - Uses smart context composition for comprehensive knowledge retrieval
    - Optional user name prefixing for message attribution

    User Identification:
    - If user_name is provided, messages are stored as "[UserName]: message" and "[Assistant]: response"
    - If user_name is None, messages are stored without prefixes
    - Designed for per-user agent instances (typical deployment pattern)

    Note:
        **No ``on_created`` hook.** Unlike :class:`ZepUserAgent`, this class
        has no lazy user provisioning and accepts no ``on_created`` hook: it
        is scoped to a standalone ``graph_id``, not a Zep user, so there is no
        "user created" event to hook into. Passing ``on_created`` raises
        ``TypeError`` at construction so a typo or copy-paste from
        ``ZepUserAgent`` fails loudly instead of being silently swallowed by
        ``**kwargs``.

    Args:
        zep_client: Initialized AsyncZep client for memory operations
        graph_id: Graph identifier for knowledge storage
        user_name: Optional user name for message prefixing (e.g., "Alice", "Bob")
        facts_limit: Maximum number of facts/edges to retrieve (default: 20)
        entity_limit: Maximum number of entities/nodes to retrieve (default: 5)
        episode_limit: Maximum number of episodes to retrieve (default: 3)
        search_filters: Optional filters for graph search
        reranker: Optional reranker for search results
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
        graph_id: str,
        user_name: str | None = None,
        facts_limit: int = 15,
        entity_limit: int = 5,
        episode_limit: int = 2,
        search_filters: SearchFilters | None = None,
        reranker: Reranker | None = "rrf",
        context_builder: GraphContextBuilder | None = None,
        context_template: str = DEFAULT_CONTEXT_TEMPLATE,
        **kwargs: Any,
    ) -> None:
        if not graph_id:
            raise AgentConfigurationError("graph_id must be a non-empty string")

        if "on_created" in kwargs:
            raise TypeError(
                "ZepGraphAgent does not support 'on_created': it is scoped to a "
                "standalone graph_id, not a Zep user. Use ZepUserAgent for "
                "user-scoped provisioning hooks."
            )

        # Initialize base Agent with all parameters passed through
        super().__init__(**kwargs)

        self._zep_client = zep_client
        self._graph_id = graph_id
        self._user_name = user_name
        self._facts_limit = facts_limit
        self._entity_limit = entity_limit
        self._episode_limit = episode_limit
        self._search_filters = search_filters
        self._reranker = reranker
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

            await self._zep_client.graph.add(
                graph_id=self._graph_id,
                type="message",
                data=truncate_graph_data(message_data, label="assistant graph data"),
            )

        except Exception as e:
            logger.warning(f"Failed to store assistant response: {e}")

    async def on_user_turn_completed(self, turn_ctx: ChatContext, new_message: ChatMessage) -> None:
        """
        Handle user turn completion - store message and inject memory context.

        1. Store user message in Zep graph
        2. Retrieve relevant context (default: hybrid search across edges,
           nodes, and episodes; or, when ``context_builder`` is set, the
           custom builder replaces this entirely)
        3. Inject context into conversation using smart composition
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

            await self._zep_client.graph.add(
                graph_id=self._graph_id,
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
                graph_id=self._graph_id,
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
        Retrieve and compose context from graph using hybrid search.

        - Search for edges (facts), nodes (entities) and episodes concurrently
        - Compose a context string using the graph utilities
        """
        try:
            # Perform parallel searches like in autogen
            search_functions = []

            if self._facts_limit:
                # Search for facts/relationships (edges)
                search_functions.append(
                    self._zep_client.graph.search(
                        graph_id=self._graph_id,
                        query=query,
                        limit=self._facts_limit,
                        search_filters=self._search_filters,
                        reranker=self._reranker,
                        scope="edges",
                    ),
                )

            if self._entity_limit:
                # Search for entities (nodes)
                search_functions.append(
                    self._zep_client.graph.search(
                        graph_id=self._graph_id,
                        query=query,
                        limit=self._entity_limit,
                        search_filters=self._search_filters,
                        reranker=self._reranker,
                        scope="nodes",
                    ),
                )

            if self._episode_limit:
                # Search for episodes
                search_functions.append(
                    self._zep_client.graph.search(
                        graph_id=self._graph_id,
                        query=query,
                        limit=self._episode_limit,
                        search_filters=self._search_filters,
                        reranker=self._reranker,
                        scope="episodes",
                    ),
                )

            results = await asyncio.gather(*search_functions)

            edges = []
            nodes = []
            episodes = []

            # Collect all results
            for result in results:
                if result.edges:
                    edges.extend(result.edges)
                if result.nodes:
                    nodes.extend(result.nodes)
                if result.episodes:
                    episodes.extend(result.episodes)

            if not edges and not nodes and not episodes:
                return None

            context = compose_context_string(edges, nodes, episodes)
            return context

        except Exception as e:
            logger.error(f"Error retrieving graph context: {e}")
            return None

    async def on_exit(self) -> None:
        """Called when the agent exits a conversation."""
        await super().on_exit()
