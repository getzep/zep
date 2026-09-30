"""
Tests for ``ZepUserAgent`` / ``ZepGraphAgent``: ``context_builder`` /
``GraphContextBuilder``, ``context_template``, and truncation.

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


USER_UUID = "11111111-1111-1111-1111-111111111111"
THREAD_UUID = "22222222-2222-2222-2222-222222222222"
GRAPH_UUID = "33333333-3333-3333-3333-333333333333"


def make_mock_client() -> MagicMock:
    client = MagicMock(spec=AsyncZep)
    client.user = MagicMock()
    client.user.create = AsyncMock()
    client.thread = MagicMock()
    client.thread.create = AsyncMock()
    client.thread.add_messages = AsyncMock()
    client.graph = MagicMock()
    client.graph.episode = MagicMock()
    client.graph.episode.add = AsyncMock()
    client.graph.get_context = AsyncMock(return_value=MagicMock(context=None))
    return client


def add_messages_response(context: str | None) -> MagicMock:
    resp = MagicMock()
    resp.context = context
    return resp


def make_user_agent(client: MagicMock | None = None, **kwargs: Any) -> ZepUserAgent:
    params: dict[str, Any] = {
        "zep_client": client or make_mock_client(),
        "user_uuid": USER_UUID,
        "thread_uuid": THREAD_UUID,
        "instructions": INSTRUCTIONS,
    }
    params.update(kwargs)
    return ZepUserAgent(**params)


def make_graph_agent(client: MagicMock | None = None, **kwargs: Any) -> ZepGraphAgent:
    params: dict[str, Any] = {
        "zep_client": client or make_mock_client(),
        "graph_uuid": GRAPH_UUID,
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
# UUID addressing
# ---------------------------------------------------------------------------


class TestUuidAddressing:
    @pytest.mark.asyncio
    async def test_thread_uuid_is_the_address(self) -> None:
        """The thread UUID is the first positional argument of add_messages,
        and the agent does not look a resource up at run time."""
        client = make_mock_client()
        client.thread.add_messages.return_value = add_messages_response(None)
        agent = make_user_agent(client)

        await user_turn(agent, "hello")

        assert client.thread.add_messages.call_args.args[0] == THREAD_UUID
        client.user.create.assert_not_called()
        client.thread.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_graph_uuid_is_the_address(self) -> None:
        client = make_mock_client()
        agent = make_graph_agent(client)

        await user_turn(agent, "hello graph")

        assert client.graph.episode.add.call_args.args[0] == GRAPH_UUID
        assert client.graph.get_context.call_args.args[0] == GRAPH_UUID


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
        assert built.user_uuid == USER_UUID
        assert built.thread_uuid == THREAD_UUID
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
        get_context call."""
        client = make_mock_client()
        client.thread.add_messages.return_value = add_messages_response("Some context")
        agent = make_user_agent(client)

        turn_ctx = await user_turn(agent, "hello")

        client.thread.add_messages.assert_called_once()
        call_kwargs = client.thread.add_messages.call_args.kwargs
        assert call_kwargs["return_context"] is True
        assert not hasattr(client.thread, "get_context") or not client.thread.get_context.called

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
        assert built.graph_uuid == GRAPH_UUID
        assert built.user_message == "hello graph"

        # Default context retrieval must not run when a builder is set.
        client.graph.get_context.assert_not_called()

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
        client.graph.episode.add.assert_called_once()
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
        agent = make_graph_agent(client)

        oversize = "z" * (GRAPH_MAX_CHARS + 500)
        await user_turn(agent, oversize)

        sent_data = client.graph.episode.add.call_args.kwargs["data"]
        assert len(sent_data) <= GRAPH_MAX_CHARS

    @pytest.mark.asyncio
    async def test_graph_assistant_add_truncates_oversize(self) -> None:
        client = make_mock_client()
        agent = make_graph_agent(client)

        oversize = "w" * (GRAPH_MAX_CHARS + 500)
        await agent._store_assistant_message(oversize, _ConversationItem())

        sent_data = client.graph.episode.add.call_args.kwargs["data"]
        assert len(sent_data) <= GRAPH_MAX_CHARS


# ---------------------------------------------------------------------------
# Basic construction guards (unchanged behavior)
# ---------------------------------------------------------------------------


class TestConstructionGuards:
    def test_user_agent_rejects_empty_user_uuid(self) -> None:
        with pytest.raises(AgentConfigurationError):
            make_user_agent(user_uuid="")

    def test_user_agent_rejects_empty_thread_uuid(self) -> None:
        with pytest.raises(AgentConfigurationError):
            make_user_agent(thread_uuid="")

    def test_graph_agent_rejects_empty_graph_uuid(self) -> None:
        with pytest.raises(AgentConfigurationError):
            make_graph_agent(graph_uuid="")
