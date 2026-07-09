"""
Tests for automatic assistant-message persistence via Pydantic AI's
``Hooks(after_run=...)`` capability.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic_ai import Agent
from pydantic_ai.capabilities import Hooks, ProcessHistory
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.test import TestModel

from zep_pydantic_ai import ZepDeps, reset_turn_cache, zep_history_processor
from zep_pydantic_ai.capabilities import create_zep_after_run_hook, zep_capabilities


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    reset_turn_cache()


def _make_mock_client(context: str | None = None) -> MagicMock:
    client = MagicMock()
    client.user = MagicMock()
    client.user.add = AsyncMock()
    client.thread = MagicMock()
    client.thread.create = AsyncMock()
    response = MagicMock()
    response.context = context
    client.thread.add_messages = AsyncMock(return_value=response)
    return client


def _make_deps(client: MagicMock, **kwargs: object) -> ZepDeps:
    base = {"user_id": "user-1", "thread_id": "thread-1"}
    base.update(kwargs)
    return ZepDeps(client=client, **base)  # type: ignore[arg-type]


def _make_ctx(deps: ZepDeps) -> MagicMock:
    ctx = MagicMock()
    ctx.deps = deps
    return ctx


def _make_result(new_messages: list) -> MagicMock:
    result = MagicMock()
    result.new_messages = MagicMock(return_value=new_messages)
    return result


class TestCreateZepAfterRunHook:
    @pytest.mark.asyncio
    async def test_after_run_hook_persists_assistant_messages(self) -> None:
        client = _make_mock_client()
        deps = _make_deps(client)
        hook = create_zep_after_run_hook(deps)

        new_messages = [
            ModelRequest(parts=[UserPromptPart(content="Hi")]),
            ModelResponse(parts=[TextPart(content="Hello there!")]),
        ]
        result = _make_result(new_messages)

        returned = await hook(_make_ctx(deps), result=result)

        assert returned is result
        client.thread.add_messages.assert_called_once()
        kwargs = client.thread.add_messages.call_args.kwargs
        assert len(kwargs["messages"]) == 1
        assert kwargs["messages"][0].role == "assistant"
        assert kwargs["messages"][0].content == "Hello there!"

    @pytest.mark.asyncio
    async def test_after_run_hook_swallows_zep_errors(self) -> None:
        """The hook runs inside the agent's run loop -- the hot-path rule
        applies: a Zep failure must never propagate and break the run."""
        client = _make_mock_client()
        client.thread.add_messages.side_effect = RuntimeError("Zep down")
        deps = _make_deps(client)
        hook = create_zep_after_run_hook(deps)

        new_messages = [ModelResponse(parts=[TextPart(content="answer")])]
        result = _make_result(new_messages)

        # Should not raise, and should still return the original result.
        returned = await hook(_make_ctx(deps), result=result)
        assert returned is result

    @pytest.mark.asyncio
    async def test_after_run_hook_swallows_provisioning_errors(self) -> None:
        """A genuine provisioning failure (e.g. auth error creating the user)
        must also be swallowed, not just the add_messages call."""
        client = _make_mock_client()
        client.user.add.side_effect = RuntimeError("auth error")
        deps = _make_deps(client)
        hook = create_zep_after_run_hook(deps)

        new_messages = [ModelResponse(parts=[TextPart(content="answer")])]
        result = _make_result(new_messages)

        returned = await hook(_make_ctx(deps), result=result)
        assert returned is result
        client.thread.add_messages.assert_not_called()

    @pytest.mark.asyncio
    async def test_noop_when_no_assistant_text(self) -> None:
        client = _make_mock_client()
        deps = _make_deps(client)
        hook = create_zep_after_run_hook(deps)

        result = _make_result([ModelRequest(parts=[UserPromptPart(content="Hi")])])

        await hook(_make_ctx(deps), result=result)
        client.thread.add_messages.assert_not_called()


class TestZepCapabilities:
    def test_returns_process_history_and_hooks(self) -> None:
        client = _make_mock_client()
        deps = _make_deps(client)

        caps = zep_capabilities(deps)

        assert len(caps) == 2
        assert isinstance(caps[0], ProcessHistory)
        assert isinstance(caps[1], Hooks)

    @pytest.mark.asyncio
    async def test_end_to_end_auto_persist_via_agent_run(self) -> None:
        """Wiring zep_capabilities(deps) into an Agent persists both the user
        turn (via the history processor) and the assistant reply (via the
        after_run hook) without any explicit persist_run call."""
        client = _make_mock_client(context=None)
        deps = _make_deps(client)

        agent = Agent(
            TestModel(custom_output_text="ack"),
            deps_type=ZepDeps,
            capabilities=zep_capabilities(deps),
        )

        await agent.run("Hello", deps=deps)

        # Two add_messages calls: the user turn (processor) + assistant reply (hook).
        assert client.thread.add_messages.call_count == 2
        roles = [
            call.kwargs["messages"][0].role for call in client.thread.add_messages.call_args_list
        ]
        assert "user" in roles
        assert "assistant" in roles

    def test_zep_capabilities_uses_zep_history_processor(self) -> None:
        client = _make_mock_client()
        deps = _make_deps(client)

        caps = zep_capabilities(deps)
        process_history = caps[0]

        assert process_history.processor is zep_history_processor  # type: ignore[attr-defined]
