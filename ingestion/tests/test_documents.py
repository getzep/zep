"""Tests for document_id helpers and Episode validation."""

import pytest

from zep_ingest.documents import (
    MAX_DOCUMENT_ID_LENGTH,
    document_id_for_path,
    document_id_for_slack_thread,
    document_id_from_parts,
    validate_document_id,
)
from zep_ingest.exceptions import ConfigurationError
from zep_ingest.transforms.chunker import TextChunker
from zep_ingest.types import Destination, Episode, to_batch_item, to_graph_add_kwargs


def test_validate_document_id_rejects_illegal_chars():
    with pytest.raises(ConfigurationError, match="/"):
        validate_document_id("bad/id")


def test_document_id_from_parts_hashes_long_keys():
    long_key = "x" * 200
    doc_id = document_id_from_parts(long_key, prefix="file")
    assert len(doc_id) <= MAX_DOCUMENT_ID_LENGTH
    assert doc_id.startswith("file-")


def test_slack_thread_document_id_is_stable():
    assert document_id_for_slack_thread(
        "#general", "1710000000.000100"
    ) == document_id_for_slack_thread("#general", "1710000000.000100")


def test_episode_document_id_validation():
    with pytest.raises(ConfigurationError, match="document_id"):
        Episode(data="hello", document_id="")


def test_api_mappers_include_document_id():
    episode = Episode(data="chunk", document_id="handbook-v1")
    destination = Destination(graph_id="g1")
    assert to_graph_add_kwargs(episode, destination)["document_id"] == "handbook-v1"
    batch_item = to_batch_item(episode, destination)
    assert batch_item.model_dump()["document_id"] == "handbook-v1"


def test_chunker_preserves_document_id_when_splitting():
    source = Episode(
        data="word " * 300,
        document_id=document_id_for_path(__import__("pathlib").Path("notes.md")),
    )
    chunks = list(TextChunker(chunk_size=100, overlap=0).apply([source]))
    assert len(chunks) > 1
    assert all(chunk.document_id == source.document_id for chunk in chunks)


def test_chunker_keeps_document_id_on_pass_through():
    doc_id = document_id_for_path(__import__("pathlib").Path("notes.md"))
    source = Episode(data="short doc", document_id=doc_id)
    [out] = list(TextChunker().apply([source]))
    assert out.document_id == doc_id


def test_email_always_gets_document_id():
    from pathlib import Path

    from zep_ingest.documents import document_id_for_email_thread

    class Msg:
        def __init__(self, headers: dict[str, str]) -> None:
            self._headers = headers

        def get(self, key: str) -> str | None:
            return self._headers.get(key)

    standalone = document_id_for_email_thread(
        Msg({"Message-ID": "<solo@example.com>"}), Path("solo.eml")
    )
    assert standalone is not None
    assert "solo@example.com" in standalone

    reply = document_id_for_email_thread(
        Msg({"In-Reply-To": "<thread-root@example.com>", "Message-ID": "<reply@example.com>"}),
        Path("reply.eml"),
    )
    assert "thread-root@example.com" in reply
