"""
Message-persistence helpers for LangGraph nodes.

These helpers wrap Zep's :meth:`thread.add_messages` so a graph node can persist
a turn of conversation to the user graph. They accept either native Zep
:class:`~zep_cloud.types.message.Message` objects or LangChain
:class:`~langchain_core.messages.BaseMessage` objects (which are converted), and
they handle Zep's role enum, message-length limits, and graceful failure.

Typical use inside an agent node::

    from zep_langgraph import persist_messages

    async def agent_node(state):
        response = await llm.ainvoke(messages)
        await persist_messages(
            zep_client,
            thread_id=state["thread_id"],
            messages=[state["messages"][-1], response],
            user_name="Alice Smith",
        )
        return {"messages": [response]}

Zep ingestion is **asynchronous**: a just-persisted fact is not immediately
retrievable. Persist a turn after it happens; do not expect read-after-write
within the same node.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from langchain_core.messages import BaseMessage
from zep_cloud import Message
from zep_cloud.client import AsyncZep, Zep

logger = logging.getLogger(__name__)

#: Maximum content length (characters) Zep accepts for a single message.
#: Longer content is truncated before sending. Use ``graph.add`` for large
#: documents instead of the thread API.
MAX_MESSAGE_CHARS = 4096

#: Maximum number of messages Zep accepts in a single ``thread.add_messages``
#: call. Larger turns are split across multiple calls.
MAX_MESSAGES_PER_CALL = 30


def _truncate_message_content(content: str) -> str:
    """Truncate message content to :data:`MAX_MESSAGE_CHARS`, logging a warning.

    Zep rejects messages whose content exceeds :data:`MAX_MESSAGE_CHARS` with a
    400. To avoid silently dropping the whole message we truncate instead. The
    log line carries only lengths -- never content -- so no PII is emitted.
    """
    if len(content) <= MAX_MESSAGE_CHARS:
        return content
    logger.warning(
        "Truncating message content from %d to %d chars before sending to Zep",
        len(content),
        MAX_MESSAGE_CHARS,
    )
    return content[:MAX_MESSAGE_CHARS]


#: Mapping from LangChain message ``type`` values to Zep role names.
_LC_ROLE_MAP: dict[str, str] = {
    "human": "user",
    "ai": "assistant",
    "system": "system",
    "tool": "tool",
    "function": "tool",
}

#: Role names recognised by the Zep thread API.
_ZEP_ROLES = frozenset({"norole", "system", "assistant", "user", "function", "tool"})


def _coerce_content(content: Any) -> str:
    """Flatten LangChain message content (string or content blocks) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return " ".join(p for p in parts if p)
    return str(content) if content is not None else ""


def to_zep_message(
    message: BaseMessage | Message,
    *,
    user_name: str | None = None,
    assistant_name: str | None = None,
) -> Message | None:
    """Convert a LangChain or Zep message into a Zep :class:`Message`.

    Args:
        message: A LangChain :class:`~langchain_core.messages.BaseMessage` or an
            already-constructed Zep :class:`~zep_cloud.types.message.Message`.
        user_name: Optional display name applied to user-role messages that lack
            one. Passing the user's real name helps Zep resolve identity.
        assistant_name: Optional display name applied to assistant-role messages
            that lack one.

    Returns:
        A Zep :class:`Message`, or ``None`` if the message has no text content
        (e.g. an assistant message that only carries tool calls).
    """
    if isinstance(message, Message):
        # Native Zep messages take the same content path the LangChain branch
        # uses (the README/example pass these), so over-long content is truncated
        # rather than rejected with a 400 by Zep.
        content = message.content or ""
        truncated = _truncate_message_content(content)
        if truncated is content:
            return message
        return message.model_copy(update={"content": truncated})

    role = _LC_ROLE_MAP.get(message.type, "user")
    content = _coerce_content(message.content).strip()
    if not content:
        # Tool-call-only AI messages or empty placeholders carry no useful text.
        return None
    content = _truncate_message_content(content)

    name = message.name
    if not name:
        if role == "user":
            name = user_name
        elif role == "assistant":
            name = assistant_name

    return Message(role=role, content=content, name=name)


def _chunk_messages(messages: Sequence[Message]) -> list[list[Message]]:
    """Split messages into chunks of at most :data:`MAX_MESSAGES_PER_CALL`.

    Zep rejects a ``thread.add_messages`` call carrying more than
    :data:`MAX_MESSAGES_PER_CALL` messages with a 400, so larger turns are split
    across multiple calls. The log line carries only counts -- never content.
    """
    if len(messages) <= MAX_MESSAGES_PER_CALL:
        return [list(messages)]
    chunks = [
        list(messages[i : i + MAX_MESSAGES_PER_CALL])
        for i in range(0, len(messages), MAX_MESSAGES_PER_CALL)
    ]
    logger.warning(
        "Splitting %d messages into %d add_messages calls (cap %d per call)",
        len(messages),
        len(chunks),
        MAX_MESSAGES_PER_CALL,
    )
    return chunks


