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
    from zep_adk import ZepContextTool, create_after_model_callback

    assert ZepContextTool is not None
    assert create_after_model_callback is not None


class TestPackageStructure:
    """Basic structure tests for the zep-adk package."""

    def test_version_exists(self) -> None:
        import zep_adk

        assert hasattr(zep_adk, "__version__")
        assert zep_adk.__version__ == "0.1.0"

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
        assert len(tool._persisted_messages) == 0
        assert len(tool._created_resources) == 0



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
            mock_tc._invocation_context.session.id = session_id
            mock_tc._invocation_context.session.user_id = session_user_id
        else:
            # Simulate missing session by making the attribute access raise
            mock_invocation = MagicMock()
            mock_invocation.session = MagicMock(spec=[])  # spec=[] means no attributes
            mock_tc._invocation_context = mock_invocation
        return mock_tc

    def test_resolves_identity_from_state(self) -> None:
        tool = self._make_tool()
        tc = self._make_tool_context(
            state={
                "zep_user_id": "user-1",
                "zep_thread_id": "thread-1",
                "zep_first_name": "Jane",
                "zep_last_name": "Smith",
                "zep_email": "jane@example.com",
            }
        )
        identity = tool._resolve_identity(tc)
        assert identity.user_id == "user-1"
        assert identity.thread_id == "thread-1"
        assert identity.first_name == "Jane"
        assert identity.last_name == "Smith"
        assert identity.email == "jane@example.com"
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

    def test_name_defaults_and_email_none(self) -> None:
        """When no identity fields are in state, first/last default but email is None."""
        tool = self._make_tool()
        tc = self._make_tool_context(state={"zep_user_id": "u", "zep_thread_id": "t"})
        identity = tool._resolve_identity(tc)
        assert identity.first_name == "Anonymous"
        assert identity.last_name == "User"
        assert identity.email is None


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
        mock_tc.state = (
            state
            if state is not None
            else {"zep_thread_id": "test-thread"}
        )
        mock_tc._invocation_context.session.id = session_id
        mock_tc._invocation_context.session.user_id = session_user_id
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

        # Should have created user and thread (lazy init)
        # user_id comes from session.user_id, name defaults to Anonymous/User
        mock_client.user.add.assert_called_once_with(
            user_id="test-user",
            first_name="Anonymous",
            last_name="User",
            email=None,
        )
        mock_client.thread.create.assert_called_once_with(
            thread_id="test-thread", user_id="test-user"
        )

        # Should have called add_messages with return_context=True
        mock_client.thread.add_messages.assert_called_once()
        call_kwargs = mock_client.thread.add_messages.call_args[1]
        assert call_kwargs["thread_id"] == "test-thread"
        assert call_kwargs["return_context"] is True
        assert len(call_kwargs["messages"]) == 1
        assert call_kwargs["messages"][0].content == "Hi there"
        assert call_kwargs["messages"][0].role == "user"

    @pytest.mark.asyncio
    async def test_user_add_receives_identity_fields(self) -> None:
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
                "zep_email": "jane@example.com",
            },
        )
        llm_request = self._make_llm_request()

        await tool.process_llm_request(tool_context=tool_context, llm_request=llm_request)

        mock_client.user.add.assert_called_once_with(
            user_id="test-user",
            first_name="Jane",
            last_name="Smith",
            email="jane@example.com",
        )

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
    async def test_deduplicates_messages_per_thread(self) -> None:
        mock_client = self._make_mock_client()
        mock_response = MagicMock()
        mock_response.context = "context"
        mock_client.thread.add_messages.return_value = mock_response

        tool = self._make_tool(mock_client)
        llm_request = self._make_llm_request()

        # Send the same message twice on the same thread
        tc1 = self._make_tool_context("Same message")
        tc2 = self._make_tool_context("Same message")

        await tool.process_llm_request(tool_context=tc1, llm_request=llm_request)
        await tool.process_llm_request(tool_context=tc2, llm_request=llm_request)

        # add_messages should only be called once (deduplicated)
        assert mock_client.thread.add_messages.call_count == 1

    @pytest.mark.asyncio
    async def test_dedup_is_per_thread_not_global(self) -> None:
        """Same message text on different threads should persist to both."""
        mock_client = self._make_mock_client()
        mock_response = MagicMock()
        mock_response.context = None
        mock_client.thread.add_messages.return_value = mock_response

        tool = self._make_tool(mock_client)
        llm_request = self._make_llm_request()

        # Same message, different threads
        tc_thread_a = self._make_tool_context(
            "Same message",
            state={"zep_user_id": "user-1", "zep_thread_id": "thread-A"},
        )
        tc_thread_b = self._make_tool_context(
            "Same message",
            state={"zep_user_id": "user-1", "zep_thread_id": "thread-B"},
        )

        await tool.process_llm_request(tool_context=tc_thread_a, llm_request=llm_request)
        await tool.process_llm_request(tool_context=tc_thread_b, llm_request=llm_request)

        # Should have persisted to both threads
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
    async def test_resource_creation_cached_per_user_thread(self) -> None:
        """user.add and thread.create should only run once per (user_id, thread_id)."""
        mock_client = self._make_mock_client()
        mock_response = MagicMock()
        mock_response.context = None
        mock_client.thread.add_messages.return_value = mock_response

        tool = self._make_tool(mock_client)
        llm_request = self._make_llm_request()

        # First call
        tc1 = self._make_tool_context("Message 1")
        await tool.process_llm_request(tool_context=tc1, llm_request=llm_request)

        # Second call (different message to avoid dedup)
        tc2 = self._make_tool_context("Message 2")
        await tool.process_llm_request(tool_context=tc2, llm_request=llm_request)

        # user.add and thread.create should only have been called once
        assert mock_client.user.add.call_count == 1
        assert mock_client.thread.create.call_count == 1

    @pytest.mark.asyncio
    async def test_resource_creation_separate_per_user_thread_pair(self) -> None:
        """Different (user_id, thread_id) pairs should each get resource creation."""
        mock_client = self._make_mock_client()
        mock_response = MagicMock()
        mock_response.context = None
        mock_client.thread.add_messages.return_value = mock_response

        tool = self._make_tool(mock_client)
        llm_request = self._make_llm_request()

        tc_a = self._make_tool_context(
            "Msg A",
            state={"zep_user_id": "user-A", "zep_thread_id": "thread-A"},
        )
        tc_b = self._make_tool_context(
            "Msg B",
            state={"zep_user_id": "user-B", "zep_thread_id": "thread-B"},
        )

        await tool.process_llm_request(tool_context=tc_a, llm_request=llm_request)
        await tool.process_llm_request(tool_context=tc_b, llm_request=llm_request)

        # Each pair should have triggered resource creation
        assert mock_client.user.add.call_count == 2
        assert mock_client.thread.create.call_count == 2

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

        # Thread should have been created with the session ID
        mock_client.thread.create.assert_called_once_with(
            thread_id="my-session-id", user_id="test-user"
        )

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
        tc = MagicMock()
        tc.user_content = mock_content
        tc.state = {}
        # Make session attribute access fail
        mock_invocation = MagicMock()
        mock_invocation.session = MagicMock(spec=[])  # no .user_id or .id
        tc._invocation_context = mock_invocation

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
        from zep_adk import ZepContextTool

        mock_client = self._make_mock_client()
        mock_client.thread.add_messages.return_value = None  # no return_context

        custom_builder = AsyncMock(return_value="Custom context from builder")

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

        # context_builder should have been called with the right args
        custom_builder.assert_called_once()
        args = custom_builder.call_args[0]
        assert args[0] is mock_client  # zep_client
        assert args[1] == "test-user"  # user_id
        assert args[2] == "test-thread"  # thread_id
        assert args[3] == "Hello"  # user_message

    @pytest.mark.asyncio
    async def test_context_builder_result_injected_into_prompt(self) -> None:
        """The string returned by context_builder should appear in LLM instructions."""
        from zep_adk import ZepContextTool

        mock_client = self._make_mock_client()
        mock_client.thread.add_messages.return_value = None

        custom_builder = AsyncMock(return_value="User works at Acme Corp as CTO")

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
        """When context_builder returns None, no context should be injected."""
        from zep_adk import ZepContextTool

        mock_client = self._make_mock_client()
        mock_client.thread.add_messages.return_value = None

        custom_builder = AsyncMock(return_value=None)

        tool = ZepContextTool(zep_client=mock_client, context_builder=custom_builder)
        tc = self._make_tool_context("Hello")
        llm_request = self._make_llm_request()

        await tool.process_llm_request(tool_context=tc, llm_request=llm_request)

        llm_request.append_instructions.assert_not_called()

    @pytest.mark.asyncio
    async def test_context_builder_and_persist_both_called(self) -> None:
        """Both add_messages and context_builder should be called (parallel execution)."""
        from zep_adk import ZepContextTool

        mock_client = self._make_mock_client()
        mock_client.thread.add_messages.return_value = None

        custom_builder = AsyncMock(return_value="context")

        tool = ZepContextTool(zep_client=mock_client, context_builder=custom_builder)
        tc = self._make_tool_context("Hello")
        llm_request = self._make_llm_request()

        await tool.process_llm_request(tool_context=tc, llm_request=llm_request)

        # Both should have been called
        mock_client.thread.add_messages.assert_called_once()
        custom_builder.assert_called_once()

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
    async def test_context_builder_error_handled_gracefully(self) -> None:
        """When context_builder raises, should log and skip, not crash."""
        from zep_adk import ZepContextTool

        mock_client = self._make_mock_client()
        mock_client.thread.add_messages.return_value = None

        custom_builder = AsyncMock(side_effect=RuntimeError("builder failed"))

        tool = ZepContextTool(zep_client=mock_client, context_builder=custom_builder)
        tc = self._make_tool_context("Hello")
        llm_request = self._make_llm_request()

        # Should not raise
        await tool.process_llm_request(tool_context=tc, llm_request=llm_request)

        llm_request.append_instructions.assert_not_called()

    @pytest.mark.asyncio
    async def test_context_builder_type_exported(self) -> None:
        """ContextBuilder type alias should be importable from the package."""
        from zep_adk import ContextBuilder

        assert ContextBuilder is not None

    @pytest.mark.asyncio
    async def test_resource_creation_failure_prevents_caching(self) -> None:
        """When user.add fails with a genuine error, resources should NOT be cached."""
        mock_client = self._make_mock_client()
        mock_client.user.add.side_effect = RuntimeError("network timeout")

        tool = self._make_tool(mock_client)
        tc = self._make_tool_context("Hello")
        llm_request = self._make_llm_request()

        await tool.process_llm_request(tool_context=tc, llm_request=llm_request)

        # Should not have persisted the message (resource creation failed)
        mock_client.thread.add_messages.assert_not_called()

        # Reset the mock and try again — should retry resource creation
        mock_client.user.add.reset_mock()
        mock_client.user.add.side_effect = None  # now succeeds
        mock_response = MagicMock()
        mock_response.context = None
        mock_client.thread.add_messages.return_value = mock_response

        tc2 = self._make_tool_context("Hello again")
        await tool.process_llm_request(tool_context=tc2, llm_request=llm_request)

        # Should have retried user.add
        mock_client.user.add.assert_called_once()


