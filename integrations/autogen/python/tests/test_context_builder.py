"""
Tests for ``ContextInput`` / ``context_builder`` / ``context_template`` support
on ``ZepUserMemory``, and the lazy user+thread provisioning path in
``update_context()`` / ``add()``.

AutoGen's ``Memory`` protocol splits injection and persistence: ``update_context()``
is called automatically before every model call (injection only), while
``add()`` is invoked explicitly by the caller to persist a turn. There is no
gather/concurrency here -- persistence and context building are separate calls
made by the framework/app, not two halves of one turn.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from autogen_core.memory import MemoryQueryResult
from autogen_core.model_context import UnboundedChatCompletionContext
from autogen_core.models import UserMessage
from zep_cloud.client import AsyncZep

from zep_autogen import ZepUserMemory
from zep_autogen.memory import ContextInput


def _make_mock_client() -> MagicMock:
    client = MagicMock(spec=AsyncZep)
    client.user = MagicMock()
    client.user.add = AsyncMock()
    client.thread = MagicMock()
    client.thread.create = AsyncMock()
    client.thread.get_user_context = AsyncMock(return_value=MagicMock(context="default context"))
    client.thread.get = AsyncMock(return_value=MagicMock(messages=[]))
    client.thread.add_messages = AsyncMock()
    return client


async def _context_with_message(text: str) -> UnboundedChatCompletionContext:
    ctx = UnboundedChatCompletionContext()
    await ctx.add_message(UserMessage(content=text, source="user"))
    return ctx


class TestContextBuilder:
    @pytest.mark.asyncio
    async def test_update_context_uses_context_builder(self) -> None:
        """When context_builder is set, get_user_context is NOT called; the
        builder receives a ContextInput with the right fields; the injected
        content contains the builder's output."""
        client = _make_mock_client()
        received: list[ContextInput] = []

        async def builder(ctx: ContextInput) -> str | None:
            received.append(ctx)
            return "Built context block"

        memory = ZepUserMemory(
            client=client, user_id="user-1", thread_id="thread-1", context_builder=builder
        )
        model_context = await _context_with_message("What's up?")

        result = await memory.update_context(model_context)

        client.thread.get_user_context.assert_not_called()

        assert len(received) == 1
        built = received[0]
        assert built.zep is client
        assert built.user_id == "user-1"
        assert built.thread_id == "thread-1"
        assert built.user_message == "What's up?"
        assert built.model_context is model_context

        messages = await model_context.get_messages()
        system_messages = [
            m for m in messages if hasattr(m, "content") and not hasattr(m, "source")
        ]
        assert any("Built context block" in str(m.content) for m in system_messages)
        assert isinstance(result.memories, MemoryQueryResult)

    @pytest.mark.asyncio
    async def test_update_context_builder_none_skips_injection(self) -> None:
        """No user message in model_context -> builder called with user_message=''."""
        client = _make_mock_client()
        received: list[ContextInput] = []

        async def builder(ctx: ContextInput) -> str | None:
            received.append(ctx)
            return None

        memory = ZepUserMemory(
            client=client, user_id="user-1", thread_id="thread-1", context_builder=builder
        )
        model_context = UnboundedChatCompletionContext()
        await model_context.add_message(UserMessage(content="", source="user"))

        before_messages = await model_context.get_messages()
        await memory.update_context(model_context)
        after_messages = await model_context.get_messages()

        # No system message injected when builder returns None.
        assert len(after_messages) == len(before_messages)

    @pytest.mark.asyncio
    async def test_update_context_builder_error_degrades(self) -> None:
        """A builder exception must be logged and degrade to an empty result,
        never raise into update_context()."""
        client = _make_mock_client()

        async def failing_builder(ctx: ContextInput) -> str | None:
            raise RuntimeError("builder boom")

        memory = ZepUserMemory(
            client=client,
            user_id="user-1",
            thread_id="thread-1",
            context_builder=failing_builder,
        )
        model_context = await _context_with_message("hello")

        result = await memory.update_context(model_context)

        assert result.memories.results == []


class TestLazyProvisioningInUpdateContext:
    @pytest.mark.asyncio
    async def test_update_context_creates_user_and_thread_lazily(self) -> None:
        client = _make_mock_client()
        memory = ZepUserMemory(client=client, user_id="user-1", thread_id="thread-1")
        model_context = await _context_with_message("hello")

        await memory.update_context(model_context)

        client.user.add.assert_called_once()
        client.thread.create.assert_called_once()
