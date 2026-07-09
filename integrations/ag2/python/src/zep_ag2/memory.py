"""
Zep Memory Manager for AG2.

This module provides the ZepMemoryManager class that integrates Zep with AG2 agents
via system message injection and message storage.

Unlike Microsoft AutoGen v4 which has a formal Memory base class, AG2 uses
composition-based patterns. ZepMemoryManager enriches agents by:
- Injecting relevant memory context into system messages
- Storing conversation messages in Zep threads
- Retrieving session facts and context

AG2 has no native memory interface: ``ConversableAgent.register_hook`` is the
closest thing to a per-turn seam. Historically this package only supported
manual invocation (``enrich_system_message``/``add_messages``, called
explicitly by the application). :meth:`ZepMemoryManager.attach_to_agent` adds
an optional, fully automatic loop on top of that seam -- see its docstring
for the exact hook wiring and persistence contract.

Note:
    **Per-user manager instances.** Like the sibling ports (see
    ``zep_autogen.ZepUserMemory``), a ``ZepMemoryManager`` is scoped to one
    ``(user_id, session_id)`` pair for the lifetime of the instance. Create
    one manager per user/thread rather than sharing a single instance across
    users -- there is no per-call identity override.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from zep_cloud.client import AsyncZep
from zep_cloud.types import Message

from zep_ag2.exceptions import ZepAG2ConfigError, ZepAG2MemoryError
from zep_ag2.provisioning import UserSetupHook
from zep_ag2.provisioning import ensure_thread as _ensure_thread
from zep_ag2.provisioning import ensure_user as _ensure_user
from zep_ag2.tools import MESSAGE_MAX_CHARS, _run_sync, _truncate, _validate_role

logger = logging.getLogger(__name__)

#: Default template used to wrap retrieved Zep context before injecting it
#: into the agent's system message.  Rendered via plain string replacement
#: (``template.replace("{context}", context_text)``), never ``str.format`` --
#: so context text or a custom template containing ``{``/``}``/``%`` is
#: always safe to inject.
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
        zep: The ``AsyncZep`` client in use by this manager.
        user_id: The Zep user ID this manager is scoped to.
        thread_id: The Zep thread ID this manager records the conversation in,
            or ``None`` when the manager was created without a ``session_id``.
        user_message: The user message text this turn is building context for.
        agent: The AG2 agent this call is being made on behalf of, when
            invoked via :meth:`ZepMemoryManager.attach_to_agent`'s automatic
            loop. ``None`` when called manually (e.g. directly via
            :meth:`ZepMemoryManager.process_user_message` or
            :meth:`ZepMemoryManager.enrich_system_message` without an agent
            in scope).

    Example:
        A builder that searches a per-user graph with a pinned scope instead
        of using the thread's default Context Block retrieval::

            async def my_builder(ctx: ContextInput) -> str | None:
                results = await ctx.zep.graph.search(
                    user_id=ctx.user_id,
                    query=ctx.user_message,
                    scope="edges",
                )
                if not results.edges:
                    return None
                return "\\n".join(edge.fact for edge in results.edges)

            manager = ZepMemoryManager(
                zep, user_id="user-123", session_id="thread-abc",
                context_builder=my_builder,
            )
    """

    zep: AsyncZep
    user_id: str
    thread_id: str | None
    user_message: str
    agent: Any | None = None


#: Type alias for a custom context builder function.
#:
#: A context builder receives a single :class:`ContextInput` and returns the
#: context string to inject (or ``None`` to skip injection).
#:
#: Error semantics: if the builder raises, the caller (``process_user_message``
#: / ``get_memory_context`` / ``enrich_system_message``) logs a warning and
#: treats the context as unavailable -- it never lets the builder's exception
#: propagate.
#:
#: **Concurrency.** When :meth:`ZepMemoryManager.process_user_message` is
#: called with a builder set, the builder runs concurrently with message
#: persistence via ``asyncio.gather(..., return_exceptions=True)``, with
#: **per-side isolation**: a builder failure never prevents the message from
#: being persisted, and a persistence failure never prevents the builder's
#: context from being returned.
ContextBuilder = Callable[[ContextInput], Awaitable[str | None]]


