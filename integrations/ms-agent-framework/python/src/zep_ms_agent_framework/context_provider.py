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

The Zep user and thread are created lazily on the first run and the result is
cached per provider instance, so repeated runs incur no extra setup calls.

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
from zep_cloud import Message as ZepMessage
from zep_cloud.client import AsyncZep

from ._text import truncate_message_content
from .provisioning import UserSetupHook
from .provisioning import ensure_thread as _ensure_thread
from .provisioning import ensure_user as _ensure_user
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
        user_id: The Zep user ID this provider is scoped to.
        thread_id: The Zep thread ID this provider records the conversation in.
        user_message: The user's message text for this turn.
        session_context: The Agent Framework ``SessionContext`` for this turn
            (add instructions/tools/messages here if the builder needs to).

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

            provider = ZepContextProvider(
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
                    user_id="user-123",
                    thread_id="thread-abc",
                    first_name="Jane",
                    last_name="Smith",
                    email="jane@example.com",
                )
            ],
        )

    On every run the provider:

    1. Reads the latest user message from ``context.input_messages``.
    2. Lazily creates the Zep user and thread (once per instance).
    3. Persists the message via ``thread.add_messages(return_context=True)`` and
       injects the returned Context Block into the model's instructions.
    4. After the model responds, persists the assistant reply.

    The provider is **async-only**: it requires an :class:`~zep_cloud.client.AsyncZep`
    client, which it does not own -- the caller is responsible for the client's
    lifecycle.

    Args:
        zep_client: An initialised :class:`~zep_cloud.client.AsyncZep` client.
        user_id: The Zep user ID this provider's memory is scoped to.  A user
            graph is created for this ID on first use.
        thread_id: The Zep thread ID used to record the conversation.  The
            thread scopes *relevance* for the Context Block; facts are still
            extracted into the whole user graph.
        first_name: Optional user first name.  Passing real names helps Zep
            resolve the user's identity node in the graph.  Defaults to ``None``.
        last_name: Optional user last name.  Defaults to ``None``.
        email: Optional user email.  Defaults to ``None``.
        user_message_name: Display name attached to persisted user messages.
            Defaults to the user's full name when available, otherwise ``None``.
        assistant_message_name: Display name attached to persisted assistant
            messages.  Defaults to ``"Assistant"``.
        source_id: The Agent Framework ``source_id`` used to attribute this
            provider's instructions.  Defaults to ``"zep"``.
        ignore_roles: An optional list of message roles to exclude from Zep's
            knowledge-graph ingestion.  Messages with these roles are still
            stored in the thread history but are not processed into the graph.
        on_user_created: An optional async callable invoked exactly once, right
            after a new Zep user is created.  Use it to set up per-user
            ontology, custom instructions, or user summary instructions.  It
            does **not** fire for users that already exist.  Passed through
            to :func:`~zep_ms_agent_framework.provisioning.ensure_user` as its
            ``on_created`` hook, so a hook failure is treated the same as a
            genuine provisioning failure: it is logged and this turn's Zep
            persistence is skipped (never raised into the run), and resource
            creation is retried on the next turn.  Contrast this with calling
            :func:`~zep_ms_agent_framework.provisioning.ensure_user` directly,
            out-of-band, where a hook failure **propagates** to the caller --
            the in-provider lazy path always swallows, out-of-band callers
            always see the error.
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
        search_pinned_params: Optional mapping of ``graph.search`` parameter
            name (``scope``, ``reranker``, ``limit``, ``mmr_lambda``,
            ``center_node_uuid``) to a fixed value.  Pinned parameters are
            hidden from the model's tool schema and always sent with the
            given value.  Only meaningful when ``expose_search_tool=True``.
        search_hidden_params: Optional set of ``graph.search`` parameter
            names to hide from the model's tool schema without pinning them
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
        ``user_id``/``thread_id`` are fixed constructor arguments; they are
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
        user_id: str,
        thread_id: str,
        first_name: str | None = None,
        last_name: str | None = None,
        email: str | None = None,
        user_message_name: str | None = None,
        assistant_message_name: str = "Assistant",
        source_id: str = DEFAULT_SOURCE_ID,
        ignore_roles: list[str] | None = None,
        on_user_created: UserSetupHook | None = None,
        context_builder: ContextBuilder | None = None,
        context_template: str = DEFAULT_CONTEXT_TEMPLATE,
        expose_search_tool: bool = False,
        search_pinned_params: dict[str, Any] | None = None,
        search_hidden_params: set[str] | None = None,
        search_filters: dict[str, Any] | None = None,
        bfs_origin_node_uuids: list[str] | None = None,
    ) -> None:
        super().__init__(source_id=source_id)

        if not user_id:
            raise ValueError("user_id must be a non-empty string")
        if not thread_id:
            raise ValueError("thread_id must be a non-empty string")

        self._zep: AsyncZep = zep_client
        self._user_id: str = user_id
        self._thread_id: str = thread_id
        self._first_name: str | None = first_name
        self._last_name: str | None = last_name
        self._email: str | None = email

        # Default the user message display name to the user's full name when one
        # was supplied, so the graph can anchor identity even without an explicit
        # name override.
        full_name = " ".join(part for part in (first_name, last_name) if part).strip()
        self._user_message_name: str | None = user_message_name or (full_name or None)
        self._assistant_message_name: str = assistant_message_name

        self._ignore_roles: list[str] | None = ignore_roles
        self._on_user_created: UserSetupHook | None = on_user_created
        self._context_builder: ContextBuilder | None = context_builder
        self._context_template: str = context_template

        # Pre-build the search tool once (it's immutable after construction)
        # so before_run only needs to register it, never rebuild its schema.
        self._expose_search_tool: bool = expose_search_tool
        self._search_tool: ZepSearchTool | None = (
            create_zep_search_tool(
                zep_client=self._zep,
                user_id=self._user_id,
                search_pinned_params=search_pinned_params,
                search_hidden_params=search_hidden_params,
                search_filters=search_filters,
                bfs_origin_node_uuids=bfs_origin_node_uuids,
            )
            if expose_search_tool
            else None
        )

        # Whether the Zep user + thread have been created (or confirmed to
        # already exist) for this provider instance.  Cached so repeated runs
        # do not re-issue setup calls.
        self._resources_ready: bool = False

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
    def user_id(self) -> str:
        """The Zep user ID this provider is scoped to."""
        return self._user_id

    @property
    def thread_id(self) -> str:
        """The Zep thread ID this provider records the conversation in."""
        return self._thread_id

    # ------------------------------------------------------------------
    # Lazy resource creation
    # ------------------------------------------------------------------

    async def _ensure_resources(self) -> bool:
        """Create the Zep user and thread if they do not already exist.

        Delegates to :func:`~zep_ms_agent_framework.provisioning.ensure_user`
        and :func:`~zep_ms_agent_framework.provisioning.ensure_thread`, the
        same create-then-catch-conflict helpers available for out-of-band
        provisioning. Idempotent and cached: succeeds for users/threads that
        already exist and only runs the ``on_user_created`` hook for
        genuinely new users.

        This is the hot path: unlike calling :func:`~.provisioning.ensure_user`
        directly (where a genuine failure or an ``on_created`` hook error
        propagates to the caller), here every failure -- including a hook
        failure -- is logged and swallowed so a Zep or setup-code outage
        never raises into ``before_run``/``after_run``. Out-of-band callers
        that need loud failures should call ``ensure_user``/``ensure_thread``
        directly instead of relying on this lazy path.

        Returns:
            ``True`` if the user and thread are ready (created or pre-existing),
            ``False`` on a genuine failure (so the caller can skip this turn and
            retry on the next).
        """
        if self._resources_ready:
            return True

        try:
            await _ensure_user(
                self._zep,
                user_id=self._user_id,
                first_name=self._first_name,
                last_name=self._last_name,
                email=self._email,
                on_created=self._on_user_created,
            )
        except Exception as exc:
            # Covers both a genuine SDK failure and an on_user_created hook
            # error -- either way, the hot path must degrade, not raise.
            logger.warning("Failed to create Zep user %s: %s", self._user_id, exc)
            return False

        try:
            await _ensure_thread(self._zep, thread_id=self._thread_id, user_id=self._user_id)
        except Exception as exc:
            logger.warning("Failed to create Zep thread %s: %s", self._thread_id, exc)
            return False

        self._resources_ready = True
        return True

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

        Note on identity: this provider resolves ``user_id``/``thread_id``
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

        if not await self._ensure_resources():
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
                thread_id=self._thread_id,
                messages=[
                    ZepMessage(
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
                self._thread_id,
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
                thread_id=self._thread_id,
                messages=[
                    ZepMessage(
                        role="user",
                        content=user_text,
                        name=self._user_message_name,
                    )
                ],
                ignore_roles=self._ignore_roles,
            )
            logger.info("Persisted user message to Zep (thread=%s).", self._thread_id)

        async def _build() -> str | None:
            context_input = ContextInput(
                zep=self._zep,
                user_id=self._user_id,
                thread_id=self._thread_id,
                user_message=user_text,
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
                self._thread_id,
            )
            return

        # Guard against Zep's 4,096-char message limit: truncate (never silently
        # drop) so the turn is persisted instead of triggering a swallowed 400.
        assistant_text = truncate_message_content(assistant_text, label="assistant message")

        try:
            await self._zep.thread.add_messages(
                thread_id=self._thread_id,
                messages=[
                    ZepMessage(
                        role="assistant",
                        content=assistant_text,
                        name=self._assistant_message_name,
                    )
                ],
                ignore_roles=self._ignore_roles,
            )
            logger.info(
                "Persisted assistant response to Zep thread %s (%d chars)",
                self._thread_id,
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
