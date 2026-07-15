"""TextChunker: paragraph-aware document chunking per Zep's cookbook.

Defaults (500 chars, 50 overlap) match the documented guidance: smaller,
focused chunks yield richer knowledge graphs than large ones.
"""

from collections.abc import Iterable, Iterator

from zep_ingest._validation import require_int_range
from zep_ingest.exceptions import ConfigurationError
from zep_ingest.transforms._splitting import split_text
from zep_ingest.types import MAX_METADATA_KEYS, Episode


class TextChunker:
    def __init__(
        self,
        *,
        chunk_size: int = 500,
        overlap: int = 50,
        min_chunk_size: int = 100,
        data_types: frozenset[str] = frozenset({"text"}),
        max_document_chars: int = 50_000,
    ) -> None:
        require_int_range("chunk_size", chunk_size, minimum=1)
        require_int_range("overlap", overlap, minimum=0)
        require_int_range("min_chunk_size", min_chunk_size, minimum=0)
        require_int_range("max_document_chars", max_document_chars, minimum=1)
        if overlap >= chunk_size:
            raise ConfigurationError(
                f"overlap must be smaller than chunk_size, got {overlap} >= {chunk_size}"
            )
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.min_chunk_size = min_chunk_size
        self.data_types = data_types
        self.max_document_chars = max_document_chars
        self.warnings: list[str] = []

    def apply(self, episodes: Iterable[Episode]) -> Iterator[Episode]:
        for episode in episodes:
            if episode.data_type not in self.data_types or len(episode.data) <= self.chunk_size:
                yield episode
                continue
            pieces = split_text(episode.data, self.chunk_size, self.overlap)
            if (
                len(pieces) > 1
                and len(pieces[-1]) < self.min_chunk_size
                and len(pieces[-2]) + 1 + len(pieces[-1]) <= self.chunk_size
            ):
                pieces[-2] = f"{pieces[-2]} {pieces.pop()}"
            if len(pieces) == 1:
                yield episode
                continue
            document = episode.data[: self.max_document_chars]
            total = len(pieces)
            base_metadata = dict(episode.metadata or {})
            include_chunk = "chunk" in base_metadata or len(base_metadata) < MAX_METADATA_KEYS
            if not include_chunk:
                self.warnings.append(
                    "Internal 'chunk' metadata marker omitted because the episode already has "
                    f"the API maximum of {MAX_METADATA_KEYS} metadata keys."
                )
            for i, piece in enumerate(pieces, start=1):
                metadata = dict(base_metadata)
                if include_chunk:
                    metadata["chunk"] = f"{i}/{total}"
                yield Episode(
                    data=piece,
                    data_type=episode.data_type,
                    created_at=episode.created_at,
                    metadata=metadata,
                    source_description=episode.source_description,
                    document=document,
                )
