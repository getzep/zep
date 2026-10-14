from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from livekit.agents.llm.chat_context import ChatContext
from zep_cloud.client import AsyncZep

from zep_livekit import ZepGraphAgent, ZepUserAgent
from zep_livekit.agent import (
    DEFAULT_CONTEXT_TEMPLATE,
    ContextInput,
    GraphContextInput,
)
from zep_livekit.exceptions import AgentConfigurationError
from zep_livekit.limits import GRAPH_MAX_CHARS, MESSAGE_CONTENT_MAX


def make_client() -> MagicMock:
    client = MagicMock(spec=AsyncZep)
    client.thread = MagicMock()
    client.thread.add_messages = AsyncMock()
    client.graph = MagicMock()
    client.graph.episode = MagicMock()
    client.graph.episode.add = AsyncMock()
    client.graph.get_context = AsyncMock()
    return client


def make_user_agent(client: MagicMock | None = None, **kwargs: Any) -> ZepUserAgent:
    params = {
        "zep_client": client or make_client(),
        "user_uuid": "user-uuid",
        "thread_uuid": "thread-uuid",
        "instructions": "Be helpful.",
    }
    params.update(kwargs)
    return ZepUserAgent(**params)


def make_graph_agent(client: MagicMock | None = None, **kwargs: Any) -> ZepGraphAgent:
    params = {
        "zep_client": client or make_client(),
        "graph_uuid": "graph-uuid",
        "instructions": "Be helpful.",
    }
    params.update(kwargs)
    return ZepGraphAgent(**params)


class _UserMessage:
    def __init__(self, text: str) -> None:
        self.text_content = text


class _AssistantItem:
    name = None


async def user_turn(agent: ZepUserAgent | ZepGraphAgent, text: str) -> ChatContext:
    context = ChatContext.empty()
    await agent.on_user_turn_completed(context, _UserMessage(text))
    return context


def system_messages(context: ChatContext) -> list[str]:
    return [
        "".join(item.content) if isinstance(item.content, list) else str(item.content)
        for item in context.items
        if getattr(item, "role", None) == "system"
    ]


class TestUserAgent:
    @pytest.mark.asyncio
    async def test_default_turn_persists_and_retrieves_in_one_call(self) -> None:
        client = make_client()
        client.thread.add_messages.return_value = SimpleNamespace(context="Saved context")
        agent = make_user_agent(client, user_message_name="Jane")

        context = await user_turn(agent, "Hello")

        client.thread.add_messages.assert_awaited_once()
        args, kwargs = client.thread.add_messages.call_args
        assert args == ("thread-uuid",)
        assert kwargs["return_context"] is True
        message = kwargs["messages"][0]
        assert message.content == "Hello"
        assert message.role == "user"
        assert message.name == "Jane"
        assert "Saved context" in system_messages(context)[0]

    @pytest.mark.asyncio
    async def test_custom_builder_receives_context_input_and_persists(self) -> None:
        client = make_client()
        received: list[ContextInput] = []

        async def builder(value: ContextInput) -> str:
            received.append(value)
            return "Custom context"

        agent = make_user_agent(client, context_builder=builder)
        context = await user_turn(agent, "Question?")

        client.thread.add_messages.assert_awaited_once()
        assert "return_context" not in client.thread.add_messages.call_args.kwargs
        assert received == [ContextInput(client, "user-uuid", "thread-uuid", "Question?", None)]
        assert "Custom context" in system_messages(context)[0]

    @pytest.mark.asyncio
    async def test_builder_error_does_not_stop_persistence_or_turn(self) -> None:
        client = make_client()

        async def builder(_value: ContextInput) -> str:
            raise RuntimeError("builder failed")

        agent = make_user_agent(client, context_builder=builder)
        context = await user_turn(agent, "Question?")

        client.thread.add_messages.assert_awaited_once()
        assert system_messages(context) == []

    @pytest.mark.asyncio
    async def test_persist_error_does_not_stop_custom_context(self) -> None:
        client = make_client()
        client.thread.add_messages.side_effect = RuntimeError("persist failed")

        async def builder(_value: ContextInput) -> str:
            return "Still available"

        agent = make_user_agent(client, context_builder=builder)
        context = await user_turn(agent, "Question?")

        assert "Still available" in system_messages(context)[0]

    @pytest.mark.asyncio
    async def test_default_persist_error_isolated(self) -> None:
        client = make_client()
        client.thread.add_messages.side_effect = RuntimeError("offline")

        context = await user_turn(make_user_agent(client), "Question?")

        assert system_messages(context) == []

    @pytest.mark.asyncio
    async def test_assistant_message_is_stored_with_name(self) -> None:
        client = make_client()
        agent = make_user_agent(client, assistant_message_name="Zep")

        await agent._store_assistant_message("Answer", _AssistantItem())

        message = client.thread.add_messages.call_args.kwargs["messages"][0]
        assert message.role == "assistant"
        assert message.name == "Zep"
        assert message.content == "Answer"

    @pytest.mark.asyncio
    async def test_assistant_persist_error_isolated(self) -> None:
        client = make_client()
        client.thread.add_messages.side_effect = RuntimeError("offline")

        await make_user_agent(client)._store_assistant_message("Answer", _AssistantItem())

    @pytest.mark.asyncio
    async def test_empty_user_turn_is_ignored(self) -> None:
        client = make_client()

        await user_turn(make_user_agent(client), "  ")

        client.thread.add_messages.assert_not_called()

    @pytest.mark.asyncio
    async def test_user_and_assistant_messages_are_truncated(self) -> None:
        client = make_client()
        client.thread.add_messages.return_value = SimpleNamespace(context=None)
        agent = make_user_agent(client)

        await user_turn(agent, "x" * (MESSAGE_CONTENT_MAX + 100))
        user_message = client.thread.add_messages.call_args.kwargs["messages"][0]
        await agent._store_assistant_message("y" * (MESSAGE_CONTENT_MAX + 100), _AssistantItem())
        assistant_message = client.thread.add_messages.call_args.kwargs["messages"][0]

        assert len(user_message.content) <= MESSAGE_CONTENT_MAX
        assert len(assistant_message.content) <= MESSAGE_CONTENT_MAX


