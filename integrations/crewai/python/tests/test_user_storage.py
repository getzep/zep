"""Tests for V4 user and thread storage."""

from unittest.mock import MagicMock

import pytest
from zep_cloud.client import Zep
from zep_cloud.types import SearchFilters

from zep_crewai import ZepUserStorage


def _client() -> MagicMock:
    client = MagicMock(spec=Zep)
    client.user = MagicMock()
    client.thread = MagicMock()
    client.graph = MagicMock()
    client.graph.episode = MagicMock()
    client.user.get.return_value.graph_uuid = "resolved-graph"
    return client


def test_initialization_success() -> None:
    storage = ZepUserStorage(
        _client(),
        user_uuid="user-uuid",
        thread_uuid="thread-uuid",
        graph_uuid="graph-uuid",
    )
    assert storage.user_uuid == "user-uuid"
    assert storage.thread_uuid == "thread-uuid"
    assert storage.graph_uuid == "graph-uuid"


def test_graph_uuid_is_resolved_once_when_omitted() -> None:
    client = _client()
    storage = ZepUserStorage(client, user_uuid="u", thread_uuid="t")
    assert storage.graph_uuid == "resolved-graph"
    client.user.get.assert_called_once_with("u")


def test_mode_is_deprecated() -> None:
    with pytest.warns(DeprecationWarning, match="mode"):
        ZepUserStorage(_client(), "u", "t", graph_uuid="g", mode="summary")


def test_initialization_requires_user_and_thread_uuids() -> None:
    with pytest.raises(ValueError, match="user_uuid and thread_uuid"):
        ZepUserStorage(_client(), "", "t", graph_uuid="g")
    with pytest.raises(ValueError, match="user_uuid and thread_uuid"):
        ZepUserStorage(_client(), "u", "", graph_uuid="g")


def test_initialization_requires_zep_client() -> None:
    with pytest.raises(TypeError, match="client must be an instance of Zep"):
        ZepUserStorage("invalid", "u", "t", graph_uuid="g")  # type: ignore[arg-type]


def test_save_message_uses_thread_uuid() -> None:
    client = _client()
    storage = ZepUserStorage(client, "u", "t", graph_uuid="g")
    storage.save("Hello", {"type": "message", "role": "assistant", "name": "Helper"})
    client.thread.add_messages.assert_called_once()
    assert client.thread.add_messages.call_args.args[0] == "t"
    message = client.thread.add_messages.call_args.kwargs["messages"][0]
    assert (message.content, message.role, message.name) == ("Hello", "assistant", "Helper")


def test_save_json_data_uses_episode_api() -> None:
    client = _client()
    ZepUserStorage(client, "u", "t", graph_uuid="g").save('{"k": "v"}', {"type": "json"})
    client.graph.episode.add.assert_called_once_with(
        "g", data='{"k": "v"}', type="json", metadata={"type": "json"}
    )


def test_save_text_data_uses_episode_api() -> None:
    client = _client()
    ZepUserStorage(client, "u", "t", graph_uuid="g").save("prefers tea", {"type": "text"})
    assert client.graph.episode.add.call_args.args[0] == "g"


def test_save_does_not_raise_on_sdk_error(caplog: pytest.LogCaptureFixture) -> None:
    client = _client()
    client.graph.episode.add.side_effect = RuntimeError("offline")
    storage = ZepUserStorage(client, "u", "t", graph_uuid="g")
    with caplog.at_level("ERROR"):
        storage.save("prefers tea", {"type": "text"})
    assert "offline" in caplog.text


def test_search_uses_graph_get_context_and_filters() -> None:
    client = _client()
    client.graph.get_context.return_value.context = "Known fact"
    filters = SearchFilters(node_labels=["Preference"])
    storage = ZepUserStorage(
        client, "u", "t", graph_uuid="g", search_filters=filters, max_characters=1000
    )
    results = storage.search("query")
    assert "Known fact" in results[0]["context"]
    client.graph.get_context.assert_called_once_with(
        "g",
        query="query",
        filters=filters,
        max_characters=1000,
    )


def test_search_handles_empty_context() -> None:
    client = _client()
    client.graph.get_context.return_value.context = None
    assert ZepUserStorage(client, "u", "t", graph_uuid="g").search("query") == []


def test_get_context_uses_v4_thread_method() -> None:
    client = _client()
    client.thread.get_context.return_value.context = "Thread context"
    storage = ZepUserStorage(client, "u", "t", graph_uuid="g", template_uuid="template")
    assert storage.get_context() == "Thread context"
    client.thread.get_context.assert_called_once_with("t", template_uuid="template")


def test_get_context_degrades_on_error() -> None:
    client = _client()
    client.thread.get_context.side_effect = RuntimeError("offline")
    assert ZepUserStorage(client, "u", "t", graph_uuid="g").get_context() is None


def test_reset_keeps_resources() -> None:
    client = _client()
    ZepUserStorage(client, "u", "t", graph_uuid="g").reset()
    client.user.delete.assert_not_called()