class TestOnUserCreatedHook:
    """Test on_user_created hook in ZepContextTool."""

    def _make_mock_client(self) -> MagicMock:
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
        session_user_id: str = "test-user",
    ) -> MagicMock:
        mock_part = MagicMock()
        mock_part.text = text
        mock_content = MagicMock()
        mock_content.parts = [mock_part]
        mock_tc = MagicMock()
        mock_tc.user_content = mock_content
        mock_tc.state = (
            state if state is not None else {"zep_thread_id": "test-thread"}
        )
        mock_tc._invocation_context.session.id = "test-session"
        mock_tc._invocation_context.session.user_id = session_user_id
        return mock_tc

    def _make_llm_request(self) -> MagicMock:
        mock_request = MagicMock()
        mock_request.append_instructions = MagicMock()
        return mock_request

    @pytest.mark.asyncio
    async def test_hook_called_on_new_user(self) -> None:
        from zep_adk import ZepContextTool

        mock_client = self._make_mock_client()
        mock_response = MagicMock()
        mock_response.context = None
        mock_client.thread.add_messages.return_value = mock_response

        hook = AsyncMock()
        tool = ZepContextTool(zep_client=mock_client, on_user_created=hook)
        tc = self._make_tool_context("Hello")
        llm_request = self._make_llm_request()

        await tool.process_llm_request(tool_context=tc, llm_request=llm_request)

        hook.assert_called_once_with(mock_client, "test-user")

    @pytest.mark.asyncio
    async def test_hook_not_called_on_existing_user(self) -> None:
        from zep_adk import ZepContextTool

        mock_client = self._make_mock_client()
        # Simulate "already exists" error
        mock_client.user.add.side_effect = Exception("already exists")
        mock_response = MagicMock()
        mock_response.context = None
        mock_client.thread.add_messages.return_value = mock_response

        hook = AsyncMock()
        tool = ZepContextTool(zep_client=mock_client, on_user_created=hook)
        tc = self._make_tool_context("Hello")
        llm_request = self._make_llm_request()

        await tool.process_llm_request(tool_context=tc, llm_request=llm_request)

        hook.assert_not_called()

    @pytest.mark.asyncio
    async def test_hook_not_called_on_cached_resources(self) -> None:
        from zep_adk import ZepContextTool

        mock_client = self._make_mock_client()
        mock_response = MagicMock()
        mock_response.context = None
        mock_client.thread.add_messages.return_value = mock_response

        hook = AsyncMock()
        tool = ZepContextTool(zep_client=mock_client, on_user_created=hook)
        llm_request = self._make_llm_request()

        # First call — hook fires
        tc1 = self._make_tool_context("Hello")
        await tool.process_llm_request(tool_context=tc1, llm_request=llm_request)
        assert hook.call_count == 1

        # Second call (different message, same user/thread) — hook does NOT fire
        tc2 = self._make_tool_context("World")
        await tool.process_llm_request(tool_context=tc2, llm_request=llm_request)
        assert hook.call_count == 1  # still 1

    @pytest.mark.asyncio
    async def test_hook_failure_does_not_block_agent(self) -> None:
        from zep_adk import ZepContextTool

        mock_client = self._make_mock_client()
        mock_response = MagicMock()
        mock_response.context = "Some context"
        mock_client.thread.add_messages.return_value = mock_response

        hook = AsyncMock(side_effect=RuntimeError("hook exploded"))
        tool = ZepContextTool(zep_client=mock_client, on_user_created=hook)
        tc = self._make_tool_context("Hello")
        llm_request = self._make_llm_request()

        # Should not raise — hook failure is logged but agent continues
        await tool.process_llm_request(tool_context=tc, llm_request=llm_request)

        # Message was still persisted and context still injected
        mock_client.thread.add_messages.assert_called_once()
        llm_request.append_instructions.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_hook_no_error(self) -> None:
        """Without on_user_created, everything works as before."""
        from zep_adk import ZepContextTool

        mock_client = self._make_mock_client()
        mock_response = MagicMock()
        mock_response.context = None
        mock_client.thread.add_messages.return_value = mock_response

        tool = ZepContextTool(zep_client=mock_client)  # no hook
        tc = self._make_tool_context("Hello")
        llm_request = self._make_llm_request()

        await tool.process_llm_request(tool_context=tc, llm_request=llm_request)

        mock_client.thread.add_messages.assert_called_once()


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
        mock_ctx._invocation_context.session.id = session_id
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
        callback_context = MagicMock()
        callback_context.state = {}
        # Simulate missing session ID by making session have no .id attribute
        mock_invocation = MagicMock()
        mock_invocation.session = MagicMock(spec=[])  # spec=[] means no attributes
        callback_context._invocation_context = mock_invocation

        result = await callback(callback_context, llm_response)

        assert result is None
        mock_client.thread.add_messages.assert_not_called()

    @pytest.mark.asyncio
    async def test_deduplicates_responses_per_thread(self) -> None:
        from zep_adk import create_after_model_callback

        mock_client = self._make_mock_client()
        callback = create_after_model_callback(zep_client=mock_client)

        llm_response = self._make_llm_response("Same response text")
        callback_context = self._make_callback_context()

        await callback(callback_context, llm_response)
        await callback(callback_context, llm_response)

        # Should only persist once
        assert mock_client.thread.add_messages.call_count == 1

    @pytest.mark.asyncio
    async def test_dedup_is_per_thread_not_global(self) -> None:
        """Same response on different threads should persist to both."""
        from zep_adk import create_after_model_callback

        mock_client = self._make_mock_client()
        callback = create_after_model_callback(zep_client=mock_client)

        llm_response = self._make_llm_response("Same response text")

        ctx_a = self._make_callback_context(state={"zep_thread_id": "thread-A"})
        ctx_b = self._make_callback_context(state={"zep_thread_id": "thread-B"})

        await callback(ctx_a, llm_response)
        await callback(ctx_b, llm_response)

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

        # Response with no text parts (e.g. function call)
        mock_part = MagicMock()
        mock_part.text = None
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
    async def test_joins_multiple_text_parts(self) -> None:
        from zep_adk import create_after_model_callback

        mock_client = self._make_mock_client()
        callback = create_after_model_callback(zep_client=mock_client)

        # Response with multiple text parts
        mock_part1 = MagicMock()
        mock_part1.text = "Part one."
        mock_part2 = MagicMock()
        mock_part2.text = "Part two."
        mock_content = MagicMock()
        mock_content.parts = [mock_part1, mock_part2]
        mock_response = MagicMock()
        mock_response.content = mock_content

        callback_context = self._make_callback_context()
        await callback(callback_context, mock_response)

        call_kwargs = mock_client.thread.add_messages.call_args[1]
        assert call_kwargs["messages"][0].content == "Part one. Part two."


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
        tool = ZepGraphSearchTool(
            zep_client=MagicMock(), search_filters=filters
        )
        assert tool._pinned["search_filters"] == filters

    def test_bfs_origin_stored_as_pinned(self) -> None:
        from zep_adk import ZepGraphSearchTool

        uuids = ["uuid-1", "uuid-2"]
        tool = ZepGraphSearchTool(
            zep_client=MagicMock(), bfs_origin_node_uuids=uuids
        )
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
        mock_tc._invocation_context.session.user_id = session_user_id
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
        mock_client.graph.search = AsyncMock(
            return_value=self._make_search_result()
        )

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
        mock_client.graph.search = AsyncMock(
            return_value=self._make_search_result()
        )

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
        mock_client.graph.search = AsyncMock(
            return_value=self._make_search_result()
        )

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
        mock_client.graph.search = AsyncMock(
            return_value=self._make_search_result()
        )

        tool = ZepGraphSearchTool(
            zep_client=mock_client, scope="nodes", limit=5
        )
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
        mock_client.graph.search = AsyncMock(
            return_value=self._make_search_result()
        )

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
        mock_client.graph.search = AsyncMock(
            return_value=self._make_search_result()
        )

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
        mock_client.graph.search = AsyncMock(
            return_value=self._make_search_result()
        )

        filters = {"node_labels": ["Person"]}
        tool = ZepGraphSearchTool(
            zep_client=mock_client, search_filters=filters
        )
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
        tc = MagicMock()
        tc.state = {}
        # Simulate no session user_id
        mock_inv = MagicMock()
        mock_inv.session = MagicMock(spec=[])
        tc._invocation_context = mock_inv

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

    def test_format_empty_results(self) -> None:
        from zep_adk.graph_search_tool import ZepGraphSearchTool

        result = MagicMock()
        result.edges = []
        result.nodes = None
        result.episodes = None

        formatted = ZepGraphSearchTool._format_results(result, "edges")
        assert formatted == "No results found."
