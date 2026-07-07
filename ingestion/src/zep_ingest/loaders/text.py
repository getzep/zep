"""TextFileLoader: plain-text / Markdown files → text episodes.

One episode per file; the downstream TextChunker splits anything over the
chunk size. created_at defaults to the file's mtime so backfilled documents
carry a real timestamp (pass created_at to override, e.g. a publication date).
"""

import glob
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from zep_ingest.exceptions import ConfigurationError
from zep_ingest.types import Episode


class TextFileLoader:
    def __init__(self, path_or_glob: str | Path, *, created_at: str | None = None) -> None:
        self.pattern = str(path_or_glob)
        self.created_at = created_at
        self.files = sorted(
            Path(p) for p in glob.glob(self.pattern, recursive=True) if Path(p).is_file()
        )
        if not self.files:
            raise ConfigurationError(f"No files match {self.pattern!r}.")

    def load(self) -> Iterator[Episode]:
        for file in self.files:
            created_at = self.created_at or (
                datetime.fromtimestamp(file.stat().st_mtime, tz=UTC).isoformat()
            )
            yield Episode(
                data=file.read_text(encoding="utf-8"),
                data_type="text",
                created_at=created_at,
                source_description=file.name,
            )
