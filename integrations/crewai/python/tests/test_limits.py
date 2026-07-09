"""
Tests for Zep size-limit handling (``zep_crewai.limits``) and its application
in the storage save paths and ``ZepAddDataTool``.
"""

from __future__ import annotations

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


def _make_mock_client() -> MagicMock:
    client = MagicMock(spec=Zep)
    client.user = MagicMock()
    client.thread = MagicMock()
    client.graph = MagicMock()
    return client


class TestTruncateHelpers:
    def test_message_within_limit_unchanged(self) -> None:
        content = "x" * MESSAGE_CONTENT_MAX
        assert truncate_message_content(content) is content

    def test_message_over_limit_truncated(self, caplog) -> None:
        content = "x" * (MESSAGE_CONTENT_MAX + 1)
        with caplog.at_level("WARNING"):
            truncated = truncate_message_content(content)
        assert len(truncated) == MESSAGE_CONTENT_TRUNCATE_TO
        # Lengths-only logging: content must never reach the logs.
        assert "xxx" not in caplog.text

    def test_graph_data_within_limit_unchanged(self) -> None:
        data = "y" * GRAPH_MAX_CHARS
        assert truncate_graph_data(data) is data

    def test_graph_data_over_limit_truncated(self, caplog) -> None:
        data = "y" * (GRAPH_MAX_CHARS + 1)
        with caplog.at_level("WARNING"):
            truncated = truncate_graph_data(data)
        assert len(truncated) == GRAPH_MAX_CHARS
        assert "yyy" not in caplog.text


class TestSaveTruncation:
    def test_save_truncates_oversize(self) -> None:
        """ZepUserStorage.save() bounds an over-long message before
        thread.add_messages."""
        client = _make_mock_client()

        storage = ZepUserStorage(client=client, user_id="u1", thread_id="t1")
        storage.save("z" * 5000, metadata={"type": "message", "role": "user"})

        message = client.thread.add_messages.call_args.kwargs["messages"][0]
        assert len(message.content) == MESSAGE_CONTENT_TRUNCATE_TO

    def test_zep_storage_save_truncates_oversize(self) -> None:
        """ZepStorage.save() bounds an over-long message the same way."""
        client = _make_mock_client()

        storage = ZepStorage(client=client, user_id="u1", thread_id="t1")
        storage.save("z" * 5000, metadata={"type": "message", "role": "user"})

        message = client.thread.add_messages.call_args.kwargs["messages"][0]
        assert len(message.content) == MESSAGE_CONTENT_TRUNCATE_TO

    def test_graph_save_truncates_oversize(self) -> None:
        """ZepGraphStorage.save() bounds an over-long graph payload at
        GRAPH_MAX_CHARS."""
        client = _make_mock_client()

        storage = ZepGraphStorage(client=client, graph_id="g1")
        storage.save("z" * 15000, metadata={"type": "text"})

        data = client.graph.add.call_args.kwargs["data"]
        assert len(data) == GRAPH_MAX_CHARS

    def test_add_data_tool_truncates_oversize(self) -> None:
        """ZepAddDataTool bounds an over-long graph payload at
        GRAPH_MAX_CHARS."""
        client = _make_mock_client()

        tool = ZepAddDataTool(client=client, graph_id="g1")
        tool._run("z" * 15000, data_type="text")

        data = client.graph.add.call_args.kwargs["data"]
        assert len(data) == GRAPH_MAX_CHARS
