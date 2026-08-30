"""Tests for document saga poll tail selection."""

from zep_ingest.result import IngestResult
from zep_ingest.types import Episode


def test_document_poll_tail_uses_latest_created_at():
    result = IngestResult(method="sequential")
    result.record_sequential_episode(
        Episode(data="older chunk", document_id="doc-1", created_at="2024-01-01T00:00:00Z"),
        "uuid-older",
    )
    result.record_sequential_episode(
        Episode(data="newer chunk", document_id="doc-1", created_at="2024-06-01T00:00:00Z"),
        "uuid-newer",
    )
    result.finalize_sequential_episode_poll()
    assert result.episode_uuids == ["uuid-newer"]


def test_document_poll_tail_submission_order_when_created_at_missing():
    result = IngestResult(method="sequential")
    result.record_sequential_episode(
        Episode(data="first", document_id="doc-1", created_at=None),
        "uuid-first",
    )
    result.record_sequential_episode(
        Episode(data="second", document_id="doc-1", created_at=None),
        "uuid-second",
    )
    result.finalize_sequential_episode_poll()
    assert result.episode_uuids == ["uuid-second"]
