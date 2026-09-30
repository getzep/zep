"""
ZepContextProvider -- a Microsoft Agent Framework ``ContextProvider`` backed by Zep.

This provider plugs into the Agent Framework *context engineering* pipeline.  It
subclasses :class:`agent_framework.ContextProvider` and overrides the two
lifecycle hooks the framework calls around every ``agent.run(...)``:

* :meth:`ZepContextProvider.before_run` -- runs **before** the model is invoked.
  It extracts the latest user message from the invocation context, persists it
  to the user's Zep thread, and injects the returned Context Block (facts,
  relationships, and prior knowledge from the *whole* user graph) into the
  model's instructions.
* :meth:`ZepContextProvider.after_run` -- runs **after** the model responds.
  It persists the assistant's reply back to the same Zep thread so both sides
  of the conversation are captured in long-term memory.

Persistence and retrieval are folded into a single ``thread.add_messages(
return_context=True)`` round-trip on the way in, matching Zep's recommended
low-latency pattern.

Zep v4 addresses a user, a thread, and a graph by a server-generated UUID.
The provider therefore takes ``user_uuid`` and ``thread_uuid``, and it does
not create Zep resources. Create the user and the thread out-of-band with
:func:`~zep_ms_agent_framework.provisioning.create_user` and
:func:`~zep_ms_agent_framework.provisioning.create_thread`, store the UUIDs
in your own database, and give them to the provider.

Every Zep call is wrapped so that a Zep failure is logged but never propagates
into the host agent: a memory outage degrades the agent to a memoryless one
rather than crashing it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agent_framework import ContextProvider
from zep_cloud import AddMessage
from zep_cloud.client import AsyncZep

from ._text import truncate_message_content
from .search import create_zep_search_tool

if TYPE_CHECKING:
    from agent_framework import (
        AgentSession,
        Message,
        SessionContext,
        SupportsAgentRun,
    )

    from .search import ZepSearchTool

logger = logging.getLogger(__name__)

#: Default ``source_id`` used to attribute this provider's contributions in the
#: Agent Framework context pipeline.
DEFAULT_SOURCE_ID = "zep"

#: Default template used to wrap retrieved Zep context before injecting it
#: into the agent's instructions.  Rendered via plain string replacement
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
        zep: The ``AsyncZep`` client in use by the provider.
        user_uuid: The UUID of the Zep user this provider is scoped to.
        thread_uuid: The UUID of the Zep thread this provider records the
            conversation in.
        graph_uuid: The UUID of the graph of the user, when the application
            supplied it.  ``None`` if it was not supplied.
        user_message: The user's message text for this turn.
        session_context: The Agent Framework ``SessionContext`` for this turn
            (add instructions/tools/messages here if the builder needs to).

    Example:
        A builder that searches a per-user graph instead of using the
        thread's default context retrieval::

            async def my_builder(ctx: ContextInput) -> str | None:
                if ctx.graph_uuid is None:
                    return None
                edges = await ctx.zep.graph.search_edges(
                    ctx.graph_uuid,
                    query=ctx.user_message,
                )
                if not edges.items:
                    return None
                return "\\n".join(edge.fact for edge in edges.items if edge.fact)

            provider = ZepContextProvider(
                zep_client=zep,
                user_uuid=user.uuid_,
                thread_uuid=thread.uuid_,
                graph_uuid=user.graph_uuid,
                context_builder=my_builder,
            )
    """

    zep: AsyncZep
    user_uuid: str
    thread_uuid: str
    user_message: str
    graph_uuid: str | None = None
    session_context: SessionContext | None = None


#: Type alias for a custom context builder function.
#:
#: A context builder receives a single :class:`ContextInput` and returns the
#: context string to inject into the agent's instructions (or ``None`` to
#: skip injection).
#:
#: Error semantics: if the builder raises, ``ZepContextProvider`` logs a
#: warning and skips injection for that turn -- it does not crash the agent
#: run and does not prevent message persistence from completing.  See
#: :class:`ZepContextProvider` for the full error-isolation contract between
#: persistence and the builder.
ContextBuilder = Callable[[ContextInput], Awaitable[str | None]]


