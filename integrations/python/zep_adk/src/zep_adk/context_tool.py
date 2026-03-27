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

if TYPE_CHECKING:
    from google.adk.models import LlmRequest

#: Type alias for a custom context builder function.
#:
#: A context builder receives the Zep client, the resolved user ID, thread ID,
#: and the user's message text.  It returns the context string to inject into
#: the LLM prompt (or ``None`` to skip injection).
ContextBuilder = Callable[[AsyncZep, str, str, str], Awaitable[str | None]]

#: Type alias for a user-setup hook that runs once after a Zep user is created.
#:
#: Receives the Zep client and the newly created user ID.  Use this to configure
#: per-user ontology, custom instructions, or user summary instructions.
UserSetupHook = Callable[[AsyncZep, str], Awaitable[None]]

logger = logging.getLogger(__name__)


@dataclass
class _ZepIdentity:
    """Resolved Zep identity from ADK session state."""

    user_id: str
    thread_id: str
    first_name: str | None
    last_name: str | None
    email: str | None
    user_display_name: str | None


class ZepContextTool(BaseTool):
    """Automatically persist user messages to Zep and inject memory context.

    This tool hooks into the ADK request lifecycle.  It is **not** invoked by
    the model -- instead, ``process_llm_request`` runs before every LLM call,
    giving it the opportunity to persist the latest user message to Zep and
    prepend relevant context from Zep's long-term memory.

    Identity (user ID, thread ID, name, email) is resolved at runtime from
    ADK session state, so a single tool instance can be shared across all
    users and sessions.

    Args:
        zep_client: An initialised ``AsyncZep`` client.
        ignore_roles: An optional list of message roles (e.g. ``["assistant"]``)
            to exclude from Zep's knowledge graph ingestion.  Messages with
            these roles are still stored in the thread history but are not
            processed into the user's graph.  Passed through to every
            ``thread.add_messages()`` call made by this tool.
        context_builder: An optional async callable that constructs the
            context block to inject into the LLM prompt.  Signature::

                async def my_builder(
                    zep_client: AsyncZep,
                    user_id: str,
                    thread_id: str,
                    user_message: str,
                ) -> str | None:
                    ...

            When provided, message persistence and context building run
            **in parallel** for lower latency.  When ``None`` (the default),
            the tool uses ``thread.add_messages(return_context=True)`` to
            persist and retrieve context in a single API call.
        on_user_created: An optional async callable that runs once after a new
            Zep user is created.  Use this to set up per-user configuration
            such as custom ontology, custom instructions, or user summary
            instructions.  Signature::

                async def setup(zep_client: AsyncZep, user_id: str) -> None:
                    ...

            The hook fires only when ``user.add()`` succeeds (i.e. the user
            is genuinely new).  It does **not** fire for users that already
            exist.  If the hook raises an exception, a warning is logged but
            the agent turn continues normally.
    """

    def __init__(
        self,
        *,
        zep_client: AsyncZep,
        context_builder: ContextBuilder | None = None,
        ignore_roles: list[str] | None = None,
        on_user_created: UserSetupHook | None = None,
    ) -> None:
        super().__init__(name="zep_context", description="zep_context")
        self._zep: AsyncZep = zep_client
        self._context_builder: ContextBuilder | None = context_builder
        self._ignore_roles: list[str] | None = ignore_roles
        self._on_user_created: UserSetupHook | None = on_user_created

        # Same-turn guard: maps thread_id → id() of the last user_content
        # object we successfully persisted.  Within a single ADK turn,
        # process_llm_request fires N times with the *same* user_content
        # Python object (tool-use loops).  Comparing id() lets us skip the
        # re-invocations without blocking legitimately repeated user text
        # in a later turn (which will be a different object).
        self._last_persisted_content_id: dict[str, int] = {}

        # Track which (user_id, thread_id) pairs have been lazily created
        self._created_resources: set[tuple[str, str]] = set()

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
        * **email**: ``zep_email`` in state → ``None``

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
                user_id = tool_context._invocation_context.session.user_id
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
                thread_id = tool_context._invocation_context.session.id
            except AttributeError as err:
                raise ValueError(
                    "Cannot determine Zep thread ID. Either set 'zep_thread_id' "
                    "in session state or ensure the ADK session has an id."
                ) from err

        # -- name / email: state with sensible defaults --------------------
        first_name = state.get("zep_first_name") or "Anonymous"
        last_name = state.get("zep_last_name") or "User"
        email = state.get("zep_email")

        display = f"{first_name} {last_name}".strip()

        return _ZepIdentity(
            user_id=user_id,
            thread_id=thread_id,
            first_name=first_name,
            last_name=last_name,
            email=email,
            user_display_name=display,
        )

    # ------------------------------------------------------------------
    # Lazy resource creation
    # ------------------------------------------------------------------

    async def _ensure_resources(self, identity: _ZepIdentity) -> bool:
        """Create the Zep user and thread if they don't already exist.

        Returns:
            True if resources are ready (created or already existed), False on
            genuine failure.
        """
        key = (identity.user_id, identity.thread_id)
        if key in self._created_resources:
            return True

        # Create user (ignore if already exists)
        user_ok = False
        try:
            await self._zep.user.add(
                user_id=identity.user_id,
                first_name=identity.first_name,
                last_name=identity.last_name,
                email=identity.email,
            )
            logger.info("Created Zep user: %s", identity.user_id)
            user_ok = True

            # Run the user-setup hook for newly created users
            if self._on_user_created is not None:
                try:
                    await self._on_user_created(self._zep, identity.user_id)
                    logger.info(
                        "on_user_created hook completed for user %s",
                        identity.user_id,
                    )
                except Exception as hook_exc:
                    logger.warning(
                        "on_user_created hook failed for user %s: %s",
                        identity.user_id,
                        hook_exc,
                    )

        except Exception as exc:
            # "already exists" conflicts are fine; genuine failures are not
            exc_str = str(exc).lower()
            if "already exists" in exc_str or "conflict" in exc_str or "409" in exc_str:
                logger.debug("Zep user %s already exists", identity.user_id)
                user_ok = True
            else:
                logger.warning("Failed to create Zep user %s: %s", identity.user_id, exc)
                return False

        # Create thread (ignore if already exists)
        thread_ok = False
        try:
            await self._zep.thread.create(thread_id=identity.thread_id, user_id=identity.user_id)
            logger.info("Created Zep thread: %s", identity.thread_id)
            thread_ok = True
        except Exception as exc:
            exc_str = str(exc).lower()
            if "already exists" in exc_str or "conflict" in exc_str or "409" in exc_str:
                logger.debug("Zep thread %s already exists", identity.thread_id)
                thread_ok = True
            else:
                logger.warning("Failed to create Zep thread %s: %s", identity.thread_id, exc)
                return False

        # Only cache as created if both succeeded or already existed
        if user_ok and thread_ok:
            self._created_resources.add(key)
            return True
        return False

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
        It resolves the user's Zep identity from session state, persists the
        latest message to Zep via ``thread.add_messages(return_context=True)``,
        and appends the returned context block to the LLM system instructions.
        """

        # --- 1. Extract user message text ---------------------------------
        user_content = tool_context.user_content
        if not user_content or not user_content.parts or not user_content.parts[0].text:
            return

        user_text: str = user_content.parts[0].text

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

        # --- 4. Ensure Zep resources exist --------------------------------
        if not await self._ensure_resources(identity):
            return

        # --- 5. Persist message and retrieve context ----------------------
        zep_msg = Message(
            role="user",
            content=user_text,
            name=identity.user_display_name,
        )

        context_text: str | None = None
        try:
            if self._context_builder is not None:
                # Custom builder: persist + build context in parallel
                context_text = await self._persist_and_build_context(identity, zep_msg, user_text)
            else:
                # Default: single round-trip
                response = await self._zep.thread.add_messages(
                    thread_id=identity.thread_id,
                    messages=[zep_msg],
                    return_context=True,
                    ignore_roles=self._ignore_roles,
                )
                context_text = response.context if response else None
            logger.info(
                "Persisted message to Zep (thread=%s). Context length: %s",
                identity.thread_id,
                len(context_text) if context_text else 0,
            )
        except Exception:
            logger.warning(
                "Failed to add message / retrieve context from Zep",
                exc_info=True,
            )
            return

        # Mark as persisted only AFTER the API call succeeded, so that a
        # transient failure does not permanently suppress the message.
        self._last_persisted_content_id[identity.thread_id] = content_id

        # --- 6. Inject context into the LLM prompt -----------------------
        if context_text:
            instruction = (
                "The following context is retrieved from Zep's long-term memory "
                "service. It contains relevant facts, relationships, and prior "
                "knowledge about the user. Use it to inform your responses.\n\n"
                "<ZEP_CONTEXT>\n"
                f"{context_text}\n"
                "</ZEP_CONTEXT>"
            )
            llm_request.append_instructions([instruction])
            logger.debug(
                "Injected Zep context (%d chars) into LLM prompt",
                len(context_text),
            )

    async def _persist_and_build_context(
        self,
        identity: _ZepIdentity,
        zep_msg: Message,
        user_text: str,
    ) -> str | None:
        """Persist the message and build context in parallel.

        Runs ``thread.add_messages`` (without ``return_context``) and the
        custom ``context_builder`` concurrently via ``asyncio.gather`` to
        minimise latency.
        """
        assert self._context_builder is not None  # noqa: S101
        context_builder = self._context_builder

        async def _persist() -> None:
            await self._zep.thread.add_messages(
                thread_id=identity.thread_id,
                messages=[zep_msg],
                ignore_roles=self._ignore_roles,
            )

        async def _build_context() -> str | None:
            return await context_builder(
                self._zep,
                identity.user_id,
                identity.thread_id,
                user_text,
            )

        _, context_text = await asyncio.gather(_persist(), _build_context())
        return context_text
