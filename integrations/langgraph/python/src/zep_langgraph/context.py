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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from langchain_core.messages import SystemMessage
from zep_cloud.client import AsyncZep, Zep

logger = logging.getLogger(__name__)

#: Default wrapper applied around a retrieved Context Block when building a
#: system message. The ``{context}`` placeholder is filled with the block via
#: plain string replacement (``template.replace("{context}", context_text)``),
#: never ``str.format`` -- so context text or a custom template containing
#: ``{``/``}``/``%`` is always safe to inject.
#:
#: This exact string is canonical across zep-adk's Python, Go, and TypeScript
#: implementations -- keep them in sync.
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
    builders. Unlike the framework-hooked ports (ADK, Pydantic AI, ...), these
    helpers are plain functions with no surrounding framework object to
    thread through, so ``ContextInput`` carries only the Zep call inputs.

    Attributes:
        zep: The ``AsyncZep`` client in use.
        user_id: The Zep user ID for this turn (as passed to
            :func:`get_zep_context` / :func:`build_system_message`).
        thread_id: The Zep thread ID for this turn.
        user_message: The user's message text for this turn (as passed by the
            caller; these helpers do not extract it from graph state).

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

            context = await get_zep_context(
                zep, thread_id, context_builder=my_builder,
                user_id="user-1", user_message=state["messages"][-1].content,
            )
    """

    zep: AsyncZep
    user_id: str
    thread_id: str
    user_message: str


#: Type alias for a custom async context builder function.
#:
#: A context builder receives a single :class:`ContextInput` and returns the
#: context string to inject into the prompt (or ``None`` to skip injection).
#:
#: Error semantics: if the builder raises, :func:`get_zep_context` /
#: :func:`build_system_message` log a warning and return ``None`` / the base
#: instructions -- these helpers never raise.
ContextBuilder = Callable[[ContextInput], Awaitable[str | None]]

#: Synchronous twin of :data:`ContextBuilder`, for use with
#: :func:`get_zep_context_sync` / :func:`build_system_message_sync`.
ContextBuilderSync = Callable[[ContextInput], str | None]


async def get_zep_context(
    zep_client: AsyncZep,
    thread_id: str,
    *,
    template_id: str | None = None,
    context_builder: ContextBuilder | None = None,
    user_id: str = "",
    user_message: str = "",
) -> str | None:
    """Fetch the Zep Context Block for a thread.

    Wraps :meth:`AsyncZep.thread.get_user_context`. The returned block is
    assembled from the whole user graph; the thread is used only to determine
    what is relevant right now.

    Args:
        zep_client: An initialised :class:`~zep_cloud.client.AsyncZep` client.
        thread_id: The Zep thread ID to retrieve context for.
        template_id: Optional ID of a context template to format the block with.
            Ignored when ``context_builder`` is set.
        context_builder: Optional async callable that *replaces*
            ``thread.get_user_context`` for this call. Receives a single
            :class:`ContextInput` built from ``zep_client``, ``user_id``,
            ``thread_id``, and ``user_message``. Use this to search a
            different graph, apply filters, or combine multiple sources.
        user_id: The Zep user ID for this turn. Only used to populate
            ``ContextInput`` when ``context_builder`` is set; ignored
            otherwise.
        user_message: The user's message text for this turn. Only used to
            populate ``ContextInput`` when ``context_builder`` is set;
            ignored otherwise.

    Returns:
        The Context Block string, or ``None`` if Zep returned no context, the
        call failed, or the builder raised. Failures are logged, never raised.
    """
    if context_builder is not None:
        try:
            return await context_builder(
                ContextInput(
                    zep=zep_client,
                    user_id=user_id,
                    thread_id=thread_id,
                    user_message=user_message,
                )
            )
        except Exception:
            logger.warning(
                "Custom context_builder raised for thread %s -- skipping context injection",
                thread_id,
                exc_info=True,
            )
            return None

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
    context_builder: ContextBuilderSync | None = None,
    user_id: str = "",
    user_message: str = "",
) -> str | None:
    """Synchronous variant of :func:`get_zep_context`.

    Use inside a synchronous graph node with a synchronous
    :class:`~zep_cloud.client.Zep` client.

    Args:
        zep_client: An initialised synchronous :class:`~zep_cloud.client.Zep` client.
        thread_id: The Zep thread ID to retrieve context for.
        template_id: Optional ID of a context template to format the block with.
            Ignored when ``context_builder`` is set.
        context_builder: Optional synchronous callable that *replaces*
            ``thread.get_user_context`` for this call. See
            :func:`get_zep_context` for the full contract.
        user_id: The Zep user ID for this turn (populates ``ContextInput``).
        user_message: The user's message text for this turn (populates
            ``ContextInput``).

    Returns:
        The Context Block string, or ``None`` if Zep returned no context, the
        call failed, or the builder raised. Failures are logged, never raised.
    """
    if context_builder is not None:
        try:
            return context_builder(
                ContextInput(
                    zep=zep_client,  # type: ignore[arg-type]
                    user_id=user_id,
                    thread_id=thread_id,
                    user_message=user_message,
                )
            )
        except Exception:
            logger.warning(
                "Custom context_builder raised for thread %s -- skipping context injection",
                thread_id,
                exc_info=True,
            )
            return None

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
        template: A template string with a literal ``{context}`` placeholder
            used to wrap the Context Block, rendered via plain string
            replacement (``template.replace("{context}", context)``) --
            never ``str.format`` -- so context text or a custom template
            containing ``{``/``}``/``%`` is always safe to inject. Defaults
            to :data:`DEFAULT_CONTEXT_TEMPLATE`.

    Returns:
        The assembled system-prompt text. When ``context`` is empty, only the
        base instructions are returned (or an empty string if there are none).
    """
    parts: list[str] = []
    if base_instructions and base_instructions.strip():
        parts.append(base_instructions.strip())
    if context and context.strip():
        parts.append(template.replace("{context}", context.strip()))
    return "\n\n".join(parts)


