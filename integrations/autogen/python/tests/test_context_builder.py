"""
Tests for ``ContextInput`` / ``context_builder`` / ``context_template`` support
on ``ZepUserMemory``, and the thread creation path in ``add()``.

AutoGen's ``Memory`` protocol splits injection and persistence: ``update_context()``
is called automatically before every model call (injection only), while
``add()`` is invoked explicitly by the caller to persist a turn. There is no
gather/concurrency here -- persistence and context building are separate calls
made by the framework/app, not two halves of one turn.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from autogen_core.memory import MemoryContent, MemoryMimeType, MemoryQueryResult
from autogen_core.model_context import UnboundedChatCompletionContext
from autogen_core.models import UserMessage
from conftest import FakePager
from zep_cloud.client import AsyncZep

from zep_autogen import ZepUserMemory
from zep_autogen.memory import ContextInput

USER_UUID = "11111111-1111-1111-1111-111111111111"
THREAD_UUID = "22222222-2222-2222-2222-222222222222"
GRAPH_UUID = "33333333-3333-3333-3333-333333333333"


def _make_mock_client() -> MagicMock:
    client = MagicMock(spec=AsyncZep)
    client.user = MagicMock()
    client.user.get = AsyncMock(return_value=MagicMock(graph_uuid=GRAPH_UUID))
    client.thread = MagicMock()
    client.thread.create = AsyncMock(return_value=MagicMock(uuid_=THREAD_UUID))
    client.thread.get_context = AsyncMock(return_value=MagicMock(context="default context"))
    client.thread.list_messages = AsyncMock(return_value=FakePager([]))
    client.thread.add_messages = AsyncMock()
    return client


async def _context_with_message(text: str) -> UnboundedChatCompletionContext:
    ctx = UnboundedChatCompletionContext()
    await ctx.add_message(UserMessage(content=text, source="user"))
    return ctx


class TestContextBuilder:
    @pytest.mark.asyncio
    async def test_update_context_uses_context_builder(self) -> None:
        """When context_builder is set, get_context is NOT called; the
        builder receives a ContextInput with the right fields; the injected
        content contains the builder's output."""
        client = _make_mock_client()
        received: list[ContextInput] = []

        async def builder(ctx: ContextInput) -> str | None:
            received.append(ctx)
            return "Built context block"

        memory = ZepUserMemory(
            client=client,
            user_uuid=USER_UUID,
            thread_uuid=THREAD_UUID,
            context_builder=builder,
        )
        model_context = await _context_with_message("What's up?")

        result = await memory.update_context(model_context)

        client.thread.get_context.assert_not_called()

        assert len(received) == 1
        built = received[0]
        assert built.zep is client
        assert built.user_uuid == USER_UUID
        assert built.thread_uuid == THREAD_UUID
        assert built.graph_uuid == GRAPH_UUID
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
            client=client,
            user_uuid=USER_UUID,
            thread_uuid=THREAD_UUID,
            context_builder=builder,
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
            user_uuid=USER_UUID,
            thread_uuid=THREAD_UUID,
            context_builder=failing_builder,
        )
        model_context = await _context_with_message("hello")

        result = await memory.update_context(model_context)

        assert result.memories.results == []


class TestThreadCreation:
    @pytest.mark.asyncio
    async def test_update_context_without_thread_injects_nothing(self) -> None:
        """Without a thread, there is no thread context to retrieve."""
        client = _make_mock_client()
        memory = ZepUserMemory(client=client, user_uuid=USER_UUID)
        model_context = await _context_with_message("hello")

        result = await memory.update_context(model_context)

        client.thread.get_context.assert_not_called()
        assert result.memories.results == []

    @pytest.mark.asyncio
    async def test_add_creates_thread_once(self) -> None:
        """The thread is created one time and then reused."""
        client = _make_mock_client()
        memory = ZepUserMemory(client=client, user_uuid=USER_UUID)

        content = MemoryContent(
            content="hello",
            mime_type=MemoryMimeType.TEXT,
            metadata={"type": "message", "role": "user"},
        )
        await memory.add(content)
        await memory.add(content)

        client.thread.create.assert_called_once_with(user_uuid=USER_UUID)
        assert client.thread.add_messages.call_count == 2

    @pytest.mark.asyncio
    async def test_add_swallows_thread_creation_failure(self) -> None:
        """A creation failure is logged, and add() does not raise."""
        client = _make_mock_client()
        client.thread.create = AsyncMock(side_effect=RuntimeError("zep down"))
        memory = ZepUserMemory(client=client, user_uuid=USER_UUID)

        await memory.add(
            MemoryContent(
                content="hello",
                mime_type=MemoryMimeType.TEXT,
                metadata={"type": "message", "role": "user"},
            )
        )

        client.thread.add_messages.assert_not_called()
