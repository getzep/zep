"""
A prebuilt ``pre_model_hook`` for ``langgraph.prebuilt.create_react_agent``.

``create_react_agent`` accepts a ``pre_model_hook`` node that runs immediately
before every call to the model. Per its documented contract, the hook receives
the current graph state (a mapping with a ``messages`` key) and returns a
state update containing **either** ``messages`` (which overwrites the
`messages` channel -- typically paired with
``RemoveMessage(id=REMOVE_ALL_MESSAGES)`` for history trimming) **or**
``llm_input_messages`` (used as the input to the model for this step only,
without touching the persisted ``messages`` channel). This module always
returns ``llm_input_messages``, since context injection should not become
part of permanent thread history -- it is re-fetched fresh on every turn.

:func:`create_zep_pre_model_hook` builds such a hook using the same retrieval
path as :func:`~zep_langgraph.context.build_system_message`: it fetches (or
builds, via a custom ``context_builder``) the Context Block for the
configured user/thread and prepends it as a ``SystemMessage``.

This hook only injects context -- it does not persist messages. Call
:func:`~zep_langgraph.persistence.persist_messages` after the model responds,
in your graph node or a dedicated node, to save the turn.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from zep_cloud.client import AsyncZep

from .context import DEFAULT_CONTEXT_TEMPLATE, ContextBuilder, format_context_block, get_zep_context

logger = logging.getLogger(__name__)

#: The type of hook ``create_zep_pre_model_hook`` returns: an async callable
#: matching ``create_react_agent(pre_model_hook=...)``'s contract, taking the
#: current graph state and returning a partial state update.
ZepPreModelHook = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def _latest_human_text(messages: list[BaseMessage]) -> str:
    """Return the text content of the most recent ``HumanMessage``, or ``""``."""
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            content = message.content
            if isinstance(content, str):
                return content
            return str(content) if content is not None else ""
    return ""


def create_zep_pre_model_hook(
    zep_client: AsyncZep,
    *,
    user_id: str,
    thread_id: str,
    base_instructions: str | None = None,
    template: str = DEFAULT_CONTEXT_TEMPLATE,
    template_id: str | None = None,
    context_builder: ContextBuilder | None = None,
) -> ZepPreModelHook:
    """Build a ``pre_model_hook`` that injects Zep context via ``llm_input_messages``.

    Register the returned hook with ``create_react_agent``::

        from langgraph.prebuilt import create_react_agent
        from zep_langgraph import create_zep_pre_model_hook

        agent = create_react_agent(
            model=model,
            tools=[...],
            pre_model_hook=create_zep_pre_model_hook(
                zep, user_id="user-1", thread_id="thread-1",
                base_instructions="You are a helpful assistant.",
            ),
        )

    On every model call, the hook fetches the Context Block for
    (``user_id``, ``thread_id``) -- via ``thread.get_user_context`` by
    default, or ``context_builder`` if set -- wraps it with
    ``base_instructions`` using ``template``, and prepends the result as a
    ``SystemMessage`` to the messages sent to the model **for this step
    only**. It returns ``llm_input_messages`` (never ``messages``), so the
    persisted thread history in graph state is untouched -- context is
    re-fetched fresh on every turn rather than baked into history.

    This hook does not persist anything. Call
    :func:`~zep_langgraph.persistence.persist_messages` separately (e.g. in
    your agent node, after the model responds, or in a ``post_model_hook``)
    to save the turn to Zep.

    Args:
        zep_client: An initialised :class:`~zep_cloud.client.AsyncZep` client.
        user_id: The Zep user ID for this agent/graph. Only used to populate
            ``ContextInput`` when ``context_builder`` is set.
        thread_id: The Zep thread ID to retrieve context for.
        base_instructions: Optional fixed system instructions placed before
            the memory block.
        template: Template string wrapping the Context Block, rendered via
            plain string replacement (see
            :func:`~zep_langgraph.context.format_context_block`).
        template_id: Optional ID of a Zep context template. Ignored when
            ``context_builder`` is set.
        context_builder: Optional async callable that *replaces*
            ``thread.get_user_context`` for this call (see
            :func:`~zep_langgraph.context.get_zep_context`). Receives a
            :class:`~zep_langgraph.context.ContextInput` whose
            ``user_message`` is the latest ``HumanMessage`` text in state.

    Returns:
        An async callable suitable for ``create_react_agent(pre_model_hook=...)``.
        A Zep failure is logged and degrades to base-instructions-only --
        the hook never raises.
    """

    async def _pre_model_hook(state: dict[str, Any]) -> dict[str, Any]:
        messages: list[BaseMessage] = list(state.get("messages", []))
        user_message = _latest_human_text(messages)

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
        system_message = SystemMessage(content=content)

        return {"llm_input_messages": [system_message, *messages]}

    return _pre_model_hook