def to_zep_messages(
    messages: Sequence[BaseMessage | Message],
    *,
    user_name: str | None = None,
    assistant_name: str | None = None,
) -> list[Message]:
    """Convert a sequence of LangChain/Zep messages to Zep :class:`Message` objects.

    Messages with no text content are dropped.

    Args:
        messages: LangChain or Zep messages to convert.
        user_name: Optional display name for user-role messages.
        assistant_name: Optional display name for assistant-role messages.

    Returns:
        A list of Zep :class:`Message` objects (possibly shorter than the input).
    """
    result: list[Message] = []
    for msg in messages:
        zep_msg = to_zep_message(
            msg,
            user_name=user_name,
            assistant_name=assistant_name,
        )
        if zep_msg is not None:
            result.append(zep_msg)
    return result


async def persist_messages(
    zep_client: AsyncZep,
    thread_id: str,
    messages: Sequence[BaseMessage | Message],
    *,
    user_name: str | None = None,
    assistant_name: str | None = None,
    ignore_roles: list[str] | None = None,
    return_context: bool = False,
) -> str | None:
    """Persist a turn of conversation to a Zep thread.

    Wraps :meth:`AsyncZep.thread.add_messages`. LangChain messages are converted
    to Zep messages automatically; messages with no text are skipped.

    Args:
        zep_client: An initialised :class:`~zep_cloud.client.AsyncZep` client.
        thread_id: The Zep thread to add the messages to.
        messages: The messages for this turn (LangChain or Zep messages).
        user_name: Optional display name for user-role messages without a name.
        assistant_name: Optional display name for assistant-role messages.
        ignore_roles: Optional roles to record in thread history but exclude from
            graph ingestion (e.g. ``["assistant"]``).
        return_context: When ``True``, request the refreshed Context Block in the
            same round-trip and return it (folds persist + retrieve into one call).

    Returns:
        The Context Block string if ``return_context`` is ``True`` and Zep
        returned one; otherwise ``None``. A Zep failure is logged and ``None`` is
        returned -- the call never raises.
    """
    zep_messages = to_zep_messages(
        messages,
        user_name=user_name,
        assistant_name=assistant_name,
    )
    if not zep_messages:
        logger.debug("No persistable messages for thread %s -- skipping", thread_id)
        return None

    chunks = _chunk_messages(zep_messages)
    last_context: str | None = None
    for index, chunk in enumerate(chunks):
        # Only request context on the final chunk -- it reflects the most recent
        # messages, which is what the Context Block is keyed off.
        want_context = return_context and index == len(chunks) - 1
        try:
            response = await zep_client.thread.add_messages(
                thread_id,
                messages=chunk,
                ignore_roles=ignore_roles,
                return_context=want_context,
            )
        except Exception:
            logger.warning(
                "Failed to persist %d message(s) to Zep thread %s "
                "(chunk %d/%d); attempting remaining chunks",
                len(chunk),
                thread_id,
                index + 1,
                len(chunks),
                exc_info=True,
            )
            # Best-effort: a chunk failure must not abandon the remaining
            # chunks. Skip this chunk and attempt the rest so the caller
            # persists as many messages as possible.
            continue
        if want_context and response is not None:
            context: str | None = getattr(response, "context", None)
            if context and context.strip():
                last_context = context.strip()
    return last_context


def persist_messages_sync(
    zep_client: Zep,
    thread_id: str,
    messages: Sequence[BaseMessage | Message],
    *,
    user_name: str | None = None,
    assistant_name: str | None = None,
    ignore_roles: list[str] | None = None,
    return_context: bool = False,
) -> str | None:
    """Synchronous variant of :func:`persist_messages`.

    Args:
        zep_client: An initialised synchronous :class:`~zep_cloud.client.Zep` client.
        thread_id: The Zep thread to add the messages to.
        messages: The messages for this turn (LangChain or Zep messages).
        user_name: Optional display name for user-role messages without a name.
        assistant_name: Optional display name for assistant-role messages.
        ignore_roles: Optional roles to exclude from graph ingestion.
        return_context: When ``True``, return the refreshed Context Block.

    Returns:
        The Context Block string if requested and available, else ``None``. Never
        raises on a Zep failure.
    """
    zep_messages = to_zep_messages(
        messages,
        user_name=user_name,
        assistant_name=assistant_name,
    )
    if not zep_messages:
        logger.debug("No persistable messages for thread %s -- skipping", thread_id)
        return None

    chunks = _chunk_messages(zep_messages)
    last_context: str | None = None
    for index, chunk in enumerate(chunks):
        # Only request context on the final chunk -- it reflects the most recent
        # messages, which is what the Context Block is keyed off.
        want_context = return_context and index == len(chunks) - 1
        try:
            response = zep_client.thread.add_messages(
                thread_id,
                messages=chunk,
                ignore_roles=ignore_roles,
                return_context=want_context,
            )
        except Exception:
            logger.warning(
                "Failed to persist %d message(s) to Zep thread %s "
                "(chunk %d/%d); attempting remaining chunks",
                len(chunk),
                thread_id,
                index + 1,
                len(chunks),
                exc_info=True,
            )
            # Best-effort: a chunk failure must not abandon the remaining
            # chunks. Skip this chunk and attempt the rest so the caller
            # persists as many messages as possible.
            continue
        if want_context and response is not None:
            context: str | None = getattr(response, "context", None)
            if context and context.strip():
                last_context = context.strip()
    return last_context
