"""
ZepContextTool -- an ADK BaseTool that integrates Zep memory into LLM requests.

This tool subclasses ADK's ``BaseTool`` and overrides ``process_llm_request()``
-- the same hook that ADK's own ``PreloadMemoryTool`` uses.  On every LLM turn
it:

  1. Extracts the user's latest message from the invocation context.
  2. Resolves the user's Zep identity from ADK session state.
  3. Persists the message to Zep and retrieves a context block -- either
     via ``thread.add_messages(return_context=True)`` (default, single
     round-trip) or by running a custom ``context_builder`` in parallel
     with message persistence for advanced use cases (multi-graph,
     custom templates, filtered searches).
  4. Injects the returned context into the LLM's system instruction via
     ``llm_request.append_instructions()``.

The tool is never called by the model directly; it only modifies the outgoing
LLM request before it is sent.

Identity is resolved at runtime from ADK session state, allowing a single
``ZepContextTool`` instance to be shared across all users/sessions.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from typing_extensions import override
from zep_cloud import Message
from zep_cloud.client import AsyncZep

from .limits import truncate_message_content
from .provisioning import UserSetupHook  # noqa: F401  (re-exported for compatibility)

if TYPE_CHECKING:
    from google.adk.models import LlmRequest

#: Default template used to wrap retrieved Zep context before injecting it
#: into the LLM's system instructions.  Rendered via plain string
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
        zep: The ``AsyncZep`` client in use by the tool.
        user_id: The resolved Zep user ID for this turn.
        thread_id: The resolved Zep thread ID for this turn.
        user_message: The user's message text for this turn.
        tool_context: The ADK ``ToolContext`` for this turn (session state,
            invocation metadata).
        llm_request: The outgoing ``LlmRequest`` about to be sent to the model.

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

            tool = ZepContextTool(zep_client=zep, context_builder=my_builder)
    """

    zep: AsyncZep
    user_id: str
    thread_id: str
    user_message: str
    tool_context: ToolContext
    llm_request: LlmRequest


#: Type alias for a custom context builder function.
#:
#: A context builder receives a single :class:`ContextInput` and returns the
#: context string to inject into the LLM prompt (or ``None`` to skip
#: injection).
#:
#: Error semantics: if the builder raises, ``ZepContextTool`` logs a warning
#: and skips injection for that turn -- it does not crash the agent and does
#: not prevent message persistence from completing. See
#: :class:`ZepContextTool` for the full error-isolation contract between
#: persistence and the builder.
ContextBuilder = Callable[[ContextInput], Awaitable[str | None]]

logger = logging.getLogger(__name__)

#: Substrings that identify a Zep "not found" error on the turn path (message
#: persistence failing because the user/thread was never provisioned).
_NOT_FOUND_MARKERS = ("not found", "404")


@dataclass
class _ZepIdentity:
    """Resolved Zep identity from ADK session state."""

    user_id: str
    thread_id: str
    first_name: str | None
    last_name: str | None
    user_display_name: str | None


