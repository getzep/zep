"""
Tests for ``context_template`` on ``ZepUserMemory``: overriding the template
used to wrap injected context, and the ``str.replace`` (never ``str.format``)
rendering contract.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from autogen_core.model_context import UnboundedChatCompletionContext
from autogen_core.models import SystemMessage, UserMessage
from zep_cloud.client import AsyncZep

from zep_autogen import ZepUserMemory
from zep_autogen.memory import DEFAULT_CONTEXT_TEMPLATE


def _make_mock_client(context: str | None = "some context") -> MagicMock:
    client = MagicMock(spec=AsyncZep)
    client.user = MagicMock()
    client.user.add = AsyncMock()
    client.thread = MagicMock()
    client.thread.create = AsyncMock()
    client.thread.get_user_context = AsyncMock(return_value=MagicMock(context=context))
    client.thread.get = AsyncMock(return_value=MagicMock(messages=[]))
    return client


async def _context_with_message(text: str) -> UnboundedChatCompletionContext:
    ctx = UnboundedChatCompletionContext()
    await ctx.add_message(UserMessage(content=text, source="user"))
    return ctx


def _system_messages(messages: list) -> list[SystemMessage]:
    return [m for m in messages if isinstance(m, SystemMessage)]


class TestContextTemplate:
    @pytest.mark.asyncio
    async def test_update_context_template_override(self) -> None:
        client = _make_mock_client(context="the retrieved facts")
        custom_template = "CUSTOM WRAP >>> {context} <<< END"
        memory = ZepUserMemory(
            client=client,
            user_id="user-1",
            thread_id="thread-1",
            context_template=custom_template,
        )
        model_context = await _context_with_message("hi")

        await memory.update_context(model_context)

        messages = await model_context.get_messages()
        system_messages = _system_messages(messages)
        assert any(
            "CUSTOM WRAP >>> the retrieved facts <<< END" in m.content for m in system_messages
        )

    @pytest.mark.asyncio
    async def test_template_rendered_via_replace_not_format(self) -> None:
        """Context containing literal `{` / `%` must survive unescaped -- this
        would raise or corrupt output under str.format()."""
        tricky_context = "50% done; use {braces} and {other} freely"
        client = _make_mock_client(context=tricky_context)
        memory = ZepUserMemory(client=client, user_id="user-1", thread_id="thread-1")
        model_context = await _context_with_message("hi")

        # Must not raise.
        await memory.update_context(model_context)

        messages = await model_context.get_messages()
        system_messages = _system_messages(messages)
        assert any(tricky_context in m.content for m in system_messages)

    @pytest.mark.asyncio
    async def test_default_template_is_canonical(self) -> None:
        assert DEFAULT_CONTEXT_TEMPLATE == (
            "The following context is retrieved from Zep, the agent's long-term memory. "
            "It contains relevant facts, entities, and prior knowledge about the user. "
            "Use it to inform your responses.\n\n"
            "<ZEP_CONTEXT>\n"
            "{context}\n"
            "</ZEP_CONTEXT>"
        )
