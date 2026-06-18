"""
Tests for the message-conversion helpers and lazy resource creation in
``zep_pydantic_ai.deps``.
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

from zep_pydantic_ai import ZepDeps
from zep_pydantic_ai.deps import (
    MAX_MESSAGE_CHARS,
    ensure_user_and_thread,
    latest_user_text,
    make_context_request,
    model_messages_to_zep,
)


def _make_deps(client: MagicMock | None = None) -> ZepDeps:
    return ZepDeps(client=client or _make_mock_client(), user_id="u", thread_id="t")


def _make_mock_client() -> MagicMock:
    client = MagicMock()
    client.user = MagicMock()
    client.user.add = AsyncMock()
    client.thread = MagicMock()
    client.thread.create = AsyncMock()
    client.thread.add_messages = AsyncMock()
    return client


class TestLatestUserText:
    def test_extracts_string_content(self) -> None:
        messages = [ModelRequest(parts=[UserPromptPart(content="Hello there")])]
        assert latest_user_text(messages) == "Hello there"

    def test_returns_most_recent_user_turn(self) -> None:
        messages = [
            ModelRequest(parts=[UserPromptPart(content="first")]),
            ModelResponse(parts=[TextPart(content="reply")]),
            ModelRequest(parts=[UserPromptPart(content="second")]),
        ]
        assert latest_user_text(messages) == "second"

    def test_joins_list_content_text_blocks(self) -> None:
        messages = [ModelRequest(parts=[UserPromptPart(content=["Describe", "this"])])]
        assert latest_user_text(messages) == "Describe this"

    def test_skips_system_prompt(self) -> None:
        messages = [ModelRequest(parts=[SystemPromptPart(content="system")])]
        assert latest_user_text(messages) is None

    def test_returns_none_when_no_user_message(self) -> None:
        messages = [ModelResponse(parts=[TextPart(content="hi")])]
        assert latest_user_text(messages) is None

    def test_empty_messages(self) -> None:
        assert latest_user_text([]) is None


class TestModelMessagesToZep:
    def test_converts_user_and_assistant(self) -> None:
        messages = [
            ModelRequest(parts=[UserPromptPart(content="Hi")]),
            ModelResponse(parts=[TextPart(content="Hello!")]),
        ]
        zep = model_messages_to_zep(messages, user_name="Jane", assistant_name="Bot")
        assert len(zep) == 2
        assert zep[0].role == "user"
        assert zep[0].content == "Hi"
        assert zep[0].name == "Jane"
        assert zep[1].role == "assistant"
        assert zep[1].content == "Hello!"
        assert zep[1].name == "Bot"

    def test_skips_tool_call_and_tool_return(self) -> None:
        messages = [
            ModelResponse(parts=[ToolCallPart(tool_name="t", args={"q": "x"})]),
            ModelRequest(parts=[ToolReturnPart(tool_name="t", content="result")]),
            ModelResponse(parts=[TextPart(content="Final answer")]),
        ]
        zep = model_messages_to_zep(messages, user_name=None, assistant_name="Bot")
        assert len(zep) == 1
        assert zep[0].role == "assistant"
        assert zep[0].content == "Final answer"

    def test_skips_empty_text(self) -> None:
        messages = [ModelResponse(parts=[TextPart(content="")])]
        zep = model_messages_to_zep(messages, user_name=None, assistant_name="Bot")
        assert zep == []

    def test_joins_multiple_assistant_text_parts(self) -> None:
        messages = [
            ModelResponse(parts=[TextPart(content="Part one."), TextPart(content="Part two.")]),
        ]
        zep = model_messages_to_zep(messages, user_name=None, assistant_name="Bot")
        assert zep[0].content == "Part one. Part two."

    def test_truncates_overlong_content(self) -> None:
        long_text = "x" * (MAX_MESSAGE_CHARS + 500)
        messages = [ModelRequest(parts=[UserPromptPart(content=long_text)])]
        zep = model_messages_to_zep(messages, user_name=None, assistant_name="Bot")
        assert len(zep[0].content) == MAX_MESSAGE_CHARS


class TestMakeContextRequest:
    def test_wraps_context_in_system_part(self) -> None:
        req = make_context_request("User likes blue.")
        assert isinstance(req, ModelRequest)
        assert len(req.parts) == 1
        part = req.parts[0]
        assert isinstance(part, SystemPromptPart)
        assert "<ZEP_CONTEXT>" in part.content
        assert "User likes blue." in part.content


class TestEnsureUserAndThread:
    @pytest.mark.asyncio
    async def test_creates_user_and_thread(self) -> None:
        client = _make_mock_client()
        deps = _make_deps(client)

        ok = await ensure_user_and_thread(deps)

        assert ok is True
        client.user.add.assert_called_once_with(
            user_id="u", first_name=None, last_name=None, email=None
        )
        client.thread.create.assert_called_once_with(thread_id="t", user_id="u")

    @pytest.mark.asyncio
    async def test_caches_after_first_creation(self) -> None:
        client = _make_mock_client()
        deps = _make_deps(client)

        await ensure_user_and_thread(deps)
        await ensure_user_and_thread(deps)

        assert client.user.add.call_count == 1
        assert client.thread.create.call_count == 1

    @pytest.mark.asyncio
    async def test_already_exists_is_success(self) -> None:
        client = _make_mock_client()
        client.user.add.side_effect = Exception("user already exists")
        client.thread.create.side_effect = Exception("409 conflict")
        deps = _make_deps(client)

        ok = await ensure_user_and_thread(deps)
        assert ok is True

    @pytest.mark.asyncio
    async def test_genuine_user_failure_returns_false_and_not_cached(self) -> None:
        client = _make_mock_client()
        client.user.add.side_effect = RuntimeError("network timeout")
        deps = _make_deps(client)

        ok = await ensure_user_and_thread(deps)
        assert ok is False
        # thread.create should not have been attempted
        client.thread.create.assert_not_called()

        # retry succeeds after the transient error clears
        client.user.add.side_effect = None
        ok2 = await ensure_user_and_thread(deps)
        assert ok2 is True

    @pytest.mark.asyncio
    async def test_passes_identity_fields(self) -> None:
        client = _make_mock_client()
        deps = ZepDeps(
            client=client,
            user_id="u",
            thread_id="t",
            first_name="Jane",
            last_name="Smith",
            email="jane@example.com",
        )

        await ensure_user_and_thread(deps)

        client.user.add.assert_called_once_with(
            user_id="u", first_name="Jane", last_name="Smith", email="jane@example.com"
        )