class ZepContextTool(BaseTool):
    """Automatically persist user messages to Zep and inject memory context.

    This tool hooks into the ADK request lifecycle.  It is **not** invoked by
    the model -- instead, ``process_llm_request`` runs before every LLM call,
    giving it the opportunity to persist the latest user message to Zep and
    prepend relevant context from Zep's long-term memory.

    Identity (user ID, thread ID, name) is resolved at runtime from ADK
    session state, so a single tool instance can be shared across all
    users and sessions.

    Args:
        zep_client: An initialised ``AsyncZep`` client.
        ignore_roles: An optional list of message roles (e.g. ``["assistant"]``)
            to exclude from Zep's knowledge graph ingestion.  Messages with
            these roles are still stored in the thread history but are not
            processed into the user's graph.  Passed through to every
            ``thread.add_messages()`` call made by this tool.
        context_builder: An optional async callable that constructs the
            context block to inject into the LLM prompt.  Receives a single
            :class:`ContextInput`. Example::

                async def my_builder(ctx: ContextInput) -> str | None:
                    results = await ctx.zep.graph.search(
                        user_id=ctx.user_id,
                        query=ctx.user_message,
                        scope="edges",
                    )
                    return "\\n".join(e.fact for e in results.edges or [])

                tool = ZepContextTool(zep_client=zep, context_builder=my_builder)

            When provided, message persistence and context building run
            **in parallel** for lower latency.  When ``None`` (the default),
            the tool uses ``thread.add_messages(return_context=True)`` to
            persist and retrieve context in a single API call.
        context_template: Template used to wrap retrieved context before
            injecting it into the LLM's system instructions.  Must contain a
            literal ``{context}`` placeholder, which is replaced with the
            retrieved context text via plain string replacement (never
            ``str.format``).  Defaults to :data:`DEFAULT_CONTEXT_TEMPLATE`.

    Note:
        This tool does **not** create the Zep user or thread.  Provision them
        out-of-band, before the first turn, with
        :func:`zep_adk.provisioning.ensure_user` and
        :func:`zep_adk.provisioning.ensure_thread`.  If persistence fails
        because the user/thread does not exist, a warning is logged naming
        those helpers and the turn continues without Zep memory.

    Note:
        **Error isolation between persistence and the context builder.**
        When ``context_builder`` is set, persistence (``add_messages``) and
        the builder run concurrently. Each is isolated from the other's
        failure:

        * If the builder raises, a warning is logged and injection is
          skipped for this turn -- but persistence still completes and the
          turn is marked as persisted (dedup) on success.
        * If persistence raises, a warning is logged and the turn is
          **not** marked as persisted (so it can be retried on the next
          invocation) -- but a successful builder result may still be
          injected into the prompt.
    """

    def __init__(
        self,
        *,
        zep_client: AsyncZep,
        context_builder: ContextBuilder | None = None,
        context_template: str = DEFAULT_CONTEXT_TEMPLATE,
        ignore_roles: list[str] | None = None,
    ) -> None:
        super().__init__(name="zep_context", description="zep_context")
        self._zep: AsyncZep = zep_client
        self._context_builder: ContextBuilder | None = context_builder
        self._context_template: str = context_template
        self._ignore_roles: list[str] | None = ignore_roles

        # Same-turn guard: maps thread_id → id() of the last user_content
        # object we successfully persisted.  Within a single ADK turn,
        # process_llm_request fires N times with the *same* user_content
        # Python object (tool-use loops).  Comparing id() lets us skip the
        # re-invocations without blocking legitimately repeated user text
        # in a later turn (which will be a different object).
        self._last_persisted_content_id: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Identity resolution from session state
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_identity(tool_context: ToolContext) -> _ZepIdentity:
        """Extract Zep identity from ADK session state and session metadata.

        Resolution order for each field:

        * **user_id**: ``zep_user_id`` in state → ADK session ``user_id``
        * **thread_id**: ``zep_thread_id`` in state → ADK session ``id``
        * **first_name**: ``zep_first_name`` in state → ``"Anonymous"``
        * **last_name**: ``zep_last_name`` in state → ``"User"``

        Email is not resolved from session state: the turn path never creates
        or updates the Zep user, so email only takes effect when passed to
        :func:`~zep_adk.provisioning.ensure_user` during provisioning.

        Raises:
            ValueError: If neither the session state key nor the ADK session
                fallback can provide a user_id or thread_id.
        """
        state = tool_context.state
        if state is None:
            raise ValueError(
                "tool_context.state is None — cannot resolve Zep identity. "
                "Ensure a session with state was created before running the agent."
            )

        # -- user_id: state override → ADK session user_id -----------------
        user_id = state.get("zep_user_id")
        if not user_id:
            try:
                user_id = tool_context.user_id
            except AttributeError as err:
                raise ValueError(
                    "Cannot determine Zep user ID. Either set 'zep_user_id' in "
                    "session state or ensure the ADK session has a user_id."
                ) from err
        if not user_id:
            raise ValueError(
                "Cannot determine Zep user ID. Either set 'zep_user_id' in "
                "session state or pass user_id to create_session()."
            )

        # -- thread_id: state override → ADK session id --------------------
        thread_id = state.get("zep_thread_id")
        if not thread_id:
            try:
                thread_id = tool_context.session.id
            except AttributeError as err:
                raise ValueError(
                    "Cannot determine Zep thread ID. Either set 'zep_thread_id' "
                    "in session state or ensure the ADK session has an id."
                ) from err

        # -- name: state with sensible defaults -----------------------------
        first_name = state.get("zep_first_name") or "Anonymous"
        last_name = state.get("zep_last_name") or "User"

        display = f"{first_name} {last_name}".strip()

        return _ZepIdentity(
            user_id=user_id,
            thread_id=thread_id,
            first_name=first_name,
            last_name=last_name,
            user_display_name=display,
        )

    @staticmethod
    def _is_not_found_error(exc: Exception) -> bool:
        """Detect a Zep "not found" error (e.g. unprovisioned user/thread)."""
        status_code = getattr(exc, "status_code", None)
        if status_code == 404:
            return True
        text = str(exc).lower()
        return any(marker in text for marker in _NOT_FOUND_MARKERS)

    # ------------------------------------------------------------------
    # Core hook -- runs on every LLM request
    # ------------------------------------------------------------------

    @override
    async def process_llm_request(
        self,
        *,
        tool_context: ToolContext,
        llm_request: LlmRequest,
    ) -> None:
        """Persist the user message to Zep and inject the returned context.

        This method is called by the ADK framework before every LLM request.
        It resolves the user's Zep identity from session state, truncates the
        message content to Zep's per-message limit if needed, persists it to
        Zep (via ``thread.add_messages(return_context=True)`` by default, or
        concurrently with a custom ``context_builder``), and appends the
        resulting context block to the LLM system instructions using
        ``context_template``.
        """

        # --- 1. Extract user message text ---------------------------------
        user_content = tool_context.user_content
        if not user_content or not user_content.parts:
            return

        # Join all text parts — user_content may contain non-text parts
        # (images, files) interleaved with text.
        text_parts = [p.text for p in user_content.parts if hasattr(p, "text") and p.text]
        if not text_parts:
            return

        user_text: str = " ".join(text_parts)

        # --- 2. Resolve identity from session state -----------------------
        try:
            identity = self._resolve_identity(tool_context)
        except (ValueError, AttributeError) as exc:
            logger.warning(
                "Cannot resolve Zep identity from session state — skipping "
                "Zep persistence for this turn: %s",
                exc,
            )
            return

        # --- 3. Same-turn guard -------------------------------------------
        # Within one ADK turn, process_llm_request fires multiple times
        # (tool-use loops) with the *same* user_content Python object.
        # Comparing id() skips re-invocations without blocking legitimately
        # repeated user text in later turns (which will be a new object).
        content_id = id(user_content)
        if self._last_persisted_content_id.get(identity.thread_id) == content_id:
            logger.debug("Skipping same-turn re-invocation for thread %s", identity.thread_id)
            return

        # --- 4. Persist message and retrieve context -----------------------
        # The Zep user and thread must already exist -- this tool never
        # creates them (see zep_adk.provisioning.ensure_user/ensure_thread).
        truncated_text = truncate_message_content(user_text, label="user")
        zep_msg = Message(
            role="user",
            content=truncated_text,
            name=identity.user_display_name,
        )

        context_text: str | None
        persist_ok: bool
        if self._context_builder is not None:
            # Custom builder: persist + build context concurrently. Each side
            # is isolated from the other's failure -- one raising must not
            # cancel or mask the other's result.
            persist_ok, context_text = await self._persist_and_build_context(
                identity, zep_msg, truncated_text, llm_request, tool_context
            )
        else:
            # Default: single round-trip
            try:
                response = await self._zep.thread.add_messages(
                    thread_id=identity.thread_id,
                    messages=[zep_msg],
                    return_context=True,
                    ignore_roles=self._ignore_roles,
                )
                context_text = response.context if response else None
                persist_ok = True
                logger.info(
                    "Persisted message to Zep (thread=%s). Context length: %s",
                    identity.thread_id,
                    len(context_text) if context_text else 0,
                )
            except Exception as exc:
                self._log_persist_failure(exc, identity)
                context_text = None
                persist_ok = False

        # Mark as persisted only AFTER the API call succeeded, so that a
        # transient failure does not permanently suppress the message.
        if persist_ok:
            self._last_persisted_content_id[identity.thread_id] = content_id

        # --- 5. Inject context into the LLM prompt -----------------------
        if context_text:
            instruction = self._context_template.replace("{context}", context_text)
            llm_request.append_instructions([instruction])
            logger.debug(
                "Injected Zep context (%d chars) into LLM prompt",
                len(context_text),
            )

    def _log_persist_failure(self, exc: Exception, identity: _ZepIdentity) -> None:
        """Log a warning for a failed ``add_messages`` call."""
        if self._is_not_found_error(exc):
            logger.warning(
                "Zep user/thread not found for user_id=%s thread_id=%s — call "
                "zep_adk.ensure_user() and zep_adk.ensure_thread() before the "
                "first turn",
                identity.user_id,
                identity.thread_id,
            )
        else:
            logger.warning(
                "Failed to add message / retrieve context from Zep",
                exc_info=True,
            )

    async def _persist_and_build_context(
        self,
        identity: _ZepIdentity,
        zep_msg: Message,
        user_text: str,
        llm_request: LlmRequest,
        tool_context: ToolContext,
    ) -> tuple[bool, str | None]:
        """Persist the message and build context concurrently.

        Runs ``thread.add_messages`` (without ``return_context``) and the
        custom ``context_builder`` concurrently via ``asyncio.gather`` to
        minimise latency.

        Error isolation: each coroutine catches its own exceptions so that
        ``asyncio.gather`` cannot let one side's failure cancel or mask the
        other's result.

        * If the builder raises, a warning is logged and ``None`` is
          returned for the context, but persistence is unaffected.
        * If persistence raises, a warning is logged and ``False`` is
          returned for ``persist_ok`` (so the caller does not mark the turn
          as persisted / dedup'd), but a successful builder result is still
          returned for injection.

        Returns:
            A ``(persist_ok, context_text)`` tuple.
        """
        assert self._context_builder is not None  # noqa: S101
        context_builder = self._context_builder

        async def _persist() -> bool:
            try:
                await self._zep.thread.add_messages(
                    thread_id=identity.thread_id,
                    messages=[zep_msg],
                    ignore_roles=self._ignore_roles,
                )
                logger.info("Persisted message to Zep (thread=%s).", identity.thread_id)
                return True
            except Exception as exc:
                self._log_persist_failure(exc, identity)
                return False

        async def _build_context() -> str | None:
            try:
                context_input = ContextInput(
                    zep=self._zep,
                    user_id=identity.user_id,
                    thread_id=identity.thread_id,
                    user_message=user_text,
                    tool_context=tool_context,
                    llm_request=llm_request,
                )
                return await context_builder(context_input)
            except Exception:
                logger.warning(
                    "Custom context_builder raised — skipping context injection for this turn",
                    exc_info=True,
                )
                return None

        persist_ok, context_text = await asyncio.gather(_persist(), _build_context())
        return persist_ok, context_text
