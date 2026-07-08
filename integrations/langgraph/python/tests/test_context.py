"""
Tests for the context-injection helpers (zep_langgraph.context).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import SystemMessage

from zep_langgraph.context import (
    DEFAULT_CONTEXT_TEMPLATE,
    ContextInput,
    build_system_message,
    build_system_message_sync,
    format_context_block,
    get_zep_context,
    get_zep_context_sync,
)

#: The canonical cross-language template text (Python/Go/TypeScript zep-adk),
#: reproduced here so a byte-level diff catches accidental drift.
_CANONICAL_TEMPLATE = (
    "The following context is retrieved from Zep, the agent's long-term memory. "
    "It contains relevant facts, entities, and prior knowledge about the user. "
    "Use it to inform your responses.\n\n"
    "<ZEP_CONTEXT>\n"
    "{context}\n"
    "</ZEP_CONTEXT>"
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
        assert "<ZEP_CONTEXT>" in out

    def test_base_only_when_no_context(self) -> None:
        out = format_context_block(None, base_instructions="Be nice.")
        assert out == "Be nice."
        assert "<ZEP_CONTEXT>" not in out

    def test_context_only_when_no_base(self) -> None:
        out = format_context_block("FACT")
        assert "FACT" in out
        assert "<ZEP_CONTEXT>" in out

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


class TestTemplateCanonical:
    def test_template_canonical_default(self) -> None:
        assert DEFAULT_CONTEXT_TEMPLATE == _CANONICAL_TEMPLATE

    def test_template_rendered_via_replace_not_format(self) -> None:
        # A context string containing literal `{`/`}`/`%` must survive
        # unescaped -- str.format would raise or mangle it.
        out = format_context_block("100% {escaped} facts", template="X: {context}")
        assert out == "X: 100% {escaped} facts"


class TestContextBuilder:
    @pytest.mark.asyncio
    async def test_get_zep_context_with_context_builder(self) -> None:
        client = _make_async_client("ignored")
        seen: list[ContextInput] = []

        async def builder(ctx: ContextInput) -> str | None:
            seen.append(ctx)
            return "built context"

        result = await get_zep_context(
            client,
            "thread-1",
            context_builder=builder,
            user_id="user-1",
            user_message="hello there",
        )

        assert result == "built context"
        client.thread.get_user_context.assert_not_called()
        assert len(seen) == 1
        assert seen[0].zep is client
        assert seen[0].user_id == "user-1"
        assert seen[0].thread_id == "thread-1"
        assert seen[0].user_message == "hello there"

    @pytest.mark.asyncio
    async def test_builder_error_degrades_to_none(self) -> None:
        client = _make_async_client("ignored")

        async def bad_builder(ctx: ContextInput) -> str | None:
            raise RuntimeError("builder boom")

        result = await get_zep_context(client, "thread-1", context_builder=bad_builder)
        assert result is None
        client.thread.get_user_context.assert_not_called()

    @pytest.mark.asyncio
    async def test_build_system_message_builder_none_falls_back_to_base(self) -> None:
        client = _make_async_client("ignored")

        async def none_builder(ctx: ContextInput) -> str | None:
            return None

        msg = await build_system_message(
            client,
            "thread-1",
            base_instructions="Base only.",
            context_builder=none_builder,
        )
        assert msg.content == "Base only."
        client.thread.get_user_context.assert_not_called()

    @pytest.mark.asyncio
    async def test_build_system_message_uses_builder_result(self) -> None:
        client = _make_async_client("ignored")

        async def builder(ctx: ContextInput) -> str | None:
            return "custom fact"

        msg = await build_system_message(
            client, "thread-1", base_instructions="Base.", context_builder=builder
        )
        assert "custom fact" in msg.content
        assert "Base." in msg.content


class TestContextBuilderSync:
    def test_get_zep_context_sync_with_context_builder(self) -> None:
        client = _make_sync_client("ignored")
        seen: list[ContextInput] = []

        def builder(ctx: ContextInput) -> str | None:
            seen.append(ctx)
            return "sync built context"

        result = get_zep_context_sync(
            client,
            "thread-1",
            context_builder=builder,
            user_id="user-2",
            user_message="hi",
        )

        assert result == "sync built context"
        client.thread.get_user_context.assert_not_called()
        assert seen[0].user_id == "user-2"

    def test_sync_builder_error_degrades_to_none(self) -> None:
        client = _make_sync_client("ignored")

        def bad_builder(ctx: ContextInput) -> str | None:
            raise RuntimeError("boom")

        result = get_zep_context_sync(client, "thread-1", context_builder=bad_builder)
        assert result is None

    def test_build_system_message_sync_builder_none_falls_back_to_base(self) -> None:
        client = _make_sync_client("ignored")

        def none_builder(ctx: ContextInput) -> str | None:
            return None

        msg = build_system_message_sync(
            client, "thread-1", base_instructions="Base only.", context_builder=none_builder
        )
        assert msg.content == "Base only."
        client.thread.get_user_context.assert_not_called()
