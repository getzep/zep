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

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from agent_framework import ContextProvider
from zep_cloud import Message as ZepMessage
from zep_cloud.client import AsyncZep

if TYPE_CHECKING:
    from agent_framework import (
        AgentSession,
        Message,
        SessionContext,
        SupportsAgentRun,
    )

logger = logging.getLogger(__name__)

#: Default ``source_id`` used to attribute this provider's contributions in the
#: Agent Framework context pipeline.
DEFAULT_SOURCE_ID = "zep"

#: Type alias for a user-setup hook that runs once after a Zep user is created.
#:
#: Receives the Zep client and the newly created user ID.  Use this to configure
#: per-user ontology, custom instructions, or user summary instructions.
UserSetupHook = Callable[[AsyncZep, str], Awaitable[None]]


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
            ontology, custom instructions, or user summary instructions.  A
            failure in the hook is logged and does not block the run.  It does
            **not** fire for users that already exist.
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

        # Whether the Zep user + thread have been created (or confirmed to
        # already exist) for this provider instance.  Cached so repeated runs
        # do not re-issue setup calls.
        self._resources_ready: bool = False

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

        Idempotent and cached: succeeds for users/threads that already exist
        and only runs the ``on_user_created`` hook for genuinely new users.

        Returns:
            ``True`` if the user and thread are ready (created or pre-existing),
            ``False`` on a genuine failure (so the caller can skip this turn and
            retry on the next).
        """
        if self._resources_ready:
            return True

        # -- Create the user (tolerate "already exists") -------------------
        try:
            await self._zep.user.add(
                user_id=self._user_id,
                first_name=self._first_name,
                last_name=self._last_name,
                email=self._email,
            )
            logger.info("Created Zep user: %s", self._user_id)
            await self._run_user_created_hook()
        except Exception as exc:
            if self._is_already_exists(exc):
                logger.debug("Zep user %s already exists", self._user_id)
            else:
                logger.warning("Failed to create Zep user %s: %s", self._user_id, exc)
                return False

        # -- Create the thread (tolerate "already exists") -----------------
        try:
            await self._zep.thread.create(thread_id=self._thread_id, user_id=self._user_id)
            logger.info("Created Zep thread: %s", self._thread_id)
        except Exception as exc:
            if self._is_already_exists(exc):
                logger.debug("Zep thread %s already exists", self._thread_id)
            else:
                logger.warning("Failed to create Zep thread %s: %s", self._thread_id, exc)
                return False

        self._resources_ready = True
        return True

    async def _run_user_created_hook(self) -> None:
        """Run the optional ``on_user_created`` hook, never raising on failure."""
        if self._on_user_created is None:
            return
        try:
            await self._on_user_created(self._zep, self._user_id)
            logger.info("on_user_created hook completed for user %s", self._user_id)
        except Exception as exc:
            logger.warning(
                "on_user_created hook failed for user %s: %s",
                self._user_id,
                exc,
            )

    @staticmethod
    def _is_already_exists(exc: Exception) -> bool:
        """Heuristically detect a Zep "resource already exists" conflict."""
        text = str(exc).lower()
        return "already exists" in text or "conflict" in text or "409" in text

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
        latest user message from ``context.input_messages``, persists it to Zep,
        and adds the returned Context Block to ``context`` via
        ``extend_instructions`` so it becomes part of the model's system prompt.

        A Zep failure is logged and the run continues without injected memory.
        """
        user_text = self._latest_user_text(context.input_messages)
        if not user_text:
            return

        if not await self._ensure_resources():
            return

        context_block: str | None = None
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
        except Exception:
            logger.warning(
                "Failed to persist user message / retrieve context from Zep",
                exc_info=True,
            )
            return

        if context_block:
            context.extend_instructions(self.source_id, self._format_context(context_block))
            logger.debug(
                "Injected Zep context (%d chars) into agent instructions",
                len(context_block),
            )

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

        # The thread/user were created in before_run; if that failed we skip
        # rather than re-attempt here, since there is no user turn to anchor to.
        if not self._resources_ready:
            return

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

    @staticmethod
    def _format_context(context_block: str) -> str:
        """Wrap Zep's Context Block in a clearly delimited instruction."""
        return (
            "The following context is retrieved from Zep's long-term memory "
            "service. It contains relevant facts, relationships, and prior "
            "knowledge about the user. Use it to inform your responses.\n\n"
            "<ZEP_CONTEXT>\n"
            f"{context_block}\n"
            "</ZEP_CONTEXT>"
        )
