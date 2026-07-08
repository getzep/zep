"""
Tests for Zep size-limit truncation: ``ZepUserMemory.add`` (thread messages),
``ZepGraphMemory.add`` (graph data), and the ``create_add_graph_data_tool``
tool factory.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from autogen_core.memory import MemoryContent, MemoryMimeType
from zep_cloud.client import AsyncZep

from zep_autogen import ZepGraphMemory, ZepUserMemory
from zep_autogen.limits import (
    GRAPH_MAX_CHARS,
    MESSAGE_CONTENT_MAX,
    MESSAGE_CONTENT_TRUNCATE_TO,
    truncate_graph_data,
    truncate_message_content,
)
from zep_autogen.tools import create_add_graph_data_tool


def _make_mock_client() -> MagicMock:
    client = MagicMock(spec=AsyncZep)
    client.user = MagicMock()
    client.user.add = AsyncMock()
    client.thread = MagicMock()
    client.thread.create = AsyncMock()
    client.thread.add_messages = AsyncMock()
    client.graph = MagicMock()
    client.graph.add = AsyncMock()
    return client


class TestTruncateMessageContent:
    def test_short_content_unchanged(self) -> None:
        assert truncate_message_content("hello") == "hello"

    def test_oversize_content_truncated(self, caplog: pytest.LogCaptureFixture) -> None:
        long_text = "x" * (MESSAGE_CONTENT_MAX + 500)
        with caplog.at_level("WARNING"):
            result = truncate_message_content(long_text, label="user")
        assert len(result) == MESSAGE_CONTENT_TRUNCATE_TO
        assert "Truncated user content" in caplog.text
        # Never log the actual content.
        assert "x" * 50 not in caplog.text


class TestTruncateGraphData:
    def test_short_data_unchanged(self) -> None:
        assert truncate_graph_data("hello") == "hello"

    def test_oversize_data_truncated(self, caplog: pytest.LogCaptureFixture) -> None:
        long_text = "y" * (GRAPH_MAX_CHARS + 1000)
        with caplog.at_level("WARNING"):
            result = truncate_graph_data(long_text)
        assert len(result) == GRAPH_MAX_CHARS
        assert "Truncated" in caplog.text
        assert "y" * 50 not in caplog.text


class TestAddTruncatesOversizeMessage:
    @pytest.mark.asyncio
    async def test_add_truncates_oversize_message(self) -> None:
        client = _make_mock_client()
        memory = ZepUserMemory(client=client, user_id="test-user", thread_id="test-session")

        long_text = "a" * (MESSAGE_CONTENT_MAX + 200)
        content = MemoryContent(
            content=long_text,
            mime_type=MemoryMimeType.TEXT,
            metadata={"type": "message", "role": "user"},
        )

        await memory.add(content)

        client.thread.add_messages.assert_called_once()
        sent_message = client.thread.add_messages.call_args.kwargs["messages"][0]
        assert len(sent_message.content) == MESSAGE_CONTENT_TRUNCATE_TO


class TestUserMemoryDataAddTruncatesOversize:
    @pytest.mark.asyncio
    async def test_user_memory_data_add_truncates_oversize(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = _make_mock_client()
        memory = ZepUserMemory(client=client, user_id="test-user", thread_id="test-session")

        long_text = "d" * (GRAPH_MAX_CHARS + 500)
        content = MemoryContent(
            content=long_text,
            mime_type=MemoryMimeType.TEXT,
            metadata={"type": "data"},
        )

        with caplog.at_level("WARNING"):
            await memory.add(content)

        client.graph.add.assert_called_once()
        sent_data = client.graph.add.call_args.kwargs["data"]
        assert len(sent_data) == GRAPH_MAX_CHARS
        assert "Truncated" in caplog.text
        assert "d" * 50 not in caplog.text


class TestGraphAddTruncatesOversize:
    @pytest.mark.asyncio
    async def test_graph_add_truncates_oversize(self) -> None:
        client = _make_mock_client()
        memory = ZepGraphMemory(client=client, graph_id="test-graph")

        long_text = "b" * (GRAPH_MAX_CHARS + 500)
        content = MemoryContent(
            content=long_text,
            mime_type=MemoryMimeType.TEXT,
            metadata={"type": "data"},
        )

        await memory.add(content)

        client.graph.add.assert_called_once()
        sent_data = client.graph.add.call_args.kwargs["data"]
        assert len(sent_data) == GRAPH_MAX_CHARS

    @pytest.mark.asyncio
    async def test_add_graph_data_tool_truncates_oversize(self) -> None:
        client = _make_mock_client()
        tool = create_add_graph_data_tool(client, user_id="test-user")

        long_text = "c" * (GRAPH_MAX_CHARS + 500)
        from autogen_core import CancellationToken

        await tool.run_json({"data": long_text}, CancellationToken())

        client.graph.add.assert_called_once()
        sent_data = client.graph.add.call_args.kwargs["data"]
        assert len(sent_data) == GRAPH_MAX_CHARS
