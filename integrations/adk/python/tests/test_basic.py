"""
Tests for the zep-adk package.

Uses mocked Zep and ADK objects to validate the integration logic without
requiring live services.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest


def test_package_import() -> None:
    """Test that the package can be imported successfully."""
    import zep_adk

    assert zep_adk is not None


def test_public_exports() -> None:
    """Test that the expected public API is exported."""
    from zep_adk import ZepContextTool, ZepMemoryService, create_after_model_callback

    assert ZepContextTool is not None
    assert ZepMemoryService is not None
    assert create_after_model_callback is not None


class TestPackageStructure:
    """Basic structure tests for the zep-adk package."""

    def test_version_exists(self) -> None:
        import zep_adk

        assert hasattr(zep_adk, "__version__")
        assert zep_adk.__version__

    def test_version_matches_installed_metadata(self) -> None:
        """``zep_adk.__version__`` must match the installed package version.

        Guards against the version drifting between ``pyproject.toml`` and the
        hand-maintained ``__version__`` constant in ``__init__.py``. Prefers
        ``importlib.metadata`` (accurate for both regular and editable
        installs); falls back to parsing ``pyproject.toml`` directly if the
        distribution metadata can't be found (e.g. running from a source
        checkout without an install).
        """
        import importlib.metadata

        import zep_adk

        try:
            installed_version = importlib.metadata.version("zep-adk")
        except importlib.metadata.PackageNotFoundError:
            import pathlib
            import re

            pyproject_path = pathlib.Path(__file__).resolve().parent.parent / "pyproject.toml"
            pyproject_text = pyproject_path.read_text()
            match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', pyproject_text)
            assert match is not None, "Could not find version in pyproject.toml"
            installed_version = match.group(1)

        assert zep_adk.__version__ == installed_version

    def test_author_exists(self) -> None:
        import zep_adk

        assert hasattr(zep_adk, "__author__")

    def test_description_exists(self) -> None:
        import zep_adk

        assert hasattr(zep_adk, "__description__")


class TestZepContextToolInit:
    """Test ZepContextTool initialisation."""

    def test_init_with_valid_args(self) -> None:
        from zep_adk import ZepContextTool

        try:
            from zep_cloud.client import AsyncZep

            mock_client = MagicMock(spec=AsyncZep)
        except ImportError:
            mock_client = MagicMock()

        tool = ZepContextTool(zep_client=mock_client)
        assert tool is not None
        assert tool._zep is mock_client
        assert len(tool._last_persisted_content_id) == 0


class TestIdentityResolution:
    """Test _resolve_identity from session state."""

    def _make_tool(self) -> "ZepContextTool":  # type: ignore[name-defined]  # noqa: F821
        from zep_adk import ZepContextTool

        return ZepContextTool(zep_client=MagicMock())

    def _make_tool_context(
        self,
        state: dict | None = None,
        session_id: str | None = "fallback-session-id",
        session_user_id: str | None = "fallback-user-id",
    ) -> MagicMock:
        """Create a mock ToolContext with state and optional session metadata."""
        mock_tc = MagicMock()
        mock_tc.state = state or {}

        if session_id is not None:
            mock_tc.session.id = session_id
            mock_tc.user_id = session_user_id
        else:
            # Simulate a ToolContext whose public user_id/session properties
            # raise (e.g. an ADK version without them), by giving the mock
            # a spec that excludes those attributes.
            mock_tc = MagicMock(spec=["state"])
            mock_tc.state = state or {}
        return mock_tc

    def test_resolves_identity_from_state(self) -> None:
        tool = self._make_tool()
        tc = self._make_tool_context(
            state={
                "zep_user_id": "user-1",
                "zep_thread_id": "thread-1",
                "zep_first_name": "Jane",
                "zep_last_name": "Smith",
            }
        )
        identity = tool._resolve_identity(tc)
        assert identity.user_id == "user-1"
        assert identity.thread_id == "thread-1"
        assert identity.first_name == "Jane"
        assert identity.last_name == "Smith"
        assert identity.user_display_name == "Jane Smith"

    def test_user_id_falls_back_to_session_user_id(self) -> None:
        """When zep_user_id is not in state, session user_id should be used."""
        tool = self._make_tool()
        tc = self._make_tool_context(
            state={"zep_thread_id": "t"},
            session_user_id="session-user-42",
        )
        identity = tool._resolve_identity(tc)
        assert identity.user_id == "session-user-42"

    def test_missing_user_id_raises_value_error(self) -> None:
        """When neither state nor session has a user_id, should raise ValueError."""
        tool = self._make_tool()
        tc = self._make_tool_context(state={}, session_id=None)
        with pytest.raises(ValueError, match="Cannot determine Zep user ID"):
            tool._resolve_identity(tc)

    def test_thread_id_falls_back_to_session_id(self) -> None:
        tool = self._make_tool()
        tc = self._make_tool_context(
            state={},
            session_id="adk-session-123",
            session_user_id="user-1",
        )
        identity = tool._resolve_identity(tc)
        assert identity.thread_id == "adk-session-123"

    def test_thread_id_fallback_fails_raises_value_error(self) -> None:
        tool = self._make_tool()
        tc = self._make_tool_context(
            state={"zep_user_id": "user-1"},
            session_id=None,
        )
        with pytest.raises(ValueError, match="Cannot determine Zep thread ID"):
            tool._resolve_identity(tc)

    def test_display_name_with_first_only(self) -> None:
        """When only first_name is provided, last_name defaults to 'User'."""
        tool = self._make_tool()
        tc = self._make_tool_context(
            state={"zep_user_id": "u", "zep_thread_id": "t", "zep_first_name": "Jane"}
        )
        identity = tool._resolve_identity(tc)
        assert identity.user_display_name == "Jane User"

    def test_display_name_defaults_when_no_name(self) -> None:
        """When no name is provided, defaults to 'Anonymous User'."""
        tool = self._make_tool()
        tc = self._make_tool_context(state={"zep_user_id": "u", "zep_thread_id": "t"})
        identity = tool._resolve_identity(tc)
        assert identity.first_name == "Anonymous"
        assert identity.last_name == "User"
        assert identity.user_display_name == "Anonymous User"

    def test_display_name_first_and_last(self) -> None:
        tool = self._make_tool()
        tc = self._make_tool_context(
            state={
                "zep_user_id": "u",
                "zep_thread_id": "t",
                "zep_first_name": "Jane",
                "zep_last_name": "Doe",
            }
        )
        identity = tool._resolve_identity(tc)
        assert identity.user_display_name == "Jane Doe"

    def test_name_defaults_when_no_identity_fields(self) -> None:
        """When no identity fields are in state, first/last names default."""
        tool = self._make_tool()
        tc = self._make_tool_context(state={"zep_user_id": "u", "zep_thread_id": "t"})
        identity = tool._resolve_identity(tc)
        assert identity.first_name == "Anonymous"
        assert identity.last_name == "User"


class TestZepContextToolProcessLlmRequest:
    """Test ZepContextTool.process_llm_request with mocked dependencies."""

    def _make_tool(self, mock_client: MagicMock | None = None) -> "ZepContextTool":  # type: ignore[name-defined]  # noqa: F821
        from zep_adk import ZepContextTool

        return ZepContextTool(zep_client=mock_client or self._make_mock_client())

    def _make_mock_client(self) -> MagicMock:
        """Create a mock AsyncZep client with async user/thread methods."""
        mock_client = MagicMock()
        mock_client.user = MagicMock()
        mock_client.user.add = AsyncMock()
        mock_client.thread = MagicMock()
        mock_client.thread.create = AsyncMock()
        mock_client.thread.add_messages = AsyncMock()
        return mock_client

    def _make_tool_context(
        self,
        text: str = "Hello",
        state: dict | None = None,
        session_id: str = "test-session",
        session_user_id: str = "test-user",
    ) -> MagicMock:
        """Create a mock ToolContext with user_content and state."""
        mock_part = MagicMock()
        mock_part.text = text
        mock_content = MagicMock()
        mock_content.parts = [mock_part]

        mock_tc = MagicMock()
        mock_tc.user_content = mock_content
        mock_tc.state = state if state is not None else {"zep_thread_id": "test-thread"}
        mock_tc.session.id = session_id
        mock_tc.user_id = session_user_id
        return mock_tc

    def _make_llm_request(self) -> MagicMock:
        """Create a mock LlmRequest."""
        mock_request = MagicMock()
        mock_request.append_instructions = MagicMock()
        return mock_request

    @pytest.mark.asyncio
    async def test_persists_user_message(self) -> None:
        mock_client = self._make_mock_client()
        mock_response = MagicMock()
        mock_response.context = "Some context from Zep"
        mock_client.thread.add_messages.return_value = mock_response

        tool = self._make_tool(mock_client)
        tool_context = self._make_tool_context("Hi there")
        llm_request = self._make_llm_request()

        await tool.process_llm_request(tool_context=tool_context, llm_request=llm_request)

        # The turn path never provisions resources -- no user.add/thread.create.
        mock_client.user.add.assert_not_called()
        mock_client.thread.create.assert_not_called()

        # Should have called add_messages with return_context=True
        mock_client.thread.add_messages.assert_called_once()
        call_kwargs = mock_client.thread.add_messages.call_args[1]
        assert call_kwargs["thread_id"] == "test-thread"
        assert call_kwargs["return_context"] is True
        assert len(call_kwargs["messages"]) == 1
        assert call_kwargs["messages"][0].content == "Hi there"
        assert call_kwargs["messages"][0].role == "user"

    @pytest.mark.asyncio
    async def test_identity_fields_do_not_trigger_user_add(self) -> None:
        """Identity fields are used for message metadata only -- the turn
        path never calls user.add, even when name/email are present in state."""
        mock_client = self._make_mock_client()
        mock_response = MagicMock()
        mock_response.context = None
        mock_client.thread.add_messages.return_value = mock_response

        tool = self._make_tool(mock_client)
        tool_context = self._make_tool_context(
            "Hello",
            state={
                "zep_user_id": "test-user",
                "zep_thread_id": "test-thread",
                "zep_first_name": "Jane",
                "zep_last_name": "Smith",
            },
        )
        llm_request = self._make_llm_request()

        await tool.process_llm_request(tool_context=tool_context, llm_request=llm_request)

        mock_client.user.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_message_includes_user_display_name(self) -> None:
        mock_client = self._make_mock_client()
        mock_response = MagicMock()
        mock_response.context = None
        mock_client.thread.add_messages.return_value = mock_response

        tool = self._make_tool(mock_client)
        tool_context = self._make_tool_context(
            "Hello",
            state={
                "zep_user_id": "test-user",
                "zep_thread_id": "test-thread",
                "zep_first_name": "Jane",
                "zep_last_name": "Smith",
            },
        )
        llm_request = self._make_llm_request()

        await tool.process_llm_request(tool_context=tool_context, llm_request=llm_request)

        call_kwargs = mock_client.thread.add_messages.call_args[1]
        assert call_kwargs["messages"][0].name == "Jane Smith"

    @pytest.mark.asyncio
    async def test_message_name_defaults_when_no_identity(self) -> None:
        """When no name is in state, message name defaults to 'Anonymous User'."""
        mock_client = self._make_mock_client()
        mock_response = MagicMock()
        mock_response.context = None
        mock_client.thread.add_messages.return_value = mock_response

        tool = self._make_tool(mock_client)
        tool_context = self._make_tool_context("Hello")
        llm_request = self._make_llm_request()

        await tool.process_llm_request(tool_context=tool_context, llm_request=llm_request)

        call_kwargs = mock_client.thread.add_messages.call_args[1]
        assert call_kwargs["messages"][0].name == "Anonymous User"

    @pytest.mark.asyncio
    async def test_injects_context_into_llm_request(self) -> None:
        mock_client = self._make_mock_client()
        mock_response = MagicMock()
        mock_response.context = "User likes blue. User is a data scientist."
        mock_client.thread.add_messages.return_value = mock_response

        tool = self._make_tool(mock_client)
        tool_context = self._make_tool_context("What do you know about me?")
        llm_request = self._make_llm_request()

        await tool.process_llm_request(tool_context=tool_context, llm_request=llm_request)

        # Should have injected context into the LLM request
        llm_request.append_instructions.assert_called_once()
        instructions = llm_request.append_instructions.call_args[0][0]
        assert len(instructions) == 1
        assert "<ZEP_CONTEXT>" in instructions[0]
        assert "User likes blue" in instructions[0]

    @pytest.mark.asyncio
    async def test_no_context_when_response_is_empty(self) -> None:
        mock_client = self._make_mock_client()
        mock_response = MagicMock()
        mock_response.context = None
        mock_client.thread.add_messages.return_value = mock_response

        tool = self._make_tool(mock_client)
        tool_context = self._make_tool_context("Hello")
        llm_request = self._make_llm_request()

        await tool.process_llm_request(tool_context=tool_context, llm_request=llm_request)

        # Should NOT have injected any context
        llm_request.append_instructions.assert_not_called()

    @pytest.mark.asyncio
    async def test_same_turn_skips_repersist(self) -> None:
        """Within one ADK turn, process_llm_request fires multiple times
        with the same user_content object (tool-use loops).  The second
        call should be skipped."""
        mock_client = self._make_mock_client()
        mock_response = MagicMock()
        mock_response.context = "context"
        mock_client.thread.add_messages.return_value = mock_response

        tool = self._make_tool(mock_client)
        llm_request = self._make_llm_request()

        # Same user_content OBJECT passed twice (simulates intra-turn loop)
        tc = self._make_tool_context("Hello")
        await tool.process_llm_request(tool_context=tc, llm_request=llm_request)
        await tool.process_llm_request(tool_context=tc, llm_request=llm_request)

        # add_messages should only be called once
        assert mock_client.thread.add_messages.call_count == 1

    @pytest.mark.asyncio
    async def test_repeated_text_different_turns_persisted(self) -> None:
        """Same text sent in separate turns (different user_content objects)
        should be persisted both times — not silently dropped."""
        mock_client = self._make_mock_client()
        mock_response = MagicMock()
        mock_response.context = "context"
        mock_client.thread.add_messages.return_value = mock_response

        tool = self._make_tool(mock_client)
        llm_request = self._make_llm_request()

        # Two calls with the same text but different user_content objects
        tc1 = self._make_tool_context("yes")
        tc2 = self._make_tool_context("yes")

        await tool.process_llm_request(tool_context=tc1, llm_request=llm_request)
        await tool.process_llm_request(tool_context=tc2, llm_request=llm_request)

        # Both should be persisted (different turns)
        assert mock_client.thread.add_messages.call_count == 2

    @pytest.mark.asyncio
    async def test_failed_persist_allows_retry(self) -> None:
        """If the API call fails, the message should NOT be marked as
        persisted, so the next invocation can retry."""
        mock_client = self._make_mock_client()
        # First call fails, second succeeds
        mock_response = MagicMock()
        mock_response.context = "context"
        mock_client.thread.add_messages.side_effect = [
            RuntimeError("transient error"),
            mock_response,
        ]

        tool = self._make_tool(mock_client)
        llm_request = self._make_llm_request()

        # Same object — first call fails
        tc = self._make_tool_context("Hello")
        await tool.process_llm_request(tool_context=tc, llm_request=llm_request)

        # New turn with a new user_content object carrying the same text
        tc_retry = self._make_tool_context("Hello")
        await tool.process_llm_request(tool_context=tc_retry, llm_request=llm_request)

        # Should have attempted twice (first failed, second succeeded)
        assert mock_client.thread.add_messages.call_count == 2

    @pytest.mark.asyncio
    async def test_skips_empty_user_content(self) -> None:
        mock_client = self._make_mock_client()
        tool = self._make_tool(mock_client)
        llm_request = self._make_llm_request()

        # user_content with no parts
        tool_context = MagicMock()
        tool_context.user_content = None

        await tool.process_llm_request(tool_context=tool_context, llm_request=llm_request)

        mock_client.thread.add_messages.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_empty_text_parts(self) -> None:
        mock_client = self._make_mock_client()
        tool = self._make_tool(mock_client)
        llm_request = self._make_llm_request()

        # user_content with empty text
        mock_part = MagicMock()
        mock_part.text = ""
        mock_content = MagicMock()
        mock_content.parts = [mock_part]
        tool_context = MagicMock()
        tool_context.user_content = mock_content

        await tool.process_llm_request(tool_context=tool_context, llm_request=llm_request)

        mock_client.thread.add_messages.assert_not_called()

    @pytest.mark.asyncio
    async def test_joins_multiple_text_parts_from_user(self) -> None:
        """When user_content has multiple text parts, all are joined."""
        mock_client = self._make_mock_client()
        mock_response = MagicMock()
        mock_response.context = None
        mock_client.thread.add_messages.return_value = mock_response

        tool = self._make_tool(mock_client)
        llm_request = self._make_llm_request()

        # Create user_content with multiple text parts
        part1 = MagicMock()
        part1.text = "Hello"
        part2 = MagicMock()
        part2.text = "Also, remind me about tomorrow"
        mock_content = MagicMock()
        mock_content.parts = [part1, part2]

        tc = MagicMock()
        tc.user_content = mock_content
        tc.state = {"zep_thread_id": "test-thread"}
        tc.session.id = "test-session"
        tc.user_id = "test-user"

        await tool.process_llm_request(tool_context=tc, llm_request=llm_request)

        call_kwargs = mock_client.thread.add_messages.call_args[1]
        assert call_kwargs["messages"][0].content == "Hello Also, remind me about tomorrow"

    @pytest.mark.asyncio
    async def test_skips_non_text_parts_from_user(self) -> None:
        """Non-text parts (images, files) are ignored; text parts extracted."""
        mock_client = self._make_mock_client()
        mock_response = MagicMock()
        mock_response.context = None
        mock_client.thread.add_messages.return_value = mock_response

        tool = self._make_tool(mock_client)
        llm_request = self._make_llm_request()

        # Part without text attribute (simulating inline_data/image)
        image_part = MagicMock(spec=["inline_data"])  # no "text" attr
        text_part = MagicMock()
        text_part.text = "Describe this image"
        mock_content = MagicMock()
        mock_content.parts = [image_part, text_part]

        tc = MagicMock()
        tc.user_content = mock_content
        tc.state = {"zep_thread_id": "test-thread"}
        tc.session.id = "test-session"
        tc.user_id = "test-user"

        await tool.process_llm_request(tool_context=tc, llm_request=llm_request)

        call_kwargs = mock_client.thread.add_messages.call_args[1]
        assert call_kwargs["messages"][0].content == "Describe this image"

    @pytest.mark.asyncio
    async def test_skips_when_only_non_text_parts(self) -> None:
        """If user_content has only non-text parts, skip entirely."""
        mock_client = self._make_mock_client()
        tool = self._make_tool(mock_client)
        llm_request = self._make_llm_request()

        image_part = MagicMock(spec=["inline_data"])
        mock_content = MagicMock()
        mock_content.parts = [image_part]

        tc = MagicMock()
        tc.user_content = mock_content

        await tool.process_llm_request(tool_context=tc, llm_request=llm_request)

        mock_client.thread.add_messages.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_zep_api_error_gracefully(self) -> None:
        mock_client = self._make_mock_client()
        mock_client.thread.add_messages.side_effect = RuntimeError("API error")

        tool = self._make_tool(mock_client)
        tool_context = self._make_tool_context("Hello")
        llm_request = self._make_llm_request()

        # Should not raise
        await tool.process_llm_request(tool_context=tool_context, llm_request=llm_request)

        # Should not have injected context
        llm_request.append_instructions.assert_not_called()

    @pytest.mark.asyncio
    async def test_thread_id_uses_session_fallback(self) -> None:
        """When zep_thread_id is not in state, session ID should be used."""
        mock_client = self._make_mock_client()
        mock_response = MagicMock()
        mock_response.context = None
        mock_client.thread.add_messages.return_value = mock_response

        tool = self._make_tool(mock_client)
        tc = self._make_tool_context(
            "Hello",
            state={},  # no zep_thread_id — falls back to session ID
            session_id="my-session-id",
            session_user_id="test-user",
        )
        llm_request = self._make_llm_request()

        await tool.process_llm_request(tool_context=tc, llm_request=llm_request)

        # No provisioning on the turn path -- just confirm the session-id
        # fallback flows through to the persistence call.
        mock_client.thread.create.assert_not_called()
        call_kwargs = mock_client.thread.add_messages.call_args[1]
        assert call_kwargs["thread_id"] == "my-session-id"

    @pytest.mark.asyncio
    async def test_missing_user_id_degrades_gracefully(self) -> None:
        """When neither state nor session has user_id, should log and skip, not crash."""
        mock_client = self._make_mock_client()
        tool = self._make_tool(mock_client)

        # No zep_user_id in state AND no session user_id (session attrs raise)
        mock_part = MagicMock()
        mock_part.text = "Hello"
        mock_content = MagicMock()
        mock_content.parts = [mock_part]
        tc = MagicMock(spec=["user_content", "state"])
        tc.user_content = mock_content
        tc.state = {}
        # tc has no user_id/session attrs (spec restricts them), simulating
        # an ADK context where the public identity properties are absent.

        llm_request = self._make_llm_request()

        # Should NOT raise — should gracefully skip
        await tool.process_llm_request(tool_context=tc, llm_request=llm_request)

        # Should not have called any Zep APIs
        mock_client.user.add.assert_not_called()
        mock_client.thread.create.assert_not_called()
        mock_client.thread.add_messages.assert_not_called()
        llm_request.append_instructions.assert_not_called()

    @pytest.mark.asyncio
    async def test_context_builder_called_instead_of_return_context(self) -> None:
        """When context_builder is set, add_messages should NOT use return_context."""
        from zep_adk import ContextInput, ZepContextTool

        mock_client = self._make_mock_client()
        mock_client.thread.add_messages.return_value = None  # no return_context

        captured: list[ContextInput] = []

        async def custom_builder(ctx: ContextInput) -> str | None:
            captured.append(ctx)
            return "Custom context from builder"

        tool = ZepContextTool(
            zep_client=mock_client,
            context_builder=custom_builder,
        )
        tc = self._make_tool_context("Hello")
        llm_request = self._make_llm_request()

        await tool.process_llm_request(tool_context=tc, llm_request=llm_request)

        # add_messages should have been called WITHOUT return_context
        call_kwargs = mock_client.thread.add_messages.call_args[1]
        assert "return_context" not in call_kwargs

        # context_builder should have been called once with a fully-populated
        # ContextInput.
        assert len(captured) == 1
        ctx = captured[0]
        assert ctx.zep is mock_client
        assert ctx.user_id == "test-user"
        assert ctx.thread_id == "test-thread"
        assert ctx.user_message == "Hello"
        assert ctx.tool_context is tc
        assert ctx.llm_request is llm_request

    @pytest.mark.asyncio
    async def test_context_builder_result_injected_into_prompt(self) -> None:
        """The string returned by context_builder should appear in LLM instructions."""
        from zep_adk import ContextInput, ZepContextTool

        mock_client = self._make_mock_client()
        mock_client.thread.add_messages.return_value = None

        async def custom_builder(ctx: ContextInput) -> str | None:
            return "User works at Acme Corp as CTO"

        tool = ZepContextTool(zep_client=mock_client, context_builder=custom_builder)
        tc = self._make_tool_context("Tell me about myself")
        llm_request = self._make_llm_request()

        await tool.process_llm_request(tool_context=tc, llm_request=llm_request)

        llm_request.append_instructions.assert_called_once()
        instructions = llm_request.append_instructions.call_args[0][0]
        assert "User works at Acme Corp as CTO" in instructions[0]
        assert "<ZEP_CONTEXT>" in instructions[0]

    @pytest.mark.asyncio
    async def test_context_builder_returning_none_skips_injection(self) -> None:
        """When context_builder returns None, no context should be injected,
        but persistence still happens."""
        from zep_adk import ContextInput, ZepContextTool

        mock_client = self._make_mock_client()
        mock_client.thread.add_messages.return_value = None

        async def custom_builder(ctx: ContextInput) -> str | None:
            return None

        tool = ZepContextTool(zep_client=mock_client, context_builder=custom_builder)
        tc = self._make_tool_context("Hello")
        llm_request = self._make_llm_request()

        await tool.process_llm_request(tool_context=tc, llm_request=llm_request)

        llm_request.append_instructions.assert_not_called()
        mock_client.thread.add_messages.assert_called_once()

    @pytest.mark.asyncio
    async def test_context_builder_returning_empty_string_skips_injection(self) -> None:
        """Empty-string builder result also skips injection; persist still happens."""
        from zep_adk import ContextInput, ZepContextTool

        mock_client = self._make_mock_client()
        mock_client.thread.add_messages.return_value = None

        async def custom_builder(ctx: ContextInput) -> str | None:
            return ""

        tool = ZepContextTool(zep_client=mock_client, context_builder=custom_builder)
        tc = self._make_tool_context("Hello")
        llm_request = self._make_llm_request()

        await tool.process_llm_request(tool_context=tc, llm_request=llm_request)

        llm_request.append_instructions.assert_not_called()
        mock_client.thread.add_messages.assert_called_once()

    @pytest.mark.asyncio
    async def test_context_builder_and_persist_both_called(self) -> None:
        """Both add_messages and context_builder should be called (parallel execution)."""
        from zep_adk import ContextInput, ZepContextTool

        mock_client = self._make_mock_client()
        mock_client.thread.add_messages.return_value = None

        calls = []

        async def custom_builder(ctx: ContextInput) -> str | None:
            calls.append(ctx)
            return "context"

        tool = ZepContextTool(zep_client=mock_client, context_builder=custom_builder)
        tc = self._make_tool_context("Hello")
        llm_request = self._make_llm_request()

        await tool.process_llm_request(tool_context=tc, llm_request=llm_request)

        # Both should have been called
        mock_client.thread.add_messages.assert_called_once()
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_default_path_uses_return_context(self) -> None:
        """Without context_builder, add_messages should use return_context=True."""
        mock_client = self._make_mock_client()
        mock_response = MagicMock()
        mock_response.context = "default context"
        mock_client.thread.add_messages.return_value = mock_response

        tool = self._make_tool(mock_client)
        tc = self._make_tool_context("Hello")
        llm_request = self._make_llm_request()

        await tool.process_llm_request(tool_context=tc, llm_request=llm_request)

        call_kwargs = mock_client.thread.add_messages.call_args[1]
        assert call_kwargs["return_context"] is True

    @pytest.mark.asyncio
    async def test_context_builder_error_handled_gracefully(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When context_builder raises, a warning is logged, injection is
        skipped, but persistence still completes and the turn is marked as
        persisted (dedup) on success."""
        from zep_adk import ContextInput, ZepContextTool

        mock_client = self._make_mock_client()
        mock_client.thread.add_messages.return_value = None

        async def custom_builder(ctx: ContextInput) -> str | None:
            raise RuntimeError("builder failed")

        tool = ZepContextTool(zep_client=mock_client, context_builder=custom_builder)
        tc = self._make_tool_context("Hello")
        llm_request = self._make_llm_request()

        with caplog.at_level("WARNING"):
            await tool.process_llm_request(tool_context=tc, llm_request=llm_request)

        llm_request.append_instructions.assert_not_called()
        mock_client.thread.add_messages.assert_called_once()
        assert any("context_builder" in r.message for r in caplog.records)

        # Persist succeeded, so the turn should be marked as persisted --
        # a second call with the SAME user_content object must be skipped.
        await tool.process_llm_request(tool_context=tc, llm_request=llm_request)
        assert mock_client.thread.add_messages.call_count == 1

    @pytest.mark.asyncio
    async def test_persist_raises_builder_succeeds_not_marked_persisted(self) -> None:
        """If persist raises while the builder succeeds, warn and do
        NOT mark the turn as persisted (so a retry is possible), but the
        builder's context may still be injected."""
        from zep_adk import ContextInput, ZepContextTool

        mock_client = self._make_mock_client()
        mock_client.thread.add_messages.side_effect = RuntimeError("persist failed")

        async def custom_builder(ctx: ContextInput) -> str | None:
            return "context despite persist failure"

        tool = ZepContextTool(zep_client=mock_client, context_builder=custom_builder)
        tc = self._make_tool_context("Hello")
        llm_request = self._make_llm_request()

        await tool.process_llm_request(tool_context=tc, llm_request=llm_request)

        # Builder result is still injected even though persistence failed.
        llm_request.append_instructions.assert_called_once()
        instructions = llm_request.append_instructions.call_args[0][0]
        assert "context despite persist failure" in instructions[0]

        # Not marked as persisted -- a retry with the SAME user_content
        # object must attempt add_messages again.
        assert mock_client.thread.add_messages.call_count == 1
        await tool.process_llm_request(tool_context=tc, llm_request=llm_request)
        assert mock_client.thread.add_messages.call_count == 2

    @pytest.mark.asyncio
    async def test_context_builder_type_exported(self) -> None:
        """ContextBuilder and ContextInput should be importable from the package."""
        from zep_adk import ContextBuilder, ContextInput

        assert ContextBuilder is not None
        assert ContextInput is not None

    @pytest.mark.asyncio
    async def test_default_template_used_when_none_provided(self) -> None:
        """The default injection template contains <ZEP_CONTEXT> tags."""
        from zep_adk import DEFAULT_CONTEXT_TEMPLATE

        assert "<ZEP_CONTEXT>" in DEFAULT_CONTEXT_TEMPLATE
        assert "{context}" in DEFAULT_CONTEXT_TEMPLATE

    @pytest.mark.asyncio
    async def test_custom_context_template_respected(self) -> None:
        """A custom context_template is used instead of the default."""
        from zep_adk import ZepContextTool

        mock_client = self._make_mock_client()
        mock_response = MagicMock()
        mock_response.context = "some facts"
        mock_client.thread.add_messages.return_value = mock_response

        tool = ZepContextTool(
            zep_client=mock_client,
            context_template="MEMORY START\n{context}\nMEMORY END",
        )
        tc = self._make_tool_context("Hello")
        llm_request = self._make_llm_request()

        await tool.process_llm_request(tool_context=tc, llm_request=llm_request)

        instructions = llm_request.append_instructions.call_args[0][0]
        assert instructions[0] == "MEMORY START\nsome facts\nMEMORY END"
        assert "<ZEP_CONTEXT>" not in instructions[0]

    @pytest.mark.asyncio
    async def test_context_template_uses_plain_replace_not_format(self) -> None:
        """Context text (or a template) containing %, {}, or a literal
        '{context}' must be injected safely via str.replace, never
        str.format -- which would raise or double-substitute."""
        mock_client = self._make_mock_client()
        mock_response = MagicMock()
        tricky_context = "50% off {unrelated} and a literal {context} marker"
        mock_response.context = tricky_context
        mock_client.thread.add_messages.return_value = mock_response

        tool = self._make_tool(mock_client)
        tc = self._make_tool_context("Hello")
        llm_request = self._make_llm_request()

        # Should not raise (str.format would raise KeyError on {unrelated}).
        await tool.process_llm_request(tool_context=tc, llm_request=llm_request)

        instructions = llm_request.append_instructions.call_args[0][0]
        # The tricky context appears verbatim exactly once, in the expected slot.
        assert instructions[0].count(tricky_context) == 1

    @pytest.mark.asyncio
    async def test_not_found_logs_warning_naming_ensure_helpers(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Persistence NotFound logs a warning naming
        ``ensure_user``/``ensure_thread``, and the callback completes without
        raising (warn-and-continue, never crash)."""
        from zep_cloud.errors import NotFoundError

        mock_client = self._make_mock_client()
        mock_client.thread.add_messages.side_effect = NotFoundError(
            body={"message": "user not found"}
        )

        tool = self._make_tool(mock_client)
        tc = self._make_tool_context("Hello")
        llm_request = self._make_llm_request()

        with caplog.at_level("WARNING"):
            await tool.process_llm_request(tool_context=tc, llm_request=llm_request)

        assert any(
            "ensure_user" in record.message and "ensure_thread" in record.message
            for record in caplog.records
        )
        llm_request.append_instructions.assert_not_called()

    @pytest.mark.asyncio
    async def test_turn_path_never_calls_user_add_or_thread_create(self) -> None:
        """process_llm_request must never call user.add or
        thread.create -- provisioning is entirely out-of-band now."""
        mock_client = self._make_mock_client()
        mock_response = MagicMock()
        mock_response.context = None
        mock_client.thread.add_messages.return_value = mock_response

        tool = self._make_tool(mock_client)
        llm_request = self._make_llm_request()

        tc1 = self._make_tool_context("Message 1")
        await tool.process_llm_request(tool_context=tc1, llm_request=llm_request)
        tc2 = self._make_tool_context(
            "Message 2",
            state={"zep_user_id": "user-B", "zep_thread_id": "thread-B"},
        )
        await tool.process_llm_request(tool_context=tc2, llm_request=llm_request)

        mock_client.user.add.assert_not_called()
        mock_client.thread.create.assert_not_called()


class TestAfterModelCallback:
    """Test create_after_model_callback."""

    def _make_mock_client(self) -> MagicMock:
        mock_client = MagicMock()
        mock_client.thread = MagicMock()
        mock_client.thread.add_messages = AsyncMock()
        return mock_client

    def _make_llm_response(self, text: str = "Hello from the assistant") -> MagicMock:
        mock_part = MagicMock()
        mock_part.text = text
        mock_part.function_call = None  # pure text response, no tool call
        mock_content = MagicMock()
        mock_content.parts = [mock_part]
        mock_response = MagicMock()
        mock_response.content = mock_content
        return mock_response

    def _make_callback_context(
        self,
        state: dict | None = None,
        session_id: str = "test-session",
    ) -> MagicMock:
        """Create a mock CallbackContext with state and session ID."""
        mock_ctx = MagicMock()
        mock_ctx.state = state if state is not None else {"zep_thread_id": "test-thread"}
        mock_ctx.session.id = session_id
        return mock_ctx

    def test_returns_callable(self) -> None:
        from zep_adk import create_after_model_callback

        mock_client = MagicMock()
        callback = create_after_model_callback(zep_client=mock_client)
        assert callable(callback)

    @pytest.mark.asyncio
    async def test_persists_assistant_response(self) -> None:
        from zep_adk import create_after_model_callback

        mock_client = self._make_mock_client()
        callback = create_after_model_callback(zep_client=mock_client)

        llm_response = self._make_llm_response("This is the answer.")
        callback_context = self._make_callback_context()

        result = await callback(callback_context, llm_response)

        # Should return None (pass-through)
        assert result is None

        # Should have persisted the message
        mock_client.thread.add_messages.assert_called_once()
        call_kwargs = mock_client.thread.add_messages.call_args[1]
        assert call_kwargs["thread_id"] == "test-thread"
        assert len(call_kwargs["messages"]) == 1
        assert call_kwargs["messages"][0].content == "This is the answer."
        assert call_kwargs["messages"][0].role == "assistant"
        assert call_kwargs["messages"][0].name == "Assistant"

    @pytest.mark.asyncio
    async def test_persists_with_custom_assistant_name(self) -> None:
        from zep_adk import create_after_model_callback

        mock_client = self._make_mock_client()
        callback = create_after_model_callback(
            zep_client=mock_client,
            assistant_name="My Bot",
        )

        llm_response = self._make_llm_response("Hello!")
        callback_context = self._make_callback_context()

        await callback(callback_context, llm_response)

        call_kwargs = mock_client.thread.add_messages.call_args[1]
        assert call_kwargs["messages"][0].name == "My Bot"

    @pytest.mark.asyncio
    async def test_resolves_thread_from_state(self) -> None:
        from zep_adk import create_after_model_callback

        mock_client = self._make_mock_client()
        callback = create_after_model_callback(zep_client=mock_client)

        llm_response = self._make_llm_response("Response")
        callback_context = self._make_callback_context(state={"zep_thread_id": "state-thread-42"})

        await callback(callback_context, llm_response)

        call_kwargs = mock_client.thread.add_messages.call_args[1]
        assert call_kwargs["thread_id"] == "state-thread-42"

    @pytest.mark.asyncio
    async def test_thread_falls_back_to_session_id(self) -> None:
        from zep_adk import create_after_model_callback

        mock_client = self._make_mock_client()
        callback = create_after_model_callback(zep_client=mock_client)

        llm_response = self._make_llm_response("Response")
        # No zep_thread_id in state
        callback_context = self._make_callback_context(state={}, session_id="session-fallback-99")

        await callback(callback_context, llm_response)

        call_kwargs = mock_client.thread.add_messages.call_args[1]
        assert call_kwargs["thread_id"] == "session-fallback-99"

    @pytest.mark.asyncio
    async def test_skips_when_no_thread_available(self) -> None:
        from zep_adk import create_after_model_callback

        mock_client = self._make_mock_client()
        callback = create_after_model_callback(zep_client=mock_client)

        llm_response = self._make_llm_response("Response")
        callback_context = MagicMock(spec=["state"])
        callback_context.state = {}
        # callback_context has no session attr (spec restricts it), simulating
        # missing session ID access via the public property.

        result = await callback(callback_context, llm_response)

        assert result is None
        mock_client.thread.add_messages.assert_not_called()

    @pytest.mark.asyncio
    async def test_persists_every_response_no_dedup(self) -> None:
        """The callback has no dedup — every LLM response is persisted,
        even if the text is identical (each is a genuine model turn)."""
        from zep_adk import create_after_model_callback

        mock_client = self._make_mock_client()
        callback = create_after_model_callback(zep_client=mock_client)

        llm_response = self._make_llm_response("Same response text")
        callback_context = self._make_callback_context()

        await callback(callback_context, llm_response)
        await callback(callback_context, llm_response)

        # Both should be persisted
        assert mock_client.thread.add_messages.call_count == 2

    @pytest.mark.asyncio
    async def test_skips_empty_response(self) -> None:
        from zep_adk import create_after_model_callback

        mock_client = self._make_mock_client()
        callback = create_after_model_callback(zep_client=mock_client)

        callback_context = self._make_callback_context()

        # None response
        result = await callback(callback_context, None)
        assert result is None
        mock_client.thread.add_messages.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_response_with_no_text(self) -> None:
        from zep_adk import create_after_model_callback

        mock_client = self._make_mock_client()
        callback = create_after_model_callback(zep_client=mock_client)

        callback_context = self._make_callback_context()

        # Response with no text parts and no function call
        mock_part = MagicMock()
        mock_part.text = None
        mock_part.function_call = None
        mock_content = MagicMock()
        mock_content.parts = [mock_part]
        mock_response = MagicMock()
        mock_response.content = mock_content

        result = await callback(callback_context, mock_response)
        assert result is None
        mock_client.thread.add_messages.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_zep_api_error_gracefully(self) -> None:
        from zep_adk import create_after_model_callback

        mock_client = self._make_mock_client()
        mock_client.thread.add_messages.side_effect = RuntimeError("API error")

        callback = create_after_model_callback(zep_client=mock_client)

        llm_response = self._make_llm_response("Response text")
        callback_context = self._make_callback_context()

        # Should not raise
        result = await callback(callback_context, llm_response)
        assert result is None

    @pytest.mark.asyncio
    async def test_skips_intermediate_tool_call_response(self) -> None:
        """When the model response contains a function_call part (intermediate
        tool-use step), the callback should NOT persist it to Zep."""
        from zep_adk import create_after_model_callback

        mock_client = self._make_mock_client()
        callback = create_after_model_callback(zep_client=mock_client)

        # Simulate response with text + function_call
        text_part = MagicMock()
        text_part.text = "Let me look that up."
        text_part.function_call = None
        fc_part = MagicMock()
        fc_part.text = None
        fc_part.function_call = MagicMock()  # truthy — indicates a tool call
        mock_content = MagicMock()
        mock_content.parts = [text_part, fc_part]
        mock_response = MagicMock()
        mock_response.content = mock_content

        callback_context = self._make_callback_context()
        result = await callback(callback_context, mock_response)

        assert result is None
        mock_client.thread.add_messages.assert_not_called()

    @pytest.mark.asyncio
    async def test_persists_final_text_only_response(self) -> None:
        """A pure text response (no function_call) IS persisted."""
        from zep_adk import create_after_model_callback

        mock_client = self._make_mock_client()
        callback = create_after_model_callback(zep_client=mock_client)

        # Simulate response with only text parts, no function_call
        text_part = MagicMock()
        text_part.text = "Here is your answer."
        text_part.function_call = None
        mock_content = MagicMock()
        mock_content.parts = [text_part]
        mock_response = MagicMock()
        mock_response.content = mock_content

        callback_context = self._make_callback_context()
        await callback(callback_context, mock_response)

        mock_client.thread.add_messages.assert_called_once()
        call_kwargs = mock_client.thread.add_messages.call_args[1]
        assert call_kwargs["messages"][0].content == "Here is your answer."

    @pytest.mark.asyncio
    async def test_joins_multiple_text_parts(self) -> None:
        from zep_adk import create_after_model_callback

        mock_client = self._make_mock_client()
        callback = create_after_model_callback(zep_client=mock_client)

        # Response with multiple text parts, no function_call
        mock_part1 = MagicMock()
        mock_part1.text = "Part one."
        mock_part1.function_call = None
        mock_part2 = MagicMock()
        mock_part2.text = "Part two."
        mock_part2.function_call = None
        mock_content = MagicMock()
        mock_content.parts = [mock_part1, mock_part2]
        mock_response = MagicMock()
        mock_response.content = mock_content

        callback_context = self._make_callback_context()
        await callback(callback_context, mock_response)

        call_kwargs = mock_client.thread.add_messages.call_args[1]
        assert call_kwargs["messages"][0].content == "Part one. Part two."

    @pytest.mark.asyncio
    async def test_truncates_oversize_assistant_message(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Assistant responses over Zep's per-message limit are
        truncated (never dropped) before being persisted, and a warning
        logs both the original and truncated lengths -- never content."""
        from zep_adk import create_after_model_callback
        from zep_adk.limits import MESSAGE_CONTENT_MAX, MESSAGE_CONTENT_TRUNCATE_TO

        mock_client = self._make_mock_client()
        callback = create_after_model_callback(zep_client=mock_client)

        oversize_text = "x" * (MESSAGE_CONTENT_MAX + 500)
        response = self._make_llm_response(oversize_text)
        callback_context = self._make_callback_context()

        with caplog.at_level("WARNING"):
            await callback(callback_context, response)

        call_kwargs = mock_client.thread.add_messages.call_args[1]
        persisted_content = call_kwargs["messages"][0].content
        assert len(persisted_content) == MESSAGE_CONTENT_TRUNCATE_TO
        assert persisted_content == oversize_text[:MESSAGE_CONTENT_TRUNCATE_TO]

        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert any(str(MESSAGE_CONTENT_MAX + 500) in r.message for r in warnings)
        assert any(str(MESSAGE_CONTENT_TRUNCATE_TO) in r.message for r in warnings)
        # Never log the actual message content.
        assert not any(oversize_text in r.message for r in warnings)

    @pytest.mark.asyncio
    async def test_assistant_message_at_limit_untouched(self) -> None:
        """Content exactly at the limit is persisted unchanged."""
        from zep_adk import create_after_model_callback
        from zep_adk.limits import MESSAGE_CONTENT_MAX

        mock_client = self._make_mock_client()
        callback = create_after_model_callback(zep_client=mock_client)

        exact_text = "y" * MESSAGE_CONTENT_MAX
        response = self._make_llm_response(exact_text)
        callback_context = self._make_callback_context()

        await callback(callback_context, response)

        call_kwargs = mock_client.thread.add_messages.call_args[1]
        assert call_kwargs["messages"][0].content == exact_text


# ======================================================================
# limits.py -- message truncation
# ======================================================================


class TestTruncateMessageContent:
    """Unit tests for zep_adk.limits.truncate_message_content."""

    def test_content_within_limit_untouched(self) -> None:
        from zep_adk.limits import MESSAGE_CONTENT_MAX, truncate_message_content

        content = "a" * (MESSAGE_CONTENT_MAX - 1)
        assert truncate_message_content(content) == content

    def test_content_at_limit_untouched(self) -> None:
        from zep_adk.limits import MESSAGE_CONTENT_MAX, truncate_message_content

        content = "a" * MESSAGE_CONTENT_MAX
        assert truncate_message_content(content) == content

    def test_content_over_limit_truncated(self) -> None:
        from zep_adk.limits import MESSAGE_CONTENT_TRUNCATE_TO, truncate_message_content

        content = "a" * (MESSAGE_CONTENT_TRUNCATE_TO + 1000)
        result = truncate_message_content(content)
        assert len(result) == MESSAGE_CONTENT_TRUNCATE_TO
        assert result == content[:MESSAGE_CONTENT_TRUNCATE_TO]

    def test_over_limit_logs_warning_with_lengths_only(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        from zep_adk.limits import (
            MESSAGE_CONTENT_MAX,
            MESSAGE_CONTENT_TRUNCATE_TO,
            truncate_message_content,
        )

        original_len = MESSAGE_CONTENT_MAX + 42
        content = "s" * original_len

        with caplog.at_level("WARNING"):
            truncate_message_content(content, label="user")

        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1
        assert str(original_len) in warnings[0].message
        assert str(MESSAGE_CONTENT_TRUNCATE_TO) in warnings[0].message
        assert content not in warnings[0].message

    def test_within_limit_does_not_log(self, caplog: pytest.LogCaptureFixture) -> None:
        from zep_adk.limits import truncate_message_content

        with caplog.at_level("WARNING"):
            truncate_message_content("short content")

        assert len(caplog.records) == 0


class TestZepContextToolTruncatesUserMessages:
    """ZepContextTool truncates over-limit user message content."""

    def _make_tool_context(self, text: str) -> MagicMock:
        mock_part = MagicMock()
        mock_part.text = text
        mock_content = MagicMock()
        mock_content.parts = [mock_part]

        mock_tc = MagicMock()
        mock_tc.user_content = mock_content
        mock_tc.state = {"zep_thread_id": "test-thread"}
        mock_tc.session.id = "test-session"
        mock_tc.user_id = "test-user"
        return mock_tc

    def _make_mock_client(self) -> MagicMock:
        mock_client = MagicMock()
        mock_client.thread = MagicMock()
        mock_client.thread.add_messages = AsyncMock()
        return mock_client

    def _make_llm_request(self) -> MagicMock:
        mock_request = MagicMock()
        mock_request.append_instructions = MagicMock()
        return mock_request

    @pytest.mark.asyncio
    async def test_oversize_user_message_truncated_before_persist(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        from zep_adk import ZepContextTool
        from zep_adk.limits import MESSAGE_CONTENT_MAX, MESSAGE_CONTENT_TRUNCATE_TO

        mock_client = self._make_mock_client()
        mock_response = MagicMock()
        mock_response.context = None
        mock_client.thread.add_messages.return_value = mock_response

        tool = ZepContextTool(zep_client=mock_client)
        oversize_text = "z" * (MESSAGE_CONTENT_MAX + 1)
        tc = self._make_tool_context(oversize_text)
        llm_request = self._make_llm_request()

        with caplog.at_level("WARNING"):
            await tool.process_llm_request(tool_context=tc, llm_request=llm_request)

        call_kwargs = mock_client.thread.add_messages.call_args[1]
        persisted_content = call_kwargs["messages"][0].content
        assert len(persisted_content) == MESSAGE_CONTENT_TRUNCATE_TO
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert any(str(MESSAGE_CONTENT_MAX + 1) in r.message for r in warnings)

    @pytest.mark.asyncio
    async def test_user_message_at_limit_untouched(self) -> None:
        from zep_adk import ZepContextTool
        from zep_adk.limits import MESSAGE_CONTENT_MAX

        mock_client = self._make_mock_client()
        mock_response = MagicMock()
        mock_response.context = None
        mock_client.thread.add_messages.return_value = mock_response

        tool = ZepContextTool(zep_client=mock_client)
        exact_text = "w" * MESSAGE_CONTENT_MAX
        tc = self._make_tool_context(exact_text)
        llm_request = self._make_llm_request()

        await tool.process_llm_request(tool_context=tc, llm_request=llm_request)

        call_kwargs = mock_client.thread.add_messages.call_args[1]
        assert call_kwargs["messages"][0].content == exact_text


# ======================================================================
# ZepGraphSearchTool tests
# ======================================================================


class TestGraphSearchToolInit:
    """Test ZepGraphSearchTool initialisation and validation."""

    def test_basic_init(self) -> None:
        from zep_adk import ZepGraphSearchTool

        mock_client = MagicMock()
        tool = ZepGraphSearchTool(zep_client=mock_client)
        assert tool.name == "zep_graph_search"
        assert tool._graph_id is None
        assert tool._pinned == {}

    def test_custom_name_and_description(self) -> None:
        from zep_adk import ZepGraphSearchTool

        tool = ZepGraphSearchTool(
            zep_client=MagicMock(),
            name="search_docs",
            description="Search documentation.",
        )
        assert tool.name == "search_docs"
        assert tool.description == "Search documentation."

    def test_graph_id_stored(self) -> None:
        from zep_adk import ZepGraphSearchTool

        tool = ZepGraphSearchTool(zep_client=MagicMock(), graph_id="docs-123")
        assert tool._graph_id == "docs-123"

    def test_pinned_params_stored(self) -> None:
        from zep_adk import ZepGraphSearchTool

        tool = ZepGraphSearchTool(
            zep_client=MagicMock(),
            scope="edges",
            limit=5,
            reranker="cross_encoder",
        )
        assert tool._pinned["scope"] == "edges"
        assert tool._pinned["limit"] == 5
        assert tool._pinned["reranker"] == "cross_encoder"

    def test_search_filters_stored_as_pinned(self) -> None:
        from zep_adk import ZepGraphSearchTool

        filters = {"node_labels": ["Person"]}
        tool = ZepGraphSearchTool(zep_client=MagicMock(), search_filters=filters)
        assert tool._pinned["search_filters"] == filters

    def test_bfs_origin_stored_as_pinned(self) -> None:
        from zep_adk import ZepGraphSearchTool

        uuids = ["uuid-1", "uuid-2"]
        tool = ZepGraphSearchTool(zep_client=MagicMock(), bfs_origin_node_uuids=uuids)
        assert tool._pinned["bfs_origin_node_uuids"] == uuids

    def test_rejects_user_id_pinning(self) -> None:
        from zep_adk import ZepGraphSearchTool

        with pytest.raises(ValueError, match="user_id.*cannot be pinned"):
            ZepGraphSearchTool(zep_client=MagicMock(), user_id="bad")

    def test_allows_query_pinning(self) -> None:
        from zep_adk import ZepGraphSearchTool

        tool = ZepGraphSearchTool(zep_client=MagicMock(), query="fixed search")
        assert tool._pinned["query"] == "fixed search"

    def test_rejects_unknown_params(self) -> None:
        from zep_adk import ZepGraphSearchTool

        with pytest.raises(ValueError, match="Unknown pinned parameters"):
            ZepGraphSearchTool(zep_client=MagicMock(), bogus_param="bad")


class TestGraphSearchToolDeclaration:
    """Test dynamic schema generation based on pinned params."""

    def test_all_params_exposed_by_default(self) -> None:
        from zep_adk import ZepGraphSearchTool

        tool = ZepGraphSearchTool(zep_client=MagicMock())
        decl = tool._get_declaration()
        assert decl is not None
        props = decl.parameters.properties
        # query + all _SEARCH_PARAMS
        assert "query" in props
        assert "scope" in props
        assert "reranker" in props
        assert "limit" in props
        assert "mmr_lambda" in props
        assert "center_node_uuid" in props

    def test_scope_enum_includes_all_supported_scopes(self) -> None:
        from zep_adk import ZepGraphSearchTool

        tool = ZepGraphSearchTool(zep_client=MagicMock())
        decl = tool._get_declaration()
        props = decl.parameters.properties
        assert list(props["scope"].enum) == [
            "edges",
            "nodes",
            "episodes",
            "observations",
            "thread_summaries",
            "auto",
        ]

    def test_pinned_params_hidden_from_schema(self) -> None:
        from zep_adk import ZepGraphSearchTool

        tool = ZepGraphSearchTool(
            zep_client=MagicMock(),
            scope="edges",
            reranker="rrf",
        )
        decl = tool._get_declaration()
        props = decl.parameters.properties
        assert "query" in props
        assert "scope" not in props
        assert "reranker" not in props
        # unpinned params still visible
        assert "limit" in props
        assert "mmr_lambda" in props

    def test_query_always_required(self) -> None:
        from zep_adk import ZepGraphSearchTool

        tool = ZepGraphSearchTool(zep_client=MagicMock())
        decl = tool._get_declaration()
        assert "query" in decl.parameters.required

    def test_all_pinned_leaves_empty_schema(self) -> None:
        from zep_adk import ZepGraphSearchTool

        tool = ZepGraphSearchTool(
            zep_client=MagicMock(),
            query="fixed",
            scope="edges",
            reranker="rrf",
            limit=10,
            mmr_lambda=0.5,
            center_node_uuid="uuid-1",
        )
        decl = tool._get_declaration()
        props = decl.parameters.properties
        assert len(props) == 0


class TestGraphSearchToolExecution:
    """Test run_async with mocked Zep client."""

    @staticmethod
    def _make_tool_context(
        state: dict | None = None,
        session_user_id: str | None = "fallback-user",
    ) -> MagicMock:
        mock_tc = MagicMock()
        mock_tc.state = state if state is not None else {}
        mock_tc.user_id = session_user_id
        return mock_tc

    @staticmethod
    def _make_search_result(edges=None, nodes=None, episodes=None):
        result = MagicMock()
        result.edges = edges
        result.nodes = nodes
        result.episodes = episodes
        return result

    @pytest.mark.asyncio
    async def test_resolves_user_id_from_session(self) -> None:
        from zep_adk import ZepGraphSearchTool

        mock_client = MagicMock()
        mock_client.graph.search = AsyncMock(return_value=self._make_search_result())

        tool = ZepGraphSearchTool(zep_client=mock_client)
        tc = self._make_tool_context(session_user_id="user-42")

        await tool.run_async(args={"query": "test"}, tool_context=tc)

        call_kwargs = mock_client.graph.search.call_args[1]
        assert call_kwargs["user_id"] == "user-42"
        assert "graph_id" not in call_kwargs

    @pytest.mark.asyncio
    async def test_resolves_user_id_from_state_override(self) -> None:
        from zep_adk import ZepGraphSearchTool

        mock_client = MagicMock()
        mock_client.graph.search = AsyncMock(return_value=self._make_search_result())

        tool = ZepGraphSearchTool(zep_client=mock_client)
        tc = self._make_tool_context(
            state={"zep_user_id": "state-user"},
            session_user_id="fallback-user",
        )

        await tool.run_async(args={"query": "test"}, tool_context=tc)

        call_kwargs = mock_client.graph.search.call_args[1]
        assert call_kwargs["user_id"] == "state-user"

    @pytest.mark.asyncio
    async def test_uses_graph_id_when_set(self) -> None:
        from zep_adk import ZepGraphSearchTool

        mock_client = MagicMock()
        mock_client.graph.search = AsyncMock(return_value=self._make_search_result())

        tool = ZepGraphSearchTool(zep_client=mock_client, graph_id="docs-123")
        tc = self._make_tool_context(session_user_id="user-42")

        await tool.run_async(args={"query": "test"}, tool_context=tc)

        call_kwargs = mock_client.graph.search.call_args[1]
        assert call_kwargs["graph_id"] == "docs-123"
        assert "user_id" not in call_kwargs

    @pytest.mark.asyncio
    async def test_pinned_params_override_model_args(self) -> None:
        from zep_adk import ZepGraphSearchTool

        mock_client = MagicMock()
        mock_client.graph.search = AsyncMock(return_value=self._make_search_result())

        tool = ZepGraphSearchTool(zep_client=mock_client, scope="nodes", limit=5)
        tc = self._make_tool_context()

        # Model tries to set scope and limit, but they're pinned
        await tool.run_async(
            args={"query": "test", "scope": "edges", "limit": 20},
            tool_context=tc,
        )

        call_kwargs = mock_client.graph.search.call_args[1]
        assert call_kwargs["scope"] == "nodes"  # pinned wins
        assert call_kwargs["limit"] == 5  # pinned wins

    @pytest.mark.asyncio
    async def test_defaults_applied_when_model_omits(self) -> None:
        from zep_adk import ZepGraphSearchTool

        mock_client = MagicMock()
        mock_client.graph.search = AsyncMock(return_value=self._make_search_result())

        tool = ZepGraphSearchTool(zep_client=mock_client)
        tc = self._make_tool_context()

        await tool.run_async(args={"query": "test"}, tool_context=tc)

        call_kwargs = mock_client.graph.search.call_args[1]
        assert call_kwargs["scope"] == "edges"  # default
        assert call_kwargs["reranker"] == "rrf"  # default
        assert call_kwargs["limit"] == 10  # default

    @pytest.mark.asyncio
    async def test_model_provided_params_used(self) -> None:
        from zep_adk import ZepGraphSearchTool

        mock_client = MagicMock()
        mock_client.graph.search = AsyncMock(return_value=self._make_search_result())

        tool = ZepGraphSearchTool(zep_client=mock_client)
        tc = self._make_tool_context()

        await tool.run_async(
            args={"query": "test", "scope": "nodes", "limit": 3},
            tool_context=tc,
        )

        call_kwargs = mock_client.graph.search.call_args[1]
        assert call_kwargs["scope"] == "nodes"
        assert call_kwargs["limit"] == 3

    @pytest.mark.asyncio
    async def test_search_filters_passed_through(self) -> None:
        from zep_adk import ZepGraphSearchTool

        mock_client = MagicMock()
        mock_client.graph.search = AsyncMock(return_value=self._make_search_result())

        filters = {"node_labels": ["Person"]}
        tool = ZepGraphSearchTool(zep_client=mock_client, search_filters=filters)
        tc = self._make_tool_context()

        await tool.run_async(args={"query": "test"}, tool_context=tc)

        call_kwargs = mock_client.graph.search.call_args[1]
        assert call_kwargs["search_filters"] == filters

    @pytest.mark.asyncio
    async def test_error_on_missing_query(self) -> None:
        from zep_adk import ZepGraphSearchTool

        tool = ZepGraphSearchTool(zep_client=MagicMock())
        tc = self._make_tool_context()

        result = await tool.run_async(args={}, tool_context=tc)
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_error_on_missing_user_id(self) -> None:
        from zep_adk import ZepGraphSearchTool

        tool = ZepGraphSearchTool(zep_client=MagicMock())
        tc = MagicMock(spec=["state"])
        tc.state = {}
        # tc has no user_id attr (spec restricts it), simulating a context
        # without the public user_id property.

        result = await tool.run_async(args={"query": "test"}, tool_context=tc)
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_handles_search_exception(self) -> None:
        from zep_adk import ZepGraphSearchTool

        mock_client = MagicMock()
        mock_client.graph.search = AsyncMock(side_effect=RuntimeError("boom"))

        tool = ZepGraphSearchTool(zep_client=mock_client)
        tc = self._make_tool_context()

        result = await tool.run_async(args={"query": "test"}, tool_context=tc)
        assert "failed" in result.lower()


class TestGraphSearchResultFormatting:
    """Test result formatting for different scopes."""

    def _make_edge(self, fact: str) -> MagicMock:
        edge = MagicMock()
        edge.fact = fact
        return edge

    def _make_node(self, name: str, summary: str) -> MagicMock:
        node = MagicMock()
        node.name = name
        node.summary = summary
        return node

    def _make_episode(self, content: str) -> MagicMock:
        ep = MagicMock()
        ep.content = content
        return ep

    def _make_observation(self, name: str, summary: str) -> MagicMock:
        observation = MagicMock()
        observation.name = name
        observation.summary = summary
        return observation

    def _make_thread_summary(self, name: str, summary: str) -> MagicMock:
        thread_summary = MagicMock()
        thread_summary.name = name
        thread_summary.summary = summary
        return thread_summary

    def test_format_edges(self) -> None:
        from zep_adk.graph_search_tool import ZepGraphSearchTool

        result = MagicMock()
        result.edges = [self._make_edge("Alice works at Acme"), self._make_edge("Bob likes hiking")]
        result.nodes = None
        result.episodes = None

        formatted = ZepGraphSearchTool._format_results(result, "edges")
        assert "Alice works at Acme" in formatted
        assert "Bob likes hiking" in formatted

    def test_format_nodes(self) -> None:
        from zep_adk.graph_search_tool import ZepGraphSearchTool

        result = MagicMock()
        result.edges = None
        result.nodes = [self._make_node("Alice", "A software engineer at Acme")]
        result.episodes = None

        formatted = ZepGraphSearchTool._format_results(result, "nodes")
        assert "Alice" in formatted
        assert "software engineer" in formatted

    def test_format_episodes(self) -> None:
        from zep_adk.graph_search_tool import ZepGraphSearchTool

        result = MagicMock()
        result.edges = None
        result.nodes = None
        result.episodes = [self._make_episode("I work at Acme Corp")]

        formatted = ZepGraphSearchTool._format_results(result, "episodes")
        assert "Acme Corp" in formatted

    def test_format_observations(self) -> None:
        from zep_adk.graph_search_tool import ZepGraphSearchTool

        result = MagicMock()
        result.edges = None
        result.nodes = None
        result.episodes = None
        result.observations = [self._make_observation("Alice", "Prefers async communication")]
        result.thread_summaries = None

        formatted = ZepGraphSearchTool._format_results(result, "observations")
        assert "Alice" in formatted
        assert "Prefers async communication" in formatted

    def test_format_thread_summaries(self) -> None:
        from zep_adk.graph_search_tool import ZepGraphSearchTool

        result = MagicMock()
        result.edges = None
        result.nodes = None
        result.episodes = None
        result.observations = None
        result.thread_summaries = [self._make_thread_summary("thread-1", "Discussed billing issue")]

        formatted = ZepGraphSearchTool._format_results(result, "thread_summaries")
        assert "thread-1" in formatted
        assert "Discussed billing issue" in formatted

    def test_format_observation_with_name_only_still_shown(self) -> None:
        """An observation with a name but no summary is rendered as just the
        name, matching the Go/TypeScript integrations, rather than being
        silently dropped."""
        from zep_adk.graph_search_tool import ZepGraphSearchTool

        result = MagicMock()
        result.edges = None
        result.nodes = None
        result.episodes = None
        result.observations = [self._make_observation("Alice", None)]
        result.thread_summaries = None

        formatted = ZepGraphSearchTool._format_results(result, "observations")
        assert "Alice" in formatted
        assert formatted != "No results found."

    def test_format_thread_summary_with_name_only_still_shown(self) -> None:
        """A thread summary with a name but no summary is rendered as just
        the name, matching the Go/TypeScript integrations, rather than being
        silently dropped."""
        from zep_adk.graph_search_tool import ZepGraphSearchTool

        result = MagicMock()
        result.edges = None
        result.nodes = None
        result.episodes = None
        result.observations = None
        result.thread_summaries = [self._make_thread_summary("thread-1", None)]

        formatted = ZepGraphSearchTool._format_results(result, "thread_summaries")
        assert "thread-1" in formatted
        assert formatted != "No results found."

    def test_format_node_with_name_only_still_shown(self) -> None:
        """A node with a name but no summary is rendered as just the name."""
        from zep_adk.graph_search_tool import ZepGraphSearchTool

        result = MagicMock()
        result.edges = None
        result.nodes = [self._make_node("Alice", None)]
        result.episodes = None

        formatted = ZepGraphSearchTool._format_results(result, "nodes")
        assert "Alice" in formatted
        assert formatted != "No results found."

    def test_format_node_with_summary_only_renders_summary_without_label(self) -> None:
        """A node with a summary but no name renders as just the summary --
        no generic 'Entity'-style label prefix, matching Go/TypeScript."""
        from zep_adk.graph_search_tool import ZepGraphSearchTool

        result = MagicMock()
        result.edges = None
        result.nodes = [self._make_node(None, "A software engineer at Acme")]
        result.episodes = None

        formatted = ZepGraphSearchTool._format_results(result, "nodes")
        assert formatted == "- A software engineer at Acme"

    def test_format_observation_with_summary_only_renders_summary_without_label(self) -> None:
        """An observation with a summary but no name renders as just the
        summary -- no generic 'Observation'-style label prefix."""
        from zep_adk.graph_search_tool import ZepGraphSearchTool

        result = MagicMock()
        result.edges = None
        result.nodes = None
        result.episodes = None
        result.observations = [self._make_observation(None, "Prefers async communication")]
        result.thread_summaries = None

        formatted = ZepGraphSearchTool._format_results(result, "observations")
        assert formatted == "- Prefers async communication"

    def test_format_thread_summary_with_summary_only_renders_summary_without_label(self) -> None:
        """A thread summary with a summary but no name renders as just the
        summary -- no generic 'Thread'-style label prefix."""
        from zep_adk.graph_search_tool import ZepGraphSearchTool

        result = MagicMock()
        result.edges = None
        result.nodes = None
        result.episodes = None
        result.observations = None
        result.thread_summaries = [self._make_thread_summary(None, "Discussed billing issue")]

        formatted = ZepGraphSearchTool._format_results(result, "thread_summaries")
        assert formatted == "- Discussed billing issue"

    def test_format_empty_results(self) -> None:
        from zep_adk.graph_search_tool import ZepGraphSearchTool

        result = MagicMock()
        result.edges = []
        result.nodes = None
        result.episodes = None

        formatted = ZepGraphSearchTool._format_results(result, "edges")
        assert formatted == "No results found."
