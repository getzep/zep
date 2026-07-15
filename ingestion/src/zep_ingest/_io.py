"""Shared row-file reader for the dataclass ingestion paths.

``ingest_fact_triples`` and ``ingest_thread_messages`` both accept a file of
rows — CSV, JSONL, or a JSON array — with columns matching their dataclass's
fields. The format dispatch lives once, here, so error handling and format
support cannot drift between the two.
"""

import csv
import json
from pathlib import Path
from typing import Any

from zep_ingest.exceptions import ConfigurationError


def load_rows(path: Path) -> list[dict[str, Any]]:
    """Read rows from a .csv, .jsonl, or JSON-array file."""
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))
    text = path.read_text(encoding="utf-8")
    try:
        if text.lstrip().startswith("["):  # a JSON array, not JSONL
            rows: list[dict[str, Any]] = json.loads(text)
            return rows
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    except json.JSONDecodeError as error:
        raise ConfigurationError(f"Could not parse {path.name} as JSON/JSONL: {error}") from error


def rows_to_fields(rows: list[dict[str, Any]], fields: frozenset[str]) -> list[dict[str, Any]]:
    """Validate row shapes and retain non-empty dataclass fields.

    Unknown columns are rejected because silently dropping a misspelled public
    field can produce a valid-looking but semantically incomplete ingestion.
    """
    validated: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ConfigurationError(
                f"Row {index} must be a JSON object or CSV record, got {type(row).__name__}"
            )
        unknown = sorted(set(row) - fields)
        if unknown:
            raise ConfigurationError(
                f"Row {index} has unknown field(s): {', '.join(unknown)}. "
                f"Expected fields: {', '.join(sorted(fields))}."
            )
        validated.append({k: v for k, v in row.items() if v not in (None, "")})
    return validated
