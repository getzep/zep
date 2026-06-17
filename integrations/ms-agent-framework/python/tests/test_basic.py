"""
Tests for the zep-ms-agent-framework package.

Uses mocked Zep and Agent Framework objects to validate the integration logic
without requiring live services.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from agent_framework import Message
from zep_cloud.client import AsyncZep

from zep_ms_agent_framework import ZepContextProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def make_mock_client() -> MagicMock:
    """Create a mock AsyncZep client with async user/thread methods."""
    client = MagicMock(spec=AsyncZep)
    client.user = MagicMock()
    client.user.add = AsyncMock()
    client.thread = MagicMock()
    client.thread.create = AsyncMock()
    client.thread.add_messages = AsyncMock()
    return client


def make_context(
    input_messages: list[Message] | None = None,
    response_messages: list[Message] | None = None,
) -> MagicMock:
    """Create a mock SessionContext.

    The real ``SessionContext.extend_instructions`` is replaced with a mock so
    tests can assert on the injected instructions; ``response`` is a simple
    object exposing ``.messages``.
    """
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
        "user_id": "user-1",
        "thread_id": "thread-1",
    }
    params.update(kwargs)
    return ZepContextProvider(**params)


def add_messages_response(context: str | None) -> MagicMock:
    """Build a mock thread.add_messages response carrying a context block."""
    resp = MagicMock()
    resp.context = context
    return resp


async def run_before(provider: ZepContextProvider, context: MagicMock) -> None:
    """Invoke before_run with throwaway agent/session/state."""
    await provider.before_run(
        agent=MagicMock(),
        session=MagicMock(),
        context=context,
        state={},
    )


async def run_after(provider: ZepContextProvider, context: MagicMock) -> None:
    """Invoke after_run with throwaway agent/session/state."""
    await provider.after_run(
        agent=MagicMock(),
        session=MagicMock(),
        context=context,
        state={},
    )


# ---------------------------------------------------------------------------
# Package structure
# ---------------------------------------------------------------------------
def test_package_import() -> None:
    import zep_ms_agent_framework

    assert zep_ms_agent_framework is not None


def test_public_exports() -> None:
    from zep_ms_agent_framework import (
        DEFAULT_SOURCE_ID,
        UserSetupHook,
        ZepContextProvider,
        ZepDependencyError,
    )

    assert ZepContextProvider is not None
    assert UserSetupHook is not None
    assert ZepDependencyError is not None
    assert DEFAULT_SOURCE_ID == "zep"


class TestPackageStructure:
    def test_version_exists(self) -> None:
        import zep_ms_agent_framework

        assert hasattr(zep_ms_agent_framework, "__version__")
        assert zep_ms_agent_framework.__version__ == "0.1.0"

    def test_author_exists(self) -> None:
        import zep_ms_agent_framework

        assert hasattr(zep_ms_agent_framework, "__author__")

    def test_description_exists(self) -> None:
        import zep_ms_agent_framework

        assert hasattr(zep_ms_agent_framework, "__description__")


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------
class TestInit:
    def test_subclasses_context_provider(self) -> None:
        from agent_framework import ContextProvider

        provider = make_provider()
        assert isinstance(provider, ContextProvider)

    def test_init_stores_identity(self) -> None:
        client = make_mock_client()
        provider = make_provider(client, user_id="u", thread_id="t")
        assert provider._zep is client
        assert provider.user_id == "u"
        assert provider.thread_id == "t"
        assert provider._resources_ready is False

    def test_default_source_id(self) -> None:
        provider = make_provider()
        assert provider.source_id == "zep"

    def test_custom_source_id(self) -> None:
        provider = make_provider(source_id="memory")
        assert provider.source_id == "memory"

    def test_user_message_name_defaults_to_full_name(self) -> None:
        provider = make_provider(first_name="Jane", last_name="Smith")
        assert provider._user_message_name == "Jane Smith"

    def test_user_message_name_explicit_override(self) -> None:
        provider = make_provider(first_name="Jane", user_message_name="Janey")
        assert provider._user_message_name == "Janey"

    def test_user_message_name_none_without_names(self) -> None:
        provider = make_provider()
        assert provider._user_message_name is None

    def test_assistant_message_name_default(self) -> None:
        provider = make_provider()
        assert provider._assistant_message_name == "Assistant"

    def test_empty_user_id_raises(self) -> None:
        with pytest.raises(ValueError, match="user_id"):
            make_provider(user_id="")

    def test_empty_thread_id_raises(self) -> None:
        with pytest.raises(ValueError, match="thread_id"):
            make_provider(thread_id="")


# ---------------------------------------------------------------------------
# Message extraction
# ---------------------------------------------------------------------------
class TestMessageExtraction:
    def test_latest_user_text_returns_last_user_message(self) -> None:
        messages = [
            Message("user", ["first"]),
            Message("assistant", ["reply"]),
            Message("user", ["second"]),
        ]
        assert ZepContextProvider._latest_user_text(messages) == "second"

    def test_latest_user_text_falls_back_to_any_text(self) -> None:
        # No explicit user role -> falls back to last message carrying text.
        messages = [Message("system", ["sys instruction"])]
        assert ZepContextProvider._latest_user_text(messages) == "sys instruction"

    def test_latest_user_text_empty_returns_none(self) -> None:
        assert ZepContextProvider._latest_user_text([]) is None

    def test_assistant_text_joins_assistant_messages(self) -> None:
        messages = [
            Message("assistant", ["part one"]),
            Message("assistant", ["part two"]),
        ]
        assert ZepContextProvider._assistant_text(messages) == "part one\npart two"

    def test_assistant_text_ignores_non_assistant(self) -> None:
        messages = [Message("user", ["hi"]), Message("assistant", ["answer"])]
        assert ZepContextProvider._assistant_text(messages) == "answer"

    def test_assistant_text_none_for_empty(self) -> None:
        assert ZepContextProvider._assistant_text(None) is None
        assert ZepContextProvider._assistant_text([]) is None


# ---------------------------------------------------------------------------
# before_run
# ---------------------------------------------------------------------------
class TestBeforeRun:
    @pytest.mark.asyncio
    async def test_persists_user_message(self) -> None:
        client = make_mock_client()
        client.thread.add_messages.return_value = add_messages_response("ctx")
        provider = make_provider(client)
        ctx = make_context(input_messages=[Message("user", ["Hi there"])])

        await run_before(provider, ctx)

        client.thread.add_messages.assert_called_once()
        call = client.thread.add_messages.call_args.kwargs
        assert call["thread_id"] == "thread-1"
        assert call["return_context"] is True
        assert len(call["messages"]) == 1
        assert call["messages"][0].content == "Hi there"
        assert call["messages"][0].role == "user"

    @pytest.mark.asyncio
    async def test_lazy_creates_user_and_thread(self) -> None:
        client = make_mock_client()
        client.thread.add_messages.return_value = add_messages_response(None)
        provider = make_provider(
            client,
            first_name="Jane",
            last_name="Smith",
            email="jane@example.com",
        )
        ctx = make_context(input_messages=[Message("user", ["Hi"])])

        await run_before(provider, ctx)

        client.user.add.assert_called_once_with(
            user_id="user-1",
            first_name="Jane",
            last_name="Smith",
            email="jane@example.com",
        )
        client.thread.create.assert_called_once_with(thread_id="thread-1", user_id="user-1")

    @pytest.mark.asyncio
    async def test_message_carries_display_name(self) -> None:
        client = make_mock_client()
        client.thread.add_messages.return_value = add_messages_response(None)
        provider = make_provider(client, first_name="Jane", last_name="Smith")
        ctx = make_context(input_messages=[Message("user", ["Hi"])])

        await run_before(provider, ctx)

        call = client.thread.add_messages.call_args.kwargs
        assert call["messages"][0].name == "Jane Smith"

    @pytest.mark.asyncio
    async def test_injects_context_block(self) -> None:
        client = make_mock_client()
        client.thread.add_messages.return_value = add_messages_response(
            "User is a data scientist in Portland."
        )
        provider = make_provider(client)
        ctx = make_context(input_messages=[Message("user", ["What do you know about me?"])])

        await run_before(provider, ctx)

        ctx.extend_instructions.assert_called_once()
        source_id, instruction = ctx.extend_instructions.call_args.args
        assert source_id == "zep"
        assert "<ZEP_CONTEXT>" in instruction
        assert "data scientist in Portland" in instruction

    @pytest.mark.asyncio
    async def test_no_injection_when_context_empty(self) -> None:
        client = make_mock_client()
        client.thread.add_messages.return_value = add_messages_response(None)
        provider = make_provider(client)
        ctx = make_context(input_messages=[Message("user", ["Hi"])])

        await run_before(provider, ctx)

        ctx.extend_instructions.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_no_user_message(self) -> None:
        client = make_mock_client()
        provider = make_provider(client)
        ctx = make_context(input_messages=[])

        await run_before(provider, ctx)

        client.user.add.assert_not_called()
        client.thread.add_messages.assert_not_called()
        ctx.extend_instructions.assert_not_called()

    @pytest.mark.asyncio
    async def test_passes_ignore_roles(self) -> None:
        client = make_mock_client()
        client.thread.add_messages.return_value = add_messages_response(None)
        provider = make_provider(client, ignore_roles=["assistant"])
        ctx = make_context(input_messages=[Message("user", ["Hi"])])

        await run_before(provider, ctx)

        call = client.thread.add_messages.call_args.kwargs
        assert call["ignore_roles"] == ["assistant"]

    @pytest.mark.asyncio
    async def test_zep_error_does_not_raise(self) -> None:
        client = make_mock_client()
        client.thread.add_messages.side_effect = RuntimeError("API down")
        provider = make_provider(client)
        ctx = make_context(input_messages=[Message("user", ["Hi"])])

        # Must not raise -- a Zep failure cannot crash the host agent.
        await run_before(provider, ctx)

        ctx.extend_instructions.assert_not_called()

    @pytest.mark.asyncio
    async def test_resource_failure_skips_persist(self) -> None:
        client = make_mock_client()
        client.user.add.side_effect = RuntimeError("network timeout")
        provider = make_provider(client)
        ctx = make_context(input_messages=[Message("user", ["Hi"])])

        await run_before(provider, ctx)

        client.thread.add_messages.assert_not_called()
        assert provider._resources_ready is False

    @pytest.mark.asyncio
    async def test_resources_created_only_once(self) -> None:
        client = make_mock_client()
        client.thread.add_messages.return_value = add_messages_response(None)
        provider = make_provider(client)

        await run_before(provider, make_context(input_messages=[Message("user", ["one"])]))
        await run_before(provider, make_context(input_messages=[Message("user", ["two"])]))

        assert client.user.add.call_count == 1
        assert client.thread.create.call_count == 1
        assert client.thread.add_messages.call_count == 2

    @pytest.mark.asyncio
    async def test_existing_user_tolerated(self) -> None:
        client = make_mock_client()
        client.user.add.side_effect = Exception("user already exists")
        client.thread.create.side_effect = Exception("thread already exists")
        client.thread.add_messages.return_value = add_messages_response("ctx")
        provider = make_provider(client)
        ctx = make_context(input_messages=[Message("user", ["Hi"])])

        await run_before(provider, ctx)

        # Despite both "already exists" errors, the message is still persisted.
        client.thread.add_messages.assert_called_once()
        assert provider._resources_ready is True


# ---------------------------------------------------------------------------
# after_run
# ---------------------------------------------------------------------------
class TestAfterRun:
    @pytest.mark.asyncio
    async def test_persists_assistant_response(self) -> None:
        client = make_mock_client()
        provider = make_provider(client)
        provider._resources_ready = True
        ctx = make_context(response_messages=[Message("assistant", ["The answer is 42."])])

        await run_after(provider, ctx)

        client.thread.add_messages.assert_called_once()
        call = client.thread.add_messages.call_args.kwargs
        assert call["thread_id"] == "thread-1"
        assert call["messages"][0].role == "assistant"
        assert call["messages"][0].content == "The answer is 42."
        assert call["messages"][0].name == "Assistant"

    @pytest.mark.asyncio
    async def test_custom_assistant_name(self) -> None:
        client = make_mock_client()
        provider = make_provider(client, assistant_message_name="Aria")
        provider._resources_ready = True
        ctx = make_context(response_messages=[Message("assistant", ["Hello"])])

        await run_after(provider, ctx)

        call = client.thread.add_messages.call_args.kwargs
        assert call["messages"][0].name == "Aria"

    @pytest.mark.asyncio
    async def test_skips_when_no_response(self) -> None:
        client = make_mock_client()
        provider = make_provider(client)
        provider._resources_ready = True
        ctx = make_context(response_messages=None)

        await run_after(provider, ctx)

        client.thread.add_messages.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_no_assistant_text(self) -> None:
        client = make_mock_client()
        provider = make_provider(client)
        provider._resources_ready = True
        ctx = make_context(response_messages=[Message("tool", ["tool output"])])

        await run_after(provider, ctx)

        client.thread.add_messages.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_resources_not_ready(self) -> None:
        client = make_mock_client()
        provider = make_provider(client)
        # _resources_ready stays False (before_run failed earlier)
        ctx = make_context(response_messages=[Message("assistant", ["Hello"])])

        await run_after(provider, ctx)

        client.thread.add_messages.assert_not_called()

    @pytest.mark.asyncio
    async def test_zep_error_does_not_raise(self) -> None:
        client = make_mock_client()
        client.thread.add_messages.side_effect = RuntimeError("API down")
        provider = make_provider(client)
        provider._resources_ready = True
        ctx = make_context(response_messages=[Message("assistant", ["Hello"])])

        # Must not raise.
        await run_after(provider, ctx)


# ---------------------------------------------------------------------------
# Full round-trip ordering
# ---------------------------------------------------------------------------
class TestRoundTrip:
    @pytest.mark.asyncio
    async def test_before_then_after_persists_both_turns(self) -> None:
        client = make_mock_client()
        client.thread.add_messages.return_value = add_messages_response("ctx")
        provider = make_provider(client)

        before_ctx = make_context(input_messages=[Message("user", ["Hi"])])
        await run_before(provider, before_ctx)

        after_ctx = make_context(response_messages=[Message("assistant", ["Hello!"])])
        await run_after(provider, after_ctx)

        assert client.thread.add_messages.call_count == 2
        roles = [c.kwargs["messages"][0].role for c in client.thread.add_messages.call_args_list]
        assert roles == ["user", "assistant"]


# ---------------------------------------------------------------------------
# on_user_created hook
# ---------------------------------------------------------------------------
class TestOnUserCreatedHook:
    @pytest.mark.asyncio
    async def test_hook_called_on_new_user(self) -> None:
        client = make_mock_client()
        client.thread.add_messages.return_value = add_messages_response(None)
        hook = AsyncMock()
        provider = make_provider(client, on_user_created=hook)
        ctx = make_context(input_messages=[Message("user", ["Hi"])])

        await run_before(provider, ctx)

        hook.assert_called_once_with(client, "user-1")

    @pytest.mark.asyncio
    async def test_hook_not_called_on_existing_user(self) -> None:
        client = make_mock_client()
        client.user.add.side_effect = Exception("already exists")
        client.thread.add_messages.return_value = add_messages_response(None)
        hook = AsyncMock()
        provider = make_provider(client, on_user_created=hook)
        ctx = make_context(input_messages=[Message("user", ["Hi"])])

        await run_before(provider, ctx)

        hook.assert_not_called()

    @pytest.mark.asyncio
    async def test_hook_runs_once_across_runs(self) -> None:
        client = make_mock_client()
        client.thread.add_messages.return_value = add_messages_response(None)
        hook = AsyncMock()
        provider = make_provider(client, on_user_created=hook)

        await run_before(provider, make_context(input_messages=[Message("user", ["one"])]))
        await run_before(provider, make_context(input_messages=[Message("user", ["two"])]))

        assert hook.call_count == 1

    @pytest.mark.asyncio
    async def test_hook_failure_does_not_block_run(self) -> None:
        client = make_mock_client()
        client.thread.add_messages.return_value = add_messages_response("ctx")
        hook = AsyncMock(side_effect=RuntimeError("hook exploded"))
        provider = make_provider(client, on_user_created=hook)
        ctx = make_context(input_messages=[Message("user", ["Hi"])])

        await run_before(provider, ctx)

        # The run continued: message persisted and context injected.
        client.thread.add_messages.assert_called_once()
        ctx.extend_instructions.assert_called_once()
