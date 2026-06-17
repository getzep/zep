"""
Tests for the message-persistence helpers (zep_langgraph.persistence).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from zep_cloud import Message

from zep_langgraph.persistence import (
    MAX_MESSAGE_CHARS,
    MAX_MESSAGES_PER_CALL,
    persist_messages,
    persist_messages_sync,
    to_zep_message,
    to_zep_messages,
)


class TestToZepMessage:
    def test_human_maps_to_user(self) -> None:
        msg = to_zep_message(HumanMessage(content="hello"))
        assert msg is not None
        assert msg.role == "user"
        assert msg.content == "hello"

    def test_ai_maps_to_assistant(self) -> None:
        msg = to_zep_message(AIMessage(content="hi there"))
        assert msg is not None
        assert msg.role == "assistant"

    def test_system_maps_to_system(self) -> None:
        msg = to_zep_message(SystemMessage(content="be nice"))
        assert msg is not None
        assert msg.role == "system"

    def test_tool_maps_to_tool(self) -> None:
        msg = to_zep_message(ToolMessage(content="result", tool_call_id="x"))
        assert msg is not None
        assert msg.role == "tool"

    def test_empty_content_returns_none(self) -> None:
        # AI message carrying only tool calls -> no text -> dropped
        assert to_zep_message(AIMessage(content="")) is None

    def test_existing_zep_message_passthrough(self) -> None:
        original = Message(role="user", content="kept", name="Bob")
        assert to_zep_message(original) is original

    def test_user_name_applied_to_user_role(self) -> None:
        msg = to_zep_message(HumanMessage(content="hi"), user_name="Alice Smith")
        assert msg is not None
        assert msg.name == "Alice Smith"

    def test_assistant_name_applied(self) -> None:
        msg = to_zep_message(AIMessage(content="hi"), assistant_name="Bot")
        assert msg is not None
        assert msg.name == "Bot"

    def test_existing_name_not_overwritten(self) -> None:
        msg = to_zep_message(HumanMessage(content="hi", name="Original"), user_name="Ignored")
        assert msg is not None
        assert msg.name == "Original"

    def test_list_content_flattened(self) -> None:
        msg = to_zep_message(
            HumanMessage(content=[{"type": "text", "text": "a"}, {"type": "text", "text": "b"}])
        )
        assert msg is not None
        assert msg.content == "a b"

    def test_long_content_truncated(self) -> None:
        msg = to_zep_message(HumanMessage(content="x" * (MAX_MESSAGE_CHARS + 500)))
        assert msg is not None
        assert len(msg.content) == MAX_MESSAGE_CHARS

    def test_native_zep_message_oversize_truncated(self) -> None:
        # Native Zep Message objects (the README/example path) must also be
        # truncated, or Zep returns a 400 for content > 4096 chars.
        original = Message(role="user", content="x" * (MAX_MESSAGE_CHARS + 500), name="Bob")
        msg = to_zep_message(original)
        assert msg is not None
        assert len(msg.content) == MAX_MESSAGE_CHARS
        assert msg.name == "Bob"
        # Original must not be mutated.
        assert len(original.content) == MAX_MESSAGE_CHARS + 500

    def test_native_zep_message_within_limit_passthrough(self) -> None:
        original = Message(role="user", content="short", name="Bob")
        assert to_zep_message(original) is original


class TestToZepMessages:
    def test_drops_empty_messages(self) -> None:
        result = to_zep_messages([HumanMessage(content="keep"), AIMessage(content="")])
        assert len(result) == 1
        assert result[0].content == "keep"


def _make_async_client(context: str | None = None) -> MagicMock:
    client = MagicMock()
    client.thread = MagicMock()
    response = MagicMock()
    response.context = context
    client.thread.add_messages = AsyncMock(return_value=response)
    return client


def _make_sync_client(context: str | None = None) -> MagicMock:
    client = MagicMock()
    client.thread = MagicMock()
    response = MagicMock()
    response.context = context
    client.thread.add_messages = MagicMock(return_value=response)
    return client


class TestPersistMessages:
    @pytest.mark.asyncio
    async def test_persists_converted_messages(self) -> None:
        client = _make_async_client()
        await persist_messages(
            client,
            "thread-1",
            [HumanMessage(content="hi"), AIMessage(content="hello")],
            user_name="Alice",
        )
        client.thread.add_messages.assert_awaited_once()
        call = client.thread.add_messages.call_args
        assert call.args[0] == "thread-1"
        msgs = call.kwargs["messages"]
        assert len(msgs) == 2
        assert msgs[0].role == "user"
        assert msgs[0].name == "Alice"
        assert msgs[1].role == "assistant"
        assert call.kwargs["return_context"] is False

    @pytest.mark.asyncio
    async def test_return_context_returns_block(self) -> None:
        client = _make_async_client(context="User likes hiking.")
        ctx = await persist_messages(
            client, "thread-1", [HumanMessage(content="hi")], return_context=True
        )
        assert ctx == "User likes hiking."
        assert client.thread.add_messages.call_args.kwargs["return_context"] is True

    @pytest.mark.asyncio
    async def test_no_context_returns_none_by_default(self) -> None:
        client = _make_async_client(context="ignored")
        ctx = await persist_messages(client, "thread-1", [HumanMessage(content="hi")])
        assert ctx is None

    @pytest.mark.asyncio
    async def test_ignore_roles_passed_through(self) -> None:
        client = _make_async_client()
        await persist_messages(
            client, "thread-1", [HumanMessage(content="hi")], ignore_roles=["assistant"]
        )
        assert client.thread.add_messages.call_args.kwargs["ignore_roles"] == ["assistant"]

    @pytest.mark.asyncio
    async def test_skips_when_no_persistable_messages(self) -> None:
        client = _make_async_client()
        result = await persist_messages(client, "thread-1", [AIMessage(content="")])
        assert result is None
        client.thread.add_messages.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_zep_failure_returns_none_not_raise(self) -> None:
        client = MagicMock()
        client.thread = MagicMock()
        client.thread.add_messages = AsyncMock(side_effect=RuntimeError("api down"))
        result = await persist_messages(
            client, "thread-1", [HumanMessage(content="hi")], return_context=True
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_chunks_over_thirty_messages(self) -> None:
        # Zep rejects > 30 messages per add_messages call with a 400; we must
        # split into multiple calls rather than send them all at once.
        client = _make_async_client()
        msgs = [HumanMessage(content=f"m{i}") for i in range(70)]
        await persist_messages(client, "thread-1", msgs)
        assert client.thread.add_messages.await_count == 3
        sent = [c.kwargs["messages"] for c in client.thread.add_messages.await_args_list]
        assert [len(s) for s in sent] == [30, 30, 10]
        # No chunk exceeds the cap.
        assert all(len(s) <= MAX_MESSAGES_PER_CALL for s in sent)
        # Every message is persisted exactly once, in order.
        flattened = [m.content for chunk in sent for m in chunk]
        assert flattened == [f"m{i}" for i in range(70)]

    @pytest.mark.asyncio
    async def test_context_requested_only_on_last_chunk(self) -> None:
        client = _make_async_client(context="final block")
        msgs = [HumanMessage(content=f"m{i}") for i in range(40)]
        ctx = await persist_messages(client, "thread-1", msgs, return_context=True)
        calls = client.thread.add_messages.await_args_list
        assert len(calls) == 2
        assert calls[0].kwargs["return_context"] is False
        assert calls[1].kwargs["return_context"] is True
        assert ctx == "final block"

    @pytest.mark.asyncio
    async def test_single_chunk_makes_single_call(self) -> None:
        client = _make_async_client()
        msgs = [HumanMessage(content=f"m{i}") for i in range(MAX_MESSAGES_PER_CALL)]
        await persist_messages(client, "thread-1", msgs)
        assert client.thread.add_messages.await_count == 1


class TestPersistMessagesSync:
    def test_persists(self) -> None:
        client = _make_sync_client()
        persist_messages_sync(client, "thread-1", [HumanMessage(content="hi")])
        client.thread.add_messages.assert_called_once()

    def test_return_context(self) -> None:
        client = _make_sync_client(context="Fact.")
        ctx = persist_messages_sync(
            client, "thread-1", [HumanMessage(content="hi")], return_context=True
        )
        assert ctx == "Fact."

    def test_zep_failure_returns_none(self) -> None:
        client = MagicMock()
        client.thread = MagicMock()
        client.thread.add_messages = MagicMock(side_effect=RuntimeError("down"))
        assert persist_messages_sync(client, "thread-1", [HumanMessage(content="hi")]) is None

    def test_chunks_over_thirty_messages(self) -> None:
        client = _make_sync_client()
        msgs = [HumanMessage(content=f"m{i}") for i in range(65)]
        persist_messages_sync(client, "thread-1", msgs)
        assert client.thread.add_messages.call_count == 3
        sent = [c.kwargs["messages"] for c in client.thread.add_messages.call_args_list]
        assert [len(s) for s in sent] == [30, 30, 5]
        assert all(len(s) <= MAX_MESSAGES_PER_CALL for s in sent)
