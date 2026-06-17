"""
End-to-end wiring tests: a real Pydantic AI ``Agent`` driven by ``TestModel``
(no LLM API key needed) with a mocked Zep client.

These prove the integration plugs into Pydantic AI as documented:

* ``capabilities=[ProcessHistory(zep_history_processor)]`` fires and injects
  context, persisting the user turn exactly once even across the multiple model
  requests a tool-calling run makes;
* the ``create_zep_search_tool`` tool registers and is callable with ``ZepDeps``;
* ``persist_run`` stores the assistant reply from ``result.new_messages()``.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic_ai import Agent
from pydantic_ai.capabilities import ProcessHistory
from pydantic_ai.messages import SystemPromptPart
from pydantic_ai.models.test import TestModel

from zep_pydantic_ai import (
    ZepDeps,
    create_zep_search_tool,
    persist_run,
    reset_turn_cache,
    zep_history_processor,
)


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    reset_turn_cache()


def _make_mock_client(context: str | None = "User likes blue.") -> MagicMock:
    client = MagicMock()
    client.user = MagicMock()
    client.user.add = AsyncMock()
    client.thread = MagicMock()
    client.thread.create = AsyncMock()
    response = MagicMock()
    response.context = context
    client.thread.add_messages = AsyncMock(return_value=response)
    search_result = MagicMock()
    edge = MagicMock()
    edge.fact = "User's favourite colour is blue"
    search_result.edges = [edge]
    client.graph.search = AsyncMock(return_value=search_result)
    return client


@pytest.mark.asyncio
async def test_processor_injects_context_into_run() -> None:
    """The history processor runs during agent.run and the model sees the
    injected Zep context (captured via the model's last request)."""
    client = _make_mock_client(context="User likes blue.")
    deps = ZepDeps(client=client, user_id="u", thread_id="t", first_name="Jane")

    agent = Agent(
        TestModel(custom_output_text="ack"),
        deps_type=ZepDeps,
        capabilities=[ProcessHistory(zep_history_processor)],
    )

    result = await agent.run("What's my favourite colour?", deps=deps)

    # The user turn was persisted with return_context once.
    client.thread.add_messages.assert_called_once()
    assert client.thread.add_messages.call_args.kwargs["return_context"] is True

    # The model history that ran includes the injected Zep system context.
    all_messages = result.all_messages()
    system_texts = [
        part.content
        for msg in all_messages
        for part in msg.parts
        if isinstance(part, SystemPromptPart)
    ]
    assert any("User likes blue." in t for t in system_texts)


@pytest.mark.asyncio
async def test_tool_run_persists_user_turn_once() -> None:
    """A tool-calling run invokes the processor multiple times; the user turn
    must be persisted to Zep exactly once."""
    client = _make_mock_client(context=None)
    deps = ZepDeps(client=client, user_id="u", thread_id="t")

    agent = Agent(
        TestModel(),  # TestModel calls available tools, then returns
        deps_type=ZepDeps,
        capabilities=[ProcessHistory(zep_history_processor)],
        tools=[create_zep_search_tool()],
    )

    result = await agent.run("look it up", deps=deps)

    # The run made >1 model request (tool call + final), but only one persist.
    assert client.thread.add_messages.call_count == 1
    # The search tool actually ran against the mocked Zep client.
    client.graph.search.assert_called()
    assert client.graph.search.call_args.kwargs["user_id"] == "u"

    # persist_run stores the assistant reply afterwards.
    await persist_run(deps, result.new_messages())
    # Now two add_messages calls total: the user turn + the assistant reply.
    assert client.thread.add_messages.call_count == 2
    last_kwargs = client.thread.add_messages.call_args.kwargs
    assert all(m.role == "assistant" for m in last_kwargs["messages"])


@pytest.mark.asyncio
async def test_zep_outage_does_not_break_run() -> None:
    """If every Zep call fails, the agent run still completes."""
    client = MagicMock()
    client.user = MagicMock()
    client.user.add = AsyncMock(side_effect=RuntimeError("down"))
    client.thread = MagicMock()
    client.thread.create = AsyncMock(side_effect=RuntimeError("down"))
    client.thread.add_messages = AsyncMock(side_effect=RuntimeError("down"))
    deps = ZepDeps(client=client, user_id="u", thread_id="t")

    agent = Agent(
        TestModel(custom_output_text="still works"),
        deps_type=ZepDeps,
        capabilities=[ProcessHistory(zep_history_processor)],
    )

    result = await agent.run("hello", deps=deps)
    assert result.output == "still works"
