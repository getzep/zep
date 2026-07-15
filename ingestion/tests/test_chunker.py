"""Tests for TextChunker — the docs-cookbook paragraph→sentence→char splitter."""

import pytest

from zep_ingest.exceptions import ConfigurationError
from zep_ingest.transforms.chunker import TextChunker
from zep_ingest.types import Episode


def apply(chunker: TextChunker, *episodes: Episode) -> list[Episode]:
    return list(chunker.apply(episodes))


def make_long_text(paragraphs: int = 20, sentence: str = "The quick brown fox jumps.") -> str:
    return "\n\n".join(" ".join([sentence] * 8) for _ in range(paragraphs))


class TestPassThrough:
    def test_short_text_untouched(self):
        ep = Episode(data="short doc", created_at="2024-01-01T00:00:00Z")
        [out] = apply(TextChunker(), ep)
        assert out.data == "short doc"
        assert out.document is None
        assert out.metadata is None

    def test_message_and_json_pass_through_regardless_of_size(self):
        long = "x " * 2000
        message = Episode(data=long, data_type="message")
        json_ep = Episode(data=long, data_type="json")
        out = apply(TextChunker(chunk_size=100), message, json_ep)
        assert [ep.data for ep in out] == [long, long]

    def test_empty_text_is_rejected(self):
        with pytest.raises(ConfigurationError, match="non-empty"):
            Episode(data="")


class TestSplitting:
    def test_chunks_within_chunk_size(self):
        text = make_long_text()
        chunks = apply(TextChunker(chunk_size=500, overlap=50), Episode(data=text))
        assert len(chunks) > 1
        assert all(len(c.data) <= 500 for c in chunks)

    def test_prefers_paragraph_boundaries(self):
        paragraphs = [f"Paragraph number {i} content here." for i in range(10)]
        text = "\n\n".join(paragraphs)
        chunks = apply(TextChunker(chunk_size=120, overlap=0), Episode(data=text))
        for chunk in chunks:
            # every chunk should consist of whole paragraphs when none is oversize
            for para in chunk.data.split("\n\n"):
                assert para in paragraphs

    def test_oversize_paragraph_split_on_sentences(self):
        text = " ".join(f"Sentence number {i} is here." for i in range(50))
        chunks = apply(TextChunker(chunk_size=100, overlap=0), Episode(data=text))
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk.data) <= 100
            assert chunk.data.rstrip().endswith(".")

    def test_pathological_unbroken_string_hard_split(self):
        text = "a" * 1200
        chunks = apply(TextChunker(chunk_size=500, overlap=0), Episode(data=text))
        assert all(len(c.data) <= 500 for c in chunks)
        assert "".join(c.data for c in chunks) == text

    def test_overlap_carries_tail_of_previous_chunk(self):
        text = make_long_text()
        chunks = apply(TextChunker(chunk_size=400, overlap=60), Episode(data=text))
        first, second = chunks[0].data, chunks[1].data
        # the second chunk begins with words that appear at the end of the first
        overlap_head = second.split("\n\n")[0].split(" ")[0]
        assert overlap_head in first

    def test_content_preserved_without_overlap(self):
        paragraphs = [f"Unique paragraph {i}." for i in range(30)]
        text = "\n\n".join(paragraphs)
        chunks = apply(TextChunker(chunk_size=100, overlap=0), Episode(data=text))
        joined = "\n\n".join(c.data for c in chunks)
        for para in paragraphs:
            assert para in joined


class TestChunkMetadata:
    def test_fields_propagate_and_chunk_index_added(self):
        ep = Episode(
            data=make_long_text(),
            created_at="2024-06-15T10:30:00Z",
            metadata={"source": "handbook"},
            source_description="employee handbook",
        )
        chunks = apply(TextChunker(chunk_size=500), ep)
        n = len(chunks)
        for i, chunk in enumerate(chunks, start=1):
            assert chunk.created_at == "2024-06-15T10:30:00Z"
            assert chunk.metadata is not None
            assert chunk.metadata["source"] == "handbook"
            assert chunk.metadata["chunk"] == f"{i}/{n}"
            assert chunk.source_description == "employee handbook"

    def test_parent_metadata_not_mutated(self):
        metadata = {"source": "handbook"}
        ep = Episode(data=make_long_text(), metadata=metadata)
        apply(TextChunker(chunk_size=500), ep)
        assert metadata == {"source": "handbook"}

    def test_full_metadata_map_is_preserved_without_chunk_key(self):
        metadata = {f"k{i}": i for i in range(10)}
        chunker = TextChunker(chunk_size=100, overlap=0)

        chunks = apply(chunker, Episode(data=make_long_text(), metadata=metadata))

        assert len(chunks) > 1
        assert all(chunk.metadata == metadata for chunk in chunks)
        assert any("chunk" in warning for warning in chunker.warnings)

    def test_document_set_to_original_only_when_split(self):
        long_ep = Episode(data=make_long_text())
        chunks = apply(TextChunker(chunk_size=500), long_ep)
        assert all(c.document == long_ep.data for c in chunks)
        [short] = apply(TextChunker(), Episode(data="tiny"))
        assert short.document is None

    def test_document_capped(self):
        text = make_long_text(paragraphs=100)
        chunker = TextChunker(chunk_size=500, max_document_chars=1000)
        chunks = apply(chunker, Episode(data=text))
        assert all(len(c.document or "") <= 1000 for c in chunks)
