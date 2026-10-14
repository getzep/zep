"""
Callback that persists the assistant's response to Zep after each model call.

Used alongside ``ZepContextTool`` (which handles user messages + context
retrieval).  Together they ensure both sides of the conversation are persisted
to Zep in real-time.

The callback resolves the Zep thread UUID from ADK session state at runtime,
allowing a single callback instance to be shared across all users/sessions.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING
from uuid import UUID

from zep_cloud import AddMessage
from zep_cloud.client import AsyncZep

from .limits import truncate_message_content

if TYPE_CHECKING:
    from google.adk.agents.callback_context import CallbackContext
    from google.adk.models import LlmResponse

logger = logging.getLogger(__name__)


def _is_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        UUID(value)
    except ValueError:
        return False
    return True


def create_after_model_callback(
    zep_client: AsyncZep,
    assistant_name: str = "Assistant",
    ignore_roles: list[str] | None = None,
) -> Callable[..., Coroutine[None, None, LlmResponse | None]]:
    """Return an ``after_model_callback`` that persists assistant responses to Zep.

    The returned callback is designed to be passed directly to
    ``google.adk.agents.Agent(after_model_callback=...)``.  It extracts the
    text from each model response, deduplicates it, and persists it to the
    Zep thread identified by session state.

    The thread UUID is resolved at runtime from ``zep_thread_uuid`` in session
    state, falling back to the ADK session ID.  This allows a single callback
    to be shared across all users/sessions.

    Args:
        zep_client: An initialised ``AsyncZep`` client.
        assistant_name: Display name for the assistant in Zep messages.
            Defaults to ``"Assistant"``.
        ignore_roles: An optional list of message roles (e.g. ``["assistant"]``)
            to exclude from Zep's knowledge graph ingestion.  Messages with
            these roles are still stored in the thread history but are not
            processed into the user's graph.

    Returns:
        An async callback function compatible with ADK's ``after_model_callback``
        interface.
    """

    async def after_model_callback(
        callback_context: CallbackContext,
        llm_response: LlmResponse,
    ) -> LlmResponse | None:
        """Persist the assistant's response text to Zep."""
        if not llm_response or not llm_response.content:
            return None

        parts = llm_response.content.parts or []

        # Skip intermediate responses that contain tool calls — these are
        # the model's "thinking" messages before a tool is executed (e.g.
        # "Let me look that up for you.").  Only persist the final text-only
        # response so Zep sees one clean assistant message per turn.
        has_function_call = any(hasattr(p, "function_call") and p.function_call for p in parts)
        if has_function_call:
            return None

        # Extract text from the response parts
        text_parts = [p.text for p in parts if hasattr(p, "text") and p.text]

        if not text_parts:
            return None

        full_text = truncate_message_content(" ".join(text_parts), label="assistant")

        # Resolve thread_uuid from session state
        state = callback_context.state
        thread_uuid = state.get("zep_thread_uuid") if state is not None else None
        if not thread_uuid:
            try:
                thread_uuid = callback_context.session.id
            except AttributeError:
                logger.warning("Cannot resolve Zep thread UUID — skipping persist")
                return None
            if not _is_uuid(thread_uuid):
                logger.warning(
                    "The ADK session does not contain a Zep thread UUID — skipping persist"
                )
                return None

        try:
            await zep_client.thread.add_messages(
                thread_uuid=thread_uuid,
                messages=[
                    AddMessage(
                        role="assistant",
                        content=full_text,
                        name=assistant_name,
                    )
                ],
                ignore_roles=ignore_roles,
            )
            logger.info(
                "Persisted assistant response to Zep thread %s (%d chars)",
                thread_uuid,
                len(full_text),
            )
        except Exception:
            logger.warning(
                "Failed to persist assistant response to Zep",
                exc_info=True,
            )

        # Return None to let the response pass through unmodified.
        return None

    return after_model_callback
