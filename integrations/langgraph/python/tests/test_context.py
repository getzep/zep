"""
Tests for the context-injection helpers (zep_langgraph.context).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import SystemMessage

from zep_langgraph.context import (
    build_system_message,
    build_system_message_sync,
    format_context_block,
    get_zep_context,
    get_zep_context_sync,
)


def _make_async_client(context: str | None) -> MagicMock:
    client = MagicMock()
    client.thread = MagicMock()
    response = MagicMock()
    response.context = context
    client.thread.get_user_context = AsyncMock(return_value=response)
    return client


def _make_sync_client(context: str | None) -> MagicMock:
    client = MagicMock()
    client.thread = MagicMock()
    response = MagicMock()
    response.context = context
    client.thread.get_user_context = MagicMock(return_value=response)
    return client


class TestGetZepContext:
    @pytest.mark.asyncio
    async def test_returns_context_string(self) -> None:
        client = _make_async_client("User likes blue.")
        result = await get_zep_context(client, "thread-1")
        assert result == "User likes blue."
        client.thread.get_user_context.assert_awaited_once_with("thread-1", template_id=None)

    @pytest.mark.asyncio
    async def test_passes_template_id(self) -> None:
        client = _make_async_client("ctx")
        await get_zep_context(client, "thread-1", template_id="tpl-9")
        client.thread.get_user_context.assert_awaited_once_with("thread-1", template_id="tpl-9")

    @pytest.mark.asyncio
    async def test_returns_none_when_empty(self) -> None:
        client = _make_async_client("   ")
        assert await get_zep_context(client, "thread-1") is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_context(self) -> None:
        client = _make_async_client(None)
        assert await get_zep_context(client, "thread-1") is None

    @pytest.mark.asyncio
    async def test_zep_failure_returns_none_not_raise(self) -> None:
        client = MagicMock()
        client.thread = MagicMock()
        client.thread.get_user_context = AsyncMock(side_effect=RuntimeError("boom"))
        result = await get_zep_context(client, "thread-1")
        assert result is None


class TestGetZepContextSync:
    def test_returns_context_string(self) -> None:
        client = _make_sync_client("Known fact.")
        assert get_zep_context_sync(client, "thread-1") == "Known fact."

    def test_zep_failure_returns_none(self) -> None:
        client = MagicMock()
        client.thread = MagicMock()
        client.thread.get_user_context = MagicMock(side_effect=RuntimeError("boom"))
        assert get_zep_context_sync(client, "thread-1") is None


class TestFormatContextBlock:
    def test_combines_base_and_context(self) -> None:
        out = format_context_block("FACT", base_instructions="You are helpful.")
        assert "You are helpful." in out
        assert "FACT" in out
        assert "<MEMORY>" in out

    def test_base_only_when_no_context(self) -> None:
        out = format_context_block(None, base_instructions="Be nice.")
        assert out == "Be nice."
        assert "<MEMORY>" not in out

    def test_context_only_when_no_base(self) -> None:
        out = format_context_block("FACT")
        assert "FACT" in out
        assert "<MEMORY>" in out

    def test_empty_when_nothing(self) -> None:
        assert format_context_block(None) == ""

    def test_custom_template(self) -> None:
        out = format_context_block("DATA", template="MEM: {context}")
        assert out == "MEM: DATA"


class TestBuildSystemMessage:
    @pytest.mark.asyncio
    async def test_returns_system_message_with_context(self) -> None:
        client = _make_async_client("User is a pilot.")
        msg = await build_system_message(
            client, "thread-1", base_instructions="You are an assistant."
        )
        assert isinstance(msg, SystemMessage)
        assert "You are an assistant." in msg.content
        assert "User is a pilot." in msg.content

    @pytest.mark.asyncio
    async def test_base_only_when_zep_empty(self) -> None:
        client = _make_async_client(None)
        msg = await build_system_message(client, "thread-1", base_instructions="Base.")
        assert isinstance(msg, SystemMessage)
        assert msg.content == "Base."

    @pytest.mark.asyncio
    async def test_zep_failure_does_not_crash(self) -> None:
        client = MagicMock()
        client.thread = MagicMock()
        client.thread.get_user_context = AsyncMock(side_effect=RuntimeError("down"))
        msg = await build_system_message(client, "thread-1", base_instructions="Base.")
        assert msg.content == "Base."

    def test_sync_variant(self) -> None:
        client = _make_sync_client("Fact one.")
        msg = build_system_message_sync(client, "thread-1", base_instructions="Base.")
        assert isinstance(msg, SystemMessage)
        assert "Fact one." in msg.content
