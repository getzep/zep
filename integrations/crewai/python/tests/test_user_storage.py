"""
Tests for ZepUserStorage.
"""

from unittest.mock import MagicMock

import pytest

from zep_crewai import ZepUserStorage

USER_UUID = "11111111-1111-1111-1111-111111111111"
THREAD_UUID = "22222222-2222-2222-2222-222222222222"
GRAPH_UUID = "33333333-3333-3333-3333-333333333333"


def _make_mock_zep_client():
    """A Zep mock whose ``user.get`` returns a user with a graph UUID."""
    from zep_cloud.client import Zep

    client = MagicMock(spec=Zep)
    client.user = MagicMock()
    client.user.get = MagicMock(return_value=MagicMock(graph_uuid=GRAPH_UUID))
    client.thread = MagicMock()
    client.graph = MagicMock()
    client.graph.episode = MagicMock()
    return client


class TestZepUserStorage:
    """Test suite for ZepUserStorage."""

    def test_initialization_success(self):
        """Test successful initialization with required parameters."""
        mock_client = _make_mock_zep_client()
        storage = ZepUserStorage(
            client=mock_client,
            user_uuid=USER_UUID,
            thread_uuid=THREAD_UUID,
            search_filters={"node_labels": ["Preference"]},
            max_characters=2000,
            graph_uuid=GRAPH_UUID,
        )

        assert storage._client is mock_client
        assert storage.user_uuid == USER_UUID
        assert storage.thread_uuid == THREAD_UUID
        assert storage.graph_uuid == GRAPH_UUID
        assert storage._search_filters == {"node_labels": ["Preference"]}
        assert storage._max_characters == 2000

    def test_initialization_mode_is_deprecated(self):
        """Test that passing the legacy 'mode' arg warns and is otherwise ignored."""
        mock_client = _make_mock_zep_client()
        with pytest.warns(DeprecationWarning, match="'mode' argument is deprecated"):
            storage = ZepUserStorage(
                client=mock_client,
                user_uuid=USER_UUID,
                thread_uuid=THREAD_UUID,
                mode="summary",
            )

        # Mode is no longer stored or forwarded to the Zep API.
        assert not hasattr(storage, "_mode")

    def test_initialization_without_thread_raises_error(self):
        """Test initialization without thread_uuid raises TypeError."""
        mock_client = _make_mock_zep_client()
        with pytest.raises(
            TypeError, match="missing 1 required positional argument: 'thread_uuid'"
        ):
            ZepUserStorage(client=mock_client, user_uuid=USER_UUID)

    def test_initialization_requires_user_uuid(self):
        """Test that user_uuid is required."""
        mock_client = _make_mock_zep_client()

        with pytest.raises(ValueError, match="user_uuid is required"):
            ZepUserStorage(client=mock_client, user_uuid="", thread_uuid=THREAD_UUID)

    def test_initialization_requires_thread_uuid(self):
        """Test that thread_uuid is required and non-empty."""
        mock_client = _make_mock_zep_client()

        with pytest.raises(ValueError, match="thread_uuid is required"):
            ZepUserStorage(client=mock_client, user_uuid=USER_UUID, thread_uuid="")

    def test_initialization_requires_zep_client(self):
        """Test that client must be Zep instance."""
        with pytest.raises(TypeError, match="client must be an instance of Zep"):
            ZepUserStorage(client="not_a_client", user_uuid=USER_UUID, thread_uuid=THREAD_UUID)

    def test_save_message_with_thread(self):
        """Test saving message to the thread that thread_uuid names."""
        mock_client = _make_mock_zep_client()
        mock_client.thread.add_messages = MagicMock()

        storage = ZepUserStorage(client=mock_client, user_uuid=USER_UUID, thread_uuid=THREAD_UUID)

        # Save message
        storage.save(
            "Hello, how can I help?",
            metadata={"type": "message", "role": "assistant", "name": "Helper"},
        )

        # Verify thread.add_messages was called
        mock_client.thread.add_messages.assert_called_once()

        call_args = mock_client.thread.add_messages.call_args
        assert call_args[0][0] == THREAD_UUID
        assert len(call_args[1]["messages"]) == 1

        message = call_args[1]["messages"][0]
        assert message.content == "Hello, how can I help?"
        assert message.role == "assistant"
        assert message.name == "Helper"

    def test_save_json_data(self):
        """Test saving JSON data to the user graph."""
        mock_client = _make_mock_zep_client()
        mock_client.graph.episode.add = MagicMock()

        storage = ZepUserStorage(
            client=mock_client,
            user_uuid=USER_UUID,
            thread_uuid=THREAD_UUID,
            graph_uuid=GRAPH_UUID,
        )

        # Save JSON data
        json_data = '{"preference": "dark_mode", "timezone": "PST"}'
        storage.save(json_data, metadata={"type": "json"})

        # Verify graph.episode.add was called correctly
        mock_client.graph.episode.add.assert_called_once_with(
            GRAPH_UUID, data=json_data, type="json"
        )

    def test_save_text_data(self):
        """Test saving text data to the user graph."""
        mock_client = _make_mock_zep_client()
        mock_client.graph.episode.add = MagicMock()

        storage = ZepUserStorage(
            client=mock_client,
            user_uuid=USER_UUID,
            thread_uuid=THREAD_UUID,
            graph_uuid=GRAPH_UUID,
        )

        # Save text data
        storage.save("User prefers morning meetings", metadata={"type": "text"})

        # Verify graph.episode.add was called correctly
        mock_client.graph.episode.add.assert_called_once_with(
            GRAPH_UUID, data="User prefers morning meetings", type="text"
        )

    def test_save_resolves_graph_uuid_once(self):
        """The user graph UUID is read one time and cached."""
        mock_client = _make_mock_zep_client()
        mock_client.graph.episode.add = MagicMock()

        storage = ZepUserStorage(client=mock_client, user_uuid=USER_UUID, thread_uuid=THREAD_UUID)
        storage.save("first", metadata={"type": "text"})
        storage.save("second", metadata={"type": "text"})

        mock_client.user.get.assert_called_once_with(USER_UUID)
        assert mock_client.graph.episode.add.call_count == 2

    def test_save_does_not_raise_on_zep_error(self, caplog):
        """save() must log and return normally when the Zep SDK call raises --
        never propagate the error into the crew."""
        mock_client = _make_mock_zep_client()
        mock_client.thread.add_messages = MagicMock(side_effect=Exception("Zep API error"))

        storage = ZepUserStorage(client=mock_client, user_uuid=USER_UUID, thread_uuid=THREAD_UUID)

        with caplog.at_level("ERROR"):
            storage.save(
                "Hello, how can I help?",
                metadata={"type": "message", "role": "assistant", "name": "Helper"},
            )

        mock_client.thread.add_messages.assert_called_once()
        assert "Zep API error" in caplog.text

    def test_search_returns_context_block(self):
        """search() returns the Context Block of the user graph."""
        mock_client = _make_mock_zep_client()
        mock_client.graph.get_context = MagicMock(
            return_value=MagicMock(context="Context: User likes Python")
        )

        storage = ZepUserStorage(
            client=mock_client,
            user_uuid=USER_UUID,
            thread_uuid=THREAD_UUID,
            graph_uuid=GRAPH_UUID,
        )

        results = storage.search("test query", limit=5)

        mock_client.graph.get_context.assert_called_once_with(GRAPH_UUID, query="test query")

        assert isinstance(results, list)
        assert len(results) == 1
        assert results[0]["type"] == "user_graph_context"
        assert "Context: User likes Python" in results[0]["context"]

    def test_search_returns_empty_list_without_context(self):
        """search() returns no results when the graph gives no context."""
        mock_client = _make_mock_zep_client()
        mock_client.graph.get_context = MagicMock(return_value=MagicMock(context=None))

        storage = ZepUserStorage(
            client=mock_client,
            user_uuid=USER_UUID,
            thread_uuid=THREAD_UUID,
            graph_uuid=GRAPH_UUID,
        )

        assert storage.search("test query", limit=5) == []

    def test_get_context_with_thread(self):
        """Test get_context retrieves context through thread.get_context."""
        mock_client = _make_mock_zep_client()

        # Mock thread context response
        mock_context = MagicMock()
        mock_context.context = "User's conversation context with summary"
        mock_client.thread.get_context.return_value = mock_context

        storage = ZepUserStorage(client=mock_client, user_uuid=USER_UUID, thread_uuid=THREAD_UUID)

        # Get context
        context = storage.get_context()

        mock_client.thread.get_context.assert_called_once_with(THREAD_UUID)

        assert context == "User's conversation context with summary"

    def test_get_context_with_empty_response(self):
        """Test get_context handles empty context response."""
        mock_client = _make_mock_zep_client()

        # Mock empty context response
        mock_context = MagicMock()
        mock_context.context = None
        mock_client.thread.get_context.return_value = mock_context

        storage = ZepUserStorage(client=mock_client, user_uuid=USER_UUID, thread_uuid=THREAD_UUID)

        # Get context
        context = storage.get_context()

        # Should return None for empty context
        assert context is None

    def test_search_with_filters(self):
        """Test that search filters are applied correctly."""
        mock_client = _make_mock_zep_client()
        mock_client.graph.get_context = MagicMock(return_value=MagicMock(context="ctx"))

        search_filters = {"node_labels": ["Preference", "Project"]}
        storage = ZepUserStorage(
            client=mock_client,
            user_uuid=USER_UUID,
            thread_uuid=THREAD_UUID,
            search_filters=search_filters,
            max_characters=1200,
            graph_uuid=GRAPH_UUID,
        )

        # Perform search
        storage.search("test query", limit=5)

        mock_client.graph.get_context.assert_called_once_with(
            GRAPH_UUID,
            query="test query",
            filters=search_filters,
            max_characters=1200,
        )

    def test_reset_does_nothing(self):
        """Test that reset method exists but does nothing."""
        mock_client = _make_mock_zep_client()
        storage = ZepUserStorage(client=mock_client, user_uuid=USER_UUID, thread_uuid=THREAD_UUID)

        # Should not raise exception
        storage.reset()

        # No methods should be called on client
        mock_client.assert_not_called()
