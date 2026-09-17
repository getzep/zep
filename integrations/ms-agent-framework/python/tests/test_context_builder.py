"""
Tests for ``ContextInput`` / ``context_builder`` support on ``ZepContextProvider``.

When a custom ``context_builder`` is configured, ``before_run`` persists the
user message via ``thread.add_messages`` WITHOUT ``return_context`` and runs
the builder concurrently via ``asyncio.gather(..., return_exceptions=True)``,
with per-side failure isolation.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from agent_framework import Message
from zep_cloud.client import AsyncZep

from zep_ms_agent_framework import ZepContextProvider
from zep_ms_agent_framework.context_provider import ContextInput

USER_UUID = "11111111-1111-1111-1111-111111111111"
GRAPH_UUID = "22222222-2222-2222-2222-222222222222"
THREAD_UUID = "33333333-3333-3333-3333-333333333333"


def make_mock_client() -> MagicMock:
    """Create a mock AsyncZep client with async thread methods."""
    client = MagicMock(spec=AsyncZep)
    client.thread = MagicMock()
    client.thread.add_messages = AsyncMock()
    return client


def make_context(
    input_messages: list[Message] | None = None,
    response_messages: list[Message] | None = None,
) -> MagicMock:
    """Create a mock SessionContext."""
    ctx = MagicMock()
    ctx.input_messages = input_messages or []
    ctx.extend_instructions = MagicMock()
    if response_messages is None:
        ctx.response = None
    else:
        response = MagicMock()
        response.messages = response_messages
        ctx.response = response
    return ctx


def make_provider(client: MagicMock | None = None, **kwargs: Any) -> ZepContextProvider:
    """Construct a ZepContextProvider with sensible test defaults."""
    params: dict[str, Any] = {
        "zep_client": client or make_mock_client(),
        "user_uuid": USER_UUID,
        "thread_uuid": THREAD_UUID,
        "graph_uuid": GRAPH_UUID,
    }
    params.update(kwargs)
    return ZepContextProvider(**params)


def add_messages_response(context: str | None) -> MagicMock:
    """Build a mock thread.add_messages response carrying a context block."""
    resp = MagicMock()
    resp.context = context
    return resp


async def run_before(provider: ZepContextProvider, context: MagicMock) -> None:
    await provider.before_run(agent=MagicMock(), session=MagicMock(), context=context, state={})


class TestContextBuilderGathering:
    @pytest.mark.asyncio
    async def test_before_run_with_context_builder_gathers(self) -> None:
        """add_messages is called WITHOUT return_context; the builder receives
        a ContextInput with the right fields, including session_context."""
        client = make_mock_client()
        client.thread.add_messages.return_value = add_messages_response(None)
        received: list[ContextInput] = []

        async def builder(ctx: ContextInput) -> str | None:
            received.append(ctx)
            return "Built context"

        provider = make_provider(client, context_builder=builder)
        ctx = make_context(input_messages=[Message("user", ["What's up?"])])

        await run_before(provider, ctx)

        client.thread.add_messages.assert_called_once()
        call = client.thread.add_messages.call_args.kwargs
        assert "return_context" not in call

        assert len(received) == 1
        built = received[0]
        assert built.zep is client
        assert built.user_uuid == USER_UUID
        assert built.thread_uuid == THREAD_UUID
        assert built.graph_uuid == GRAPH_UUID
        assert built.user_message == "What's up?"
        assert built.session_context is ctx

        ctx.extend_instructions.assert_called_once()
        source_id, instruction = ctx.extend_instructions.call_args.args
        assert source_id == "zep"
        assert "Built context" in instruction

    @pytest.mark.asyncio
    async def test_builder_failure_isolated_from_persist(self) -> None:
        """If the builder raises, persistence still completes (the user turn
        is marked persisted) and no context is injected."""
        client = make_mock_client()
        client.thread.add_messages.return_value = add_messages_response(None)

        async def failing_builder(ctx: ContextInput) -> str | None:
            raise RuntimeError("builder boom")

        provider = make_provider(client, context_builder=failing_builder)
        ctx = make_context(input_messages=[Message("user", ["hello"])])

        await run_before(provider, ctx)

        client.thread.add_messages.assert_called_once()
        ctx.extend_instructions.assert_not_called()
        assert provider._user_turn_persisted is True

    @pytest.mark.asyncio
    async def test_persist_failure_still_injects_builder_result(self) -> None:
        """If persistence raises, a successful builder result is still
        injected, but the turn is NOT marked as persisted."""
        client = make_mock_client()
        client.thread.add_messages.side_effect = RuntimeError("persist boom")

        async def builder(ctx: ContextInput) -> str | None:
            return "Still injected"

        provider = make_provider(client, context_builder=builder)
        ctx = make_context(input_messages=[Message("user", ["hello"])])

        await run_before(provider, ctx)

        ctx.extend_instructions.assert_called_once()
        source_id, instruction = ctx.extend_instructions.call_args.args
        assert "Still injected" in instruction
        assert provider._user_turn_persisted is False

    @pytest.mark.asyncio
    async def test_user_turn_persisted_only_on_persist_success(self) -> None:
        """Both sides failing: user turn not persisted, no context injected,
        and the run must not raise."""
        client = make_mock_client()
        client.thread.add_messages.side_effect = RuntimeError("persist boom")

        async def failing_builder(ctx: ContextInput) -> str | None:
            raise RuntimeError("builder boom")

        provider = make_provider(client, context_builder=failing_builder)
        ctx = make_context(input_messages=[Message("user", ["hello"])])

        await run_before(provider, ctx)

        ctx.extend_instructions.assert_not_called()
        assert provider._user_turn_persisted is False

    @pytest.mark.asyncio
    async def test_ignore_roles_passed_through_with_builder(self) -> None:
        client = make_mock_client()
        client.thread.add_messages.return_value = add_messages_response(None)

        async def builder(ctx: ContextInput) -> str | None:
            return None

        provider = make_provider(client, context_builder=builder, ignore_roles=["assistant"])
        ctx = make_context(input_messages=[Message("user", ["hello"])])

        await run_before(provider, ctx)

        call = client.thread.add_messages.call_args.kwargs
        assert call["ignore_roles"] == ["assistant"]
