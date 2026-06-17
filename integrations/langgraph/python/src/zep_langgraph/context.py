"""
Context-injection helpers for LangGraph nodes.

These helpers wrap Zep's :meth:`thread.get_user_context` so a graph node can
fetch the user's :class:`Context Block <zep_cloud.types.thread_context_response.ThreadContextResponse>`
-- a prompt-ready summary assembled from the *entire* user graph (the thread
only scopes what is currently relevant) -- and inject it into the system
prompt.

The recommended pattern, matching Zep's own LangGraph guide, is to call
:func:`get_zep_context` (or :func:`build_system_message`) at the top of an
agent node and prepend the result to the message list passed to the model::

    from zep_langgraph import build_system_message

    async def agent_node(state):
        system = await build_system_message(
            zep_client,
            thread_id=state["thread_id"],
            base_instructions="You are a helpful assistant.",
        )
        messages = [system, *state["messages"]]
        response = await llm.ainvoke(messages)
        ...

Every helper degrades gracefully: a Zep failure is logged and an empty / base
result is returned so the host agent keeps running.
"""

from __future__ import annotations

import logging

from langchain_core.messages import SystemMessage
from zep_cloud.client import AsyncZep, Zep

logger = logging.getLogger(__name__)

#: Default wrapper applied around a retrieved Context Block when building a
#: system message. The ``{context}`` placeholder is filled with the block.
DEFAULT_CONTEXT_TEMPLATE = (
    "The following information is retrieved from the user's long-term memory. "
    "Use it to inform your response when relevant.\n\n"
    "<MEMORY>\n{context}\n</MEMORY>"
)


async def get_zep_context(
    zep_client: AsyncZep,
    thread_id: str,
    *,
    template_id: str | None = None,
) -> str | None:
    """Fetch the Zep Context Block for a thread.

    Wraps :meth:`AsyncZep.thread.get_user_context`. The returned block is
    assembled from the whole user graph; the thread is used only to determine
    what is relevant right now.

    Args:
        zep_client: An initialised :class:`~zep_cloud.client.AsyncZep` client.
        thread_id: The Zep thread ID to retrieve context for.
        template_id: Optional ID of a context template to format the block with.

    Returns:
        The Context Block string, or ``None`` if Zep returned no context or the
        call failed. Failures are logged, never raised.
    """
    try:
        response = await zep_client.thread.get_user_context(
            thread_id,
            template_id=template_id,
        )
    except Exception:
        logger.warning(
            "Failed to retrieve Zep context for thread %s",
            thread_id,
            exc_info=True,
        )
        return None

    context: str | None = getattr(response, "context", None)
    if context and context.strip():
        return context.strip()
    return None


def get_zep_context_sync(
    zep_client: Zep,
    thread_id: str,
    *,
    template_id: str | None = None,
) -> str | None:
    """Synchronous variant of :func:`get_zep_context`.

    Use inside a synchronous graph node with a synchronous
    :class:`~zep_cloud.client.Zep` client.

    Args:
        zep_client: An initialised synchronous :class:`~zep_cloud.client.Zep` client.
        thread_id: The Zep thread ID to retrieve context for.
        template_id: Optional ID of a context template to format the block with.

    Returns:
        The Context Block string, or ``None`` if Zep returned no context or the
        call failed. Failures are logged, never raised.
    """
    try:
        response = zep_client.thread.get_user_context(
            thread_id,
            template_id=template_id,
        )
    except Exception:
        logger.warning(
            "Failed to retrieve Zep context for thread %s",
            thread_id,
            exc_info=True,
        )
        return None

    context: str | None = getattr(response, "context", None)
    if context and context.strip():
        return context.strip()
    return None


def format_context_block(
    context: str | None,
    *,
    base_instructions: str | None = None,
    template: str = DEFAULT_CONTEXT_TEMPLATE,
) -> str:
    """Combine base instructions with a retrieved Context Block.

    Args:
        context: A Context Block string (e.g. from :func:`get_zep_context`),
            or ``None``/empty.
        base_instructions: Optional fixed system instructions to place before
            the memory block.
        template: A format string with a single ``{context}`` placeholder used
            to wrap the Context Block. Defaults to :data:`DEFAULT_CONTEXT_TEMPLATE`.

    Returns:
        The assembled system-prompt text. When ``context`` is empty, only the
        base instructions are returned (or an empty string if there are none).
    """
    parts: list[str] = []
    if base_instructions and base_instructions.strip():
        parts.append(base_instructions.strip())
    if context and context.strip():
        parts.append(template.format(context=context.strip()))
    return "\n\n".join(parts)


async def build_system_message(
    zep_client: AsyncZep,
    thread_id: str,
    *,
    base_instructions: str | None = None,
    template: str = DEFAULT_CONTEXT_TEMPLATE,
    template_id: str | None = None,
) -> SystemMessage:
    """Build a LangChain :class:`~langchain_core.messages.SystemMessage` with Zep context.

    Convenience wrapper that fetches the Context Block for ``thread_id`` and
    formats it together with ``base_instructions`` into a ``SystemMessage`` ready
    to prepend to the model's message list.

    Args:
        zep_client: An initialised :class:`~zep_cloud.client.AsyncZep` client.
        thread_id: The Zep thread ID to retrieve context for.
        base_instructions: Optional fixed system instructions placed before the
            memory block.
        template: Format string wrapping the Context Block (see
            :func:`format_context_block`).
        template_id: Optional ID of a Zep context template.

    Returns:
        A ``SystemMessage`` whose content is the base instructions plus the
        retrieved memory block. If Zep returns no context, the message contains
        only the base instructions.
    """
    context = await get_zep_context(zep_client, thread_id, template_id=template_id)
    content = format_context_block(
        context,
        base_instructions=base_instructions,
        template=template,
    )
    return SystemMessage(content=content)


def build_system_message_sync(
    zep_client: Zep,
    thread_id: str,
    *,
    base_instructions: str | None = None,
    template: str = DEFAULT_CONTEXT_TEMPLATE,
    template_id: str | None = None,
) -> SystemMessage:
    """Synchronous variant of :func:`build_system_message`.

    Args:
        zep_client: An initialised synchronous :class:`~zep_cloud.client.Zep` client.
        thread_id: The Zep thread ID to retrieve context for.
        base_instructions: Optional fixed system instructions placed before the
            memory block.
        template: Format string wrapping the Context Block.
        template_id: Optional ID of a Zep context template.

    Returns:
        A ``SystemMessage`` with the base instructions plus the retrieved memory
        block.
    """
    context = get_zep_context_sync(zep_client, thread_id, template_id=template_id)
    content = format_context_block(
        context,
        base_instructions=base_instructions,
        template=template,
    )
    return SystemMessage(content=content)