async def build_system_message(
    zep_client: AsyncZep,
    thread_id: str,
    *,
    base_instructions: str | None = None,
    template: str = DEFAULT_CONTEXT_TEMPLATE,
    template_id: str | None = None,
    context_builder: ContextBuilder | None = None,
    user_id: str = "",
    user_message: str = "",
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
        template: Template string wrapping the Context Block, rendered via
            plain string replacement (see :func:`format_context_block`).
        template_id: Optional ID of a Zep context template. Ignored when
            ``context_builder`` is set.
        context_builder: Optional async callable that *replaces*
            ``thread.get_user_context`` for this call (see
            :func:`get_zep_context`).
        user_id: The Zep user ID for this turn (populates ``ContextInput``
            when ``context_builder`` is set).
        user_message: The user's message text for this turn (populates
            ``ContextInput`` when ``context_builder`` is set).

    Returns:
        A ``SystemMessage`` whose content is the base instructions plus the
        retrieved memory block. If Zep returns no context (or the builder
        returns ``None``), the message contains only the base instructions.
    """
    context = await get_zep_context(
        zep_client,
        thread_id,
        template_id=template_id,
        context_builder=context_builder,
        user_id=user_id,
        user_message=user_message,
    )
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
    context_builder: ContextBuilderSync | None = None,
    user_id: str = "",
    user_message: str = "",
) -> SystemMessage:
    """Synchronous variant of :func:`build_system_message`.

    Args:
        zep_client: An initialised synchronous :class:`~zep_cloud.client.Zep` client.
        thread_id: The Zep thread ID to retrieve context for.
        base_instructions: Optional fixed system instructions placed before the
            memory block.
        template: Template string wrapping the Context Block.
        template_id: Optional ID of a Zep context template. Ignored when
            ``context_builder`` is set.
        context_builder: Optional synchronous callable that *replaces*
            ``thread.get_user_context`` for this call (see
            :func:`get_zep_context_sync`).
        user_id: The Zep user ID for this turn (populates ``ContextInput``
            when ``context_builder`` is set).
        user_message: The user's message text for this turn (populates
            ``ContextInput`` when ``context_builder`` is set).

    Returns:
        A ``SystemMessage`` with the base instructions plus the retrieved memory
        block.
    """
    context = get_zep_context_sync(
        zep_client,
        thread_id,
        template_id=template_id,
        context_builder=context_builder,
        user_id=user_id,
        user_message=user_message,
    )
    content = format_context_block(
        context,
        base_instructions=base_instructions,
        template=template,
    )
    return SystemMessage(content=content)