class ZepMemoryManager:
    """
    Manages Zep memory for AG2 agents via system message injection.

    This class provides methods to enrich AG2 agents with memory context
    from Zep, store conversation messages, and retrieve session facts.

    Example:
        >>> from zep_cloud.client import AsyncZep
        >>> from zep_ag2 import ZepMemoryManager
        >>> zep = AsyncZep(api_key="your-key")
        >>> manager = ZepMemoryManager(zep, user_id="user123", session_id="sess456")
        >>> await manager.enrich_system_message(agent, query="project discussion")

    Note:
        **Lazy provisioning.** The Zep user and (if ``session_id`` is set)
        thread are created lazily, on first use, by
        :meth:`ensure_user_and_thread` -- called internally from
        :meth:`process_user_message`, :meth:`get_memory_context`,
        :meth:`enrich_system_message`, and the :meth:`attach_to_agent` hooks.
        The result is cached on the instance, so repeated calls incur no
        extra setup round-trips. This lazy path is hot-path-wrapped: a
        genuine provisioning failure (or an ``on_created`` hook failure) is
        logged and returns ``False``, never raised into a memory-path method.
        Callers who want provisioning failures to surface loudly should call
        :func:`zep_ag2.provisioning.ensure_user` and
        :func:`zep_ag2.provisioning.ensure_thread` directly, out-of-band,
        before the first turn.
    """

    def __init__(
        self,
        client: AsyncZep,
        user_id: str,
        session_id: str | None = None,
        *,
        first_name: str | None = None,
        last_name: str | None = None,
        email: str | None = None,
        on_created: UserSetupHook | None = None,
        context_builder: ContextBuilder | None = None,
        context_template: str = DEFAULT_CONTEXT_TEMPLATE,
    ) -> None:
        """
        Initialize ZepMemoryManager.

        Args:
            client: An initialized AsyncZep instance.
            user_id: User ID for memory isolation (required).
            session_id: Optional thread/session ID for conversation-scoped memory.
            first_name: Optional first name, passed to ``user.add`` during lazy
                provisioning. Helps Zep anchor the user's identity node in the graph.
            last_name: Optional last name, passed to ``user.add`` during lazy provisioning.
            email: Optional email, passed to ``user.add`` during lazy provisioning.
            on_created: Optional async hook invoked exactly once, right after a new
                Zep user is created during lazy provisioning. Use it to configure
                per-user ontology, custom instructions, or user summary instructions.
                Does not fire for users that already exist. See
                :func:`zep_ag2.provisioning.ensure_user` for the hook contract;
                note that on this lazy path, a hook failure is logged and swallowed
                rather than raised (see the class-level "Lazy provisioning" note).
            context_builder: Optional async callable that replaces the default
                Zep Context Block retrieval used by :meth:`process_user_message`,
                :meth:`get_memory_context`, and :meth:`enrich_system_message`.
                Receives a single :class:`ContextInput`. See :data:`ContextBuilder`
                for the full error-isolation and concurrency contract.
            context_template: Template used to wrap retrieved context before
                injecting it into the agent's system message. Must contain a
                literal ``{context}`` placeholder, replaced via plain string
                replacement (never ``str.format``). Defaults to
                :data:`DEFAULT_CONTEXT_TEMPLATE`.

        Raises:
            ZepAG2ConfigError: If client is not an AsyncZep instance or user_id is empty.
        """
        if not isinstance(client, AsyncZep):
            raise ZepAG2ConfigError("client must be an instance of AsyncZep")
        if not user_id:
            raise ZepAG2ConfigError("user_id is required")

        self._client = client
        self._user_id = user_id
        self._session_id = session_id
        self._first_name = first_name
        self._last_name = last_name
        self._email = email
        self._on_created = on_created
        self._context_builder = context_builder
        self._context_template = context_template

        # Whether the Zep user (and, once a session_id exists, the thread)
        # have been created (or confirmed to already exist). Cached so
        # repeated calls do not re-issue setup calls.
        self._user_ready = False
        self._thread_ready = False

    @property
    def client(self) -> AsyncZep:
        """The underlying AsyncZep client."""
        return self._client

    @property
    def user_id(self) -> str:
        """The user ID for memory isolation."""
        return self._user_id

    @property
    def session_id(self) -> str | None:
        """The thread/session ID, if set."""
        return self._session_id

    async def ensure_user_and_thread(self) -> bool:
        """Lazily create the Zep user and (if set) thread, hot-path-wrapped.

        Unlike calling :func:`zep_ag2.provisioning.ensure_user` /
        :func:`~.provisioning.ensure_thread` directly (where a genuine
        failure or an ``on_created`` hook error propagates to the caller),
        every failure here -- including a hook failure -- is logged and
        swallowed so a Zep or setup-code outage never raises into a
        memory-path method.

        The result is cached on the instance: subsequent calls are no-ops
        once the user (and thread, if applicable) are confirmed ready.

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
                logger.warning("Failed to create Zep user %s: %s", self._user_id, exc)
                return False

        if self._session_id and not self._thread_ready:
            try:
                await _ensure_thread(
                    self._client, thread_id=self._session_id, user_id=self._user_id
                )
                self._thread_ready = True
            except Exception as exc:
                logger.warning("Failed to create Zep thread %s: %s", self._session_id, exc)
                return False

        return True

    async def _build_context_via_builder(self, user_message: str, agent: Any | None) -> str | None:
        """Invoke ``context_builder`` if set, isolating any failure.

        Returns ``None`` (and logs a warning) if the builder is unset or
        raises -- callers treat that as "no context available", never as an
        exception to propagate.
        """
        if self._context_builder is None:
            return None
        try:
            return await self._context_builder(
                ContextInput(
                    zep=self._client,
                    user_id=self._user_id,
                    thread_id=self._session_id,
                    user_message=user_message,
                    agent=agent,
                )
            )
        except Exception as exc:
            logger.warning("Custom context_builder raised — skipping context injection: %s", exc)
            return None

    async def process_user_message(
        self,
        user_message: str,
        *,
        agent: Any | None = None,
    ) -> str | None:
        """
        Persist a user turn and retrieve memory context in one call.

        This is the manager's own per-turn seam (AG2 has no framework-owned
        equivalent): it persists ``user_message`` to the Zep thread and
        returns a context string ready for injection, or ``None`` if no
        context is available.

        Requires ``session_id`` to be set (a session/thread id is required to
        persist a message).

        Behavior:

        * When ``context_builder`` is set: persistence
          (``thread.add_messages`` **without** ``return_context``) and the
          builder run concurrently via
          ``asyncio.gather(..., return_exceptions=True)``, with **per-side
          isolation** -- a builder failure never blocks persistence from
          completing, and a persistence failure never prevents the builder's
          context from being returned.
        * When unset: a single ``thread.add_messages(..., return_context=True)``
          round-trip both persists the message and retrieves Zep's default
          Context Block.

        Args:
            user_message: The user's message text to persist.
            agent: Optional AG2 agent this call is on behalf of; forwarded to
                the ``context_builder`` via :class:`ContextInput`, else unused.

        Returns:
            The context string to inject, or ``None`` if unavailable
            (builder returned ``None``/raised, or the default retrieval
            returned no context).

        Raises:
            ZepAG2ConfigError: If no session_id is set.
        """
        if not self._session_id:
            raise ZepAG2ConfigError(
                "session_id is required to process a user message. "
                "Set session_id when creating ZepMemoryManager."
            )
        session_id: str = self._session_id

        await self.ensure_user_and_thread()

        message = Message(
            content=_truncate(user_message, MESSAGE_MAX_CHARS, "message content"),
            role="user",
        )

        if self._context_builder is not None:

            async def _persist() -> None:
                await self._client.thread.add_messages(
                    thread_id=session_id,
                    messages=[message],
                )

            persist_result, context_result = await asyncio.gather(
                _persist(),
                self._build_context_via_builder(user_message, agent),
                return_exceptions=True,
            )
            if isinstance(persist_result, BaseException):
                logger.error("Zep thread.add_messages failed: %s", type(persist_result).__name__)
            if isinstance(context_result, BaseException):
                # _build_context_via_builder already isolates builder errors
                # internally; this branch only guards against gather-level
                # surprises so a failure here still degrades to no context.
                logger.warning("Context builder failed: %s", type(context_result).__name__)
                return None
            return context_result

        try:
            add_result = await self._client.thread.add_messages(
                thread_id=session_id,
                messages=[message],
                return_context=True,
            )
            return add_result.context or None
        except Exception as e:
            logger.error("Zep thread.add_messages failed: %s", type(e).__name__)
            return None

    async def get_memory_context(self, query: str | None = None, limit: int = 5) -> str:
        """
        Retrieve relevant memories as a formatted context string.

        If ``context_builder`` is set, it replaces the default retrieval
        below entirely (see :data:`ContextBuilder`). Otherwise: if a query is
        provided, performs semantic search on the user's knowledge graph; if
        a session_id is set, also retrieves thread context.

        Args:
            query: Optional search query for semantic memory retrieval. Also
                forwarded as ``user_message`` on :class:`ContextInput` when a
                ``context_builder`` is set.
            limit: Maximum number of results to return.

        Returns:
            A formatted string containing relevant memory context, or empty string
            if no relevant memories are found.
        """
        if self._context_builder is not None:
            await self.ensure_user_and_thread()
            context_text = await self._build_context_via_builder(query or "", None)
            return context_text or ""

        parts: list[str] = []

        # Get thread context if session_id is set. get_user_context returns a
        # prompt-ready Context Block assembled from the whole user graph, so a
        # separate recent-messages read is redundant.
        if self._session_id:
            try:
                context_result = await self._client.thread.get_user_context(
                    thread_id=self._session_id,
                )
                if context_result.context:
                    parts.append(f"Memory context: {context_result.context}")
            except Exception as e:
                logger.error("Zep get_user_context failed: %s", type(e).__name__)

        # Search knowledge graph if query is provided
        if query:
            try:
                graph_results = await self._client.graph.search(
                    user_id=self._user_id,
                    query=query,
                    limit=limit,
                )
                facts: list[str] = []
                if graph_results.edges:
                    for edge in graph_results.edges:
                        facts.append(f"- {edge.fact}")
                if graph_results.nodes:
                    for node in graph_results.nodes:
                        summary = node.summary or "No summary"
                        facts.append(f"- {node.name}: {summary}")

                if facts:
                    parts.append("Relevant knowledge:\n" + "\n".join(facts))
            except Exception as e:
                logger.error("Zep graph.search failed: %s", type(e).__name__)

        return "\n\n".join(parts)

    async def enrich_system_message(
        self,
        agent: Any,
        query: str | None = None,
        limit: int = 5,
    ) -> None:
        """
        Inject memory context into an AG2 agent's system message.

        Retrieves relevant memories from Zep (via ``context_builder`` if set,
        otherwise the default retrieval -- see :meth:`get_memory_context`),
        wraps them in ``context_template``, and appends them to the agent's
        existing ``system_message`` using ``agent.update_system_message()``.

        Args:
            agent: An AG2 ConversableAgent (or subclass) with system_message
                   and update_system_message() attributes.
            query: Optional search query for semantic retrieval. Also used as
                the builder's ``user_message`` when ``context_builder`` is set.
            limit: Maximum number of memory results.
        """
        if self._context_builder is not None:
            await self.ensure_user_and_thread()
            context_text = await self._build_context_via_builder(query or "", agent)
            if context_text:
                original_msg = agent.system_message
                agent.update_system_message(
                    f"{original_msg}\n\n{self._context_template.replace('{context}', context_text)}"
                )
            return

        context = await self.get_memory_context(query, limit)
        if context:
            original_msg = agent.system_message
            agent.update_system_message(
                f"{original_msg}\n\n{self._context_template.replace('{context}', context)}"
            )

    async def add_messages(self, messages: list[dict[str, str]]) -> None:
        """
        Store messages in Zep for future retrieval.

        Args:
            messages: A list of message dicts, each with 'content', 'role',
                      and optionally 'name' keys.

        Raises:
            ZepAG2ConfigError: If no session_id is set.
            ZepAG2MemoryError: If the Zep API call fails.
        """
        if not self._session_id:
            raise ZepAG2ConfigError(
                "session_id is required to add messages. "
                "Set session_id when creating ZepMemoryManager."
            )

        await self.ensure_user_and_thread()

        try:
            zep_messages = [
                Message(
                    content=_truncate(msg["content"], MESSAGE_MAX_CHARS, "message content"),
                    role=_validate_role(msg.get("role", "user")),
                    name=msg.get("name"),
                )
                for msg in messages
            ]
            await self._client.thread.add_messages(
                thread_id=self._session_id,
                messages=zep_messages,
            )
        except Exception as e:
            logger.error("Zep thread.add_messages failed: %s", type(e).__name__)
            raise ZepAG2MemoryError("Failed to add messages") from e

    async def get_session_facts(self) -> list[str]:
        """
        Get extracted facts from the current session.

        Returns:
            A list of fact strings extracted from the session.

        Raises:
            ZepAG2ConfigError: If no session_id is set.
        """
        if not self._session_id:
            raise ZepAG2ConfigError("session_id is required to get session facts.")

        try:
            context_result = await self._client.thread.get_user_context(
                thread_id=self._session_id,
            )
            if context_result.context:
                return [context_result.context]
            return []
        except Exception as e:
            logger.error("Zep get_user_context failed: %s", type(e).__name__)
            return []

    # -----------------------------------------------------------------
    # Automatic loop (attach_to_agent)
    # -----------------------------------------------------------------

    def attach_to_agent(self, agent: Any) -> None:
        """
        Register an automatic Zep memory loop on an AG2 agent.

        AG2 has no native memory interface; ``ConversableAgent.register_hook``
        is the framework's per-turn seam. This registers **two** hooks:

        * ``process_last_received_message`` -- fires for every message the
          agent receives. Bridges (via the package's ``_run_sync`` background
          loop) into :meth:`process_user_message` to persist the incoming
          message and retrieve context, then injects that context into the
          agent's system message via ``agent.update_system_message()``
          (**replaces** the system message wholesale -- AG2's
          ``update_system_message`` has no append mode, so the hook keeps the
          agent's original system message text as a stable prefix across
          calls and appends the freshly-rendered ``context_template`` after
          it).
        * ``process_message_before_send`` -- fires for every message the
          agent sends. AG2's hook contract here is clean enough to persist
          through: it receives the outgoing ``message`` (a ``str`` or a dict
          with a ``content`` key) and the hook can return it unchanged. This
          persists the agent's reply as an ``assistant`` message, completing
          the inject+persist loop automatically (previously, in this
          package, assistant-reply persistence required a manual
          ``add_messages()`` call).

        Both hooks return their input **unmodified** -- this is a
        persist/inject side channel, not a message transform -- and both
        wrap their entire body in ``try/except`` so a Zep outage or any
        internal failure never breaks the agent's conversation loop; on
        failure the incoming hook simply skips the system-message update.

        Multi-agent caveat: attach this to exactly one agent per Zep thread
        (normally the user-facing agent) -- if two agents both attach managers
        pointing at the same ``session_id``, each turn is persisted twice with
        conflicting roles (one agent's outgoing hook persists it as
        ``assistant``, the other's incoming hook persists the same content as
        ``user``); see the README's "Multi-agent caveat" section for the
        correct wiring.

        Args:
            agent: An AG2 ``ConversableAgent`` (or subclass) exposing
                ``register_hook``, ``system_message``, and
                ``update_system_message``.
        """
        base_system_message = agent.system_message

        def _on_last_received_message(user_content: str) -> str:
            try:
                if not isinstance(user_content, str):
                    return user_content
                context_text = _run_sync(self.process_user_message(user_content, agent=agent))
                if context_text:
                    rendered = self._context_template.replace("{context}", context_text)
                    agent.update_system_message(f"{base_system_message}\n\n{rendered}")
            except Exception as exc:
                logger.warning("attach_to_agent: incoming-message hook failed: %s", exc)
            return user_content

        def _on_message_before_send(sender: Any, message: Any, recipient: Any, silent: bool) -> Any:
            try:
                content = message.get("content") if isinstance(message, dict) else message
                if isinstance(content, str) and content:
                    _run_sync(self._persist_assistant_reply(content))
            except Exception as exc:
                logger.warning("attach_to_agent: outgoing-message hook failed: %s", exc)
            return message

        agent.register_hook("process_last_received_message", _on_last_received_message)
        agent.register_hook("process_message_before_send", _on_message_before_send)

    async def _persist_assistant_reply(self, content: str) -> None:
        """Persist an assistant-authored message, used by the outgoing hook.

        Requires ``session_id``; a missing session_id is treated the same as
        any other failure here -- logged and swallowed, since this is called
        from inside the (already try/except-wrapped) ``attach_to_agent`` hook.
        """
        if not self._session_id:
            return
        await self.ensure_user_and_thread()
        message = Message(
            content=_truncate(content, MESSAGE_MAX_CHARS, "message content"),
            role="assistant",
        )
        await self._client.thread.add_messages(
            thread_id=self._session_id,
            messages=[message],
        )

    # Sync wrappers for non-async AG2 usage

    def get_memory_context_sync(self, query: str | None = None, limit: int = 5) -> str:
        """Synchronous wrapper for get_memory_context().

        Bridges to async via the package's shared background event loop, so it
        works on Python 3.11–3.13 and whether or not a loop is already running.
        """
        return str(_run_sync(self.get_memory_context(query, limit)))

    def enrich_system_message_sync(
        self,
        agent: Any,
        query: str | None = None,
        limit: int = 5,
    ) -> None:
        """Synchronous wrapper for enrich_system_message().

        Bridges to async via the package's shared background event loop.
        """
        _run_sync(self.enrich_system_message(agent, query, limit))
