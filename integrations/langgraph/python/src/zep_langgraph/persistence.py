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
        return message

    role = _LC_ROLE_MAP.get(message.type, "user")
    content = _coerce_content(message.content).strip()
    if not content:
        # Tool-call-only AI messages or empty placeholders carry no useful text.
        return None
    if len(content) > MAX_MESSAGE_CHARS:
        logger.debug(
            "Truncating message content from %d to %d chars before sending to Zep",
            len(content),
            MAX_MESSAGE_CHARS,
        )
        content = content[:MAX_MESSAGE_CHARS]

    name = message.name
    if not name:
        if role == "user":
            name = user_name
        elif role == "assistant":
            name = assistant_name

    return Message(role=role, content=content, name=name)


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

    try:
        response = await zep_client.thread.add_messages(
            thread_id,
            messages=zep_messages,
            ignore_roles=ignore_roles,
            return_context=return_context,
        )
    except Exception:
        logger.warning(
            "Failed to persist %d message(s) to Zep thread %s",
            len(zep_messages),
            thread_id,
            exc_info=True,
        )
        return None

    if return_context and response is not None:
        context: str | None = getattr(response, "context", None)
        if context and context.strip():
            return context.strip()
    return None


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

    try:
        response = zep_client.thread.add_messages(
            thread_id,
            messages=zep_messages,
            ignore_roles=ignore_roles,
            return_context=return_context,
        )
    except Exception:
        logger.warning(
            "Failed to persist %d message(s) to Zep thread %s",
            len(zep_messages),
            thread_id,
            exc_info=True,
        )
        return None

    if return_context and response is not None:
        context: str | None = getattr(response, "context", None)
        if context and context.strip():
            return context.strip()
    return None
