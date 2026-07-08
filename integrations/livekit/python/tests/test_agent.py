"""
Tests for ``ZepUserAgent`` / ``ZepGraphAgent``: lazy resource creation,
``on_created``, ``context_builder`` / ``GraphContextBuilder``,
``context_template``, and truncation.

Agents are constructed directly (no LiveKit session/room) -- the same pattern
``tests/test_integration.py`` uses to drive ``on_user_turn_completed``
directly. ``self.session`` is unreachable until the agent is attached to a
running session, so ``context_input.session`` is expected to be ``None`` in
these tests (mirroring the ``hasattr(self, "session")`` guard already used by
``_setup_session_handlers``).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from livekit.agents.llm.chat_context import ChatContext
from zep_cloud.client import AsyncZep

from zep_livekit import ZepGraphAgent, ZepUserAgent
from zep_livekit.agent import DEFAULT_CONTEXT_TEMPLATE, ContextInput, GraphContextInput
from zep_livekit.exceptions import AgentConfigurationError
from zep_livekit.limits import GRAPH_MAX_CHARS, MESSAGE_CONTENT_MAX

INSTRUCTIONS = "You are a helpful assistant."


def make_mock_client() -> MagicMock:
    client = MagicMock(spec=AsyncZep)
    client.user = MagicMock()
    client.user.add = AsyncMock()
    client.thread = MagicMock()
    client.thread.create = AsyncMock()
    client.thread.add_messages = AsyncMock()
    client.graph = MagicMock()
    client.graph.add = AsyncMock()
    client.graph.search = AsyncMock()
    return client


def add_messages_response(context: str | None) -> MagicMock:
    resp = MagicMock()
    resp.context = context
    return resp


def make_user_agent(client: MagicMock | None = None, **kwargs: Any) -> ZepUserAgent:
    params: dict[str, Any] = {
        "zep_client": client or make_mock_client(),
        "user_id": "user-1",
        "thread_id": "thread-1",
        "instructions": INSTRUCTIONS,
    }
    params.update(kwargs)
    return ZepUserAgent(**params)


def make_graph_agent(client: MagicMock | None = None, **kwargs: Any) -> ZepGraphAgent:
    params: dict[str, Any] = {
        "zep_client": client or make_mock_client(),
        "graph_id": "graph-1",
        "instructions": INSTRUCTIONS,
    }
    params.update(kwargs)
    return ZepGraphAgent(**params)


class _UserMsg:
    def __init__(self, text: str) -> None:
        self.text_content = text


class _ConversationItem:
    """Minimal stand-in for a LiveKit conversation item (only ``name`` is read)."""

    name = None


async def user_turn(agent: ZepUserAgent | ZepGraphAgent, text: str) -> ChatContext:
    turn_ctx = ChatContext.empty()
    await agent.on_user_turn_completed(turn_ctx, _UserMsg(text))
    return turn_ctx


def system_messages(turn_ctx: ChatContext) -> list[str]:
    return [
        "".join(item.content) if isinstance(item.content, list) else str(item.content)
        for item in turn_ctx.items
        if getattr(item, "role", None) == "system"
    ]


# ---------------------------------------------------------------------------
# Ensure-resources / on_created (constraint 3)
# ---------------------------------------------------------------------------


class TestEnsureResources:
    @pytest.mark.asyncio
    async def test_ensure_resources_fires_hook_on_create(self) -> None:
        client = make_mock_client()
        hook = AsyncMock()
        agent = make_user_agent(client, on_created=hook, first_name="Jane")

        ready = await agent._ensure_resources()

        assert ready is True
        hook.assert_called_once_with(client, "user-1")
        client.thread.create.assert_called_once_with(thread_id="thread-1", user_id="user-1")

    @pytest.mark.asyncio
    async def test_ensure_resources_conflict_no_hook(self) -> None:
        """User already exists -> hook must not fire, but thread creation still runs."""
        client = make_mock_client()
        client.user.add.side_effect = Exception("already exists")
        hook = AsyncMock()
        agent = make_user_agent(client, on_created=hook)

        ready = await agent._ensure_resources()

        assert ready is True
        hook.assert_not_called()
        client.thread.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_ensure_resources_swallows_errors(self) -> None:
        """A genuine failure (or hook failure) is logged and returns False,
        never raised into the voice session."""
        client = make_mock_client()
        client.user.add.side_effect = RuntimeError("boom")
        agent = make_user_agent(client)

        ready = await agent._ensure_resources()

        assert ready is False

    @pytest.mark.asyncio
    async def test_ensure_resources_swallows_hook_errors(self) -> None:
        client = make_mock_client()

        async def failing_hook(_client: Any, _user_id: str) -> None:
            raise RuntimeError("hook boom")

        agent = make_user_agent(client, on_created=failing_hook)

        ready = await agent._ensure_resources()

        assert ready is False

    @pytest.mark.asyncio
    async def test_ensure_resources_cached_after_success(self) -> None:
        client = make_mock_client()
        agent = make_user_agent(client)

        assert await agent._ensure_resources() is True
        assert await agent._ensure_resources() is True

        client.user.add.assert_called_once()
        client.thread.create.assert_called_once()

    def test_user_agent_accepts_new_params(self) -> None:
        """Constructor accepts the new keyword-only params without error."""
        agent = make_user_agent(
            first_name="Jane",
            last_name="Smith",
            email="jane@example.com",
            on_created=AsyncMock(),
            context_builder=AsyncMock(),
            context_template="Custom: {context}",
        )
        assert agent._first_name == "Jane"
        assert agent._context_template == "Custom: {context}"

    @pytest.mark.asyncio
    async def test_turn_skipped_when_resources_not_ready(self) -> None:
        """on_user_turn_completed must not attempt persistence when
        _ensure_resources fails."""
        client = make_mock_client()
        client.user.add.side_effect = RuntimeError("boom")
        agent = make_user_agent(client)

        await user_turn(agent, "hello")

        client.thread.add_messages.assert_not_called()


# ---------------------------------------------------------------------------
# ContextBuilder + default-path fold (constraints 4-5)
# ---------------------------------------------------------------------------


class TestUserAgentContextBuilder:
    @pytest.mark.asyncio
    async def test_user_agent_context_builder(self) -> None:
        """add_messages called WITHOUT return_context; builder receives a
        populated ContextInput."""
        client = make_mock_client()
        received: list[ContextInput] = []

        async def builder(ctx: ContextInput) -> str | None:
            received.append(ctx)
            return "Built context"

        agent = make_user_agent(client, context_builder=builder)

        turn_ctx = await user_turn(agent, "What's up?")

        client.thread.add_messages.assert_called_once()
        call_kwargs = client.thread.add_messages.call_args.kwargs
        assert "return_context" not in call_kwargs

        assert len(received) == 1
        built = received[0]
        assert built.zep is client
        assert built.user_id == "user-1"
        assert built.thread_id == "thread-1"
        assert built.user_message == "What's up?"

        messages = system_messages(turn_ctx)
        assert any("Built context" in m for m in messages)

    @pytest.mark.asyncio
    async def test_builder_failure_isolated_from_persist(self) -> None:
        """Builder raises -> persistence still completes, no injection."""
        client = make_mock_client()

        async def failing_builder(ctx: ContextInput) -> str | None:
            raise RuntimeError("builder boom")

        agent = make_user_agent(client, context_builder=failing_builder)

        turn_ctx = await user_turn(agent, "hello")

        client.thread.add_messages.assert_called_once()
        assert system_messages(turn_ctx) == []

    @pytest.mark.asyncio
    async def test_persist_failure_still_injects_builder_result(self) -> None:
        """Persist raises -> a successful builder result is still injected."""
        client = make_mock_client()
        client.thread.add_messages.side_effect = RuntimeError("persist boom")

        async def builder(ctx: ContextInput) -> str | None:
            return "Still injected"

        agent = make_user_agent(client, context_builder=builder)

        turn_ctx = await user_turn(agent, "hello")

        messages = system_messages(turn_ctx)
        assert any("Still injected" in m for m in messages)

    @pytest.mark.asyncio
    async def test_default_path_single_roundtrip(self) -> None:
        """Default path: add_messages(return_context=True) once; no separate
        get_user_context call. context_mode has no add_messages-equivalent,
        so it is not forwarded (see agent.py's on_user_turn_completed note)."""
        client = make_mock_client()
        client.thread.add_messages.return_value = add_messages_response("Some context")
        agent = make_user_agent(client)

        turn_ctx = await user_turn(agent, "hello")

        client.thread.add_messages.assert_called_once()
        call_kwargs = client.thread.add_messages.call_args.kwargs
        assert call_kwargs["return_context"] is True
        assert (
            not hasattr(client.thread, "get_user_context")
            or not client.thread.get_user_context.called
        )

        messages = system_messages(turn_ctx)
        assert any("Some context" in m for m in messages)


# ---------------------------------------------------------------------------
# GraphContextBuilder + template (constraints 6-7)
# ---------------------------------------------------------------------------


class TestGraphAgentContextBuilder:
    @pytest.mark.asyncio
    async def test_graph_agent_context_builder(self) -> None:
        client = make_mock_client()
        received: list[GraphContextInput] = []

        async def builder(ctx: GraphContextInput) -> str | None:
            received.append(ctx)
            return "Graph builder context"

        agent = make_graph_agent(client, context_builder=builder)

        turn_ctx = await user_turn(agent, "hello graph")

        assert len(received) == 1
        built = received[0]
        assert built.zep is client
        assert built.graph_id == "graph-1"
        assert built.user_message == "hello graph"

        # Default hybrid search must not run when a builder is set.
        client.graph.search.assert_not_called()

        messages = system_messages(turn_ctx)
        assert any("Graph builder context" in m for m in messages)

    @pytest.mark.asyncio
    async def test_graph_agent_builder_failure_isolated(self) -> None:
        client = make_mock_client()

        async def failing_builder(ctx: GraphContextInput) -> str | None:
            raise RuntimeError("boom")

        agent = make_graph_agent(client, context_builder=failing_builder)

        turn_ctx = await user_turn(agent, "hello")

        # Message persistence to the graph still happens.
        client.graph.add.assert_called_once()
        assert system_messages(turn_ctx) == []

    def test_graph_agent_rejects_on_created(self) -> None:
        with pytest.raises(TypeError):
            make_graph_agent(on_created=AsyncMock())

    def test_context_template_override(self) -> None:
        agent = make_user_agent(context_template="Wrapped: {context}")
        assert agent._context_template == "Wrapped: {context}"

    def test_default_template_is_canonical(self) -> None:
        assert DEFAULT_CONTEXT_TEMPLATE.startswith(
            "The following context is retrieved from Zep, the agent's long-term memory."
        )
        assert "<ZEP_CONTEXT>" in DEFAULT_CONTEXT_TEMPLATE
        assert "{context}" in DEFAULT_CONTEXT_TEMPLATE

    @pytest.mark.asyncio
    async def test_template_rendered_via_replace_not_format(self) -> None:
        """A template/context containing brace or percent characters must not
        raise (rules out str.format / % formatting)."""
        client = make_mock_client()
        client.thread.add_messages.return_value = add_messages_response("100% {raw} facts")
        agent = make_user_agent(client, context_template="Ctx: {context} -- {literal_braces}")

        turn_ctx = await user_turn(agent, "hello")

        messages = system_messages(turn_ctx)
        assert any("100% {raw} facts" in m for m in messages)
        assert any("{literal_braces}" in m for m in messages)


# ---------------------------------------------------------------------------
# Truncation (constraint 9)
# ---------------------------------------------------------------------------


class TestTruncation:
    @pytest.mark.asyncio
    async def test_user_turn_truncates_oversize(self) -> None:
        client = make_mock_client()
        client.thread.add_messages.return_value = add_messages_response(None)
        agent = make_user_agent(client)

        oversize = "x" * (MESSAGE_CONTENT_MAX + 500)
        await user_turn(agent, oversize)

        sent_message = client.thread.add_messages.call_args.kwargs["messages"][0]
        assert len(sent_message.content) <= MESSAGE_CONTENT_MAX

    @pytest.mark.asyncio
    async def test_assistant_message_truncates_oversize(self) -> None:
        client = make_mock_client()
        agent = make_user_agent(client)

        oversize = "y" * (MESSAGE_CONTENT_MAX + 500)
        await agent._store_assistant_message(oversize, _ConversationItem())

        sent_message = client.thread.add_messages.call_args.kwargs["messages"][0]
        assert len(sent_message.content) <= MESSAGE_CONTENT_MAX

    @pytest.mark.asyncio
    async def test_graph_add_truncates_oversize(self) -> None:
        client = make_mock_client()
        client.graph.search.return_value = MagicMock(edges=[], nodes=[], episodes=[])
        agent = make_graph_agent(client)

        oversize = "z" * (GRAPH_MAX_CHARS + 500)
        await user_turn(agent, oversize)

        sent_data = client.graph.add.call_args.kwargs["data"]
        assert len(sent_data) <= GRAPH_MAX_CHARS

    @pytest.mark.asyncio
    async def test_graph_assistant_add_truncates_oversize(self) -> None:
        client = make_mock_client()
        agent = make_graph_agent(client)

        oversize = "w" * (GRAPH_MAX_CHARS + 500)
        await agent._store_assistant_message(oversize, _ConversationItem())

        sent_data = client.graph.add.call_args.kwargs["data"]
        assert len(sent_data) <= GRAPH_MAX_CHARS


# ---------------------------------------------------------------------------
# Basic construction guards (unchanged behavior)
# ---------------------------------------------------------------------------


class TestConstructionGuards:
    def test_user_agent_rejects_empty_user_id(self) -> None:
        with pytest.raises(AgentConfigurationError):
            make_user_agent(user_id="")

    def test_graph_agent_rejects_empty_graph_id(self) -> None:
        with pytest.raises(AgentConfigurationError):
            make_graph_agent(graph_id="")
