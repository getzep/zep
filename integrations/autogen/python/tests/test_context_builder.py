from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from autogen_core.model_context import UnboundedChatCompletionContext
from autogen_core.models import UserMessage
from zep_cloud.client import AsyncZep

from zep_autogen import ZepUserMemory
from zep_autogen.memory import ContextInput


def _make_client() -> MagicMock:
    client = MagicMock(spec=AsyncZep)
    client.thread = MagicMock()
    client.thread.create = AsyncMock(return_value=SimpleNamespace(uuid_="thread-1"))
    client.thread.get_context = AsyncMock(return_value=SimpleNamespace(context="default context"))
    client.thread.list_messages = AsyncMock(return_value=SimpleNamespace(items=[]))
    return client


async def _context_with_message(text: str) -> UnboundedChatCompletionContext:
    context = UnboundedChatCompletionContext()
    await context.add_message(UserMessage(content=text, source="user"))
    return context


@pytest.mark.asyncio
async def test_builder_receives_v4_context_input_and_replaces_default_retrieval() -> None:
    client = _make_client()
    received: list[ContextInput] = []

    async def builder(context: ContextInput) -> str:
        received.append(context)
        return "Built context block"

    memory = ZepUserMemory(
        client=client,
        user_uuid="user-1",
        thread_uuid="thread-1",
        graph_uuid="graph-1",
        context_builder=builder,
    )
    model_context = await _context_with_message("What does Alice like?")

    result = await memory.update_context(model_context)

    assert len(received) == 1
    assert received[0].zep is client
    assert received[0].user_uuid == "user-1"
    assert received[0].thread_uuid == "thread-1"
    assert received[0].graph_uuid == "graph-1"
    assert received[0].user_message == "What does Alice like?"
    assert received[0].model_context is model_context
    client.thread.get_context.assert_not_awaited()
    messages = await model_context.get_messages()
    assert any("Built context block" in str(message.content) for message in messages)
    assert result.memories.results[0].content == "Built context block"


@pytest.mark.asyncio
async def test_builder_receives_empty_user_message_when_context_has_no_user_text() -> None:
    client = _make_client()
    received: list[ContextInput] = []

    async def builder(context: ContextInput) -> None:
        received.append(context)
        return None

    memory = ZepUserMemory(
        client=client,
        user_uuid="user-1",
        context_builder=builder,
    )
    model_context = UnboundedChatCompletionContext()
    await model_context.add_message(UserMessage(content="", source="user"))
    before = await model_context.get_messages()

    await memory.update_context(model_context)

    assert received[0].user_message == ""
    assert await model_context.get_messages() == before


@pytest.mark.asyncio
async def test_builder_error_degrades_without_failing_the_turn() -> None:
    client = _make_client()

    async def builder(_context: ContextInput) -> str:
        raise RuntimeError("builder failed")

    memory = ZepUserMemory(
        client=client,
        user_uuid="user-1",
        thread_uuid="thread-1",
        context_builder=builder,
    )
    model_context = await _context_with_message("hello")

    result = await memory.update_context(model_context)

    assert result.memories.results == []
    client.thread.get_context.assert_not_awaited()


@pytest.mark.asyncio
async def test_builder_can_run_without_a_thread_uuid() -> None:
    client = _make_client()
    builder = AsyncMock(return_value="custom context")
    memory = ZepUserMemory(client=client, user_uuid="user-1", context_builder=builder)
    model_context = await _context_with_message("hello")

    await memory.update_context(model_context)

    builder.assert_awaited_once()
    client.thread.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_default_context_path_does_not_create_a_thread() -> None:
    client = _make_client()
    memory = ZepUserMemory(client=client, user_uuid="user-1")
    model_context = await _context_with_message("hello")

    result = await memory.update_context(model_context)

    assert result.memories.results == []
    client.thread.create.assert_not_awaited()
    client.thread.get_context.assert_not_awaited()
