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
        provider._user_turn_persisted = True
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
        provider._user_turn_persisted = True
        ctx = make_context(response_messages=[Message("assistant", ["Hello"])])

        await run_after(provider, ctx)

        call = client.thread.add_messages.call_args.kwargs
        assert call["messages"][0].name == "Aria"

    @pytest.mark.asyncio
    async def test_skips_when_no_response(self) -> None:
        client = make_mock_client()
        provider = make_provider(client)
        provider._resources_ready = True
        provider._user_turn_persisted = True
        ctx = make_context(response_messages=None)

        await run_after(provider, ctx)

        client.thread.add_messages.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_no_assistant_text(self) -> None:
        client = make_mock_client()
        provider = make_provider(client)
        provider._resources_ready = True
        provider._user_turn_persisted = True
        ctx = make_context(response_messages=[Message("tool", ["tool output"])])

        await run_after(provider, ctx)

        client.thread.add_messages.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_user_turn_not_persisted(self) -> None:
        client = make_mock_client()
        provider = make_provider(client)
        # Resources are ready, but this run's user turn never persisted (e.g.
        # before_run's add_messages failed). after_run must not write an
        # orphaned assistant-only record.
        provider._resources_ready = True
        provider._user_turn_persisted = False
        ctx = make_context(response_messages=[Message("assistant", ["Hello"])])

        await run_after(provider, ctx)

        client.thread.add_messages.assert_not_called()

    @pytest.mark.asyncio
    async def test_zep_error_does_not_raise(self) -> None:
        client = make_mock_client()
        client.thread.add_messages.side_effect = RuntimeError("API down")
        provider = make_provider(client)
        provider._resources_ready = True
        provider._user_turn_persisted = True
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


# ---------------------------------------------------------------------------
# Message-size guard (Zep 4,096-char limit)
# ---------------------------------------------------------------------------
class TestMessageSizeGuard:
    def test_truncate_helper_keeps_short_content(self) -> None:
        from zep_ms_agent_framework._text import truncate_message_content

        text = "short"
        assert truncate_message_content(text, label="user message") == text

    def test_truncate_helper_caps_oversize_content(self) -> None:
        from zep_ms_agent_framework._text import (
            MESSAGE_TRUNCATE_LIMIT,
            ZEP_MESSAGE_CONTENT_LIMIT,
            truncate_message_content,
        )

        text = "x" * 9000
        result = truncate_message_content(text, label="user message")
        assert len(result) == MESSAGE_TRUNCATE_LIMIT
        # Result must always be within Zep's hard limit (never silently dropped).
        assert len(result) <= ZEP_MESSAGE_CONTENT_LIMIT
        assert result == text[:MESSAGE_TRUNCATE_LIMIT]

    def test_truncate_helper_warns_lengths_only(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        from zep_ms_agent_framework._text import truncate_message_content

        secret = "SENSITIVE" + ("y" * 9000)
        with caplog.at_level(logging.WARNING, logger="zep_ms_agent_framework._text"):
            truncate_message_content(secret, label="user message")

        assert len(caplog.records) == 1
        record = caplog.records[0]
        # The warning must contain lengths/counts and the (safe) label only --
        # never the content / PII.
        assert "SENSITIVE" not in record.getMessage()
        assert "user message" in record.getMessage()
        assert "9009" in record.getMessage()

    @pytest.mark.asyncio
    async def test_before_run_truncates_oversize_user_message(self) -> None:
        from zep_ms_agent_framework._text import MESSAGE_TRUNCATE_LIMIT

        client = make_mock_client()
        client.thread.add_messages.return_value = add_messages_response(None)
        provider = make_provider(client)
        oversize = "u" * 9000
        ctx = make_context(input_messages=[Message("user", [oversize])])

        await run_before(provider, ctx)

        # The turn was persisted (not dropped) and truncated to the safe limit.
        client.thread.add_messages.assert_called_once()
        sent = client.thread.add_messages.call_args.kwargs["messages"][0].content
        assert len(sent) == MESSAGE_TRUNCATE_LIMIT
        assert sent == oversize[:MESSAGE_TRUNCATE_LIMIT]

    @pytest.mark.asyncio
    async def test_after_run_truncates_oversize_assistant_message(self) -> None:
        from zep_ms_agent_framework._text import MESSAGE_TRUNCATE_LIMIT

        client = make_mock_client()
        provider = make_provider(client)
        provider._resources_ready = True
        provider._user_turn_persisted = True
        oversize = "a" * 9000
        ctx = make_context(response_messages=[Message("assistant", [oversize])])

        await run_after(provider, ctx)

        client.thread.add_messages.assert_called_once()
        sent = client.thread.add_messages.call_args.kwargs["messages"][0].content
        assert len(sent) == MESSAGE_TRUNCATE_LIMIT
        assert sent == oversize[:MESSAGE_TRUNCATE_LIMIT]


# ---------------------------------------------------------------------------
# Orphaned-turn protection: after_run must not persist when the user turn failed
# ---------------------------------------------------------------------------
class TestOrphanedTurnProtection:
    @pytest.mark.asyncio
    async def test_after_run_skips_when_before_run_persist_failed(self) -> None:
        # Resources create fine, but the user-turn add_messages fails.
        client = make_mock_client()
        client.thread.add_messages.side_effect = RuntimeError("API down")
        provider = make_provider(client)

        before_ctx = make_context(input_messages=[Message("user", ["Hi"])])
        await run_before(provider, before_ctx)

        # Resources were created, but the user turn was NOT persisted.
        assert provider._resources_ready is True
        assert provider._user_turn_persisted is False

        # Now the model "responds" -- after_run must not write an orphan.
        client.thread.add_messages.reset_mock()
        client.thread.add_messages.side_effect = None
        after_ctx = make_context(response_messages=[Message("assistant", ["Hello"])])
        await run_after(provider, after_ctx)

        client.thread.add_messages.assert_not_called()

    @pytest.mark.asyncio
    async def test_before_run_failure_does_not_leak_into_next_run(self) -> None:
        # A failed user turn in one run must not let a later run's after_run
        # persist an orphan: before_run resets the per-run flag each time.
        client = make_mock_client()
        provider = make_provider(client)

        # Run 1: user-turn persist succeeds, after_run persists.
        client.thread.add_messages.return_value = add_messages_response(None)
        await run_before(provider, make_context(input_messages=[Message("user", ["one"])]))
        assert provider._user_turn_persisted is True

        # Run 2: user-turn persist fails -> flag flips back to False.
        client.thread.add_messages.side_effect = RuntimeError("API down")
        await run_before(provider, make_context(input_messages=[Message("user", ["two"])]))
        assert provider._user_turn_persisted is False

        client.thread.add_messages.reset_mock()
        client.thread.add_messages.side_effect = None
        await run_after(provider, make_context(response_messages=[Message("assistant", ["resp"])]))
        client.thread.add_messages.assert_not_called()