class ZepContextProvider(ContextProvider):
    """Give a Microsoft Agent Framework ``Agent`` long-term memory via Zep.

    Attach an instance to an agent through the ``context_providers`` keyword
    argument::

        from agent_framework import Agent
        from agent_framework.openai import OpenAIChatClient
        from zep_cloud.client import AsyncZep
        from zep_ms_agent_framework import ZepContextProvider

        zep = AsyncZep(api_key="...")
        agent = Agent(
            OpenAIChatClient(model="gpt-5-mini"),
            instructions="You are a helpful assistant with long-term memory.",
            context_providers=[
                ZepContextProvider(
                    zep_client=zep,
                    user_uuid="1f0f8b1e-...",
                    thread_uuid="3a2c4d5e-...",
                )
            ],
        )

    On every run the provider:

    1. Reads the latest user message from ``context.input_messages``.
    2. Persists the message via ``thread.add_messages(return_context=True)`` and
       injects the returned Context Block into the model's instructions.
    3. After the model responds, persists the assistant reply.

    The provider is **async-only**: it requires an :class:`~zep_cloud.client.AsyncZep`
    client, which it does not own -- the caller is responsible for the client's
    lifecycle.

    Args:
        zep_client: An initialised :class:`~zep_cloud.client.AsyncZep` client.
        user_uuid: The UUID of the Zep user this provider's memory is scoped
            to.  Zep returns it from ``user.create``; the application stores
            it.  The provider does not create the user.
        thread_uuid: The UUID of the Zep thread used to record the
            conversation.  Zep returns it from ``thread.create``.  The thread
            scopes *relevance* for the Context Block; facts are still
            extracted into the whole user graph.
        graph_uuid: The UUID of the graph of the user, from ``user.create``.
            It is required when ``expose_search_tool=True``, because Zep v4
            addresses a graph search by graph UUID.  It is also passed to a
            ``context_builder`` in :class:`ContextInput`.
        user_message_name: Display name attached to persisted user messages.
            Defaults to ``None``.
        assistant_message_name: Display name attached to persisted assistant
            messages.  Defaults to ``"Assistant"``.
        source_id: The Agent Framework ``source_id`` used to attribute this
            provider's instructions.  Defaults to ``"zep"``.
        ignore_roles: An optional list of message roles to exclude from Zep's
            knowledge-graph ingestion.  Messages with these roles are still
            stored in the thread history but are not processed into the graph.
        context_builder: An optional async callable that constructs the
            context block to inject into the agent's instructions, in place
            of the default ``thread.add_messages(return_context=True)``
            retrieval.  Receives a single :class:`ContextInput`.  When set,
            message persistence and context building run **concurrently**
            for lower latency -- see the Note below for the error-isolation
            contract between the two.
        context_template: Template used to wrap retrieved context before
            injecting it into the agent's instructions.  Must contain a
            literal ``{context}`` placeholder, replaced via plain string
            replacement (never ``str.format``).  Defaults to
            :data:`DEFAULT_CONTEXT_TEMPLATE`.
        expose_search_tool: When ``True``, ``before_run`` also registers a
            model-callable graph-search tool via
            ``context.extend_tools(self.source_id, [tool])``.  The model can
            then decide when to search the graph for specific facts,
            entities, or prior episodes, in addition to the context
            automatically injected on every turn.  Defaults to ``False``.
        search_pinned_params: Optional mapping of search parameter name
            (``scope``, ``reranker``, ``limit``, ``mmr_lambda``,
            ``center_node_uuid``) to a fixed value.  Pinned parameters are
            hidden from the model's tool schema and always sent with the
            given value.  Only meaningful when ``expose_search_tool=True``.
        search_hidden_params: Optional set of search parameter names to hide
            from the model's tool schema without pinning them
            -- omitted from the SDK call so Zep's own default applies.  Only
            meaningful when ``expose_search_tool=True``.
        search_filters: Optional Zep search filters (constructor-only, never
            exposed to the model).  Supports ``node_labels``, ``edge_types``,
            ``exclude_node_labels``, ``exclude_edge_types``, and property
            filters.  Only meaningful when ``expose_search_tool=True``.
        bfs_origin_node_uuids: Optional list of node UUIDs for BFS seeding
            (constructor-only).  Only meaningful when
            ``expose_search_tool=True``.

    Note:
        **Per-run identity is bound at construction, not resolved per-run.**
        ``user_uuid``/``thread_uuid`` are fixed constructor arguments; they are
        *not* re-resolved from ``before_run``'s ``session``/``state``
        keyword arguments on each call. This was investigated when adding
        the graph-search tool (dimension H): the Agent Framework's
        ``AgentSession`` exposes only ``session_id``,
        ``service_session_id``, and a generic ``state`` dict with no
        identity semantics -- there is no ``user_id`` anywhere in
        ``SupportsAgentRun.run()``'s signature, ``AgentSession``, or
        ``SessionContext``, and no framework convention (documented or in
        the installed source) for stashing identity in ``session.state``,
        unlike e.g. Google ADK's documented ``tool_context.state["zep_user_id"]``
        pattern. The ``state`` dict actually passed to ``before_run``/
        ``after_run`` is additionally scoped per-provider
        (``session.state.setdefault(source_id, {})``), not the full session
        state, which would make any such convention awkward to rely on even
        if the framework adopted one later.

        For a multi-user application, construct one ``ZepContextProvider``
        (and typically one ``Agent``) per user/conversation rather than
        sharing a single instance across users. If per-run resolution
        becomes possible in a future Agent Framework release, this is the
        place to add it.

    Note:
        **Error isolation between persistence and the context builder.**
        When ``context_builder`` is set, persistence (``add_messages``) and
        the builder run concurrently. Each is isolated from the other's
        failure:

        * If the builder raises, a warning is logged and injection is
          skipped for this turn -- but persistence still completes and the
          turn is marked as persisted (so ``after_run`` may write the
          assistant reply) on success.
        * If persistence raises, a warning is logged and the turn is
          **not** marked as persisted (so ``after_run`` skips this turn and
          it can be retried on the next invocation) -- but a successful
          builder result may still be injected into the prompt.
    """

    def __init__(
        self,
        *,
        zep_client: AsyncZep,
        user_uuid: str,
        thread_uuid: str,
        graph_uuid: str | None = None,
        user_message_name: str | None = None,
        assistant_message_name: str = "Assistant",
        source_id: str = DEFAULT_SOURCE_ID,
        ignore_roles: list[str] | None = None,
        context_builder: ContextBuilder | None = None,
        context_template: str = DEFAULT_CONTEXT_TEMPLATE,
        expose_search_tool: bool = False,
        search_pinned_params: dict[str, Any] | None = None,
        search_hidden_params: set[str] | None = None,
        search_filters: dict[str, Any] | None = None,
        bfs_origin_node_uuids: list[str] | None = None,
    ) -> None:
        super().__init__(source_id=source_id)

        if not user_uuid:
            raise ValueError("user_uuid must be a non-empty string")
        if not thread_uuid:
            raise ValueError("thread_uuid must be a non-empty string")
        if expose_search_tool and not graph_uuid:
            raise ValueError("graph_uuid is required when expose_search_tool is True")

        self._zep: AsyncZep = zep_client
        self._user_uuid: str = user_uuid
        self._thread_uuid: str = thread_uuid
        self._graph_uuid: str | None = graph_uuid

        self._user_message_name: str | None = user_message_name
        self._assistant_message_name: str = assistant_message_name

        self._ignore_roles: list[str] | None = ignore_roles
        self._context_builder: ContextBuilder | None = context_builder
        self._context_template: str = context_template

        # Pre-build the search tool once (it's immutable after construction)
        # so before_run only needs to register it, never rebuild its schema.
        self._expose_search_tool: bool = expose_search_tool
        self._search_tool: ZepSearchTool | None = (
            create_zep_search_tool(
                zep_client=self._zep,
                graph_uuid=graph_uuid,
                search_pinned_params=search_pinned_params,
                search_hidden_params=search_hidden_params,
                search_filters=search_filters,
                bfs_origin_node_uuids=bfs_origin_node_uuids,
            )
            if expose_search_tool and graph_uuid
            else None
        )

        # Whether THIS run's user turn was actually persisted in before_run.
        # Reset at the start of every before_run and gated on in after_run so an
        # assistant-only record is never written when the user turn failed.  This
        # is intentionally per-run instance state, not the per-session ``state``
        # dict, so a failure in one run cannot leak into a later run.
        self._user_turn_persisted: bool = False

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def user_uuid(self) -> str:
        """The UUID of the Zep user this provider is scoped to."""
        return self._user_uuid

    @property
    def thread_uuid(self) -> str:
        """The UUID of the Zep thread this provider records the conversation in."""
        return self._thread_uuid

    @property
    def graph_uuid(self) -> str | None:
        """The UUID of the graph of the user, when the application supplied it."""
        return self._graph_uuid

    # ------------------------------------------------------------------
    # Message extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _latest_user_text(messages: list[Message]) -> str | None:
        """Return the concatenated text of the last user message, or ``None``.

        Scans backwards so multi-message inputs resolve to the most recent user
        turn.  Falls back to the last message with any text when no message is
        explicitly tagged with the ``user`` role.
        """
        for message in reversed(messages):
            if message.role == "user" and message.text:
                return message.text

        # Fallback: most recent message carrying text (some callers send a bare
        # string, which the framework normalises to a user message anyway).
        for message in reversed(messages):
            if message.text:
                return message.text
        return None

    @staticmethod
    def _assistant_text(messages: list[Message] | None) -> str | None:
        """Return the concatenated assistant text from a response, or ``None``."""
        if not messages:
            return None
        parts = [m.text for m in messages if m.role == "assistant" and m.text]
        if not parts:
            return None
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------

    async def before_run(
        self,
        *,
        agent: SupportsAgentRun,
        session: AgentSession,
        context: SessionContext,
        state: dict[str, Any],
    ) -> None:
        """Persist the latest user message and inject Zep's Context Block.

        Called by the Agent Framework before each model invocation.  Reads the
        latest user message from ``context.input_messages``, persists it to
        Zep, and adds the returned Context Block to ``context`` via
        ``extend_instructions`` so it becomes part of the model's system
        prompt.  When ``expose_search_tool`` is enabled, also registers the
        graph-search tool via ``context.extend_tools``.

        By default, persistence and retrieval are folded into a single
        ``thread.add_messages(return_context=True)`` round-trip.  When a
        ``context_builder`` is configured, persistence (``add_messages``
        without ``return_context``) and the builder run **concurrently**
        instead -- see the class docstring for the error-isolation contract.

        A Zep failure is logged and the run continues without injected memory.

        Note on identity: this provider resolves ``user_uuid``/``thread_uuid``
        from constructor arguments, not from ``session``/``state``. See the
        class docstring's "per-run identity" note for why and what to do in
        multi-user deployments.
        """
        # Reset the per-run flag: until this run's user turn is persisted below,
        # after_run must not write an orphaned assistant-only record.
        self._user_turn_persisted = False

        if self._search_tool is not None:
            context.extend_tools(self.source_id, [self._search_tool])

        user_text = self._latest_user_text(context.input_messages)
        if not user_text:
            return

        # Guard against Zep's 4,096-char message limit: truncate (never silently
        # drop) so the turn is persisted instead of triggering a swallowed 400.
        user_text = truncate_message_content(user_text, label="user message")

        if self._context_builder is not None:
            persist_ok, context_block = await self._persist_and_build_context(user_text, context)
        else:
            persist_ok, context_block = await self._persist_with_return_context(user_text)

        # Mark as persisted only AFTER the API call succeeded, so that a
        # transient failure does not permanently suppress the message.
        self._user_turn_persisted = persist_ok

        if context_block:
            context.extend_instructions(self.source_id, self._format_context(context_block))
            logger.debug(
                "Injected Zep context (%d chars) into agent instructions",
                len(context_block),
            )

    async def _persist_with_return_context(self, user_text: str) -> tuple[bool, str | None]:
        """Persist the user message and retrieve context in one round-trip.

        Returns:
            A ``(persist_ok, context_block)`` tuple.  On failure, logs a
            warning and returns ``(False, None)``.
        """
        try:
            response = await self._zep.thread.add_messages(
                self._thread_uuid,
                messages=[
                    AddMessage(
                        role="user",
                        content=user_text,
                        name=self._user_message_name,
                    )
                ],
                return_context=True,
                ignore_roles=self._ignore_roles,
            )
            context_block = response.context if response else None
            logger.info(
                "Persisted user message to Zep (thread=%s). Context length: %s",
                self._thread_uuid,
                len(context_block) if context_block else 0,
            )
            return True, context_block
        except Exception:
            logger.warning(
                "Failed to persist user message / retrieve context from Zep",
                exc_info=True,
            )
            return False, None

    async def _persist_and_build_context(
        self,
        user_text: str,
        session_context: SessionContext,
    ) -> tuple[bool, str | None]:
        """Persist the message and build context concurrently.

        Runs ``thread.add_messages`` (without ``return_context``) and the
        custom ``context_builder`` concurrently via
        ``asyncio.gather(..., return_exceptions=True)`` so one side's
        exception can never cancel or mask the other's result.

        * If the builder raises, a warning is logged and ``None`` is used for
          the context, but persistence is unaffected.
        * If persistence raises, a warning is logged and ``persist_ok=False``
          is returned (so the caller does not mark the turn as persisted),
          but a successful builder result is still returned for injection.

        Returns:
            A ``(persist_ok, context_block)`` tuple.
        """
        context_builder = self._context_builder
        assert context_builder is not None  # caller already checked

        async def _persist() -> None:
            await self._zep.thread.add_messages(
                self._thread_uuid,
                messages=[
                    AddMessage(
                        role="user",
                        content=user_text,
                        name=self._user_message_name,
                    )
                ],
                ignore_roles=self._ignore_roles,
            )
            logger.info("Persisted user message to Zep (thread=%s).", self._thread_uuid)

        async def _build() -> str | None:
            context_input = ContextInput(
                zep=self._zep,
                user_uuid=self._user_uuid,
                thread_uuid=self._thread_uuid,
                user_message=user_text,
                graph_uuid=self._graph_uuid,
                session_context=session_context,
            )
            return await context_builder(context_input)

        persist_result, build_result = await asyncio.gather(
            _persist(), _build(), return_exceptions=True
        )

        if isinstance(build_result, BaseException):
            logger.warning(
                "Custom context_builder raised — skipping context injection for this turn",
                exc_info=build_result,
            )
            context_block: str | None = None
        else:
            context_block = build_result

        if isinstance(persist_result, BaseException):
            logger.warning(
                "Failed to persist user message to Zep",
                exc_info=persist_result,
            )
            return False, context_block

        return True, context_block

    async def after_run(
        self,
        *,
        agent: SupportsAgentRun,
        session: AgentSession,
        context: SessionContext,
        state: dict[str, Any],
    ) -> None:
        """Persist the assistant's response to Zep.

        Called by the Agent Framework after the model responds.  Reads the
        assistant text from ``context.response.messages`` and stores it on the
        same Zep thread, capturing both sides of the conversation.

        A Zep failure is logged and never propagated to the host agent.
        """
        response = context.response
        if response is None:
            return

        assistant_text = self._assistant_text(response.messages)
        if not assistant_text:
            return

        # Only persist the assistant turn if THIS run's user turn was actually
        # written in before_run.  Otherwise we would record an orphaned
        # assistant-only turn with no user message to anchor it to.
        if not self._user_turn_persisted:
            logger.debug(
                "Skipping assistant persist: this run's user turn was not persisted (thread=%s).",
                self._thread_uuid,
            )
            return

        # Guard against Zep's 4,096-char message limit: truncate (never silently
        # drop) so the turn is persisted instead of triggering a swallowed 400.
        assistant_text = truncate_message_content(assistant_text, label="assistant message")

        try:
            await self._zep.thread.add_messages(
                self._thread_uuid,
                messages=[
                    AddMessage(
                        role="assistant",
                        content=assistant_text,
                        name=self._assistant_message_name,
                    )
                ],
                ignore_roles=self._ignore_roles,
            )
            logger.info(
                "Persisted assistant response to Zep thread %s (%d chars)",
                self._thread_uuid,
                len(assistant_text),
            )
        except Exception:
            logger.warning(
                "Failed to persist assistant response to Zep",
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _format_context(self, context_block: str) -> str:
        """Wrap Zep's Context Block using ``self._context_template``.

        Rendered via plain string replacement (``template.replace("{context}",
        context_block)``), never ``str.format`` -- so context text or a custom
        template containing ``{``/``}``/``%`` is always safe to inject.
        """
        return self._context_template.replace("{context}", context_block)
