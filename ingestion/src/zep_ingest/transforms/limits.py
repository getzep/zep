"""LimitGuard: the always-on safety net for the 10,000-character episode limit.

Pipeline appends this after all user transforms, so no episode ever reaches
the API oversized — users never think about the limit. Splitting is
boundary-aware per data type: paragraphs/sentences for text, whole lines for
message, top-level structure for JSON.
"""

from collections.abc import Iterable, Iterator
from dataclasses import replace

from zep_ingest.transforms._splitting import (
    hard_split,
    split_json_top_level,
    split_lines,
    split_text,
)
from zep_ingest.types import SAFE_EPISODE_CHARS, Episode


class LimitGuard:
    def __init__(self, *, limit: int = SAFE_EPISODE_CHARS) -> None:
        self.limit = limit
        self.warnings: list[str] = []

    def apply(self, episodes: Iterable[Episode]) -> Iterator[Episode]:
        for episode in episodes:
            if len(episode.data) <= self.limit:
                yield episode
                continue
            pieces = self._split(episode)
            total = len(pieces)
            if total == 1:
                # Splitting can shrink an over-limit episode into one piece
                # (stripped whitespace, compact JSON re-render) — yield the
                # piece, never the original over-limit data.
                yield replace(episode, data=pieces[0])
                continue
            for i, piece in enumerate(pieces, start=1):
                metadata = dict(episode.metadata or {})
                metadata["part"] = f"{i}/{total}"
                yield Episode(
                    data=piece,
                    data_type=episode.data_type,
                    created_at=episode.created_at,
                    metadata=metadata,
                    source_description=episode.source_description,
                    document=episode.document,
                )

    def _split(self, episode: Episode) -> list[str]:
        if episode.data_type == "message":
            return split_lines(episode.data, self.limit)
        if episode.data_type == "json":
            pieces = split_json_top_level(episode.data, self.limit)
            if pieces is None:
                self.warnings.append(
                    "A json episode exceeded the episode size limit and could not be "
                    "split at the top level; it was hard-split and the pieces are not "
                    "valid JSON. Pre-shape large JSON (see JsonNormalizer) to avoid this."
                )
                return hard_split(episode.data, self.limit)
            if len(pieces) > 1:  # a compact re-render can fit in one piece
                self.warnings.append(
                    "A json episode exceeded the episode size limit and was split at "
                    "the top level; cross-references between pieces may be lost. "
                    "Pre-shape large JSON (see JsonNormalizer) for better results."
                )
            # a single element can still be oversize; hard-split those pieces
            flattened: list[str] = []
            for piece in pieces:
                if len(piece) > self.limit:
                    flattened.extend(hard_split(piece, self.limit))
                else:
                    flattened.append(piece)
            return flattened
        return split_text(episode.data, self.limit, overlap=0)
