"""Shared row-file reader for the dataclass ingestion paths.

``ingest_fact_triples``, ``ingest_nodes``, and ``ingest_thread_messages`` each
accept a file of rows — JSONL or a JSON array — with keys matching their
dataclass's fields. JSON is the only file format here because these schemas
carry list and mapping fields (node labels, attributes, metadata) that CSV
cannot express; flat tabular data belongs in ``ingest_json_records``, which
does accept CSV. The dispatch lives once, here, so behavior cannot drift
between the paths.
"""

import json
from pathlib import Path
from typing import Any

from zep_ingest.exceptions import ConfigurationError


def load_rows(path: Path) -> list[dict[str, Any]]:
    """Read rows from a .jsonl or JSON-array file."""
    if path.suffix.lower() == ".csv":
        raise ConfigurationError(
            f"CSV is not supported for this ingestion path ({path.name}); use JSONL "
            "or a JSON array. They can express the list and mapping fields (node "
            "labels, attributes, metadata) that CSV cannot. For flat tabular records, "
            "use ingest_json_records, which does accept CSV."
        )
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
            raise ConfigurationError(f"Row {index} must be a JSON object, got {type(row).__name__}")
        unknown = sorted(set(row) - fields)
        if unknown:
            raise ConfigurationError(
                f"Row {index} has unknown field(s): {', '.join(unknown)}. "
                f"Expected fields: {', '.join(sorted(fields))}."
            )
        validated.append({k: v for k, v in row.items() if v not in (None, "")})
    return validated
