"""JsonRecordsLoader: JSONL / CSV / JSON-array files → one json episode per record.

Implements the docs' "unified entity" guidance: each record can be given
id/name/description identity fields, a contextualizing record_type, and a
created_at parsed from a record field so structured backfills carry real
timestamps.

Every episode carries source_type/file_name provenance, which a record field
of the same name can never overwrite, so metadata_fields may lift at most
MAX_METADATA_FIELDS fields alongside it.
"""

import csv
import glob
import json
import math
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from zep_ingest._validation import is_scalar_or_scalar_array
from zep_ingest.exceptions import ConfigurationError
from zep_ingest.types import MAX_METADATA_KEYS, Episode

# provenance the loader stamps on every episode; record fields with these
# names are not liftable, so what the episode reports about its own origin
# stays trustworthy (source_type is always 'json_record')
_RESERVED_METADATA_KEYS = ("source_type", "file_name")
MAX_METADATA_FIELDS = MAX_METADATA_KEYS - len(_RESERVED_METADATA_KEYS)


def _parse_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        try:
            return datetime.fromtimestamp(float(value), tz=UTC).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.isoformat()


def _find_non_finite(value: Any, path: str = "") -> tuple[str, float] | None:
    """Locate the first NaN / Infinity / -Infinity anywhere inside a record.

    Returns the dotted path to the offending value ('price', 'dims.w',
    'sizes[0].w') so a bad row in a large file is findable, or None if the
    record is clean. The path is empty when the record is itself such a number.
    """
    if isinstance(value, float) and not math.isfinite(value):
        return path, value
    if isinstance(value, dict):
        for key, item in value.items():
            found = _find_non_finite(item, f"{path}.{key}" if path else str(key))
            if found is not None:
                return found
    elif isinstance(value, list | tuple):
        for position, item in enumerate(value):
            found = _find_non_finite(item, f"{path}[{position}]")
            if found is not None:
                return found
    return None


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
        # rejected up front, before any file is touched: an over-budget request
        # otherwise survives preview() and aborts run() on the first record that
        # happens to carry every requested field
        if len(self.metadata_fields) > MAX_METADATA_FIELDS:
            raise ConfigurationError(
                f"metadata_fields names {len(self.metadata_fields)} fields; at most "
                f"{MAX_METADATA_FIELDS} are allowed. Every episode reserves "
                f"{len(_RESERVED_METADATA_KEYS)} of the API's {MAX_METADATA_KEYS} metadata "
                f"keys for provenance ({', '.join(_RESERVED_METADATA_KEYS)})."
            )
        self._liftable_fields = [
            f for f in self.metadata_fields if f not in _RESERVED_METADATA_KEYS
        ]
        self.record_type = record_type
        self.warnings: list[str] = []
        self.files = sorted(
            Path(p) for p in glob.glob(self.pattern, recursive=True) if Path(p).is_file()
        )
        if not self.files:
            raise ConfigurationError(f"No files match {self.pattern!r}.")

    def load(self) -> Iterator[Episode]:
        # a collision is a property of metadata_fields, not of the records, so
        # warn once per pass — preview() and run() then report it identically
        reserved = [f for f in self.metadata_fields if f in _RESERVED_METADATA_KEYS]
        if reserved:
            self.warnings.append(
                f"metadata_fields names {', '.join(reserved)}, which the loader stamps as "
                "provenance on every episode; those record fields were not lifted into "
                "metadata and their values remain only in the episode body."
            )
        for file in self.files:
            missing_timestamps = 0
            for index, record in enumerate(self._read(file), start=1):
                episode, timestamp_missing = self._to_episode(record, file, index)
                missing_timestamps += timestamp_missing
                yield episode
            if missing_timestamps and self.created_at_field:
                self.warnings.append(
                    f"{file.name}: {missing_timestamps} record(s) missing or with "
                    f"unparseable {self.created_at_field!r}; their episodes have no "
                    "created_at and Zep will default to the ingestion time."
                )

    @staticmethod
    def _read_lines(file: Path) -> Iterator[Any]:
        for line in file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                yield json.loads(line)

    def _read(self, file: Path) -> Iterator[Any]:
        fmt: str = self.format
        detected = fmt == "auto"
        if detected:
            fmt = {".jsonl": "jsonl", ".csv": "csv"}.get(file.suffix.lower(), "json")
        try:
            if fmt == "jsonl":
                yield from self._read_lines(file)
            elif fmt == "csv":
                with file.open(newline="", encoding="utf-8") as handle:
                    yield from csv.DictReader(handle)
            else:
                try:
                    parsed = json.loads(file.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    # a .json file holding newline-delimited objects is a common
                    # export shape, and the shared row reader already accepts it;
                    # an explicit format="json" means the caller ruled that out
                    if not detected:
                        raise
                    yield from self._read_lines(file)
                    return
                yield from parsed if isinstance(parsed, list) else [parsed]
        except (json.JSONDecodeError, ValueError) as error:
            raise ConfigurationError(f"Unparseable records in {file}: {error}") from error

    def _to_episode(self, record: Any, file: Path, index: int) -> tuple[Episode, int]:
        # json reads NaN/Infinity/-Infinity as a Python extension and would write
        # them straight back out, so they are refused here — at the record, the one
        # point every format reaches, and before any mapping, so the field named is
        # the one in the caller's file. CSV rows and records that never went through
        # json.loads are covered alike, and allow_nan=False on the dump below keeps
        # the guarantee mechanical.
        found = _find_non_finite(record)
        if found is not None:
            path, value = found
            where = f"field {path!r}" if path else "the record"
            raise ConfigurationError(
                f"Non-finite number in {file}, record {index}: {where} is {value!r}. "
                "NaN, Infinity and -Infinity are not valid JSON, so the episode body "
                "would be unparseable, and a value lifted into metadata is sent as null. "
                "Replace it with a finite number, a string, or null."
            )
        created_at: str | None = None
        timestamp_missing = 0
        metadata: dict[str, Any] = {"source_type": "json_record", "file_name": file.name}
        if isinstance(record, dict):
            # every field the caller names is read from the record as they wrote it,
            # never from a key the loader just wrote: with id_field='sku' and
            # name_field='id' the name is still the record's own 'id', and the order
            # of the mappings below carries no meaning
            original = record
            record = dict(record)
            for target, field in (
                ("id", self.id_field),
                ("name", self.name_field),
                ("description", self.description_field),
            ):
                if field and field in original:
                    record[target] = original[field]
            if self.record_type:
                record["record_type"] = self.record_type
            if self.created_at_field:
                created_at = _parse_timestamp(original.get(self.created_at_field))
                timestamp_missing = int(created_at is None)
            # metadata_fields promotes keys of the episode as emitted, so a lifted
            # value always matches the episode body under that key — a mapped
            # identity key or an injected record_type included. A field the API
            # would not take as a metadata value (a nested object, say) stays in
            # the body only.
            for f in self._liftable_fields:
                if f in record and is_scalar_or_scalar_array(record[f]):
                    metadata[f] = record[f]
        return (
            Episode(
                data=json.dumps(record, allow_nan=False),
                data_type="json",
                created_at=created_at,
                metadata=metadata,
            ),
            timestamp_missing,
        )
