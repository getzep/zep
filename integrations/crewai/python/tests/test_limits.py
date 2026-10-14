"""Tests for V4 payload limits."""

from unittest.mock import MagicMock

from zep_cloud.client import Zep

from zep_crewai import ZepAddDataTool, ZepGraphStorage, ZepStorage, ZepUserStorage
from zep_crewai.limits import (
    GRAPH_MAX_CHARS,
    MESSAGE_CONTENT_MAX,
    MESSAGE_CONTENT_TRUNCATE_TO,
    truncate_graph_data,
    truncate_message_content,
)


def _client() -> MagicMock:
    client = MagicMock(spec=Zep)
    client.user = MagicMock()
    client.thread = MagicMock()
    client.graph = MagicMock()
    client.graph.episode = MagicMock()
    return client


def test_message_under_limit_is_unchanged() -> None:
    value = "x" * MESSAGE_CONTENT_MAX
    assert truncate_message_content(value) is value


def test_message_over_limit_is_truncated(caplog) -> None:
    with caplog.at_level("WARNING"):
        result = truncate_message_content("x" * (MESSAGE_CONTENT_MAX + 1))
    assert len(result) == MESSAGE_CONTENT_TRUNCATE_TO
    assert "xxx" not in caplog.text


def test_graph_data_under_limit_is_unchanged() -> None:
    value = "y" * GRAPH_MAX_CHARS
    assert truncate_graph_data(value) is value


def test_graph_data_over_limit_is_truncated(caplog) -> None:
    with caplog.at_level("WARNING"):
        result = truncate_graph_data("y" * (GRAPH_MAX_CHARS + 1))
    assert len(result) == GRAPH_MAX_CHARS
    assert "yyy" not in caplog.text


def test_user_storage_truncates_message() -> None:
    client = _client()
    storage = ZepUserStorage(client, "u", "t", graph_uuid="g")
    storage.save("x" * 5000, {"type": "message"})
    message = client.thread.add_messages.call_args.kwargs["messages"][0]
    assert len(message.content) == MESSAGE_CONTENT_TRUNCATE_TO


def test_compat_storage_truncates_message() -> None:
    client = _client()
    storage = ZepStorage(client, "u", "t", graph_uuid="g")
    storage.save("x" * 5000, {"type": "message"})
    message = client.thread.add_messages.call_args.kwargs["messages"][0]
    assert len(message.content) == MESSAGE_CONTENT_TRUNCATE_TO


def test_graph_storage_truncates_episode() -> None:
    client = _client()
    ZepGraphStorage(client, "g").save("x" * 15000)
    assert len(client.graph.episode.add.call_args.kwargs["data"]) == GRAPH_MAX_CHARS


def test_add_data_tool_truncates_episode() -> None:
    client = _client()
    ZepAddDataTool(client, graph_uuid="g")._run("x" * 15000)
    assert len(client.graph.episode.add.call_args.kwargs["data"]) == GRAPH_MAX_CHARS
