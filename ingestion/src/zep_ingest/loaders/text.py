"""TextFileLoader: plain-text / Markdown files → text episodes.

One episode per file; the downstream TextChunker splits anything over the
chunk size. Supply a source ``created_at`` when known. Filesystem mtime is used
only with explicit ``use_file_mtime=True`` because copy time is not a reliable
factual timestamp.
"""

from collections.abc import Iterator
from datetime import UTC, datetime

from zep_ingest._io import resolve_source_files, source_paths_label
from zep_ingest.types import Episode, SourcePaths


class TextFileLoader:
    def __init__(
        self,
        path_or_glob: SourcePaths,
        *,
        created_at: str | None = None,
        use_file_mtime: bool = False,
    ) -> None:
        self.pattern = source_paths_label(path_or_glob)
        self.created_at = created_at
        self.use_file_mtime = use_file_mtime
        self.warnings: list[str] = []
        self.files = resolve_source_files(path_or_glob)

    def load(self) -> Iterator[Episode]:
        for file in self.files:
            created_at = self.created_at
            if created_at is None and self.use_file_mtime:
                created_at = datetime.fromtimestamp(file.stat().st_mtime, tz=UTC).isoformat()
                self.warnings.append(
                    f"{file.name}: using filesystem modification time as created_at by explicit "
                    "request; verify it represents the document's source date."
                )
            yield Episode(
                data=file.read_text(encoding="utf-8"),
                data_type="text",
                created_at=created_at,
                metadata={"source_type": "document", "file_name": file.name},
            )
