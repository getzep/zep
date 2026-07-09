"""
Tests for the prebuilt pre_model_hook wrapper (zep_langgraph.hooks).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from zep_langgraph.hooks import create_zep_pre_model_hook


def _make_async_client(context: str | None) -> MagicMock:
    client = MagicMock()
    client.thread = MagicMock()
    response = MagicMock()
    response.context = context
    client.thread.get_user_context = AsyncMock(return_value=response)
    return client


class TestCreateZepPreModelHook:
    @pytest.mark.asyncio
    async def test_pre_model_hook_injects_context(self) -> None:
        client = _make_async_client("User likes hiking.")
        hook = create_zep_pre_model_hook(
            client, user_id="user-1", thread_id="thread-1", base_instructions="Be helpful."
        )

        state = {"messages": [HumanMessage(content="What do I like?")]}
        result = await hook(state)

        assert "llm_input_messages" in result
        assert "messages" not in result
        llm_messages = result["llm_input_messages"]
        assert isinstance(llm_messages[0], SystemMessage)
        assert "User likes hiking." in llm_messages[0].content
        assert "Be helpful." in llm_messages[0].content
        # Original human message preserved after the injected system message.
        assert llm_messages[1] is state["messages"][0]

    @pytest.mark.asyncio
    async def test_pre_model_hook_zep_failure_passthrough(self) -> None:
        client = MagicMock()
        client.thread = MagicMock()
        client.thread.get_user_context = AsyncMock(side_effect=RuntimeError("down"))
        hook = create_zep_pre_model_hook(
            client, user_id="user-1", thread_id="thread-1", base_instructions="Be helpful."
        )

        state = {"messages": [HumanMessage(content="hi")]}
        result = await hook(state)

        llm_messages = result["llm_input_messages"]
        assert isinstance(llm_messages[0], SystemMessage)
        assert llm_messages[0].content == "Be helpful."
        assert llm_messages[1] is state["messages"][0]

    @pytest.mark.asyncio
    async def test_pre_model_hook_does_not_mutate_messages_key(self) -> None:
        # Per the create_react_agent pre_model_hook contract, returning
        # `llm_input_messages` must NOT also update the `messages` state key.
        client = _make_async_client("fact")
        hook = create_zep_pre_model_hook(client, user_id="u", thread_id="t")
        state = {"messages": [HumanMessage(content="hi")]}
        result = await hook(state)
        assert "messages" not in result

    @pytest.mark.asyncio
    async def test_pre_model_hook_uses_context_builder(self) -> None:
        client = _make_async_client("ignored")

        async def builder(ctx):
            return f"built for {ctx.user_message}"

        hook = create_zep_pre_model_hook(
            client, user_id="user-1", thread_id="thread-1", context_builder=builder
        )
        state = {"messages": [HumanMessage(content="hello")]}
        result = await hook(state)
        assert "built for hello" in result["llm_input_messages"][0].content
        client.thread.get_user_context.assert_not_called()

    @pytest.mark.asyncio
    async def test_pre_model_hook_no_human_message_uses_empty_query(self) -> None:
        client = _make_async_client("fact")
        hook = create_zep_pre_model_hook(client, user_id="u", thread_id="t")
        state = {"messages": [AIMessage(content="assistant only")]}
        result = await hook(state)
        assert isinstance(result["llm_input_messages"][0], SystemMessage)

    @pytest.mark.asyncio
    async def test_pre_model_hook_no_context_no_instructions_passthrough(self) -> None:
        # With no Zep context and no base_instructions the formatted block is
        # empty; the hook must NOT prepend an empty SystemMessage (providers
        # such as Anthropic reject empty message content).
        client = _make_async_client(None)
        hook = create_zep_pre_model_hook(client, user_id="u", thread_id="t")

        state = {"messages": [HumanMessage(content="hi")]}
        result = await hook(state)

        assert result == {"llm_input_messages": state["messages"]}
        assert not any(isinstance(m, SystemMessage) for m in result["llm_input_messages"])

    @pytest.mark.asyncio
    async def test_pre_model_hook_custom_template(self) -> None:
        client = _make_async_client("fact")
        hook = create_zep_pre_model_hook(
            client, user_id="u", thread_id="t", template="MEM: {context}"
        )
        state = {"messages": [HumanMessage(content="hi")]}
        result = await hook(state)
        assert result["llm_input_messages"][0].content == "MEM: fact"
