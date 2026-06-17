"""
Tests for ``zep_history_processor`` and ``persist_run`` with a mocked Zep
client and a stub ``RunContext``.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from zep_pydantic_ai import ZepDeps, persist_run, reset_turn_cache, zep_history_processor


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    """Each test starts with a clean turn-dedupe cache."""
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


def _user_history(text: str = "Hello") -> list:
    return [ModelRequest(parts=[UserPromptPart(content=text)])]


class TestPersistAndContext:
    @pytest.mark.asyncio
    async def test_persists_user_message_with_return_context(self) -> None:
        client = _make_mock_client(context="Some context")
        deps = _make_deps(client, first_name="Jane", last_name="Smith")
        ctx = _make_ctx(deps)

        await zep_history_processor(ctx, _user_history("Hi there"))

        client.thread.add_messages.assert_called_once()
        kwargs = client.thread.add_messages.call_args.kwargs
        assert kwargs["thread_id"] == "thread-1"
        assert kwargs["return_context"] is True
        assert len(kwargs["messages"]) == 1
        assert kwargs["messages"][0].role == "user"
        assert kwargs["messages"][0].content == "Hi there"
        assert kwargs["messages"][0].name == "Jane Smith"

    @pytest.mark.asyncio
    async def test_creates_user_and_thread_lazily(self) -> None:
        client = _make_mock_client(context=None)
        deps = _make_deps(client)
        ctx = _make_ctx(deps)

        await zep_history_processor(ctx, _user_history())

        client.user.add.assert_called_once()
        client.thread.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_prepends_context_block(self) -> None:
        client = _make_mock_client(context="User likes blue.")
        deps = _make_deps(client)
        ctx = _make_ctx(deps)

        messages = _user_history("What's my favourite colour?")
        result = await zep_history_processor(ctx, messages)

        # First message should be the injected context request.
        assert len(result) == len(messages) + 1
        first = result[0]
        assert isinstance(first, ModelRequest)
        assert isinstance(first.parts[0], SystemPromptPart)
        assert "User likes blue." in first.parts[0].content
        # Original messages preserved after it.
        assert result[1:] == messages

    @pytest.mark.asyncio
    async def test_no_context_returns_history_unchanged(self) -> None:
        client = _make_mock_client(context=None)
        deps = _make_deps(client)
        ctx = _make_ctx(deps)

        messages = _user_history()
        result = await zep_history_processor(ctx, messages)

        assert result == messages

    @pytest.mark.asyncio
    async def test_ignore_roles_passed_through(self) -> None:
        client = _make_mock_client(context=None)
        deps = _make_deps(client, ignore_roles=["assistant"])
        ctx = _make_ctx(deps)

        await zep_history_processor(ctx, _user_history())

        kwargs = client.thread.add_messages.call_args.kwargs
        assert kwargs["ignore_roles"] == ["assistant"]


class TestDedupeGuard:
    @pytest.mark.asyncio
    async def test_same_turn_reinvocation_persists_once(self) -> None:
        """The processor fires once per model request; the same user turn must
        only be persisted to Zep once, with context replayed from cache."""
        client = _make_mock_client(context="cached context")
        deps = _make_deps(client)
        ctx = _make_ctx(deps)

        messages = _user_history("Same question")
        r1 = await zep_history_processor(ctx, messages)
        r2 = await zep_history_processor(ctx, messages)

        # Persisted only once across the two model requests.
        assert client.thread.add_messages.call_count == 1
        # But both invocations still inject the (cached) context block.
        assert isinstance(r1[0].parts[0], SystemPromptPart)
        assert isinstance(r2[0].parts[0], SystemPromptPart)
        assert "cached context" in r2[0].parts[0].content

    @pytest.mark.asyncio
    async def test_new_user_turn_persists_again(self) -> None:
        client = _make_mock_client(context="ctx")
        deps = _make_deps(client)
        ctx = _make_ctx(deps)

        await zep_history_processor(ctx, _user_history("first"))
        await zep_history_processor(ctx, _user_history("second"))

        assert client.thread.add_messages.call_count == 2

    @pytest.mark.asyncio
    async def test_distinct_threads_tracked_separately(self) -> None:
        client = _make_mock_client(context="ctx")
        deps_a = _make_deps(client, user_id="user-A", thread_id="thread-A")
        deps_b = _make_deps(client, user_id="user-B", thread_id="thread-B")

        await zep_history_processor(_make_ctx(deps_a), _user_history("hi"))
        await zep_history_processor(_make_ctx(deps_b), _user_history("hi"))

        # Same text but different threads -> two persists.
        assert client.thread.add_messages.call_count == 2


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_zep_failure_returns_history_unchanged(self) -> None:
        client = _make_mock_client()
        client.thread.add_messages.side_effect = RuntimeError("Zep down")
        deps = _make_deps(client)
        ctx = _make_ctx(deps)

        messages = _user_history()
        result = await zep_history_processor(ctx, messages)

        # No crash; original history returned with no context injected.
        assert result == messages

    @pytest.mark.asyncio
    async def test_failed_persist_not_cached_so_retries(self) -> None:
        client = _make_mock_client()
        ok_response = MagicMock()
        ok_response.context = "ctx"
        client.thread.add_messages.side_effect = [RuntimeError("transient"), ok_response]
        deps = _make_deps(client)
        ctx = _make_ctx(deps)

        messages = _user_history("retry me")
        await zep_history_processor(ctx, messages)  # fails
        await zep_history_processor(ctx, messages)  # retries, succeeds

        assert client.thread.add_messages.call_count == 2

    @pytest.mark.asyncio
    async def test_no_user_text_skips_persist(self) -> None:
        client = _make_mock_client()
        deps = _make_deps(client)
        ctx = _make_ctx(deps)

        # History with only an assistant turn -> nothing to persist.
        messages = [ModelResponse(parts=[TextPart(content="hello")])]
        result = await zep_history_processor(ctx, messages)

        client.thread.add_messages.assert_not_called()
        assert result == messages

    @pytest.mark.asyncio
    async def test_resource_creation_failure_skips_persist(self) -> None:
        client = _make_mock_client()
        client.user.add.side_effect = RuntimeError("auth error")
        deps = _make_deps(client)
        ctx = _make_ctx(deps)

        messages = _user_history()
        result = await zep_history_processor(ctx, messages)

        client.thread.add_messages.assert_not_called()
        # No context injected; the same history object is returned unchanged.
        assert result == messages


class TestPersistRun:
    @pytest.mark.asyncio
    async def test_persists_assistant_message(self) -> None:
        client = _make_mock_client()
        deps = _make_deps(client, assistant_name="Bot")

        new_messages = [
            ModelRequest(parts=[UserPromptPart(content="Hi")]),
            ModelResponse(parts=[TextPart(content="Hello, friend!")]),
        ]
        await persist_run(deps, new_messages)

        client.thread.add_messages.assert_called_once()
        kwargs = client.thread.add_messages.call_args.kwargs
        # Only the assistant message is persisted (user turn already in Zep).
        assert len(kwargs["messages"]) == 1
        assert kwargs["messages"][0].role == "assistant"
        assert kwargs["messages"][0].content == "Hello, friend!"
        assert kwargs["messages"][0].name == "Bot"
        assert "return_context" not in kwargs

    @pytest.mark.asyncio
    async def test_skips_tool_scaffolding(self) -> None:
        client = _make_mock_client()
        deps = _make_deps(client)

        new_messages = [
            ModelResponse(parts=[ToolCallPart(tool_name="search", args={"q": "x"})]),
            ModelRequest(parts=[ToolReturnPart(tool_name="search", content="result")]),
            ModelResponse(parts=[TextPart(content="The answer is 42.")]),
        ]
        await persist_run(deps, new_messages)

        kwargs = client.thread.add_messages.call_args.kwargs
        assert len(kwargs["messages"]) == 1
        assert kwargs["messages"][0].content == "The answer is 42."

    @pytest.mark.asyncio
    async def test_noop_when_no_assistant_text(self) -> None:
        client = _make_mock_client()
        deps = _make_deps(client)

        new_messages = [ModelRequest(parts=[UserPromptPart(content="Hi")])]
        await persist_run(deps, new_messages)

        client.thread.add_messages.assert_not_called()

    @pytest.mark.asyncio
    async def test_failure_does_not_raise(self) -> None:
        client = _make_mock_client()
        client.thread.add_messages.side_effect = RuntimeError("Zep down")
        deps = _make_deps(client)

        new_messages = [ModelResponse(parts=[TextPart(content="answer")])]
        # Should not raise.
        await persist_run(deps, new_messages)
