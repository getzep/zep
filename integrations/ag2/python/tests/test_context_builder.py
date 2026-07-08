"""
Tests for ``ContextInput`` / ``context_builder`` / ``process_user_message`` on
``ZepMemoryManager``.

AG2 has no native memory interface -- there is no single framework-owned
"turn" hook the way ADK/pydantic-ai/ms-agent-framework have. ``process_user_message``
is the manager's own per-turn seam: when a ``context_builder`` is set, it
persists the user message and builds context concurrently
(``asyncio.gather``, per-side isolation); when unset, it does a single
``add_messages(..., return_context=True)`` round-trip.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from zep_cloud.client import AsyncZep

from zep_ag2 import ZepAG2ConfigError, ZepMemoryManager
from zep_ag2.memory import ContextInput


def _make_mock_client() -> MagicMock:
    client = MagicMock(spec=AsyncZep)
    client.user = MagicMock()
    client.user.add = AsyncMock()
    client.thread = MagicMock()
    client.thread.create = AsyncMock()
    client.thread.add_messages = AsyncMock(return_value=MagicMock(context="default context"))
    client.thread.get_user_context = AsyncMock(return_value=MagicMock(context="default context"))
    return client


class TestProcessUserMessageWithBuilder:
    @pytest.mark.asyncio
    async def test_process_user_message_with_context_builder_gathers(self) -> None:
        """When context_builder is set, add_messages is called WITHOUT
        return_context, and the builder receives a ContextInput including the
        agent field."""
        client = _make_mock_client()
        received: list[ContextInput] = []

        async def builder(ctx: ContextInput) -> str | None:
            received.append(ctx)
            return "Built context block"

        manager = ZepMemoryManager(client, user_id="u1", session_id="s1", context_builder=builder)
        fake_agent = object()

        result = await manager.process_user_message("Hello there", agent=fake_agent)

        client.thread.add_messages.assert_called_once()
        call_kwargs = client.thread.add_messages.call_args.kwargs
        assert call_kwargs.get("return_context") is not True

        assert len(received) == 1
        built = received[0]
        assert built.zep is client
        assert built.user_id == "u1"
        assert built.thread_id == "s1"
        assert built.user_message == "Hello there"
        assert built.agent is fake_agent

        assert result == "Built context block"

    @pytest.mark.asyncio
    async def test_process_user_message_default_single_roundtrip(self) -> None:
        """Without a builder, a single add_messages(return_context=True) call
        both persists and retrieves context."""
        client = _make_mock_client()
        manager = ZepMemoryManager(client, user_id="u1", session_id="s1")

        result = await manager.process_user_message("Hi")

        client.thread.add_messages.assert_called_once()
        call_kwargs = client.thread.add_messages.call_args.kwargs
        assert call_kwargs.get("return_context") is True
        assert result == "default context"

    @pytest.mark.asyncio
    async def test_builder_failure_isolated_from_persist(self) -> None:
        """A builder exception must not prevent the message from being
        persisted -- persistence completes, context injection is skipped."""
        client = _make_mock_client()

        async def failing_builder(ctx: ContextInput) -> str | None:
            raise RuntimeError("builder boom")

        manager = ZepMemoryManager(
            client, user_id="u1", session_id="s1", context_builder=failing_builder
        )

        result = await manager.process_user_message("Hi")

        client.thread.add_messages.assert_called_once()
        assert result is None

    @pytest.mark.asyncio
    async def test_persist_failure_still_returns_builder_result(self) -> None:
        """A persistence failure must not prevent the builder's context from
        being returned."""
        client = _make_mock_client()
        client.thread.add_messages = AsyncMock(side_effect=Exception("persist boom"))

        async def builder(ctx: ContextInput) -> str | None:
            return "Builder context survives"

        manager = ZepMemoryManager(client, user_id="u1", session_id="s1", context_builder=builder)

        result = await manager.process_user_message("Hi")

        assert result == "Builder context survives"

    @pytest.mark.asyncio
    async def test_process_user_message_requires_session(self) -> None:
        client = _make_mock_client()
        manager = ZepMemoryManager(client, user_id="u1")

        with pytest.raises(ZepAG2ConfigError):
            await manager.process_user_message("Hi")


class TestEnrichSystemMessageUsesBuilder:
    @pytest.mark.asyncio
    async def test_enrich_system_message_uses_builder(self) -> None:
        client = _make_mock_client()
        received: list[ContextInput] = []

        async def builder(ctx: ContextInput) -> str | None:
            received.append(ctx)
            return "Builder retrieval context"

        manager = ZepMemoryManager(client, user_id="u1", session_id="s1", context_builder=builder)
        agent = MagicMock()
        agent.system_message = "You are a helpful assistant."
        agent.update_system_message = MagicMock()

        await manager.enrich_system_message(agent, query="hiking")

        client.thread.get_user_context.assert_not_called()
        assert len(received) == 1
        assert received[0].user_message == "hiking"
        agent.update_system_message.assert_called_once()
        injected = agent.update_system_message.call_args[0][0]
        assert "Builder retrieval context" in injected

    @pytest.mark.asyncio
    async def test_enrich_system_message_builder_none_no_injection(self) -> None:
        client = _make_mock_client()

        async def builder(ctx: ContextInput) -> str | None:
            return None

        manager = ZepMemoryManager(client, user_id="u1", session_id="s1", context_builder=builder)
        agent = MagicMock()
        agent.system_message = "You are a helpful assistant."
        agent.update_system_message = MagicMock()

        await manager.enrich_system_message(agent)

        agent.update_system_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_enrich_system_message_builder_error_degrades(self) -> None:
        client = _make_mock_client()

        async def failing_builder(ctx: ContextInput) -> str | None:
            raise RuntimeError("boom")

        manager = ZepMemoryManager(
            client, user_id="u1", session_id="s1", context_builder=failing_builder
        )
        agent = MagicMock()
        agent.system_message = "You are a helpful assistant."
        agent.update_system_message = MagicMock()

        await manager.enrich_system_message(agent)

        agent.update_system_message.assert_not_called()
