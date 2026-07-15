"""JsonRecordsLoader: JSONL / CSV / JSON-array files → one json episode per record.

Implements the docs' "unified entity" guidance: each record can be given
id/name/description identity fields, a contextualizing record_type, and a
created_at parsed from a record field so structured backfills carry real
timestamps.
"""

import csv
import glob
import json
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from zep_ingest.exceptions import ConfigurationError
from zep_ingest.types import Episode

_SCALARS = (str, int, float, bool)


def _parse_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return datetime.fromtimestamp(float(value), tz=UTC).isoformat()
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.isoformat()


class JsonRecordsLoader:
    def __init__(
        self,
        path_or_glob: str | Path,
        *,
        format: Literal["auto", "jsonl", "csv", "json"] = "auto",
        id_field: str | None = None,
        name_field: str | None = None,
        description_field: str | None = None,
        created_at_field: str | None = None,
        metadata_fields: Sequence[str] = (),
        record_type: str | None = None,
    ) -> None:
        if format not in ("auto", "jsonl", "csv", "json"):
            raise ConfigurationError(
                f"format must be one of ['auto', 'csv', 'json', 'jsonl'], got {format!r}"
            )
        self.pattern = str(path_or_glob)
        self.format = format
        self.id_field = id_field
        self.name_field = name_field
        self.description_field = description_field
        self.created_at_field = created_at_field
        self.metadata_fields = list(metadata_fields)
        self.record_type = record_type
        self.warnings: list[str] = []
        self.files = sorted(
            Path(p) for p in glob.glob(self.pattern, recursive=True) if Path(p).is_file()
        )
        if not self.files:
            raise ConfigurationError(f"No files match {self.pattern!r}.")

    def load(self) -> Iterator[Episode]:
        for file in self.files:
            missing_timestamps = 0
            for record in self._read(file):
                episode, timestamp_missing = self._to_episode(record, file)
                missing_timestamps += timestamp_missing
                yield episode
            if missing_timestamps and self.created_at_field:
                self.warnings.append(
                    f"{file.name}: {missing_timestamps} record(s) missing or with "
                    f"unparseable {self.created_at_field!r}; their episodes have no "
                    "created_at and Zep will default to the ingestion time."
                )

    def _read(self, file: Path) -> Iterator[Any]:
        fmt: str = self.format
        if fmt == "auto":
            suffix = file.suffix.lower()
            fmt = {"jsonl": "jsonl", ".jsonl": "jsonl", ".csv": "csv"}.get(suffix, "json")
        try:
            if fmt == "jsonl":
                for line in file.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        yield json.loads(line)
            elif fmt == "csv":
                with file.open(newline="", encoding="utf-8") as handle:
                    yield from csv.DictReader(handle)
            else:
                parsed = json.loads(file.read_text(encoding="utf-8"))
                yield from parsed if isinstance(parsed, list) else [parsed]
        except (json.JSONDecodeError, ValueError) as error:
            raise ConfigurationError(f"Unparseable records in {file}: {error}") from error

    def _to_episode(self, record: Any, file: Path) -> tuple[Episode, int]:
        created_at: str | None = None
        timestamp_missing = 0
        metadata: dict[str, Any] | None = None
        if isinstance(record, dict):
            record = dict(record)
            for target, source in (
                ("id", self.id_field),
                ("name", self.name_field),
                ("description", self.description_field),
            ):
                if source and source in record:
                    record[target] = record[source]
            if self.record_type:
                record["record_type"] = self.record_type
            if self.created_at_field:
                created_at = _parse_timestamp(record.get(self.created_at_field))
                timestamp_missing = int(created_at is None)
            lifted = {
                f: record[f]
                for f in self.metadata_fields
                if f in record and isinstance(record[f], _SCALARS)
            }
            metadata = lifted or None
        return (
            Episode(
                data=json.dumps(record),
                data_type="json",
                created_at=created_at,
                metadata=metadata,
                source_description=file.name,
            ),
            timestamp_missing,
        )
