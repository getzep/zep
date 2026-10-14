"""Basic tests for the CrewAI V4 storage adapter."""

from unittest.mock import MagicMock

import pytest
from zep_cloud.client import Zep

from zep_crewai import ZepStorage


def _client() -> MagicMock:
    client = MagicMock(spec=Zep)
    client.user = MagicMock()
    client.thread = MagicMock()
    client.graph = MagicMock()
    client.graph.episode = MagicMock()
    return client


def _storage(client: MagicMock | None = None) -> ZepStorage:
    return ZepStorage(
        client=client or _client(),
        user_uuid="user-uuid",
        thread_uuid="thread-uuid",
        graph_uuid="graph-uuid",
    )


def test_package_import() -> None:
    import zep_crewai

    assert zep_crewai.__version__ == "1.3.0"


def test_storage_import() -> None:
    assert ZepStorage is not None


def test_storage_requires_uuids() -> None:
    client = _client()
    with pytest.raises(ValueError, match="user_uuid and thread_uuid"):
        ZepStorage(client, user_uuid="", thread_uuid="thread-uuid")


def test_storage_requires_zep_client() -> None:
    with pytest.raises(TypeError, match="client must be an instance of Zep"):
        ZepStorage("invalid", user_uuid="u", thread_uuid="t")  # type: ignore[arg-type]


def test_storage_properties() -> None:
    storage = _storage()
    assert storage.user_uuid == "user-uuid"
    assert storage.thread_uuid == "thread-uuid"
    assert storage.graph_uuid == "graph-uuid"


def test_message_save_uses_v4_thread_uuid() -> None:
    client = _client()
    _storage(client).save("hello", {"type": "message", "role": "user", "name": "Alice"})
    client.thread.add_messages.assert_called_once()
    assert client.thread.add_messages.call_args.args[0] == "thread-uuid"
    message = client.thread.add_messages.call_args.kwargs["messages"][0]
    assert (message.content, message.role, message.name) == ("hello", "user", "Alice")


def test_graph_save_uses_v4_episode_api() -> None:
    client = _client()
    _storage(client).save("likes tea", {"type": "text"})
    client.graph.episode.add.assert_called_once_with(
        "graph-uuid", data="likes tea", type="text", metadata={"type": "text"}
    )


def test_graph_save_defaults_to_text() -> None:
    client = _client()
    _storage(client).save("likes tea")
    assert client.graph.episode.add.call_args.kwargs["type"] == "text"


def test_save_does_not_raise_on_sdk_error(caplog: pytest.LogCaptureFixture) -> None:
    client = _client()
    client.thread.add_messages.side_effect = RuntimeError("offline")
    with caplog.at_level("ERROR"):
        _storage(client).save("hello", {"type": "message"})
    assert "offline" in caplog.text


def test_search_uses_auto_context() -> None:
    client = _client()
    client.graph.get_context.return_value.context = "known fact"
    result = _storage(client).search("question")
    assert "known fact" in result[0]["context"]
    client.graph.get_context.assert_called_once()
    assert client.graph.get_context.call_args.args[0] == "graph-uuid"


def test_search_sdk_error_degrades_to_empty() -> None:
    client = _client()
    client.graph.get_context.side_effect = RuntimeError("offline")
    assert _storage(client).search("question") == []


def test_reset_does_not_delete_persistent_resources() -> None:
    client = _client()
    _storage(client).reset()
    client.user.delete.assert_not_called()


def test_storage_resolves_graph_uuid_once_when_omitted() -> None:
    client = _client()
    client.user.get.return_value.graph_uuid = "resolved-graph"
    storage = ZepStorage(client, user_uuid="user-uuid", thread_uuid="thread-uuid")
    assert storage.graph_uuid == "resolved-graph"
    client.user.get.assert_called_once_with("user-uuid")
