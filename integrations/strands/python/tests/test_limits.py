"""Tests for message / graph payload truncation helpers."""

from zep_strands._text import (
    GRAPH_DATA_TRUNCATE_LIMIT,
    MESSAGE_TRUNCATE_LIMIT,
    truncate_graph_data,
    truncate_message_content,
)


def test_truncate_message_content_passthrough() -> None:
    assert truncate_message_content("hello", label="user message") == "hello"


def test_truncate_message_content_cuts_long() -> None:
    long = "x" * (MESSAGE_TRUNCATE_LIMIT + 50)
    out = truncate_message_content(long, label="user message")
    assert len(out) == MESSAGE_TRUNCATE_LIMIT


def test_truncate_graph_data_cuts_long() -> None:
    long = "y" * (GRAPH_DATA_TRUNCATE_LIMIT + 100)
    out = truncate_graph_data(long, label="graph data")
    assert len(out) == GRAPH_DATA_TRUNCATE_LIMIT