class TestGraphAgent:
    @pytest.mark.asyncio
    async def test_default_turn_stores_episode_and_retrieves_context(self) -> None:
        client = make_client()
        client.graph.get_context.return_value = SimpleNamespace(context="Graph context")
        agent = make_graph_agent(client, max_characters=6000)
        context = await user_turn(agent, "Question?")

        client.graph.episode.add.assert_awaited_once_with(
            "graph-uuid",
            data="Question?",
            type="message",
        )
        client.graph.get_context.assert_awaited_once_with(
            "graph-uuid",
            query="Question?",
            filters=None,
            max_characters=6000,
        )
        assert "Graph context" in system_messages(context)[0]

    @pytest.mark.asyncio
    async def test_graph_template_uuid_is_sent_to_context_retrieval(self) -> None:
        client = make_client()
        client.graph.get_context.return_value = SimpleNamespace(context=None)
        agent = make_graph_agent(client, template_uuid="template-uuid")

        await user_turn(agent, "Question?")

        assert client.graph.get_context.call_args.kwargs["template_uuid"] == "template-uuid"

    @pytest.mark.asyncio
    async def test_graph_context_builder_receives_graph_context_input(self) -> None:
        client = make_client()
        received: list[GraphContextInput] = []

        async def builder(value: GraphContextInput) -> str:
            received.append(value)
            return "Built graph context"

        agent = make_graph_agent(client, context_builder=builder)
        context = await user_turn(agent, "Question?")

        assert received == [GraphContextInput(client, "graph-uuid", "Question?", None)]
        client.graph.get_context.assert_not_called()
        assert "Built graph context" in system_messages(context)[0]

    @pytest.mark.asyncio
    async def test_graph_builder_error_does_not_stop_episode_persistence(self) -> None:
        client = make_client()

        async def builder(_value: GraphContextInput) -> str:
            raise RuntimeError("builder failed")

        context = await user_turn(make_graph_agent(client, context_builder=builder), "Question?")

        client.graph.episode.add.assert_awaited_once()
        assert system_messages(context) == []

    @pytest.mark.asyncio
    async def test_graph_sdk_errors_do_not_escape_turn(self) -> None:
        client = make_client()
        client.graph.episode.add.side_effect = RuntimeError("write failed")
        client.graph.get_context.side_effect = RuntimeError("read failed")

        context = await user_turn(make_graph_agent(client), "Question?")

        assert system_messages(context) == []

    @pytest.mark.asyncio
    async def test_graph_user_and_assistant_episodes_are_truncated(self) -> None:
        client = make_client()
        client.graph.get_context.return_value = SimpleNamespace(context=None)
        agent = make_graph_agent(client)

        await user_turn(agent, "x" * (GRAPH_MAX_CHARS + 100))
        user_data = client.graph.episode.add.call_args.kwargs["data"]
        await agent._store_assistant_message("y" * (GRAPH_MAX_CHARS + 100), _AssistantItem())
        assistant_data = client.graph.episode.add.call_args.kwargs["data"]

        assert len(user_data) <= GRAPH_MAX_CHARS
        assert len(assistant_data) <= GRAPH_MAX_CHARS

    def test_graph_agent_rejects_on_created(self) -> None:
        with pytest.raises(TypeError):
            make_graph_agent(on_created=AsyncMock())


class TestAgentConfiguration:
    def test_uuid_inputs_must_not_be_empty(self) -> None:
        with pytest.raises(AgentConfigurationError, match="user_uuid"):
            make_user_agent(user_uuid="")
        with pytest.raises(AgentConfigurationError, match="thread_uuid"):
            make_user_agent(thread_uuid="")
        with pytest.raises(AgentConfigurationError, match="graph_uuid"):
            make_graph_agent(graph_uuid="")

    def test_custom_template_requires_context_placeholder(self) -> None:
        with pytest.raises(AgentConfigurationError, match="context"):
            make_user_agent(context_template="No placeholder")

    def test_custom_template_uses_plain_string_replacement(self) -> None:
        agent = make_user_agent(context_template="Context: {context}")
        context = agent._context_template.replace("{context}", "{raw} % text")
        assert context == "Context: {raw} % text"

    def test_default_template_contains_context_placeholder(self) -> None:
        assert "{context}" in DEFAULT_CONTEXT_TEMPLATE
        assert "<ZEP_CONTEXT>" in DEFAULT_CONTEXT_TEMPLATE
