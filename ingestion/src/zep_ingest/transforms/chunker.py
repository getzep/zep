"""TextChunker: paragraph-aware document chunking per Zep's cookbook.

Defaults (500 chars, 50 overlap) match the documented guidance: smaller,
focused chunks yield richer knowledge graphs than large ones.
"""

from collections.abc import Iterable, Iterator

from zep_ingest.transforms._splitting import split_text
from zep_ingest.types import Episode


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
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.min_chunk_size = min_chunk_size
        self.data_types = data_types
        self.max_document_chars = max_document_chars

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
            for i, piece in enumerate(pieces, start=1):
                metadata = dict(episode.metadata or {})
                metadata["chunk"] = f"{i}/{total}"
                yield Episode(
                    data=piece,
                    data_type=episode.data_type,
                    created_at=episode.created_at,
                    metadata=metadata,
                    source_description=episode.source_description,
                    document=document,
                )
