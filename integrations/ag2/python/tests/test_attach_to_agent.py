"""
Tests for the ``attach_to_agent`` auto-loop -- AG2's only real per-turn seam.

AG2 has no native memory interface; ``ConversableAgent.register_hook`` is the
closest thing to one. ``attach_to_agent`` registers a
``process_last_received_message`` hook that bridges (via ``_run_sync``) into
``ZepMemoryManager.process_user_message``, and -- because
``process_message_before_send`` cleanly receives the outgoing message and can
return it unchanged (verified against the installed ``ag2`` source) -- also
registers a ``process_message_before_send`` hook that persists the agent's
outgoing reply. Together these give a fully automatic inject+persist loop.

Both hooks must return the message content **unmodified**: this is a
persist/inject side-channel, not a message transform. Any internal failure is
caught so a Zep outage never breaks the agent's conversation loop.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from zep_cloud.client import AsyncZep

from zep_ag2 import ZepMemoryManager


def _make_mock_client(context: str = "Zep context block") -> MagicMock:
    client = MagicMock(spec=AsyncZep)
    client.user = MagicMock()
    client.user.add = AsyncMock()
    client.thread = MagicMock()
    client.thread.create = AsyncMock()
    client.thread.add_messages = AsyncMock(return_value=MagicMock(context=context))
    client.thread.get_user_context = AsyncMock(return_value=MagicMock(context=context))
    return client


def _make_fake_agent() -> MagicMock:
    agent = MagicMock()
    agent.system_message = "You are a helpful assistant."
    agent.update_system_message = MagicMock()
    agent.register_hook = MagicMock()
    return agent


class TestAttachToAgentRegistersHooks:
    def test_attach_to_agent_registers_hook(self) -> None:
        client = _make_mock_client()
        manager = ZepMemoryManager(client, user_id="u1", session_id="s1")
        agent = _make_fake_agent()

        manager.attach_to_agent(agent)

        registered_methods = [call.args[0] for call in agent.register_hook.call_args_list]
        assert "process_last_received_message" in registered_methods

    def test_attach_to_agent_registers_outgoing_reply_hook(self) -> None:
        """process_message_before_send's contract is clean enough (receives
        the outgoing message, can return it unchanged) to wire assistant
        persistence through -- the loop is complete, not just inject-only."""
        client = _make_mock_client()
        manager = ZepMemoryManager(client, user_id="u1", session_id="s1")
        agent = _make_fake_agent()

        manager.attach_to_agent(agent)

        registered_methods = [call.args[0] for call in agent.register_hook.call_args_list]
        assert "process_message_before_send" in registered_methods


class TestIncomingHookBehavior:
    def test_hook_persists_and_updates_system_message(self) -> None:
        client = _make_mock_client(context="Fresh context")
        manager = ZepMemoryManager(client, user_id="u1", session_id="s1")
        agent = _make_fake_agent()

        manager.attach_to_agent(agent)

        # Grab the registered process_last_received_message hook.
        incoming_hook = next(
            call.args[1]
            for call in agent.register_hook.call_args_list
            if call.args[0] == "process_last_received_message"
        )

        result = incoming_hook("Hello, agent!")

        # Hook contract: returns the message content unmodified.
        assert result == "Hello, agent!"
        client.thread.add_messages.assert_called_once()
        agent.update_system_message.assert_called_once()
        injected = agent.update_system_message.call_args[0][0]
        assert "Fresh context" in injected

    def test_hook_failure_returns_message_unchanged(self) -> None:
        client = _make_mock_client()
        client.thread.add_messages = AsyncMock(side_effect=Exception("zep is down"))
        manager = ZepMemoryManager(client, user_id="u1", session_id="s1")
        agent = _make_fake_agent()

        manager.attach_to_agent(agent)

        incoming_hook = next(
            call.args[1]
            for call in agent.register_hook.call_args_list
            if call.args[0] == "process_last_received_message"
        )

        result = incoming_hook("Hello, agent!")

        assert result == "Hello, agent!"
        # Failure must not propagate as an exception, and must not touch the
        # agent's system message.
        agent.update_system_message.assert_not_called()

    def test_hook_wraps_update_system_message_failure_too(self) -> None:
        """Even if update_system_message itself raises, the hook must still
        return the message unchanged."""
        client = _make_mock_client(context="Some context")
        manager = ZepMemoryManager(client, user_id="u1", session_id="s1")
        agent = _make_fake_agent()
        agent.update_system_message.side_effect = RuntimeError("agent explosion")

        manager.attach_to_agent(agent)

        incoming_hook = next(
            call.args[1]
            for call in agent.register_hook.call_args_list
            if call.args[0] == "process_last_received_message"
        )

        result = incoming_hook("Hi")

        assert result == "Hi"


class TestOutgoingHookBehavior:
    def test_outgoing_hook_persists_assistant_reply(self) -> None:
        client = _make_mock_client()
        manager = ZepMemoryManager(client, user_id="u1", session_id="s1")
        agent = _make_fake_agent()

        manager.attach_to_agent(agent)

        outgoing_hook = next(
            call.args[1]
            for call in agent.register_hook.call_args_list
            if call.args[0] == "process_message_before_send"
        )

        recipient = MagicMock()
        result = outgoing_hook(
            sender=agent, message="Here is my reply", recipient=recipient, silent=False
        )

        assert result == "Here is my reply"
        client.thread.add_messages.assert_called_once()
        sent_messages = client.thread.add_messages.call_args.kwargs["messages"]
        assert sent_messages[0].role == "assistant"
        assert sent_messages[0].content == "Here is my reply"

    def test_outgoing_hook_failure_returns_message_unchanged(self) -> None:
        client = _make_mock_client()
        client.thread.add_messages = AsyncMock(side_effect=Exception("zep is down"))
        manager = ZepMemoryManager(client, user_id="u1", session_id="s1")
        agent = _make_fake_agent()

        manager.attach_to_agent(agent)

        outgoing_hook = next(
            call.args[1]
            for call in agent.register_hook.call_args_list
            if call.args[0] == "process_message_before_send"
        )

        recipient = MagicMock()
        result = outgoing_hook(
            sender=agent, message="Here is my reply", recipient=recipient, silent=False
        )

        assert result == "Here is my reply"

    def test_outgoing_hook_handles_dict_message(self) -> None:
        """AG2 messages may be dicts with a 'content' key, not just plain
        strings -- the hook must extract the text content in either shape."""
        client = _make_mock_client()
        manager = ZepMemoryManager(client, user_id="u1", session_id="s1")
        agent = _make_fake_agent()

        manager.attach_to_agent(agent)

        outgoing_hook = next(
            call.args[1]
            for call in agent.register_hook.call_args_list
            if call.args[0] == "process_message_before_send"
        )

        recipient = MagicMock()
        message = {"content": "Dict-shaped reply", "role": "assistant"}
        result = outgoing_hook(sender=agent, message=message, recipient=recipient, silent=False)

        assert result == message
        sent_messages = client.thread.add_messages.call_args.kwargs["messages"]
        assert sent_messages[0].content == "Dict-shaped reply"
